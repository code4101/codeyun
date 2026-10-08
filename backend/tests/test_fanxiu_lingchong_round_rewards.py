import pytest

from backend.core.fanxiu.activity.lingchong_round_rewards import build_lingchong_round_rewards


def facts():
    tasks = [{"id": n, "times": (n - 1) // 10 + 1, "sub": ((n - 1) // 10) * 100,
              "finishCondition": [f"ActivityItemScore|2_{n * 10}_1"]} for n in range(1, 21)]
    boxes = [{"id": 100 + n, "grade": n, "process": n * 10, "reward": []} for n in (1, 2)]
    quest = {"ok": True, "finished_task_ids": list(range(1, 11)),
             "task_entries": [{"taskId": n, "status": 3,
                               "progressList": [{"progress": 100, "finish": False}]} for n in range(11, 21)]}
    box = {"complete": True, "active_num": 10, "claimed_config_ids": []}
    return tasks, boxes, quest, box


def test_claimed_tasks_do_not_skip_unclaimed_round_box():
    result = build_lingchong_round_rewards(*facts())
    assert result["current_round"] == 1
    assert result["rounds"][0]["box_claimable"]
    assert result["score"] == 100
    assert not result["all_claimed"]


def test_next_round_requires_both_claim_receipts():
    tasks, boxes, quest, box = facts()
    box["claimed_config_ids"] = [101]
    result = build_lingchong_round_rewards(tasks, boxes, quest, box)
    assert result["current_round"] == 2
    assert result["rounds"][1]["target"] == 100
    quest["task_entries"] = []
    with pytest.raises(RuntimeError, match="尚未加载"):
        build_lingchong_round_rewards(tasks, boxes, quest, box)


def test_unknown_or_unreadable_box_fails_closed():
    tasks, boxes, quest, box = facts()
    box["claimed_config_ids"] = [999]
    with pytest.raises(RuntimeError, match="身份"):
        build_lingchong_round_rewards(tasks, boxes, quest, box)
    box["complete"] = False
    with pytest.raises(RuntimeError, match="不可读"):
        build_lingchong_round_rewards(tasks, boxes, quest, box)
