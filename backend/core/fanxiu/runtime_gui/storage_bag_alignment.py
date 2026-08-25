from __future__ import annotations

"""Sequence registration between #525 quantity OCR and ``ItemInfoList``.

The bottom-right number in a storage-bag cell is not an item name.  It is only
an observation of ``num``: either ``num`` itself or ``num/required`` for an
item with a composition requirement.  Several adjacent observations are
therefore matched to the already-loaded, ordered Runtime projection.  A click
plan exists only when this offset is globally unique.
"""

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Literal, Mapping, Sequence

from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.runtime_gui.storage_bag_grid import Box, StorageBagGrid, StorageBagViewport
from backend.core.fanxiu.runtime_gui.text import (
    DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD,
    best_ocr_name_match,
)


@dataclass(frozen=True)
class StorageBagVisibleCell:
    """One fully reachable grid cell in the current scroll viewport."""

    visible_index: int
    visible_row: int
    column: int
    box: Box
    point: tuple[float, float]


@dataclass(frozen=True)
class StorageBagQuantityObservation:
    """OCR observation from the bottom-right quantity field of one cell."""

    visible_index: int
    quantity: int
    required: int | None
    text: str
    confidence: float


@dataclass(frozen=True)
class StorageBagItemClickPlan:
    """A unique Runtime item mapped to one current #525 grid coordinate."""

    status: Literal[
        "ready",
        "ambiguous_offset",
        "insufficient_observations",
        "target_not_found",
        "target_not_visible",
        "invalid_runtime_snapshot",
    ]
    reason: str
    runtime_index: int | None = None
    runtime_item: Mapping[str, Any] | None = None
    point: tuple[float, float] | None = None
    viewport_runtime_start: int | None = None
    candidate_starts: tuple[int, ...] = ()
    observations: tuple[StorageBagQuantityObservation, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.point is not None


@dataclass(frozen=True)
class StorageBagDetailVerification:
    """Independent post-click verification from the item-detail title OCR ROI."""

    status: Literal["confirmed", "name_mismatch", "missing_title_ocr", "unplanned_click"]
    reason: str
    expected_name: str
    observed_name: str = ""
    similarity: float = 0.0

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"


@dataclass(frozen=True)
class StorageBagTarget:
    """The Runtime instance plus static-catalog identity required for a click."""

    base_id: int
    instance_id: str
    name: str
    type_name: str
    runtime_index: int


@dataclass(frozen=True)
class StorageBagScrollDirective:
    """A bounded scroll intent; the next viewport must always be re-registered."""

    direction: Literal["none", "up", "down"]
    mode: Literal["visible", "fine", "coarse"]
    target_runtime_index: int
    remaining_items: int
    reason: str


def verify_storage_bag_item_detail(
    plan: StorageBagItemClickPlan,
    *,
    expected_name: str,
    detail_title_texts: Iterable[str],
    threshold: float = DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD,
) -> StorageBagDetailVerification:
    """Confirm a planned click by fuzzy-matching only detail-title OCR text.

    The caller must pass OCR obtained from the opened item's title region, not
    generic full-screen OCR.  This makes the second evidence independent from
    the quantity sequence that selected the grid cell.
    """

    expected = str(expected_name or "").strip()
    if not plan.ready:
        return StorageBagDetailVerification(
            "unplanned_click",
            "没有通过唯一数量序列生成点击计划，详情名称不能补授权",
            expected,
        )
    if not expected:
        raise ValueError("详情二次核验需要 Runtime/Catalog 提供目标名称")
    # Shared spatial OCR can expose a CJK title as one token per character.
    # Preserve the individual candidates (some engines emit whole words), and
    # add their title-ROI reading order as a second candidate for the same
    # independent detail check.
    title_candidates = tuple(str(text or "").strip() for text in detail_title_texts)
    merged_title = "".join(title_candidates)
    if merged_title and merged_title not in title_candidates:
        title_candidates = (*title_candidates, merged_title)
    match = best_ocr_name_match(expected, title_candidates, threshold=threshold)
    if match is None:
        return StorageBagDetailVerification(
            "missing_title_ocr",
            "详情标题区域没有可用 OCR 文本，不能确认已点中目标物品",
            expected,
        )
    if not match.passed_threshold:
        return StorageBagDetailVerification(
            "name_mismatch",
            f"详情标题与目标名称相似度 {match.similarity:.0%}，低于门槛 {float(threshold):.0%}",
            expected,
            match.observed,
            match.similarity,
        )
    return StorageBagDetailVerification(
        "confirmed",
        f"数量序列与详情标题二次核验均成立（名称相似度 {match.similarity:.0%}）",
        expected,
        match.observed,
        match.similarity,
    )


def _number_fragments_in_columns(
    fragments: Iterable[Mapping[str, Any]],
    *,
    grid: StorageBagGrid,
    y_minimum: float,
    y_maximum: float,
) -> tuple[tuple[float, float], ...]:
    """Return ``(center_y, confidence)`` for numeric OCR boxes in grid columns."""

    first_x, _first_y = grid.first_center
    accepted: list[tuple[float, float]] = []
    for fragment in fragments:
        text = str(fragment.get("text") or "").strip()
        values = parse_ocr_values(text)
        if values is None or len(values) > 2:
            continue
        x = float(fragment.get("x") or 0.0)
        y = float(fragment.get("y") or 0.0)
        width = float(fragment.get("w") or fragment.get("width") or 0.0)
        height = float(fragment.get("h") or fragment.get("height") or 0.0)
        center_x = x + width / 2.0
        center_y = y + height / 2.0
        column = round((center_x - first_x) / grid.column_pitch)
        expected_x = first_x + column * grid.column_pitch
        if (
            not 0 <= column < grid.columns
            or abs(center_x - expected_x) > grid.first_cell[2] * 0.52
            or not y_minimum <= center_y <= y_maximum
        ):
            continue
        confidence = float(fragment.get("score") or fragment.get("confidence") or 0.0)
        accepted.append((center_y, confidence))
    return tuple(accepted)


def _quantity_row_centers(
    anchor_centers: Sequence[float],
    *,
    quantity_offset_y: float,
    grid: StorageBagGrid,
) -> tuple[float, ...]:
    """Fit a vertical item lattice from multiple right-bottom number boxes."""

    if len(anchor_centers) < 2:
        return ()
    distinct = sorted(anchor_centers)
    bands: list[float] = []
    for value in distinct:
        if not bands or value - bands[-1] >= grid.row_pitch * 0.45:
            bands.append(value)
    if len(bands) < 2:
        return ()
    candidates: list[tuple[float, float]] = []
    values = [value - quantity_offset_y for value in anchor_centers]
    for anchor in values:
        aligned = [value - round((value - anchor) / grid.row_pitch) * grid.row_pitch for value in values]
        base = float(median(aligned))
        residual = sum(abs(value - round((value - base) / grid.row_pitch) * grid.row_pitch - base) for value in values)
        candidates.append((residual, base))
    base = min(candidates, key=lambda item: item[0])[1]
    lower = grid.window[1] - grid.first_cell[3] / 2.0
    upper = grid.window[1] + grid.window[3] + grid.first_cell[3] / 2.0
    first_offset = int((lower - base) // grid.row_pitch) - 1
    return tuple(
        round(base + offset * grid.row_pitch, 3)
        for offset in range(first_offset, first_offset + 20)
        if lower <= base + offset * grid.row_pitch <= upper
    )


def register_storage_bag_viewport_from_quantity_ocr(
    reference_fragments: Iterable[Mapping[str, Any]],
    current_fragments: Iterable[Mapping[str, Any]],
    *,
    grid: StorageBagGrid,
) -> StorageBagViewport:
    """Reconstruct visible rows from OCR number *boxes*, not their values.

    The reference frame calibrates the vertical offset between an annotated
    item centre and its bottom-right quantity.  Current numeric OCR boxes then
    fit the row pitch.  Quantity text may be wrong; its geometry is still
    usable, while value correctness is handled separately by sequence matching.
    """

    reference = tuple(reference_fragments)
    current = tuple(current_fragments)
    first_y = grid.first_center[1]
    reference_anchors = _number_fragments_in_columns(
        reference,
        grid=grid,
        y_minimum=grid.first_cell[1] + grid.first_cell[3] * 0.40,
        y_maximum=grid.window[1] + grid.window[3],
    )
    if not reference_anchors:
        return StorageBagViewport(
            status="insufficient_geometry",
            reason="参考帧未找到首行右下角数字 OCR 框，不能标定数字到物品中心的偏移",
        )
    reference_offsets: list[float] = []
    for center_y, _score in reference_anchors:
        reference_row = round((center_y - first_y) / grid.row_pitch)
        expected_center = first_y + reference_row * grid.row_pitch
        offset = center_y - expected_center
        # Numbers belong to the lower part of their item box.  This excludes
        # unrelated numeric UI from the reference calibration.
        if grid.first_cell[3] * 0.20 <= offset <= grid.first_cell[3] * 0.55:
            reference_offsets.append(offset)
    if not reference_offsets:
        return StorageBagViewport(
            status="insufficient_geometry",
            reason="参考帧数字 OCR 框未落在已标注物品的右下角带，不能标定偏移",
        )
    quantity_offset_y = float(median(reference_offsets))
    anchors = _number_fragments_in_columns(
        current,
        grid=grid,
        y_minimum=grid.window[1],
        y_maximum=grid.window[1] + grid.window[3],
    )
    row_centers = _quantity_row_centers(
        [center_y for center_y, _score in anchors],
        quantity_offset_y=quantity_offset_y,
        grid=grid,
    )
    if len(row_centers) < 2:
        return StorageBagViewport(
            status="insufficient_geometry",
            reason="当前帧数字 OCR 框不足两个不同行，不能可靠重建行栅格",
        )
    return StorageBagViewport(
        status="aligned",
        reason="已由右下角数字 OCR 框重建当前行栅格；数字值仍单独参与 Runtime 序列对齐",
        row_centers=row_centers,
    )


def visible_storage_bag_cells(
    grid: StorageBagGrid,
    viewport: StorageBagViewport,
) -> tuple[StorageBagVisibleCell, ...]:
    """Project fully visible four-column cells from the current row lattice."""

    if not viewport.aligned:
        raise ValueError("储物袋视窗未完成行间隙对齐")
    cell_width, cell_height = grid.first_cell[2:]
    window_x, window_y, window_width, window_height = grid.window
    cells: list[StorageBagVisibleCell] = []
    for visible_row, center_y in enumerate(viewport.row_centers):
        top = center_y - cell_height / 2.0
        if top < window_y or top + cell_height > window_y + window_height:
            continue
        for column in range(grid.columns):
            center_x, point_y = viewport.point(grid, visible_row=visible_row, column=column)
            left = center_x - cell_width / 2.0
            # The user-established invariant is exactly four columns.  A
            # hand-drawn container may shave a few pixels off a card edge, so
            # require only the actionable centre to remain in the window; do
            # not silently delete the fourth column and renumber the grid.
            if not window_x <= center_x <= window_x + window_width:
                continue
            cells.append(
                StorageBagVisibleCell(
                    visible_index=len(cells),
                    visible_row=visible_row,
                    column=column,
                    box=(round(left, 3), round(top, 3), cell_width, cell_height),
                    point=(center_x, point_y),
                )
            )
    return tuple(cells)


def quantity_observations_from_ocr(
    cells: Sequence[StorageBagVisibleCell],
    fragments: Iterable[Mapping[str, Any]],
    *,
    minimum_confidence: float = 0.5,
) -> tuple[StorageBagQuantityObservation, ...]:
    """Read one quantity or numerator/denominator group for each cell.

    OCR fragments are assigned geometrically, then constrained to the
    bottom-right part of the cell.  Text outside that field cannot become
    quantity evidence merely because it happens to contain digits.
    """

    observations: list[StorageBagQuantityObservation] = []
    for cell in cells:
        left, top, width, height = cell.box
        candidates: list[tuple[float, float, str, float]] = []
        for fragment in fragments:
            text = str(fragment.get("text") or "").strip()
            # The shared spatial OCR token stream intentionally omits a
            # confidence field.  A missing score must not erase a usable
            # bottom-right numeric *geometry* observation: its value is only
            # soft evidence and is independently checked against the full
            # ordered Runtime sequence below.  Explicit low scores still
            # retain their normal rejection semantics.
            raw_confidence = fragment.get("score")
            if raw_confidence is None:
                raw_confidence = fragment.get("confidence")
            confidence = float(raw_confidence) if raw_confidence is not None else minimum_confidence
            x = float(fragment.get("x") or 0.0)
            y = float(fragment.get("y") or 0.0)
            fragment_width = float(fragment.get("w") or fragment.get("width") or 0.0)
            fragment_height = float(fragment.get("h") or fragment.get("height") or 0.0)
            center_x = x + fragment_width / 2.0
            center_y = y + fragment_height / 2.0
            if (
                confidence < minimum_confidence
                or not text
                or center_x < left + width * 0.42
                or center_x > left + width
                or center_y < top + height * 0.52
                or center_y > top + height
            ):
                continue
            candidates.append((y, x, text, confidence))
        if not candidates:
            continue
        candidates.sort()
        text = "".join(candidate[2] for candidate in candidates)
        values = parse_ocr_values(text)
        if values is None or len(values) not in {1, 2}:
            continue
        observations.append(
            StorageBagQuantityObservation(
                visible_index=cell.visible_index,
                quantity=values[0],
                required=values[1] if len(values) == 2 else None,
                text=text,
                confidence=min(candidate[3] for candidate in candidates),
            )
        )
    return tuple(observations)


def _runtime_items(snapshot: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...] | None:
    if snapshot.get("complete") is not True:
        return None
    items = snapshot.get("items")
    if not isinstance(items, list):
        return None
    ordered = tuple(item for item in items if isinstance(item, Mapping) and not item.get("is_padding"))
    if any(not isinstance(item.get("num"), int) or not isinstance(item.get("base_id"), int) for item in ordered):
        return None
    return ordered


def prepare_storage_bag_target(
    snapshot: Mapping[str, Any],
    *,
    base_id: int,
    catalog_cards_by_id: Mapping[str, Mapping[str, Any]],
) -> StorageBagTarget:
    """Join the unique live Runtime item to its immutable Catalog name/type."""

    runtime_items = _runtime_items(snapshot)
    if runtime_items is None:
        raise ValueError("ItemInfoList Runtime 快照不完整，不能准备储物袋目标")
    target_id = int(base_id)
    matches = [
        (index, item)
        for index, item in enumerate(runtime_items)
        if item.get("base_id") == target_id
    ]
    if len(matches) != 1:
        raise ValueError(f"Runtime 中 base_id={target_id} 的实例数为 {len(matches)}，不能唯一操作")
    card = catalog_cards_by_id.get(str(target_id))
    name = str((card or {}).get("name") or "").strip()
    if not name:
        raise ValueError(f"Catalog 缺少 base_id={target_id} 的稳定物品名称")
    index, item = matches[0]
    instance_id = str(item.get("instance_id") or "")
    if not instance_id:
        raise ValueError(f"Runtime 中 base_id={target_id} 缺少实例 id")
    return StorageBagTarget(
        base_id=target_id,
        instance_id=instance_id,
        name=name,
        type_name=str((card or {}).get("type_name") or ""),
        runtime_index=index,
    )


def prepare_storage_bag_target_by_name(
    snapshot: Mapping[str, Any],
    *,
    name: str,
    catalog_cards_by_id: Mapping[str, Mapping[str, Any]],
) -> StorageBagTarget:
    """Resolve one catalog name before joining it to the live bag instance."""

    target_name = str(name or "").strip()
    if not target_name:
        raise ValueError("储物袋目标名称不能为空")
    matches: list[int] = []
    for raw_id, card in catalog_cards_by_id.items():
        if not isinstance(card, Mapping):
            continue
        if str(card.get("name") or "").strip() != target_name:
            continue
        try:
            matches.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    if len(matches) != 1:
        raise ValueError(
            f"Catalog 中名称「{target_name}」对应 {len(matches)} 个 base_id，不能唯一操作"
        )
    return prepare_storage_bag_target(
        snapshot,
        base_id=matches[0],
        catalog_cards_by_id=catalog_cards_by_id,
    )


def plan_storage_bag_scroll(
    *,
    target_runtime_index: int,
    viewport_runtime_start: int,
    visible_cell_count: int,
) -> StorageBagScrollDirective:
    """Choose only scroll direction/coarseness; never infer post-scroll index.

    A drag does not reorder the already captured ``ItemInfoList`` snapshot.
    Reuse that snapshot across a scroll sequence; acquire only a fresh frame,
    rebuild its visible geometry, and re-register its Runtime start from the
    new quantity observations before any click.
    """

    if target_runtime_index < 0 or viewport_runtime_start < 0 or visible_cell_count < 1:
        raise ValueError("储物袋滚动规划需要非负索引和至少一个可见格")
    relative = target_runtime_index - viewport_runtime_start
    if 0 <= relative < visible_cell_count:
        return StorageBagScrollDirective(
            "none", "visible", target_runtime_index, 0, "目标已在当前已配准视窗"
        )
    direction: Literal["up", "down"] = "down" if relative >= visible_cell_count else "up"
    remaining = relative - visible_cell_count + 1 if direction == "down" else -relative
    mode: Literal["fine", "coarse"] = "coarse" if remaining > visible_cell_count * 4 else "fine"
    return StorageBagScrollDirective(
        direction,
        mode,
        target_runtime_index,
        remaining,
        "滚动不改变既有 Runtime 顺序；下一帧仅重新配准 GUI/OCR 数量序列起点",
    )


def _ranked_matching_starts(
    runtime_items: Sequence[Mapping[str, Any]],
    observations: Sequence[StorageBagQuantityObservation],
) -> tuple[tuple[int, int], ...]:
    if not observations:
        return ()
    maximum_visible_index = max(observation.visible_index for observation in observations)
    starts: list[tuple[int, int]] = []
    for start in range(0, len(runtime_items) - maximum_visible_index):
        matched = sum(
            runtime_items[start + observation.visible_index]["num"] == observation.quantity
            for observation in observations
        )
        starts.append((start, matched))
    return tuple(sorted(starts, key=lambda item: (-item[1], item[0])))


def _exact_matching_starts(
    runtime_items: Sequence[Mapping[str, Any]],
    observations: Sequence[StorageBagQuantityObservation],
) -> tuple[int, ...]:
    """Find starts satisfying every anchor without ranking all Runtime rows.

    Two observations are the smallest safe registration proof.  That narrow
    case must be exact and globally unique, so a linear scan is both clearer
    and cheaper than building and sorting a score for every possible start.
    """

    if not observations:
        return ()
    maximum_visible_index = max(observation.visible_index for observation in observations)
    starts: list[int] = []
    for start in range(0, len(runtime_items) - maximum_visible_index):
        if all(
            runtime_items[start + observation.visible_index]["num"] == observation.quantity
            for observation in observations
        ):
            starts.append(start)
            # A second exact start already proves ambiguity.  Do not retain or
            # scan an unbounded repeated-value candidate set.
            if len(starts) == 2:
                break
    return tuple(starts)


def _geometry_ordered_observations(
    cells: Sequence[StorageBagVisibleCell],
    observations: Sequence[StorageBagQuantityObservation],
) -> tuple[tuple[StorageBagQuantityObservation, ...], str | None]:
    """Keep only a well-formed observation sequence from the current grid.

    Quantity values alone cannot establish a viewport.  The visible indices
    must name distinct current cells, and those indices must preserve the
    row-major geometry produced by :func:`visible_storage_bag_cells`.
    """

    ordered_cells = sorted(cells, key=lambda cell: cell.visible_index)
    cell_indexes = [cell.visible_index for cell in ordered_cells]
    geometry_indexes = [(cell.visible_row, cell.column) for cell in ordered_cells]
    if (
        len(set(cell_indexes)) != len(cell_indexes)
        or cell_indexes != list(range(len(ordered_cells)))
        or len(set(geometry_indexes)) != len(geometry_indexes)
        or geometry_indexes != sorted(geometry_indexes)
    ):
        return (), "当前可见格索引与行列几何顺序不一致，不能建立数量锚点"

    by_index: dict[int, StorageBagQuantityObservation] = {}
    for observation in observations:
        if observation.visible_index in by_index:
            return (), "同一可见格出现重复数量观测，不能把它们当成独立锚点"
        if observation.visible_index not in cell_indexes:
            return (), f"数量观测格 {observation.visible_index} 不属于当前完整可点击视窗"
        by_index[observation.visible_index] = observation
    return tuple(by_index[index] for index in sorted(by_index)), None


def plan_storage_bag_item_click(
    snapshot: Mapping[str, Any],
    *,
    target_base_id: int | None = None,
    target_instance_id: str | int | None = None,
    cells: Sequence[StorageBagVisibleCell],
    observations: Sequence[StorageBagQuantityObservation],
    minimum_observations: int = 2,
) -> StorageBagItemClickPlan:
    """Resolve one requested Runtime item to a current, safe #525 click point.

    ``snapshot`` is the ordered Runtime baseline and may be reused after each
    drag.  ``cells`` and ``observations`` must always come from the *current*
    post-drag frame; this function establishes the new visible offset rather
    than assuming a scroll distance.
    """

    runtime_items = _runtime_items(snapshot)
    if runtime_items is None:
        return StorageBagItemClickPlan("invalid_runtime_snapshot", "ItemInfoList Runtime 快照不完整")
    if target_base_id is None and target_instance_id is None:
        raise ValueError("target_base_id/target_instance_id 至少提供一个")
    target_instance = str(target_instance_id) if target_instance_id is not None else None
    targets = [
        (index, item)
        for index, item in enumerate(runtime_items)
        if (target_base_id is None or item["base_id"] == int(target_base_id))
        and (target_instance is None or str(item.get("instance_id")) == target_instance)
    ]
    if len(targets) != 1:
        return StorageBagItemClickPlan(
            "target_not_found",
            "目标 Runtime 物品不存在或不是唯一实例",
        )
    ordered_observations, geometry_error = _geometry_ordered_observations(cells, observations)
    if geometry_error is not None:
        return StorageBagItemClickPlan(
            "insufficient_observations",
            geometry_error,
            observations=tuple(observations),
        )
    required_observation_count = max(2, minimum_observations)
    if len(ordered_observations) < required_observation_count:
        return StorageBagItemClickPlan(
            "insufficient_observations",
            (
                f"仅有 {len(ordered_observations)} 个可用数量观测，"
                f"至少需要 {required_observation_count} 个"
            ),
            observations=ordered_observations,
        )
    if len(ordered_observations) == 2:
        exact_starts = _exact_matching_starts(runtime_items, ordered_observations)
        if len(exact_starts) != 1:
            return StorageBagItemClickPlan(
                "ambiguous_offset",
                (
                    "两个几何有效数量锚点必须全部精确命中且在完整 Runtime 序列中"
                    f"形成唯一起点；当前精确候选 {len(exact_starts)} 个，拒绝猜测"
                ),
                candidate_starts=exact_starts,
                observations=ordered_observations,
            )
        best_score = 2
        best_starts = exact_starts
        second_score = 0
    else:
        required_matches = max(3, minimum_observations)
        ranked = _ranked_matching_starts(runtime_items, ordered_observations)
        best_score = ranked[0][1] if ranked else 0
        best_starts = tuple(start for start, score in ranked if score == best_score)
        second_score = next((score for _start, score in ranked if score < best_score), 0)
        if best_score < required_matches:
            return StorageBagItemClickPlan(
                "ambiguous_offset",
                (
                    f"数量序列最佳匹配 {best_score}/{len(ordered_observations)}，"
                    f"至少需要 {required_matches} 个匹配，拒绝猜测"
                ),
                candidate_starts=best_starts,
                observations=ordered_observations,
            )
    if (
        len(best_starts) != 1
        or best_score - second_score < 1
    ):
        return StorageBagItemClickPlan(
            "ambiguous_offset",
            (
                f"数量序列最佳匹配 {best_score}/{len(ordered_observations)}，"
                f"次佳 {second_score}/{len(ordered_observations)}，"
                f"并列最佳起点 {len(best_starts)} 个，拒绝猜测"
            ),
            candidate_starts=best_starts,
            observations=ordered_observations,
        )
    start = best_starts[0]
    runtime_index, item = targets[0]
    visible_index = runtime_index - start
    cell_by_index = {cell.visible_index: cell for cell in cells}
    cell = cell_by_index.get(visible_index)
    if cell is None:
        return StorageBagItemClickPlan(
            "target_not_visible",
            "目标已在 Runtime 中定位，但不在当前完整可点击视窗",
            runtime_index=runtime_index,
            runtime_item=item,
            viewport_runtime_start=start,
            candidate_starts=best_starts,
            observations=ordered_observations,
        )
    return StorageBagItemClickPlan(
        "ready",
        (
            f"Runtime 顺序与数量序列以 {best_score}/{len(ordered_observations)} "
            "唯一对齐，允许点击当前格中心"
        ),
        runtime_index=runtime_index,
        runtime_item=item,
        point=cell.point,
        viewport_runtime_start=start,
        candidate_starts=best_starts,
        observations=ordered_observations,
    )


__all__ = [
    "StorageBagItemClickPlan",
    "StorageBagDetailVerification",
    "StorageBagScrollDirective",
    "StorageBagTarget",
    "StorageBagQuantityObservation",
    "StorageBagVisibleCell",
    "plan_storage_bag_item_click",
    "plan_storage_bag_scroll",
    "prepare_storage_bag_target",
    "prepare_storage_bag_target_by_name",
    "quantity_observations_from_ocr",
    "register_storage_bag_viewport_from_quantity_ocr",
    "verify_storage_bag_item_detail",
    "visible_storage_bag_cells",
]
