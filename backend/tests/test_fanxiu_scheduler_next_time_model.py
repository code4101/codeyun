from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json

import pytest

from backend.core.fanxiu.data_annotation import behavior_tree_control
from backend.core.fanxiu.data_annotation.runner import (
    create_behavior_tree_runtime_runner,
)
from backend.core.fanxiu.data_annotation.scheduler import (
    repair_data_annotation_scheduler_tasks,
    set_scheduler_task_trigger_time,
)
from backend.core.fanxiu.data_annotation.state import (
    data_annotation_task_due,
    normalize_data_annotation_scheduler_task,
    persist_behavior_tree_runtime_status,
    read_data_annotation_world_facts,
    record_data_annotation_scheduler_task_fact,
)


def test_default_trigger_descriptions_are_only_simple_types():
    from backend.core.fanxiu.data_annotation.scheduler_defaults import (
        default_data_annotation_scheduler_tasks,
    )

    descriptions = {
        str(task.get("trigger_description") or "")
        for task in default_data_annotation_scheduler_tasks(datetime(2026, 8, 12, 9, 0))
    }
    assert descriptions <= {"每日", "每周", "动态", "手动"}


def _task(**updates):
    task = {
        "id": "job-a",
        "task_type": "job_a",
        "label": "作业A",
        "next_time": "2026-07-23 12:00:00",
    }
    task.update(updates)
    return task


@pytest.mark.parametrize(
    ("value", "expected_message"),
    [
        ("skipped", ""),
        ({"result": "failed", "message": "业务未达成"}, "业务未达成"),
    ],
)
def test_any_normal_business_return_is_trigger_success(value, expected_message):
    runner = create_behavior_tree_runtime_runner()

    assert runner._normalize_runtime_task_result(value) == (
        "success",
        expected_message,
    )


def test_trigger_command_only_sets_or_clears_next_time():
    tasks = [_task(next_time=None)]

    updated = set_scheduler_task_trigger_time(
        tasks,
        "作业A",
        "13:10",
        now=datetime(2026, 7, 23, 12, 0),
    )

    assert updated["next_time"] == "2026-07-23 13:10:00"

    set_scheduler_task_trigger_time(tasks, "job-a", None)
    assert updated["next_time"] is None


def test_trigger_once_only_sets_next_time_to_now(tmp_path, monkeypatch):
    path = tmp_path / "scheduler_tasks.json"
    path.write_text(
        json.dumps([_task(next_time=None)], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(behavior_tree_control, "task_supported", lambda _task: True)

    triggered_at = behavior_tree_control.trigger_scheduler_task_once(
        "job-a",
        scheduler_state_path=path,
        now=datetime(2026, 7, 23, 12, 34, 56),
    )

    stored = json.loads(path.read_text(encoding="utf-8"))
    stored_task = next(item for item in stored if item.get("id") == "job-a")
    assert triggered_at == "2026-07-23 12:34:56"
    assert stored_task["next_time"] == "2026-07-23 12:34:56"
    assert stored_task.get("last_run_at") is None
    assert stored_task.get("attempt_id") is None


@pytest.mark.parametrize(
    ("next_time", "expected"),
    [
        ("2000-01-01 00:00:00", True),
        (None, False),
        ("2999-01-01 00:00:00", False),
    ],
)
def test_due_is_exactly_next_time_reached(next_time, expected):
    assert data_annotation_task_due(_task(next_time=next_time)) is expected


def test_future_retry_time_temporarily_removes_failed_task_from_due_set(monkeypatch):
    monkeypatch.setattr(behavior_tree_control, "task_supported", lambda _task: True)
    cooling = _task(
        id="cooling",
        next_time="2999-01-01 00:00:00",
    )
    healthy = _task(
        id="healthy",
        next_time="2000-01-01 00:00:01",
    )

    assert data_annotation_task_due(cooling) is False
    assert data_annotation_task_due(healthy) is True
    assert [
        task["id"]
        for task in behavior_tree_control.select_due_data_annotation_scheduler_tasks(
            [cooling, healthy]
        )
    ] == ["healthy"]


@pytest.mark.parametrize(
    ("next_time", "expected_scheduled_attempt"),
    [
        ("2000-01-01 00:00:00", True),
        ("2999-01-01 00:00:00", False),
    ],
)
def test_run_now_enforces_reschedule_contract_only_for_due_job(
    monkeypatch,
    next_time,
    expected_scheduled_attempt,
):
    task = _task(next_time=next_time)
    captured = {}
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [task])
    monkeypatch.setattr(behavior_tree_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        behavior_tree_control,
        "data_annotation_scheduler_run_now_task",
        lambda _tasks, _task_id, _payload: dict(task),
    )
    monkeypatch.setattr(behavior_tree_control, "task_supported", lambda _task: True)
    monkeypatch.setattr(behavior_tree_control, "prepare_runtime_for_scheduler_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        behavior_tree_control,
        "ensure_scheduler_kernel_code_current",
        lambda **_kwargs: {"ready": True},
    )

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"status": "success"}

    monkeypatch.setattr(
        behavior_tree_control,
        "_run_scheduler_task_cell_and_record_terminal",
        fake_run,
    )

    result = behavior_tree_control.run_now_scheduler_task(
        entry=object(),
        entry_id="entry-a",
        task_id="job-a",
        business_time_mode="current",
    )

    assert result == {"status": "success"}
    assert captured["scheduled_attempt"] is expected_scheduled_attempt


