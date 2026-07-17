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
    def parallel(texts: Any, boxes: Any, line_index: int | None):
        text_items = list(texts) if isinstance(texts, (list, tuple, str)) else []
        box_items = list(boxes) if isinstance(boxes, (list, tuple)) else []
        for index, text in enumerate(text_items[: len(box_items)]):
            clean = _clean_text(text)
            if clean:
                yield clean, box_items[index], line_index

    # PaddleOCR/PaddleX 3.x canonical output.  For Chinese, ``text_word`` is
    # character-granular even though the public option is named word boxes.
    texts = payload.get("text_word")
    boxes = payload.get("text_word_boxes")
    if isinstance(texts, (list, tuple)) and isinstance(boxes, (list, tuple)):
        for line_index, (line_texts, line_boxes) in enumerate(zip(texts, boxes)):
            yield from parallel(line_texts, line_boxes, line_index)

    # Older PaddleOCR and adapter payloads retained for input compatibility.
    for key in ("rec_word_infos", "rec_word_info", "word_infos", "words"):
        raw_infos = payload.get(key)
        if not isinstance(raw_infos, (list, tuple)):
            continue
        for line_index, info in enumerate(raw_infos):
            if isinstance(info, dict):
                info_texts = (
                    info.get("texts")
                    or info.get("words")
                    or info.get("chars")
                    or info.get("word_texts")
                    or info.get("char_texts")
                )
                info_boxes = (
                    info.get("boxes")
                    or info.get("word_boxes")
                    or info.get("char_boxes")
                    or info.get("polys")
                    or info.get("points")
                )
                yield from parallel(info_texts, info_boxes, line_index)
            elif isinstance(info, (list, tuple)) and len(info) >= 2:
                yield from parallel(info[0], info[1], line_index)

    for text_key, box_key in (
        ("rec_words", "rec_word_boxes"),
        ("rec_word_texts", "rec_word_boxes"),
        ("word_texts", "word_boxes"),
        ("char_texts", "char_boxes"),
    ):
        raw_texts = payload.get(text_key)
        raw_boxes = payload.get(box_key)
        if not isinstance(raw_texts, (list, tuple)) or not isinstance(raw_boxes, (list, tuple)):
            continue
        nested = any(isinstance(item, (list, tuple)) and not isinstance(item, str) for item in raw_texts)
        if nested:
            for line_index, (line_texts, line_boxes) in enumerate(zip(raw_texts, raw_boxes)):
                yield from parallel(line_texts, line_boxes, line_index)
        else:
            yield from parallel(raw_texts, raw_boxes, None)


def extract_ocr_tokens(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the finest available OCR tokens with stable spatial metadata."""

    tokens: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for text, raw_box, line_index in _iter_token_candidates(payload):
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
            "line_index": line_index,
        }
        key = (text, line_index, round(token["x"], 3), round(token["y"], 3), round(token["w"], 3), round(token["h"], 3))
        if key in seen:
            continue
        seen.add(key)
        tokens.append(token)
    tokens.sort(
        key=lambda item: (
            item["line_index"] if item["line_index"] is not None else 10**9,
            float(item["y"]),
            float(item["x"]),
        )
    )
    return tokens
