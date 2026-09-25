"""Reject contradictory count inputs before any game interaction."""

import pytest

from backend.core.fanxiu.runtime_gui.integer_count_control import (
    IntegerSliderAssets,
    set_verified_integer_slider_count,
)


@pytest.mark.parametrize("current,desired,maximum", [
    (90, 105, 100),  # A small residual must not bypass the upper bound.
    (105, 105, 100),  # Neither may the already-exact path.
    (105, 100, 100),
    (1, 1, 0),
    (1, 1, True),
    (1, 1, 1.5),
])
def test_invalid_limits_fail_before_context_is_used(current, desired, maximum):
    operation = set_verified_integer_slider_count(
        None, IntegerSliderAssets(settings_scene_id=706), desired,
        maximum=maximum, initial_count=current, max_adjustments=10,
    )
    with pytest.raises(ValueError):
        next(operation)
