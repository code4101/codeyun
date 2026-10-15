"""购买次数配置属于纯消费边界；不模拟游戏画面或点击流程。"""

import pytest
from datetime import datetime

from backend.core.fanxiu.data_annotation.tasks.youli import resolve_youli_purchase_limit
from backend.core.fanxiu.data_annotation.tasks.youli import plan_youli_natural_next_time


def test_no_purchase_policy_survives_defaults_and_legacy_alias():
    assert resolve_youli_purchase_limit({}) == 0
    assert resolve_youli_purchase_limit({"buy_uses": 0}) == 0
    assert resolve_youli_purchase_limit({"purchase_uses": 0, "buy_uses": 99}) == 0
    assert resolve_youli_purchase_limit({"purchase_uses": "0"}) == 0
    assert resolve_youli_purchase_limit({"buy_uses": 2}) == 2


@pytest.mark.parametrize("value", [True, False, 2.5, -1, None, "unknown"])
def test_ambiguous_purchase_configuration_cannot_authorize_spending(value):
    with pytest.raises(ValueError):
        resolve_youli_purchase_limit({"purchase_uses": value})


def test_daily_target_and_resource_wait_have_separate_triggers():
    now = datetime(2026, 10, 12, 4, 10)
    assert plan_youli_natural_next_time(now, daily_target_met=True) == "2026-10-13 08:00:00"
    assert plan_youli_natural_next_time(now, daily_target_met=False) == "2026-10-12 04:20:00"
    assert plan_youli_natural_next_time(datetime(2026, 10, 12, 23, 55), daily_target_met=False) == "2026-10-13 00:05:00"
    with pytest.raises(ValueError):
        plan_youli_natural_next_time(now, daily_target_met=False, retry_seconds=0)


def test_youli_daily_target_uses_complete_quest_1004_fact_only():
    from backend.core.fanxiu.data_annotation.tasks.youli import youli_daily_target_complete
    base = {"complete": True, "task_id": 1004, "present": True}
    assert youli_daily_target_complete({**base, "turn": 17, "target_turn": 18, "done": False}) is False
    assert youli_daily_target_complete({**base, "turn": 18, "target_turn": 18, "done": True}) is True
    assert youli_daily_target_complete({"complete": True, "task_id": 1004, "present": False, "done": True}) is True
    for bad in ({**base, "complete": False, "done": True}, {**base, "task_id": 1049, "done": True},
                {**base, "turn": 19, "target_turn": 18, "done": False},
                {"complete": True, "task_id": 1004, "present": False, "done": False}):
        with pytest.raises(RuntimeError):
            youli_daily_target_complete(bad)
