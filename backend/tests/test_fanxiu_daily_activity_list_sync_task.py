from __future__ import annotations

import threading
from datetime import datetime
from types import GeneratorType
from zoneinfo import ZoneInfo

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.daily_activity_list_sync import (
    DailyActivityListSyncPendingResearchError,
    _carry_same_process_activity_observations,
    next_daily_activity_list_sync_time,
    run_daily_activity_list_sync_flow,
)


TIMEZONE = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 8, 14, 0, 20, 5, tzinfo=TIMEZONE)


def _drain(value):
    if not isinstance(value, GeneratorType):
        return value
    try:
        while True:
            next(value)
    except StopIteration as exc:
        return exc.value


class FakeRuntime:
    def __init__(self, *, fail_target: int | None = None):
        self.fail_target = fail_target
        self.calls: list[tuple] = []
        self.next_time_writes: list[tuple[str, str | None]] = []

    def goto_view(self, scene_id: int):
        self.calls.append(("goto", scene_id))
        if self.fail_target == scene_id:
            raise RuntimeError(f"missing route to #{scene_id}")
        if False:
            yield None

    def wait_view(self, scene_id: int, *, timeout: float, label: str):
        self.calls.append(("wait", scene_id, timeout, label))
        if False:
            yield None

    def set_job_next_time(self, task_id: str, next_time: str | None):
        self.next_time_writes.append((task_id, next_time))

    def set_next_time(self, next_time: str | None):
        self.next_time_writes.append(("self", next_time))


def _ready_plan(*, operations=None):
    return {
        "status": "ready",
        "reason": "ready",
        "target_date": NOW.date().isoformat(),
        "requires_ui_preheat": False,
        "source_kind": "worldline_activity_runtime_memory",
        "captured_at": NOW.isoformat(),
        "operations": list(operations or []),
    }


def _sync_result(status: str, *, persist: bool):
    return {
        "status": status,
        "write_authorized": persist,
        "created_count": 1 if status.startswith("updated") else 0,
        "noop_count": 2,
        "review_count": 1 if "review" in status else 0,
        "reviews": [{"action": "review_unknown_identity"}]
        if "review" in status
        else [],
    }


def test_next_time_is_owned_by_job_and_always_moves_to_next_day_0020():
    assert next_daily_activity_list_sync_time(
        datetime(2026, 8, 14, 0, 1, tzinfo=TIMEZONE)
    ) == datetime(2026, 8, 15, 0, 20, tzinfo=TIMEZONE)


def test_preheat_carries_same_process_left_menu_observation_only() -> None:
    observation = {"activity_id": 712, "is_schedule_occurrence": False}
    initial = {
        "activity_observations": [observation],
        "source_evidence": {
            "supplemental_activity_observation": {
                "complete": True,
                "evidence": {"pid": 2626, "process_start_ticks": 4748},
            }
        },
    }
    refreshed = {
        "activity_observations": [],
        "source_evidence": {
            "runtime": {"pid": 2626, "process_start_ticks": 4748}
        },
        "summary": {"total": 3},
    }

    merged = _carry_same_process_activity_observations(initial, refreshed)
    assert merged["activity_observations"] == [observation]
    assert merged["summary"]["activity_observation_total"] == 1

    changed_process = {
        **refreshed,
        "source_evidence": {
            "runtime": {"pid": 9999, "process_start_ticks": 1}
        },
    }
    assert not _carry_same_process_activity_observations(
        initial, changed_process
    ).get("activity_observations")
    assert next_daily_activity_list_sync_time(
        datetime(2026, 8, 14, 23, 59, tzinfo=TIMEZONE)
    ) == datetime(2026, 8, 15, 0, 20, tzinfo=TIMEZONE)


def test_job_is_registered_once_and_scheduler_only_seeds_first_0020():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "activity_daily_list_sync"
    )
    assert definition is not None
    assert definition.label == "活动_每日清单同步"
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "activity-daily-list-sync"
    assert definition.standard_job_description == "每日"

    tasks = default_data_annotation_scheduler_tasks(
        now=datetime(2026, 8, 14, 0, 10)
    )
    matches = [
        task
        for task in tasks
        if task.get("task_type") == "activity_daily_list_sync"
    ]
    assert len(matches) == 1
    assert matches[0]["id"] == "activity-daily-list-sync"
    assert matches[0]["label"] == "活动_每日清单同步"
    assert matches[0]["trigger_description"] == "每日"
    assert matches[0]["next_time"] == "2026-08-14 00:20:00"
    assert matches[0]["error_retry_delay_seconds"] == 3600
    assert "schedule_kind" not in matches[0]


