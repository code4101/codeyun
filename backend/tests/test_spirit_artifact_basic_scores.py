import pytest
from backend.core.fanxiu.catalog.inventory_models import spirit_artifact_basic_scores


def effect(value, maximum, *, kind=1):
    return dict(name="灵力", value=value, normal_max=maximum, type=kind)


def test_prebreak_current_max_and_part_multiplier():
    assert spirit_artifact_basic_scores([effect(386208, 960000)], 2) == {"灵力": 40.23}
    assert spirit_artifact_basic_scores([effect(1440000, 1440000)], 5) == {"灵力": 150.0}
    assert spirit_artifact_basic_scores([effect(1728000, 1440000)], 6) == {"灵力": 180.0}


def test_postbreak_denominator_changes_and_quality_is_orthogonal():
    assert spirit_artifact_basic_scores([effect(1200000, 1200000)], 4) == {"灵力": 100.0}
    assert spirit_artifact_basic_scores([{**effect(386208, 960000), "quality": 3}], 2) == {"灵力": 40.23}


@pytest.mark.parametrize("effects", [[effect(100,100,kind=3)], [effect(100,None)],
    [effect(100,0)], [effect(100,float('inf'))], [effect(100,100),effect(100,100)]])
def test_special_unknown_or_ambiguous_values_are_not_rescaled(effects):
    assert spirit_artifact_basic_scores(effects, 5) == {}