def test_run_now_future_job_defaults_effective_now_to_one_minute_after_next_time(
    monkeypatch,
):
    task = _task(next_time="2999-01-01 21:30:00")
    captured_payload = {}
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [task])
    monkeypatch.setattr(behavior_tree_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(behavior_tree_control, "task_supported", lambda _task: True)
    monkeypatch.setattr(behavior_tree_control, "prepare_runtime_for_scheduler_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "ensure_scheduler_kernel_code_current", lambda **_kwargs: {"ready": True})

    def capture_cell_task(**kwargs):
        captured_payload.update(kwargs["task"].get("payload") or {})
        return {"status": "success"}

    monkeypatch.setattr(
        behavior_tree_control,
        "_run_scheduler_task_cell_and_record_terminal",
        capture_cell_task,
    )

    behavior_tree_control.run_now_scheduler_task(
        entry=object(),
        entry_id="entry-a",
        task_id="job-a",
    )

    assert captured_payload["effective_now"] == "2999-01-01 21:31:00"


def test_run_now_transient_payload_reaches_kernel_after_fresh_attempt_claim(monkeypatch):
    durable_payload = {"daily_start_time": "15:30", "daily_end_time": "22:00"}
    state = [_task(payload=deepcopy(durable_payload), next_time="2999-01-01 21:30:00")]
    submitted_payload = {}

    monkeypatch.setattr(
        behavior_tree_control,
        "read_scheduler_tasks",
        lambda **_kwargs: deepcopy(state),
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda tasks, **_kwargs: state.__setitem__(slice(None), deepcopy(tasks)),
    )
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"execution_state": "idle", "generation": 7},
    )

    def submit(**kwargs):
        submitted_payload.update(deepcopy(kwargs["payload"]))
        return {
            "status": "success",
            "message": "planned window entered",
            "result_text": "{'result': 'success', 'message': 'planned window entered'}",
        }

    monkeypatch.setattr(behavior_tree_control, "submit_runtime_task_cell", submit)
    run_task = deepcopy(state[0])
    run_task["payload"] = {
        **durable_payload,
        "effective_now": "2999-01-01 21:31:00",
        "run_now_probe": "kept",
    }

    result = behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="entry-a",
        task=run_task,
    )

    assert result["status"] == "success"
    assert submitted_payload["effective_now"] == "2999-01-01 21:31:00"
    assert submitted_payload["run_now_probe"] == "kept"
    assert submitted_payload["daily_start_time"] == "15:30"
    assert submitted_payload["__scheduler_task_id"] == "job-a"
    assert submitted_payload["__scheduler_attempt_id"]
    assert state[0]["payload"] == durable_payload


