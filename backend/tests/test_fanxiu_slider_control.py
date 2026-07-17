from __future__ import annotations

import threading

import pytest

from backend.core.fanxiu.data_annotation.slider_control import (
    BalancedPointState,
    DiscreteSliderScale,
    read_labeled_percentage,
)
from backend.core.fanxiu.runtime.behavior_tree import create_fanxiu_runtime_runner


def _finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def test_discrete_slider_scale_maps_twenty_positions_to_thumb_centers():
    scale = DiscreteSliderScale(minimum=2, maximum=40, step=2)
    box = {"x": 226.0, "y": 700.0, "w": 492.0, "h": 32.0}

    assert scale.position_count == 20
    assert scale.center_x(box, 2) == pytest.approx(242.0)
    assert scale.center_x(box, 40) == pytest.approx(702.0)
    assert scale.center_x(box, 20) == pytest.approx(459.8947)


def test_read_labeled_percentage_uses_the_live_ocr_line():
    assert read_labeled_percentage(
        [{"text": "怪物受到伤害降低【20%】"}],
        "伤害降低",
    ) == (20, "怪物受到伤害降低【20%】")


def test_balanced_points_fill_the_less_used_item_and_prefer_first_on_ties():
    assert BalancedPointState(remaining=1, first_value=20, second_value=10).next_target() == "second"
    assert BalancedPointState(remaining=1, first_value=20, second_value=20).next_target() == "first"


def test_runtime_slider_rechecks_and_applies_a_bounded_correction(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image = {
        "id": 358,
        "title": "设置难度",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "难度条", "x": 226 / 900, "y": 700 / 1600, "w": 492 / 900, "h": 32 / 1600},
            {"title": "伤害标题", "x": 171 / 900, "y": 640 / 1600, "w": 390 / 900, "h": 40 / 1600},
        ],
    }
    ctx = {"images": {358: image}}
    runtime = runner._fanxiu_runtime(ctx, stop_event=threading.Event())
    values = iter((2, 18, 20))
    drags: list[tuple[float, float, float, float, int]] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(
        runner,
        "_shared_spatial_ocr_result",
        lambda _ctx, _frame: {
            "lines": [{"text": f"怪物受到伤害降低【{next(values)}%】", "y": 400, "h": 500}],
            "words": [
                {"text": char, "x": 180 + index * 20, "y": 645, "w": 18, "h": 40}
                for index, char in enumerate("伤害降低")
            ],
        },
    )
    monkeypatch.setattr(
        runner,
        "_drag_frame_point",
        lambda _ctx, _image, x1, y1, x2, y2, *, duration_ms: drags.append((x1, y1, x2, y2, duration_ms)),
    )

    result = _finish(
        runtime.set_slider_value(
            358,
            "伤害降低",
            20,
            track="难度条",
            anchor="伤害标题",
            minimum=2,
            maximum=40,
            step=2,
            settle_seconds=0,
        )
    )

    assert result == {
        "label": "伤害降低",
        "before": 2,
        "after": 20,
        "target": 20,
        "attempts": 2,
        "text": "怪物受到伤害降低【20%】",
    }
    assert len(drags) == 2
    assert drags[0][0] == pytest.approx(242.0)
    assert drags[0][2] == pytest.approx(459.8947)
    assert drags[0][1] == pytest.approx(721.0)
    assert drags[1][0] < drags[1][2]


def test_runtime_balanced_points_rechecks_each_increment(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image = {
        "id": 358,
        "title": "设置难度",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": title, "x": 0.1 + index * 0.1, "y": 0.7, "w": 0.08, "h": 0.04}
            for index, title in enumerate(("五行点数", "当前攻击", "当前伤害", "增加攻击", "增加伤害"))
        ],
    }
    runtime = runner._fanxiu_runtime({"images": {358: image}}, stop_event=threading.Event())
    readings = iter(([1], [20], [10], [0], [20], [20]))
    clicks: list[str] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(
        runtime,
        "ocr_numbers_in_shapes",
        lambda *_args, **_kwargs: (next(readings), "ocr"),
    )
    monkeypatch.setattr(
        runtime,
        "click_shape_center",
        lambda _view, shape: clicks.append(str(shape)),
    )

    result = _finish(
        runtime.allocate_balanced_points(
            358,
            points_shape="五行点数",
            first_value_shape="当前攻击",
            second_value_shape="当前伤害",
            first_increase_shape="增加攻击",
            second_increase_shape="增加伤害",
            first_label="攻击",
            second_label="伤害",
            settle_seconds=0,
        )
    )

    assert clicks == ["增加伤害"]
    assert result["before"] == {"remaining": 1, "first_value": 20, "second_value": 10}
    assert result["after"] == {"remaining": 0, "first_value": 20, "second_value": 20}
