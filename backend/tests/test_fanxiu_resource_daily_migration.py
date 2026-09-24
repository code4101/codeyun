from __future__ import annotations

from copy import deepcopy
import json
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.behavior_tree_executor import (
    BehaviorTreeExecutor,
)
from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import (
    consolidate_arena_scheduler_instances,
    default_kernel_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.daily_signin import (
    DailySigninTaskMixin,
)
from backend.core.fanxiu.data_annotation.tasks.prayer_daily_resource import (
    PrayerDailyResourceTaskMixin,
)
from backend.core.fanxiu.data_annotation.tasks.resource_daily_contract import (
    RESOURCE_DAILY_INTERNALIZED_JOBS_KEY,
    RESOURCE_DAILY_LABEL,
    RESOURCE_DAILY_PAYLOAD_SCHEMA_KEY,
    RESOURCE_DAILY_PROGRESS_KEY,
    RESOURCE_DAILY_RETIRED_TASK_IDS,
    RESOURCE_DAILY_RETIRED_TASK_TYPES,
    RESOURCE_DAILY_SCHEMA_VERSION,
    RESOURCE_DAILY_STAGES,
    RESOURCE_DAILY_TASK_ID,
    RESOURCE_DAILY_TASK_TYPE,
    RESOURCE_DAILY_TRIGGER_DESCRIPTION,
    resource_daily_cycle_key,
    resource_daily_completion,
)
from backend.core.fanxiu.data_annotation.tasks.xianshi_exchange import (
    XianshiExchangeTaskMixin,
)


NOW = datetime(2026, 9, 21, 12, 0, 0)
NEXT_MIDNIGHT = "2026-09-22 00:00:00"


def test_real_maintenance_persists_moved_histories_and_does_not_mutate_reader_input(tmp_path):
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
        maintain_scheduler_tasks, read_scheduler_job_progress,
    )
    from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_default_jobs
    register_fanxiu_default_jobs()
    canonical = next(t for t in default_kernel_scheduler_tasks(NOW) if t['id'] == RESOURCE_DAILY_TASK_ID)
    canonical['payload'] = {'keep': 'configuration'}
    canonical['next_time'] = None
    raw = [canonical, *deepcopy(RETIRED_RAW)]
    before = deepcopy(raw)
    consolidate_arena_scheduler_instances(raw, now=NOW)
    assert raw == before
    state = tmp_path / 'scheduler.json'
    facts = tmp_path / 'facts.json'
    state.write_text(json.dumps(raw), encoding='utf-8')
    maintain_scheduler_tasks(scheduler_state_path=state, world_facts_path=facts, now=NOW)
    saved = json.loads(state.read_text(encoding='utf-8'))
    parent = next(t for t in saved if t['id'] == RESOURCE_DAILY_TASK_ID)
    assert parent['next_time'] == NEXT_MIDNIGHT
    assert parent['payload']['keep'] == 'configuration'
    assert set(parent['payload']['internalized_jobs']) == {stage.task_id for stage in RESOURCE_DAILY_STAGES}
    assert not any(t['id'] in RESOURCE_DAILY_RETIRED_TASK_IDS for t in saved)
    assert read_scheduler_job_progress(RESOURCE_DAILY_TASK_ID, '2026-09-21', scheduler_state_path=state)['legacy-daily-vip']['status'] == 'complete'
    assert resource_daily_completion(RESOURCE_DAILY_STAGES[0], {
        'last_result': 'success', 'finished_at': '2026-09-21 00:01:00',
        'next_time': NEXT_MIDNIGHT, 'job_status': 'failed',
    }) is None

