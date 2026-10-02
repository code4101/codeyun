import pytest

from backend.core.fanxiu.activity.lingzhuang_tasks import build_lingzhuang_task_progress
from backend.core.fanxiu.data_annotation.tasks.lingzhuang_resource_use import estimate_round_material


def test_round_budget_uses_actual_increment_and_rounds_up():
    assert estimate_round_material(remaining_score=11_870_500, consumed=46_000, score_gained=11_500_000) == 47_482
    assert estimate_round_material(remaining_score=101, consumed=40, score_gained=10_000) == 1
    with pytest.raises(ValueError):
        estimate_round_material(remaining_score=100, consumed=40, score_gained=0)


def test_task_projection_uses_configuration_rounds_and_offsets():
    config = [dict(id=1, subType=13, name="装备强化", finishCondition=["EquipStrengthItemNum|100"])]
    entries = [dict(taskId=1, status=3, progressList=[dict(progress=40, finish=False)])]
    for number in range(1, 9):
        for tier in range(1, 11):
            task_id = number * 100 + tier
            target = (number - 1) * 1000 + tier * 100
            config.append(dict(id=task_id, subType=999, times=number, sort=tier, name="积分",
                               sub=(number - 1) * 1000, finishCondition=[f"ActivityItemScore|6_{target}_1"]))
            entries.append(dict(taskId=task_id, status=3, progressList=[dict(progress=1200, finish=False)]))
    claimed = list(range(101, 111))
    facts = build_lingzhuang_task_progress(config, dict(task_entries=entries, finished_task_ids=claimed))
    assert facts["score_total_rounds"] == 8
    assert facts["score_round"] == 2
    assert facts["score_current"] == 200
    assert facts["score_tasks"][0]["target"] == 100
    assert facts["equipment_current"] == 40
    with pytest.raises(RuntimeError, match="尚未加载"):
        build_lingzhuang_task_progress(config, dict(task_entries=entries[:1] + entries[2:], finished_task_ids=[]))
