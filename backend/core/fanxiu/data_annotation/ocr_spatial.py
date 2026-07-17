from __future__ import annotations

import math
from typing import Any, Iterable


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
    item_box: tuple[float, float, float, float],
    query_box: tuple[float, float, float, float],
) -> float:
    overlap = _intersection(item_box, query_box)
    if overlap is None:
        return 0.0
    area = max(1.0, (item_box[2] - item_box[0]) * (item_box[3] - item_box[1]))
    return ((overlap[2] - overlap[0]) * (overlap[3] - overlap[1])) / area


def _center_in_box(
    item_box: tuple[float, float, float, float],
    query_box: tuple[float, float, float, float],
) -> bool:
    center_x = (item_box[0] + item_box[2]) / 2
    center_y = (item_box[1] + item_box[3]) / 2
    return query_box[0] <= center_x <= query_box[2] and query_box[1] <= center_y <= query_box[3]


def _line_sort_key(item: dict[str, Any]) -> tuple[float, float]:
    return float(item.get("y") or 0), float(item.get("x") or 0)


def _word_belongs_to_line(word: dict[str, Any], line: dict[str, Any], line_index: int) -> bool:
    word_box = _box(word)
    line_box = _box(line)
    if word_box is None or line_box is None:
        return False
    # Paddle's token ``line_index`` addresses the raw detector region.  The
    # normalized line layer may merge adjacent regions (a frequent layout in
    # game UIs), so those indexes are not comparable.  Geometry remains stable
    # across both representations and is therefore the source of truth.
    return _center_in_box(word_box, line_box) or _overlap_ratio(word_box, line_box) >= 0.5


def _clip_uniform_text(
    text: str,
    line_box: tuple[float, float, float, float],
    query_box: tuple[float, float, float, float],
) -> str:
    """Estimate visible characters when Paddle only returned a line-level box.

    Game UI text is predominantly horizontal and close to fixed-width. Character
    centers are used instead of slicing by raw overlap length so a half-width
    shape does not accidentally include the first character outside its box.
    """
    if not text:
        return ""
    overlap = _intersection(line_box, query_box)
    if overlap is None:
        return ""
    width = max(1.0, line_box[2] - line_box[0])
    char_width = width / len(text)
    selected: list[str] = []
    for index, char in enumerate(text):
        center_x = line_box[0] + (index + 0.5) * char_width
        if overlap[0] <= center_x <= overlap[2]:
            selected.append(char)
    return "".join(selected)


def _selected_words_text(
    words: Iterable[dict[str, Any]],
    query_box: tuple[float, float, float, float],
) -> tuple[str, list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    for word in words:
        word_box = _box(word)
        if word_box is None:
            continue
        if _center_in_box(word_box, query_box) or _overlap_ratio(word_box, query_box) >= 0.5:
            selected.append(word)
    selected.sort(key=_line_sort_key)
    return "".join(str(word.get("text") or "") for word in selected), selected


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


def query_spatial_ocr(
    lines: list[dict[str, Any]],
    words: list[dict[str, Any]],
    query: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild the OCR text covered by a shape from one full-frame OCR pass."""
    query_box = _box(query)
    if query_box is None:
        return {"text": "", "fragments": [], "ambiguous": False}

    fragments: list[dict[str, Any]] = []
    selected_tokens: list[dict[str, Any]] = []
    ambiguous = False
    indexed_lines = [(index, line) for index, line in enumerate(lines) if isinstance(line, dict)]
    indexed_lines.sort(key=lambda pair: _line_sort_key(pair[1]))
    for original_index, line in indexed_lines:
        text = str(line.get("text") or "")
        line_box = _box(line)
        if not text or line_box is None:
            continue
        overlap = _intersection(line_box, query_box)
        if overlap is None:
            continue
        line_height = max(1.0, line_box[3] - line_box[1])
        vertical_ratio = (overlap[3] - overlap[1]) / line_height
        if vertical_ratio < 0.35:
            continue

        horizontal_ratio = (overlap[2] - overlap[0]) / max(1.0, line_box[2] - line_box[0])
        line_words = [
            word for word in words
            if isinstance(word, dict) and _word_belongs_to_line(word, line, original_index)
        ]
        word_text, selected_words = _selected_words_text(line_words, query_box)
        selected_text = word_text
        source = "words"
        fragment_box = _union_boxes(selected_words)
        if not selected_text:
            selected_text = text
            source = "line"
            fragment_box = None
            if horizontal_ratio < 0.96:
                selected_text = _clip_uniform_text(text, line_box, query_box)
                source = "estimated_characters"
                ambiguous = True
        if not selected_text:
            continue
        selected_tokens.extend(selected_words)
        if fragment_box is None:
            fragment_box = {
                "x": max(line_box[0], query_box[0]),
                "y": max(line_box[1], query_box[1]),
                "w": max(1.0, min(line_box[2], query_box[2]) - max(line_box[0], query_box[0])),
                "h": max(1.0, min(line_box[3], query_box[3]) - max(line_box[1], query_box[1])),
            }
        fragments.append(
            {
                "text": selected_text,
                **fragment_box,
                "source": source,
            }
        )

    fragments.sort(key=_line_sort_key)
    return {
        "text": "".join(str(fragment.get("text") or "") for fragment in fragments),
        "fragments": fragments,
        "tokens": selected_tokens,
        "ambiguous": ambiguous,
    }


def locate_text_box(tokens: list[dict[str, Any]], target: str) -> dict[str, float] | None:
    """Locate a substring from ordered OCR tokens without falling back to a line center."""

    target = str(target or "")
    ordered = [token for token in tokens if isinstance(token, dict) and str(token.get("text") or "")]
    text = "".join(str(token.get("text") or "") for token in ordered)
    start = text.find(target)
    if start < 0 or not target:
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
            piece_left = left + width * ((overlap_start - token_start) / length)
            piece_right = left + width * ((overlap_end - token_start) / length)
            left, right = piece_left, piece_right
        pieces.append({"x": left, "y": top, "w": max(1.0, right - left), "h": max(1.0, bottom - top)})
    return _union_boxes(pieces)


def union_fragment_box(fragments: list[dict[str, Any]]) -> dict[str, float] | None:
    box = _union_boxes(fragment for fragment in fragments if isinstance(fragment, dict))
    if box is None or not all(math.isfinite(value) for value in box.values()):
        return None
    return box
