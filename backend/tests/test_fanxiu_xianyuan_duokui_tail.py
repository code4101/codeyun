from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks.xianyuan_duokui_tail import (
    _click_exact_compact_shop_name,
    _shop_ready,
    _store_panel_wallet,
    read_xianyuan_shop_wallet_from_ocr,
)


def _shop_list_view():
    list_shape = SimpleNamespace(
        box=lambda: {"x": 50.0, "y": 328.0, "w": 792.0, "h": 976.0}
    )
    row_shapes = {
        f"商品行{slot}": SimpleNamespace(
            box=lambda slot=slot: {
                "x": 54.0,
                "y": 328.0 + (slot - 1) * 180.0,
                "w": 783.0,
                "h": 160.0,
            }
        )
        for slot in range(1, 6)
    }
    return SimpleNamespace(
        get_shape=lambda name: list_shape if name == "商品列表" else row_shapes.get(name)
    )


def test_exact_shop_header_reads_activity_local_wallet() -> None:
    class Runtime:
        def full_frame_ocr_tokens(self, **_kwargs):
            return [
                {"parent_line_id": "a", "text": "当前拥有夺魁灵玉", "x": 10, "y": 100, "w": 120, "h": 20},
                {"parent_line_id": "a", "text": "12080", "x": 140, "y": 100, "w": 50, "h": 20},
                {"parent_line_id": "b", "text": "活动期间累计夺魁灵玉", "x": 10, "y": 130, "w": 160, "h": 20},
                {"parent_line_id": "b", "text": "136080", "x": 180, "y": 130, "w": 60, "h": 20},
            ]

    assert read_xianyuan_shop_wallet_from_ocr(Runtime()) == (12_080, 136_080)


def test_split_shop_header_reads_activity_local_wallet() -> None:
    class Runtime:
        def full_frame_ocr_tokens(self, **_kwargs):
            return [
                {"parent_line_id": "a", "text": "当前拥有夺魁灵玉", "x": 10, "y": 100, "w": 120, "h": 20},
                {"parent_line_id": "a2", "text": "12080", "x": 140, "y": 100, "w": 50, "h": 20},
                {"parent_line_id": "b", "text": "活动期间累计夺魁灵玉", "x": 10, "y": 130, "w": 160, "h": 20},
                {"parent_line_id": "b2", "text": "136080", "x": 180, "y": 130, "w": 60, "h": 20},
            ]

    assert read_xianyuan_shop_wallet_from_ocr(Runtime()) == (12_080, 136_080)


def test_shop_ready_requires_two_consistent_valid_frames() -> None:
    class Runtime:
        samples = iter(((212_080, 136_080), (12_080, 136_080), (12_080, 136_080)))

        def full_frame_ocr_tokens(self, **_kwargs):
            current, cumulative = next(self.samples)
            return [
                {"parent_line_id": "a", "text": "当前拥有夺魁灵玉", "x": 10, "y": 100, "w": 120, "h": 20},
                {"parent_line_id": "a2", "text": str(current), "x": 140, "y": 100, "w": 60, "h": 20},
                {"parent_line_id": "b", "text": "活动期间累计夺魁灵玉", "x": 10, "y": 130, "w": 160, "h": 20},
                {"parent_line_id": "b2", "text": str(cumulative), "x": 180, "y": 130, "w": 60, "h": 20},
            ]

        def wait_action_settle(self, _seconds):
            yield None

    operation = _shop_ready(Runtime(), attempts=3)
    while True:
        try:
            next(operation)
        except StopIteration as done:
            assert done.value == (12_080, 136_080)
            break


def test_shop_name_click_distinguishes_xuanling_from_xuanxue() -> None:
    class Runtime:
        clicked = None

        def full_frame_ocr_tokens(self, **_kwargs):
            return [
                {"parent_line_id": "a", "text": "玄血丹", "x": 200, "y": 900, "w": 100, "h": 30},
                {"parent_line_id": "a", "text": "·珍", "x": 300, "y": 900, "w": 50, "h": 30},
                {"parent_line_id": "b", "text": "玄灵丹", "x": 210, "y": 1100, "w": 100, "h": 30},
                {"parent_line_id": "b", "text": "·珍", "x": 310, "y": 1100, "w": 50, "h": 30},
            ]

        def view(self, scene):
            assert scene == 559
            return _shop_list_view()

        def click_frame_point(self, scene, x, y):
            self.clicked = (scene, x, y)

    runtime = Runtime()
    _click_exact_compact_shop_name(runtime, "玄灵丹·珍")
    assert runtime.clicked == (559, 285.0, 1115.0)


