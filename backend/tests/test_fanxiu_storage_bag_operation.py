"""储物袋调度契约。场景、点击、奖励链须在真实游戏验收。"""
import threading
from datetime import datetime
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
