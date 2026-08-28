from __future__ import annotations

import pytest

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
        self.clicks = []

    def ocr_numbers_in_shapes(self, scene_id, shapes):
        value = next(self.readings)
        return ([value] if value is not None else [], f"次数：{value}")

    def drag_shape_to_frame_edge(self, scene_id, shape, *, direction, duration):
        self.drags.append((scene_id, shape, direction, duration))

    def wait_action_settle(self, _seconds):
        if False:
            yield None
        return None

    def click_shape_center(self, scene_id, shape):
        self.clicks.append((scene_id, shape))


def test_read_count_requires_one_positive_ocr_value() -> None:
    runtime = _Runtime([12])
    assert read_tiandi_yiju_round_count(runtime, TiandiYijuCountAssets()) == 12


def test_read_count_fails_closed_when_ocr_is_empty() -> None:
    runtime = _Runtime([None])
    with pytest.raises(RuntimeError, match="无法唯一读回"):
        read_tiandi_yiju_round_count(runtime, TiandiYijuCountAssets())


def test_exact_target_only_moves_from_minimum_with_verified_plus_steps() -> None:
    runtime = _Runtime([4, 1, 2, 3, 3])
    result = _drain(set_tiandi_yiju_round_count(runtime, 3))

    assert result == {
        "before": 4,
        "after": 3,
        "target": 3,
        "reset_to_minimum": True,
        "increase_actions": 2,
        "native_maximum_probed": False,
    }
    assert runtime.drags == [(680, "对弈次数_滑块", "left", 0.6)]
    assert runtime.clicks == [(680, "对弈次数_增加"), (680, "对弈次数_增加")]


def test_right_bound_probe_is_forbidden() -> None:
    with pytest.raises(ValueError, match="禁止向右探测"):
        _drain(set_tiandi_yiju_round_count(_Runtime([]), 10, force_bound_probe=True))


def test_adjustment_budget_must_cover_monotonic_increase() -> None:
    with pytest.raises(ValueError, match="调整预算不足"):
        _drain(set_tiandi_yiju_round_count(_Runtime([]), 30, max_adjustments=10))


def test_plus_step_must_be_exactly_one() -> None:
    runtime = _Runtime([1, 1, 3])
    with pytest.raises(RuntimeError, match="没有按单步递增"):
        _drain(set_tiandi_yiju_round_count(runtime, 2))


@pytest.mark.parametrize("target", [0, -1, True, "all", "max", 101, 4451])
def test_invalid_target_is_rejected(target) -> None:
    with pytest.raises(ValueError, match=r"1\.\.100.*禁止使用原生最大值"):
        _drain(set_tiandi_yiju_round_count(_Runtime([]), target))
