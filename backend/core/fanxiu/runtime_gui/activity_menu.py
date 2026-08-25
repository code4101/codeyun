from __future__ import annotations

"""Map authoritative activity-menu order to sparse OCR geometry."""

import statistics
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Sequence

from backend.core.fanxiu.instrumentation.activity_menu import (
    ActivityMenuItem,
    ActivityMenuSnapshot,
)
from backend.core.fanxiu.runtime_gui.alignment import GuiCandidate
from backend.core.fanxiu.runtime_gui.text import ocr_name_similarity


ActivityMenuPlanStatus = Literal[
    "ready",
    "incomplete_runtime",
    "target_not_found",
    "ambiguous_target",
    "insufficient_geometry",
]


@dataclass(frozen=True)
class ActivityMenuGrid:
    columns: int
    column_pitch: float | None = None
    row_pitch: float | None = None
    click_offset_heights: float = 1.0
    candidate_bounds: tuple[float, float, float, float] | None = None

    def slot(self, index: int) -> tuple[int, int]:
        if self.columns <= 0 or index <= 0:
            raise ValueError("菜单列数和一基索引必须为正数")
        zero_based = index - 1
        return zero_based // self.columns, zero_based % self.columns


WORLD_LEFT_ACTIVITY_GRID = ActivityMenuGrid(columns=1)
# The current ActivityBtnGroup popup renders four columns.  Measurements are
# from live 900x1600 #403 frames: neighbouring icon-label centres are
# about 145 px apart horizontally and 170 px vertically.  The first row is
# around y=410; the former y>=620 envelope silently discarded it. Runtime owns
# identity/order; these values only let any reliable OCR anchor project a
# target whose own label was missed.
GROUP_POPUP_ACTIVITY_GRID = ActivityMenuGrid(
    columns=4,
    column_pitch=145.0,
    row_pitch=170.0,
    candidate_bounds=(285.0, 380.0, 850.0, 820.0),
)


@dataclass(frozen=True)
class ActivityMenuAnchor:
    runtime_index: int
    runtime_key: str
    runtime_name: str
    gui_key: str
    gui_text: str
    score: float
    click_point: tuple[float, float]


