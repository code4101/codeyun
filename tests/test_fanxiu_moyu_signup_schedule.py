from __future__ import annotations

from datetime import datetime

from backend.core.fanxiu.behavior_tree.runtime import (
    create_behavior_tree_runtime_runner,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)


def test_moyu_signup_default_starts_at_next_daily_signup_slot():
    tasks = default_data_annotation_scheduler_tasks(datetime(2026, 7, 31, 4, 0, 0))
    task = next(item for item in tasks if item["id"] == "moyu-signup")

    assert task["trigger_description"] == "每日"
    assert task["next_time"] == "2026-07-31 05:00:00"


def test_moyu_signup_default_afternoon_slot_starts_at_fourteen():
    tasks = default_data_annotation_scheduler_tasks(datetime(2026, 7, 31, 13, 30, 0))
    task = next(item for item in tasks if item["id"] == "moyu-signup")

    assert task["next_time"] == "2026-07-31 14:00:00"


def test_moyu_signup_next_slot_moves_from_morning_to_afternoon_then_next_day():
    runner = create_behavior_tree_runtime_runner()

    assert runner._next_moyu_signup_time(datetime(2026, 7, 31, 5, 5, 0)) == datetime(2026, 7, 31, 14, 0, 0)
    assert runner._next_moyu_signup_time(datetime(2026, 7, 31, 13, 5, 0)) == datetime(2026, 7, 31, 14, 0, 0)
    assert runner._next_moyu_signup_time(datetime(2026, 7, 31, 14, 5, 0)) == datetime(2026, 8, 1, 5, 0, 0)
    assert runner._next_moyu_signup_time(datetime(2026, 7, 31, 18, 0, 0)) == datetime(2026, 8, 1, 5, 0, 0)
