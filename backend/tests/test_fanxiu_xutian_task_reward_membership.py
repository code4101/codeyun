"""Pure safety contract for versioned reward membership; no fake game flow."""

from backend.core.fanxiu.instrumentation import xutian_task_rewards as rewards
from backend.core.fanxiu.data_annotation.tasks.xutian_native_auto import xutian_consumable_permissions
import pytest


def test_consumable_authorization_requires_boolean():
    assert not any(xutian_consumable_permissions({}).values())
    assert xutian_consumable_permissions({"allow_item_refill": True, "allow_boost_items": False})["allow_item_refill"] is True
    for value in ("false", "true", 1):
        with pytest.raises(ValueError, match="显式布尔值"):
            xutian_consumable_permissions({"allow_boost_items": value})


def test_runtime_membership_selects_one_version_per_slot(monkeypatch):
    monkeypatch.setattr(rewards, "_task_rows", lambda: [
        {"id": 1, "activityId": 9, "type": 3, "subType": 4, "sort": 1},
        {"id": 2, "activityId": 9, "type": 3, "subType": 2, "sort": 2},
        {"id": 3, "activityId": 9, "type": 3, "subType": 2, "sort": 2},
    ])
    entry = {"taskId": 3, "status": 4, "turn": 1, "rewardTime": 0, "progressList": []}
    result = rewards.build_xutian_task_reward_snapshot(activity_id=9, task_entries=[entry], finished_task_ids=[1])
    assert result["complete"] is True
    assert result["authorized_claim_task_ids"] == [3]
    for finished in ([1, 2], []):
        blocked = rewards.build_xutian_task_reward_snapshot(activity_id=9, task_entries=[entry], finished_task_ids=finished)
        assert blocked["complete"] is False
        assert blocked["authorized_claim_task_ids"] == []