def test_run_now_current_time_mode_does_not_inject_planned_effective_now(monkeypatch):
    task = _task(next_time="2999-01-01 21:30:00")
    captured_payload = {}
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [task])
    monkeypatch.setattr(behavior_tree_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)

    def build_run_task(_tasks, _task_id, payload):
        captured_payload.update(payload)
        return dict(task)

    monkeypatch.setattr(behavior_tree_control, "data_annotation_scheduler_run_now_task", build_run_task)
    monkeypatch.setattr(behavior_tree_control, "task_supported", lambda _task: True)
    monkeypatch.setattr(behavior_tree_control, "prepare_runtime_for_scheduler_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "ensure_scheduler_kernel_code_current", lambda **_kwargs: {"ready": True})
    monkeypatch.setattr(
        behavior_tree_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **_kwargs: {"status": "success"},
    )

    behavior_tree_control.run_now_scheduler_task(
        entry=object(),
        entry_id="entry-a",
        task_id="job-a",
        business_time_mode="current",
    )

    assert "effective_now" not in captured_payload


@pytest.mark.parametrize(
    ("next_time", "message"),
    [
        (None, "没有计划时间"),
        ("2000-01-01 00:00:00", "已经到期"),
    ],
)
def test_early_run_requires_a_future_next_time(monkeypatch, next_time, message):
    task = _task(next_time=next_time)
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [task])
    monkeypatch.setattr(behavior_tree_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)

    with pytest.raises(ValueError, match=message):
        behavior_tree_control.run_now_scheduler_task(
            entry=object(),
            entry_id="entry-a",
            task_id="job-a",
        )


