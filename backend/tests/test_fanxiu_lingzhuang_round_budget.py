import pytest

from backend.core.fanxiu.activity.lingzhuang_tasks import build_lingzhuang_task_progress
from backend.core.fanxiu.data_annotation.tasks.lingzhuang_resource_use import estimate_round_material
from backend.core.fanxiu.data_annotation.tasks.lingzhuang_resource_use import parse_lingzhuang_round_header


def test_round_budget_uses_actual_increment_and_rounds_up():
    assert estimate_round_material(remaining_score=11_870_500, consumed=46_000, score_gained=11_500_000) == 47_482
    assert estimate_round_material(remaining_score=101, consumed=40, score_gained=10_000) == 1
    with pytest.raises(ValueError):
        estimate_round_material(remaining_score=100, consumed=40, score_gained=0)


@pytest.mark.parametrize("title,state,expected", [
    ("当前轮次 1/8", "可领取", dict(round=1, total=8, state="可领取")),
    ("当前轮次2／8", "未完成", dict(round=2, total=8, state="未完成")),
    ("当前轮次8/8", "已领取", dict(round=8, total=8, state="已领取")),
    ("当前轮次9/8", "可领取", None),
    ("当前轮次2/8", "未完成获", None),
    ("当前轮次2/8", "领取", None),
])
def test_round_header_requires_explicit_reward_state(title, state, expected):
    assert parse_lingzhuang_round_header(title, state) == expected


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


def test_compact_task_read_rediscovers_after_round_claim(monkeypatch):
    from backend.core.fanxiu.activity import lingzhuang_tasks as tasks

    config = [dict(id=1, subType=13, name="装备强化", finishCondition=["EquipStrengthItemNum|100"])]
    for number in (1, 2):
        for tier in range(1, 11):
            config.append(dict(id=number * 100 + tier, subType=999, times=number, sort=tier,
                               name="积分", sub=(number - 1) * 1000,
                               finishCondition=[f"ActivityItemScore|6_{(number - 1) * 1000 + tier * 100}_1"]))
    claimed = []
    discoveries = []
    bindings = []

    def snapshot(ids):
        return dict(ok=True, captured_at="now", finished_task_ids=list(claimed),
                    task_entries=[dict(taskId=i, status=3,
                                       progressList=[dict(progress=1200, finish=False)]) for i in ids])

    def discover(*args, **kwargs):
        discoveries.append(True)
        return snapshot([row["id"] for row in config])

    def fast(spec, **kwargs):
        bindings.append(spec.task_ids)
        return snapshot(spec.task_ids)

    monkeypatch.setattr(tasks, "_BOUND_TASK_SPECS", {})
    monkeypatch.setattr(tasks, "lingzhuang_task_config", lambda activity_id: config)
    monkeypatch.setattr(tasks, "read_activity_task_reward_snapshots", discover)
    monkeypatch.setattr(tasks, "read_task_reward_spec_fast_snapshot", fast)
    assert tasks.read_lingzhuang_task_progress(99)["score_round"] == 1
    claimed.extend(range(101, 111))
    assert tasks.read_lingzhuang_task_progress(99)["score_round"] == 2
    assert tasks.read_lingzhuang_task_progress(99)["score_current"] == 200
    assert len(discoveries) == 2
    assert set(bindings[0]) == {1, *range(101, 111)}
    assert set(bindings[1]) == {1, *range(201, 211)}
