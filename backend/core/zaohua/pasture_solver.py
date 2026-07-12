from __future__ import annotations

import itertools
import random
from typing import Any, Iterable


def _normalize_shape(shape: Iterable[dict[str, int]]) -> list[tuple[int, int]]:
    cells = sorted({(int(cell["x"]), int(cell["y"])) for cell in shape}, key=lambda item: (item[1], item[0]))
    if not cells:
        raise ValueError("灵田形状至少需要一个格子")
    cell_set = set(cells)
    visited = {cells[0]}
    pending = [cells[0]]
    while pending:
        x, y = pending.pop()
        for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if neighbor in cell_set and neighbor not in visited:
                visited.add(neighbor)
                pending.append(neighbor)
    if visited != cell_set:
        raise ValueError("灵田形状必须上下左右连通")
    return cells


def _is_optional_adjacency_bonus(building: dict[str, Any]) -> bool:
    return int(building.get("effect_range_type") or 0) == 11


def _herb_bonus(building: dict[str, Any]) -> tuple[float, float]:
    effect = str(building.get("effect") or "")
    params = list(building.get("effect_params") or [])
    if effect == "updateGrowSpeed" and params and str(params[0]) == "10":
        return float(params[-1]) / 100, 0
    if effect == "updateGrowCount":
        return 0, float(params[-1])
    return 0, 0


def solve_pasture(
    shape: Iterable[dict[str, int]],
    buildings: Iterable[dict[str, Any]],
    enabled_building_ids: Iterable[int],
) -> dict[str, Any]:
    coordinates = _normalize_shape(shape)
    plot_count = len(coordinates)
    building_by_id = {int(item.get("build_id") or 0): item for item in buildings}
    enabled = [building_by_id[item_id] for item_id in enabled_building_ids if item_id in building_by_id]
    optional = [item for item in enabled if _is_optional_adjacency_bonus(item) and _herb_bonus(item) != (0, 0)]
    required = [item for item in enabled if not _is_optional_adjacency_bonus(item)]
    if len(required) > plot_count:
        raise ValueError("必放建筑数量超过可用格子数")

    coordinate_index = {coordinate: index for index, coordinate in enumerate(coordinates)}
    neighbors = []
    for x, y in coordinates:
        neighbors.append([
            coordinate_index[neighbor]
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
            if neighbor in coordinate_index
        ])
    speed_building = next((item for item in optional if _herb_bonus(item)[0] > 0), None)
    yield_building = next((item for item in optional if _herb_bonus(item)[1] > 0), None)
    states = [0]
    if speed_building:
        states.append(1)
    if yield_building:
        states.append(2)
    if len(states) == 3 and plot_count > 16:
        raise ValueError("同时启用两类加成建筑时，精确求解暂支持最多 16 格")

    best: dict[str, Any] | None = None
    for assignment in itertools.product(states, repeat=plot_count):
        optional_count = sum(state != 0 for state in assignment)
        crop_count = plot_count - optional_count - len(required)
        if crop_count < 0:
            continue
        boosters = {
            index: speed_building if state == 1 else yield_building
            for index, state in enumerate(assignment)
            if state != 0
        }
        remaining = [index for index, state in enumerate(assignment) if state == 0]
        contributions = []
        for index in remaining:
            speed_count = sum(assignment[neighbor] == 1 for neighbor in neighbors[index])
            yield_count = sum(assignment[neighbor] == 2 for neighbor in neighbors[index])
            speed_factor = 1 + speed_count
            yield_factor = 1 + yield_count
            coefficient = speed_factor * yield_factor
            contributions.append((coefficient, index, speed_factor, yield_factor, speed_count, yield_count))
        contributions.sort(key=lambda item: (-item[0], item[1]))
        crops = contributions[:crop_count]
        blocker_positions = [item[1] for item in contributions[crop_count:]]
        score = sum(item[0] for item in crops)
        signature = tuple(index for index, state in enumerate(assignment) if state) + tuple(sorted(blocker_positions))
        candidate = {
            "score": score,
            "signature": signature,
            "boosters": boosters,
            "crops": {
                item[1]: {
                    "speed": item[2],
                    "yield": item[3],
                    "speed_count": item[4],
                    "yield_count": item[5],
                    "coefficient": item[0],
                    "output": item[0],
                }
                for item in crops
            },
            "blocker_positions": blocker_positions,
        }
        if best is None or (score, tuple(-value for value in signature)) > (
            best["score"], tuple(-value for value in best["signature"])
        ):
            best = candidate

    assert best is not None
    required_by_position = dict(zip(sorted(best["blocker_positions"]), required, strict=True))
    cells = []
    for index in range(plot_count):
        if index in best["boosters"]:
            building = best["boosters"][index]
            cells.append({"index": index, "x": coordinates[index][0], "y": coordinates[index][1], "kind": "building", "building_id": building["build_id"]})
        elif index in required_by_position:
            building = required_by_position[index]
            cells.append({"index": index, "x": coordinates[index][0], "y": coordinates[index][1], "kind": "building", "building_id": building["build_id"]})
        else:
            cells.append({"index": index, "x": coordinates[index][0], "y": coordinates[index][1], "kind": "plot", **best["crops"][index]})
    used_ids = [cell["building_id"] for cell in cells if cell["kind"] == "building"]
    return {
        "plot_count": plot_count,
        "shape": [{"x": x, "y": y} for x, y in coordinates],
        "objective": "herb_output_per_time",
        "base_output": sum(cell["kind"] == "plot" for cell in cells),
        "equivalent_output": best["score"],
        "total_value": best["score"],
        "gain": best["score"] - sum(cell["kind"] == "plot" for cell in cells),
        "used_building_ids": used_ids,
        "cells": cells,
        "exact": True,
    }


