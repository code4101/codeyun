"""A reward threshold does not clamp a recipient's native favor progress."""

from backend.core.fanxiu.data_annotation.tasks.xianyuan_auto_gift import (
    plan_xianyuan_gift_batch,
)


def test_gift_batch_may_overflow_a_reward_threshold_below_the_track_cap():
    item = {
        "base_id": 37092001,
        "quantity": 10,
        "eligible_targets": [{"npc_id": 4005, "runtime_index": 350}],
    }
    person = {
        "npc_id": 4005,
        "runtime_index": 350,
        "giftable": True,
        "hostile": False,
        "favor": 12000,
        "favor_level": 12,
        "reset_favor": 4000,
        "reset_favor_level": 4,
        "gift_options": [{"item_id": 37092001, "favorability": 700}],
        "favor_thresholds": [
            {"level": level, "favor": level * 1000, "reset_favor": level * 1000}
            for level in range(1, 13)
        ],
        "rewards": [{"level": 5, "state": 0}],
    }

    result = plan_xianyuan_gift_batch(item, person)

    assert result["quantity"] == 2
    assert result["target_favor"] == 5000
    assert result["expected_favor_after"] == 5400
