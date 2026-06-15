from __future__ import annotations

import re
from typing import Any

from backend.core.fanxiu.game.ocr_utils import _extract_ocr_line_entries, _sanitize_ocr_text


def _normalize_formation_requirement_text(text: str) -> str:
    normalized = _sanitize_ocr_text(text)
    return re.sub(r"\s*[（(]?\s*\d+\s*/\s*\d+\s*[)）]?\s*$", "", normalized).strip()


def _normalize_formation_effect_text(text: str) -> str:
    normalized = _sanitize_ocr_text(text)
    if not normalized:
        return ""
    normalized = re.sub(r"[［\[]", "【", normalized)
    normalized = re.sub(r"[］\]]", "】", normalized)
    normalized = re.sub(r"\s*[:：]\s*", "", normalized)
    normalized = re.sub(r"【\s*", "【", normalized)
    normalized = re.sub(r"\s*】", "】", normalized)
    return normalized.strip()


def _normalize_formation_effect_name(text: str) -> str:
    normalized = _sanitize_ocr_text(text)
    normalized = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9【】\[\]（）()]+", "", normalized)
    normalized = re.sub(r"\s*[:：]\s*", "", normalized)
    return normalized.strip()


def _normalize_formation_effect_detail(text: str) -> str:
    normalized = _sanitize_ocr_text(text)
    normalized = re.sub(r"\s*[（(]?\s*\d+\s*/\s*\d+\s*[)）]?\s*$", "", normalized).strip()
    return normalized


def _is_formation_requirement_condition(text: str) -> bool:
    return bool(re.match(r"^(入阵|上阵|点亮|阵法神通达到)", _normalize_formation_requirement_text(text)))


def _looks_like_formation_effect_line(text: str) -> bool:
    normalized = _normalize_formation_effect_text(text)
    return normalized.startswith("【")


def _merge_formation_effect_text(left: str, right: str) -> str:
    parts: list[str] = []
    for chunk in [left, right]:
        for item in re.split(r"[；;]+", _sanitize_ocr_text(chunk)):
            normalized = _normalize_formation_effect_text(item)
            if normalized and normalized not in parts:
                parts.append(normalized)
    return "；".join(parts)


def _match_formation_effect_detail_heading(text: str, heading: str) -> tuple[bool, str]:
    normalized = _normalize_formation_effect_name(text)
    match = re.match(rf"^{heading}[：:]?(.*)$", normalized)
    if not match:
        return False, ""
    return True, _normalize_formation_effect_name(match.group(1))


