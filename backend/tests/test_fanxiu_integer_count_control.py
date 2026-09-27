"""整数控制器的纯分支判定；真实拖动、点击、回读由游戏验收。"""
from types import SimpleNamespace
import pytest
from backend.core.fanxiu.runtime_gui.integer_count_control import set_verified_integer_slider_count
from backend.core.fanxiu.runtime_gui.integer_count_control import (
    IntegerButtonAssets,
    _button_click_counts,
    _estimated_button_actions,
    set_verified_integer_button_count,
)

ASSETS = SimpleNamespace(
    settings_scene_id=658,
    count_region="次数",
    count_decrease="减少",
    count_increase="增加",
    count_slider_thumb="滑块",
    count_minimum_marker=None,
    count_slider_left_anchor="左端",
    count_slider_right_anchor="右端",
)



def _finish(generator):
    with pytest.raises(StopIteration) as stopped:
        while True:
            next(generator)
    return stopped.value.value


@pytest.mark.parametrize("track_only", [False, True])
@pytest.mark.parametrize("large_step", [0, 10])
@pytest.mark.parametrize("error", [0, -11, -10, -9, 9, 10, 11, 167])
def test_initial_phase_uses_count_error_threshold(
    monkeypatch, track_only, large_step, error,
) -> None:
    # Check only deterministic phase selection; do not simulate game feedback.
    from backend.core.fanxiu.runtime_gui import integer_count_control as control

    assets = SimpleNamespace(
        **{**ASSETS.__dict__, "count_slider_thumb": None if track_only else "滑块"},
        count_slider_track="滑条" if track_only else None,
        count_decrease_large="-10" if large_step else None,
        count_increase_large="+10" if large_step else None,
        count_large_step=large_step,
    )

    class PhaseSelected(Exception):
        pass

    def fine(*args, **kwargs):
        raise PhaseSelected("fine")

    def proportional(*args, **kwargs):
        raise PhaseSelected("proportional")

    monkeypatch.setattr(control, "_fine_tune_batches", fine)
    monkeypatch.setattr(control, "_slider_geometry", lambda *args: None)
    monkeypatch.setattr(control, "_proportional_position", proportional)
    monkeypatch.setattr(control, "_set_track_only_count", proportional)
    operation = set_verified_integer_slider_count(
        None, assets, 200 + error, initial_count=200,
        maximum=1000, max_adjustments=10,
    )
    if error == 0:
        assert _finish(operation)["phase"] == "already_exact"
    else:
        expected = "fine" if abs(error) <= 10 else "proportional"
        with pytest.raises(PhaseSelected, match=f"^{expected}$"):
            next(operation)


@pytest.mark.parametrize("initial", [True, False, 1.9, "1", 0, -1])
@pytest.mark.parametrize("slider", [False, True])
def test_initial_count_must_be_a_positive_integer_before_any_action(initial, slider):
    # A truncated initial value could falsely report already_exact or send
    # the wrong number of taps. Both public entrances reject it before UI use.
    operation = (
        set_verified_integer_slider_count(
            None, ASSETS, 1, max_adjustments=10, initial_count=initial,
        ) if slider else set_verified_integer_button_count(
            None, IntegerButtonAssets(658), 1, initial_count=initial,
        )
    )
    with pytest.raises(ValueError, match="初始值必须为正整数"):
        next(operation)


@pytest.mark.parametrize("step", [None, 10, 100])
@pytest.mark.parametrize("delta", [0, -201, -19, -1, 1, 19, 201])
def test_button_budget_matches_exact_non_overshooting_plan(step, delta):
    assets = IntegerButtonAssets(
        658, count_decrease_large="大步减少" if step else None,
        count_increase_large="大步增加" if step else None,
        count_large_step=step,
    )
    large, unit = _button_click_counts(assets, current=300, desired=300 + delta)
    assert large * (step or 1) + unit == abs(delta)
    assert large >= 0 and unit >= 0
    if step:
        assert unit < step
    else:
        assert large == 0
    assert _estimated_button_actions(assets, current=300, desired=300 + delta) == large + unit
