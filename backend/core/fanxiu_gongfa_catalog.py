from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root
from backend.core.fanxiu_timeline import (
    TIMELINE_SOURCE_ROWS,
    TIMELINE_HINT_LIMIT,
    build_activity_passed_hints,
    build_timeline_context,
    card_timeline_sort_value,
    clone_hints_via_item,
    first_timeline_hint,
    sort_timeline_hints,
)


DEFAULT_GONGFA_ROWS = Path("parsed_configs/Gongfa/rows.json")
DEFAULT_GONGFA_SKILL_ROWS = Path("parsed_configs/GongfaSkill/rows.json")
DEFAULT_GONGFA_PIN_ROWS = Path("parsed_configs/GongfaPin/rows.json")
DEFAULT_QUALITY_ROWS = Path("parsed_configs/Quality/rows.json")
DEFAULT_ITEM_ROWS = Path("parsed_configs/Item/rows.json")
DEFAULT_FAZE_RESOURCE_ROWS = Path("parsed_configs/FazeResource/rows.json")
DEFAULT_FAZE_EFFECT_RESOURCE_ROWS = Path("parsed_configs/FazeEffectResource/rows.json")
DEFAULT_ANI_EFFECT_ROWS = Path("parsed_configs/AniEffect/rows.json")
DEFAULT_GONGFA_CATALOG = Path("parsed_configs/gongfa_catalog/gongfa_catalog.json")
GONGFA_CATALOG_SCHEMA_VERSION = 5
PROGRESSION_TABLES = {
    "gongfa_jie": Path("parsed_configs/GongfaJie/rows.json"),
    "lingjie_jie": Path("parsed_configs/Lingjie-GongfaJie/rows.json"),
    "renjie_jie": Path("parsed_configs/Renjie-GongfaJie/rows.json"),
    "special_jie": Path("parsed_configs/Special-GongfaJie/rows.json"),
    "star": Path("parsed_configs/GongfaStar/rows.json"),
    "upgrade": Path("parsed_configs/GongfaUpgrade/rows.json"),
}
_WHITESPACE_RE = re.compile(r"\s+")
_BRACKET_TERM_RE = re.compile(r"【([^】]{1,30})】")
_COLOR_TAG_RE = re.compile(r"<color=(#[0-9a-fA-F]{3,8})>(.*?)</color>", re.DOTALL)
SKILL_TYPE_NAMES = {
    1: "普攻",
    2: "神通",
    3: "绝招",
    4: "法宝",
    5: "心法",
    24: "秘传",
    25: "其他",
    29: "异能",
    30: "异能",
    42: "斗技",
    410: "异火",
}
SKILL_SUB_TYPE_NAMES = {
    1: "剑修",
    2: "法修",
    3: "魔修",
    4: "体修",
    5: "绝招",
    10: "特殊",
    11: "仙书",
    12: "仙界书",
    13: "斗技",
    20: "被动",
}
GONGFA_QUALITY_FAMILY_NAMES = ["功法", "仙术", "异能", "秘传", "斗技", "绝招"]
GONGFA_QUALITY_GRADE_NAMES = ["上品", "珍品", "绝品", "仙品", "神品", "圣品"]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_export_file(path: str | Path | None, default: Path, *, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(path) if path else default
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"文件必须位于导出根目录内：{root}")
    if not resolved.is_file():
        raise FanxiuResourceError(f"文件不存在：{resolved}")
    return resolved


def _load_json_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise FanxiuResourceError(f"JSON 文件不是行列表：{path}")
    return [item for item in data if isinstance(item, dict)]


def _load_optional_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return _load_json_rows(path)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


def _text_value(row: dict[str, Any], field: str) -> str:
    value = row.get(f"{field}_plain")
    if value is None or value == "":
        value = row.get(field)
    return "" if value is None else str(value)


