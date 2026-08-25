from __future__ import annotations

"""Pure layout optimization for the 5 x 6 beast-soul board."""

from collections.abc import Iterable, Sequence
from itertools import permutations
from typing import Any


Cell = tuple[int, int]


def board_placements(
    boards: Sequence[dict[str, Any]],
    *,
    soul_id: int = 1,
) -> dict[str, list[list[int]]]:
    """Group a read-only board snapshot into item -> occupied cells."""

    board = next(
        (
            candidate
            for candidate in boards
            if int(candidate.get("soul_id") or 0) == int(soul_id)
        ),
        None,
    )
    if board is None:
        return {}
    result: dict[str, list[list[int]]] = {}
    for cell in board.get("cells") or []:
        item_id = str(cell.get("item_id") or "")
        if not item_id:
            continue
        result.setdefault(item_id, []).append(
            [int(cell["row"]), int(cell["column"])]
        )
    for cells in result.values():
        cells.sort()
    return result


def layout_transition_plan(
    current: dict[str, Sequence[Sequence[int]]],
    selected: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Plan takeoffs before embeds while preserving exact current placements."""

    normalized_current = {
        str(item_id): [list(cell) for cell in sorted(tuple(map(int, c)) for c in cells)]
        for item_id, cells in current.items()
    }
    target = {
        str(item["item_id"]): [
            list(cell)
            for cell in sorted(tuple(map(int, c)) for c in item.get("cells") or [])
        ]
        for item in selected
    }
    preserved = sorted(
        item_id
        for item_id, cells in target.items()
        if normalized_current.get(item_id) == cells
    )
    target_cells = {
        tuple(cell)
        for cells in target.values()
        for cell in cells
    }
    retained_extras = sorted(
        item_id
        for item_id, cells in normalized_current.items()
        if item_id not in target
        and not any(tuple(cell) in target_cells for cell in cells)
    )
    takeoff = [
        {"item_id": item_id, "cells": cells}
        for item_id, cells in sorted(normalized_current.items())
        if item_id not in preserved and item_id not in retained_extras
    ]
    embed = [
        {
            "item_id": str(item["item_id"]),
            "row": int(item["row"]),
            "column": int(item["column"]),
            "cells": target[str(item["item_id"])],
        }
        for item in selected
        if str(item["item_id"]) not in preserved
    ]
    return {
        "preserved_item_ids": preserved,
        "retained_extra_item_ids": retained_extras,
        "takeoff": takeoff,
        "embed": embed,
        "action_count": len(takeoff) + len(embed),
    }


def first_fit_placement(
    shape: Iterable[Sequence[int]],
    occupied: Iterable[Sequence[int]],
    *,
    rows: int = 5,
    columns: int = 6,
) -> list[list[int]] | None:
    """Mirror the game's row-major ``CheckEmbedJade`` first-fit scan."""

    normalized = normalize_shape(shape)
    occupied_cells = {
        (int(cell[0]), int(cell[1])) for cell in occupied
    }
    height = max(row for row, _column in normalized) + 1
    width = max(column for _row, column in normalized) + 1
    for top in range(rows - height + 1):
        for left in range(columns - width + 1):
            cells = [
                [top + row + 1, left + column + 1]
                for row, column in normalized
            ]
            if all(tuple(cell) not in occupied_cells for cell in cells):
                return sorted(cells)
    return None


def plan_first_fit_layout(
    current: dict[str, Sequence[Sequence[int]]],
    selected: Sequence[dict[str, Any]],
    *,
    rows: int = 5,
    columns: int = 6,
    max_current_items: int = 12,
) -> dict[str, Any]:
    """Find a takeoff/embed sequence executable through native detail buttons.

    The game's embed button does not accept a destination; it places the item
    at the first valid row-major slot.  This planner therefore searches which
    current pieces to retain and in which order to embed the missing optimal
    pieces.  It minimizes mutation by preferring more retained optimal pieces,
    then more harmless extras.  The small bound keeps a corrupt/unexpected
    board from turning a Runtime job into an unbounded combinatorial search.
    """

    normalized_current = {
        str(item_id): sorted([list(map(int, cell)) for cell in cells])
        for item_id, cells in current.items()
    }
    if len(normalized_current) > max_current_items:
        raise RuntimeError(
            "兽魂当前阵容项目过多，拒绝执行穷举规划："
            f"{len(normalized_current)} > {max_current_items}"
        )
    selected_by_id = {str(item["item_id"]): dict(item) for item in selected}
    selected_ids = set(selected_by_id)
    current_ids = list(normalized_current)

    # The common already-optimal case must be a true no-op, including harmless
    # zero-score fillers that the optimizer intentionally does not select.
    if selected_ids.issubset(normalized_current):
        return {
            "preserved_item_ids": sorted(selected_ids),
            "retained_extra_item_ids": sorted(set(current_ids) - selected_ids),
            "takeoff": [],
            "embed": [],
            "action_count": 0,
            "strategy": "native_first_fit",
        }

    subsets: list[tuple[int, int, tuple[str, ...]]] = []
    for mask in range(1 << len(current_ids)):
        kept = tuple(
            item_id
            for index, item_id in enumerate(current_ids)
            if mask & (1 << index)
        )
        kept_set = set(kept)
        subsets.append(
            (
                len(kept_set & selected_ids),
                len(kept_set - selected_ids),
                kept,
            )
        )
    subsets.sort(key=lambda value: (-value[0], -value[1], value[2]))

    for _selected_kept, _extras_kept, kept in subsets:
        kept_set = set(kept)
        occupied = {
            tuple(cell)
            for item_id in kept
            for cell in normalized_current[item_id]
        }
        missing = [
            item
            for item_id, item in selected_by_id.items()
            if item_id not in kept_set
        ]
        # Large shapes first usually succeeds sooner; permutations retain exact
        # completeness for the small selected set.
        missing.sort(
            key=lambda item: (-len(item.get("shape") or []), -int(item.get("score") or 0))
        )
        for order in permutations(missing):
            trial_occupied = set(occupied)
            embeds: list[dict[str, Any]] = []
            for item in order:
                cells = first_fit_placement(
                    item.get("shape") or [],
                    trial_occupied,
                    rows=rows,
                    columns=columns,
                )
                if cells is None:
                    break
                trial_occupied.update(tuple(cell) for cell in cells)
                embeds.append(
                    {
                        "item_id": str(item["item_id"]),
                        "row": min(cell[0] for cell in cells),
                        "column": min(cell[1] for cell in cells),
                        "cells": cells,
                    }
                )
            else:
                return {
                    "preserved_item_ids": sorted(kept_set & selected_ids),
                    "retained_extra_item_ids": sorted(kept_set - selected_ids),
                    "takeoff": [
                        {"item_id": item_id, "cells": cells}
                        for item_id, cells in sorted(normalized_current.items())
                        if item_id not in kept_set
                    ],
                    "embed": embeds,
                    "action_count": len(normalized_current) - len(kept) + len(embeds),
                    "strategy": "native_first_fit",
                }
    raise RuntimeError("最优兽魂集合不存在可由原生首次适配镶嵌实现的顺序")


def normalize_shape(cells: Iterable[Sequence[int]]) -> tuple[Cell, ...]:
    parsed = {(int(cell[0]), int(cell[1])) for cell in cells}
    if not parsed:
        raise ValueError("兽魂形状为空")
    min_row = min(row for row, _column in parsed)
    min_column = min(column for _row, column in parsed)
    return tuple(
        sorted((row - min_row, column - min_column) for row, column in parsed)
    )


def shape_orientations(
    cells: Iterable[Sequence[int]],
    *,
    allow_transform: bool,
) -> tuple[tuple[Cell, ...], ...]:
    """Return fixed shape, or all unique rotations/reflections when authorized."""

    original = normalize_shape(cells)
    if not allow_transform:
        return (original,)
    variants: set[tuple[Cell, ...]] = set()
    current = original
    for _rotation in range(4):
        variants.add(normalize_shape(current))
        variants.add(normalize_shape((row, -column) for row, column in current))
        current = normalize_shape((column, -row) for row, column in current)
    return tuple(sorted(variants))


def _placement_cells(
    shape: tuple[Cell, ...],
    *,
    rows: int,
    columns: int,
) -> Iterable[tuple[int, int, tuple[int, ...]]]:
    height = max(row for row, _column in shape) + 1
    width = max(column for _row, column in shape) + 1
    for top in range(rows - height + 1):
        for left in range(columns - width + 1):
            yield (
                top,
                left,
                tuple((top + row) * columns + left + column for row, column in shape),
            )


def optimize_beast_soul_layout(
    items: Sequence[dict[str, Any]],
    *,
    rows: int = 5,
    columns: int = 6,
    allow_transform: bool = False,
    preferred_placements: dict[str, Sequence[Sequence[int]]] | None = None,
    required_min_level: int | None = None,
    required_min_level_count: int | None = None,
    time_limit_seconds: float = 30.0,
) -> dict[str, Any]:
    """Maximize total item score under per-item and per-cell constraints.

    ``allow_transform`` defaults to false because the reverse-engineered game
    placement path always consumes the item's configured shape as-is and no
    rotate/flip operation has been found.  The option exists for calculator
    what-if analysis, not as an assertion that the game supports transforms.
    """

    if rows <= 0 or columns <= 0:
        raise ValueError("棋盘尺寸必须为正数")
    ranked = sorted(
        (
            dict(item)
            for item in items
            if int(item.get("score") or 0) > 0 and item.get("shape")
        ),
        key=lambda item: (-int(item["score"]), int(item["item_id"])),
    )
    if not ranked:
        return {
            "optimal": True,
            "score": 0,
            "selected": [],
            "protected_prefix_k": 0,
            "candidate_count": 0,
            "placement_count": 0,
            "allow_transform": allow_transform,
        }

    preferred_cell_indices = {
        str(item_id): tuple(
            sorted(
                (int(cell[0]) - 1) * columns + int(cell[1]) - 1
                for cell in cells
            )
        )
        for item_id, cells in (preferred_placements or {}).items()
    }
    placements: list[dict[str, Any]] = []
    for rank, item in enumerate(ranked, start=1):
        seen_cell_sets: set[tuple[int, ...]] = set()
        for orientation in shape_orientations(
            item["shape"],
            allow_transform=allow_transform,
        ):
            for top, left, cell_indices in _placement_cells(
                orientation,
                rows=rows,
                columns=columns,
            ):
                if cell_indices in seen_cell_sets:
                    continue
                seen_cell_sets.add(cell_indices)
                placements.append(
                    {
                        "item_index": rank - 1,
                        "rank": rank,
                        "item_id": str(item["item_id"]),
                        "score": int(item["score"]),
                        "top": top,
                        "left": left,
                        "shape": orientation,
                        "cell_indices": cell_indices,
                        "preferred": (
                            preferred_cell_indices.get(str(item["item_id"]))
                            == cell_indices
                        ),
                    }
                )
    if not placements:
        return {
            "optimal": True,
            "score": 0,
            "selected": [],
            "protected_prefix_k": 0,
            "candidate_count": len(ranked),
            "placement_count": 0,
            "allow_transform": allow_transform,
        }

    # Import lazily so snapshot reads and pure shape helpers do not pay SciPy's
    # import cost.  This is a binary set-packing MILP: each item at most once,
    # and each board cell covered at most once.
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix

    if (required_min_level is None) != (required_min_level_count is None):
        raise ValueError("required_min_level 与 required_min_level_count 必须同时提供")
    if required_min_level_count is not None and int(required_min_level_count) < 0:
        raise ValueError("required_min_level_count 不能为负数")

    constraint_rows: list[int] = []
    constraint_columns: list[int] = []
    values: list[float] = []
    item_constraint_count = len(ranked)
    group_constraint_row = item_constraint_count + rows * columns
    has_group_constraint = required_min_level is not None
    for variable, placement in enumerate(placements):
        constraint_rows.append(placement["item_index"])
        constraint_columns.append(variable)
        values.append(1.0)
        for cell_index in placement["cell_indices"]:
            constraint_rows.append(item_constraint_count + cell_index)
            constraint_columns.append(variable)
            values.append(1.0)
        if has_group_constraint and int(
            ranked[placement["item_index"]].get("level") or 0
        ) >= int(required_min_level):
            constraint_rows.append(group_constraint_row)
            constraint_columns.append(variable)
            values.append(1.0)
    constraint_row_count = item_constraint_count + rows * columns + int(
        has_group_constraint
    )
    matrix = coo_matrix(
        (values, (constraint_rows, constraint_columns)),
        shape=(constraint_row_count, len(placements)),
    ).tocsr()
    lower_bounds = np.zeros(matrix.shape[0])
    upper_bounds = np.ones(matrix.shape[0])
    if has_group_constraint:
        required_count = float(required_min_level_count)
        lower_bounds[group_constraint_row] = required_count
        upper_bounds[group_constraint_row] = required_count
    constraint = LinearConstraint(
        matrix,
        lb=lower_bounds,
        ub=upper_bounds,
    )
    objective = -np.asarray([placement["score"] for placement in placements])
    result = milp(
        c=objective,
        integrality=np.ones(len(placements)),
        bounds=Bounds(np.zeros(len(placements)), np.ones(len(placements))),
        constraints=constraint,
        options={"time_limit": max(0.1, float(time_limit_seconds))},
    )
    if result.x is None:
        raise RuntimeError(f"兽魂布局求解失败：status={result.status}, {result.message}")
    optimal_score = int(
        round(
            sum(
                placement["score"]
                for placement, value in zip(placements, result.x, strict=True)
                if value > 0.5
            )
        )
    )
    if preferred_cell_indices:
        score_row = coo_matrix(
            (
                [float(placement["score"]) for placement in placements],
                ([0] * len(placements), list(range(len(placements)))),
            ),
            shape=(1, len(placements)),
        ).tocsr()
        preserve_result = milp(
            c=-np.asarray(
                [1.0 if placement["preferred"] else 0.0 for placement in placements]
            ),
            integrality=np.ones(len(placements)),
            bounds=Bounds(np.zeros(len(placements)), np.ones(len(placements))),
            constraints=(
                constraint,
                LinearConstraint(
                    score_row,
                    lb=np.asarray([float(optimal_score)]),
                    ub=np.asarray([np.inf]),
                ),
            ),
            options={"time_limit": max(0.1, float(time_limit_seconds))},
        )
        if preserve_result.x is None:
            raise RuntimeError(
                "兽魂布局保留求解失败："
                f"status={preserve_result.status}, {preserve_result.message}"
            )
        result = preserve_result
    selected = []
    for variable, value in enumerate(result.x):
        if value <= 0.5:
            continue
        placement = placements[variable]
        selected.append(
            {
                "rank": placement["rank"],
                "item_id": placement["item_id"],
                "score": placement["score"],
                "row": placement["top"] + 1,
                "column": placement["left"] + 1,
                "shape": [list(cell) for cell in placement["shape"]],
                "cells": [
                    [cell_index // columns + 1, cell_index % columns + 1]
                    for cell_index in placement["cell_indices"]
                ],
            }
        )
    selected.sort(key=lambda item: item["rank"])
    preserved_item_ids = sorted(
        item["item_id"]
        for item in selected
        if preferred_cell_indices.get(item["item_id"])
        == tuple(
            sorted(
                (int(cell[0]) - 1) * columns + int(cell[1]) - 1
                for cell in item["cells"]
            )
        )
    )
    return {
        "optimal": result.status == 0,
        "solver_status": int(result.status),
        "solver_message": str(result.message),
        "score": sum(item["score"] for item in selected),
        "selected": selected,
        "preserved_item_ids": preserved_item_ids,
        "protected_prefix_k": max((item["rank"] for item in selected), default=0),
        "candidate_count": len(ranked),
        "placement_count": len(placements),
        "allow_transform": allow_transform,
    }


def beast_soul_low_level_reserves(
    items: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Select the persistent two single-cell and two oriented two-cell reserves."""

    def main_attribute_rolls(item: dict[str, Any]) -> tuple[int, int, int]:
        """Rank score-tied souls by their two zero-score main attributes.

        PetJadeAttr uses 21 ordered rolls per level.  The final digits of the
        main-entry config ID are that roll number, so the two unlike units do
        not need to be added as raw values.  Total roll quality leads; 魂元 then
        breaks an equal total before 气血加成.
        """

        rolls: dict[int, int] = {}
        for entry in item.get("main_entries") or []:
            if str(entry.get("kind") or "") != "attribute":
                continue
            attribute_id = int(entry.get("attribute_id") or 0)
            config_id = int(entry.get("config_id") or 0)
            rolls[attribute_id] = config_id % 10_000 if config_id else 0
        soul_roll = rolls.get(10_500_001, 0)
        hp_rate_roll = rolls.get(1_002, 0)
        return soul_roll + hp_rate_roll, soul_roll, hp_rate_roll

    def ranked(level: int) -> list[dict[str, Any]]:
        return sorted(
            (
                dict(item)
                for item in items
                if int(item.get("level") or 0) == level and item.get("shape")
            ),
            key=lambda item: (
                -int(item.get("score") or 0),
                *(-value for value in main_attribute_rolls(item)),
                int(item["item_id"]),
            ),
        )

    singles = [
        item
        for item in ranked(1)
        if len(normalize_shape(item["shape"])) == 1
    ][:2]
    horizontal = next(
        (
            item
            for item in ranked(2)
            if normalize_shape(item["shape"]) == ((0, 0), (0, 1))
        ),
        None,
    )
    vertical = next(
        (
            item
            for item in ranked(2)
            if normalize_shape(item["shape"]) == ((0, 0), (1, 0))
        ),
        None,
    )
    selected = singles + [item for item in (horizontal, vertical) if item is not None]
    return {
        "single_item_ids": [str(item["item_id"]) for item in singles],
        "horizontal_item_id": str(horizontal["item_id"]) if horizontal else None,
        "vertical_item_id": str(vertical["item_id"]) if vertical else None,
        "item_ids": [str(item["item_id"]) for item in selected],
        "items": selected,
    }


def optimize_beast_soul_structured_layout(
    items: Sequence[dict[str, Any]],
    *,
    rows: int = 5,
    columns: int = 6,
    preferred_placements: dict[str, Sequence[Sequence[int]]] | None = None,
    time_limit_seconds: float = 30.0,
) -> dict[str, Any]:
    """Optimize the business layout and derive its dynamic lock prefix.

    The normal beast-soul board is seven fixed-orientation four-cell pieces plus
    two remaining cells.  Every high-level item through the lowest-ranked high
    item used by that geometric optimum is protected, together with the four
    persistent low-level reserve roles.
    """

    high_items = sorted(
        (
            dict(item)
            for item in items
            if int(item.get("level") or 0) >= 4
            and int(item.get("score") or 0) > 0
            and len(normalize_shape(item.get("shape") or [])) == 4
        ),
        key=lambda item: (-int(item.get("score") or 0), int(item["item_id"])),
    )
    if len(high_items) < 7:
        raise RuntimeError(f"四级以上四格魂晶不足7件：{len(high_items)}")
    reserves = beast_soul_low_level_reserves(items)
    candidate_ids = {
        str(item["item_id"]) for item in high_items + reserves["items"]
    }
    candidates = [
        dict(item) for item in items if str(item.get("item_id")) in candidate_ids
    ]
    layout = optimize_beast_soul_layout(
        candidates,
        rows=rows,
        columns=columns,
        preferred_placements=preferred_placements,
        required_min_level=4,
        required_min_level_count=7,
        time_limit_seconds=time_limit_seconds,
    )
    item_by_id = {str(item["item_id"]): item for item in items}
    selected_high_ids = [
        str(item["item_id"])
        for item in layout.get("selected") or []
        if int(item_by_id[str(item["item_id"])].get("level") or 0) >= 4
    ]
    if len(selected_high_ids) != 7:
        raise RuntimeError(
            "兽魂结构化求解未得到恰好7个四格魂晶："
            f"{len(selected_high_ids)}"
        )
    high_rank = {
        str(item["item_id"]): rank for rank, item in enumerate(high_items, start=1)
    }
    prefix_k = max(high_rank[item_id] for item_id in selected_high_ids)
    high_prefix_ids = [str(item["item_id"]) for item in high_items[:prefix_k]]
    protected_ids = sorted(
        set(high_prefix_ids) | set(reserves["item_ids"]),
        key=int,
    )
    layout.update(
        {
            "protected_prefix_k": prefix_k,
            "protected_prefix_m": prefix_k - 7,
            "high_candidate_count": len(high_items),
            "selected_high_item_ids": selected_high_ids,
            "high_prefix_item_ids": high_prefix_ids,
            "low_level_reserves": {
                key: value for key, value in reserves.items() if key != "items"
            },
            "protected_item_ids": protected_ids,
        }
    )
    return layout
