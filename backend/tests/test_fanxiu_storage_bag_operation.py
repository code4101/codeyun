"""储物袋调度契约。场景、点击、奖励链须在真实游戏验收。"""
import json
import threading
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation import default_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import default_kernel_scheduler_tasks
from backend.core.fanxiu.data_annotation.tasks import storage_bag_operation
from backend.core.fanxiu.data_annotation.tasks.storage_bag_operation import next_storage_bag_operation_at


def _consume(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def test_next_time_is_following_day_at_0000():
    assert next_storage_bag_operation_at(
        datetime(2026, 8, 11, 0, 30)
    ) == datetime(2026, 8, 12, 0, 0)


def test_storage_bag_cell_is_independent_daily_standard_job():
    default_jobs.register_fanxiu_default_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "storage_bag_operation"
    )
    assert definition is not None
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "storage-bag-operation"
    assert definition.standard_job_description == "每日"
    tasks = default_kernel_scheduler_tasks(datetime(2026, 8, 11, 0, 0))
    matches = [task for task in tasks if task["id"] == "storage-bag-operation"]
    assert len(matches) == 1
    assert matches[0]["task_type"] == "storage_bag_operation"
    assert matches[0]["trigger_description"] == "每日"
    assert matches[0]["next_time"] == "2026-08-12 00:00:00"
    assert matches[0]["payload"] == {"max_rounds": 3}


def test_storage_bag_success_persists_following_daily_trigger(monkeypatch):
    default_jobs.register_fanxiu_default_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "storage_bag_operation"
    )
    writes = []

    class Runner:
        def _persist_scheduler_task_next_time(self, task_id, next_time):
            writes.append((task_id, next_time))

    def execute(*_args, **_kwargs):
        yield None
        return {"ok": True, "outcome": "complete"}

    monkeypatch.setattr(
        storage_bag_operation,
        "execute_storage_bag_operation_task",
        execute,
    )
    monkeypatch.setattr(default_jobs, "job_now", lambda: datetime(2026, 8, 11, 1, 0))
    result = _consume(definition.handler(
        Runner(),
        {},
        {},
        threading.Event(),
    ))

    assert result["ok"] is True
    assert writes == [("storage-bag-operation", "2026-08-12 00:00:00")]


def test_generated_new_stock_persists_one_minute_follow_up(monkeypatch):
    default_jobs.register_fanxiu_default_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "storage_bag_operation"
    )
    writes = []

    class Runner:
        def _persist_scheduler_task_next_time(self, task_id, next_time):
            writes.append((task_id, next_time))

    def execute(*_args, **_kwargs):
        yield None
        return {
            "ok": True,
            "outcome": "partial",
            "remaining_current_items": [{"base_id": 1001}],
            "unresolved_items": [],
        }

    monkeypatch.setattr(
        storage_bag_operation,
        "execute_storage_bag_operation_task",
        execute,
    )
    monkeypatch.setattr(default_jobs, "job_now", lambda: datetime(2026, 10, 9, 3, 20, 42))
    result = _consume(definition.handler(Runner(), {}, {}, threading.Event()))

    assert result["outcome"] == "partial"
    assert writes == [("storage-bag-operation", "2026-10-09 03:21:42")]


def _write_storage_bag_schedule(path, next_time, **extra):
    path.write_text(json.dumps([{
        "id": "storage-bag-operation",
        "next_time": next_time,
        "last_result": "success",
        "finished_at": "2026-10-09 03:00:00",
        **extra,
    }]), encoding="utf-8")


