from __future__ import annotations

"""Geometry-only viewport registration for the #525 storage-bag grid.

The game Runtime owns item identity and global order.  This module only turns
the user-marked storage-bag shapes into current-frame grid coordinates.  In
particular, repeated row gaps determine vertical *phase*, not which global
Runtime item is at a given row after a scroll.
"""

from dataclasses import dataclass
from typing import Literal, Mapping, Sequence


Shape = Mapping[str, object]
Box = tuple[float, float, float, float]


def _shape_box(shape: Shape, *, frame_width: int, frame_height: int) -> Box:
    """Convert one normalized annotation shape into a pixel box."""

    try:
        x = float(shape["x"]) * frame_width
        y = float(shape["y"]) * frame_height
        width = float(shape["w"]) * frame_width
        height = float(shape["h"]) * frame_height
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("储物袋标注缺少有效的 x/y/w/h") from exc
    if width <= 0 or height <= 0:
        raise ValueError("储物袋标注宽高必须为正数")
    return (x, y, width, height)


def _center(box: Box) -> tuple[float, float]:
    return (box[0] + box[2] / 2.0, box[1] + box[3] / 2.0)


@dataclass(frozen=True)
class StorageBagGrid:
    """A four-column grid derived from the three user-marked reference cells."""

    window: Box
    first_cell: Box
    columns: int
    column_pitch: float
    row_pitch: float

    @classmethod
    def from_shapes(
        cls,
        shapes: Mapping[str, Shape],
        *,
        frame_width: int,
        frame_height: int,
        window_title: str = "窗口",
        first_title: str = "第1行第1个",
        second_title: str = "第1行第2个",
        next_row_title: str = "第2行第1个",
        columns: int = 4,
    ) -> "StorageBagGrid":
        """Derive grid pitch from the annotated first, next, and next-row cells."""

        if columns <= 0:
            raise ValueError("储物袋列数必须为正数")
        try:
            window = _shape_box(shapes[window_title], frame_width=frame_width, frame_height=frame_height)
            first = _shape_box(shapes[first_title], frame_width=frame_width, frame_height=frame_height)
            second = _shape_box(shapes[second_title], frame_width=frame_width, frame_height=frame_height)
            next_row = _shape_box(shapes[next_row_title], frame_width=frame_width, frame_height=frame_height)
        except KeyError as exc:
            raise ValueError(f"储物袋缺少网格标注：{exc.args[0]}") from exc
        first_center = _center(first)
        second_center = _center(second)
        next_row_center = _center(next_row)
        column_pitch = second_center[0] - first_center[0]
        row_pitch = next_row_center[1] - first_center[1]
        if column_pitch <= 0 or row_pitch <= 0:
            raise ValueError("储物袋基准格未形成向右、向下的规则网格")
        return cls(
            window=window,
            first_cell=first,
            columns=int(columns),
            column_pitch=column_pitch,
            row_pitch=row_pitch,
        )

    @property
    def first_center(self) -> tuple[float, float]:
        return _center(self.first_cell)

    def point(self, *, row: int, column: int, row_center_y: float | None = None) -> tuple[float, float]:
        """Return a cell centre for a current visible row and zero-based column."""

        if row < 0 or not 0 <= column < self.columns:
            raise ValueError("储物袋行列索引超出网格范围")
        first_x, first_y = self.first_center
        y = row_center_y if row_center_y is not None else first_y + row * self.row_pitch
        return (round(first_x + column * self.column_pitch, 3), round(y, 3))


@dataclass(frozen=True)
class StorageBagGapMatch:
    """One horizontal row-gap image match in the current viewport."""

    top: float
    center_y: float
    score: float


@dataclass(frozen=True)
class StorageBagViewport:
    """Current visible row lattice reconstructed from repeated row gaps.

    ``row_centers`` are visual coordinates only.  ``runtime_start_index`` is
    deliberately absent: callers must establish that separately from an
    ordered Runtime/UI sequence overlap before converting a global item index
    into a visible row.
    """

    status: Literal["aligned", "insufficient_geometry"]
    reason: str
    row_centers: tuple[float, ...] = ()
    gap_matches: tuple[StorageBagGapMatch, ...] = ()

    @property
    def aligned(self) -> bool:
        return self.status == "aligned"

    def point(self, grid: StorageBagGrid, *, visible_row: int, column: int) -> tuple[float, float]:
        """Return a point only after the caller supplies a visible-row index."""

        if not self.aligned:
            raise ValueError("储物袋视窗尚未完成行间隙对齐")
        try:
            center_y = self.row_centers[visible_row]
        except IndexError as exc:
            raise ValueError("储物袋可见行索引超出当前视窗") from exc
        return grid.point(row=visible_row, column=column, row_center_y=center_y)


def _gray(frame):
    import cv2

    if frame is None or getattr(frame, "ndim", 0) not in {2, 3}:
        raise ValueError("储物袋行间隙定位需要有效图像帧")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame


def _crop(frame, box: Box):
    height, width = frame.shape[:2]
    x = max(0, int(round(box[0])))
    y = max(0, int(round(box[1])))
    right = min(width, int(round(box[0] + box[2])))
    bottom = min(height, int(round(box[1] + box[3])))
    if right <= x or bottom <= y:
        raise ValueError("储物袋标注超出图像范围")
    return frame[y:bottom, x:right]


