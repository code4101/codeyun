from __future__ import annotations

import threading

import pytest

from backend.core.fanxiu.data_annotation.trial_purchase import (
    normalize_xianqiao_trial_purchase_target,
    purchases_completed_before_price,
)
from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner


def _finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def _runtime():
    runner = create_behavior_tree_runtime_runner()
    images = {
        357: {
            "id": 357,
            "title": "仙窍试炼",
            "width": 900,
            "height": 1600,
            "shapes": [{"title": "购买", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.05}],
        },
        363: {
            "id": 363,
            "title": "购买次数",
            "width": 900,
            "height": 1600,
            "shapes": [
                {"title": "价格", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.05},
                {"title": "购买并使用", "x": 0.4, "y": 0.7, "w": 0.2, "h": 0.05},
                {"title": "返回", "x": 0.05, "y": 0.9, "w": 0.08, "h": 0.04},
            ],
        },
        364: {
            "id": 364,
            "title": "购买次数已达上限",
            "width": 900,
            "height": 1600,
            "shapes": [{"title": "返回", "x": 0.05, "y": 0.9, "w": 0.08, "h": 0.04}],
        },
    }
    return runner._fanxiu_runtime({"images": images}, stop_event=threading.Event())


def _stub_purchase_io(
    monkeypatch,
    runtime,
    *,
    initial_scene,
    waited_scenes,
    prices=(),
    polled_scenes=(),
):
    clicks: list[tuple[int, str]] = []
    waits = iter(waited_scenes)
    observed_prices = iter(prices)
    polls = iter(polled_scenes)
    first_observation = True

    def current_scene(*_args, **_kwargs):
        nonlocal first_observation
        if first_observation:
            first_observation = False
            return initial_scene, 100.0, "frame"
        return next(polls), 100.0, "frame"

    monkeypatch.setattr(runtime, "current_scene", current_scene)
    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "frame")
    monkeypatch.setattr(
        runtime,
        "ocr_numbers_in_shapes",
        lambda *_args, **_kwargs: ([price := next(observed_prices)], str(price)),
    )
    monkeypatch.setattr(
        runtime,
        "click_shape_center",
        lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))),
    )
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))),
    )

    def wait_view(*_args, **_kwargs):
        if False:
            yield None
        return next(waits)

    monkeypatch.setattr(runtime, "wait_view", wait_view)
    return clicks


def test_purchase_model_maps_live_price_to_completed_count():
    assert [purchases_completed_before_price(price) for price in (100, 150, 200)] == [0, 1, 2]
    assert normalize_xianqiao_trial_purchase_target(3) == 3
    with pytest.raises(ValueError):
        normalize_xianqiao_trial_purchase_target(4)
    with pytest.raises(ValueError):
        purchases_completed_before_price(250)


def test_target_two_buys_100_and_150_then_returns_at_200(monkeypatch):
    runtime = _runtime()
    clicks = _stub_purchase_io(
        monkeypatch,
        runtime,
        initial_scene=357,
        waited_scenes=(363, 363, 363, 357),
        prices=(100, 150, 200),
    )

    result = _finish(runtime.purchase_xianqiao_trial_attempts(2, settle_seconds=0))

    assert clicks == [
        (357, "购买"),
        (363, "购买并使用"),
        (363, "购买并使用"),
        (363, "返回"),
    ]
    assert result["purchases_now"] == [100, 150]
    assert result["purchased_after"] == 2
    assert result["exit_reason"] == "target_reached"


def test_target_two_resumes_at_price_200_without_buying(monkeypatch):
    runtime = _runtime()
    clicks = _stub_purchase_io(
        monkeypatch,
        runtime,
        initial_scene=363,
        waited_scenes=(357,),
        prices=(200,),
    )

    result = _finish(runtime.purchase_xianqiao_trial_attempts(2, settle_seconds=0))

    assert clicks == [(363, "返回")]
    assert result["purchases_now"] == []
    assert result["purchased_before"] == 2


def test_exhausted_page_is_a_valid_open_result_and_returns(monkeypatch):
    runtime = _runtime()
    clicks = _stub_purchase_io(
        monkeypatch,
        runtime,
        initial_scene=357,
        waited_scenes=(364, 357),
    )

    result = _finish(runtime.purchase_xianqiao_trial_attempts(3, settle_seconds=0))

    assert clicks == [(357, "购买"), (364, "返回")]
    assert result["purchased_after"] == 3
    assert result["exit_reason"] == "daily_limit_reached"


def test_final_purchase_reopens_entry_and_accepts_exhausted_page(monkeypatch):
    runtime = _runtime()
    clicks = _stub_purchase_io(
        monkeypatch,
        runtime,
        initial_scene=363,
        waited_scenes=(357, 364, 357),
        prices=(200,),
    )

    result = _finish(runtime.purchase_xianqiao_trial_attempts(3, settle_seconds=0))

    assert clicks == [
        (363, "购买并使用"),
        (357, "购买"),
        (364, "返回"),
    ]
    assert result["purchases_now"] == [200]
    assert result["purchased_after"] == 3


def test_same_purchase_page_waits_until_stale_price_advances(monkeypatch):
    runtime = _runtime()
    clicks = _stub_purchase_io(
        monkeypatch,
        runtime,
        initial_scene=363,
        waited_scenes=(363, 357),
        prices=(100, 100, 150),
        polled_scenes=(363,),
    )

    result = _finish(runtime.purchase_xianqiao_trial_attempts(1, settle_seconds=0))

    assert clicks == [(363, "购买并使用"), (363, "返回")]
    assert result["purchases_now"] == [100]
    assert result["purchased_after"] == 1