@pytest.mark.parametrize("business_time_mode", ["planned", "current"])
def test_run_now_explicit_effective_now_overrides_early_run_default(
    monkeypatch,
    business_time_mode,
):
    task = _task(next_time="2999-01-01 21:30:00")
    captured_payload = {}
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [task])
    monkeypatch.setattr(behavior_tree_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)

    def build_run_task(_tasks, _task_id, payload):
        captured_payload.update(payload)
        return dict(task)

    monkeypatch.setattr(behavior_tree_control, "data_annotation_scheduler_run_now_task", build_run_task)
    monkeypatch.setattr(behavior_tree_control, "task_supported", lambda _task: True)
    monkeypatch.setattr(behavior_tree_control, "prepare_runtime_for_scheduler_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "ensure_scheduler_kernel_code_current", lambda **_kwargs: {"ready": True})
    monkeypatch.setattr(
        behavior_tree_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **_kwargs: {"status": "success"},
    )

    behavior_tree_control.run_now_scheduler_task(
        entry=object(),
        entry_id="entry-a",
        task_id="job-a",
        payload_override={"effective_now": "2999-01-01 22:05:00"},
        business_time_mode=business_time_mode,
    )

    assert captured_payload["effective_now"] == "2999-01-01 22:05:00"


def test_retry_policy_delays_only_ordinary_jobs():
    finished = datetime(2026, 7, 25, 6, 30, 0)

    assert behavior_tree_control.scheduler_task_retry_time(
        _task(),
        finished,
    ) == "2026-07-25 06:40:00"
    assert behavior_tree_control.scheduler_task_retry_time(
        _task(error_retry_delay_seconds=1800),
        finished,
    ) == "2026-07-25 07:00:00"
    assert behavior_tree_control.scheduler_task_retry_time(
        _task(dispatch_level=1),
        finished,
    ) == "2026-07-25 06:30:00"
    assert behavior_tree_control.scheduler_task_retry_time(
        _task(dispatch_level=5, error_retry_delay_seconds=1800),
        finished,
    ) == "2026-07-25 07:00:00"
    assert behavior_tree_control.scheduler_task_retry_time(
        _task(window=["06:30", "06:35"]),
        finished,
    ) == "2026-07-25 06:40:00"


def test_retry_policy_uses_the_single_next_time_field():
    ordinary = _task(next_time="2026-07-25 05:00:00")
    urgent = _task(next_time="2026-07-25 05:00:00", dispatch_level=1)
    finished = datetime(2026, 7, 25, 6, 30, 0)

    behavior_tree_control.schedule_failed_task_retry(ordinary, finished)
    behavior_tree_control.schedule_failed_task_retry(urgent, finished)

    assert ordinary["next_time"] == "2026-07-25 06:40:00"
    assert urgent["next_time"] == "2026-07-25 06:30:00"
    assert "retry_after" not in ordinary
    assert "retry_after" not in urgent


def test_zero_delay_failure_stays_due_but_yields_to_older_same_level_work():
    failed = _task(
        id="failed",
        next_time="2026-07-25 05:00:00",
        dispatch_level=1,
    )
    waiting = _task(
        id="waiting",
        next_time="2026-07-25 06:00:00",
        dispatch_level=1,
    )
    finished = datetime(2026, 7, 25, 6, 30, 0)

    behavior_tree_control.schedule_failed_task_retry(failed, finished)
    ordered = behavior_tree_control.sort_scheduler_tasks_for_dispatch([failed, waiting])

    assert failed["next_time"] == "2026-07-25 06:30:00"
    assert [task["id"] for task in ordered] == ["waiting", "failed"]


def test_normalized_scheduler_record_has_one_trigger_time():
    normalized = normalize_data_annotation_scheduler_task(_task(retry_after="2999-01-01 00:00:00"))
    assert normalized is not None
    assert "enabled" not in normalized
    assert "retry_after" not in normalized
    assert normalized["next_time"] == "2026-07-23 12:00:00"
    assert [key for key in normalized if key.endswith("_time")] == ["next_time"]


def test_attempt_trigger_snapshot_preserves_absent_vs_explicit_none():
    legacy = normalize_data_annotation_scheduler_task(_task())
    manual = normalize_data_annotation_scheduler_task(
        _task(next_time=None, attempt_original_trigger=None)
    )

    assert legacy is not None
    assert "attempt_original_trigger" not in legacy
    assert manual is not None
    assert "attempt_original_trigger" in manual
    assert manual["attempt_original_trigger"] is None


def test_manual_rule_without_next_time_stays_asleep():
    source = _task(next_time=None)
    repaired, _changed = repair_data_annotation_scheduler_tasks(
        [source],
        [source],
        {},
        task_supported=lambda _task: True,
        now=datetime(2026, 7, 23, 12, 0),
    )
    assert "enabled" not in repaired[0]
    assert repaired[0]["next_time"] is None


def test_world_fact_is_observational_and_does_not_mirror_trigger_time(tmp_path):
    path = tmp_path / "world-facts.json"
    record_data_annotation_scheduler_task_fact(path, _task(), "success")

    fact = read_data_annotation_world_facts(path)["discoveries"]["task"]["job-a"]
    assert fact["last_result"] == "success"
    assert [key for key in fact if key.endswith("_time")] == []


def test_persist_runtime_status_records_each_guard_event_once(tmp_path):
    runtime_path = tmp_path / "runtime.json"
    facts_path = tmp_path / "world-facts.json"
    event = {
        "time": 123.0,
        "kind": "popup",
        "image": "#86",
        "title": "离开场景",
        "folder_path": "弹窗/所有提示窗口",
        "score": 100.0,
        "action": "click:确认",
    }
    status = {
        "entry_id": "entry-a",
        "current_scene": 86,
        "status": "running",
        "running": True,
        "last_guard_event": event,
    }

    persist_behavior_tree_runtime_status(runtime_path, facts_path, status)
    persist_behavior_tree_runtime_status(runtime_path, facts_path, status)

    facts = read_data_annotation_world_facts(facts_path)
    guard_events = [item for item in facts["events"] if item.get("kind") == "guard_popup"]
    assert len(guard_events) == 1
    assert guard_events[0]["image"] == "#86"
    assert guard_events[0]["action"] == "click:确认"


@pytest.mark.parametrize("cell_status", ["success", "error"])
def test_terminal_result_does_not_infer_a_new_time(monkeypatch, cell_status):
    state = [_task()]
    original_time = state[0]["next_time"]
    runtime_terminal: dict = {}

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: deepcopy(state))
    monkeypatch.setattr(
        behavior_tree_control,
        "read_behavior_tree_runtime_status",
        lambda _path: {
            "running": True,
            "status": "running",
            "phase": "scheduler_task",
            "task_type": state[0]["task_type"],
            "current_task": state[0]["label"],
            "current_task_id": state[0]["id"],
        },
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "persist_behavior_tree_runtime_status",
        lambda status, **_kwargs: runtime_terminal.update(deepcopy(status)),
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda tasks, **_kwargs: state.__setitem__(slice(None), deepcopy(tasks)),
    )
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "task_payload_with_meta", lambda _task: {})
    monkeypatch.setattr(
        behavior_tree_control,
        "submit_runtime_task_cell",
        lambda **_kwargs: {
            "status": cell_status,
            "message": cell_status,
            "error": cell_status if cell_status == "error" else "",
            "result_text": (
                "{'result': 'success', 'message': 'done'}"
                if cell_status == "success"
                else ""
            ),
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"execution_state": "idle", "generation": 1},
    )

    behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="entry-a",
        task=deepcopy(state[0]),
        runtime_state_path=object(),
    )

    assert state[0]["last_result"] == cell_status
    if cell_status == "error":
        assert state[0]["next_time"] > state[0]["finished_at"]
        assert state[0]["next_time"] != original_time
    else:
        assert state[0]["next_time"] == original_time
    assert "retry_after" not in state[0]
    assert runtime_terminal["running"] is False
    assert runtime_terminal["status"] == cell_status
    assert runtime_terminal["current_task_id"] == ""