RETIRED_RAW = [
    {
        "id": "legacy-daily-vip",
        "task_type": "daily_vip",
        "next_time": "2026-09-22 00:00:00",
        "last_result": "success",
        "finished_at": "2026-09-21 00:01:00",
        "payload": {"config": "vip"},
    },
    {
        "id": "daily-signin",
        "task_type": "daily_signin",
        "next_time": "2026-09-22 00:00:00",
        "last_result": "error",
        "finished_at": "2026-09-21 00:02:00",
        "last_message": "boom",
    },
    {
        "id": "prayer-daily-resource",
        "task_type": "prayer_daily_resource",
        "next_time": "2026-09-22 00:00:00",
    },
    {
        "id": "legacy-daily-xianshi",
        "task_type": "daily_xianshi",
        "next_time": "2026-09-22 05:00:00",
        "last_result": "running",
    },
    {
        "id": "xianshi-langya-rankings",
        "task_type": "xianshi_langya_rankings",
        "next_time": "2026-09-28 00:05:00",
        "last_result": "success",
        "finished_at": "2026-09-21 00:06:00",
    },
    {
        "id": "xianshi-zhenwuge",
        "task_type": "xianshi_zhenwuge",
        "next_time": "2026-09-28 00:05:00",
        "last_result": "interrupted",
        "finished_at": "2026-09-21 00:07:00",
    },
    {
        "id": "beast-spirit-update",
        "task_type": "beast_spirit_update",
        "next_time": "2026-09-28 00:05:00",
        "last_result": "success",
        "finished_at": "2026-09-21 01:58:32",
        "payload": {"max_source_level": 8},
    },
]


def _canonical_raw() -> dict:
    return {
        "id": RESOURCE_DAILY_TASK_ID,
        "task_type": RESOURCE_DAILY_TASK_TYPE,
        "label": "资源_自动使用",
        "template_label": "资源_自动使用",
        "next_time": "2026-09-21 05:00:00",
        "last_result": "success",
        "last_message": "旧历史",
        "attempt_id": "attempt-canonical",
        "finished_at": "2026-09-21 00:05:00",
        "payload": {"max_rounds": 3},
    }


def _seed() -> list[dict]:
    return [
        {"id": "keep-me", "task_type": "keep_me", "next_time": "2026-09-22 00:00:00", "payload": {"x": 1}},
        _canonical_raw(),
        *deepcopy(RETIRED_RAW),
    ]


def _find(tasks, task_id):
    return next(item for item in tasks if item.get("id") == task_id)


def test_default_catalogue_has_one_daily_canonical_and_no_retired_jobs():
    tasks = default_kernel_scheduler_tasks(NOW)
    matches = [task for task in tasks if task["id"] == RESOURCE_DAILY_TASK_ID]
    assert len(matches) == 1
    canonical = matches[0]
    assert canonical["task_type"] == RESOURCE_DAILY_TASK_TYPE
    assert canonical["label"] == RESOURCE_DAILY_LABEL
    assert canonical["template_label"] == RESOURCE_DAILY_LABEL
    assert canonical["trigger_description"] == RESOURCE_DAILY_TRIGGER_DESCRIPTION
    assert canonical["next_time"] == NEXT_MIDNIGHT
    assert canonical["payload"]["max_execution_seconds"] == 10800

    ids = {str(task.get("id") or "") for task in tasks}
    types = {str(task.get("task_type") or "") for task in tasks}
    assert ids.isdisjoint(RESOURCE_DAILY_RETIRED_TASK_IDS)
    assert types.isdisjoint(RESOURCE_DAILY_RETIRED_TASK_TYPES)


