import pytest

from backend.core.fanxiu.catalog.inventory_models import FanxiuSpiritArtifactPartRow


@pytest.mark.parametrize(
    "rank,affixes,names,expected",
    [
        (0, [], [], "待识别"),
        (5, [], [], "普通"),
        (6, [], [], "待识别"),
        (6, [None] * 4, [], "待识别"),
        (6, ["满"] * 3, ["灵器无双", "混沌道威"], "错升"),
        (6, ["满"] * 4, [], "升阶"),
        (6, ["满"] * 4, ["灵器无双"], "无双"),
        (6, ["满"] * 4, ["灵器无双", "混沌道威"], "道威"),
        (6, ["满"] * 3 + ["巅"], ["灵器无双", "混沌道威"], "巅峰"),
        (6, ["巅"] * 4, [], "升阶"),
    ],
)
def test_stage_requires_complete_facts_and_nested_conditions(rank, affixes, names, expected):
    effects = [{} if affix is None else {"affix": affix} for affix in affixes]
    effects += [{"name": name, "affix": ""} for name in names]
    row = FanxiuSpiritArtifactPartRow(rank=rank, runtime_effects=effects)
    assert row.model_dump()["stage"] == expected
