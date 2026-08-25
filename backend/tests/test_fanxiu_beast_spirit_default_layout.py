from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.beast_spirit_default_layout import (
    build_beast_spirit_default_layout,
    read_beast_spirit_default_layout,
    save_beast_spirit_default_layout,
)


def _snapshot() -> dict:
    items = [
        {
            "item_id": str(index),
            "level": 4,
            "shape_id": 400 + index,
            "shape": [[0, 0], [0, 1], [1, 0], [2, 0]],
            "score": 1000 - index,
        }
        for index in range(1, 9)
    ]
    items.extend(
        [
            {"item_id": "11", "level": 1, "shape_id": 101, "shape": [[0, 0]], "score": 0},
            {"item_id": "12", "level": 1, "shape_id": 102, "shape": [[0, 0]], "score": 0},
            {"item_id": "21", "level": 2, "shape_id": 201, "shape": [[0, 0], [0, 1]], "score": 50},
            {"item_id": "22", "level": 2, "shape_id": 202, "shape": [[0, 0], [1, 0]], "score": 40},
        ]
    )
    return {
        "complete": True,
        "captured_at": "2026-08-09 13:00:00",
        "items": items,
        "layout": {
            "score": 7000,
            "score_gain": 0,
            "protected_prefix_k": 8,
            "protected_prefix_m": 1,
            "high_prefix_item_ids": [str(index) for index in range(1, 9)],
            "low_level_reserves": {
                "single_item_ids": ["11", "12"],
                "horizontal_item_id": "21",
                "vertical_item_id": "22",
            },
        },
    }


def test_build_default_layout_orders_high_prefix_before_low_level_reserves() -> None:
    layout = build_beast_spirit_default_layout(_snapshot())

    assert [item["item_id"] for item in layout["items"]] == [
        "1", "2", "3", "4", "5", "6", "7", "8", "11", "12", "21", "22"
    ]
    assert [item["role"] for item in layout["items"][-4:]] == [
        "level1_single",
        "level1_single",
        "level2_horizontal",
        "level2_vertical",
    ]
    assert layout["items"][0]["code"] == "11;10;10"


def test_default_layout_round_trips_through_database() -> None:
    test_engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        saved = save_beast_spirit_default_layout(_snapshot(), session=session)
        loaded = read_beast_spirit_default_layout(session)

    assert loaded == saved