def test_migration_absorbs_retired_jobs_and_preserves_canonical_history():
    migrated, changed = consolidate_arena_scheduler_instances(_seed(), now=NOW)
    ids = {str(item.get("id") or "") for item in migrated}

    assert changed is True
    assert "keep-me" in ids
    assert ids.isdisjoint(RESOURCE_DAILY_RETIRED_TASK_IDS)

    canonical = _find(migrated, RESOURCE_DAILY_TASK_ID)
    # Original execution / attempt / result history is never rewritten.
    assert canonical["last_result"] == "success"
    assert canonical["last_message"] == "旧历史"
    assert canonical["attempt_id"] == "attempt-canonical"
    assert canonical["finished_at"] == "2026-09-21 00:05:00"
    assert canonical["label"] == RESOURCE_DAILY_LABEL
    assert canonical["next_time"] == NEXT_MIDNIGHT

    payload = canonical["payload"]
    assert payload[RESOURCE_DAILY_PAYLOAD_SCHEMA_KEY] == RESOURCE_DAILY_SCHEMA_VERSION
    internalized = payload[RESOURCE_DAILY_INTERNALIZED_JOBS_KEY]
    assert set(internalized) == {stage.task_id for stage in RESOURCE_DAILY_STAGES}
    for original in RETIRED_RAW:
        stage_id = "prayer-update" if original["id"] == "prayer-daily-resource" else original["id"]
        assert internalized[stage_id] == original

    progress = payload[RESOURCE_DAILY_PROGRESS_KEY]
    completed = progress["2026-09-21"]["legacy-daily-vip"]
    assert completed["stage_version"] == "1"
    assert completed["status"] == "complete"
    assert completed["result"]["migrated"] is True
    assert completed["result"]["from"] == "legacy-daily-vip"
    assert completed["result"]["run_status"] == "success"
    assert completed["result"]["finished_at"] == "2026-09-21 00:01:00"
    assert progress["week:2026-09-21"]["xianshi-langya-rankings"]["status"] == "complete"

    # A failed / pending / future-only retired job never earns a completion.
    assert "daily-signin" not in progress.get("2026-09-21", {})
    assert "prayer-daily-resource" not in progress.get("2026-09-21", {})
    assert "legacy-daily-xianshi" not in progress.get("2026-09-21", {})
    assert "xianshi-zhenwuge" not in progress.get("week:2026-09-21", {})
    assert "beast-spirit-update" not in progress.get("week:2026-09-21", {})


def test_beast_spirit_is_monday_only_with_weekly_receipt():
    stage = next(s for s in RESOURCE_DAILY_STAGES if s.task_type == "beast_spirit_update")
    assert stage.monday_only and stage.weekly
    assert resource_daily_cycle_key(stage, datetime(2026, 9, 21, 0, 0)) == "week:2026-09-21"
    assert resource_daily_completion(stage, RETIRED_RAW[-1]) is None


def test_legacy_prayer_completion_does_not_skip_new_prayer_update() -> None:
    stage = next(s for s in RESOURCE_DAILY_STAGES if s.task_type == "prayer_update")
    old = {
        "id": "prayer-daily-resource",
        "task_type": "prayer_daily_resource",
        "last_result": "success",
        "finished_at": "2026-09-21 00:05:00",
        "next_time": "2026-09-22 00:00:00",
    }
    assert resource_daily_completion(stage, old) is None


def test_migration_is_idempotent_and_does_not_rewrite_running_state():
    migrated, _ = consolidate_arena_scheduler_instances(_seed(), now=NOW)
    snapshot = deepcopy(migrated)

    again, changed_again = consolidate_arena_scheduler_instances(migrated, now=NOW)

    assert changed_again is False
    assert again == snapshot

    # A later maintain with a different clock must not overwrite next_time.
    later, changed_later = consolidate_arena_scheduler_instances(
        again, now=datetime(2026, 9, 21, 18, 0, 0)
    )
    assert changed_later is False
    assert _find(later, RESOURCE_DAILY_TASK_ID)["next_time"] == NEXT_MIDNIGHT


def test_canonical_is_not_created_when_no_retired_instance_exists():
    raw = [{"id": "keep-me", "task_type": "keep_me", "next_time": "2026-09-22 00:00:00"}]

    migrated, changed = consolidate_arena_scheduler_instances(raw, now=NOW)

    assert changed is False
    assert [item["id"] for item in migrated] == ["keep-me"]


def test_future_next_time_alone_is_not_a_completion_credential():
    stage = RESOURCE_DAILY_STAGES[0]
    assert resource_daily_completion(
        stage, {"id": stage.task_id, "next_time": "2030-01-01 00:00:00"}
    ) is None
    assert resource_daily_completion(
        stage, {"id": stage.task_id, "last_result": "error", "finished_at": "2026-09-21 00:00:00"}
    ) is None
    assert resource_daily_completion(
        stage, {"id": stage.task_id, "last_result": "success"}
    ) is None


def test_weekly_stage_completion_uses_monday_cycle_key():
    stage = next(item for item in RESOURCE_DAILY_STAGES if item.task_id == "xianshi-zhenwuge")
    assert stage.weekly is True

    completion = resource_daily_completion(
        stage,
        {"id": "xianshi-zhenwuge", "last_result": "success", "finished_at": "2026-09-23 10:00:00", "next_time": "2026-09-28 00:05:00"},
    )

    assert completion is not None
    cycle_key, record = completion
    assert cycle_key == "week:2026-09-21"
    assert record["status"] == "complete"


