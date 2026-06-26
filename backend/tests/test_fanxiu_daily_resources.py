from backend.core.fanxiu.data_annotation import runtime_runner  # noqa: F401
from backend.core.fanxiu.data_annotation.tasks.daily_resources import DailyResourceTaskMixin


def test_daily_gongfeng_law_progress_strips_previous_required_prefix_when_current_is_enough():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("800011400/8000") == (11400, 8000)


def test_daily_gongfeng_law_progress_strips_previous_required_prefix_when_current_is_insufficient():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("80001400/8000") == (1400, 8000)


def test_daily_gongfeng_law_progress_keeps_plain_progress():
    task = DailyResourceTaskMixin()

    assert task._parse_daily_gongfeng_law_progress("11400/8000") == (11400, 8000)
