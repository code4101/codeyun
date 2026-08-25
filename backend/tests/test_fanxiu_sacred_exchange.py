from __future__ import annotations

from backend.core.fanxiu.runtime_gui.sacred_exchange import (
    plan_sacred_exchange_item_click,
    sacred_exchange_quantity_observations,
    visible_sacred_exchange_rows,
)


def _snapshot() -> dict:
    quantities = (747562, 116906, 1690385, 1291132, 465269, 1528004)
    return {
        "complete": True,
        "items": [
            {
                "base_id": 4_000_006 if index == 0 else 4_000_000 + index,
                "num": quantity,
                "instance_id": str(index),
            }
            for index, quantity in enumerate(quantities)
        ],
    }


def test_divine_exchange_filters_floating_and_occluded_ocr_before_alignment() -> None:
    rows = visible_sacred_exchange_rows(
        (100.0, 100.0, 300.0, 80.0),
        (100.0, 200.0, 300.0, 80.0),
        (80.0, 90.0, 360.0, 300.0),
        frame_width=720,
        frame_height=1280,
    )
    observations = sacred_exchange_quantity_observations(
        rows,
        [
            {"text": "747562", "x": 105, "y": 145, "w": 55, "h": 20},
            {"text": "116906", "x": 105, "y": 245, "w": 55, "h": 20},
            # Floating damage text: not a Runtime quantity.
            {"text": "114", "x": 105, "y": 345, "w": 30, "h": 20},
            # Occluded 1291132 lost its leading digit: reject, do not repair by guess.
            {"text": "291132", "x": 105, "y": 345, "w": 55, "h": 20},
        ],
        runtime_quantities=(747562, 116906, 1690385, 1291132, 465269, 1528004),
    )

    assert [(item.visible_index, item.quantity) for item in observations] == [
        (0, 747562),
        (1, 116906),
    ]
    plan = plan_sacred_exchange_item_click(
        _snapshot(),
        target_base_id=4_000_001,
        rows=rows,
        observations=observations,
    )
    assert plan.ready
    assert plan.runtime_index == 1
    assert plan.point == rows[1].point


def test_divine_exchange_refuses_a_single_ocr_anchor() -> None:
    rows = visible_sacred_exchange_rows(
        (100.0, 100.0, 300.0, 80.0),
        (100.0, 200.0, 300.0, 80.0),
        (80.0, 90.0, 360.0, 300.0),
        frame_width=720,
        frame_height=1280,
    )
    plan = plan_sacred_exchange_item_click(
        _snapshot(),
        target_base_id=4_000_001,
        rows=rows,
        observations=(),
    )

    assert plan.status == "insufficient_observations"