def test_terminal_result_ignores_returned_business_next_time(monkeypatch):
    state = [_task()]

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: deepcopy(state))
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda tasks, **_kwargs: state.__setitem__(slice(None), deepcopy(tasks)),
    )
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "task_payload_with_meta", lambda _task: {})
    monkeypatch.setattr(
        behavior_tree_control,
        "submit_runtime_task_cell",
        lambda **_kwargs: {
            "status": "success",
            "message": "success",
            "error": "",
            "result_text": (
                "{'result': 'success', 'message': 'done', "
                "'next_time': '2026-07-24 20:30:00'}"
            ),
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"execution_state": "idle", "generation": 1},
    )

    behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="entry-a",
        task=deepcopy(state[0]),
    )

    assert state[0]["last_result"] == "success"
    assert state[0]["next_time"] == "2026-07-23 12:00:00"
    assert "retry_after" not in state[0]


def test_terminal_error_writeback_preserves_checkpoint_added_inside_running_cell(
    monkeypatch,
):
    checkpoint = {"started_at": "2026-08-12T07:00:10", "start_tower_id": 1426}
    state = [
        _task(
            id="lingta-challenge",
            task_type="lingta_challenge",
            payload={"monitor_poll_seconds": 2},
            error_retry_delay_seconds=600,
        )
    ]

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: deepcopy(state))
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda tasks, **_kwargs: state.__setitem__(slice(None), deepcopy(tasks)),
    )
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    # A scheduled error normally emits an incident. Keep this synthetic task
    # out of the machine's real Fanxiu Runtime evidence directory.
    incidents: list[dict] = []
    monkeypatch.setattr(
        behavior_tree_control,
        "record_scheduler_incident",
        lambda **kwargs: incidents.append(deepcopy(kwargs)) or kwargs,
    )
    monkeypatch.setattr(behavior_tree_control, "task_payload_with_meta", lambda _task: {})

    def submit(**_kwargs):
        # This models the running Cell's atomic checkpoint write after the
        # external Scheduler has already retained its pre-submit task object.
        state[0]["payload"]["lingta_auto_chain_started"] = deepcopy(checkpoint)
        return {
            "status": "error",
            "message": "保留结算现场",
            "error": "保留结算现场",
            "result_text": "",
        }

    monkeypatch.setattr(behavior_tree_control, "submit_runtime_task_cell", submit)
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"execution_state": "idle", "generation": 1},
    )

    behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="entry-a",
        task=deepcopy(state[0]),
        scheduled_attempt=True,
    )

    assert state[0]["last_result"] == "error"
    assert state[0]["payload"]["monitor_poll_seconds"] == 2
    assert state[0]["payload"]["lingta_auto_chain_started"] == checkpoint
    assert len(incidents) == 1


