from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from itertools import product
import re
from typing import Any, Iterable, Literal

from backend.core.zaohua.grades import get_grade_visual
from backend.models import ZaohuaAlchemyRecipe, ZaohuaHerb


ELEMENT_IDS = {1: "gold", 2: "water", 3: "wood", 4: "fire", 5: "soil", 6: "ice", 7: "wind", 8: "thunder"}
ELEMENT_KEYS = tuple(ELEMENT_IDS.values())
ELEMENT_CYCLE_EDGES = {
    frozenset(("gold", "water")),
    frozenset(("water", "wood")),
    frozenset(("wood", "fire")),
    frozenset(("fire", "soil")),
    frozenset(("soil", "gold")),
}
EXOTIC_PARENT = {"ice": "water", "wind": "wood", "thunder": "fire"}
ValueMetric = Literal["output_input_ratio", "net_profit", "profit_rate"]
DEFAULT_VALUE_SORT_METRICS: tuple[ValueMetric, ...] = (
    "output_input_ratio",
    "net_profit",
    "profit_rate",
)


def _normalize_value_sort_metrics(metrics: Iterable[ValueMetric]) -> tuple[ValueMetric, ...]:
    normalized = tuple(metrics)
    if len(normalized) != len(DEFAULT_VALUE_SORT_METRICS) or set(normalized) != set(DEFAULT_VALUE_SORT_METRICS):
        raise ValueError("价值排序必须且只能包含产出比、净利润和时利润")
    return normalized


def _result_order_key(result: dict[str, Any]) -> tuple[Any, ...]:
    return (
        *tuple(int(value) for value in result["grade_histogram"]),
        int(result["occupied_cells"]),
        -int(result["final_yield"]),
        tuple((int(item["item_id"]), str(item["side"])) for item in result["herbs"]),
    )


@dataclass(frozen=True)
class Candidate:
    herb: ZaohuaHerb
    side: str
    vector: tuple[int, ...]
    cells: tuple[tuple[int, int], ...]
    rotations: tuple[tuple[tuple[int, int], ...], ...]
    family: str
    grade_rank: int


def _herb_family(attributes: dict[str, int]) -> str:
    nonzero = {key: value for key, value in attributes.items() if value}
    if len(nonzero) == 1:
        return "B"
    positive = {key for key, value in nonzero.items() if value > 0}
    negative = {key for key, value in nonzero.items() if value < 0}
    if (
        len(nonzero) == 2
        and not negative
        and frozenset(positive) in ELEMENT_CYCLE_EDGES
    ):
        return "A"
    if len(nonzero) == 2 and any(
        exotic in negative and parent in positive
        for exotic, parent in EXOTIC_PARENT.items()
    ):
        return "C"
    return "other"


def _herb_grade_rank(herb: ZaohuaHerb) -> int:
    rank = int(get_grade_visual(herb.grade_id, herb.grade_name)["order"])
    return rank if 1 <= rank <= 15 else 15