def test_new_stock_wake_advances_future_trigger_to_observation_plus_one_minute(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _write_storage_bag_schedule(path, "2026-10-10 00:00:00")

    result = storage_bag_operation.advance_storage_bag_operation_for_new_stock(
        datetime(2026, 10, 9, 3, 20, 42),
        scheduler_state_path=path,
    )

    assert result == "2026-10-09 03:21:42"
    task = json.loads(path.read_text(encoding="utf-8"))[0]
    assert task["next_time"] == "2026-10-09 03:21:42"


def test_new_stock_wake_never_delays_an_earlier_trigger(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _write_storage_bag_schedule(path, "2026-10-09 03:20:50")

    result = storage_bag_operation.advance_storage_bag_operation_for_new_stock(
        datetime(2026, 10, 9, 3, 20, 42),
        scheduler_state_path=path,
    )

    assert result == "2026-10-09 03:20:50"


def test_new_stock_wake_preserves_a_live_attempt(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _write_storage_bag_schedule(
        path,
        "2026-10-10 00:00:00",
        last_result="running",
        attempt_id="attempt-1",
        started_at="2026-10-09 03:19:00",
        finished_at=None,
    )

    result = storage_bag_operation.advance_storage_bag_operation_for_new_stock(
        datetime(2026, 10, 9, 3, 20, 42),
        scheduler_state_path=path,
    )

    assert result == "2026-10-10 00:00:00"


def _scheduled_next_time(path):
    return json.loads(path.read_text(encoding="utf-8"))[0]["next_time"]


def _acceptance(path, result, *, authorized, enabled=True):
    return storage_bag_operation.advance_storage_bag_from_mail_claim_result(
        result,
        observed_at=datetime(2026, 10, 9, 3, 20, 42),
        authorized_item_ids=authorized,
        enabled=enabled,
        scheduler_state_path=path,
    )


def test_mail_claim_wake_acceptance_is_disabled_by_default(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _write_storage_bag_schedule(path, "2026-10-10 00:00:00")

    outcome = _acceptance(
        path,
        {"result": "success", "claimed_reward_item_ids": ["1001"], "claimed_count": 1},
        authorized=[1001],
        enabled=False,
    )

    assert outcome == {"woke": False, "reason": "acceptance_entry_disabled"}
    assert _scheduled_next_time(path) == "2026-10-10 00:00:00"


@pytest.mark.parametrize("claim_result", ["running", "error", "interrupted", ""])
def test_mail_claim_wake_acceptance_rejects_non_success_result(tmp_path, claim_result):
    path = tmp_path / "scheduler_tasks.json"
    _write_storage_bag_schedule(path, "2026-10-10 00:00:00")

    outcome = _acceptance(
        path,
        {"result": claim_result, "claimed_reward_item_ids": ["1001"], "claimed_count": 1},
        authorized=[1001],
    )

    assert outcome["woke"] is False
    assert outcome["reason"] == "claim_not_successful"
    assert _scheduled_next_time(path) == "2026-10-10 00:00:00"


@pytest.mark.parametrize("result", [
    {"result": "success"},
    {"result": "success", "claimed_count": 3},
    {"result": "success", "claimed_count": 3, "claimed_reward_item_ids": []},
])
def test_mail_claim_wake_acceptance_requires_reward_evidence(tmp_path, result):
    path = tmp_path / "scheduler_tasks.json"
    _write_storage_bag_schedule(path, "2026-10-10 00:00:00")

    outcome = _acceptance(path, result, authorized=[1001])

    assert outcome["woke"] is False
    assert outcome["reason"] == "missing_reward_evidence"
    assert _scheduled_next_time(path) == "2026-10-10 00:00:00"


@pytest.mark.parametrize("authorized,reason", [
    ([], "missing_authorized_selection"),
    ([2002], "no_authorized_reward"),
])
def test_mail_claim_wake_acceptance_requires_authorized_intersection(
    tmp_path, authorized, reason,
):
    path = tmp_path / "scheduler_tasks.json"
    _write_storage_bag_schedule(path, "2026-10-10 00:00:00")

    outcome = _acceptance(
        path,
        {"result": "success", "claimed_reward_item_ids": ["1001"], "claimed_count": 1},
        authorized=authorized,
    )

    assert outcome["woke"] is False
    assert outcome["reason"] == reason
    assert _scheduled_next_time(path) == "2026-10-10 00:00:00"


def test_mail_claim_wake_acceptance_wakes_on_authorized_reward(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _write_storage_bag_schedule(path, "2026-10-10 00:00:00")

    outcome = _acceptance(
        path,
        {"result": "success", "claimed_reward_item_ids": ["1001", "1002"], "claimed_count": 2},
        authorized=[1002],
    )

    assert outcome["woke"] is True
    assert outcome["reason"] == "advanced"
    assert outcome["matched_item_ids"] == ["1002"]
    assert outcome["next_time"] == "2026-10-09 03:21:42"
    assert _scheduled_next_time(path) == "2026-10-09 03:21:42"


def test_mail_claim_wake_acceptance_reports_live_attempt_refusal(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _write_storage_bag_schedule(
        path, "2026-10-10 00:00:00",
        last_result="running", attempt_id="attempt-1",
        started_at="2026-10-09 03:19:00", finished_at=None,
    )

    outcome = _acceptance(
        path,
        {"result": "success", "claimed_reward_item_ids": ["1001"], "claimed_count": 1},
        authorized=[1001],
    )

    assert outcome["woke"] is False
    assert outcome["reason"] == "attempt_active"
    assert _scheduled_next_time(path) == "2026-10-10 00:00:00"


def test_mail_claim_wake_acceptance_reports_retry_backoff_refusal(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _write_storage_bag_schedule(path, "2026-10-10 00:00:00", last_result="error")

    outcome = _acceptance(
        path,
        {"result": "success", "claimed_reward_item_ids": ["1001"], "claimed_count": 1},
        authorized=[1001],
    )

    assert outcome["woke"] is False
    assert outcome["reason"] == "retry_backoff_active"
    assert _scheduled_next_time(path) == "2026-10-10 00:00:00"


def test_mail_claim_wake_acceptance_reports_already_earlier_trigger(tmp_path):
    path = tmp_path / "scheduler_tasks.json"
    _write_storage_bag_schedule(path, "2026-10-09 03:20:50")

    outcome = _acceptance(
        path,
        {"result": "success", "claimed_reward_item_ids": ["1001"], "claimed_count": 1},
        authorized=[1001],
    )

    assert outcome["woke"] is False
    assert outcome["reason"] == "already_due"
    assert _scheduled_next_time(path) == "2026-10-09 03:20:50"
