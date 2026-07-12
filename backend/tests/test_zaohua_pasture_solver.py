from backend.core.zaohua.pasture_solver import optimize_pasture_shape, solve_pasture


BUILDINGS = [
    {"build_id": 3, "name": "灵池", "effect_range_type": 0, "effect": "", "effect_params": []},
    {"build_id": 4, "name": "灵泉", "effect_range_type": 11, "effect": "updateGrowSpeed", "effect_params": ["10", "100"]},
    {"build_id": 5, "name": "灵枢台", "effect_range_type": 11, "effect": "updateGrowCount", "effect_params": ["0", "1"]},
    {"build_id": 7, "name": "悟丹亭", "effect_range_type": 0, "effect": "", "effect_params": []},
]
SQUARE = [{"x": x, "y": y} for y in range(3) for x in range(3)]


def test_empty_selection_fills_every_plot() -> None:
    result = solve_pasture(SQUARE, BUILDINGS, [])
    assert result["base_output"] == 9
    assert result["equivalent_output"] == 9
    assert result["used_building_ids"] == []


def test_optional_adjacency_buildings_are_used_only_when_profitable() -> None:
    result = solve_pasture(SQUARE, BUILDINGS, [4, 5])
    assert set(result["used_building_ids"]) == {4, 5}
    assert result["equivalent_output"] > 9
    assert result["exact"] is True
    assert result["total_value"] == sum(cell.get("coefficient", 0) for cell in result["cells"])


def test_non_bonus_building_is_required_and_occupies_one_plot() -> None:
    result = solve_pasture(SQUARE, BUILDINGS, [7])
    assert result["used_building_ids"] == [7]
    assert result["base_output"] == 8
    assert result["equivalent_output"] == 8


def test_irregular_connected_shape_keeps_coordinate_adjacency() -> None:
    shape = [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 2, "y": 0}, {"x": 1, "y": 1}]
    result = solve_pasture(shape, BUILDINGS, [4])
    assert result["plot_count"] == 4
    assert {tuple((cell["x"], cell["y"])) for cell in result["cells"]} == {(0, 0), (1, 0), (2, 0), (1, 1)}
    assert result["equivalent_output"] == 6


def test_disconnected_shape_is_rejected() -> None:
    try:
        solve_pasture([{"x": 0, "y": 0}, {"x": 2, "y": 0}], BUILDINGS, [])
    except ValueError as exc:
        assert "连通" in str(exc)
    else:
        raise AssertionError("disconnected shape must be rejected")


def test_joint_optimizer_generates_connected_shape_and_coefficients() -> None:
    result = optimize_pasture_shape(9, BUILDINGS, [4, 5])
    coordinates = {(cell["x"], cell["y"]) for cell in result["cells"]}
    assert len(coordinates) == 9
    assert result["total_value"] == sum(cell.get("coefficient", 0) for cell in result["cells"])
    assert all("coefficient" in cell for cell in result["cells"] if cell["kind"] == "plot")
    assert result["search"]["shape_candidates"] > 1
    assert result["exact"] is False


def test_user_counterexample_is_a_lower_bound_for_joint_search() -> None:
    plots = {(1, 0), (0, 1), (2, 1), (1, 2), (3, 2), (2, 3)}
    springs = {(1, 1)}
    pivots = {(2, 2)}
    value = 0
    for x, y in plots:
        neighbors = {(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)}
        value += (1 + len(neighbors & springs)) * (1 + len(neighbors & pivots))
    assert value == 16

    result = optimize_pasture_shape(9, BUILDINGS, [4, 5, 7])
    assert result["total_value"] >= value
    assert 7 in result["used_building_ids"]


def test_speed_only_joint_search_beats_checkerboard_baseline() -> None:
    result = optimize_pasture_shape(9, BUILDINGS, [4])
    assert result["total_value"] >= 17


def test_two_bonus_types_joint_search_beats_three_by_three_baseline() -> None:
    exact_square = solve_pasture(SQUARE, BUILDINGS, [4, 5])
    assert exact_square["total_value"] == 25

    result = optimize_pasture_shape(9, BUILDINGS, [4, 5])
    assert result["total_value"] >= exact_square["total_value"]
    for cell in result["cells"]:
        if cell["kind"] == "plot":
            assert cell["coefficient"] == (1 + cell["speed_count"]) * (1 + cell["yield_count"])
    assert result["total_value"] == sum(cell.get("coefficient", 0) for cell in result["cells"])


def test_required_building_count_is_respected() -> None:
    result = optimize_pasture_shape(9, BUILDINGS, [3], {3: 2})
    assert result["used_building_ids"].count(3) == 2
    assert result["base_output"] == 7


def test_pivot_bonus_applies_to_spirit_pool_output() -> None:
    result = optimize_pasture_shape(9, BUILDINGS, [3, 5], {3: 2})
    pools = [cell for cell in result["cells"] if cell.get("building_id") == 3]
    assert len(pools) == 2
    assert all(pool["productive"] is True for pool in pools)
    assert any(pool["yield_count"] > 0 and pool["coefficient"] > 1 for pool in pools)
    assert result["total_value"] == sum(cell.get("output", 0) for cell in result["cells"])


def test_exact_production_counts_are_respected() -> None:
    result = optimize_pasture_shape(9, BUILDINGS, [4, 5], production_mode="exact", herb_count=5, pool_count=1)
    assert result["herb_count"] == 5
    assert result["pool_count"] == 1
    support_count = sum(cell["kind"] == "building" and not cell.get("productive") for cell in result["cells"])
    assert result["herb_count"] + result["pool_count"] + support_count == 9


def test_target_ratio_is_only_a_secondary_objective() -> None:
    all_herbs = optimize_pasture_shape(9, BUILDINGS, [4], production_mode="target_ratio", herb_count=1, pool_count=1)
    assert all_herbs["total_value"] >= 17
    assert all_herbs["production_mode"] == "target_ratio"


def test_free_mode_can_choose_the_production_mix() -> None:
    result = optimize_pasture_shape(9, BUILDINGS, [5], production_mode="free")
    assert result["herb_count"] + result["pool_count"] > 0
    assert result["total_value"] == result["herb_value"] + result["fish_value"]


def test_exact_building_counts_use_the_configured_number_of_boosters() -> None:
    result = optimize_pasture_shape(
        9, BUILDINGS, [4, 5], {4: 2, 5: 1},
        production_mode="exact", herb_count=6, pool_count=0, exact_building_counts=True,
    )
    assert result["used_building_ids"].count(4) == 2
    assert result["used_building_ids"].count(5) == 1
    assert result["herb_count"] == 6


def test_special_cells_are_connected_zero_value_blockers() -> None:
    result = optimize_pasture_shape(
        9, BUILDINGS, [], production_mode="exact", herb_count=8, pool_count=0, special_cell_count=1,
    )
    special_cells = [cell for cell in result["cells"] if cell.get("building_id") == -1]
    assert len(special_cells) == 1
    assert special_cells[0].get("output", 0) == 0
    assert result["herb_count"] == 8
    assert result["total_value"] == 8


def test_exact_building_counts_without_enabled_boosters_avoids_duplicate_layouts() -> None:
    result = optimize_pasture_shape(
        9, BUILDINGS, [], {0: 9, 3: 0},
        production_mode="target_ratio", herb_count=9, pool_count=0, exact_building_counts=True,
    )
    assert result["used_building_ids"] == []
    assert result["herb_count"] == 9
    assert result["pool_count"] == 0
    assert result["search"]["layout_candidates"] == result["search"]["shape_candidates"]