def _canonical_shape(cells: set[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    variants = []
    for reflect in (1, -1):
        for rotation in range(4):
            transformed = []
            for source_x, source_y in cells:
                x, y = source_x * reflect, source_y
                for _ in range(rotation):
                    x, y = -y, x
                transformed.append((x, y))
            min_x = min(x for x, _ in transformed)
            min_y = min(y for _, y in transformed)
            variants.append(tuple(sorted((x - min_x, y - min_y) for x, y in transformed)))
    return min(variants)


def _shape_score(shape: tuple[tuple[int, int], ...]) -> tuple[int, int]:
    cells = set(shape)
    edges = sum((x + 1, y) in cells for x, y in cells) + sum((x, y + 1) in cells for x, y in cells)
    degree_square = sum(sum(neighbor in cells for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))) ** 2 for x, y in cells)
    return degree_square, edges


def _candidate_shapes(plot_count: int, beam_size: int = 240) -> list[tuple[tuple[int, int], ...]]:
    beam = [((0, 0),)]
    for _ in range(1, plot_count):
        candidates = set()
        for shape in beam:
            cells = set(shape)
            boundary = {
                neighbor
                for x, y in cells
                for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
                if neighbor not in cells
            }
            for neighbor in boundary:
                candidates.add(_canonical_shape(cells | {neighbor}))
        beam = sorted(candidates, key=lambda shape: (_shape_score(shape), shape), reverse=True)[:beam_size]
    return beam


def _unique_samples_from_fixed_states(
    fixed_states: tuple[int, ...],
    rng: random.Random,
    sample_count: int,
) -> list[tuple[int, ...]]:
    samples: list[tuple[int, ...]] = [fixed_states]
    seen = {fixed_states}
    for _ in range(sample_count):
        candidate = tuple(rng.sample(fixed_states, len(fixed_states)))
        if candidate in seen:
            continue
        seen.add(candidate)
        samples.append(candidate)
    return samples