def test_registered_handler_delegates_to_the_runner_business_method():
    calls: list[tuple] = []

    class FakeRunner:
        def _execute_daily_activity_list_sync_task(
            self,
            ctx,
            stop_event,
            payload,
        ):
            calls.append((ctx, stop_event, payload))
            yield "running"
            return "success"

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "activity_daily_list_sync"
    )
    assert definition is not None
    stop_event = threading.Event()
    result = _drain(
        definition.handler(
            FakeRunner(),
            {"asset_tree_path": "unused"},
            {"__scheduler_task_id": "activity-daily-list-sync"},
            stop_event,
        )
    )

    assert result == "success"
    assert calls == [
        (
            {"asset_tree_path": "unused"},
            stop_event,
            {"__scheduler_task_id": "activity-daily-list-sync"},
        )
    ]


def test_ready_hot_read_dry_runs_then_persists_and_returns_world():
    reads: list[dict] = []
    sync_calls: list[bool] = []

    def reader(**kwargs):
        reads.append(kwargs)
        return _ready_plan()

    def sync(_plan, *, persist, now):
        assert now.tzinfo is not None
        assert now >= NOW
        sync_calls.append(persist)
        return _sync_result("updated_with_review" if persist else "planned_with_review", persist=persist)

    runtime = FakeRuntime()
    result = _drain(
        run_daily_activity_list_sync_flow(
            runtime,
            plan_reader=reader,
            synchronizer=sync,
            now=NOW,
        )
    )

    assert sync_calls == [False, True]
    assert len(reads) == 1
    assert reads[0]["allow_discovery"] is False
    assert reads[0]["force_refresh"] is False
    assert result == {
        "result": "success",
        "status": "updated_with_review",
        "preheated": False,
        "created_count": 1,
        "noop_count": 2,
        "review_count": 1,
        "reviews": [{"action": "review_unknown_identity"}],
        "job_schedule": {
            "target_date": "2026-08-14",
            "desired_next_times": {
                "legacy-daily-xianmeng": None,
                "yunmeng-tail": None,
            },
            "decisions": [],
        },
        "current_scene": 34,
        "message": "活动_每日清单同步完成：新增 1，已存在 2，待复核 1，活动作业 0 项",
    }
    assert runtime.next_time_writes == [
        ("legacy-daily-xianmeng", None),
        ("yunmeng-tail", None),
        ("self", "2026-08-15 00:20:00"),
    ]
    assert [call[:2] for call in runtime.calls] == [("goto", 34), ("wait", 34)]


def test_authorized_xianmeng_qualifying_configures_challenge_for_1000():
    operation = {
        "action": "propose_create",
        "occurrence": {
            "activity_id": 421601,
            "activity_type": 43,
            "base_id": 28100,
            "schedule_id": 6400002,
            "catalog_status": "known",
            "identity_complete": True,
            "day_relation": "starts_today",
            "start_at": "2026-08-14T10:00:00+08:00",
            "end_at": "2026-08-14T22:00:00+08:00",
            "close_panel_at": "2026-08-14T21:59:03+08:00",
            "raw": {"baseId": 28100},
        },
    }
    runtime = FakeRuntime()

    result = _drain(
        run_daily_activity_list_sync_flow(
            runtime,
            plan_reader=lambda **_kwargs: _ready_plan(operations=[operation]),
            synchronizer=lambda _plan, *, persist, now: _sync_result(
                "updated" if persist else "planned",
                persist=persist,
            ),
            now=NOW,
        )
    )

    assert runtime.next_time_writes == [
        ("legacy-daily-xianmeng", "2026-08-14 10:00:00"),
        ("yunmeng-tail", None),
        ("self", "2026-08-15 00:20:00"),
    ]
    assert result["job_schedule"]["decisions"][0]["base_id"] == 28100


def test_authorized_xianmeng_final_starts_a_fresh_single_day_cycle():
    operation = {
        "action": "propose_create",
        "occurrence": {
            "activity_id": 421701,
            "activity_type": 42,
            "base_id": 28200,
            "schedule_id": 6400003,
            "catalog_status": "known",
            "identity_complete": True,
            "day_relation": "starts_today",
            "start_at": "2026-08-14T10:00:00+08:00",
            "end_at": "2026-08-14T22:00:00+08:00",
            "close_panel_at": "2026-08-14T21:59:03+08:00",
            "raw": {"baseId": 28200},
        },
    }
    runtime = FakeRuntime()

    result = _drain(
        run_daily_activity_list_sync_flow(
            runtime,
            plan_reader=lambda **_kwargs: _ready_plan(operations=[operation]),
            synchronizer=lambda _plan, *, persist, now: _sync_result(
                "updated" if persist else "planned",
                persist=persist,
            ),
            now=NOW,
        )
    )

    assert runtime.next_time_writes == [
        ("legacy-daily-xianmeng", "2026-08-14 10:00:00"),
        ("yunmeng-tail", None),
        ("self", "2026-08-15 00:20:00"),
    ]
    assert result["job_schedule"]["decisions"][0]["base_id"] == 28200


