from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.tasks.daily_assistant import (
    DailyAssistantTaskMixin,
    _daily_assistant_business_date,
    daily_assistant_confirmed_lingshi_cost,
    daily_assistant_lingshi_cost_limit,
)
from backend.core.fanxiu.data_annotation.tasks.daily_challenge import DailyChallengeTaskMixin


@pytest.mark.parametrize(("text", "cost"), [
    ("本次执行预计消耗 ０ 灵石，是否继续执行？", 0),
    ("本次执行预计消耗100灵石，是否继续？", 100),
    ("是否继续执行？", None),
    ("预计消耗0灵石，预计消耗100灵石", None),
])
def test_assistant_cost_must_be_explicit_and_unambiguous(text, cost):
    assert daily_assistant_confirmed_lingshi_cost(text) == cost


def test_assistant_cost_limit_keeps_default_and_no_purchase_precedence():
    assert daily_assistant_lingshi_cost_limit({}) is None
    assert daily_assistant_lingshi_cost_limit({'maximum_lingshi_cost': 140}) == 140
    assert daily_assistant_lingshi_cost_limit({'maximum_lingshi_cost': 140, 'skip_purchase': True}) == 0
    for invalid in (-1, True, 140.0, '140'):
        with pytest.raises(ValueError):
            daily_assistant_lingshi_cost_limit({'maximum_lingshi_cost': invalid})


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