def _rich_text_value(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if value is None or value == "":
        value = row.get(f"{field}_plain")
    return "" if value is None else str(value)


def _enum_name(mapping: dict[int, str], value: Any) -> str:
    parsed = _as_int(value)
    return mapping.get(parsed, "") if parsed is not None else ""


def _preview(value: Any, limit: int = 180) -> str:
    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _normalize_search_text(value: Any) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip().lower()


def _strip_rich_tags(value: Any) -> str:
    return re.sub(r"</?color(?:=[^>]+)?>", "", str(value or ""))


def _rich_color_segments(value: Any) -> list[tuple[str, str]]:
    text = str(value or "")
    segments: list[tuple[str, str]] = []
    cursor = 0
    for match in _COLOR_TAG_RE.finditer(text):
        if match.start() > cursor:
            segments.append(("", _strip_rich_tags(text[cursor : match.start()])))
        segments.append((match.group(1), _strip_rich_tags(match.group(2))))
        cursor = match.end()
    if cursor < len(text):
        segments.append(("", _strip_rich_tags(text[cursor:])))
    return [(color, segment_text) for color, segment_text in segments if segment_text]


def _slice_rich_text(value: Any, start: int, end: int) -> str:
    if start < 0 or end <= start:
        return ""
    pieces: list[str] = []
    cursor = 0
    for color, text in _rich_color_segments(value):
        next_cursor = cursor + len(text)
        left = max(start, cursor)
        right = min(end, next_cursor)
        if left < right:
            piece = text[left - cursor : right - cursor]
            pieces.append(f"<color={color}>{piece}</color>" if color else piece)
        cursor = next_cursor
        if cursor >= end:
            break
    return "".join(pieces)


def _first_rich_color(value: Any) -> str:
    match = _COLOR_TAG_RE.search(str(value or ""))
    return match.group(1) if match else ""


def _is_progression_section_title(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if re.fullmatch(r"【[^】]{1,30}】", text):
        return True
    return bool(re.match(r"^[一二三四五六七八九十0-9]+[阶重]效果[:：]", text))


def _split_progression_description_sections(plain_value: Any, rich_value: Any) -> list[dict[str, Any]]:
    rich_text = str(rich_value or plain_value or "")
    plain_text = str(plain_value or _strip_rich_tags(rich_text) or "")
    if not rich_text and not plain_text:
        return []
    rich_lines = rich_text.splitlines()
    plain_lines = plain_text.splitlines()
    if len(plain_lines) < len(rich_lines):
        plain_lines.extend(_strip_rich_tags(line) for line in rich_lines[len(plain_lines) :])
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        if current.get("title") or current.get("lines"):
            sections.append(current)
        current = None

    for index, rich_line in enumerate(rich_lines):
        plain_line = plain_lines[index] if index < len(plain_lines) else _strip_rich_tags(rich_line)
        plain_stripped = plain_line.strip()
        rich_stripped = rich_line.strip()
        if _is_progression_section_title(plain_stripped):
            flush()
            current = {
                "title": plain_stripped,
                "title_rich": rich_stripped,
                "lines": [],
                "rich_lines": [],
            }
            continue
        if current is None:
            current = {"title": "", "title_rich": "", "lines": [], "rich_lines": []}
        if plain_stripped or rich_stripped:
            current["lines"].append(plain_line)
            current["rich_lines"].append(rich_line)
    flush()
    return sections


def _quality_part_rich_label(card: dict[str, Any], label: str) -> str:
    rich_label = str(card.get("quality_rich_name") or "")
    plain_label = _strip_rich_tags(rich_label) or str(card.get("quality_name") or "")
    if not rich_label or not label:
        return ""
    start = plain_label.find(label)
    if start < 0:
        return ""
    return _slice_rich_text(rich_label, start, start + len(label))


def _extract_terms(*values: Any, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        for match in _BRACKET_TERM_RE.finditer(str(value or "")):
            term = match.group(1).strip()
            if not term or term in seen:
                continue
            seen.add(term)
            terms.append(term)
            if len(terms) >= limit:
                return terms
    return terms


def _extract_item_refs(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, (list, tuple, set)):
        refs: list[dict[str, Any]] = []
        for item in value:
            refs.extend(_extract_item_refs(item))
        return refs
    if isinstance(value, dict):
        refs: list[dict[str, Any]] = []
        for item in value.values():
            refs.extend(_extract_item_refs(item))
        return refs
    refs = []
    for item_id, count in re.findall(r"Item\|(\d+)(?:_(-?\d+(?:\.\d+)?))?", str(value or "")):
        refs.append({"id": item_id, "count": count or ""})
    return refs


def _compact_item_row(row: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id") or row.get("_row_key") or ref.get("id"),
        "name": _text_value(row, "name") or str(ref.get("id") or ""),
        "icon": row.get("icon"),
        "small_icon": row.get("smallIcon"),
        "quality": row.get("quality"),
        "count": ref.get("count") or "",
        "description": _text_value(row, "descript"),
    }


def _resolve_item_refs(value: Any, items_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in _extract_item_refs(value):
        item_id = str(ref.get("id") or "")
        count = str(ref.get("count") or "")
        if not item_id:
            continue
        key = (item_id, count)
        if key in seen:
            continue
        seen.add(key)
        row = items_by_id.get(item_id, {"id": item_id})
        resolved.append(_compact_item_row(row, ref))
    return resolved


def _split_faze_tip_str(value: Any) -> list[dict[str, str]]:
    tips: list[dict[str, str]] = []
    for item in str(value or "").split(";"):
        text = item.strip()
        if not text:
            continue
        code, sep, label = text.partition("|")
        reason = code.strip() if sep else ""
        tips.append({"code": reason, "reason": reason, "text": (label if sep else code).strip()})
    return tips


def _compact_faze_effect_resource_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id") or row.get("_row_key"),
        "type": row.get("type"),
        "params": row.get("params"),
    }


def _compact_faze_resource_row(
    row: dict[str, Any],
    faze_effect_by_id: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tip_text = _text_value(row, "tipStr")
    effect_id = row.get("effects")
    effect_resource = None
    if faze_effect_by_id:
        parsed_effect_id = _as_int(effect_id)
        if parsed_effect_id is not None:
            effect_row = faze_effect_by_id.get(parsed_effect_id)
            if effect_row:
                effect_resource = _compact_faze_effect_resource_row(effect_row)
    return {
        "id": row.get("id") or row.get("_row_key"),
        "sort": row.get("sort"),
        "name": _text_value(row, "name"),
        "head_name": _text_value(row, "headName"),
        "effects": row.get("effects"),
        "effect_resource": effect_resource,
        "last_grade": row.get("lastGrade"),
        "show_condition": row.get("showCondition"),
        "source": row.get("source"),
        "tip_str": tip_text,
        "tips": _split_faze_tip_str(tip_text),
    }


def _append_sample(samples: list[str], value: Any, *, limit: int = 10) -> None:
    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    if not text or text in samples or len(samples) >= limit:
        return
    samples.append(text)


def _build_faze_effect_type_summary_rows(
    faze_effect_rows: list[dict[str, Any]],
    faze_resource_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    effects_by_id = {
        effect_id: row
        for row in faze_effect_rows
        if (effect_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    grouped: dict[str, dict[str, Any]] = {}
    for row in faze_effect_rows:
        effect_type = str(row.get("type") or "")
        if not effect_type:
            continue
        item = grouped.setdefault(
            effect_type,
            {
                "type": effect_type,
                "effect_count": 0,
                "faze_resource_count": 0,
                "effect_ids": [],
                "faze_ids": [],
                "faze_names": [],
                "tips": [],
                "params": [],
                "attrs": [],
            },
        )
        item["effect_count"] += 1
        _append_sample(item["effect_ids"], row.get("id") or row.get("_row_key"))
        _append_sample(item["params"], row.get("params"))
        _append_sample(item["attrs"], row.get("attr"))

    for row in faze_resource_rows:
        effect_id = _as_int(row.get("effects"))
        effect_row = effects_by_id.get(effect_id or -1)
        if not effect_row:
            continue
        effect_type = str(effect_row.get("type") or "")
        item = grouped.get(effect_type)
        if not item:
            continue
        item["faze_resource_count"] += 1
        _append_sample(item["faze_ids"], row.get("id") or row.get("_row_key"))
        _append_sample(item["faze_names"], _text_value(row, "name") or _text_value(row, "headName"))
        for tip in _split_faze_tip_str(_text_value(row, "tipStr")):
            _append_sample(item["tips"], tip.get("text"))

    rows: list[dict[str, Any]] = []
    for item in grouped.values():
        rows.append(
            {
                "type": item["type"],
                "effect_count": item["effect_count"],
                "faze_resource_count": item["faze_resource_count"],
                "effect_ids_sample": "、".join(item["effect_ids"]),
                "faze_ids_sample": "、".join(item["faze_ids"]),
                "faze_names_sample": "、".join(item["faze_names"]),
                "tips_sample": "；".join(item["tips"]),
                "params_sample": "；".join(item["params"]),
                "attrs_sample": "；".join(item["attrs"]),
            }
        )
    return sorted(rows, key=lambda row: (_sort_value(row.get("type")), str(row.get("type") or "")))


def _build_faze_tip_code_summary_rows(
    faze_resource_rows: list[dict[str, Any]],
    faze_effect_by_id: dict[int, dict[str, Any]],
    ani_effect_by_reason_id: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in faze_resource_rows:
        effect_id = _as_int(row.get("effects"))
        effect_row = faze_effect_by_id.get(effect_id or -1)
        effect_type = effect_row.get("type") if effect_row else ""
        faze_id = row.get("id") or row.get("_row_key")
        faze_name = _text_value(row, "name") or _text_value(row, "headName")
        for tip in _split_faze_tip_str(_text_value(row, "tipStr")):
            code = str(tip.get("code") or "").strip()
            if not code:
                continue
            item = grouped.setdefault(
                code,
                {
                    "code": code,
                    "tip_count": 0,
                    "faze_ids_seen": set(),
                    "ani_effects": [],
                    "ani_base_ids": [],
                    "effect_types": [],
                    "faze_ids": [],
                    "faze_names": [],
                    "texts": [],
                },
            )
            item["tip_count"] += 1
            item["faze_ids_seen"].add(str(faze_id))
            if ani_effect_by_reason_id and (reason_id := _as_int(code)) is not None:
                ani_effect_row = ani_effect_by_reason_id.get(reason_id)
                if ani_effect_row:
                    _append_sample(item["ani_effects"], ani_effect_row.get("effect"))
                    _append_sample(item["ani_base_ids"], ",".join(str(base_id) for base_id in ani_effect_row.get("baseId") or []))
            _append_sample(item["effect_types"], effect_type)
            _append_sample(item["faze_ids"], faze_id)
            _append_sample(item["faze_names"], faze_name)
            _append_sample(item["texts"], tip.get("text"))

    rows: list[dict[str, Any]] = []
    for item in grouped.values():
        rows.append(
            {
                "code": item["code"],
                "tip_count": item["tip_count"],
                "faze_resource_count": len(item["faze_ids_seen"]),
                "has_ani_effect": bool(item["ani_effects"]),
                "ani_effects_sample": "；".join(item["ani_effects"]),
                "ani_base_ids_sample": "；".join(item["ani_base_ids"]),
                "effect_types_sample": "、".join(item["effect_types"]),
                "faze_ids_sample": "、".join(item["faze_ids"]),
                "faze_names_sample": "、".join(item["faze_names"]),
                "texts_sample": "；".join(item["texts"]),
            }
        )
    return sorted(rows, key=lambda row: (_sort_value(row.get("code")), str(row.get("code") or "")))


def _sort_value(value: Any, fallback: int = 10**12) -> int:
    parsed = _as_int(value)
    return parsed if parsed is not None else fallback


def _skill_sort_key(row: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        _sort_value(row.get("group")),
        _sort_value(row.get("pin")),
        _sort_value(row.get("quality")),
        str(row.get("_row_key") or row.get("id") or ""),
    )


def _progression_sort_key(row: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
    return (
        _sort_value(row.get("pin")),
        _sort_value(row.get("jie")),
        _sort_value(row.get("star")),
        _sort_value(row.get("grade")),
        _sort_value(row.get("id")),
        str(row.get("_row_key") or row.get("id") or ""),
    )


def _gongfa_sort_key(row: dict[str, Any]) -> tuple[int, int]:
    return (_sort_value(row.get("sort")), _sort_value(row.get("id")))


def _compact_gongfa_row(row: dict[str, Any], items_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    skill_type = row.get("skillType")
    return {
        "id": row.get("id"),
        "name": _text_value(row, "name"),
        "quality": row.get("quality"),
        "skill_type": skill_type,
        "skill_type_name": _enum_name(SKILL_TYPE_NAMES, skill_type),
        "icon": row.get("icon"),
        "small_icon": row.get("smallIcon"),
        "description": _text_value(row, "descript"),
        "description_rich": _rich_text_value(row, "descript"),
        "consume": row.get("consume"),
        "consume_items": _resolve_item_refs(row.get("consume"), items_by_id),
        "show_condition": row.get("showCondition"),
        "show_condition_items": _resolve_item_refs(row.get("showCondition"), items_by_id),
        "sort": row.get("sort"),
        "level_group": row.get("levelGroup"),
        "species": row.get("species"),
        "source_row_key": row.get("_row_key"),
    }


def _compact_quality_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality_name": _text_value(row, "name"),
        "quality_rich_name": row.get("name") or _text_value(row, "name"),
        "quality_rank": row.get("quality"),
        "quality_icon": row.get("qualityIcon"),
        "quality_type_id": row.get("typeId"),
        "quality_type_name": _text_value(row, "typeName"),
        "quality_sort": row.get("sort"),
    }


def _compact_standard_quality_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality_name": _text_value(row, "name"),
        "quality_color": row.get("color"),
        "quality_tab": _text_value(row, "tab"),
    }


def _compact_skill_row(row: dict[str, Any], standard_quality_by_id: dict[int, dict[str, Any]]) -> dict[str, Any]:
    quality = row.get("quality")
    skill_type = row.get("type")
    sub_type = row.get("subType")
    standard_quality = standard_quality_by_id.get(_as_int(quality) or -1, {})
    describe_plain = _text_value(row, "describe")
    describe_rich = _rich_text_value(row, "describe")
    effect_describe_plain = _text_value(row, "effectDescribe")
    effect_describe_rich = _rich_text_value(row, "effectDescribe")
    additional_describe_plain = _text_value(row, "additionalDescribe")
    additional_describe_rich = _rich_text_value(row, "additionalDescribe")
    return {
        "row_key": row.get("_row_key"),
        "id": row.get("id"),
        "origin_id": row.get("originId"),
        "name": _text_value(row, "name"),
        "skill_name": _text_value(row, "skillName"),
        "quality": quality,
        "quality_name": standard_quality.get("quality_name", ""),
        "quality_color": standard_quality.get("quality_color", ""),
        "quality_tab": standard_quality.get("quality_tab", ""),
        "pin": row.get("pin"),
        "group": row.get("group"),
        "type": skill_type,
        "type_name": _enum_name(SKILL_TYPE_NAMES, skill_type),
        "sub_type": sub_type,
        "sub_type_name": _enum_name(SKILL_SUB_TYPE_NAMES, sub_type),
        "icon": row.get("iconNew") or row.get("icon"),
        "describe": describe_plain,
        "describe_rich": describe_rich,
        "describe_sections": _split_progression_description_sections(describe_plain, describe_rich),
        "effect_describe": effect_describe_plain,
        "effect_describe_rich": effect_describe_rich,
        "effect_describe_sections": _split_progression_description_sections(effect_describe_plain, effect_describe_rich),
        "additional_describe": additional_describe_plain,
        "additional_describe_rich": additional_describe_rich,
        "additional_describe_sections": _split_progression_description_sections(additional_describe_plain, additional_describe_rich),
    }


def _compact_progression_row(
    row: dict[str, Any],
    items_by_id: dict[str, dict[str, Any]],
    faze_resource_by_id: dict[int, dict[str, Any]] | None = None,
    faze_effect_by_id: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    faze_id = row.get("fazeId")
    faze_resource = None
    if faze_resource_by_id:
        parsed_faze_id = _as_int(faze_id)
        if parsed_faze_id is not None:
            faze_row = faze_resource_by_id.get(parsed_faze_id)
            if faze_row:
                faze_resource = _compact_faze_resource_row(faze_row, faze_effect_by_id)
    describe_plain = _text_value(row, "describe")
    describe_rich = _rich_text_value(row, "describe")
    return {
        "row_key": row.get("_row_key"),
        "id": row.get("id"),
        "gid": row.get("gid"),
        "pin": row.get("pin"),
        "jie": row.get("jie"),
        "star": row.get("star"),
        "grade": row.get("grade"),
        "name": _text_value(row, "name"),
        "title": _text_value(row, "title"),
        "condition": row.get("condition"),
        "show_condition": row.get("showCondition"),
        "consume": row.get("consume"),
        "consume_items": _resolve_item_refs(row.get("consume"), items_by_id),
        "skill": row.get("skill"),
        "feature": row.get("feature"),
        "attr": row.get("attr"),
        "attributes": row.get("attributes"),
        "faze_id": faze_id,
        "faze_resource": faze_resource,
        "describe": describe_plain,
        "describe_rich": describe_rich,
        "describe_sections": _split_progression_description_sections(describe_plain, describe_rich),
        "top_describe": _text_value(row, "topDescribe"),
        "top_describe_rich": _rich_text_value(row, "topDescribe"),
        "down_describe": _text_value(row, "downDescribe"),
        "down_describe_rich": _rich_text_value(row, "downDescribe"),
        "upgrade_desc": _text_value(row, "upgradeDesc"),
        "upgrade_desc_rich": _rich_text_value(row, "upgradeDesc"),
        "bag_effect": row.get("bagEffect"),
        "skill_effect": row.get("skillEffect"),
    }


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _group_by_int(rows: list[dict[str, Any]], field: str) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = _as_int(row.get(field))
        if value is not None:
            grouped[value].append(row)
    return grouped


def _build_progression_alias_rows(
    progression_tables: dict[str, list[dict[str, Any]]],
    skill_rows: list[dict[str, Any]],
    item_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    special_by_id = {
        row_id: row
        for row in progression_tables.get("special_jie", [])
        if (row_id := _as_int(row.get("id"))) is not None
    }
    special_by_gid = _group_by_int(progression_tables.get("special_jie", []), "gid")
    skill_by_origin = _group_by_int(skill_rows, "originId")
    item_by_effect_value = _group_by_int(item_rows, "effectValue")
    alias_rows: list[dict[str, Any]] = []

    for source_table in ("gongfa_jie", "lingjie_jie"):
        rows_by_gid = _group_by_int(progression_tables.get(source_table, []), "gid")
        for source_gid, rows in sorted(rows_by_gid.items()):
            feature_targets: list[dict[str, Any]] = []
            for row in rows:
                feature_id = _as_int(row.get("feature"))
                if feature_id is None:
                    continue
                target = special_by_id.get(feature_id)
                if target is None:
                    continue
                feature_targets.append(target)
            target_gids = sorted({
                target_gid
                for target in feature_targets
                if (target_gid := _as_int(target.get("gid"))) is not None
            })
            if not target_gids:
                source_skills = skill_by_origin.get(source_gid, [])
                source_items = item_by_effect_value.get(source_gid, [])
                alias_rows.append(
                    {
                        "alias_status": "source_only" if source_skills or source_items else "unresolved",
                        "source_table": source_table,
                        "source_gid": source_gid,
                        "source_row_count": len(rows),
                        "source_item_count": len(source_items),
                        "source_item_names": "、".join(
                            dict.fromkeys(_text_value(item, "name") or str(item.get("_row_key") or "") for item in source_items[:8])
                        ),
                        "source_skill_count": len(source_skills),
                        "source_skill_names": "、".join(
                            dict.fromkeys(
                                _text_value(skill, "skillName") or _text_value(skill, "name") or str(skill.get("_row_key") or "")
                                for skill in source_skills[:8]
                            )
                        ),
                        "target_table": "",
                        "target_gid": "",
                        "feature_match_count": 0,
                        "target_row_count": 0,
                        "target_skill_count": 0,
                        "target_skill_names": "",
                    }
                )
                continue
            for target_gid in target_gids:
                target_skills = skill_by_origin.get(target_gid, [])
                alias_rows.append(
                    {
                        "alias_status": "target_special",
                        "source_table": source_table,
                        "source_gid": source_gid,
                        "source_row_count": len(rows),
                        "source_item_count": len(item_by_effect_value.get(source_gid, [])),
                        "source_item_names": "、".join(
                            dict.fromkeys(
                                _text_value(item, "name") or str(item.get("_row_key") or "")
                                for item in item_by_effect_value.get(source_gid, [])[:8]
                            )
                        ),
                        "source_skill_count": len(skill_by_origin.get(source_gid, [])),
                        "source_skill_names": "、".join(
                            dict.fromkeys(
                                _text_value(skill, "skillName") or _text_value(skill, "name") or str(skill.get("_row_key") or "")
                                for skill in skill_by_origin.get(source_gid, [])[:8]
                            )
                        ),
                        "target_table": "special_jie",
                        "target_gid": target_gid,
                        "feature_match_count": sum(1 for target in feature_targets if _as_int(target.get("gid")) == target_gid),
                        "target_row_count": len(special_by_gid.get(target_gid, [])),
                        "target_skill_count": len(target_skills),
                        "target_skill_names": "、".join(
                            dict.fromkeys(
                                _text_value(skill, "skillName") or _text_value(skill, "name") or str(skill.get("_row_key") or "")
                                for skill in target_skills[:8]
                            )
                        ),
                    }
                )
    return alias_rows


def _default_catalog_source_files(root: Path) -> list[Path]:
    return [
        root / DEFAULT_GONGFA_ROWS,
        root / DEFAULT_GONGFA_SKILL_ROWS,
        root / DEFAULT_GONGFA_PIN_ROWS,
        root / DEFAULT_QUALITY_ROWS,
        root / DEFAULT_ITEM_ROWS,
        root / DEFAULT_FAZE_RESOURCE_ROWS,
        root / DEFAULT_FAZE_EFFECT_RESOURCE_ROWS,
        root / DEFAULT_ANI_EFFECT_ROWS,
        *[root / relative_path for relative_path in PROGRESSION_TABLES.values()],
        *[root / relative_path for relative_path in TIMELINE_SOURCE_ROWS],
    ]


def _is_default_catalog_stale(catalog_path: Path, root: Path) -> bool:
    if not catalog_path.is_file():
        return True
    try:
        with catalog_path.open("r", encoding="utf-8") as file:
            header = file.read(4096)
    except OSError:
        return True
    match = re.search(r'"schema_version"\s*:\s*(\d+)', header)
    if not match or int(match.group(1)) != GONGFA_CATALOG_SCHEMA_VERSION:
        return True
    catalog_mtime_ns = catalog_path.stat().st_mtime_ns
    return any(
        source_path.is_file() and source_path.stat().st_mtime_ns > catalog_mtime_ns
        for source_path in _default_catalog_source_files(root)
    )


def _resolve_catalog_file(export_root: str | Path | None = None, *, rebuild_missing: bool = True) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    path = (root / DEFAULT_GONGFA_CATALOG).resolve()
    if not _is_relative_to(path, root):
        raise FanxiuResourceError(f"文件必须位于导出根目录内：{root}")
    if rebuild_missing and _is_default_catalog_stale(path, root):
        build_fanxiu_gongfa_catalog(export_root=export_root)
    if not path.is_file():
        raise FanxiuResourceError(f"功法目录不存在，请先生成：{path}")
    return path


@lru_cache(maxsize=4)
def _load_gongfa_catalog_cached(path_text: str, mtime_ns: int, size: int, export_root_text: str) -> dict[str, Any]:
    catalog_path = Path(path_text)
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        raise FanxiuResourceError(f"功法目录格式不正确：{catalog_path}")
    return {
        "export_root": export_root_text,
        "catalog_path": str(catalog_path),
        **data,
    }


def load_fanxiu_gongfa_catalog(
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    catalog_path = _resolve_catalog_file(export_root, rebuild_missing=rebuild_missing)
    root = resolve_fanxiu_export_root(export_root)
    stat = catalog_path.stat()
    return _load_gongfa_catalog_cached(str(catalog_path), stat.st_mtime_ns, stat.st_size, str(root))


def _build_gongfa_search_doc(card: dict[str, Any], index: int) -> dict[str, Any]:
    card_id = _normalize_search_text(card.get("id"))
    name = _normalize_search_text(card.get("name"))
    icon = _normalize_search_text(card.get("icon"))
    description = _normalize_search_text(card.get("description"))
    quality_texts = tuple(
        _normalize_search_text(value)
        for value in (
            card.get("quality"),
            card.get("quality_name"),
            card.get("quality_type_name"),
            card.get("skill_type_name"),
        )
    )
    skill_texts: list[str] = []
    for skill in card.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        skill_texts.extend(
            [
                _normalize_search_text(skill.get("row_key")),
                _normalize_search_text(skill.get("id")),
                _normalize_search_text(skill.get("name")),
                _normalize_search_text(skill.get("skill_name")),
                _normalize_search_text(skill.get("quality_name")),
                _normalize_search_text(skill.get("type_name")),
                _normalize_search_text(skill.get("sub_type_name")),
                _normalize_search_text(skill.get("describe")),
                _normalize_search_text(skill.get("effect_describe")),
                _normalize_search_text(skill.get("additional_describe")),
            ]
        )
    progression_texts: list[str] = []
    progression = card.get("progression") or {}
    if isinstance(progression, dict):
        for rows in progression.values():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                progression_texts.extend(
                    [
                        _normalize_search_text(row.get("row_key")),
                        _normalize_search_text(row.get("id")),
                        _normalize_search_text(row.get("name")),
                        _normalize_search_text(row.get("title")),
                        _normalize_search_text(row.get("describe")),
                        _normalize_search_text(row.get("upgrade_desc")),
                    ]
                )
                faze_resource = row.get("faze_resource")
                if isinstance(faze_resource, dict):
                    effect_resource = faze_resource.get("effect_resource")
                    progression_texts.extend(
                        [
                            _normalize_search_text(faze_resource.get("id")),
                            _normalize_search_text(faze_resource.get("name")),
                            _normalize_search_text(faze_resource.get("head_name")),
                            _normalize_search_text(faze_resource.get("effects")),
                            _normalize_search_text(faze_resource.get("tip_str")),
                        ]
                    )
                    if isinstance(effect_resource, dict):
                        progression_texts.extend(
                            [
                                _normalize_search_text(effect_resource.get("id")),
                                _normalize_search_text(effect_resource.get("type")),
                                _normalize_search_text(effect_resource.get("params")),
                            ]
                        )

    return {
        "index": index,
        "card": card,
        "card_id": card_id,
        "name": name,
        "icon": icon,
        "description": description,
        "quality_values": tuple(str(value or "").strip() for value in (card.get("quality_name"), card.get("quality_rich_name"), card.get("quality"))),
        "quality_grade_name": _gongfa_quality_grade_name(card),
        "quality_family_name": _gongfa_quality_family_name(card),
        "skill_type_names": tuple(_gongfa_skill_type_labels(card)),
        "skill_type_values": tuple(str(value or "").strip() for value in (card.get("skill_type_name"), card.get("skill_type"), *_gongfa_skill_type_labels(card))),
        "quality_texts": quality_texts,
        "skill_texts": tuple(skill_texts),
        "progression_texts": tuple(progression_texts),
        "combined": " ".join([card_id, name, icon, description, *quality_texts, *skill_texts, *progression_texts]),
    }


@lru_cache(maxsize=4)
def _load_gongfa_runtime_index_cached(path_text: str, mtime_ns: int, size: int, export_root_text: str) -> dict[str, Any]:
    catalog = _load_gongfa_catalog_cached(path_text, mtime_ns, size, export_root_text)
    cards = [card for card in catalog.get("cards") or [] if isinstance(card, dict)]
    cards_by_id = {str(card.get("id")): card for card in cards if card.get("id") not in (None, "")}
    return {
        "catalog": catalog,
        "cards_by_id": cards_by_id,
        "quality_options": _build_gongfa_quality_options(cards),
        "quality_grade_options": _build_gongfa_quality_grade_options(cards),
        "quality_family_options": _build_gongfa_quality_family_options(cards),
        "skill_type_options": _build_gongfa_skill_type_options(cards),
        "search_docs": tuple(_build_gongfa_search_doc(card, index) for index, card in enumerate(cards)),
    }


def load_fanxiu_gongfa_runtime_index(
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    catalog_path = _resolve_catalog_file(export_root, rebuild_missing=rebuild_missing)
    root = resolve_fanxiu_export_root(export_root)
    stat = catalog_path.stat()
    return _load_gongfa_runtime_index_cached(str(catalog_path), stat.st_mtime_ns, stat.st_size, str(root))


def _card_terms(card: dict[str, Any], *, limit: int = 8) -> list[str]:
    skill_texts = [
        skill.get("describe") or skill.get("effect_describe") or skill.get("additional_describe") or ""
        for skill in card.get("skills") or []
        if isinstance(skill, dict)
    ]
    return _extract_terms(card.get("name"), card.get("description"), *skill_texts, limit=limit)


def _card_effect_preview(card: dict[str, Any]) -> str:
    for skill in card.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        text = skill.get("describe") or skill.get("effect_describe") or skill.get("additional_describe")
        if text:
            return _preview(text, 180)
    return ""


def _format_gongfa_search_item(card: dict[str, Any], score: int) -> dict[str, Any]:
    skills = [skill for skill in (card.get("skills") or []) if isinstance(skill, dict)]
    first_skills = skills[:3]
    skill_type_names = list(
        dict.fromkeys(
            str(name).strip()
            for skill in skills
            if (name := skill.get("type_name"))
        )
    )
    item = {
        "id": card.get("id"),
        "name": card.get("name") or str(card.get("id") or "未命名"),
        "quality": card.get("quality"),
        "quality_name": card.get("quality_name"),
        "quality_rich_name": card.get("quality_rich_name"),
        "quality_grade_name": _gongfa_quality_grade_name(card),
        "quality_family_name": _gongfa_quality_family_name(card),
        "quality_rank": card.get("quality_rank"),
        "quality_icon": card.get("quality_icon"),
        "quality_type_id": card.get("quality_type_id"),
        "quality_type_name": card.get("quality_type_name"),
        "skill_type": card.get("skill_type"),
        "skill_type_name": card.get("skill_type_name"),
        "icon": card.get("icon"),
        "small_icon": card.get("small_icon"),
        "description_preview": _preview(card.get("description"), 140),
        "effect_preview": _card_effect_preview(card),
        "skill_count": card.get("skill_count") or len(skills),
        "progression_counts": card.get("progression_counts") or {},
        "terms": _card_terms(card),
        "skill_names": [
            skill.get("skill_name") or skill.get("name") or str(skill.get("row_key") or "")
            for skill in first_skills
        ],
        "skill_type_names": skill_type_names[:4],
        "score": score,
    }
    if card.get("first_time_hint"):
        item["first_time_hint"] = card.get("first_time_hint")
    return item


def _build_gongfa_quality_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        label = str(card.get("quality_name") or "").strip()
        if not label:
            quality = card.get("quality")
            label = f"品质 {quality}" if quality not in (None, "") else "品质未知"
        item = grouped.setdefault(
            label,
            {
                "value": label,
                "label": label,
                "rich_label": card.get("quality_rich_name") or label,
                "color": _first_rich_color(card.get("quality_rich_name")),
                "count": 0,
                "quality": card.get("quality"),
                "quality_rank": card.get("quality_rank"),
                "quality_sort": card.get("quality_sort"),
            },
        )
        item["count"] += 1
    return sorted(
        grouped.values(),
        key=lambda item: (
            _sort_value(item.get("quality_sort")),
            _sort_value(item.get("quality_rank")),
            _sort_value(item.get("quality")),
            str(item.get("label") or ""),
        ),
    )


def _split_gongfa_quality_name(card: dict[str, Any]) -> tuple[str, str]:
    label = str(card.get("quality_name") or "").strip()
    for family in GONGFA_QUALITY_FAMILY_NAMES:
        if label.endswith(family) and len(label) > len(family):
            return label[: -len(family)], family
    return label, ""


def _gongfa_quality_grade_name(card: dict[str, Any]) -> str:
    return _split_gongfa_quality_name(card)[0]


def _gongfa_quality_family_name(card: dict[str, Any]) -> str:
    return _split_gongfa_quality_name(card)[1]


def _build_gongfa_quality_grade_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        label = _gongfa_quality_grade_name(card)
        if not label:
            continue
        rich_label = _quality_part_rich_label(card, label)
        item = grouped.setdefault(
            label,
            {
                "value": label,
                "label": label,
                "rich_label": rich_label or label,
                "color": _first_rich_color(rich_label),
                "count": 0,
            },
        )
        if not item.get("rich_label") and rich_label:
            item["rich_label"] = rich_label
        if not item.get("color") and rich_label:
            item["color"] = _first_rich_color(rich_label)
        item["count"] += 1
    grade_order = {name: index for index, name in enumerate(GONGFA_QUALITY_GRADE_NAMES)}
    return sorted(grouped.values(), key=lambda item: (grade_order.get(str(item.get("label") or ""), 10**9), str(item.get("label") or "")))


def _build_gongfa_quality_family_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        label = _gongfa_quality_family_name(card)
        if not label:
            continue
        rich_label = _quality_part_rich_label(card, label)
        item = grouped.setdefault(
            label,
            {
                "value": label,
                "label": label,
                "rich_label": rich_label or label,
                "color": _first_rich_color(rich_label),
                "count": 0,
            },
        )
        if not item.get("rich_label") and rich_label:
            item["rich_label"] = rich_label
        if not item.get("color") and rich_label:
            item["color"] = _first_rich_color(rich_label)
        item["count"] += 1
    family_order = {name: index for index, name in enumerate(GONGFA_QUALITY_FAMILY_NAMES)}
    return sorted(grouped.values(), key=lambda item: (family_order.get(str(item.get("label") or ""), 10**9), str(item.get("label") or "")))


def _gongfa_skill_type_labels(card: dict[str, Any]) -> list[str]:
    labels = [
        str(card.get("skill_type_name") or "").strip(),
    ]
    for skill in card.get("skills") or []:
        if isinstance(skill, dict):
            labels.append(str(skill.get("type_name") or "").strip())
    return [label for label in dict.fromkeys(labels) if label]


def _build_gongfa_skill_type_options(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        for label in _gongfa_skill_type_labels(card):
            item = grouped.setdefault(
                label,
                {
                    "value": label,
                    "label": label,
                    "count": 0,
                    "skill_type": card.get("skill_type"),
                },
            )
            item["count"] += 1
    type_order = {name: index for index, name in enumerate(dict.fromkeys(SKILL_TYPE_NAMES.values()))}
    return sorted(
        grouped.values(),
        key=lambda item: (
            type_order.get(str(item.get("label") or ""), 10**9),
            str(item.get("label") or ""),
        ),
    )


def _matches_gongfa_quality_filter(card: dict[str, Any], quality_name: str) -> bool:
    if not quality_name:
        return True
    values = [
        card.get("quality_name"),
        card.get("quality_rich_name"),
        card.get("quality"),
    ]
    return any(str(value or "").strip() == quality_name for value in values)


def _matches_gongfa_quality_grade_filter(card: dict[str, Any], quality_grade_name: str) -> bool:
    if not quality_grade_name:
        return True
    return _gongfa_quality_grade_name(card) == quality_grade_name


def _matches_gongfa_quality_family_filter(card: dict[str, Any], quality_family_name: str) -> bool:
    if not quality_family_name:
        return True
    return _gongfa_quality_family_name(card) == quality_family_name


def _matches_gongfa_skill_type_filter(card: dict[str, Any], skill_type_name: str) -> bool:
    if not skill_type_name:
        return True
    values = [
        card.get("skill_type_name"),
        card.get("skill_type"),
        *_gongfa_skill_type_labels(card),
    ]
    for skill in card.get("skills") or []:
        if isinstance(skill, dict):
            values.extend([skill.get("type_name"), skill.get("type")])
    return any(str(value or "").strip() == skill_type_name for value in values)


def _score_gongfa_search_doc(doc: dict[str, Any], terms: tuple[str, ...]) -> int:
    if not terms:
        return 1
    if not all(term in doc["combined"] for term in terms):
        return 0

    card_id = doc["card_id"]
    name = doc["name"]
    icon = doc["icon"]
    description = doc["description"]
    quality_texts = doc["quality_texts"]
    skill_texts = doc["skill_texts"]
    progression_texts = doc["progression_texts"]
    score = 0
    for term in terms:
        if card_id == term:
            score += 180
        if name == term:
            score += 220
        if term in name:
            score += 90
        if term in icon:
            score += 30
        if term in description:
            score += 18
        if any(term in text for text in quality_texts):
            score += 28
        if any(term in text for text in skill_texts):
            score += 40
        if any(term in text for text in progression_texts):
            score += 20
    return score


def _score_gongfa_card(card: dict[str, Any], terms: tuple[str, ...]) -> int:
    return _score_gongfa_search_doc(_build_gongfa_search_doc(card, 0), terms)


def _build_gongfa_facet_index(scored_rows: list[tuple[int, int, dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    rows: dict[str, dict[str, list[str]]] = {
        "quality_grade_name": {},
        "quality_family_name": {},
        "skill_type_name": {},
    }
    object_ids: list[str] = []
    for _score, _index, card, doc in scored_rows:
        object_id = str(card.get("id") or "")
        if not object_id:
            continue
        object_ids.append(object_id)
        grade_name = str(doc.get("quality_grade_name") or "").strip()
        family_name = str(doc.get("quality_family_name") or "").strip()
        if grade_name:
            rows["quality_grade_name"].setdefault(grade_name, []).append(object_id)
        if family_name:
            rows["quality_family_name"].setdefault(family_name, []).append(object_id)
        for skill_type_name in doc.get("skill_type_names") or ():
            if skill_type_name:
                rows["skill_type_name"].setdefault(str(skill_type_name), []).append(object_id)
    return {
        "object_ids": object_ids,
        "rows": rows,
    }


def search_fanxiu_gongfa_cards(
    *,
    query: str = "",
    quality_name: str = "",
    quality_grade_name: str = "",
    quality_family_name: str = "",
    skill_type_name: str = "",
    sort_by: str = "default",
    sort_order: str = "asc",
    limit: int = 80,
    offset: int = 0,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    runtime_index = load_fanxiu_gongfa_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    catalog = runtime_index["catalog"]
    quality_name = str(quality_name or "").strip()
    quality_grade_name = str(quality_grade_name or "").strip()
    quality_family_name = str(quality_family_name or "").strip()
    skill_type_name = str(skill_type_name or "").strip()
    sort_by = str(sort_by or "default").strip()
    sort_order = str(sort_order or "asc").strip().lower()
    if sort_by not in {"default", "time"}:
        sort_by = "default"
    if sort_order not in {"asc", "desc"}:
        sort_order = "asc"
    terms = tuple(item.strip().lower() for item in re.split(r"\s+", query or "") if item.strip())
    query_rows: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
    for doc in runtime_index["search_docs"]:
        card = doc["card"]
        score = _score_gongfa_search_doc(doc, terms)
        if score <= 0:
            continue
        query_rows.append((score, int(doc["index"]), card, doc))
    scored_rows: list[tuple[int, int, dict[str, Any]]] = []
    for score, index, card, doc in query_rows:
        if quality_name and quality_name not in doc["quality_values"]:
            continue
        if quality_grade_name and doc["quality_grade_name"] != quality_grade_name:
            continue
        if quality_family_name and doc["quality_family_name"] != quality_family_name:
            continue
        if skill_type_name and skill_type_name not in doc["skill_type_values"]:
            continue
        scored_rows.append((score, index, card))
    if sort_by == "time":
        if sort_order == "desc":
            scored_rows.sort(
                key=lambda item: (
                    -card_timeline_sort_value(item[2]),
                    _sort_value(item[2].get("sort")),
                    _sort_value(item[2].get("id")),
                    item[1],
                )
            )
        else:
            scored_rows.sort(
                key=lambda item: (
                    card_timeline_sort_value(item[2]),
                    _sort_value(item[2].get("sort")),
                    _sort_value(item[2].get("id")),
                    item[1],
                )
            )
    elif terms:
        scored_rows.sort(key=lambda item: (-item[0], _sort_value(item[2].get("sort")), _sort_value(item[2].get("id"))))
    else:
        scored_rows.sort(key=lambda item: (_sort_value(item[2].get("sort")), _sort_value(item[2].get("id")), item[1]))
    page_rows = scored_rows[offset : offset + limit]
    return {
        "query": query,
        "quality_name": quality_name,
        "quality_grade_name": quality_grade_name,
        "quality_family_name": quality_family_name,
        "skill_type_name": skill_type_name,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "limit": limit,
        "offset": offset,
        "total": len(scored_rows),
        "stats": catalog.get("stats") or {},
        "catalog_path": catalog["catalog_path"],
        "quality_options": runtime_index["quality_options"],
        "quality_grade_options": runtime_index["quality_grade_options"],
        "quality_family_options": runtime_index["quality_family_options"],
        "skill_type_options": runtime_index["skill_type_options"],
        "facet_index": _build_gongfa_facet_index(query_rows),
        "items": [_format_gongfa_search_item(card, score) for score, _index, card in page_rows],
    }


def get_fanxiu_gongfa_card(
    gongfa_id: str | int,
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    requested = str(gongfa_id)
    runtime_index = load_fanxiu_gongfa_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    catalog = runtime_index["catalog"]
    card = runtime_index["cards_by_id"].get(requested)
    if card:
        return {
            "catalog_path": catalog["catalog_path"],
            "card": {
                **card,
                "quality_grade_name": _gongfa_quality_grade_name(card),
                "quality_family_name": _gongfa_quality_family_name(card),
                "terms": _card_terms(card, limit=20),
            },
        }
    raise FanxiuResourceError(f"没有找到功法：{gongfa_id}")


def build_fanxiu_gongfa_catalog(
    *,
    gongfa_rows_path: str | Path | None = None,
    skill_rows_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    gongfa_path = _resolve_export_file(gongfa_rows_path, DEFAULT_GONGFA_ROWS, export_root=export_root)
    skill_path = _resolve_export_file(skill_rows_path, DEFAULT_GONGFA_SKILL_ROWS, export_root=export_root)
    quality_path = root / DEFAULT_GONGFA_PIN_ROWS
    standard_quality_path = root / DEFAULT_QUALITY_ROWS
    item_path = root / DEFAULT_ITEM_ROWS
    faze_resource_path = root / DEFAULT_FAZE_RESOURCE_ROWS
    faze_effect_path = root / DEFAULT_FAZE_EFFECT_RESOURCE_ROWS
    ani_effect_path = root / DEFAULT_ANI_EFFECT_ROWS

    gongfa_rows = _load_json_rows(gongfa_path)
    skill_rows = _load_json_rows(skill_path)
    quality_rows = _load_optional_json_rows(quality_path)
    standard_quality_rows = _load_optional_json_rows(standard_quality_path)
    item_rows = _load_optional_json_rows(item_path)
    faze_resource_rows = _load_optional_json_rows(faze_resource_path)
    faze_effect_rows = _load_optional_json_rows(faze_effect_path)
    ani_effect_rows = _load_optional_json_rows(ani_effect_path)
    items_by_id = {
        str(item_id): row
        for row in item_rows
        if (item_id := row.get("id") or row.get("_row_key")) not in (None, "")
    }
    quality_by_id = {
        quality_id: _compact_quality_row(row)
        for row in quality_rows
        if (quality_id := _as_int(row.get("id"))) is not None
    }
    standard_quality_by_id = {
        quality_id: _compact_standard_quality_row(row)
        for row in standard_quality_rows
        if (quality_id := _as_int(row.get("id"))) is not None
    }
    faze_resource_by_id = {
        faze_id: row
        for row in faze_resource_rows
        if (faze_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    faze_effect_by_id = {
        effect_id: row
        for row in faze_effect_rows
        if (effect_id := _as_int(row.get("id") or row.get("_row_key"))) is not None
    }
    ani_effect_by_reason_id = {
        reason_id: row
        for row in ani_effect_rows
        if (reason_id := _as_int(row.get("reasonId") or row.get("_row_key"))) is not None
    }
    timeline_context = build_timeline_context(root)
    activity_by_id = timeline_context["activity_by_id"]
    item_time_hints_by_id = timeline_context["item_hints_by_id"]
    gongfa_by_id = {
        gongfa_id: row
        for row in gongfa_rows
        if (gongfa_id := _as_int(row.get("id"))) is not None
    }
    progression_tables = {
        table_name: _load_optional_json_rows(root / relative_path)
        for table_name, relative_path in PROGRESSION_TABLES.items()
    }
    progression_alias_rows = _build_progression_alias_rows(progression_tables, skill_rows, item_rows)

    skills_by_origin: dict[int, list[dict[str, Any]]] = defaultdict(list)
    unmatched_skills: list[dict[str, Any]] = []
    for row in skill_rows:
        origin_id = _as_int(row.get("originId"))
        if origin_id is None or origin_id == 0 or origin_id not in gongfa_by_id:
            unmatched_skills.append(row)
            continue
        skills_by_origin[origin_id].append(row)

    progression_by_table: dict[str, dict[int, list[dict[str, Any]]]] = {}
    unmatched_progression_counts: dict[str, int] = {}
    for table_name, rows in progression_tables.items():
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        unmatched = 0
        for row in rows:
            gid = _as_int(row.get("gid"))
            if gid is None or gid not in gongfa_by_id:
                unmatched += 1
                continue
            grouped[gid].append(row)
        progression_by_table[table_name] = grouped
        unmatched_progression_counts[table_name] = unmatched

    cards: list[dict[str, Any]] = []
    overview_rows: list[dict[str, Any]] = []
    skill_tsv_rows: list[dict[str, Any]] = []
    progression_tsv_rows: list[dict[str, Any]] = []
    for gongfa_row in sorted(gongfa_rows, key=_gongfa_sort_key):
        gongfa_id = _as_int(gongfa_row.get("id"))
        skills = sorted(skills_by_origin.get(gongfa_id or -1, []), key=_skill_sort_key)
        compact_skills = [_compact_skill_row(row, standard_quality_by_id) for row in skills]
        base = _compact_gongfa_row(gongfa_row, items_by_id)
        base.update(quality_by_id.get(_as_int(base.get("quality")) or -1, {}))
        time_hints = build_activity_passed_hints(
            base.get("show_condition"),
            activity_by_id,
            source="Gongfa.showCondition",
        )
        for item in base.get("consume_items") or []:
            item_hints = item_time_hints_by_id.get(str(item.get("id") or ""))
            if item_hints:
                time_hints.extend(
                    clone_hints_via_item(
                        item_hints,
                        item_id=item.get("id"),
                        item_name=item.get("name"),
                        relation="consume_item",
                    )
                )
        time_hints = sort_timeline_hints(time_hints, limit=TIMELINE_HINT_LIMIT)
        if time_hints:
            base["time_hints"] = time_hints
            base["first_time_hint"] = first_timeline_hint(time_hints)
        progression = {
            table_name: [
                _compact_progression_row(row, items_by_id, faze_resource_by_id, faze_effect_by_id)
                for row in sorted(progression_by_table.get(table_name, {}).get(gongfa_id or -1, []), key=_progression_sort_key)
            ]
            for table_name in PROGRESSION_TABLES
        }
        card = {
            **base,
            "skill_count": len(compact_skills),
            "skills": compact_skills,
            "progression_counts": {table_name: len(items) for table_name, items in progression.items()},
            "progression": progression,
        }
        cards.append(card)

        first_skill = compact_skills[0] if compact_skills else {}
        progression_counts = {table_name: len(items) for table_name, items in progression.items()}
        overview_rows.append(
            {
                "id": base["id"],
                "name": base["name"],
                "quality": base["quality"],
                "quality_name": base.get("quality_name"),
                "quality_rank": base.get("quality_rank"),
                "quality_type_name": base.get("quality_type_name"),
                "skill_type": base["skill_type"],
                "skill_type_name": base.get("skill_type_name"),
                "icon": base["icon"],
                "consume_items": "、".join(item["name"] for item in base.get("consume_items") or []),
                "skill_count": len(compact_skills),
                **{f"{table_name}_count": count for table_name, count in progression_counts.items()},
                "first_skill": first_skill.get("skill_name") or first_skill.get("name") or "",
                "description_preview": _preview(base["description"]),
                "first_skill_preview": _preview(first_skill.get("describe")),
                "first_time_hint": (base.get("first_time_hint") or {}).get("date") or "",
            }
        )
        for skill in compact_skills:
            skill_tsv_rows.append(
                {
                    "gongfa_id": base["id"],
                    "gongfa_name": base["name"],
                    "skill_row_key": skill["row_key"],
                    "skill_id": skill["id"],
                    "skill_name": skill["skill_name"],
                    "pin": skill["pin"],
                    "quality": skill["quality"],
                    "quality_name": skill["quality_name"],
                    "group": skill["group"],
                    "type": skill["type"],
                    "type_name": skill["type_name"],
                    "sub_type": skill["sub_type"],
                    "sub_type_name": skill["sub_type_name"],
                    "icon": skill["icon"],
                    "describe_preview": _preview(skill["describe"], 260),
                }
            )
        for table_name, items in progression.items():
            for item in items:
                faze_resource = item.get("faze_resource") or {}
                faze_effect = faze_resource.get("effect_resource") if isinstance(faze_resource, dict) else {}
                if not isinstance(faze_effect, dict):
                    faze_effect = {}
                progression_tsv_rows.append(
                    {
                        "table": table_name,
                        "gongfa_id": base["id"],
                        "gongfa_name": base["name"],
                        "row_key": item["row_key"],
                        "id": item["id"],
                        "pin": item["pin"],
                        "jie": item["jie"],
                        "star": item["star"],
                        "grade": item["grade"],
                        "name": item["name"],
                        "skill": item["skill"],
                        "faze_id": item["faze_id"],
                        "faze_resource_name": faze_resource.get("name") if isinstance(faze_resource, dict) else "",
                        "faze_last_grade": faze_resource.get("last_grade") if isinstance(faze_resource, dict) else "",
                        "faze_show_condition": faze_resource.get("show_condition") if isinstance(faze_resource, dict) else "",
                        "faze_effect_id": faze_effect.get("id"),
                        "faze_effect_type": faze_effect.get("type"),
                        "faze_effect_params": faze_effect.get("params"),
                        "faze_resource_tips": "；".join(
                            tip.get("text", "")
                            for tip in ((faze_resource if isinstance(faze_resource, dict) else {}).get("tips") or [])
                            if isinstance(tip, dict)
                        ),
                        "consume": item["consume"],
                        "describe_section_count": len(item.get("describe_sections") or []),
                        "describe_rich_preview": _preview(
                            item.get("describe_rich")
                            or item.get("upgrade_desc_rich")
                            or item.get("top_describe_rich")
                            or item.get("down_describe_rich"),
                            260,
                        ),
                        "describe_preview": _preview(
                            item.get("describe") or item.get("upgrade_desc") or item.get("top_describe") or item.get("down_describe"),
                            260,
                        ),
                    }
                )

    out_dir = root / "parsed_configs" / "gongfa_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = out_dir / "gongfa_catalog.json"
    overview_path = out_dir / "gongfa_overview.tsv"
    skills_path = out_dir / "gongfa_skills.tsv"
    progression_path = out_dir / "gongfa_progression.tsv"
    progression_alias_path = out_dir / "gongfa_progression_aliases.tsv"
    faze_effect_type_path = out_dir / "faze_effect_type_summary.tsv"
    faze_tip_code_path = out_dir / "faze_tip_code_summary.tsv"
    report_path = out_dir / "gongfa_catalog_report.md"
    faze_effect_type_rows = _build_faze_effect_type_summary_rows(faze_effect_rows, faze_resource_rows)
    faze_tip_code_rows = _build_faze_tip_code_summary_rows(
        faze_resource_rows,
        faze_effect_by_id,
        ani_effect_by_reason_id,
    )

    stats = {
        "gongfa_count": len(gongfa_rows),
        "skill_count": len(skill_rows),
        "quality_count": len(quality_rows),
        "standard_quality_count": len(standard_quality_rows),
        "item_count": len(item_rows),
        "faze_resource_count": len(faze_resource_rows),
        "faze_effect_resource_count": len(faze_effect_rows),
        "ani_effect_count": len(ani_effect_rows),
        "faze_effect_type_count": len(faze_effect_type_rows),
        "faze_tip_code_count": len(faze_tip_code_rows),
        "faze_tip_code_with_ani_effect_count": sum(1 for row in faze_tip_code_rows if row.get("has_ani_effect")),
        "linked_skill_count": sum(len(items) for items in skills_by_origin.values()),
        "unmatched_skill_count": len(unmatched_skills),
        "cards_with_skills": sum(1 for card in cards if card["skill_count"]),
        "max_skill_count": max((card["skill_count"] for card in cards), default=0),
        "progression_table_counts": {table_name: len(rows) for table_name, rows in progression_tables.items()},
        "progression_alias_count": sum(1 for row in progression_alias_rows if row["target_gid"]),
        "source_only_progression_count": sum(1 for row in progression_alias_rows if row.get("alias_status") == "source_only"),
        "unresolved_progression_alias_count": sum(
            1 for row in progression_alias_rows if row.get("alias_status") == "unresolved"
        ),
        "linked_progression_counts": {
            table_name: sum(len(items) for items in grouped.values())
            for table_name, grouped in progression_by_table.items()
        },
        "unmatched_progression_counts": unmatched_progression_counts,
        "linked_faze_resource_progression_count": sum(
            1
            for card in cards
            for rows in (card.get("progression") or {}).values()
            for row in rows
            if isinstance(row, dict) and row.get("faze_resource")
        ),
        "linked_faze_effect_resource_count": sum(
            1
            for row in faze_resource_rows
            if (effect_id := _as_int(row.get("effects"))) is not None and effect_id in faze_effect_by_id
        ),
        "linked_faze_effect_progression_count": sum(
            1
            for card in cards
            for rows in (card.get("progression") or {}).values()
            for row in rows
            if isinstance((row.get("faze_resource") or {}).get("effect_resource"), dict)
        ),
        "activity_count": timeline_context["stats"]["activity_count"],
        "item_with_time_hint_count": sum(
            1
            for row in item_rows
            if str(row.get("id") or row.get("_row_key") or "") in item_time_hints_by_id
        ),
        "gongfa_with_time_hint_count": sum(1 for card in cards if card.get("time_hints")),
    }

    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": GONGFA_CATALOG_SCHEMA_VERSION,
                "source": {
                    "gongfa_rows": str(gongfa_path),
                    "skill_rows": str(skill_path),
                    "quality_rows": str(quality_path) if quality_path.is_file() else "",
                    "standard_quality_rows": str(standard_quality_path) if standard_quality_path.is_file() else "",
                    "item_rows": str(item_path) if item_path.is_file() else "",
                    "faze_resource_rows": str(faze_resource_path) if faze_resource_path.is_file() else "",
                    "faze_effect_resource_rows": str(faze_effect_path) if faze_effect_path.is_file() else "",
                    "ani_effect_rows": str(ani_effect_path) if ani_effect_path.is_file() else "",
                    "timeline_rows": [
                        str(root / relative_path)
                        for relative_path in TIMELINE_SOURCE_ROWS
                        if (root / relative_path).is_file()
                    ],
                },
                "stats": stats,
                "progression_aliases": progression_alias_rows,
                "cards": cards,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_tsv(
        overview_path,
        overview_rows,
        [
            "id",
            "name",
            "quality",
            "quality_name",
            "quality_rank",
            "quality_type_name",
            "skill_type",
            "skill_type_name",
            "icon",
            "consume_items",
            "skill_count",
            *[f"{table_name}_count" for table_name in PROGRESSION_TABLES],
            "first_skill",
            "first_time_hint",
            "description_preview",
            "first_skill_preview",
        ],
    )
    _write_tsv(
        skills_path,
        skill_tsv_rows,
        [
            "gongfa_id",
            "gongfa_name",
            "skill_row_key",
            "skill_id",
            "skill_name",
            "pin",
            "quality",
            "quality_name",
            "group",
            "type",
            "type_name",
            "sub_type",
            "sub_type_name",
            "icon",
            "describe_preview",
        ],
    )
    _write_tsv(
        progression_path,
        progression_tsv_rows,
        [
            "table",
            "gongfa_id",
            "gongfa_name",
            "row_key",
            "id",
            "pin",
            "jie",
            "star",
            "grade",
            "name",
            "skill",
            "faze_id",
            "faze_resource_name",
            "faze_last_grade",
            "faze_show_condition",
            "faze_effect_id",
            "faze_effect_type",
            "faze_effect_params",
            "faze_resource_tips",
            "consume",
            "describe_section_count",
            "describe_rich_preview",
            "describe_preview",
        ],
    )
    _write_tsv(
        progression_alias_path,
        progression_alias_rows,
        [
            "source_table",
            "alias_status",
            "source_gid",
            "source_row_count",
            "source_item_count",
            "source_item_names",
            "source_skill_count",
            "source_skill_names",
            "target_table",
            "target_gid",
            "feature_match_count",
            "target_row_count",
            "target_skill_count",
            "target_skill_names",
        ],
    )
    _write_tsv(
        faze_effect_type_path,
        faze_effect_type_rows,
        [
            "type",
            "effect_count",
            "faze_resource_count",
            "effect_ids_sample",
            "faze_ids_sample",
            "faze_names_sample",
            "tips_sample",
            "params_sample",
            "attrs_sample",
        ],
    )
    _write_tsv(
        faze_tip_code_path,
        faze_tip_code_rows,
        [
            "code",
            "tip_count",
            "faze_resource_count",
            "has_ani_effect",
            "ani_effects_sample",
            "ani_base_ids_sample",
            "effect_types_sample",
            "faze_ids_sample",
            "faze_names_sample",
            "texts_sample",
        ],
    )
    report_path.write_text(
        "\n".join(
            [
                "# 凡修功法图鉴结构化报告",
                "",
                f"- 功法：{stats['gongfa_count']}",
                f"- 技能/效果：{stats['skill_count']}",
                f"- 道具：{stats['item_count']}",
                f"- 活动：{stats['activity_count']}",
                f"- 有时间线索的功法：{stats['gongfa_with_time_hint_count']}",
                f"- 有时间线索的道具：{stats['item_with_time_hint_count']}",
                f"- 规则资源：{stats['faze_resource_count']}",
                f"- 规则效果资源：{stats['faze_effect_resource_count']}",
                f"- 顶部特效配置：{stats['ani_effect_count']}",
                f"- 规则效果类型：{stats['faze_effect_type_count']}",
                f"- 规则提示编号：{stats['faze_tip_code_count']}",
                f"- 带顶部特效的规则提示编号：{stats['faze_tip_code_with_ani_effect_count']}",
                f"- 已关联技能/效果：{stats['linked_skill_count']}",
                f"- 未关联技能/效果：{stats['unmatched_skill_count']}",
                f"- 有技能/效果的功法：{stats['cards_with_skills']}",
                f"- 单个功法最多技能/效果：{stats['max_skill_count']}",
                "",
                "## 进阶表",
                "",
                *[
                    f"- {table_name}：{stats['linked_progression_counts'][table_name]}/{stats['progression_table_counts'][table_name]} 已关联"
                    for table_name in PROGRESSION_TABLES
                ],
                "",
                "## 进阶别名链",
                "",
                f"- 已识别别名链：{stats['progression_alias_count']}",
                f"- 源表独立进阶链：{stats['source_only_progression_count']}",
                f"- 暂未识别别名链：{stats['unresolved_progression_alias_count']}",
                f"- 已关联规则资源的进阶行：{stats['linked_faze_resource_progression_count']}",
                f"- 已关联规则效果资源的进阶行：{stats['linked_faze_effect_progression_count']}",
                f"- 规则资源 effects 可直连效果资源：{stats['linked_faze_effect_resource_count']}",
                "- 目前确认：`GongfaJie.feature -> Special-GongfaJie.id -> Special-GongfaJie.gid -> GongfaSkill.originId`。",
                "",
                "关联规则：`GongfaSkill.originId -> Gongfa.id`。",
                "进阶/升星关联规则：各进阶表 `gid -> Gongfa.id`。",
            ]
        ),
        encoding="utf-8",
    )

    return {
        "output_dir": str(out_dir),
        "stats": stats,
        "files": {
            "catalog": str(catalog_path),
            "overview_tsv": str(overview_path),
            "skills_tsv": str(skills_path),
            "progression_tsv": str(progression_path),
            "progression_aliases_tsv": str(progression_alias_path),
            "faze_effect_type_summary_tsv": str(faze_effect_type_path),
            "faze_tip_code_summary_tsv": str(faze_tip_code_path),
            "report": str(report_path),
        },
    }
