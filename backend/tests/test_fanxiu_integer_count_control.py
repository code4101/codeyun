"""整数控制器的纯分支判定；真实拖动、点击、回读由游戏验收。"""
from types import SimpleNamespace
import pytest
from backend.core.fanxiu.runtime_gui.integer_count_control import set_verified_integer_slider_count

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
