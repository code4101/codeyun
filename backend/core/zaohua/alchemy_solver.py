from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from backend.models import ZaohuaAlchemyRecipe, ZaohuaHerb


ELEMENT_IDS = {1: "gold", 2: "water", 3: "wood", 4: "fire", 5: "soil", 6: "ice", 7: "wind", 8: "thunder"}


@dataclass(frozen=True)
class Candidate:
    herb: ZaohuaHerb
    side: str
    vector: tuple[int, ...]
    cells: tuple[tuple[int, int], ...]
    rotations: tuple[tuple[tuple[int, int], ...], ...]


def _normalize(cells: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    cells = tuple(cells)
    min_x = min(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    return tuple(sorted((x - min_x, y - min_y) for x, y in cells))


def shape_rotations(cells: Iterable[tuple[int, int]]) -> tuple[tuple[tuple[int, int], ...], ...]:
    current = _normalize(cells)
    variants: list[tuple[tuple[int, int], ...]] = []
    for _ in range(4):
        if current not in variants:
            variants.append(current)
        height = max(y for _, y in current) + 1
        current = _normalize((height - 1 - y, x) for x, y in current)
    return tuple(variants)


def _attribute_map(attributes: Iterable[dict[str, Any]]) -> dict[str, int]:
    return {
        str(item.get("element") or "").strip().lower(): int(item.get("value") or 0)
        for item in attributes
        if str(item.get("element") or "").strip()
    }


def _candidate_pool(
    recipe: ZaohuaAlchemyRecipe,
    herbs: Iterable[ZaohuaHerb],
    width: int,
    height: int,
) -> tuple[tuple[str, ...], tuple[int, ...], list[Candidate]]:
    target_map = _attribute_map(recipe.attr_limits or [])
    elements = tuple(target_map)
    target = tuple(target_map[element] for element in elements)
    candidates: list[Candidate] = []
    for herb in herbs:
        shape = dict((herb.source_json or {}).get("shape") or {})
        raw_cells = shape.get("cells") or []
        if not raw_cells:
            continue
        cells = _normalize((int(cell[0]), int(cell[1])) for cell in raw_cells)
        if len(cells) > width * height:
            continue
        attrs = _attribute_map(herb.crafting_attributes or [])
        # First version deliberately keeps only monotone contributions. Mixed-sign
        # cancellation remains valid game logic, but would make the search unbounded.
        for side, sign in (("yang", 1), ("yin", -1)):
            vector = tuple(sign * attrs.get(element, 0) for element in elements)
            outside = any(sign * value != 0 for key, value in attrs.items() if key not in target_map)
            if outside or not any(vector) or any(value < 0 for value in vector):
                continue
            if any(value > limit for value, limit in zip(vector, target, strict=True)):
                continue
            rotations = tuple(
                rotation
                for rotation in shape_rotations(cells)
                if max(x for x, _ in rotation) < width and max(y for _, y in rotation) < height
            )
            if rotations:
                candidates.append(Candidate(herb, side, vector, cells, rotations))
    candidates.sort(key=lambda item: (max(float(item.herb.price), 0.0) / max(sum(item.vector), 1), item.herb.item_id, item.side))
    return elements, target, candidates


def _poses(candidate: Candidate, width: int, height: int, prefer: str = "") -> list[dict[str, Any]]:
    poses: list[dict[str, Any]] = []
    for rotation_index, cells in enumerate(candidate.rotations):
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
    return poses


def _pack(
    choices: tuple[int, ...],
    candidates: list[Candidate],
    width: int,
    height: int,
    rule_name: str,
    node_limit: int = 20_000,
) -> tuple[list[dict[str, Any]] | None, int]:
    prefer = next((label for label in ("底部", "顶部", "左侧", "右侧") if label in rule_name), "")
    instances = [candidates[index] for index in choices]
    instances.sort(key=lambda item: (-len(item.cells), item.herb.item_id, item.side))
    pose_sets = [_poses(item, width, height, prefer) for item in instances]
    occupied = {"yang": 0, "yin": 0}
    placements: list[dict[str, Any]] = []
    nodes = 0

    def visit(position: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > node_limit:
            return False
        if position == len(instances):
            return True
        candidate = instances[position]
        for pose in pose_sets[position]:
            if occupied[candidate.side] & pose["mask"]:
                continue
            occupied[candidate.side] |= pose["mask"]
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
            occupied[candidate.side] ^= pose["mask"]
        return False

    return (placements if visit(0) else None), nodes


def _rule_bonus(recipe: ZaohuaAlchemyRecipe, placements: list[dict[str, Any]], herbs: dict[int, ZaohuaHerb], width: int, height: int) -> tuple[int, bool]:
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
            divisor = max(1, int(str(rule.get("calculate_type") or "5#1").split("#")[-1]))
        except ValueError:
            supported = False
            continue
        area = next((label for label in ("底部", "顶部", "左侧", "右侧") if label in name), "")
        target_key = ELEMENT_IDS.get(int(rule.get("target1") or 0), "")
        if not area or not target_key or "每有" not in name:
            supported = False
            continue
        count = 0
        for placement in placements:
            herb = herbs[placement["item_id"]]
            if herb.element_key != target_key:
                continue
            cells = placement["cells"]
            touches = {
                "底部": any(y == height - 1 for _, y in cells),
                "顶部": any(y == 0 for _, y in cells),
                "左侧": any(x == 0 for x, _ in cells),
                "右侧": any(x == width - 1 for x, _ in cells),
            }
            count += int(touches[area])
        bonus += (count // divisor) * effect_value
    return bonus, supported


def solve_alchemy(
    recipe: ZaohuaAlchemyRecipe,
    herbs: Iterable[ZaohuaHerb],
    width: int,
    height: int,
    limit: int = 5,
    search_node_limit: int = 120_000,
    packing_node_limit: int = 80_000,
    solution_limit: int = 400,
    excluded_item_ids: Iterable[int] = (),
) -> dict[str, Any]:
    excluded_ids = {int(item_id) for item_id in excluded_item_ids}
    herb_list = [herb for herb in herbs if herb.item_id not in excluded_ids]
    elements, target, candidates = _candidate_pool(recipe, herb_list, width, height)
    results: dict[tuple[tuple[int, str, int], ...], dict[str, Any]] = {}
    search_nodes = 0
    packing_nodes = 0
    exhausted = True
    max_cells = width * height * 2
    rule_name = " ".join(str(rule.get("name") or "") for rule in recipe.state_rules or [])
    herb_by_id = {herb.item_id: herb for herb in herb_list}

    def visit(start: int, remaining: tuple[int, ...], choices: tuple[int, ...], used_cells: int) -> None:
        nonlocal search_nodes, packing_nodes, exhausted
        search_nodes += 1
        if search_nodes > search_node_limit:
            exhausted = False
            return
        if not any(remaining):
            if packing_nodes >= packing_node_limit or len(results) >= solution_limit:
                exhausted = False
                return
            placements, nodes = _pack(choices, candidates, width, height, rule_name, node_limit=4_000)
            packing_nodes += nodes
            if placements is None:
                return
            composition = Counter((candidates[index].herb.item_id, candidates[index].side) for index in choices)
            key = tuple(sorted((item_id, side, count) for (item_id, side), count in composition.items()))
            cost = sum(float(herb_by_id[item_id].price) * count for item_id, _, count in key)
            rule_bonus, rule_supported = _rule_bonus(recipe, placements, herb_by_id, width, height)
            final_yield = max(0, int(recipe.output_count) + rule_bonus)
            total_value = final_yield * float(recipe.output_price)
            result = {
                "ratio": total_value / cost if cost > 0 else None,
                "cost": cost,
                "base_yield": int(recipe.output_count),
                "rule_bonus": rule_bonus,
                "final_yield": final_yield,
                "total_value": total_value,
                "rule_supported": rule_supported,
                "herbs": [
                    {"item_id": item_id, "name": herb_by_id[item_id].name, "side": side, "count": count, "unit_price": float(herb_by_id[item_id].price)}
                    for item_id, side, count in key
                ],
                "placements": placements,
            }
            previous = results.get(key)
            if previous is None or (result["ratio"] or 0) > (previous["ratio"] or 0):
                results[key] = result
            return
        for index in range(start, len(candidates)):
            candidate = candidates[index]
            next_remaining = tuple(value - delta for value, delta in zip(remaining, candidate.vector, strict=True))
            if any(value < 0 for value in next_remaining):
                continue
            next_cells = used_cells + len(candidate.cells)
            if next_cells > max_cells:
                continue
            visit(index, next_remaining, (*choices, index), next_cells)
            if not exhausted:
                return

    visit(0, target, (), 0)
    all_ordered = sorted(
        results.values(),
        key=lambda item: (-(item["ratio"] or 0), item["cost"], -item["final_yield"]),
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
        "search_nodes": search_nodes,
        "packing_nodes": packing_nodes,
        "exhaustive": exhausted,
        "search_mode": "monotone",
        "has_more": len(all_ordered) > limit,
        "solution_count": len(all_ordered),
        "available_herbs": available_herbs,
        "excluded_item_ids": sorted(excluded_ids),
    }