def test_terminal_window_expiry_creates_scheduler_incident(monkeypatch, tmp_path):
    state = [_task(
        id="daily-daofa",
        task_type="daily_daofa",
        next_time="2026-07-30 23:00:00",
        last_result="error",
        last_message="等待挑战结果超时",
    )]
    incidents: list[dict] = []

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: deepcopy(state))
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda tasks, **_kwargs: state.__setitem__(slice(None), deepcopy(tasks)),
    )
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        behavior_tree_control,
        "record_scheduler_incident",
        lambda **kwargs: incidents.append(deepcopy(kwargs)) or kwargs,
    )
    monkeypatch.setattr(behavior_tree_control, "task_payload_with_meta", lambda _task: {})
    monkeypatch.setattr(
        behavior_tree_control,
        "submit_runtime_task_cell",
        lambda **_kwargs: {
            "status": "success",
            "message": "success",
            "error": "",
            "result_text": (
                "{'result': 'success', 'message': 'expired', "
                "'next_time': '2026-07-31 23:00:00', "
                "'scheduler_incident': {'kind': 'window_expired', "
                "'cycle_kind': 'daily', 'window': '23:00-23:59'}}"
            ),
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"execution_state": "idle", "generation": 86},
    )

    behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="entry-a",
        task=deepcopy(state[0]),
        scheduler_state_path=tmp_path / "scheduler_tasks.json",
    )

    assert state[0]["last_result"] == "success"
    assert state[0]["next_time"] == "2026-07-30 23:00:00"
    assert len(incidents) == 1
    assert incidents[0]["original_next_time"] == "2026-07-30 23:00:00"
    assert incidents[0]["incident"]["kind"] == "window_expired"
    assert incidents[0]["task"]["last_result"] == "running"
    assert incidents[0]["task"]["last_message"] == "已向 Fanxiu Kernel 提交普通 Cell"
    assert incidents[0]["task"]["attempt_id"]


def test_level_one_business_miss_is_trigger_success_and_stays_due_now(monkeypatch):
    original_time = "2026-07-25 05:00:00"
    business_time = "2026-07-25 12:01:28"
    state = [_task(next_time=original_time, dispatch_level=1)]

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: state)
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", lambda value, **_kwargs: state.__setitem__(slice(None), deepcopy(value)))
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_runner_status", lambda: {"running": False})
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"generation": 1},
    )

    def submit(**_kwargs):
        state[0]["next_time"] = business_time
        return {
            "status": "success",
            "message": "业务未达成，保持立即到期",
            "result_text": (
                "{'result': 'success', 'message': '业务未达成，保持立即到期', "
                "'next_time': '2026-07-25 12:01:28'}"
            ),
        }

    monkeypatch.setattr(behavior_tree_control, "submit_runtime_task_cell", submit)

    behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="game-window2",
        task=state[0],
    )

    assert state[0]["last_result"] == "success"
    assert state[0]["next_time"] == business_time


