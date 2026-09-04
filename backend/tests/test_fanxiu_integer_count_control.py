from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks.integer_count_control import (
    IntegerButtonAssets,
    IntegerSliderAssets,
    _fine_tune_batches,
    _slider_geometry,
    _stable_read,
    read_positive_integer_count,
    set_verified_integer_button_count,
    set_verified_integer_slider_count,
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


class SliderContext:
    def __init__(
        self,
        *,
        maximum: int = 1000,
        gain: float = 1.0,
        initial_offset: int = 0,
        frozen: bool = False,
        geometry: bool = True,
    ) -> None:
        self.maximum = maximum
        self.gain = gain
        self.initial_offset = initial_offset
        self.frozen = frozen
        self.geometry = geometry
        self.count = 1
        self.thumb_x = 0.0
        self.frame_drags: list[tuple[float, float]] = []
        self.clicks: list[str] = []
        self.fast_clicks: list[str] = []
        self.reads = 0

    def ocr_numbers_in_shapes(self, _scene, _shapes):
        self.reads += 1
        return [self.count], str(self.count)

    def wait_action_settle(self, _seconds):
        if False:
            yield None

    def shape_center(self, _scene, title, *, live=False, strict_live=False):
        if not self.geometry:
            raise AttributeError
        if title == "左端":
            return 0.0, 20.0
        if title == "右端":
            return 100.0, 20.0
        return self.thumb_x, 20.0

    def shape_center_in_box(self, _scene, _title, _search_box):
        if not self.geometry:
            raise AttributeError
        return self.thumb_x, 20.0

    def shape_box(self, _scene, _title):
        return {"w": 0.0}

    def drag_frame_point(
        self, _scene, start_x, _start_y, end_x, _end_y, *, duration_ms
    ):
        assert duration_ms == 1000
        self.frame_drags.append((start_x, end_x))
        if not self.frozen:
            self.thumb_x += (end_x - start_x) * self.gain
            self.thumb_x = min(100.0, max(0.0, self.thumb_x))
            self.count = round(1 + self.thumb_x / 100.0 * (self.maximum - 1))

    def drag_shape_between_shapes_fraction(
        self, _scene, _thumb, _left, _right, *, fraction, duration
    ):
        assert duration == 0.45
        self.count = round(1 + fraction * (self.maximum - 1)) + self.initial_offset

    def click_shape_center(self, _scene, title):
        self.clicks.append(title)
        amount = 10 if title in {"增加10", "+10", "减少10", "-10"} else 1
        self.count += amount if title in {"增加", "增加10", "+", "+10"} else -amount

    def click_shape_center_fast(self, _scene, title):
        self.fast_clicks.append(title)
        amount = 10 if title in {"增加10", "+10", "减少10", "-10"} else 1
        self.count += amount if title in {"增加", "增加10", "+", "+10"} else -amount


def test_slider_geometry_uses_minimum_thumb_center_and_right_track_edge() -> None:
    context = SliderContext()

    def shape_box(_scene, _title):
        return {"x": 80.0, "y": 10.0, "w": 20.0, "h": 20.0}

    context.shape_box = shape_box
    geometry = _slider_geometry(context, ASSETS)

    assert geometry is not None
    assert geometry["left_x"] == 0.0
    assert geometry["right_x"] == 90.0
    assert geometry["y"] == 20.0


def test_proportional_drag_can_land_exactly_without_clicks() -> None:
    context = SliderContext()

    result = _finish(set_verified_integer_slider_count(
        context, ASSETS, 100, maximum=1000, max_adjustments=10,
    ))

    assert result["after"] == 100
    assert result["pixel_probes"] == []
    assert result["fine_batches"] == []
    assert context.clicks == []


def test_coarse_pixel_probe_still_removes_bulk_error_when_grain_exceeds_threshold() -> None:
    context = SliderContext(maximum=5000, gain=0.5)

    result = _finish(set_verified_integer_slider_count(
        context, ASSETS, 100, maximum=5000, max_adjustments=10,
    ))

    assert result["pixel_probes"][0]["count_delta"] > 10
    assert result["coarse_exit"] in {"within_threshold", "within_drag_grain"}
    assert result["after"] == 100
    assert result["fine_adjustment_actions"] <= 10


def test_pixel_probe_fails_closed_when_drag_never_changes_value() -> None:
    context = SliderContext(frozen=True)

    with pytest.raises(RuntimeError, match="未产生有效变化"):
        _finish(set_verified_integer_slider_count(
            context, ASSETS, 100, maximum=1000, max_adjustments=10,
        ))


def test_pixel_probe_keeps_accelerating_beyond_eight_pixels() -> None:
    class ThresholdDragContext(SliderContext):
        def drag_frame_point(
            self, _scene, start_x, _start_y, end_x, _end_y, *, duration_ms
        ):
            assert duration_ms == 1000
            self.frame_drags.append((start_x, end_x))
            if abs(end_x - start_x) < 16:
                return
            self.thumb_x += end_x - start_x
            self.thumb_x = min(100.0, max(0.0, self.thumb_x))
            self.count = round(1 + self.thumb_x / 100.0 * (self.maximum - 1))

    context = ThresholdDragContext(maximum=1000)

    result = _finish(set_verified_integer_slider_count(
        context, ASSETS, 100, maximum=1000, max_adjustments=10,
    ))

    assert result["after"] == 100
    assert any(row["commanded_pixels"] >= 16 for row in result["pixel_probes"])


def test_fine_adjustment_without_large_assets_preserves_unit_click_control() -> None:
    context = SliderContext()
    context.count = 77
    after, batches = _finish(_fine_tune_batches(
        context, ASSETS, 100, current=77,
        count_label="测试次数", runtime_reader=None,
    ))

    assert after == 100
    assert len(batches) <= 5
    assert all(row["clicks"] >= 5 for row in batches[:-1])
    assert context.reads == len(batches) * 2
    assert len(context.fast_clicks) == 23
    assert context.clicks == []


def test_fine_adjustment_falls_back_when_fast_click_is_unavailable() -> None:
    context = SliderContext()
    context.click_shape_center_fast = None
    context.count = 97

    after, batches = _finish(_fine_tune_batches(
        context, ASSETS, 100, current=97,
        count_label="测试次数", runtime_reader=None,
    ))

    assert after == 100
    assert batches == [{
        "before": 97,
        "after": 100,
        "clicks": 3,
        "large_clicks": 0,
        "unit_clicks": 3,
    }]
    assert context.clicks == ["增加", "增加", "增加"]


def test_button_only_controller_uses_large_and_unit_steps_with_stable_reread() -> None:
    assets = IntegerButtonAssets(
        settings_scene_id=470,
        count_region="数量",
        count_decrease="-",
        count_increase="+",
        count_decrease_large="-10",
        count_increase_large="+10",
        count_large_step=10,
    )
    context = SliderContext()
    context.count = 1

    result = _finish(set_verified_integer_button_count(
        context,
        assets,
        35,
        count_label="仙市兑换数量",
        runtime_count_reader=lambda: {"current": context.count},
        initial_count=1,
    ))

    assert result["before"] == 1
    assert result["after"] == 35
    assert result["phase"] == "button_only_closed_loop"
    assert context.fast_clicks == ["+10", "+10", "+10", "+", "+", "+", "+"]


def test_button_only_controller_rejects_invalid_initial_count() -> None:
    assets = IntegerButtonAssets(settings_scene_id=470)
    context = SliderContext()

    with pytest.raises(ValueError, match="初始值必须为正整数"):
        _finish(set_verified_integer_button_count(
            context,
            assets,
            5,
            initial_count=0,
        ))


def test_fine_adjustment_uses_two_large_steps_from_80_to_100() -> None:
    assets = SimpleNamespace(
        **ASSETS.__dict__,
        count_decrease_large="减少10",
        count_increase_large="增加10",
        count_large_step=10,
    )
    context = SliderContext()
    context.count = 80

    after, batches = _finish(_fine_tune_batches(
        context,
        assets,
        100,
        current=80,
        count_label="测试次数",
        runtime_reader=None,
    ))

    assert after == 100
    assert batches == [{
        "before": 80,
        "after": 100,
        "clicks": 2,
        "large_clicks": 2,
        "unit_clicks": 0,
    }]
    assert context.fast_clicks == ["增加10", "增加10"]


def test_fine_adjustment_combines_large_steps_and_unit_remainder() -> None:
    assets = SimpleNamespace(
        **ASSETS.__dict__,
        count_decrease_large="减少10",
        count_increase_large="增加10",
        count_large_step=10,
    )
    context = SliderContext()
    context.count = 83

    after, batches = _finish(_fine_tune_batches(
        context,
        assets,
        100,
        current=83,
        count_label="测试次数",
        runtime_reader=None,
    ))

    assert after == 100
    assert batches[0]["large_clicks"] == 1
    assert batches[0]["unit_clicks"] == 7
    assert context.fast_clicks == ["增加10", *("增加" for _ in range(7))]


def test_fine_adjustment_recomputes_after_a_dropped_large_click() -> None:
    assets = SimpleNamespace(
        **ASSETS.__dict__,
        count_decrease_large="减少10",
        count_increase_large="增加10",
        count_large_step=10,
    )

    class DropFirstLargeContext(SliderContext):
        dropped = False

        def click_shape_center_fast(self, scene, title):
            if title == "增加10" and not self.dropped:
                self.fast_clicks.append(title)
                self.dropped = True
                return
            super().click_shape_center_fast(scene, title)

    context = DropFirstLargeContext()
    context.count = 80

    after, batches = _finish(_fine_tune_batches(
        context,
        assets,
        100,
        current=80,
        count_label="测试次数",
        runtime_reader=None,
    ))

    assert after == 100
    assert [row["before"] for row in batches] == [80, 90]
    assert [row["large_clicks"] for row in batches] == [2, 1]
    assert context.fast_clicks == ["增加10", "增加10", "增加10"]


def test_ocr_failure_uses_explicit_runtime_reader() -> None:
    context = SimpleNamespace(
        ocr_numbers_in_shapes=lambda *_args: ([], "??"),
    )

    value = read_positive_integer_count(
        context,
        ASSETS,
        count_label="测试次数",
        runtime_reader=lambda: {"current": 42},
    )

    assert value == 42


def test_stable_read_tolerates_transitional_value_until_two_samples_agree() -> None:
    context = SliderContext()
    context.ocr_numbers_in_shapes = lambda *_args: ([], "")
    values = iter((40, 401, 401))

    value = _finish(_stable_read(
        context,
        ASSETS,
        count_label="神物兑换数量",
        runtime_reader=lambda: {"current": next(values)},
    ))

    assert value == 401


def test_stable_read_fails_with_trace_when_value_keeps_changing() -> None:
    context = SliderContext()
    context.ocr_numbers_in_shapes = lambda *_args: ([], "")
    values = iter((40, 401, 402, 403, 404, 405))

    with pytest.raises(RuntimeError, match=r"观测轨迹=\[40, 401, 402, 403, 404, 405\]"):
        _finish(_stable_read(
            context,
            ASSETS,
            count_label="神物兑换数量",
            runtime_reader=lambda: {"current": next(values)},
        ))


def test_pixel_trace_uses_actual_thumb_motion_and_gesture_gain() -> None:
    context = SliderContext(gain=0.75)

    result = _finish(set_verified_integer_slider_count(
        context, ASSETS, 100, maximum=1000, max_adjustments=10,
    ))

    first_drag = result["proportional_drag"]["drag_attempts"][0]
    commanded = first_drag["commanded_end_x"] - first_drag["start_x"]
    actual = first_drag["actual_end_x"] - first_drag["start_x"]
    assert actual / commanded == pytest.approx(0.75)
    assert (
        result["proportional_drag"]["actual_pixels"]
        < result["proportional_drag"]["commanded_pixels"]
    )
    assert result["after"] == 100


def test_track_only_common_shop_slider_moves_from_one_to_exact_100() -> None:
    assets = IntegerSliderAssets(
        settings_scene_id=634,
        count_region="数量滑条",
        count_decrease="-",
        count_increase="+",
        count_slider_thumb=None,
        count_slider_track="数量滑条",
    )
    context = SliderContext(maximum=1998)
    context.ocr_numbers_in_shapes = lambda *_args: ([], "")
    context.shape_box = lambda *_args: {"x": 0.0, "y": 10.0, "w": 100.0, "h": 20.0}

    result = _finish(set_verified_integer_slider_count(
        context,
        assets,
        100,
        maximum=1998,
        max_adjustments=10,
        count_label="神物兑换数量",
        runtime_count_reader=lambda: {
            "current": context.count,
            "maximum": context.maximum,
        },
    ))

    assert result["before"] == 1
    assert result["after"] == 100
    assert result["maximum"] == 1998
    assert result["phase"] == "track_only_closed_loop"


def test_track_only_slider_models_drag_response_before_fine_tuning() -> None:
    assets = IntegerSliderAssets(
        settings_scene_id=634,
        count_region="数量滑条",
        count_decrease="-",
        count_increase="+",
        count_slider_thumb=None,
        count_slider_track="数量滑条",
    )
    context = SliderContext(maximum=1998, gain=0.5)
    context.ocr_numbers_in_shapes = lambda *_args: ([], "")
    context.shape_box = lambda *_args: {
        "x": 0.0,
        "y": 10.0,
        "w": 100.0,
        "h": 20.0,
    }

    result = _finish(set_verified_integer_slider_count(
        context,
        assets,
        100,
        maximum=1998,
        max_adjustments=10,
        count_label="神物兑换数量",
        runtime_count_reader=lambda: {
            "current": context.count,
            "maximum": context.maximum,
        },
    ))

    assert result["after"] == 100
    assert result["pixel_probes"]
    assert result["interpolation_drags"]
    assert result["interpolation_drags"][0]["after"] == 100
    assert result["fine_batches"] == []
