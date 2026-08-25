from __future__ import annotations

"""Plan main-world menu clicks from Runtime order and sparse OCR geometry."""

import statistics
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

from backend.core.fanxiu.instrumentation.world_menu import (
    WorldMenuItem,
    WorldMenuSnapshot,
)
from backend.core.fanxiu.runtime_gui.alignment import GuiCandidate
from backend.core.fanxiu.runtime_gui.text import ocr_name_similarity


MenuPlanStatus = Literal[
    "ready",
    "incomplete_runtime",
    "target_not_found",
    "ambiguous_target",
    "insufficient_geometry",
]


@dataclass(frozen=True)
class OrderedMenuGrid:
    columns: int = 4
    column_pitch: float | None = None
    row_pitch: float | None = None
    icon_offset_heights: float = 1.0

    def slot(self, index: int) -> tuple[int, int]:
        if self.columns <= 0 or index <= 0:
            raise ValueError("菜单列数和一基索引必须为正数")
        zero_based = index - 1
        return zero_based // self.columns, zero_based % self.columns


@dataclass(frozen=True)
class MenuAnchorEvidence:
    runtime_index: int
    runtime_name: str
    gui_key: str
    gui_text: str
    score: float
    icon_point: tuple[float, float]


@dataclass(frozen=True)
class WorldMenuClickPlan:
    status: MenuPlanStatus
    reason: str
    target: WorldMenuItem | None = None
    point: tuple[float, float] | None = None
    expected_scene_ids: tuple[int, ...] = ()
    runtime_fingerprint: str = ""
    anchors: tuple[MenuAnchorEvidence, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "ready"


def _coerce_candidate(value: GuiCandidate | dict, index: int) -> GuiCandidate:
    if isinstance(value, GuiCandidate):
        return value
    box = value.get("box")
    return GuiCandidate(
        key=str(value.get("key") or value.get("id") or index),
        text=str(value.get("text") or value.get("ocr_text") or ""),
        box=tuple(box) if isinstance(box, Sequence) and len(box) == 4 else None,
        reliability=float(value.get("reliability", 1.0)),
    )


def _with_grouped_ocr_lines(
    values: Iterable[GuiCandidate | dict],
) -> tuple[GuiCandidate | dict, ...]:
    """Keep raw OCR tokens and add their line-level unions as candidates."""

    original = tuple(values)
    groups: dict[str, list[dict]] = {}
    for value in original:
        if not isinstance(value, dict) or value.get("parent_line_id") is None:
            continue
        groups.setdefault(str(value["parent_line_id"]), []).append(value)
    combined: list[dict] = []
    for line_id, tokens in groups.items():
        ordered = sorted(tokens, key=lambda item: (int(item.get("order", 0)), float(item.get("x", 0))))
        boxed = [item for item in ordered if all(key in item for key in ("x", "y", "w", "h"))]
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


def _icon_point(candidate: GuiCandidate, grid: OrderedMenuGrid) -> tuple[float, float] | None:
    if candidate.box is None:
        return candidate.point
    x, y, width, height = candidate.box
    return (x + width / 2.0, y - height * grid.icon_offset_heights)


def _resolve_target(
    items: tuple[WorldMenuItem, ...], target: str | int
) -> tuple[WorldMenuItem | None, MenuPlanStatus, str]:
    text = str(target).strip()
    exact = [item for item in items if item.key == text or item.name == text]
    if len(exact) == 1:
        return exact[0], "ready", "目标由 Runtime key/name 唯一确定"
    if len(exact) > 1:
        return None, "ambiguous_target", "Runtime 中目标 key/name 不唯一"
    return None, "target_not_found", "当前 Runtime 可见菜单中没有目标"


def _anchor_candidates(
    items: tuple[WorldMenuItem, ...],
    candidates: tuple[GuiCandidate, ...],
    grid: OrderedMenuGrid,
    *,
    minimum_score: float,
    minimum_margin: float,
) -> tuple[MenuAnchorEvidence, ...]:
    anchors: list[MenuAnchorEvidence] = []
    occupied: set[int] = set()
    for candidate in candidates:
        point = _icon_point(candidate, grid)
        if point is None or not candidate.text.strip():
            continue
        ranked = sorted(
            (
                (ocr_name_similarity(item.name, candidate.text) * candidate.reliability, item)
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
        anchors.append(
            MenuAnchorEvidence(
                runtime_index=item.index,
                runtime_name=item.name,
                gui_key=candidate.key,
                gui_text=candidate.text,
                score=round(best_score, 6),
                icon_point=point,
            )
        )
    return tuple(anchors)


def plan_world_menu_click(
    snapshot: WorldMenuSnapshot,
    target: str | int,
    gui_candidates: Iterable[GuiCandidate | dict],
    *,
    expected_scene_ids: Iterable[int],
    grid: OrderedMenuGrid = OrderedMenuGrid(),
    minimum_anchor_score: float = 0.45,
    minimum_anchor_margin: float = 0.12,
) -> WorldMenuClickPlan:
    """Resolve one target without making OCR the source of menu identity."""

    expected = tuple(dict.fromkeys(int(value) for value in expected_scene_ids))
    if not snapshot.complete or not snapshot.items:
        return WorldMenuClickPlan(
            status="incomplete_runtime",
            reason="当前菜单 Runtime 未完整加载",
            runtime_fingerprint=snapshot.fingerprint,
        )
    target_item, status, reason = _resolve_target(snapshot.items, target)
    if target_item is None:
        return WorldMenuClickPlan(
            status=status,
            reason=reason,
            runtime_fingerprint=snapshot.fingerprint,
        )
    candidates = tuple(
        _coerce_candidate(value, index)
        for index, value in enumerate(_with_grouped_ocr_lines(gui_candidates), start=1)
    )
    anchors = _anchor_candidates(
        snapshot.items,
        candidates,
        grid,
        minimum_score=minimum_anchor_score,
        minimum_margin=minimum_anchor_margin,
    )
    target_anchors = [item for item in anchors if item.runtime_index == target_item.index]
    if len(target_anchors) == 1:
        point = target_anchors[0].icon_point
    else:
        if not anchors or grid.column_pitch is None or grid.row_pitch is None:
            return WorldMenuClickPlan(
                status="insufficient_geometry",
                reason="目标 OCR 未唯一出现，且没有足够栅格几何从其它锚点推导",
                target=target_item,
                expected_scene_ids=expected,
                runtime_fingerprint=snapshot.fingerprint,
                anchors=anchors,
            )
        target_row, target_column = grid.slot(target_item.index)
        projections: list[tuple[float, float]] = []
        for anchor in anchors:
            anchor_row, anchor_column = grid.slot(anchor.runtime_index)
            projections.append(
                (
                    anchor.icon_point[0]
                    + (target_column - anchor_column) * grid.column_pitch,
                    anchor.icon_point[1]
                    + (target_row - anchor_row) * grid.row_pitch,
                )
            )
        point = (
            statistics.median(value[0] for value in projections),
            statistics.median(value[1] for value in projections),
        )
    if not expected:
        return WorldMenuClickPlan(
            status="insufficient_geometry",
            reason="未提供独立的点击后继场景验证契约",
            target=target_item,
            runtime_fingerprint=snapshot.fingerprint,
            anchors=anchors,
        )
    return WorldMenuClickPlan(
        status="ready",
        reason="Runtime 目标与四列菜单几何已唯一确定",
        target=target_item,
        point=(round(point[0], 3), round(point[1], 3)),
        expected_scene_ids=expected,
        runtime_fingerprint=snapshot.fingerprint,
        anchors=anchors,
    )


def verify_world_menu_successor(plan: WorldMenuClickPlan, scene_id: int | None) -> bool:
    """Verify the independent post-click scene contract."""

    return bool(plan.ready and scene_id is not None and int(scene_id) in plan.expected_scene_ids)


__all__ = [
    "MenuAnchorEvidence",
    "OrderedMenuGrid",
    "WorldMenuClickPlan",
    "plan_world_menu_click",
    "verify_world_menu_successor",
]
