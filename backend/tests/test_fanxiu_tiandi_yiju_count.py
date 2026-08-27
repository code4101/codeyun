from __future__ import annotations

import pytest

import backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_count as count_module
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_count import (
    TiandiYijuCountAssets,
    read_tiandi_yiju_round_count,
    set_tiandi_yiju_round_count,
)


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        return stop.value


class _Runtime:
    def __init__(self, readings):
        self.readings = iter(readings)
        self.drags = []

    def ocr_numbers_in_shapes(self, scene_id, shapes):
        value = next(self.readings)
        return ([value] if value is not None else [], f"次数：{value}")

    def drag_shape_to_frame_edge(self, scene_id, shape, *, direction, duration):
        self.drags.append((scene_id, shape, direction, duration))

    def wait_action_settle(self, _seconds):
        if False:
            yield None
        return None


def test_read_count_requires_one_positive_ocr_value() -> None:
    runtime = _Runtime([12])
    assert read_tiandi_yiju_round_count(runtime, TiandiYijuCountAssets()) == 12


def test_read_count_fails_closed_when_ocr_is_empty() -> None:
    runtime = _Runtime([None])
    with pytest.raises(RuntimeError, match="无法唯一读回"):
        read_tiandi_yiju_round_count(runtime, TiandiYijuCountAssets())


def test_exact_target_reuses_shared_verified_slider(monkeypatch) -> None:
    captured = {}

    def _shared(runtime, assets, desired, **options):
        captured.update(
            runtime=runtime,
            assets=assets,
            desired=desired,
            options=options,
        )
        if False:
            yield None
        return {"before": 1, "after": desired, "fine_adjustment_actions": 2}

    monkeypatch.setattr(count_module, "set_verified_integer_slider_count", _shared)
    runtime = object()
    result = _drain(set_tiandi_yiju_round_count(runtime, 30, max_adjustments=6))

    assert result["target"] == 30
    assert result["after"] == 30
    assert captured["desired"] == 30
    assert captured["assets"].settings_scene_id == 680
    assert captured["assets"].count_region == "单次对弈"
    assert captured["options"] == {
        "max_adjustments": 6,
        "force_bound_probe": False,
        "count_label": "天地弈局对弈次数",
    }


def test_maximum_uses_bounded_right_probes_and_stable_readback() -> None:
    runtime = _Runtime([10, 70, 100, 100, 100])
    result = _drain(
        set_tiandi_yiju_round_count(runtime, "max", max_bound_drags=4)
    )

    assert result == {
        "before": 10,
        "after": 100,
        "maximum": 100,
        "target": "max",
        "bound_drag_count": 3,
        "fine_adjustment_actions": 0,
    }
    assert runtime.drags == [
        (680, "对弈次数_滑块", "right", 0.6),
        (680, "对弈次数_滑块", "right", 0.6),
        (680, "对弈次数_滑块", "right", 0.6),
    ]


def test_maximum_rejects_non_monotonic_readback() -> None:
    runtime = _Runtime([50, 40])
    with pytest.raises(RuntimeError, match="未单调增加"):
        _drain(set_tiandi_yiju_round_count(runtime, "max"))


def test_maximum_stops_at_bound_budget() -> None:
    runtime = _Runtime([1, 10, 20])
    with pytest.raises(RuntimeError, match="未确认最大值"):
        _drain(
            set_tiandi_yiju_round_count(runtime, "max", max_bound_drags=2)
        )


@pytest.mark.parametrize("target", [0, -1, True, "all"])
def test_invalid_target_is_rejected(target) -> None:
    with pytest.raises(ValueError, match="正整数或 max"):
        _drain(set_tiandi_yiju_round_count(_Runtime([]), target))