def _build_formation_requirements_from_ocr_document(
    preview_document: dict[str, Any],
) -> tuple[list[dict[str, str]], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [
        "".join(_sanitize_ocr_text(item.get("text")) for item in group if _sanitize_ocr_text(item.get("text")))
        for group in line_entries
    ]
    normalized_lines = [line for line in lines if line]
    if not normalized_lines:
        raise ValueError("未能从截图中识别触发条件")

    raw_items: list[dict[str, str]] = []
    pending_effect_lines: list[str] = []
    current_condition_lines: list[str] = []

    def flush_condition_lines() -> None:
        nonlocal current_condition_lines, pending_effect_lines
        if not current_condition_lines:
            return
        normalized_condition = _normalize_formation_requirement_text("".join(current_condition_lines))
        if normalized_condition:
            raw_items.append(
                {
                    "text": normalized_condition,
                    "effect_text": "；".join(pending_effect_lines),
                }
            )
        current_condition_lines = []
        pending_effect_lines = []

    for line in normalized_lines:
        if _looks_like_formation_effect_line(line):
            flush_condition_lines()
            normalized_effect = _normalize_formation_effect_text(line)
            if normalized_effect and (not pending_effect_lines or pending_effect_lines[-1] != normalized_effect):
                pending_effect_lines.append(normalized_effect)
            continue

        if _is_formation_requirement_condition(line):
            flush_condition_lines()
            current_condition_lines = [line]
            continue

        if current_condition_lines:
            current_condition_lines.append(line)
            continue

        normalized_effect = _normalize_formation_effect_text(line)
        if normalized_effect and (not pending_effect_lines or pending_effect_lines[-1] != normalized_effect):
            pending_effect_lines.append(normalized_effect)

    flush_condition_lines()

    if not raw_items:
        raise ValueError("未能从截图中识别触发条件")

    merged: list[dict[str, str]] = []
    merged_by_text: dict[str, dict[str, str]] = {}
    for item in raw_items:
        key = _sanitize_ocr_text(item.get("text"))
        if not key:
            continue
        existing = merged_by_text.get(key)
        if existing is None:
            payload = {
                "text": key,
                "effect_text": _normalize_formation_effect_text(item.get("effect_text", "")),
            }
            merged_by_text[key] = payload
            merged.append(payload)
            continue
        existing["effect_text"] = _merge_formation_effect_text(existing.get("effect_text", ""), item.get("effect_text", ""))

    if not merged:
        raise ValueError("未能从截图中识别触发条件")
    return merged, normalized_lines


def _build_formation_effect_details_from_ocr_document(
    preview_document: dict[str, Any],
) -> tuple[list[dict[str, str]], list[str]]:
    line_entries = _extract_ocr_line_entries(preview_document)
    lines = [
        "".join(_sanitize_ocr_text(item.get("text")) for item in group if _sanitize_ocr_text(item.get("text")))
        for group in line_entries
    ]
    normalized_lines = [line for line in lines if line]
    if not normalized_lines:
        raise ValueError("未能从截图中识别词缀效果")

    raw_items: list[dict[str, str]] = []
    current_name = ""
    current_detail_lines: list[str] = []
    waiting_name = False
    collecting_detail = False

    def flush_current() -> None:
        nonlocal current_name, current_detail_lines, waiting_name, collecting_detail
        effect_name = _normalize_formation_effect_name(current_name)
        effect_detail = _normalize_formation_effect_detail("".join(current_detail_lines))
        if effect_name and effect_detail:
            raw_items.append(
                {
                    "effect_name": effect_name,
                    "effect_detail": effect_detail,
                }
            )
        current_name = ""
        current_detail_lines = []
        waiting_name = False
        collecting_detail = False

    for line in normalized_lines:
        is_name_heading, name_remainder = _match_formation_effect_detail_heading(line, "名字")
        if is_name_heading:
            flush_current()
            current_name = name_remainder
            waiting_name = not bool(name_remainder)
            collecting_detail = False
            continue

        is_effect_heading, effect_remainder = _match_formation_effect_detail_heading(line, "效果")
        if is_effect_heading:
            collecting_detail = True
            if effect_remainder:
                current_detail_lines.append(effect_remainder)
            continue

        if waiting_name and not current_name:
            current_name = _normalize_formation_effect_name(line)
            waiting_name = False
            continue

        if collecting_detail:
            normalized_detail_line = _normalize_formation_effect_detail(line)
            if normalized_detail_line:
                current_detail_lines.append(normalized_detail_line)

    flush_current()

    merged: list[dict[str, str]] = []
    merged_by_name: dict[str, dict[str, str]] = {}
    for item in raw_items:
        effect_name = _normalize_formation_effect_name(item.get("effect_name", ""))
        effect_detail = _normalize_formation_effect_detail(item.get("effect_detail", ""))
        if not effect_name or not effect_detail:
            continue
        existing = merged_by_name.get(effect_name)
        if existing is None:
            payload = {
                "effect_name": effect_name,
                "effect_detail": effect_detail,
            }
            merged_by_name[effect_name] = payload
            merged.append(payload)
            continue
        if effect_detail != existing["effect_detail"]:
            existing["effect_detail"] = "\n".join(
                dict.fromkeys(
                    line
                    for line in [*existing["effect_detail"].splitlines(), *effect_detail.splitlines()]
                    if line
                )
            )

    if not merged:
        raise ValueError("未能从截图中识别词缀效果")
    return merged, normalized_lines
