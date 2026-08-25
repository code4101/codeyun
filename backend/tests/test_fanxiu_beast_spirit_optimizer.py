from backend.core.fanxiu.beast_spirit_optimizer import (
    beast_soul_low_level_reserves,
    board_placements,
    first_fit_placement,
    layout_transition_plan,
    normalize_shape,
    optimize_beast_soul_layout,
    optimize_beast_soul_structured_layout,
    plan_first_fit_layout,
    shape_orientations,
)


def test_board_placements_groups_cells_for_main_soul():
    boards = [
        {
            "soul_id": 1,
            "cells": [
                {"row": 2, "column": 2, "item_id": "a"},
                {"row": 1, "column": 2, "item_id": "a"},
                {"row": 1, "column": 1, "item_id": ""},
            ],
        }
    ]

    assert board_placements(boards) == {"a": [[1, 2], [2, 2]]}


def test_layout_transition_takes_off_moved_and_blocking_extra_before_embed():
    current = {
        "keep": [[1, 1]],
        "move": [[1, 2]],
        "blocking": [[2, 2]],
        "harmless": [[3, 3]],
    }
    selected = [
        {"item_id": "keep", "row": 1, "column": 1, "cells": [[1, 1]]},
        {"item_id": "move", "row": 2, "column": 1, "cells": [[2, 1]]},
        {"item_id": "new", "row": 2, "column": 2, "cells": [[2, 2]]},
    ]

    plan = layout_transition_plan(current, selected)

    assert plan["preserved_item_ids"] == ["keep"]
    assert plan["retained_extra_item_ids"] == ["harmless"]
    assert [item["item_id"] for item in plan["takeoff"]] == [
        "blocking",
        "move",
    ]
    assert [item["item_id"] for item in plan["embed"]] == ["move", "new"]
    assert plan["action_count"] == 4


def test_first_fit_placement_scans_rows_then_columns():
    assert first_fit_placement(
        [[0, 0], [0, 1]],
        [[1, 1]],
        rows=2,
        columns=3,
    ) == [[1, 2], [1, 3]]


def test_native_first_fit_plan_keeps_already_optimal_board_and_extra():
    plan = plan_first_fit_layout(
        {"best": [[1, 1]], "filler": [[2, 2]]},
        [{"item_id": "best", "score": 10, "shape": [[0, 0]]}],
        rows=2,
        columns=2,
    )
    assert plan["action_count"] == 0
    assert plan["retained_extra_item_ids"] == ["filler"]


def test_native_first_fit_plan_removes_blocker_and_orders_embeds():
    plan = plan_first_fit_layout(
        {"blocker": [[1, 1], [1, 2]]},
        [
            {"item_id": "wide", "score": 10, "shape": [[0, 0], [0, 1]]},
            {"item_id": "single", "score": 9, "shape": [[0, 0]]},
        ],
        rows=1,
        columns=3,
    )
    assert [item["item_id"] for item in plan["takeoff"]] == ["blocker"]
    assert [item["item_id"] for item in plan["embed"]] == ["wide", "single"]
    assert plan["embed"][0]["cells"] == [[1, 1], [1, 2]]
    assert plan["embed"][1]["cells"] == [[1, 3]]


def test_shape_orientations_keep_game_shape_fixed_by_default():
    shape = ((0, 0), (1, 0), (1, 1))
    assert shape_orientations(shape, allow_transform=False) == (shape,)
    assert len(shape_orientations(shape, allow_transform=True)) == 4


def test_normalize_shape_removes_offset():
    assert normalize_shape(((3, 4), (4, 4), (4, 5))) == (
        (0, 0),
        (1, 0),
        (1, 1),
    )


def test_optimizer_respects_shape_and_uses_lower_rank_when_it_adds_value():
    items = [
        {"item_id": 1, "score": 100, "shape": [[0, 0], [0, 1]]},
        {"item_id": 2, "score": 90, "shape": [[0, 0], [0, 1]]},
        {"item_id": 3, "score": 5, "shape": [[0, 0]]},
    ]
    result = optimize_beast_soul_layout(items, rows=1, columns=3)
    assert result["optimal"] is True
    assert result["score"] == 105
    assert [item["item_id"] for item in result["selected"]] == ["1", "3"]
    assert result["protected_prefix_k"] == 3