def test_shop_name_click_ignores_same_exact_text_outside_product_list() -> None:
    class Runtime:
        clicked = None

        def full_frame_ocr_tokens(self, **_kwargs):
            return [
                {"parent_line_id": "header", "text": "玄灵丹·珍", "x": 210, "y": 180, "w": 150, "h": 30},
                {"parent_line_id": "row", "text": "玄灵丹·珍", "x": 210, "y": 1100, "w": 150, "h": 30},
            ]

        def view(self, scene):
            assert scene == 559
            return _shop_list_view()

        def click_frame_point(self, scene, x, y):
            self.clicked = (scene, x, y)

    runtime = Runtime()
    _click_exact_compact_shop_name(runtime, "玄灵丹·珍")
    assert runtime.clicked == (559, 285.0, 1115.0)


def test_shop_name_click_fails_closed_on_two_exact_product_rows() -> None:
    class Runtime:
        def full_frame_ocr_tokens(self, **_kwargs):
            return [
                {"parent_line_id": "row1", "text": "玄灵丹·珍", "x": 210, "y": 900, "w": 150, "h": 30},
                {"parent_line_id": "price1", "text": "60", "x": 220, "y": 950, "w": 40, "h": 25},
                {"parent_line_id": "row2", "text": "玄灵丹·珍", "x": 210, "y": 1100, "w": 150, "h": 30},
                {"parent_line_id": "price2", "text": "60", "x": 220, "y": 1150, "w": 40, "h": 25},
            ]

        def view(self, scene):
            assert scene == 559
            return _shop_list_view()

    with pytest.raises(RuntimeError, match="唯一命中数为 2"):
        _click_exact_compact_shop_name(
            Runtime(), "玄灵丹·珍", expected_unit_price=60
        )


def test_shop_name_click_uses_left_current_price_to_disambiguate_same_name() -> None:
    class Runtime:
        clicked = None

        def full_frame_ocr_tokens(self, **_kwargs):
            return [
                {"parent_line_id": "name1", "text": "诛首秘法残页", "x": 210, "y": 361, "w": 180, "h": 30},
                {"parent_line_id": "price1", "text": "2500", "x": 220, "y": 415, "w": 60, "h": 25},
                {"parent_line_id": "old1", "text": "5000", "x": 430, "y": 415, "w": 60, "h": 25},
                {"parent_line_id": "name2", "text": "诛首秘法残页", "x": 210, "y": 543, "w": 180, "h": 30},
                {"parent_line_id": "price2", "text": "5000", "x": 220, "y": 597, "w": 60, "h": 25},
                {"parent_line_id": "old2", "text": "10000", "x": 430, "y": 597, "w": 75, "h": 25},
            ]

        def view(self, scene):
            assert scene == 559
            return _shop_list_view()

        def click_frame_point(self, scene, x, y):
            self.clicked = (scene, x, y)

    runtime = Runtime()
    _click_exact_compact_shop_name(
        runtime, "诛首秘法残页", expected_unit_price=5000
    )
    assert runtime.clicked == (559, 300.0, 558.0)


def test_panel_wallet_invalidates_the_preceding_derived_budget() -> None:
    activity = SimpleNamespace(
        current_currency=0,
        cumulative_currency=0,
        captured_at="",
        evidence={
            "refresh_status": {"shop": "updated", "currency": "retained"},
            "exchange_plan": {"schema": 9, "budget_ready": False},
        },
    )

    class Session:
        def add(self, value):
            assert value is activity

    _store_panel_wallet(
        Session(), activity=activity, current=12_080, cumulative=136_080
    )

    assert activity.current_currency == 12_080
    assert activity.cumulative_currency == 136_080
    assert activity.evidence["refresh_status"]["currency"] == "updated"
    assert activity.evidence["exchange_plan"]["schema"] == 0