class _WriteStub:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str | None]] = []
        self.logs: list[tuple[str, str]] = []

    def _persist_scheduler_task_next_time(self, task_id: str, next_time: str | None) -> None:
        self.writes.append((task_id, next_time))

    def _log(self, level: str, message: str) -> None:
        self.logs.append((level, message))

    def _next_daily_boss_reset_time_text(self) -> str:
        return NEXT_MIDNIGHT

    def _next_daily_vip_reset_time_text(self) -> str:
        return NEXT_MIDNIGHT

    def _now(self) -> datetime:
        return NOW


def test_daily_xianshi_and_vip_records_only_log_when_schedule_disabled():
    stub = _WriteStub()

    BehaviorTreeExecutor._record_daily_xianshi_done(stub, {"schedule": False}, message="免费宝匣已领取")
    BehaviorTreeExecutor._record_daily_vip_done(stub, {"schedule": False}, message="已返回世界")

    assert stub.writes == []
    assert len(stub.logs) == 2

    BehaviorTreeExecutor._record_daily_xianshi_done(
        stub, {"__scheduler_task_id": "legacy-daily-xianshi"}, message="免费宝匣已领取"
    )
    BehaviorTreeExecutor._record_daily_vip_done(
        stub, {"__scheduler_task_id": "legacy-daily-vip"}, message="已返回世界"
    )

    assert [task_id for task_id, _ in stub.writes] == [
        "legacy-daily-xianshi",
        "legacy-daily-vip",
    ]


def test_daily_xianshi_recheck_cannot_pretend_completion_under_aggregation():
    stub = _WriteStub()

    with pytest.raises(RuntimeError, match="聚合调度"):
        BehaviorTreeExecutor._schedule_daily_xianshi_next_check(
            stub, {"schedule": False}, message="等待首项刷新", seconds=300
        )

    assert stub.writes == []


def test_signin_and_prayer_results_only_log_when_schedule_disabled():
    class SigninStub(DailySigninTaskMixin):
        def __init__(self) -> None:
            self.writes = []

        def _persist_scheduler_task_next_time(self, task_id, next_time) -> None:
            self.writes.append((task_id, next_time))

    signin = SigninStub()
    result = signin._daily_signin_result(
        "already_claimed",
        "日常_签到：已领取",
        payload={"__scheduler_task_id": "daily-signin", "schedule": False},
    )
    assert signin.writes == []
    assert "下次" not in result["message"]

    signin_writes = SigninStub()
    signin_writes._daily_signin_result(
        "already_claimed",
        "日常_签到：已领取",
        payload={"__scheduler_task_id": "daily-signin"},
    )
    assert [task_id for task_id, _ in signin_writes.writes] == ["daily-signin"]

    class PrayerStub(PrayerDailyResourceTaskMixin):
        def __init__(self) -> None:
            self.writes = []

        def _persist_scheduler_task_next_time(self, task_id, next_time) -> None:
            self.writes.append((task_id, next_time))

    prayer = PrayerStub()
    prayer._prayer_daily_result(
        {"__scheduler_task_id": "prayer-daily-resource", "schedule": False},
        outcome="claimed",
        message="祈愿_每日资源：已领取",
    )
    assert prayer.writes == []


def test_xianshi_exchange_record_only_logs_when_schedule_disabled():
    class ExchangeStub(XianshiExchangeTaskMixin):
        def __init__(self) -> None:
            self.writes = []

        def _persist_scheduler_task_next_time(self, task_id, next_time) -> None:
            self.writes.append((task_id, next_time))

    stub = ExchangeStub()
    next_time = stub._record_xianshi_exchange_done(
        {"schedule": False}, default_task_id="xianshi-zhenwuge", now=NOW
    )
    assert stub.writes == []
    assert next_time == "2026-09-28 00:05:00"

    writes_stub = ExchangeStub()
    writes_stub._record_xianshi_exchange_done(
        {}, default_task_id="xianshi-zhenwuge", now=NOW
    )
    assert writes_stub.writes == [("xianshi-zhenwuge", "2026-09-28 00:05:00")]
