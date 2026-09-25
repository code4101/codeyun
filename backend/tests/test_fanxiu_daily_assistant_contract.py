from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.tasks.daily_assistant import (
    DailyAssistantTaskMixin,
    _daily_assistant_business_date,
)
from backend.core.fanxiu.data_annotation.tasks.daily_challenge import DailyChallengeTaskMixin


@pytest.mark.parametrize(
    ("clock", "business_date"),
    [("00:00:00", None), ("04:59:59", None), ("05:00:00", "2026-09-26"), ("23:59:59", "2026-09-26")],
)
def test_assistant_lilian_trigger_starts_at_five(clock, business_date):
    assert _daily_assistant_business_date(datetime.fromisoformat(f"2026-09-26 {clock}")) == business_date


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("剩余时间 ０３：２５", 205), ("剩余时间 00:59", 59), ("剩余时间 12:65", None), ("助手正在寻路", None)],
)
def test_assistant_progress_time_parser(text, seconds):
    assistant = DailyAssistantTaskMixin()
    assert assistant._daily_assistant_one_key_progress_seconds(text) == seconds


def test_challenge_composition_retains_assistant_entry():
    assert DailyChallengeTaskMixin._execute_daily_assistant_task is DailyAssistantTaskMixin._execute_daily_assistant_task
