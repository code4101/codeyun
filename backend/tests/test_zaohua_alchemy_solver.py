from __future__ import annotations

import pytest

from backend.core.zaohua.alchemy_solver import _result_order_key, shape_rotations, solve_alchemy
from backend.models import ZaohuaAlchemyRecipe, ZaohuaHerb


def _herb(
    item_id: int,
    name: str,
    element: str,
    value: int,
    price: float,
    cells: list[list[int]],
) -> ZaohuaHerb:
    return ZaohuaHerb(
        item_id=item_id,
        name=name,
        grade_id=301,
        grade_name="一阶下品",
        element_id={"water": 2, "wood": 3}[element],
        element_key=element,
        price=price,
        crafting_attributes=[{"element": element, "label": element, "value": value}],
        source_json={"shape": {"draw_id": item_id, "cells": cells}},
    )


def test_shape_rotations_remove_symmetric_duplicates() -> None:
    assert len(shape_rotations([(0, 0), (1, 0)])) == 2
    assert len(shape_rotations([(0, 0)])) == 1


def test_solver_uses_yin_as_negative_vector_and_ranks_by_grade_profile() -> None:
    recipe = ZaohuaAlchemyRecipe(
        recipe_id=1,
        output_count=2,
        output_price=100,
        attr_limits=[{"element": "water", "label": "水", "value": 2}],
    )
    herbs = [
        _herb(1, "阳水草", "water", 2, 100, [[0, 0]]),
        _herb(2, "阴水草", "water", -1, 20, [[0, 0]]),
    ]

    result = solve_alchemy(recipe, herbs, yang_width=2, yang_height=1, limit=5)

    assert result["exhaustive"] is True
    assert result["solutions"][0]["grade_sequence"] == [1]
    assert result["solutions"][0]["herbs"] == [
        {"item_id": 1, "name": "阳水草", "side": "yang", "count": 1, "unit_price": 100.0}
    ]

    first_page = solve_alchemy(recipe, herbs, yang_width=2, yang_height=1, limit=1)
    assert first_page["has_more"] is True
    assert first_page["solution_count"] == 2
    assert len(first_page["solutions"]) == 1


def test_solver_applies_bottom_element_yield_rule() -> None:
    recipe = ZaohuaAlchemyRecipe(
        recipe_id=1,
        output_count=4,
        output_price=30,
        attr_limits=[{"element": "wood", "label": "木", "value": 4}],
        state_rules=[{
            "name": "丹炉底部每有1个木系灵草，成丹数+1",
            "calculate_type": "5#1",
            "target1": "3",
            "base_effect": "UpdateCraftingDrugAttr,1,1",
        }],
    )
    herbs = [_herb(1, "回春草", "wood", 2, 20, [[0, 0]])]

    result = solve_alchemy(recipe, herbs, yang_width=2, yang_height=2, limit=1)
    solution = result["solutions"][0]

    assert solution["rule_supported"] is True
    assert solution["rule_bonus"] == 2
    assert solution["final_yield"] == 6
    assert solution["total_value"] == 180


def test_solver_seeds_high_tier_search_from_official_example() -> None:
    recipe = ZaohuaAlchemyRecipe(
        recipe_id=12,
        output_count=2,
        output_price=3_000,
        attr_limits=[
            {"element": "water", "label": "水", "value": 100},
            {"element": "wood", "label": "木", "value": 150},
        ],
        example_items=[
            {"item_id": 1, "count": 1},
            {"item_id": 2, "count": 1},
        ],
        state_rules=[{
            "name": "丹炉底部木属性<100，成丹数+1",
            "target1": "3",
            "base_effect": "UpdateCraftingDrugAttr,1,1",
        }],
    )
    herbs = [
        _herb(1, "甘泉凝脂藤", "water", -100, 1_000, [[0, 0], [1, 0], [1, 1], [2, 1]]),
        _herb(2, "外朽内荣木", "wood", 150, 1_500, [[0, 0], [1, 0], [0, 1], [1, 1], [0, 2], [1, 2]]),
    ]

    result = solve_alchemy(
        recipe,
        herbs,
        yang_width=10,
        yang_height=10,
        yin_width=9,
        yin_height=10,
        search_node_limit=1,
    )
    solution = result["solutions"][0]

    assert result["seed_solution_found"] is True
    assert result["exhaustive"] is False
    assert {(item["item_id"], item["side"]) for item in solution["herbs"]} == {
        (1, "yin"),
        (2, "yang"),
    }
    assert solution["rule_supported"] is True
    assert solution["rule_bonus"] == 1
    assert solution["final_yield"] == 3