def _normalize(cells: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    cells = tuple(cells)
    min_x = min(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    return tuple(sorted((x - min_x, y - min_y) for x, y in cells))


@lru_cache(maxsize=256)
def _shape_rotations_cached(
    normalized_cells: tuple[tuple[int, int], ...],
) -> tuple[tuple[tuple[int, int], ...], ...]:
    current = normalized_cells
    variants: list[tuple[tuple[int, int], ...]] = []
    for _ in range(4):
        if current not in variants:
            variants.append(current)
        height = max(y for _, y in current) + 1
        current = _normalize((height - 1 - y, x) for x, y in current)
    return tuple(variants)


def shape_rotations(cells: Iterable[tuple[int, int]]) -> tuple[tuple[tuple[int, int], ...], ...]:
    return _shape_rotations_cached(_normalize(cells))


def _attribute_map(attributes: Iterable[dict[str, Any]]) -> dict[str, int]:
    return {
        str(item.get("element") or "").strip().lower(): int(item.get("value") or 0)
        for item in attributes
        if str(item.get("element") or "").strip()
    }


def _candidate_pool(
    recipe: ZaohuaAlchemyRecipe,
    herbs: Iterable[ZaohuaHerb],
    yang_width: int,
    yang_height: int,
    yin_width: int,
    yin_height: int,
) -> tuple[tuple[str, ...], tuple[int, ...], list[Candidate], int]:
    target_map = _attribute_map(recipe.attr_limits or [])
    # C-family herbs are only useful for requested exotic elements. Keep the
    # compact original vector space for ordinary recipes, and expand it only
    # when a converter may create a base-element deficit to compensate.
    has_exotic_target = any(target_map.get(element, 0) > 0 for element in EXOTIC_PARENT)
    elements = ELEMENT_KEYS if has_exotic_target else tuple(target_map)
    target = tuple(target_map.get(element, 0) for element in elements)
    monotone_candidates: list[Candidate] = []
    converter_candidates: list[Candidate] = []
    for herb in herbs:
        shape = dict((herb.source_json or {}).get("shape") or {})
        raw_cells = shape.get("cells") or []
        if not raw_cells:
            continue
        cells = _normalize((int(cell[0]), int(cell[1])) for cell in raw_cells)
        attrs = _attribute_map(herb.crafting_attributes or [])
        family = _herb_family(attrs)
        if any(value and key not in elements for key, value in attrs.items()):
            continue
        for side, sign in (("yang", 1), ("yin", -1)):
            width, height = (yang_width, yang_height) if side == "yang" else (yin_width, yin_height)
            if len(cells) > width * height:
                continue
            vector = tuple(sign * attrs.get(element, 0) for element in elements)
            if not any(vector):
                continue
            rotations = tuple(
                rotation
                for rotation in shape_rotations(cells)
                if max(x for x, _ in rotation) < width and max(y for _, y in rotation) < height
            )
            if rotations:
                candidate = Candidate(
                    herb,
                    side,
                    vector,
                    cells,
                    rotations,
                    family,
                    _herb_grade_rank(herb),
                )
                if all(value >= 0 for value in vector):
                    monotone_candidates.append(candidate)
                elif (
                    family == "C"
                    and sum(value > 0 for value in vector) == 1
                    and sum(value < 0 for value in vector) == 1
                    and any(
                        value > 0 and elements[index] in EXOTIC_PARENT
                        for index, value in enumerate(vector)
                    )
                    and any(value > 0 and target[index] > 0 for index, value in enumerate(vector))
                ):
                    converter_candidates.append(candidate)

    def candidate_order(item: Candidate) -> tuple[int, float, int, int, str]:
        positive_total = sum(max(value, 0) for value in item.vector)
        return (
            item.grade_rank,
            len(item.cells) / max(positive_total, 1),
            -positive_total,
            item.herb.item_id,
            item.side,
        )

    converter_candidates = [
        candidate
        for candidate in converter_candidates
        if all(value <= target[index] for index, value in enumerate(candidate.vector) if value > 0)
    ]
    maximum_required = list(target)
    for candidate in converter_candidates:
        positive_index = next(index for index, value in enumerate(candidate.vector) if value > 0)
        positive_value = candidate.vector[positive_index]
        maximum_count = target[positive_index] // positive_value
        side_capacity = yang_width * yang_height if candidate.side == "yang" else yin_width * yin_height
        maximum_count = min(maximum_count, side_capacity // len(candidate.cells))
        for index, value in enumerate(candidate.vector):
            if value < 0:
                maximum_required[index] += -value * maximum_count
    needed_dimensions = {index for index, value in enumerate(maximum_required) if value > 0}
    needed_dimensions.update(
        index
        for candidate in converter_candidates
        for index, value in enumerate(candidate.vector)
        if value < 0
    )
    monotone_candidates = [
        candidate
        for candidate in monotone_candidates
        if all(
            value == 0 or (index in needed_dimensions and value <= maximum_required[index])
            for index, value in enumerate(candidate.vector)
        )
    ]
    monotone_candidates.sort(key=candidate_order)
    converter_candidates.sort(key=candidate_order)
    candidates = [*monotone_candidates, *converter_candidates]
    return elements, target, candidates, len(monotone_candidates)


def _build_suffix_cell_bounds(
    candidates: list[Candidate],
    target: tuple[int, ...],
) -> tuple[tuple[tuple[int, ...], ...], ...]:
    """预计算每个候选后缀达到单项剩余向量所需的最少格数。"""

    unreachable = 10**9
    suffix_bounds: list[tuple[tuple[int, ...], ...]] = []
    for start in range(len(candidates) + 1):
        dimension_bounds: list[tuple[int, ...]] = []
        for dimension, limit in enumerate(target):
            minimum_cells = [unreachable] * (limit + 1)
            minimum_cells[0] = 0
            for candidate in candidates[start:]:
                contribution = candidate.vector[dimension]
                if contribution <= 0:
                    continue
                cell_count = len(candidate.cells)
                for value in range(contribution, limit + 1):
                    previous = minimum_cells[value - contribution]
                    if previous < unreachable:
                        minimum_cells[value] = min(minimum_cells[value], previous + cell_count)
            dimension_bounds.append(tuple(minimum_cells))
        suffix_bounds.append(tuple(dimension_bounds))
    return tuple(suffix_bounds)


def _build_exact_cell_bounds(
    candidates: list[Candidate],
    target: tuple[int, ...],
    max_states: int = 5_000,
) -> dict[tuple[int, ...], tuple[tuple[int, int], ...]] | None:
    """Exact relaxed packing bound for compact ABC vector spaces.

    The table ignores yang/yin separation and geometry, so its cell counts are
    admissible lower bounds. It is intentionally skipped for large products.
    """

    state_count = 1
    for limit in target:
        state_count *= limit + 1
        if state_count > max_states:
            return None
    zero = tuple(0 for _ in target)
    capacity_frontiers: dict[tuple[int, ...], tuple[tuple[int, int], ...]] = {
        zero: ((0, 0),),
    }
    for candidate in candidates:
        if not any(candidate.vector) or any(value < 0 for value in candidate.vector):
            continue
        ranges = [range(value, limit + 1) for value, limit in zip(candidate.vector, target, strict=True)]
        for state in product(*ranges):
            previous = tuple(
                value - delta
                for value, delta in zip(state, candidate.vector, strict=True)
            )
            previous_frontier = capacity_frontiers.get(previous)
            if previous_frontier is None:
                continue
            yang_delta = len(candidate.cells) if candidate.side == "yang" else 0
            yin_delta = len(candidate.cells) if candidate.side == "yin" else 0
            frontier = list(capacity_frontiers.get(state, ()))
            for yang_cells, yin_cells in previous_frontier:
                pair = (yang_cells + yang_delta, yin_cells + yin_delta)
                if any(yang <= pair[0] and yin <= pair[1] for yang, yin in frontier):
                    continue
                frontier = [
                    (yang, yin)
                    for yang, yin in frontier
                    if not (pair[0] <= yang and pair[1] <= yin)
                ]
                frontier.append(pair)
            if frontier:
                capacity_frontiers[state] = tuple(frontier)
    return capacity_frontiers


def _example_seed_choices(
    recipe: ZaohuaAlchemyRecipe,
    candidates: list[Candidate],
    target: tuple[int, ...],
) -> tuple[int, ...]:
    """把游戏示例药材映射为单调候选索引，作为高阶搜索的初始可行解。"""

    candidates_by_item_id: dict[int, list[int]] = {}
    for index, candidate in enumerate(candidates):
        candidates_by_item_id.setdefault(candidate.herb.item_id, []).append(index)
    choices: list[int] = []
    total = [0] * len(target)
    for item in recipe.example_items or []:
        item_id = int(item.get("item_id") or 0)
        count = int(item.get("count") or 0)
        indexes = candidates_by_item_id.get(item_id, [])
        if count <= 0 or len(indexes) != 1:
            return ()
        index = indexes[0]
        choices.extend([index] * count)
        for dimension, contribution in enumerate(candidates[index].vector):
            total[dimension] += contribution * count
    return tuple(sorted(choices)) if tuple(total) == target else ()


@lru_cache(maxsize=256)
def _poses(
    rotations: tuple[tuple[tuple[int, int], ...], ...],
    width: int,
    height: int,
    prefer: str = "",
) -> tuple[dict[str, Any], ...]:
    poses: list[dict[str, Any]] = []
    for rotation_index, cells in enumerate(rotations):
        shape_width = max(x for x, _ in cells) + 1
        shape_height = max(y for _, y in cells) + 1
        for y in range(height - shape_height + 1):
            for x in range(width - shape_width + 1):
                placed = tuple((x + dx, y + dy) for dx, dy in cells)
                mask = sum(1 << (py * width + px) for px, py in placed)
                poses.append({"mask": mask, "x": x, "y": y, "rotation": rotation_index * 90, "cells": placed})
    if prefer:
        def priority(pose: dict[str, Any]) -> tuple[int, int, int]:
            cells = pose["cells"]
            touches = {
                "底部": any(y == height - 1 for _, y in cells),
                "顶部": any(y == 0 for _, y in cells),
                "左侧": any(x == 0 for x, _ in cells),
                "右侧": any(x == width - 1 for x, _ in cells),
            }
            return (0 if touches.get(prefer, False) else 1, pose["y"], pose["x"])
        poses.sort(key=priority)
    return tuple(poses)


def _pack(
    choices: tuple[int, ...],
    candidates: list[Candidate],
    yang_width: int,
    yang_height: int,
    yin_width: int,
    yin_height: int,
    rule_name: str,
    node_limit: int = 20_000,
    pose_cache: dict[tuple[int, int, int, str], tuple[dict[str, Any], ...]] | None = None,
) -> tuple[list[dict[str, Any]] | None, int, bool]:
    prefer = (
        next((label for label in ("底部", "顶部", "左侧", "右侧") if label in rule_name), "")
        if "每有" in rule_name
        else ""
    )
    instance_indexes = sorted(
        choices,
        key=lambda index: (-len(candidates[index].cells), candidates[index].herb.item_id, candidates[index].side),
    )
    instances = [candidates[index] for index in instance_indexes]
    pose_sets: list[tuple[dict[str, Any], ...]] = []
    for index, candidate in zip(instance_indexes, instances, strict=True):
        width, height = (
            (yang_width, yang_height)
            if candidate.side == "yang"
            else (yin_width, yin_height)
        )
        cache_key = (index, width, height, prefer)
        poses = pose_cache.get(cache_key) if pose_cache is not None else None
        if poses is None:
            poses = _poses(candidate.rotations, width, height, prefer)
            if pose_cache is not None:
                pose_cache[cache_key] = poses
        pose_sets.append(poses)
    occupied = {"yang": 0, "yin": 0}
    placements: list[dict[str, Any]] = []
    failed_states: set[tuple[int, int, int, int]] = set()
    selected_pose_indexes: list[int] = []
    nodes = 0
    limit_hit = False

    def visit(position: int) -> bool:
        nonlocal limit_hit, nodes
        if position == len(instances):
            return True
        same_as_previous = position > 0 and instance_indexes[position] == instance_indexes[position - 1]
        minimum_pose_index = selected_pose_indexes[-1] if same_as_previous else 0
        state = (position, occupied["yang"], occupied["yin"], minimum_pose_index)
        if state in failed_states:
            return False
        nodes += 1
        if nodes > node_limit:
            limit_hit = True
            return False
        candidate = instances[position]
        for pose_index, pose in enumerate(pose_sets[position]):
            # Identical herbs are indistinguishable. Requiring nondecreasing pose
            # indexes removes factorial permutations of the same placement.
            if same_as_previous and pose_index < minimum_pose_index:
                continue
            if occupied[candidate.side] & pose["mask"]:
                continue
            occupied[candidate.side] |= pose["mask"]
            selected_pose_indexes.append(pose_index)
            shape = dict((candidate.herb.source_json or {}).get("shape") or {})
            placements.append({
                "item_id": candidate.herb.item_id,
                "name": candidate.herb.name,
                "side": candidate.side,
                "x": pose["x"],
                "y": pose["y"],
                "rotation": pose["rotation"],
                "cells": [list(cell) for cell in pose["cells"]],
                "shape_draw_id": int(shape.get("draw_id") or 0),
                "shape_width": int(shape.get("width") or 1),
                "shape_height": int(shape.get("height") or 1),
            })
            if visit(position + 1):
                return True
            placements.pop()
            selected_pose_indexes.pop()
            occupied[candidate.side] ^= pose["mask"]
            if limit_hit:
                return False
        failed_states.add(state)
        return False

    return (placements if visit(0) else None), nodes, not limit_hit


def _rule_bonus(
    recipe: ZaohuaAlchemyRecipe,
    placements: list[dict[str, Any]],
    herbs: dict[int, ZaohuaHerb],
    yang_width: int,
    yang_height: int,
    yin_width: int,
    yin_height: int,
) -> tuple[int, bool]:
    bonus = 0
    supported = True
    for rule in recipe.state_rules or []:
        name = str(rule.get("name") or "")
        effect = str(rule.get("base_effect") or "")
        if not effect.startswith("UpdateCraftingDrugAttr,1,"):
            supported = False
            continue
        try:
            effect_value = int(effect.rsplit(",", 1)[1])
        except ValueError:
            supported = False
            continue
        area = next((label for label in ("底部", "顶部", "左侧", "右侧") if label in name), "")
        target_key = ELEMENT_IDS.get(int(rule.get("target1") or 0), "")
        if not area or not target_key:
            supported = False
            continue
        area_placements: list[tuple[dict[str, Any], ZaohuaHerb]] = []
        for placement in placements:
            width, height = (
                (yang_width, yang_height)
                if placement["side"] == "yang"
                else (yin_width, yin_height)
            )
            herb = herbs[placement["item_id"]]
            cells = placement["cells"]
            touches = {
                "底部": any(y == height - 1 for _, y in cells),
                "顶部": any(y == 0 for _, y in cells),
                "左侧": any(x == 0 for x, _ in cells),
                "右侧": any(x == width - 1 for x, _ in cells),
            }
            if touches[area]:
                area_placements.append((placement, herb))
        if "每有" in name:
            try:
                divisor = max(1, int(str(rule.get("calculate_type") or "5#1").split("#")[-1]))
            except ValueError:
                supported = False
                continue
            count = sum(herb.element_key == target_key for _, herb in area_placements)
            bonus += (count // divisor) * effect_value
            continue
        comparison = re.search(r"(<=|>=|<|>|=)\s*(-?\d+)", name)
        if comparison is None:
            supported = False
            continue
        operator, raw_threshold = comparison.groups()
        threshold = int(raw_threshold)
        area_value = sum(
            (1 if placement["side"] == "yang" else -1)
            * _attribute_map(herb.crafting_attributes or []).get(target_key, 0)
            for placement, herb in area_placements
        )
        matched = {
            "<": area_value < threshold,
            "<=": area_value <= threshold,
            ">": area_value > threshold,
            ">=": area_value >= threshold,
            "=": area_value == threshold,
        }[operator]
        bonus += int(matched) * effect_value
    return bonus, supported


def solve_alchemy(
    recipe: ZaohuaAlchemyRecipe,
    herbs: Iterable[ZaohuaHerb],
    yang_width: int,
    yang_height: int,
    yin_width: int | None = None,
    yin_height: int | None = None,
    limit: int = 5,
    search_node_limit: int = 120_000,
    packing_node_limit: int = 20_000,
    solution_limit: int = 400,
    excluded_item_ids: Iterable[int] = (),
    duration: float = 1.0,
    sort_metrics: Iterable[ValueMetric] = DEFAULT_VALUE_SORT_METRICS,
) -> dict[str, Any]:
    yin_width = yang_width if yin_width is None else yin_width
    yin_height = yang_height if yin_height is None else yin_height
    excluded_ids = {int(item_id) for item_id in excluded_item_ids}
    normalized_sort_metrics = _normalize_value_sort_metrics(sort_metrics)
    normalized_duration = float(duration)
    herb_list = [herb for herb in herbs if herb.item_id not in excluded_ids]
    elements, target, candidates, monotone_candidate_count = _candidate_pool(
        recipe,
        herb_list,
        yang_width,
        yang_height,
        yin_width,
        yin_height,
    )
    results: dict[tuple[int, ...], dict[str, Any]] = {}
    packing_cache: dict[tuple[int, ...], tuple[list[dict[str, Any]] | None, bool]] = {}
    pose_cache: dict[tuple[int, int, int, str], tuple[dict[str, Any], ...]] = {}
    search_nodes = 0
    packing_nodes = 0
    exhausted = True
    pruned_unreachable = 0
    pruned_cell_capacity = 0
    pruned_exact_capacity = 0
    converter_search_nodes = 0
    yang_capacity = yang_width * yang_height
    yin_capacity = yin_width * yin_height
    total_capacity = yang_capacity + yin_capacity
    rule_name = " ".join(str(rule.get("name") or "") for rule in recipe.state_rules or [])
    herb_by_id = {herb.item_id: herb for herb in herb_list}
    monotone_candidates = candidates[:monotone_candidate_count]
    converter_start = monotone_candidate_count
    suffix_bounds_cache: dict[tuple[int, ...], tuple[tuple[tuple[int, ...], ...], ...]] = {}
    exact_bounds_cache: dict[
        tuple[int, ...],
        dict[tuple[int, ...], tuple[tuple[int, int], ...]] | None,
    ] = {}

    def evaluate_choices(choices: tuple[int, ...]) -> bool:
        nonlocal exhausted, packing_nodes
        normalized_choices = tuple(sorted(choices))
        composition = Counter((candidates[index].herb.item_id, candidates[index].side) for index in choices)
        combination_key = tuple(sorted({item_id for item_id, _ in composition}))
        if packing_nodes >= packing_node_limit or (combination_key not in results and len(results) >= solution_limit):
            exhausted = False
            return False
        cached_pack = packing_cache.get(normalized_choices)
        if cached_pack is None:
            placements, nodes, pack_exhaustive = _pack(
                normalized_choices,
                candidates,
                yang_width,
                yang_height,
                yin_width,
                yin_height,
                rule_name,
                node_limit=4_000,
                pose_cache=pose_cache,
            )
            packing_nodes += nodes
            packing_cache[normalized_choices] = (placements, pack_exhaustive)
        else:
            placements, pack_exhaustive = cached_pack
        if not pack_exhaustive:
            exhausted = False
        if placements is None:
            return False
        key = tuple(sorted((item_id, side, count) for (item_id, side), count in composition.items()))
        cost = sum(float(herb_by_id[item_id].price) * count for item_id, _, count in key)
        grade_counts = Counter(
            _herb_grade_rank(herb_by_id[item_id])
            for item_id, _, count in key
            for _ in range(count)
        )
        grade_histogram = tuple(grade_counts.get(rank, 0) for rank in range(15, 0, -1))
        grade_sequence = [
            rank
            for rank in range(15, 0, -1)
            for _ in range(grade_counts.get(rank, 0))
        ]
        occupied_cells = sum(len(candidates[index].cells) for index in choices)
        rule_bonus, rule_supported = _rule_bonus(
            recipe,
            placements,
            herb_by_id,
            yang_width,
            yang_height,
            yin_width,
            yin_height,
        )
        final_yield = max(0, int(recipe.output_count) + rule_bonus)
        total_value = final_yield * float(recipe.output_price)
        output_input_ratio = total_value / cost if cost > 0 else None
        net_profit = total_value - cost
        profit_rate = net_profit / normalized_duration if normalized_duration > 0 else None
        result = {
            "ratio": output_input_ratio,
            "output_input_ratio": output_input_ratio,
            "net_profit": net_profit,
            "profit_rate": profit_rate,
            "cost": cost,
            "base_yield": int(recipe.output_count),
            "rule_bonus": rule_bonus,
            "final_yield": final_yield,
            "total_value": total_value,
            "rule_supported": rule_supported,
            "grade_histogram": list(grade_histogram),
            "grade_sequence": grade_sequence,
            "grade_groups": [
                {"grade_rank": rank, "count": grade_counts[rank]}
                for rank in range(15, 0, -1)
                if grade_counts[rank]
            ],
            "max_herb_grade": max(grade_sequence, default=0),
            "occupied_cells": occupied_cells,
            "herbs": [
                {
                    "item_id": item_id,
                    "name": herb_by_id[item_id].name,
                    "side": side,
                    "count": count,
                    "unit_price": float(herb_by_id[item_id].price),
                }
                for item_id, side, count in key
            ],
            "placements": placements,
        }
        previous = results.get(combination_key)
        result_order = _result_order_key(result)
        previous_order = _result_order_key(previous) if previous is not None else None
        if previous_order is None or result_order < previous_order:
            results[combination_key] = result
        return True

    def visit(
        start: int,
        remaining: tuple[int, ...],
        choices: tuple[int, ...],
        used_yang_cells: int,
        used_yin_cells: int,
        suffix_cell_bounds: tuple[tuple[tuple[int, ...], ...], ...],
        exact_cell_bounds: dict[tuple[int, ...], tuple[tuple[int, int], ...]] | None,
    ) -> None:
        nonlocal exhausted, pruned_cell_capacity, pruned_exact_capacity, pruned_unreachable, search_nodes
        search_nodes += 1
        if search_nodes > search_node_limit:
            exhausted = False
            return
        if not any(remaining):
            evaluate_choices(choices)
            return
        if exact_cell_bounds is not None:
            exact_frontier = exact_cell_bounds.get(remaining)
            if exact_frontier is None:
                pruned_unreachable += 1
                return
            remaining_yang_capacity = yang_capacity - used_yang_cells
            remaining_yin_capacity = yin_capacity - used_yin_cells
            if not any(
                yang <= remaining_yang_capacity and yin <= remaining_yin_capacity
                for yang, yin in exact_frontier
            ):
                pruned_exact_capacity += 1
                return
        dimension_bounds = suffix_cell_bounds[start]
        minimum_cells = []
        for dimension, value in enumerate(remaining):
            required = dimension_bounds[dimension][value]
            if required >= 10**9:
                pruned_unreachable += 1
                return
            minimum_cells.append(required)
        if used_yang_cells + used_yin_cells + max(minimum_cells, default=0) > total_capacity:
            pruned_cell_capacity += 1
            return
        for index in range(start, monotone_candidate_count):
            candidate = candidates[index]
            next_remaining = tuple(value - delta for value, delta in zip(remaining, candidate.vector, strict=True))
            if any(value < 0 for value in next_remaining):
                continue
            cell_count = len(candidate.cells)
            next_yang_cells = used_yang_cells + (cell_count if candidate.side == "yang" else 0)
            next_yin_cells = used_yin_cells + (cell_count if candidate.side == "yin" else 0)
            if next_yang_cells > yang_capacity or next_yin_cells > yin_capacity:
                pruned_cell_capacity += 1
                continue
            visit(
                index,
                next_remaining,
                (*choices, index),
                next_yang_cells,
                next_yin_cells,
                suffix_cell_bounds,
                exact_cell_bounds,
            )
            if not exhausted:
                return

    def visit_converters(
        start: int,
        remaining: tuple[int, ...],
        positive_used: tuple[int, ...],
        choices: tuple[int, ...],
        used_yang_cells: int,
        used_yin_cells: int,
    ) -> None:
        """Enumerate bounded C-family conversions, then finish with monotone A/B herbs."""

        nonlocal converter_search_nodes, exhausted, pruned_cell_capacity, search_nodes
        converter_search_nodes += 1
        search_nodes += 1
        if search_nodes > search_node_limit:
            exhausted = False
            return
        suffix_cell_bounds = suffix_bounds_cache.get(remaining)
        if suffix_cell_bounds is None:
            suffix_cell_bounds = _build_suffix_cell_bounds(monotone_candidates, remaining)
            suffix_bounds_cache[remaining] = suffix_cell_bounds
        if remaining not in exact_bounds_cache:
            exact_bounds_cache[remaining] = _build_exact_cell_bounds(monotone_candidates, remaining)
        exact_cell_bounds = exact_bounds_cache[remaining]
        visit(
            0,
            remaining,
            choices,
            used_yang_cells,
            used_yin_cells,
            suffix_cell_bounds,
            exact_cell_bounds,
        )
        if not exhausted:
            return
        for index in range(start, len(candidates)):
            candidate = candidates[index]
            next_positive_used = tuple(
                used + max(delta, 0)
                for used, delta in zip(positive_used, candidate.vector, strict=True)
            )
            # A converter may only supply an originally requested positive
            # component. This makes cancellation finite and prevents cycles.
            if any(used > limit for used, limit in zip(next_positive_used, target, strict=True)):
                continue
            next_remaining = tuple(
                value - delta
                for value, delta in zip(remaining, candidate.vector, strict=True)
            )
            if any(value < 0 for value in next_remaining):
                continue
            cell_count = len(candidate.cells)
            next_yang_cells = used_yang_cells + (cell_count if candidate.side == "yang" else 0)
            next_yin_cells = used_yin_cells + (cell_count if candidate.side == "yin" else 0)
            if next_yang_cells > yang_capacity or next_yin_cells > yin_capacity:
                pruned_cell_capacity += 1
                continue
            visit_converters(
                index,
                next_remaining,
                next_positive_used,
                (*choices, index),
                next_yang_cells,
                next_yin_cells,
            )
            if not exhausted:
                return

    seed_choices = _example_seed_choices(recipe, candidates, target)
    seed_solution_found = bool(seed_choices and evaluate_choices(seed_choices))
    if exhausted:
        visit_converters(converter_start, target, tuple(0 for _ in target), (), 0, 0)
    undominated_results = [
        result
        for combination_key, result in results.items()
        if not any(
            set(other_key) < set(combination_key)
            and _result_order_key(other_result) <= _result_order_key(result)
            for other_key, other_result in results.items()
        )
    ]
    all_ordered = sorted(
        undominated_results,
        key=_result_order_key,
    )
    ordered = all_ordered[:limit]
    available_item_ids = {
        int(herb["item_id"])
        for solution in all_ordered
        for herb in solution["herbs"]
    }
    available_herbs = sorted(
        [
            {
                "item_id": item_id,
                "name": herb_by_id[item_id].name,
                "price": float(herb_by_id[item_id].price),
                "icon_path": herb_by_id[item_id].icon_path,
            }
            for item_id in available_item_ids
        ],
        key=lambda item: (item["price"], item["item_id"]),
    )
    for rank, item in enumerate(ordered, 1):
        item["rank"] = rank
    return {
        "solutions": ordered,
        "target_vector": dict(zip(elements, target, strict=True)),
        "candidate_count": len(candidates),
        "candidate_family_counts": dict(Counter(candidate.family for candidate in candidates)),
        "converter_candidate_count": len(candidates) - monotone_candidate_count,
        "converter_search_nodes": converter_search_nodes,
        "search_nodes": search_nodes,
        "packing_nodes": packing_nodes,
        "pruned_unreachable": pruned_unreachable,
        "pruned_cell_capacity": pruned_cell_capacity,
        "pruned_exact_capacity": pruned_exact_capacity,
        "seed_solution_found": seed_solution_found,
        "exhaustive": exhausted,
        "search_mode": "grade_descent",
        "vector_mode": "abc_bounded",
        "objective": "grade_descent",
        "duration": normalized_duration,
        "has_more": len(all_ordered) > limit,
        "solution_count": len(all_ordered),
        "available_herbs": available_herbs,
        "excluded_item_ids": sorted(excluded_ids),
    }
