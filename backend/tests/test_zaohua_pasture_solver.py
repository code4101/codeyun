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