@dataclass(frozen=True)
class ActivityMenuClickPlan:
    status: ActivityMenuPlanStatus
    reason: str
    target: ActivityMenuItem | None = None
    point: tuple[float, float] | None = None
    runtime_fingerprint: str = ""
    anchors: tuple[ActivityMenuAnchor, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "ready"


def _coerce_candidate(value: GuiCandidate | Mapping, index: int) -> GuiCandidate:
    if isinstance(value, GuiCandidate):
        return value
    box = value.get("box")
    if box is None and all(key in value for key in ("x", "y", "w", "h")):
        box = (value["x"], value["y"], value["w"], value["h"])
    return GuiCandidate(
        key=str(value.get("key") or value.get("id") or index),
        text=str(value.get("text") or value.get("ocr_text") or ""),
        box=tuple(box) if isinstance(box, Sequence) and len(box) == 4 else None,
        point=tuple(value["point"])
        if isinstance(value.get("point"), Sequence) and len(value["point"]) == 2
        else None,
        reliability=float(value.get("reliability", 1.0)),
    )


def _with_line_unions(values: Iterable[GuiCandidate | Mapping]) -> tuple:
    original = tuple(values)
    groups: dict[str, list[Mapping]] = {}
    for value in original:
        if isinstance(value, Mapping) and value.get("parent_line_id") is not None:
            groups.setdefault(str(value["parent_line_id"]), []).append(value)
    combined: list[dict] = []
    for line_id, tokens in groups.items():
        ordered = sorted(
            tokens,
            key=lambda item: (int(item.get("order", 0)), float(item.get("x", 0))),
        )
        boxed = [
            item
            for item in ordered
            if all(key in item for key in ("x", "y", "w", "h"))
        ]
        if not boxed:
            continue
        left = min(float(item["x"]) for item in boxed)
        top = min(float(item["y"]) for item in boxed)
        right = max(float(item["x"]) + float(item["w"]) for item in boxed)
        bottom = max(float(item["y"]) + float(item["h"]) for item in boxed)
        combined.append(
            {
                "key": f"ocr-line:{line_id}",
                "text": "".join(str(item.get("text") or "") for item in ordered),
                "box": (left, top, right - left, bottom - top),
            }
        )
    return original + tuple(combined)


def _click_point(
    candidate: GuiCandidate, grid: ActivityMenuGrid
) -> tuple[float, float] | None:
    if candidate.box is None:
        return candidate.point
    x, y, width, height = candidate.box
    return (x + width / 2.0, y - height * grid.click_offset_heights)


def _resolve_target(
    items: tuple[ActivityMenuItem, ...], target: str | int
) -> tuple[ActivityMenuItem | None, ActivityMenuPlanStatus, str]:
    text = str(target).strip()
    matches = [
        item
        for item in items
        if item.key == text
        or item.name == text
        or (item.activity_id is not None and str(item.activity_id) == text)
        or (item.group_type is not None and str(item.group_type) == text)
    ]
    if len(matches) == 1:
        return matches[0], "ready", "目标由 Runtime 身份唯一确定"
    if len(matches) > 1:
        return None, "ambiguous_target", "Runtime 中目标身份不唯一"
    return None, "target_not_found", "当前 Runtime 菜单中没有目标"


def _anchors(
    items: tuple[ActivityMenuItem, ...],
    candidates: tuple[GuiCandidate, ...],
    grid: ActivityMenuGrid,
    minimum_score: float,
    minimum_margin: float,
) -> tuple[ActivityMenuAnchor, ...]:
    result: list[ActivityMenuAnchor] = []
    occupied: set[int] = set()
    for candidate in candidates:
        point = _click_point(candidate, grid)
        if point is None or not candidate.text.strip():
            continue
        ranked = sorted(
            (
                (
                    ocr_name_similarity(item.name, candidate.text)
                    * candidate.reliability,
                    item,
                )
                for item in items
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, item = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        if best_score < minimum_score or best_score - second_score < minimum_margin:
            continue
        if item.index in occupied:
            continue
        occupied.add(item.index)
        result.append(
            ActivityMenuAnchor(
                runtime_index=item.index,
                runtime_key=item.key,
                runtime_name=item.name,
                gui_key=candidate.key,
                gui_text=candidate.text,
                score=round(best_score, 6),
                click_point=point,
            )
        )
    return tuple(result)


def plan_activity_menu_click(
    snapshot: ActivityMenuSnapshot,
    target: str | int,
    gui_candidates: Iterable[GuiCandidate | Mapping],
    *,
    grid: ActivityMenuGrid,
    minimum_anchor_score: float = 0.45,
    minimum_anchor_margin: float = 0.12,
) -> ActivityMenuClickPlan:
    """Plan a click while keeping menu identity authoritative in Runtime."""

    if not snapshot.complete or not snapshot.items:
        return ActivityMenuClickPlan(
            status="incomplete_runtime",
            reason="当前活动菜单 Runtime 未完整加载",
            runtime_fingerprint=snapshot.fingerprint,
        )
    target_item, status, reason = _resolve_target(snapshot.items, target)
    if target_item is None:
        return ActivityMenuClickPlan(
            status=status,
            reason=reason,
            runtime_fingerprint=snapshot.fingerprint,
        )
    candidates = tuple(
        _coerce_candidate(value, index)
        for index, value in enumerate(_with_line_unions(gui_candidates), start=1)
    )
    if grid.candidate_bounds is not None:
        left, top, right, bottom = grid.candidate_bounds
        bounded_candidates = tuple(
            candidate
            for candidate in candidates
            if candidate.box is not None
            and left <= candidate.box[0] + candidate.box[2] / 2.0 <= right
            and top <= candidate.box[1] + candidate.box[3] / 2.0 <= bottom
        )
        # Geometry-only unit consumers may use a cropped/local coordinate
        # system.  Apply the absolute full-frame envelope only when this frame
        # actually contains candidates in that envelope.
        if bounded_candidates:
            candidates = bounded_candidates
    anchors = _anchors(
        snapshot.items,
        candidates,
        grid,
        minimum_anchor_score,
        minimum_anchor_margin,
    )
    direct = [anchor for anchor in anchors if anchor.runtime_index == target_item.index]
    if len(direct) == 1:
        point = direct[0].click_point
    else:
        if not anchors or grid.row_pitch is None or (
            grid.columns > 1 and grid.column_pitch is None
        ):
            return ActivityMenuClickPlan(
                status="insufficient_geometry",
                reason="目标 OCR 未唯一出现，且没有足够栅格证据推导坐标",
                target=target_item,
                runtime_fingerprint=snapshot.fingerprint,
                anchors=anchors,
            )
        target_row, target_column = grid.slot(target_item.index)
        projections: list[tuple[float, float]] = []
        for anchor in anchors:
            anchor_row, anchor_column = grid.slot(anchor.runtime_index)
            projections.append(
                (
                    anchor.click_point[0]
                    + (target_column - anchor_column) * float(grid.column_pitch or 0),
                    anchor.click_point[1]
                    + (target_row - anchor_row) * float(grid.row_pitch),
                )
            )
        point = (
            statistics.median(value[0] for value in projections),
            statistics.median(value[1] for value in projections),
        )
    return ActivityMenuClickPlan(
        status="ready",
        reason="Runtime 身份与当前菜单几何已唯一对齐",
        target=target_item,
        point=(round(point[0], 3), round(point[1], 3)),
        runtime_fingerprint=snapshot.fingerprint,
        anchors=anchors,
    )


__all__ = [
    "ActivityMenuAnchor",
    "ActivityMenuClickPlan",
    "ActivityMenuGrid",
    "ActivityMenuPlanStatus",
    "GROUP_POPUP_ACTIVITY_GRID",
    "WORLD_LEFT_ACTIVITY_GRID",
    "plan_activity_menu_click",
]
