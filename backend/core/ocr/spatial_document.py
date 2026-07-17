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


def _iter_token_candidates(payload: dict[str, Any]):
    def parallel(texts: Any, boxes: Any):
        text_items = list(texts) if isinstance(texts, (list, tuple, str)) else []
        box_items = list(boxes) if isinstance(boxes, (list, tuple)) else []
        for index, text in enumerate(text_items[: len(box_items)]):
            clean = _clean_text(text)
            if clean:
                yield clean, box_items[index]

    # PaddleOCR/PaddleX 3.x canonical output.  For Chinese, ``text_word`` is
    # character-granular even though Paddle's public option is named word box.
    texts = payload.get("text_word")
    boxes = payload.get("text_word_boxes")
    if isinstance(texts, (list, tuple)) and isinstance(boxes, (list, tuple)):
        for line_texts, line_boxes in zip(texts, boxes):
            yield from parallel(line_texts, line_boxes)


def extract_ocr_tokens(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return character-level OCR tokens without detector-line metadata."""

    tokens: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for text, raw_box in _iter_token_candidates(payload):
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
        }
        key = (text, round(token["x"], 3), round(token["y"], 3), round(token["w"], 3), round(token["h"], 3))
        if key in seen:
            continue
        seen.add(key)
        tokens.append(token)
    tokens.sort(
        key=lambda item: (
            float(item["y"]),
            float(item["x"]),
        )
    )
    return tokens
