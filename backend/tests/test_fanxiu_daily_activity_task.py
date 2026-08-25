from datetime import datetime

from backend.core.fanxiu.data_annotation import behavior_tree_runtime
from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks


class _Runner(behavior_tree_runtime.DailyFoundationTaskMixin):
    pass


def test_daily_activity_default_schedule_starts_at_seven():
    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["id"] == "legacy-daily-activity"
    )

    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert task["error_retry_delay_seconds"] == 3600


def test_daily_activity_success_schedules_next_day_at_seven(monkeypatch):
    monkeypatch.setattr(
        behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 7, 22, 14, 30, 0),
    )

    assert _Runner()._next_daily_activity_time_text() == "2026-07-23 07:00:00"