def test_solver_excludes_disabled_herbs_and_sorts_available_pool_by_price() -> None:
    recipe = ZaohuaAlchemyRecipe(
        recipe_id=1,
        output_count=2,
        output_price=100,
        attr_limits=[{"element": "water", "label": "水", "value": 2}],
    )
    herbs = [
        _herb(1, "昂贵水草", "water", 2, 100, [[0, 0]]),
        _herb(2, "便宜水草", "water", -1, 20, [[0, 0]]),
    ]

    default = solve_alchemy(recipe, herbs, 2, 1)
    excluded = solve_alchemy(recipe, herbs, 2, 1, excluded_item_ids=[2])

    assert [item["item_id"] for item in default["available_herbs"]] == [2, 1]
    assert excluded["solutions"][0]["herbs"][0]["item_id"] == 1
    assert [item["item_id"] for item in excluded["available_herbs"]] == [1]
    assert excluded["excluded_item_ids"] == [2]


def test_solver_uses_independent_yang_and_yin_board_sizes() -> None:
    recipe = ZaohuaAlchemyRecipe(
        recipe_id=1,
        output_count=1,
        output_price=10,
        attr_limits=[{"element": "water", "label": "水", "value": 2}],
    )
    negative_herb = _herb(1, "阴水藤", "water", -2, 10, [[0, 0], [1, 0]])

    blocked = solve_alchemy(
        recipe,
        [negative_herb],
        yang_width=2,
        yang_height=1,
        yin_width=1,
        yin_height=1,
    )
    allowed = solve_alchemy(
        recipe,
        [negative_herb],
        yang_width=1,
        yang_height=1,
        yin_width=2,
        yin_height=1,
    )

    assert blocked["solutions"] == []
    assert allowed["solutions"][0]["placements"][0]["side"] == "yin"


def test_solver_uses_bounded_c_family_conversion_and_compensation() -> None:
    recipe = ZaohuaAlchemyRecipe(
        recipe_id=1,
        output_count=1,
        output_price=100,
        attr_limits=[{"element": "wind", "label": "风", "value": 1}],
    )
    converter = ZaohuaHerb(
        item_id=1,
        name="刃风草",
        element_key="wind",
        price=10,
        crafting_attributes=[
            {"element": "wind", "label": "风", "value": -1},
            {"element": "wood", "label": "木", "value": 1},
        ],
        source_json={"shape": {"draw_id": 1, "cells": [[0, 0]]}},
    )
    compensator = _herb(2, "回春草", "wood", 1, 5, [[0, 0]])

    result = solve_alchemy(
        recipe,
        [converter, compensator],
        yang_width=1,
        yang_height=1,
        yin_width=1,
        yin_height=1,
    )

    assert result["search_mode"] == "grade_descent"
    assert result["vector_mode"] == "abc_bounded"
    assert result["converter_candidate_count"] == 1
    assert result["candidate_family_counts"] == {"B": 1, "C": 1}
    assert {(item["item_id"], item["side"]) for item in result["solutions"][0]["herbs"]} == {
        (1, "yin"),
        (2, "yang"),
    }


def test_solver_prunes_exact_abc_capacity_by_furnace_side() -> None:
    recipe = ZaohuaAlchemyRecipe(
        recipe_id=1,
        output_count=1,
        output_price=10,
        attr_limits=[
            {"element": "water", "label": "水", "value": 2},
            {"element": "wood", "label": "木", "value": 2},
        ],
    )
    herbs = [
        _herb(1, "水草", "water", 1, 1, [[0, 0]]),
        _herb(2, "木草", "wood", 1, 1, [[0, 0]]),
    ]

    result = solve_alchemy(
        recipe,
        herbs,
        yang_width=3,
        yang_height=1,
        yin_width=3,
        yin_height=1,
    )

    assert result["solutions"] == []
    assert result["pruned_exact_capacity"] > 0