def test_runtime_xianmeng_root_final_day_writes_today_1000_to_challenge_job():
    final_now = datetime(2026, 8, 17, 0, 20, 5, tzinfo=TIMEZONE)
    occurrence = {
        "activity_id": 16420001,
        "activity_type": 42,
        "base_id": 28000,
        "schedule_id": 6400002,
        "catalog_status": "known",
        "identity_complete": True,
        "day_relation": "continues_today",
        "ends_today": True,
        "start_at": "2026-08-16T10:00:00+08:00",
        "end_at": "2026-08-17T22:00:00+08:00",
        "close_panel_at": "2026-08-18T23:58:59+08:00",
        "raw": {"baseId": 28000},
    }
    plan = {
        "status": "ready",
        "reason": "ready",
        "target_date": "2026-08-17",
        "requires_ui_preheat": False,
        "source_kind": "worldline_activity_runtime_memory",
        "captured_at": final_now.isoformat(),
        "occurrences": [occurrence],
        "operations": [],
    }
    runtime = FakeRuntime()

    result = _drain(
        run_daily_activity_list_sync_flow(
            runtime,
            plan_reader=lambda **_kwargs: plan,
            synchronizer=lambda _plan, *, persist, now: _sync_result(
                "no_change",
                persist=persist,
            ),
            now=final_now,
        )
    )

    assert runtime.next_time_writes == [
        ("legacy-daily-xianmeng", "2026-08-17 10:00:00"),
        ("yunmeng-tail", None),
        ("self", "2026-08-18 00:20:00"),
    ]
    assert result["job_schedule"]["decisions"][0]["base_id"] == 28000


def test_not_loaded_uses_formal_66_preheat_then_hot_reread():
    plans = [
        {
            "status": "not_loaded",
            "reason": "manager data not loaded",
            "requires_ui_preheat": True,
        },
        _ready_plan(),
    ]
    sync_calls: list[bool] = []

    def reader(**_kwargs):
        return plans.pop(0)

    def sync(_plan, *, persist, now):
        sync_calls.append(persist)
        return _sync_result("no_change", persist=persist)

    runtime = FakeRuntime()
    result = _drain(
        run_daily_activity_list_sync_flow(
            runtime,
            plan_reader=reader,
            synchronizer=sync,
            now=NOW,
        )
    )

    assert result["preheated"] is True
    assert sync_calls == [False, True]
    assert [call[:2] for call in runtime.calls] == [
        ("goto", 66),
        ("wait", 66),
        ("goto", 34),
        ("wait", 34),
    ]


def test_missing_66_route_is_pending_research_and_never_syncs():
    sync_calls: list[bool] = []

    def reader(**_kwargs):
        return {
            "status": "not_loaded",
            "reason": "manager data not loaded",
            "requires_ui_preheat": True,
        }

    def sync(_plan, *, persist, now):
        sync_calls.append(persist)
        return _sync_result("no_change", persist=persist)

    runtime = FakeRuntime(fail_target=66)
    with pytest.raises(DailyActivityListSyncPendingResearchError, match="pending_research"):
        _drain(
            run_daily_activity_list_sync_flow(
                runtime,
                plan_reader=reader,
                synchronizer=sync,
                now=NOW,
            )
        )

    assert sync_calls == []
    assert ("goto", 34) in runtime.calls


def test_still_not_loaded_after_preheat_fails_without_persistence_or_next_time():
    sync_calls: list[bool] = []

    def reader(**_kwargs):
        return {
            "status": "not_loaded",
            "reason": "activity list absent",
            "requires_ui_preheat": True,
        }

    def sync(_plan, *, persist, now):
        sync_calls.append(persist)
        return _sync_result("no_change", persist=persist)

    runtime = FakeRuntime()
    with pytest.raises(DailyActivityListSyncPendingResearchError, match="#66 已自然预热后"):
        _drain(
            run_daily_activity_list_sync_flow(
                runtime,
                plan_reader=reader,
                synchronizer=sync,
                now=NOW,
            )
        )

    assert sync_calls == []
    assert [call[:2] for call in runtime.calls][-2:] == [("goto", 34), ("wait", 34)]


def test_rejected_dry_run_never_calls_persist_and_still_returns_world():
    sync_calls: list[bool] = []

    def sync(_plan, *, persist, now):
        sync_calls.append(persist)
        return {
            "status": "not_written",
            "reason": "Runtime 同步计划已经过期",
        }

    runtime = FakeRuntime()
    with pytest.raises(RuntimeError, match="dry-run未通过"):
        _drain(
            run_daily_activity_list_sync_flow(
                runtime,
                plan_reader=lambda **_kwargs: _ready_plan(),
                synchronizer=sync,
                now=NOW,
            )
        )

    assert sync_calls == [False]
    assert [call[:2] for call in runtime.calls] == [("goto", 34), ("wait", 34)]


def test_departure_failure_prevents_success_and_next_time():
    runtime = FakeRuntime(fail_target=34)

    def sync(_plan, *, persist, now):
        return _sync_result("no_change", persist=persist)

    with pytest.raises(RuntimeError, match="未安全回到 #34"):
        _drain(
            run_daily_activity_list_sync_flow(
                runtime,
                plan_reader=lambda **_kwargs: _ready_plan(),
                synchronizer=sync,
                now=NOW,
            )
        )

    assert runtime.next_time_writes == []