def _template_peaks(scores, *, minimum_score: float, minimum_distance: float, maximum: int) -> tuple[StorageBagGapMatch, ...]:
    """Select vertically separated local maxima from a template-score image."""

    import numpy as np

    row_scores = scores.max(axis=1)
    row_x = scores.argmax(axis=1)
    selected: list[StorageBagGapMatch] = []
    for raw_y in np.argsort(row_scores)[::-1]:
        y = int(raw_y)
        score = float(row_scores[y])
        if score < minimum_score:
            break
        if any(abs(y - match.top) < minimum_distance for match in selected):
            continue
        selected.append(StorageBagGapMatch(top=float(y), center_y=0.0, score=round(score, 6)))
        if len(selected) >= maximum:
            break
    del row_x  # x is intentionally not used: horizontal location is fixed by the window.
    return tuple(sorted(selected, key=lambda match: match.top))


def _fit_lattice_base(values: Sequence[float], *, pitch: float) -> float:
    """Fit one row lattice despite a few pixels of per-gap match error."""

    if not values or pitch <= 0:
        raise ValueError("储物袋行栅格拟合缺少有效间隙")
    candidates: list[tuple[float, float]] = []
    for anchor in values:
        aligned = [
            value - round((value - anchor) / pitch) * pitch
            for value in values
        ]
        base = sorted(aligned)[len(aligned) // 2]
        residual = sum(abs(value - round((value - base) / pitch) * pitch - base) for value in values)
        candidates.append((residual, base))
    return min(candidates, key=lambda item: item[0])[1]


def register_storage_bag_viewport(
    reference_frame,
    current_frame,
    *,
    grid: StorageBagGrid,
    row_gap_shape: Shape,
    minimum_score: float = 0.6,
    maximum_gap_matches: int = 8,
) -> StorageBagViewport:
    """Rebuild current row centres by matching the annotated horizontal gap.

    Reference and current frames must use the same pixel geometry.  The result
    deliberately fails closed when fewer than two separated gaps are found,
    because one repeated band cannot distinguish a reliable grid phase from a
    coincidental image match.
    """

    import cv2

    reference_gray = _gray(reference_frame)
    current_gray = _gray(current_frame)
    if reference_gray.shape != current_gray.shape:
        raise ValueError("储物袋参考帧与当前帧尺寸不一致，拒绝缩放猜测网格")
    frame_height, frame_width = reference_gray.shape[:2]
    gap_box = _shape_box(row_gap_shape, frame_width=frame_width, frame_height=frame_height)
    template = _crop(reference_gray, gap_box)
    if template.shape[0] < 2 or template.shape[1] < 2:
        raise ValueError("储物袋行间隙模板过小")
    window_gray = _crop(current_gray, grid.window)
    if window_gray.shape[0] < template.shape[0] or window_gray.shape[1] < template.shape[1]:
        raise ValueError("储物袋窗口小于行间隙模板")
    if float(template.std()) < 1e-6:
        raise ValueError("储物袋行间隙模板没有可辨别图像特征")
    scores = cv2.matchTemplate(window_gray, template, cv2.TM_CCOEFF_NORMED)
    raw_matches = _template_peaks(
        scores,
        minimum_score=float(minimum_score),
        minimum_distance=grid.row_pitch * 0.45,
        maximum=int(maximum_gap_matches),
    )
    matches = tuple(
        StorageBagGapMatch(
            top=round(grid.window[1] + match.top, 3),
            center_y=round(grid.window[1] + match.top + template.shape[0] / 2.0, 3),
            score=match.score,
        )
        for match in raw_matches
    )
    if len(matches) < 2:
        return StorageBagViewport(
            status="insufficient_geometry",
            reason="行间隙匹配不足两条，不能可靠重建当前滚动行栅格",
            gap_matches=matches,
        )

    reference_gap_center = gap_box[1] + gap_box[3] / 2.0
    first_center_y = grid.first_center[1]
    next_row_number = int((reference_gap_center - first_center_y) // grid.row_pitch) + 1
    next_row_center = first_center_y + next_row_number * grid.row_pitch
    gap_to_next_center = next_row_center - reference_gap_center

    lower = grid.window[1] - grid.first_cell[3] / 2.0
    upper = grid.window[1] + grid.window[3] + grid.first_cell[3] / 2.0
    base = _fit_lattice_base(
        [match.center_y + gap_to_next_center for match in matches],
        pitch=grid.row_pitch,
    )
    first_offset = int((lower - base) // grid.row_pitch) - 1
    row_centers = tuple(
        round(base + offset * grid.row_pitch, 3)
        for offset in range(first_offset, first_offset + 20)
        if lower <= base + offset * grid.row_pitch <= upper
    )
    if len(row_centers) < 2:
        return StorageBagViewport(
            status="insufficient_geometry",
            reason="行间隙未形成覆盖窗口的连续行栅格",
            gap_matches=matches,
        )
    return StorageBagViewport(
        status="aligned",
        reason="行间隙已重建当前视窗的纵向行栅格；全局索引仍需 Runtime 序列锚定",
        row_centers=row_centers,
        gap_matches=matches,
    )


__all__ = [
    "StorageBagGapMatch",
    "StorageBagGrid",
    "StorageBagViewport",
    "register_storage_bag_viewport",
]