def test_solver_keeps_only_best_solution_for_each_herb_type_set() -> None:
    recipe = ZaohuaAlchemyRecipe(
        recipe_id=1,
        output_count=1,
        output_price=100,
        attr_limits=[{"element": "water", "label": "水", "value": 5}],
    )
    herbs = [
        _herb(1, "细长水草", "water", 1, 1, [[0, 0], [1, 0]]),
        _herb(2, "浓缩水草", "water", 2, 10, [[0, 0]]),
    ]

    result = solve_alchemy(recipe, herbs, yang_width=7, yang_height=1, limit=5)

    assert result["solution_count"] == 1
    assert result["solutions"][0]["grade_sequence"] == [1, 1, 1]
    assert result["solutions"][0]["cost"] == 21
    assert {(item["item_id"], item["count"]) for item in result["solutions"][0]["herbs"]} == {
        (1, 1),
        (2, 2),
    }


def test_solver_removes_lower_ratio_superset_derivations() -> None:
    recipe = ZaohuaAlchemyRecipe(
        recipe_id=1,
        output_count=1,
        output_price=100,
        attr_limits=[{"element": "water", "label": "水", "value": 4}],
    )
    herbs = [
        _herb(1, "基础水草", "water", 1, 1, [[0, 0]]),
        _herb(2, "双倍水草", "water", 2, 3, [[0, 0]]),
    ]

    result = solve_alchemy(recipe, herbs, yang_width=4, yang_height=1, limit=5)
    herb_sets = [{item["item_id"] for item in solution["herbs"]} for solution in result["solutions"]]

    assert herb_sets == [{2}, {1}]
    assert result["solution_count"] == 2


def test_solver_grade_objective_ignores_legacy_value_metric_priority() -> None:
    recipe = ZaohuaAlchemyRecipe(
        recipe_id=1,
        output_count=1,
        output_price=1_000,
        attr_limits=[{"element": "wood", "label": "木", "value": 2}],
        state_rules=[{
            "name": "丹炉底部每有1个木系灵草，成丹数+1",
            "calculate_type": "5#1",
            "target1": "3",
            "base_effect": "UpdateCraftingDrugAttr,1,1",
        }],
    )
    herbs = [
        _herb(1, "高产出比药材", "wood", 2, 10, [[0, 0]]),
        _herb(2, "高净利润药材", "wood", 1, 100, [[0, 0]]),
    ]

    by_ratio = solve_alchemy(recipe, herbs, 2, 1, duration=5)
    by_profit = solve_alchemy(
        recipe,
        herbs,
        2,
        1,
        duration=5,
        sort_metrics=("net_profit", "output_input_ratio", "profit_rate"),
    )

    assert by_ratio["solutions"][0]["herbs"][0]["item_id"] == 1
    assert by_ratio["solutions"][0]["output_input_ratio"] == 200
    assert by_ratio["solutions"][0]["net_profit"] == 1_990
    assert by_ratio["solutions"][0]["profit_rate"] == 398
    assert by_profit["solutions"][0]["herbs"][0]["item_id"] == 1
    assert by_profit["objective"] == "grade_descent"


def test_grade_profile_prefers_balanced_simultaneous_downgrade() -> None:
    common = {
        "occupied_cells": 2,
        "final_yield": 1,
        "herbs": [],
    }
    balanced = {**common, "grade_histogram": [0] * 13 + [2, 0]}
    uneven = {**common, "grade_histogram": [0] * 12 + [1, 0, 1]}

    assert _result_order_key(balanced) < _result_order_key(uneven)


def test_solver_rejects_incomplete_value_metric_priority() -> None:
    recipe = ZaohuaAlchemyRecipe(
        recipe_id=1,
        output_count=1,
        output_price=10,
        attr_limits=[{"element": "water", "label": "水", "value": 1}],
    )

    with pytest.raises(ValueError, match="价值排序"):
        solve_alchemy(
            recipe,
            [_herb(1, "水草", "water", 1, 1, [[0, 0]])],
            1,
            1,
            sort_metrics=("net_profit",),
        )
