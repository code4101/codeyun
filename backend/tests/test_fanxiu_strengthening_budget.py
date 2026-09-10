import pytest

from backend.core.fanxiu.data_annotation.equipment import strengthening_overshoot_limit


@pytest.mark.parametrize("target, percent, expected", [(8000, 5, 400), (8000, 0, 0), (99, 5, 4)])
def test_overshoot_limit(target, percent, expected):
    assert strengthening_overshoot_limit(target, percent) == expected


@pytest.mark.parametrize("target, percent", [(0, 5), (8000, -1), (8000, 101)])
def test_invalid_overshoot_limit(target, percent):
    with pytest.raises(ValueError):
        strengthening_overshoot_limit(target, percent)