def test_optimizer_can_rotate_only_when_explicitly_enabled():
    items = [{"item_id": 1, "score": 10, "shape": [[0, 0], [0, 1]]}]
    fixed = optimize_beast_soul_layout(items, rows=2, columns=1)
    transformed = optimize_beast_soul_layout(
        items,
        rows=2,
        columns=1,
        allow_transform=True,
    )
    assert fixed["score"] == 0
    assert transformed["score"] == 10


def test_optimizer_preserves_as_many_current_placements_as_possible():
    items = [
        {"item_id": "1", "score": 10, "shape": [[0, 0]]},
        {"item_id": "2", "score": 9, "shape": [[0, 0]]},
        {"item_id": "3", "score": 8, "shape": [[0, 0]]},
    ]

    result = optimize_beast_soul_layout(
        items,
        rows=1,
        columns=2,
        preferred_placements={"1": [[1, 1]], "2": [[1, 2]]},
    )

    assert result["score"] == 19
    assert result["preserved_item_ids"] == ["1", "2"]
    assert {
        item["item_id"]: item["cells"] for item in result["selected"]
    } == {"1": [[1, 1]], "2": [[1, 2]]}


def test_structured_optimizer_protects_geometric_high_score_prefix_and_reserves():
    horizontal_four = [[0, 0], [0, 1], [0, 2], [0, 3]]
    vertical_four = [[0, 0], [1, 0], [2, 0], [3, 0]]
    items = [
        {
            "item_id": str(index),
            "level": 5,
            "score": 1000 - index,
            "shape": horizontal_four if index <= 7 else vertical_four,
        }
        for index in range(1, 10)
    ] + [
        {"item_id": "101", "level": 1, "score": 20, "shape": [[0, 0]]},
        {"item_id": "102", "level": 1, "score": 10, "shape": [[0, 0]]},
        {"item_id": "103", "level": 1, "score": 1, "shape": [[0, 0]]},
        {
            "item_id": "201",
            "level": 2,
            "score": 40,
            "shape": [[0, 0], [0, 1]],
        },
        {
            "item_id": "202",
            "level": 2,
            "score": 30,
            "shape": [[0, 0], [1, 0]],
        },
        {
            "item_id": "203",
            "level": 2,
            "score": 2,
            "shape": [[0, 0], [0, 1]],
        },
    ]

    result = optimize_beast_soul_structured_layout(items)

    assert len(result["selected_high_item_ids"]) == 7
    assert result["protected_prefix_k"] == 9
    assert result["protected_prefix_m"] == 2
    assert result["high_prefix_item_ids"] == [str(index) for index in range(1, 10)]
    assert result["low_level_reserves"] == {
        "single_item_ids": ["101", "102"],
        "horizontal_item_id": "201",
        "vertical_item_id": "202",
        "item_ids": ["101", "102", "201", "202"],
    }
    assert set(result["protected_item_ids"]) == {
        *(str(index) for index in range(1, 10)),
        "101",
        "102",
        "201",
        "202",
    }


def test_low_level_reserves_rank_zero_score_level_one_by_main_attribute_rolls():
    items = [
        {
            "item_id": "101",
            "level": 1,
            "score": 0,
            "shape": [[0, 0]],
            "main_entries": [
                {"kind": "attribute", "attribute_id": 10500001, "config_id": 100001},
                {"kind": "attribute", "attribute_id": 1002, "config_id": 200001},
            ],
        },
        {
            "item_id": "102",
            "level": 1,
            "score": 0,
            "shape": [[0, 0]],
            "main_entries": [
                {"kind": "attribute", "attribute_id": 10500001, "config_id": 100021},
                {"kind": "attribute", "attribute_id": 1002, "config_id": 200021},
            ],
        },
        {
            "item_id": "103",
            "level": 1,
            "score": 0,
            "shape": [[0, 0]],
            "main_entries": [
                {"kind": "attribute", "attribute_id": 10500001, "config_id": 100020},
                {"kind": "attribute", "attribute_id": 1002, "config_id": 200020},
            ],
        },
    ]

    reserves = beast_soul_low_level_reserves(items)

    assert reserves["single_item_ids"] == ["102", "103"]
