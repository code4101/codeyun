"""Pure scale validation and candidate identity contracts, without device I/O."""
import pytest
from backend.core.fanxiu.client.mumu_control import normalize_scan_scales, suppress_overlapping_scan_matches


def test_default_and_explicit_scale_order():
    assert normalize_scan_scales() == (1.0,)
    assert normalize_scan_scales([1, 1.25, 1]) == (1.0, 1.25)


@pytest.mark.parametrize("value", [[], [0], [float("nan")], [float("inf")], [True], ["1.25"], [2.1], [1] * 18])
def test_reject_invalid_scales(value):
    with pytest.raises(ValueError):
        normalize_scan_scales(value)


def test_scale_hypotheses_collapse_but_separate_objects_remain():
    matches = [
        {"box": {"x": 10, "y": 20, "w": 50, "h": 60}, "score": .9},
        {"box": {"x": 8, "y": 18, "w": 63, "h": 75}, "score": .95},
        {"box": {"x": 110, "y": 120, "w": 50, "h": 60}, "score": .91},
    ]
    assert suppress_overlapping_scan_matches(matches) == [matches[1], matches[2]]
    assert len(matches) == 3
