"""Pure config/QuestEntry join contracts; no simulated game execution."""
import pytest

from backend.core.fanxiu.instrumentation.shequn_lianti_task_rewards import (
    build_shequn_lianti_task_spec,
)


def rows():
    return [dict(id=804340151 + index, activityId=8043401, sort=index + 1,
                 finishCondition=[f"PhysicalFightScore|{target}"])
            for index, target in enumerate((5000, 10000, 20000))]


def entries(config):
    return [dict(taskId=row["id"], progressList=[dict(
        target=int(row["finishCondition"][0].split("|")[1]), progress=0)])
        for row in config]


def test_selects_exact_live_ladder_and_highest_reward_target():
    config = rows()
    retained = [{**row, "id": row["id"] + 14} for row in config]
    spec = build_shequn_lianti_task_spec(
        config + retained, task_entries=entries(config), finished_task_ids=[])
    assert spec.task_ids == (804340151, 804340152, 804340153)
    assert spec.thresholds == (5000, 10000, 20000)


def test_already_claimed_ids_remain_members():
    config = rows()
    spec = build_shequn_lianti_task_spec(
        config, task_entries=[], finished_task_ids=[row["id"] for row in config])
    assert len(spec.task_ids) == 3


@pytest.mark.parametrize("case", ["missing", "mixed", "wrong_target", "wrong_condition", "missing_progress"])
def test_rejects_incomplete_or_mismatched_membership(case):
    config = rows()
    live = entries(config)
    if case == "missing":
        live.pop()
    elif case == "mixed":
        config.append({**config[0], "id": 804340165})
        live.append({**live[0], "taskId": 804340165})
    elif case == "wrong_target":
        live[0]["progressList"][0]["target"] = 1
    elif case == "missing_progress":
        live[0]["progressList"][0].pop("progress")
    else:
        config[0]["finishCondition"] = ["OtherScore|5000"]
    with pytest.raises(ValueError):
        build_shequn_lianti_task_spec(config, task_entries=live, finished_task_ids=[])
