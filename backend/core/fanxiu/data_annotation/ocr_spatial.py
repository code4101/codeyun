from __future__ import annotations

import math
from typing import Any, Iterable


MIN_TOKEN_SHAPE_OVERLAP_RATIO = 0.30


def _box(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    x = float(item.get("x") or 0)
    y = float(item.get("y") or 0)
    w = float(item.get("w") or 0)
    h = float(item.get("h") or 0)
    if w <= 0 or h <= 0:
        return None
    return x, y, x + w, y + h


def _intersection(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _overlap_ratio(
    token_box: tuple[float, float, float, float],
    query_box: tuple[float, float, float, float],
) -> float:
    overlap = _intersection(token_box, query_box)
    if overlap is None:
        return 0.0
    token_area = max(1.0, (token_box[2] - token_box[0]) * (token_box[3] - token_box[1]))
    return ((overlap[2] - overlap[0]) * (overlap[3] - overlap[1])) / token_area


def _union_boxes(items: Iterable[dict[str, Any]]) -> dict[str, float] | None:
    boxes = [_box(item) for item in items]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    bottom = max(box[3] for box in boxes)
    return {"x": left, "y": top, "w": max(1.0, right - left), "h": max(1.0, bottom - top)}


def _group_token_rows(tokens: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group character tokens into visual rows using geometry only."""

    candidates = [token for token in tokens if isinstance(token, dict) and _box(token) is not None]
    candidates.sort(key=lambda item: (float(item.get("y") or 0) + float(item.get("h") or 0) / 2, float(item.get("x") or 0)))
    rows: list[list[dict[str, Any]]] = []
    for token in candidates:
        center_y = float(token.get("y") or 0) + float(token.get("h") or 0) / 2
        height = max(1.0, float(token.get("h") or 0))
        best_row: list[dict[str, Any]] | None = None
        best_distance = float("inf")
        for row in rows:
            row_center_y = sum(float(item.get("y") or 0) + float(item.get("h") or 0) / 2 for item in row) / len(row)
            row_height = max(float(item.get("h") or 1) for item in row)
            distance = abs(center_y - row_center_y)
            if distance <= max(height, row_height) * 0.5 and distance < best_distance:
                best_row = row
                best_distance = distance
        if best_row is None:
            rows.append([token])
        else:
            best_row.append(token)
    for row in rows:
        row.sort(key=lambda item: float(item.get("x") or 0))
    rows.sort(
        key=lambda row: (
            min(float(item.get("y") or 0) for item in row),
            min(float(item.get("x") or 0) for item in row),
        )
    )
    return rows


def order_ocr_tokens(tokens: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [token for token in tokens if isinstance(token, dict) and _box(token) is not None]
    if candidates and all(token.get("parent_line_id") is not None for token in candidates):
        return sorted(
            candidates,
            key=lambda item: (
                int(item.get("line_order") or 0),
                int(item.get("order") or 0),
            ),
        )
    return [token for row in _group_token_rows(candidates) for token in row]


def group_ocr_tokens(tokens: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group only tokens that explicitly share a Paddle parent line.

    Unlinked legacy tokens stay separate.  This is intentionally conservative:
    geometry cannot recreate Paddle's detector grouping and must not pretend to.
    ROI callers should use :func:`query_spatial_ocr`, where local aggregation is
    an explicit business operation.
    """

    fragments: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    group_order: list[str] = []
    for fallback_index, token in enumerate(order_ocr_tokens(tokens)):
        parent_id = token.get("parent_line_id")
        key = str(parent_id) if parent_id is not None else f"legacy-token-{fallback_index}"
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append(token)
    for key in group_order:
        members = groups[key]
        box = _union_boxes(members)
        if box is None:
            continue
        linked = not key.startswith("legacy-token-")
        fragments.append({
            "text": "".join(str(item.get("text") or "") for item in members),
            **box,
            "source": "linked_tokens" if linked else "unlinked_token_fallback",
            "parent_line_id": key if linked else None,
            "fallback": not linked,
        })
    return fragments


def query_ocr_lines(lines: list[dict[str, Any]], query: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter authoritative Paddle lines geometrically without rebuilding them."""

    query_box = _box(query)
    if query_box is None:
        return []
    return [
        line for line in lines
        if isinstance(line, dict)
        and (line_box := _box(line)) is not None
        and _overlap_ratio(line_box, query_box) >= MIN_TOKEN_SHAPE_OVERLAP_RATIO
    ]


def query_spatial_ocr(tokens: list[dict[str, Any]], query: dict[str, Any]) -> dict[str, Any]:
    """Select tokens by shape first, then derive text fragments."""

    query_box = _box(query)
    if query_box is None:
        return {"text": "", "fragments": [], "tokens": []}
    selected = [
        token
        for token in tokens
        if isinstance(token, dict)
        and (token_box := _box(token)) is not None
        and _overlap_ratio(token_box, query_box) >= MIN_TOKEN_SHAPE_OVERLAP_RATIO
    ]
    ordered = order_ocr_tokens(selected)
    if ordered and all(token.get("parent_line_id") is not None for token in ordered):
        fragments = group_ocr_tokens(ordered)
    else:
        # This aggregation is deliberately confined to an explicit local ROI.
        fragments = []
        for row in _group_token_rows(ordered):
            box = _union_boxes(row)
            if box is not None:
                fragments.append({
                    "text": "".join(str(item.get("text") or "") for item in row),
                    **box,
                    "source": "roi_tokens_unlinked",
                    "fallback": True,
                })
    return {
        "text": "".join(str(fragment.get("text") or "") for fragment in fragments),
        "fragments": fragments,
        "tokens": ordered,
    }


def locate_text_box(tokens: list[dict[str, Any]], target: str) -> dict[str, float] | None:
    """Locate a substring from ordered OCR tokens using real token boxes."""

    target = str(target or "")
    ordered = order_ocr_tokens(tokens)
    if not target:
        return None
    groups: list[list[dict[str, Any]]] = []
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for token in ordered:
        parent_id = token.get("parent_line_id")
        if parent_id is None:
            if not groups or groups[-1] is not by_parent.get("__legacy__"):
                legacy = by_parent.setdefault("__legacy__", [])
                groups.append(legacy)
            by_parent["__legacy__"].append(token)
        else:
            key = str(parent_id)
            if key not in by_parent:
                by_parent[key] = []
                groups.append(by_parent[key])
            by_parent[key].append(token)
    for group in groups:
        result = _locate_text_box_in_ordered_tokens(group, target)
        if result is not None:
            return result
    return None


def _locate_text_box_in_ordered_tokens(
    ordered: list[dict[str, Any]], target: str,
) -> dict[str, float] | None:
    text = "".join(str(token.get("text") or "") for token in ordered)
    start = text.find(target)
    if start < 0:
        return None
    end = start + len(target)
    cursor = 0
    pieces: list[dict[str, float]] = []
    for token in ordered:
        token_text = str(token.get("text") or "")
        token_start, token_end = cursor, cursor + len(token_text)
        cursor = token_end
        overlap_start = max(start, token_start)
        overlap_end = min(end, token_end)
        if overlap_end <= overlap_start:
            continue
        token_box = _box(token)
        if token_box is None:
            continue
        left, top, right, bottom = token_box
        if overlap_start > token_start or overlap_end < token_end:
            width = max(1.0, right - left)
            length = max(1, len(token_text))
            left, right = (
                left + width * ((overlap_start - token_start) / length),
                left + width * ((overlap_end - token_start) / length),
            )
        pieces.append({"x": left, "y": top, "w": max(1.0, right - left), "h": max(1.0, bottom - top)})
    return _union_boxes(pieces)


def union_fragment_box(fragments: list[dict[str, Any]]) -> dict[str, float] | None:
    box = _union_boxes(fragment for fragment in fragments if isinstance(fragment, dict))
    if box is None or not all(math.isfinite(value) for value in box.values()):
        return None
    return box
