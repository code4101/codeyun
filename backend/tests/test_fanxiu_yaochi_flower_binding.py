"""榜期持久绑定及纯循环决策契约；不模拟游戏画面或点击。"""

from copy import deepcopy

import pytest
from sqlmodel import Session, create_engine

from backend.models import FanxiuPacketBusinessRecord
from backend.core.fanxiu.activity.yaochi_flower_recipient_store import (
    bind_yaochi_flower_recipient, load_yaochi_flower_recipient_binding,
)
from backend.core.fanxiu.activity.yaochi_flower_strategy import (
    plan_yaochi_flower_cycle_action, plan_yaochi_flower_recipient,
)


def _plan(key="period-1", npc_id=1207):
    return {
        "status": "ready", "instance_key": key,
        "chosen_npc": {"npc_id": npc_id, "runtime_index": 409},
        "target_gongfa": {"book_id": 306401},
        "recommendation": {"npc_id": npc_id, "reset_step": 1},
    }


def test_binding_survives_new_session_and_cannot_be_replaced(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'bindings.db').as_posix()}")
    FanxiuPacketBusinessRecord.__table__.create(engine)
    with Session(engine) as session:
        assert load_yaochi_flower_recipient_binding(session, instance_key="period-1") is None
        original = bind_yaochi_flower_recipient(session, instance_key="period-1", plan=_plan())
    with Session(engine) as session:
        repeated = bind_yaochi_flower_recipient(session, instance_key="period-1", plan=_plan(npc_id=2202))
        assert original == repeated
        assert repeated["npc_id"] == 1207
        assert "runtime_index" not in str(repeated)
        other = bind_yaochi_flower_recipient(session, instance_key="period-2", plan=_plan("period-2", 2202))
        assert other["npc_id"] == 2202
    engine.dispose()


def test_locked_recipient_uses_current_index_and_never_reselects():
    binding = {**_plan(), "npc_id": 1207}
    inputs = (
        {"runtime_complete": True, "books": [{"book_id": 999}]},
        {"runtime_complete": True, "people": [{
            "npc_id": 1207, "runtime_index": 888, "giftable": True,
            "activity_flower_gift_count": 1,
        }]},
        {"complete": True, "items": []},
    )
    plan = plan_yaochi_flower_recipient(*inputs, instance_key="period-1", binding=binding)
    assert plan["chosen_npc"]["runtime_index"] == 888
    assert plan["target_gongfa"]["book_id"] == 306401
    assert plan["recipient_locked"] is True
    with pytest.raises(ValueError, match="榜期不一致"):
        plan_yaochi_flower_recipient(*inputs, instance_key="period-2", binding=binding)
    hostile = deepcopy(inputs)
    hostile[1]["people"][0]["hostile"] = True
    with pytest.raises(ValueError, match="不重新选人"):
        plan_yaochi_flower_recipient(*hostile, instance_key="period-1", binding=binding)


@pytest.mark.parametrize("capacity,rewards,can_reset,remaining,action", [
    (28000, 0, False, 60000, "gift"),
    (0, 1, True, 60000, "claim_rewards"),
    (0, 0, True, 60000, "reset"),
    (0, 0, False, 60000, "blocked"),
    (88000, 0, False, 60000, "gift"),
    (0, 1, True, 0, "claim_rewards"),
    (0, 0, True, 0, "complete"),
])
def test_same_recipient_claim_reset_repeat(capacity, rewards, can_reset, remaining, action):
    result = plan_yaochi_flower_cycle_action(_plan(), {
        "complete": True, "npc_id": 1207, "reset_step": 1,
        "gift_capacity_friendship": capacity, "claimable_reward_count": rewards,
        "can_reset": can_reset,
    }, remaining_friendship=remaining)
    assert result["action"] == action
    assert result["npc_id"] == 1207
    if action == "gift":
        assert result["friendship_limit"] == min(capacity, remaining)


def test_unknown_or_other_reset_step_blocks():
    result = plan_yaochi_flower_cycle_action(_plan(), {
        "complete": True, "npc_id": 1207, "reset_step": 2,
        "gift_capacity_friendship": 88000, "claimable_reward_count": 0,
        "can_reset": True,
    }, remaining_friendship=60000)
    assert result["action"] == "blocked"
