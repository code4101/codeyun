from __future__ import annotations

from typing import Any


def _coerce_float(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if result == result else fallback


def ocr_box_to_xywh(value: Any) -> tuple[float, float, float, float] | None:
    """Normalize PaddleOCR/PaddleX rectangle or polygon boxes to ``x/y/w/h``."""

    if isinstance(value, dict):
        if {"x", "y", "w", "h"} <= set(value):
            x = _coerce_float(value.get("x"))
            y = _coerce_float(value.get("y"))
            w = _coerce_float(value.get("w"))
            h = _coerce_float(value.get("h"))
            return (x, y, w, h) if w > 0 and h > 0 else None
        nested = value.get("points") or value.get("poly") or value.get("polygon") or value.get("box")
        return ocr_box_to_xywh(nested)
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if len(value) >= 4 and all(not isinstance(item, (list, tuple, dict)) for item in value[:4]):
        x1, y1, x2, y2 = (_coerce_float(item) for item in value[:4])
        return min(x1, x2), min(y1, y2), max(1.0, abs(x2 - x1)), max(1.0, abs(y2 - y1))
    points = [
        (_coerce_float(item[0]), _coerce_float(item[1]))
        for item in value
        if isinstance(item, (list, tuple)) and len(item) >= 2
    ]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(1.0, max(xs) - min(xs)), max(1.0, max(ys) - min(ys))


def _clean_text(value: Any) -> str:
    return "".join(str(value or "").split())


def _geometry_reading_order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order OCR boxes by visual rows, then left-to-right within each row."""

    candidates = [item for item in items if _coerce_float(item.get("w")) > 0 and _coerce_float(item.get("h")) > 0]
    candidates.sort(
        key=lambda item: (
            _coerce_float(item.get("y")) + _coerce_float(item.get("h")) / 2,
            _coerce_float(item.get("x")),
        )
    )
    rows: list[list[dict[str, Any]]] = []
    for item in candidates:
        center_y = _coerce_float(item.get("y")) + _coerce_float(item.get("h")) / 2
        height = max(1.0, _coerce_float(item.get("h"), 1.0))
        best_row: list[dict[str, Any]] | None = None
        best_distance = float("inf")
        for row in rows:
            row_center_y = sum(
                _coerce_float(member.get("y")) + _coerce_float(member.get("h")) / 2
                for member in row
            ) / len(row)
            row_height = max(_coerce_float(member.get("h"), 1.0) for member in row)
            distance = abs(center_y - row_center_y)
            if distance <= max(height, row_height) * 0.5 and distance < best_distance:
                best_row = row
                best_distance = distance
        if best_row is None:
            rows.append([item])
        else:
            best_row.append(item)
    for row in rows:
        row.sort(key=lambda item: (_coerce_float(item.get("x")), _coerce_float(item.get("y"))))
    rows.sort(
        key=lambda row: (
            min(_coerce_float(item.get("y")) for item in row),
            min(_coerce_float(item.get("x")) for item in row),
        )
    )
    return [item for row in rows for item in row]


def extract_ocr_lines(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract Paddle text boxes in geometric reading order."""

    texts = payload.get("rec_texts") or []
    scores = payload.get("rec_scores") or []
    boxes = payload.get("rec_boxes") or payload.get("rec_polys") or payload.get("dt_polys") or []
    lines: list[dict[str, Any]] = []
    for index, raw_box in enumerate(boxes):
        box = ocr_box_to_xywh(raw_box)
        if box is None:
            continue
        text = str(texts[index] if index < len(texts) else "")
        if not text.strip():
            continue
        x, y, w, h = box
        line: dict[str, Any] = {
            "line_id": f"line-{index}",
            "order": index,
            "text": text,
            "x": max(0.0, x),
            "y": max(0.0, y),
            "w": max(1.0, w),
            "h": max(1.0, h),
            "source": "paddle",
        }
        if index < len(scores):
            line["score"] = _coerce_float(scores[index])
        lines.append(line)
    lines = _geometry_reading_order(lines)
    for order, line in enumerate(lines):
        line["order"] = order
    return lines


def _iter_token_candidates(payload: dict[str, Any]):
    def parallel(texts: Any, boxes: Any):
        text_items = list(texts) if isinstance(texts, (list, tuple, str)) else []
        box_items = list(boxes) if isinstance(boxes, (list, tuple)) else []
        for index, text in enumerate(text_items[: len(box_items)]):
            clean = _clean_text(text)
            if clean:
                yield index, clean, box_items[index]

    # PaddleOCR/PaddleX 3.x canonical output.  For Chinese, ``text_word`` is
    # character-granular even though Paddle's public option is named word box.
    texts = payload.get("text_word")
    boxes = payload.get("text_word_boxes")
    if isinstance(texts, (list, tuple)) and isinstance(boxes, (list, tuple)):
        for line_index, (line_texts, line_boxes) in enumerate(zip(texts, boxes)):
            for token_index, text, box in parallel(line_texts, line_boxes):
                yield line_index, token_index, text, box


def extract_ocr_tokens(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return word/character boxes linked to their native Paddle text box."""

    tokens: list[dict[str, Any]] = []
    for line_index, token_index, text, raw_box in _iter_token_candidates(payload):
        box = ocr_box_to_xywh(raw_box)
        if box is None:
            continue
        x, y, w, h = box
        token = {
            "text": text,
            "x": max(0.0, x),
            "y": max(0.0, y),
            "w": max(1.0, w),
            "h": max(1.0, h),
            "parent_line_id": f"line-{line_index}",
            "line_order": line_index,
            "order": token_index,
        }
        tokens.append(token)
    return tokens


def extract_ocr_spatial_document(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return both authoritative first-level lines and linked second-level tokens."""

    lines = extract_ocr_lines(payload)
    tokens = extract_ocr_tokens(payload)
    line_order = {str(line.get("line_id")): int(line.get("order") or 0) for line in lines}
    for token in tokens:
        token["line_order"] = line_order.get(
            str(token.get("parent_line_id")),
            int(token.get("line_order") or 0),
        )
    tokens.sort(key=lambda token: (int(token.get("line_order") or 0), int(token.get("order") or 0)))
    return {
        "lines": lines,
        "tokens": tokens,
    }
