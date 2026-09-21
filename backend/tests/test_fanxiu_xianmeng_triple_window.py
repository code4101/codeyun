from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.runner import get_behavior_tree_executor_class


@pytest.mark.parametrize(
    ("clock", "end_time", "allowed"),
    [
        ("21:29:59", "22:00", True),
        ("21:30:00", "22:00", False),
        ("21:52:00", "22:00", False),
        ("19:29:59", "20:00", True),
        ("19:30:00", "20:00", False),
    ],
)
def test_triple_attack_closes_thirty_minutes_before_battlefield(clock, end_time, allowed):
    runner_type = get_behavior_tree_executor_class()
    now = datetime.fromisoformat(f"2026-09-21T{clock}+08:00")
    assert runner_type._daily_xianmeng_triple_window_open(
        {"daily_end_time": end_time}, now=now,
    ) is allowed
