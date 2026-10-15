from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks.activity_store import (
    scan_activity_store_region,
)


def _tokens(text: str, *, x: float, y: float, line: str) -> list[dict]:
    result = []
    cursor = x
    for order, character in enumerate(text):
        width = 18.0 if character.isdigit() else 24.0
        result.append(
            {
                "text": character,
                "x": cursor,
                "y": y,
                "w": width,
                "h": 30.0,
                "parent_line_id": line,
                "line_order": int(y),
                "order": order,
            }
        )
        cursor += width
    return result


def test_store_region_finds_arbitrary_integer_prices_and_classifies_cash():
    tokens = (
        _tokens("488", x=100, y=1100, line="first")
        + _tokens("12345", x=360, y=1100, line="second")
        + _tokens("6元", x=700, y=1100, line="cash")
    )

    scan = scan_activity_store_region(tokens)

    assert [(target.value, target.is_cash) for target in scan.targets] == [
        (488, False),
        (12345, False),
        (6, True),
    ]
    assert scan.targets[0].center == pytest.approx((127, 1115))


def test_store_region_normalizes_fullwidth_digits_before_classifying_cash():
    scan = scan_activity_store_region(
        _tokens("９８８", x=100, y=1100, line="stone")
        + _tokens("３０元", x=500, y=1100, line="cash")
    )

    assert [(target.value, target.is_cash) for target in scan.targets] == [
        (988, False),
        (30, True),
    ]


def test_store_region_rejects_partial_box_inside_multi_character_token():
    scan = scan_activity_store_region(
        [
            {
                "text": "售价488",
                "x": 100,
                "y": 1100,
                "w": 120,
                "h": 30,
                "parent_line_id": "merged",
                "line_order": 1,
                "order": 0,
            }
        ]
    )

    assert scan.targets == ()


@pytest.mark.parametrize('toast', ['气血+156兆', '攻击+98.2兆'])
def test_store_region_ignores_dynamic_attribute_bonus_in_store_region(toast):
    scan = scan_activity_store_region(
        _tokens(toast, x=50, y=1100, line="floating-stat")
        + _tokens("488", x=430, y=1140, line="price")
    )

    assert [(target.value, target.is_cash) for target in scan.targets] == [
        (488, False),
    ]
