"""Pure activity-identity gates; no simulated game or GUI."""
from backend.core.fanxiu.instrumentation.yunmeng_task_rewards import (
    select_live_yunmeng_task_reward_snapshot,
)


def test_current_activity_does_not_authorize_an_older_loaded_ladder():
    older = {"ok": True, "available": True, "complete": True, "state": "claimable",
             "activity_id": 1210001, "domain": "yunmeng_1210001_summon",
             "authorized_claim_task_ids": [1160022]}
    current = {**older, "activity_id": 4210001, "domain": "yunmeng_4210001_summon",
               "authorized_claim_task_ids": [4160022]}
    result = select_live_yunmeng_task_reward_snapshot(
        {older["domain"]: older, current["domain"]: current}, activity_id=4210001)
    assert result["complete"] is True
    assert result["authorized_claim_task_ids"] == [4160022]


def test_missing_current_activity_cannot_fall_back_to_old_claims():
    older = {"ok": True, "available": True, "complete": True, "state": "claimable",
             "activity_id": 1210001, "domain": "yunmeng_1210001_summon",
             "authorized_claim_task_ids": [1160022]}
    result = select_live_yunmeng_task_reward_snapshot({older["domain"]: older}, activity_id=4210001)
    assert result["complete"] is False
    assert result["authorized_claim_task_ids"] == []
