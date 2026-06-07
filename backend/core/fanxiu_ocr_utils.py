from __future__ import annotations

import json
import re
from typing import Any


def _sanitize_ocr_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _extract_shape_text(shape: dict[str, Any]) -> str:
    raw_label = shape.get("label")
    if isinstance(raw_label, str):
        try:
            payload = json.loads(raw_label)
        except json.JSONDecodeError:
            return _sanitize_ocr_text(raw_label)
        if isinstance(payload, dict):
            return _sanitize_ocr_text(payload.get("text"))
    return ""


def _extract_shape_rectangle(points: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(points, list) or len(points) < 2:
        return None

    flattened: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        try:
            flattened.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            return None

    xs = [item[0] for item in flattened]
    ys = [item[1] for item in flattened]
    return min(xs), min(ys), max(xs), max(ys)


def _extract_ocr_line_entries(preview_document: dict[str, Any]) -> list[list[dict[str, Any]]]:
    raw_shapes = preview_document.get("shapes") or []
    if not isinstance(raw_shapes, list):
        return []

    entries: list[dict[str, Any]] = []
    for shape in raw_shapes:
        if not isinstance(shape, dict):
            continue
        text = _extract_shape_text(shape)
        if not text:
            continue
        rectangle = _extract_shape_rectangle(shape.get("points"))
        if rectangle is None:
            continue
        x1, y1, x2, y2 = rectangle
        entries.append(
            {
                "text": text,
                "x": x1,
                "x2": x2,
                "width": max(x2 - x1, 1),
                "y": (y1 + y2) / 2,
                "height": max(y2 - y1, 1),
            }
        )

    if not entries:
        return []

    entries.sort(key=lambda item: (item["y"], item["x"]))
    heights = sorted(entry["height"] for entry in entries)
    median_height = heights[len(heights) // 2]
    tolerance = max(12.0, median_height * 0.75)

    grouped: list[list[dict[str, Any]]] = []
    current_group: list[dict[str, Any]] = []
    current_y = 0.0
    for entry in entries:
        if not current_group:
            current_group = [entry]
            current_y = entry["y"]
            continue

        if abs(entry["y"] - current_y) <= tolerance:
            current_group.append(entry)
            current_y = sum(item["y"] for item in current_group) / len(current_group)
            continue

        grouped.append(sorted(current_group, key=lambda item: item["x"]))
        current_group = [entry]
        current_y = entry["y"]

    if current_group:
        grouped.append(sorted(current_group, key=lambda item: item["x"]))

    return [[item for item in group if str(item["text"]).strip()] for group in grouped]


def _extract_magic_treasure_ocr_line_entries(preview_document: dict[str, Any]) -> list[list[dict[str, Any]]]:
    return _extract_ocr_line_entries(preview_document)


def _join_ocr_line_entries(entries: list[dict[str, Any]]) -> str:
    return "".join(_sanitize_ocr_text(item.get("text")) for item in entries if _sanitize_ocr_text(item.get("text")))


def _extract_magic_treasure_ocr_lines(preview_document: dict[str, Any]) -> list[list[str]]:
    grouped_entries = _extract_magic_treasure_ocr_line_entries(preview_document)
    return [[str(item["text"]) for item in group if str(item["text"]).strip()] for group in grouped_entries]
