import pytest

from backend.core.fanxiu.runtime_gui.exchange_shop import resolve_exchange_shop_item


LIST_BOX = {"x": 50, "y": 300, "w": 800, "h": 600}
ROW_BOXES = [
    {"x": 50, "y": 300 + index * 180, "w": 800, "h": 160}
    for index in range(3)
]


def test_resolves_exact_normalized_name_inside_product_list() -> None:
    target = resolve_exchange_shop_item(
        [
            {"text": "玄灵丹·珍", "x": 210, "y": 510, "w": 150, "h": 30},
            {"text": "玄灵丹珍", "x": 210, "y": 100, "w": 150, "h": 30},
        ],
        product_list_box=LIST_BOX,
        product_row_boxes=ROW_BOXES,
        expected_name="玄灵丹·珍",
    )

    assert (target.x, target.y, target.row_index) == (285.0, 525.0, 2)


def test_duplicate_name_uses_left_current_price_not_crossed_out_original() -> None:
    target = resolve_exchange_shop_item(
        [
            {"text": "诛首秘法残页", "x": 210, "y": 330, "w": 180, "h": 30},
            {"text": "2500", "x": 220, "y": 385, "w": 60, "h": 25},
            {"text": "5000", "x": 520, "y": 385, "w": 60, "h": 25},
            {"text": "诛首秘法残页", "x": 210, "y": 510, "w": 180, "h": 30},
            {"text": "5000", "x": 220, "y": 565, "w": 60, "h": 25},
            {"text": "10000", "x": 520, "y": 565, "w": 75, "h": 25},
        ],
        product_list_box=LIST_BOX,
        product_row_boxes=ROW_BOXES,
        expected_name="诛首秘法残页",
        expected_unit_price=5000,
    )

    assert (target.x, target.y, target.row_index) == (300.0, 525.0, 2)
    assert target.current_unit_price == 5000


def test_crossed_out_original_price_alone_cannot_match_current_price() -> None:
    with pytest.raises(RuntimeError, match="唯一命中数为 0"):
        resolve_exchange_shop_item(
            [
                {"text": "诛首秘法残页", "x": 210, "y": 330, "w": 180, "h": 30},
                {"text": "5000", "x": 520, "y": 385, "w": 60, "h": 25},
            ],
            product_list_box=LIST_BOX,
            product_row_boxes=ROW_BOXES,
            expected_name="诛首秘法残页",
            expected_unit_price=5000,
        )


def test_duplicate_exact_rows_fail_closed_when_price_is_still_ambiguous() -> None:
    with pytest.raises(RuntimeError, match="唯一命中数为 2"):
        resolve_exchange_shop_item(
            [
                {"text": "玄灵丹·珍", "x": 210, "y": 330, "w": 150, "h": 30},
                {"text": "60", "x": 220, "y": 385, "w": 40, "h": 25},
                {"text": "玄灵丹·珍", "x": 210, "y": 510, "w": 150, "h": 30},
                {"text": "60", "x": 220, "y": 565, "w": 40, "h": 25},
            ],
            product_list_box=LIST_BOX,
            product_row_boxes=ROW_BOXES,
            expected_name="玄灵丹·珍",
            expected_unit_price=60,
        )
