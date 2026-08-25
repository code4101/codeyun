from __future__ import annotations

"""Runtime-GUI alignment for the shared gameplay-rank divine-item exchange."""

from typing import Any, Iterable, Mapping, Sequence

from backend.core.fanxiu.runtime_gui.storage_bag_alignment import (
    StorageBagItemClickPlan,
    StorageBagQuantityObservation,
    StorageBagVisibleCell,
    plan_storage_bag_item_click,
)
from backend.core.fanxiu.runtime_gui.storage_bag_grid import Box


def visible_sacred_exchange_rows(
    first_row: Box,
    second_row: Box,
    window: Box,
    *,
    frame_width: int,
    frame_height: int,
) -> tuple[StorageBagVisibleCell, ...]:
    """Project fully visible single-column rows from two annotated row boxes.

    Boxes may be normalized annotation coordinates or already projected pixel
    coordinates.  Only complete rows inside the annotated scroll window are
    actionable, so a clipped row can never be assigned a Runtime identity.
    """

    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("神物兑换帧尺寸必须为正数")

    def pixels(box: Box) -> Box:
        x, y, width, height = (float(value) for value in box)
        if width <= 0 or height <= 0:
            raise ValueError("神物兑换标注框宽高必须为正数")
        if max(abs(x), abs(y), abs(width), abs(height)) <= 1.0:
            return (x * frame_width, y * frame_height, width * frame_width, height * frame_height)
        return (x, y, width, height)

    first = pixels(first_row)
    second = pixels(second_row)
    viewport = pixels(window)
    pitch = second[1] - first[1]
    if pitch <= 0:
        raise ValueError("神物兑换第2行必须位于第1行下方")
    left, top, width, height = first
    window_left, window_top, window_width, window_height = viewport
    cells: list[StorageBagVisibleCell] = []
    for row in range(64):
        row_top = top + row * pitch
        if row_top >= window_top + window_height:
            break
        center_x = left + width / 2.0
        center_y = row_top + height / 2.0
        if (
            row_top < window_top
            or row_top + height > window_top + window_height
            or center_x < window_left
            or center_x > window_left + window_width
        ):
            continue
        cells.append(
            StorageBagVisibleCell(
                visible_index=len(cells),
                visible_row=row,
                column=0,
                box=(round(left, 3), round(row_top, 3), round(width, 3), round(height, 3)),
                point=(round(center_x, 3), round(center_y, 3)),
            )
        )
    if len(cells) < 2:
        raise ValueError("神物兑换滚动窗口内不足两个完整行，不能进行序列对齐")
    return tuple(cells)


def sacred_exchange_quantity_observations(
    rows: Sequence[StorageBagVisibleCell],
    tokens: Iterable[Mapping[str, Any]],
    *,
    runtime_quantities: Iterable[int],
    minimum_confidence: float = 0.5,
    x_band: tuple[float, float] = (0.0, 0.46),
) -> tuple[StorageBagQuantityObservation, ...]:
    """Keep only row-local quantities that also exist in the Runtime list.

    Floating damage text and partially occluded digits are common on this
    screen.  OCR is therefore never identity evidence by itself: a token must
    be a pure integer, lie in the row's left quantity band, and equal a value
    from the complete active Runtime projection.
    """

    band_start, band_end = (float(value) for value in x_band)
    if not 0.0 <= band_start < band_end <= 1.0:
        raise ValueError("神物兑换数量 OCR 横向带必须位于 0..1 且起点小于终点")
    allowed = {int(value) for value in runtime_quantities if int(value) >= 0}
    observations: list[StorageBagQuantityObservation] = []
    for row in rows:
        left, top, width, height = row.box
        candidates: list[tuple[float, str, float]] = []
        for token in tokens:
            text = str(token.get("text") or "").strip().replace(",", "")
            if not text.isdecimal():
                continue
            quantity = int(text)
            if quantity not in allowed:
                continue
            raw_confidence = token.get("score", token.get("confidence"))
            confidence = float(raw_confidence) if raw_confidence is not None else minimum_confidence
            x = float(token.get("x") or 0.0)
            y = float(token.get("y") or 0.0)
            token_width = float(token.get("w") or token.get("width") or 0.0)
            token_height = float(token.get("h") or token.get("height") or 0.0)
            center_x = x + token_width / 2.0
            center_y = y + token_height / 2.0
            if (
                confidence < minimum_confidence
                or not left + width * band_start <= center_x <= left + width * band_end
                or not top + height * 0.42 <= center_y <= top + height
            ):
                continue
            candidates.append((x, text, confidence))
        if len(candidates) != 1:
            continue
        _x, text, confidence = candidates[0]
        observations.append(
            StorageBagQuantityObservation(
                visible_index=row.visible_index,
                quantity=int(text),
                required=None,
                text=text,
                confidence=confidence,
            )
        )
    return tuple(observations)


def plan_sacred_exchange_item_click(
    snapshot: Mapping[str, Any],
    *,
    target_base_id: int,
    rows: Sequence[StorageBagVisibleCell],
    observations: Sequence[StorageBagQuantityObservation],
) -> StorageBagItemClickPlan:
    """Map a divine item to one row only after unique ordered registration."""

    return plan_storage_bag_item_click(
        snapshot,
        target_base_id=int(target_base_id),
        cells=rows,
        observations=observations,
        minimum_observations=2,
    )


__all__ = [
    "plan_sacred_exchange_item_click",
    "sacred_exchange_quantity_observations",
    "visible_sacred_exchange_rows",
]