def optimize_pasture_shape(
    plot_count: int,
    buildings: Iterable[dict[str, Any]],
    enabled_building_ids: Iterable[int],
    building_counts: dict[int, int] | None = None,
    *,
    production_mode: str = "free",
    herb_count: int = 0,
    pool_count: int = 0,
    herb_weight: float = 1,
    fish_weight: float = 1,
    exact_building_counts: bool = False,
    special_cell_count: int = 0,
) -> dict[str, Any]:
    if not 1 <= plot_count <= 60:
        raise ValueError("自动形状求解支持 1 至 60 格")
    if production_mode not in {"free", "exact", "target_ratio"}:
        raise ValueError("未知的生产格约束模式")
    building_list = list(buildings)
    building_by_id = {int(item.get("build_id") or 0): item for item in building_list}
    enabled = [building_by_id[item_id] for item_id in enabled_building_ids if item_id in building_by_id]
    speed = next((item for item in enabled if _herb_bonus(item)[0] > 0), None)
    output = next((item for item in enabled if _herb_bonus(item)[1] > 0), None)
    counts = building_counts or {}
    speed_count_required = int(counts.get(int(speed.get("build_id") or 0), 0)) if speed and exact_building_counts else None
    output_count_required = int(counts.get(int(output.get("build_id") or 0), 0)) if output and exact_building_counts else None
    required = [
        item
        for item in enabled
        if not _is_optional_adjacency_bonus(item)
        for _ in range(max(1, int(counts.get(int(item.get("build_id") or 0), 1))))
    ]
    legacy_pools = [item for item in required if int(item.get("build_id") or 0) == 3]
    pool_building = building_by_id.get(3)
    requested_pool_count = len(legacy_pools) if legacy_pools else pool_count
    pool_count_is_exact = production_mode == "exact" or bool(legacy_pools)
    pools = ([pool_building] * requested_pool_count) if pool_building else []
    blockers = [item for item in required if int(item.get("build_id") or 0) != 3]
    blockers.extend({"build_id": -1, "name": "特殊格"} for _ in range(special_cell_count))
    if production_mode == "exact" and herb_count + requested_pool_count + len(blockers) > plot_count:
        raise ValueError("灵田、灵池和必放建筑数量超过总格子数")
    if len(pools) + len(blockers) > plot_count:
        raise ValueError("必放建筑数量超过可用格子数")

    rng = random.Random(260710 + plot_count * 97 + sum(int(item.get("build_id") or 0) for item in enabled))
    best: dict[str, Any] | None = None
    searched = 0
    shapes = _candidate_shapes(plot_count)
    for shape in shapes:
        coordinates = list(shape)
        coordinate_index = {coordinate: index for index, coordinate in enumerate(coordinates)}
        neighbors = [[coordinate_index[item] for item in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)) if item in coordinate_index] for x, y in coordinates]
        states = [0] + ([1] if speed else []) + ([2] if output else [])
        assignments = [tuple(0 for _ in coordinates)]
        linear_forms = (
            lambda x, y: x + y,
            lambda x, y: x - y,
            lambda x, y: x + 2 * y,
            lambda x, y: 2 * x + y,
        )
        for modulus in range(2, 6):
            for offset in range(modulus):
                for linear_form in linear_forms:
                    assignments.append(tuple(states[((linear_form(x, y) + offset) % modulus) % len(states)] for x, y in coordinates))
        if speed and output:
            for crop_parity in (0, 1):
                assignments.append(tuple(
                    0 if (x + y) % 2 == crop_parity else (1 if x % 2 else 2)
                    for x, y in coordinates
                ))
                assignments.append(tuple(
                    0 if (x + y) % 2 == crop_parity else (2 if x % 2 else 1)
                    for x, y in coordinates
                ))
        if exact_building_counts:
            fixed_states = ([1] * (speed_count_required or 0)) + ([2] * (output_count_required or 0))
            fixed_states += [0] * (plot_count - len(fixed_states))
            if len(fixed_states) != plot_count:
                continue
            assignments = _unique_samples_from_fixed_states(tuple(fixed_states), rng, 360)
        else:
            assignments.extend(tuple(rng.choice(states) for _ in coordinates) for _ in range(180))
        for assignment in assignments:
            if exact_building_counts and (
                assignment.count(1) != (speed_count_required or 0)
                or assignment.count(2) != (output_count_required or 0)
            ):
                continue
            booster_count = sum(state != 0 for state in assignment)
            available_count = plot_count - booster_count
            if available_count < len(required):
                continue
            contributions = []
            for index, state in enumerate(assignment):
                if state:
                    continue
                speed_count = sum(assignment[neighbor] == 1 for neighbor in neighbors[index])
                yield_count = sum(assignment[neighbor] == 2 for neighbor in neighbors[index])
                speed_factor = 1 + speed_count
                yield_factor = 1 + yield_count
                coefficient = speed_factor * yield_factor
                contributions.append({
                    "index": index,
                    "speed_count": speed_count,
                    "yield_count": yield_count,
                    "crop_value": coefficient,
                    "pool_value": yield_factor,
                })
            dp: dict[tuple[int, int, int], tuple[float, dict[int, dict[str, Any]]]] = {(0, 0, 0): (0, {})}
            for item in contributions:
                next_dp: dict[tuple[int, int], tuple[int, dict[int, dict[str, Any]]]] = {}
                for (crop_used, pool_used, blocker_used), (value, placements) in dp.items():
                    options = [(crop_used + 1, pool_used, blocker_used, item["crop_value"] * herb_weight, "plot")]
                    pool_limit = requested_pool_count if pool_count_is_exact else available_count
                    if pool_used < pool_limit and pool_building:
                        options.append((crop_used, pool_used + 1, blocker_used, item["pool_value"] * fish_weight, "pool"))
                    if blocker_used < len(blockers):
                        options.append((crop_used, pool_used, blocker_used + 1, 0, "blocker"))
                    for next_crop, next_pool, next_blocker, added_value, kind in options:
                        key = (next_crop, next_pool, next_blocker)
                        candidate_value = value + added_value
                        if key not in next_dp or candidate_value > next_dp[key][0]:
                            next_dp[key] = (
                                candidate_value,
                                {**placements, item["index"]: {**item, "kind": kind}},
                            )
                dp = next_dp
            finals = []
            for (actual_herbs, actual_pools, actual_blockers), resolved in dp.items():
                if actual_blockers != len(blockers):
                    continue
                if pool_count_is_exact and actual_pools != requested_pool_count:
                    continue
                if production_mode == "exact" and actual_herbs != herb_count:
                    continue
                ratio_gap = 0.0
                if production_mode == "target_ratio" and herb_count + pool_count > 0 and actual_herbs + actual_pools > 0:
                    ratio_gap = abs(actual_herbs * pool_count - actual_pools * herb_count) / ((actual_herbs + actual_pools) * (herb_count + pool_count))
                finals.append((resolved[0], -ratio_gap, actual_herbs + actual_pools, -actual_pools, resolved[1], actual_herbs, actual_pools, ratio_gap))
            if not finals:
                continue
            score, _, _, _, placements, actual_herbs, actual_pools, ratio_gap = max(finals, key=lambda item: item[:4])
            searched += 1
            candidate_key = (score, -ratio_gap, actual_herbs + actual_pools)
            if best is None or candidate_key > best["key"]:
                best = {"key": candidate_key, "score": score, "shape": shape, "assignment": assignment, "placements": placements, "herb_count": actual_herbs, "pool_count": actual_pools, "ratio_gap": ratio_gap}
    if best is None:
        raise ValueError("当前建筑数量无法填满全部格子，请检查数量总和")
    pool_iter = itertools.repeat(pool_building)
    blocker_iter = iter(blockers)
    cells = []
    for index, (x, y) in enumerate(best["shape"]):
        state = best["assignment"][index]
        if state == 1 and speed:
            cells.append({"index": index, "x": x, "y": y, "kind": "building", "building_id": speed["build_id"]})
        elif state == 2 and output:
            cells.append({"index": index, "x": x, "y": y, "kind": "building", "building_id": output["build_id"]})
        else:
            placement = best["placements"][index]
            if placement["kind"] == "pool":
                pool = next(pool_iter)
                coefficient = placement["pool_value"]
                cells.append({"index": index, "x": x, "y": y, "kind": "building", "building_id": pool["build_id"], "productive": True, "speed_count": 0, "yield_count": placement["yield_count"], "coefficient": coefficient, "output": coefficient})
            elif placement["kind"] == "blocker":
                blocker = next(blocker_iter)
                cells.append({"index": index, "x": x, "y": y, "kind": "building", "building_id": blocker["build_id"]})
            else:
                coefficient = placement["crop_value"]
                cells.append({"index": index, "x": x, "y": y, "kind": "plot", "speed_count": placement["speed_count"], "yield_count": placement["yield_count"], "coefficient": coefficient, "output": coefficient})
    base_output = sum(cell["kind"] == "plot" for cell in cells)
    base_value = sum(bool(cell["kind"] == "plot" or cell.get("productive")) for cell in cells)
    return {
        "plot_count": plot_count,
        "shape": [{"x": x, "y": y} for x, y in best["shape"]],
        "objective": "total_value",
        "base_output": base_output,
        "equivalent_output": best["score"],
        "total_value": best["score"],
        "base_value": base_value,
        "herb_count": best["herb_count"],
        "pool_count": best["pool_count"],
        "herb_value": sum(cell.get("output", 0) * herb_weight for cell in cells if cell["kind"] == "plot"),
        "fish_value": sum(cell.get("output", 0) * fish_weight for cell in cells if cell.get("productive")),
        "production_mode": production_mode,
        "ratio_deviation": best["ratio_gap"],
        "gain": best["score"] - base_value,
        "used_building_ids": [cell["building_id"] for cell in cells if cell["kind"] == "building"],
        "cells": cells,
        "exact": False,
        "search": {"shape_candidates": len(shapes), "layout_candidates": searched, "method": "beam+deterministic-multistart"},
    }