def test_manual_normal_return_preserves_unchanged_future_trigger(monkeypatch):
    future_trigger = "2099-07-25 21:31:00"
    state = [_task(next_time=future_trigger, window=["10:00", "22:00"])]

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: state)
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", lambda value, **_kwargs: state.__setitem__(slice(None), deepcopy(value)))
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_runner_status", lambda: {"running": False})
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"generation": 1},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "submit_runtime_task_cell",
        lambda **_kwargs: {
            "status": "success",
            "message": "Jupyter cell 执行完成",
            "result_text": "{'result': 'skipped', 'message': 'not due yet'}",
        },
    )

    behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="early-window-task",
        task=state[0],
    )

    assert state[0]["last_result"] == "success"
    assert state[0]["next_time"] == future_trigger


def test_static_daily_success_preserves_next_time_written_during_cell(monkeypatch):
    original_time = "2026-07-25 21:33:00"
    business_time = "2026-07-27 13:00:00"
    state = [_task(
        next_time=original_time,
    )]

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: state)
    monkeypatch.setattr(behavior_tree_control, "write_scheduler_tasks", lambda value, **_kwargs: state.__setitem__(slice(None), deepcopy(value)))
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"generation": 1},
    )

    def submit(**_kwargs):
        state[0]["next_time"] = business_time
        return {
            "status": "success",
            "message": "Jupyter cell 执行完成",
            "result_text": "{'result': 'success', 'message': '本周已完成'}",
        }

    monkeypatch.setattr(behavior_tree_control, "submit_runtime_task_cell", submit)

    behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="game-window2",
        task=state[0],
    )

    assert state[0]["last_result"] == "success"
    assert state[0]["next_time"] == business_time


def test_stale_attempt_never_becomes_success_only_because_next_time_advanced(monkeypatch):
    task = _task(
        last_result="running",
        next_time="2026-07-26 05:00:00",
        started_at="2026-07-25 05:00:00",
        attempt_id="attempt-a",
        attempt_kernel_generation=1,
    )
    tasks = [task]
    writes: list[list[dict]] = []

    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle", "generation": 2},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda value, **_kwargs: writes.append(deepcopy(value)),
    )
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)

    assert behavior_tree_control.reconcile_stale_scheduler_attempts(tasks) is True
    assert task["last_result"] == "error"
    assert task["attempt_id"] is None
    assert task["next_time"] > task["finished_at"]
    assert "retry_after" not in task
    assert writes


def test_scheduled_success_preserves_unchanged_business_time(monkeypatch):
    state = [
        _task(
            next_time="2026-07-23 21:30:00",
        )
    ]
    incidents: list[dict] = []

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: deepcopy(state))
    monkeypatch.setattr(
        behavior_tree_control,
        "write_scheduler_tasks",
        lambda tasks, **_kwargs: state.__setitem__(slice(None), deepcopy(tasks)),
    )
    monkeypatch.setattr(behavior_tree_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        behavior_tree_control,
        "record_scheduler_incident",
        lambda **kwargs: incidents.append(deepcopy(kwargs)) or kwargs,
    )
    monkeypatch.setattr(behavior_tree_control, "task_payload_with_meta", lambda _task: {})
    monkeypatch.setattr(
        behavior_tree_control,
        "submit_runtime_task_cell",
        lambda **_kwargs: {
            "status": "success",
            "message": "success",
            "error": "",
            "result_text": "{'result': 'success', 'message': 'done'}",
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"execution_state": "idle", "generation": 1},
    )

    behavior_tree_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="entry-a",
        task=deepcopy(state[0]),
        scheduled_attempt=True,
    )

    assert state[0]["last_result"] == "success"
    assert state[0]["last_message"] == "done"
    assert state[0]["next_time"] == "2026-07-23 21:30:00"
    assert incidents == []
