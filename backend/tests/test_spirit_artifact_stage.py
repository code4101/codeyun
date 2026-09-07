import pytest

from backend.core.fanxiu.catalog.inventory_models import FanxiuSpiritArtifactPartRow


@pytest.mark.parametrize(
    "rank,affixes,names,expected",
    [
        (0, [], [], "待识别"),
        (1, [], [], "待识别"),
        (5, [], [], "待识别"),
        (6, [], [], "待识别"),
        (6, [None] * 4, [], "待识别"),
        (6, ["满"] * 3, ["灵器无双", "混沌道威"], "错升"),
        (6, ["满"] * 4, [], "突破"),
        (6, ["满"] * 4, ["灵器无双"], "无双"),
        (6, ["满"] * 4, ["灵器无双", "混沌道威"], "道威"),
        (6, ["满"] * 3 + ["巅"], ["灵器无双", "混沌道威"], "巅峰"),
        (6, ["巅"] * 4, [], "突破"),
    ],
)
def test_stage_requires_complete_facts_and_nested_conditions(rank, affixes, names, expected):
    effects = [{} if affix is None else {"affix": affix} for affix in affixes]
    effects += [{"name": name, "affix": ""} for name in names]
    row = FanxiuSpiritArtifactPartRow(rank=rank, runtime_base_id=14000106, runtime_is_break=True, runtime_effects=effects)
    assert row.model_dump()["stage"] == expected


@pytest.mark.parametrize("is_break,expected", [(False, "预备"), (True, "错升"), (None, "待识别")])
def test_rebuilt_high_rank_part_requires_breakthrough_fact(is_break, expected):
    row = FanxiuSpiritArtifactPartRow(
        rank=10, runtime_base_id=14000406, runtime_is_break=is_break,
        runtime_effects=[{"affix": ""}] * 6,
    )
    assert row.stage == expected


def test_non_red_part_is_initial():
    assert FanxiuSpiritArtifactPartRow(rank=1, runtime_base_id=14000405).stage == "初始"


@pytest.mark.parametrize("rank", [1, 5, 6, 10])
def test_wrong_breakthrough_is_independent_of_rank(rank):
    row = FanxiuSpiritArtifactPartRow(rank=rank, runtime_base_id=14000406,
        runtime_is_break=True, runtime_effects=[{"affix": ""}] * 6)
    assert row.stage == "错升"


@pytest.mark.parametrize("rank", [1, 5])
def test_unbroken_red_part_below_six_is_initial(rank):
    row = FanxiuSpiritArtifactPartRow(rank=rank, runtime_base_id=14000406,
        runtime_is_break=False, runtime_effects=[{"affix": ""}] * 6)
    assert row.stage == "初始"


def test_affixes_ready_without_breakthrough_remains_prepared():
    row = FanxiuSpiritArtifactPartRow(rank=6, runtime_base_id=14000406,
        runtime_is_break=False, runtime_effects=[{"affix": "满"}] * 4)
    assert row.stage == "预备"
