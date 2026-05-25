from __future__ import annotations

import csv
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu_item_catalog import load_fanxiu_item_runtime_index
from backend.core.fanxiu_lua_config import load_fanxiu_lang_map, parse_fanxiu_generated_lua_config
from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root
from backend.core.fanxiu_wiki import strip_fanxiu_rich_text


DOUPOTD_CATALOG_SCHEMA_VERSION = 2
DEFAULT_CATALOG = Path("parsed_configs/doupotd_catalog/doupotd_catalog.json")
DEFAULT_TOWER_DEFENSE_DIR_PATTERN = "by_source/lscripts/generate/cfg/doupotowerdefense_*/text_assets"
DEFAULT_CARD_COMPOSE_DIR_PATTERN = "by_source/lscripts/generate/cfg/doupocardcompose_*/text_assets"
DEFAULT_DROP_CONFIG_DIR_PATTERN = "by_source/lscripts/generate/cfg/drop_*/text_assets"
DEFAULT_ITEM_CORNER_PATTERN = "by_source/lscripts/generate/cfg/item_*/text_assets/ItemCorner.lua"
DEFAULT_LANG_PATTERN = "by_source/lscripts/generate/localization/chinese/lang_*/text_assets/lang.lua"
DOUPOTD_EFFECT_TYPE_REQUIRE = "GameSystem.Game.DoupoTD.Core.Fight.SkillEffect.Const.DoupoTDEffectType"
ATTR_FALLBACK_LABELS = {
    "ATK_ASSEMBLY_RATE": "攻击资质",
    "ATK_ASSEMBLY_RATE_TOTAL": "攻击资质",
    "VIOLENT_ADDDAMAGE": "会心伤害加成",
}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FanxiuResourceError(f"斗破图鉴格式不正确：{path}")
    return data


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe_preserve(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _sort_value(value: Any, fallback: int = 10**12) -> int:
    parsed = _as_int(value)
    return parsed if parsed is not None else fallback


def _plain(value: Any) -> str:
    return strip_fanxiu_rich_text(str(value or "")).strip()


def _preview(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", _plain(value))
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _normalize_search_text(value: Any) -> str:
    return str(value or "").lower()


def _extract_terms(*values: Any, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        text = _plain(value)
        for match in re.finditer(r"【([^】]{1,30})】", text):
            term = match.group(1).strip()
            if term and term not in seen:
                seen.add(term)
                terms.append(term)
                if len(terms) >= limit:
                    return terms
    return terms


def _group_by_int(rows: list[dict[str, Any]], field: str) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        value = _as_int(row.get(field))
        if value is None:
            continue
        grouped.setdefault(value, []).append(row)
    return grouped


def _resolve_export_dir(path: str | Path | None, *, export_root: str | Path | None = None) -> Path | None:
    if path is None:
        return None
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(path)
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"目录必须位于导出根目录内：{root}")
    if not resolved.is_dir():
        raise FanxiuResourceError(f"目录不存在：{resolved}")
    return resolved


def _find_default_config_dir(root: Path, pattern: str, label: str) -> Path:
    candidates = [path for path in root.glob(pattern) if path.is_dir()]
    if not candidates:
        raise FanxiuResourceError(f"未找到 {label} 配置目录：{pattern}")
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _find_default_lang_path(root: Path) -> Path | None:
    candidates = [path for path in root.glob(DEFAULT_LANG_PATTERN) if path.is_file()]
    if not candidates:
        candidates = [path for path in root.glob("by_source/**/text_assets/lang.lua") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.stat().st_size, item.stat().st_mtime_ns))


def _parse_config_rows(
    config_dir: Path,
    name: str,
    lang_path: Path | None,
    lang_map: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    path = config_dir / f"{name}.lua"
    if not path.is_file():
        return []
    return list(parse_fanxiu_generated_lua_config(path, lang_path=lang_path, lang_map=lang_map).get("rows") or [])


def _compact_item(card: dict[str, Any] | None, fallback_id: Any = None) -> dict[str, Any] | None:
    if not card:
        if fallback_id is None:
            return None
        return {"id": fallback_id, "name": "", "icon": "", "small_icon": "", "description": ""}
    return {
        "id": card.get("id"),
        "name": card.get("name") or str(card.get("id") or ""),
        "icon": card.get("icon") or "",
        "small_icon": card.get("small_icon") or "",
        "description": card.get("description") or "",
        "description_rich": card.get("description_rich") or card.get("description") or "",
        "quality_name": card.get("quality_name") or "",
    }


def _iter_reward_parts(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        raw_parts = value
    else:
        raw_parts = str(value or "").split(",")
    return [str(part).strip() for part in raw_parts if str(part).strip()]


def _compact_reward_items(value: Any, item_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rewards: list[dict[str, Any]] = []
    for text in _iter_reward_parts(value):
        pieces = text.split("|", 1)
        reward_type = pieces[0].strip()
        item_text = pieces[1] if len(pieces) > 1 else pieces[0]
        item_bits = item_text.split("_")
        item_id_text = item_bits[0] if item_bits else ""
        count_text = item_bits[1] if len(item_bits) > 1 else ""
        extra_mark_text = item_bits[2] if len(item_bits) > 2 else ""
        item_id = item_id_text.strip()
        count = _as_int(count_text.strip()) if count_text else None
        extra_mark = _as_int(extra_mark_text.strip()) if extra_mark_text else None
        item = _compact_item(item_by_id.get(item_id), item_id)
        item_name = item.get("name") if item else item_id
        parsed_item_id = _as_int(item_id)
        rewards.append(
            {
                "type": reward_type,
                "id": parsed_item_id if parsed_item_id is not None else item_id,
                "count": count,
                "extra_mark": extra_mark,
                "item": item,
                "raw": text,
                "text": f"{item_name}x{count}" if item_name and count is not None and count >= 0 else (item_name or text),
            }
        )
    return rewards


def _format_chance(weight: int, total_weight: int) -> str:
    if total_weight <= 0:
        return ""
    value = weight * 100 / total_weight
    return f"{value:.2f}".rstrip("0").rstrip(".") + "%"


def _parse_weighted_card_pool(value: Any) -> tuple[int, list[tuple[int, int]]]:
    text = str(value or "").strip()
    if not text:
        return 0, []
    total_weight = 0
    body = text
    if "#" in text:
        total_text, body = text.split("#", 1)
        total_weight = _as_int(total_text.strip()) or 0
    entries: list[tuple[int, int]] = []
    for part in body.split("|"):
        card_text, _, weight_text = part.strip().partition("_")
        card_id = _as_int(card_text)
        weight = _as_int(weight_text)
        if card_id is None or weight is None:
            continue
        entries.append((card_id, weight))
    if not total_weight:
        total_weight = sum(weight for _card_id, weight in entries)
    return total_weight, entries


def _compact_weighted_card_entry(card: dict[str, Any] | None, card_id: int, weight: int, total_weight: int) -> dict[str, Any]:
    return {
        "card_id": card_id,
        "title": card.get("title") if card else str(card_id),
        "partner_id": card.get("char_id") if card else None,
        "partner_name": card.get("partner_name") if card else "",
        "quality_name": card.get("quality_name") if card else "",
        "star": card.get("star") if card else None,
        "weight": weight,
        "chance_text": _format_chance(weight, total_weight),
    }


def _load_items_by_id(root: Path) -> dict[str, dict[str, Any]]:
    try:
        runtime = load_fanxiu_item_runtime_index(export_root=root, rebuild_missing=False)
    except Exception:
        return {}
    return {
        str(item_id): card
        for item_id, card in (runtime.get("cards_by_id") or {}).items()
        if isinstance(card, dict)
    }


def _attr_meta_maps(attr_rows: list[dict[str, Any]]) -> tuple[dict[tuple[int, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    by_char: dict[tuple[int, str], dict[str, Any]] = {}
    fallback: dict[str, dict[str, Any]] = {}
    for row in attr_rows:
        attr_key = str(row.get("attr") or "").strip()
        char_id = _as_int(row.get("charId"))
        if not attr_key:
            continue
        if char_id is not None:
            by_char[(char_id, attr_key)] = row
        fallback.setdefault(attr_key, row)
    return by_char, fallback


def _format_attr_value(value: Any, meta: dict[str, Any] | None, attr_key: str = "") -> str:
    parsed = _as_int(value)
    if parsed is None:
        return str(value)
    if meta and _as_int(meta.get("ratio")) == 1:
        trans = _as_int(meta.get("attrTrans")) or 10000
        return f"{parsed * 100 / trans:g}%"
    if not meta and (attr_key.endswith("_RATE") or "ADDDAMAGE" in attr_key):
        return f"{parsed / 100:g}%"
    trans = _as_int(meta.get("attrTrans") if meta else None)
    if trans and trans not in {0, 1} and parsed % trans == 0:
        return str(parsed // trans)
    return str(parsed)


def _compact_attr_entries(
    attrs: Any,
    *,
    char_id: int,
    attr_by_char: dict[tuple[int, str], dict[str, Any]],
    attr_fallback: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(attrs, dict):
        return []
    entries: list[dict[str, Any]] = []
    for key, value in sorted(attrs.items(), key=lambda item: str(item[0])):
        attr_key = str(key)
        meta = attr_by_char.get((char_id, attr_key)) or attr_fallback.get(attr_key) or {}
        label = str(meta.get("attrName") or ATTR_FALLBACK_LABELS.get(attr_key) or attr_key)
        formatted = _format_attr_value(value, meta, attr_key)
        entries.append(
            {
                "key": attr_key,
                "label": label,
                "value": value,
                "formatted": formatted,
                "text": f"{label}+{formatted}",
                "sort": _sort_value(meta.get("sort"), 999999),
            }
        )
    entries.sort(key=lambda item: (item["sort"], item["label"], item["key"]))
    return entries


def _compact_skill_show(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "skill_type": row.get("skillType"),
        "skill_title": _plain(row.get("skillTitle")),
        "skill_title_rich": row.get("skillTitle") or "",
        "skill_patch": row.get("skillPatch") or "",
        "skill_icon": row.get("skillIcon") or "",
        "skill_name": row.get("skillName") or "",
        "skill_description": _plain(row.get("skillDes")),
        "skill_description_rich": row.get("skillDes") or "",
    }


def _compact_skill_logic(row: dict[str, Any], skill_runtime_by_id: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    fields = [
        "id",
        "skillType",
        "level",
        "baseSkill",
        "timeLineId",
        "pvpTimeLineId",
        "damage",
        "cd",
        "duration",
        "interval",
        "range",
        "atkRange",
        "buffId",
        "extSkill",
        "bulletCount",
        "bulletSpeed",
        "bulletDuration",
        "maxHit",
    ]
    compact = {field: value for field in fields if (value := row.get(field)) is not None and value != ""}
    runtime = (skill_runtime_by_id or {}).get(_as_int(row.get("id")) or -1)
    if runtime:
        compact["runtime"] = runtime
    return compact


def _is_truthy_cell(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _load_doupotd_buff_flow_index(root: Path) -> dict[str, dict[str, Any]]:
    output_dir = root / "apk_static_index"
    class_rows = _read_tsv_dicts(output_dir / "lua_lscript_module_doupotd_buff_class_flow_classes.tsv")
    if not class_rows:
        return {}
    function_rows = _read_tsv_dicts(output_dir / "lua_lscript_module_doupotd_buff_class_flow_functions.tsv")
    functions_by_class: dict[str, list[dict[str, str]]] = {}
    for row in function_rows:
        class_name = row.get("buff_class") or ""
        if class_name:
            functions_by_class.setdefault(class_name, []).append(row)

    priority = {
        "DoBuffLogic": 0,
        "DoHitTargetAddBuff": 1,
        "TriggerPercentBuff": 2,
        "AddBuffLayer": 3,
        "CheckBuffLayerTrigger": 4,
        "Start": 5,
        "Update": 6,
        "RemoveSelf": 7,
    }
    flow_by_class: dict[str, dict[str, Any]] = {}
    for row in class_rows:
        class_name = row.get("buff_class") or ""
        if not class_name:
            continue
        sorted_functions = sorted(
            functions_by_class.get(class_name, []),
            key=lambda item: (priority.get(str(item.get("function") or ""), 100), _sort_value(item.get("start_line"))),
        )
        key_functions: list[dict[str, Any]] = []
        for function_row in sorted_functions:
            if not (_as_int(function_row.get("step_count")) or 0):
                continue
            key_functions.append(
                {
                    "name": function_row.get("function") or "",
                    "categories": _split_cell_values(function_row.get("categories")),
                    "calls": _split_cell_values(function_row.get("calls"))[:6],
                    "adds_buff": _is_truthy_cell(function_row.get("adds_buff")),
                    "removes_buff": _is_truthy_cell(function_row.get("removes_buff")),
                    "uses_random_gate": _is_truthy_cell(function_row.get("uses_random_gate")),
                    "uses_skill_filter": _is_truthy_cell(function_row.get("uses_skill_filter")),
                    "uses_target_buff_check": _is_truthy_cell(function_row.get("uses_target_buff_check")),
                    "uses_friend_target_expansion": _is_truthy_cell(function_row.get("uses_friend_target_expansion")),
                }
            )
            if len(key_functions) >= 4:
                break
        flow_by_class[class_name] = {
            "hint": row.get("flow_hint") or "",
            "categories": _split_cell_values(row.get("flow_categories")),
            "function_count": _as_int(row.get("function_count")) or 0,
            "flow_step_count": _as_int(row.get("flow_step_count")) or 0,
            "key_functions": key_functions,
        }
    return flow_by_class


def _build_doupotd_skill_runtime_map(
    root: Path,
    *,
    logic_skill_rows: list[dict[str, Any]],
    buff_rows: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    buff_by_id = {
        _as_int(row.get("id")): row
        for row in buff_rows
        if _as_int(row.get("id")) is not None
    }
    type_text = _find_doupotd_type_text(root)
    buff_type_name_by_value = _parse_lua_enum_values(type_text, "SkillBuffType")
    target_type_name_by_value = _parse_lua_enum_values(type_text, "BuffTargetType")
    layer_type_name_by_value = _parse_lua_enum_values(type_text, "BuffLayerType")
    buff_path_by_type_name = _parse_doupotd_buff_path_map(type_text)
    files_by_asset = _find_lscript_files_by_asset(root)
    class_flags: dict[str, list[str]] = {}
    flow_by_class = _load_doupotd_buff_flow_index(root)

    def class_name_for_buff(buff: dict[str, Any]) -> str:
        type_id = _as_int(buff.get("type")) or 0
        type_name = str(buff_type_name_by_value.get(type_id, "None"))
        return _class_asset_from_path(buff_path_by_type_name.get(type_name) or buff_path_by_type_name.get("None", ""))

    def flags_for_class(class_name: str) -> list[str]:
        if not class_name:
            return []
        if class_name in class_flags:
            return class_flags[class_name]
        class_files = files_by_asset.get(f"{class_name}.lua", [])
        text = class_files[0].read_text(encoding="utf-8", errors="ignore") if class_files else ""
        class_flags[class_name] = _doupotd_buff_class_flags(text) if text else []
        return class_flags[class_name]

    def compact_buff(buff_id: int, source_kind: str) -> dict[str, Any]:
        buff = buff_by_id.get(buff_id, {})
        type_id = _as_int(buff.get("type")) or 0
        target_type_id = _as_int(buff.get("targetType")) or 0
        layer_type_id = _as_int(buff.get("effType")) or 0
        type_name = str(buff_type_name_by_value.get(type_id, "None"))
        class_name = class_name_for_buff(buff)
        result = {
            "id": buff_id,
            "source_kind": source_kind,
            "found": bool(buff),
            "type": buff.get("type") or "",
            "type_name": type_name,
            "buff_class": class_name,
            "target_type": buff.get("targetType") or "",
            "target_type_name": target_type_name_by_value.get(target_type_id, ""),
            "trigger_type": buff.get("triggerType") or "",
            "layer_type": buff.get("effType") or "",
            "layer_type_name": layer_type_name_by_value.get(layer_type_id, ""),
            "duration": buff.get("duration") or "",
            "interval": buff.get("interval") or "",
            "damage": buff.get("damage") or "",
            "add_attr": buff.get("addAttr") or "",
            "timeline_id": buff.get("timelineId") or "",
            "trigger_buff_ids": _buff_ids_from_value(buff.get("triggerBuffId")),
            "kill_add_buff_ids": _buff_ids_from_value(buff.get("killAddBuffId")),
            "buff_end_skill_ids": _buff_ids_from_value(buff.get("buffEndSkillId")),
            "semantic_flags": flags_for_class(class_name),
        }
        flow = flow_by_class.get(class_name)
        if flow:
            result["flow"] = flow
        return result

    runtime_by_skill: dict[int, dict[str, Any]] = {}
    for row in logic_skill_rows:
        skill_id = _as_int(row.get("id"))
        if skill_id is None:
            continue
        direct_buff_ids = _buff_ids_from_value(row.get("buffId"))
        secondary_ids: list[int] = []
        for buff_id in direct_buff_ids:
            buff = buff_by_id.get(buff_id, {})
            for field in ("triggerBuffId", "killAddBuffId", "buffEndSkillId"):
                secondary_ids.extend(_buff_ids_from_value(buff.get(field)))
        timeline_ids = _timeline_ids_for_skill(row)
        buffs = [compact_buff(buff_id, "direct") for buff_id in direct_buff_ids]
        buffs.extend(compact_buff(buff_id, "secondary") for buff_id in _dedupe_preserve(secondary_ids))
        if not timeline_ids and not buffs:
            continue
        runtime_by_skill[skill_id] = {
            "timeline_ids": timeline_ids,
            "buff_ids": direct_buff_ids,
            "secondary_buff_ids": _dedupe_preserve(secondary_ids),
            "buffs": buffs,
        }
    return runtime_by_skill


def _compact_strength(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "quality_name": row.get("qualityName_plain") or row.get("qualityName") or "",
        "level": row.get("level"),
        "unlock_description": row.get("unlockDes") or "",
        "skill_patch": row.get("skillPatch") or "",
        "skill_icon": row.get("skillIcon") or "",
        "skill_name": row.get("skillName") or "",
        "skill_description": _plain(row.get("skillDes")),
        "skill_description_rich": row.get("skillDes") or "",
    }


def _build_level_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    sorted_rows = sorted(rows, key=lambda item: _sort_value(item.get("level")))
    first = sorted_rows[0]
    last = sorted_rows[-1]
    return {
        "level_count": len(rows),
        "min_level": first.get("level"),
        "max_level": last.get("level"),
        "level1_attrs": first.get("attr") or {},
        "max_level_attrs": last.get("attr") or {},
        "default_skill": first.get("defaultSkill") or [],
        "default_skill_enhance": first.get("defaultSkillEnhance") or [],
    }


def _compact_compose_card(
    row: dict[str, Any],
    *,
    partner_name: str,
    quality_name_by_id: dict[int, str],
    item_by_id: dict[str, dict[str, Any]],
    attr_by_char: dict[tuple[int, str], dict[str, Any]],
    attr_fallback: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    char_id = _as_int(row.get("charId")) or 0
    item = _compact_item(item_by_id.get(str(row.get("showItem"))), row.get("showItem"))
    quality = _as_int(row.get("quality"))
    quality_name = quality_name_by_id.get(quality or -1) or str(row.get("name_plain") or row.get("name") or "")
    star = _as_int(row.get("star")) or 0
    star_suffix = f"{star}星" if star > 0 else ""
    title = str(item.get("name") if item else "") or f"{quality_name}·{partner_name}{star_suffix}"
    attr_entries = _compact_attr_entries(row.get("attr"), char_id=char_id, attr_by_char=attr_by_char, attr_fallback=attr_fallback)
    return {
        "id": row.get("id"),
        "char_id": char_id,
        "partner_name": partner_name,
        "name": row.get("name_plain") or row.get("name") or "",
        "quality": row.get("quality"),
        "quality_name": quality_name,
        "star": star,
        "title": title,
        "show_item": item,
        "attrs": attr_entries,
        "attr_text": "\n".join(entry["text"] for entry in attr_entries),
    }


def _compact_partner_card(
    row: dict[str, Any],
    *,
    compose_rows: list[dict[str, Any]],
    skill_rows: list[dict[str, Any]],
    logic_skill_rows: list[dict[str, Any]],
    strength_rows: list[dict[str, Any]],
    level_rows: list[dict[str, Any]],
    quality_name_by_id: dict[int, str],
    item_by_id: dict[str, dict[str, Any]],
    attr_by_char: dict[tuple[int, str], dict[str, Any]],
    attr_fallback: dict[str, dict[str, Any]],
    skill_runtime_by_id: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    partner_id = _as_int(row.get("id")) or 0
    compose_cards = [
        _compact_compose_card(
            compose_row,
            partner_name=str(row.get("name") or ""),
            quality_name_by_id=quality_name_by_id,
            item_by_id=item_by_id,
            attr_by_char=attr_by_char,
            attr_fallback=attr_fallback,
        )
        for compose_row in sorted(compose_rows, key=lambda item: (_sort_value(item.get("quality")), _sort_value(item.get("star")), _sort_value(item.get("id"))))
    ]
    skills = [_compact_skill_show(item) for item in sorted(skill_rows, key=lambda item: (_sort_value(item.get("skillType")), _sort_value(item.get("id"))))]
    logic_skills = [
        _compact_skill_logic(item, skill_runtime_by_id)
        for item in sorted(logic_skill_rows, key=lambda item: (_sort_value(item.get("skillType")), _sort_value(item.get("level")), _sort_value(item.get("id"))))
    ]
    strengths = [_compact_strength(item) for item in sorted(strength_rows, key=lambda item: (_sort_value(item.get("level")), _sort_value(item.get("id"))))]
    skill_description_rich = row.get("skillDes") or ""
    return {
        "id": partner_id,
        "name": row.get("name") or "",
        "different": row.get("different") or "",
        "position_type": row.get("positionType"),
        "career_type": row.get("careerType"),
        "positioning": row.get("positioning") or "",
        "model": row.get("model"),
        "quality": row.get("quality"),
        "icon": row.get("icon") or "",
        "big_icon": row.get("bigIcon") or "",
        "head_icon": row.get("headIcon") or "",
        "skill_icon": row.get("skillIcon") or "",
        "skill_name": row.get("skillName") or "",
        "skill_description": _plain(skill_description_rich),
        "skill_description_rich": skill_description_rich,
        "skill_group": row.get("skillGroup"),
        "unlock_level": row.get("unlockLevel"),
        "unlock_level1": row.get("unlockLevel1"),
        "unlock_condition": row.get("unlockCondition") or "",
        "unlock_description": row.get("unLockDesc") or "",
        "unlock_description1": row.get("unLockDesc1") or "",
        "sort": row.get("sort"),
        "can_battle": row.get("canBattle"),
        "damage_proportion": row.get("damageProportion"),
        "change_ration": row.get("changeRation"),
        "light_icon": row.get("lightIcon") or "",
        "draw_effect": row.get("drawEffect") or "",
        "skills": skills,
        "logic_skills": logic_skills,
        "strengths": strengths,
        "level_summary": _build_level_summary(level_rows),
        "compose_cards": compose_cards,
        "compose_card_count": len(compose_cards),
        "skill_count": len(skills),
        "strength_count": len(strengths),
        "terms": _extract_terms(skill_description_rich, *(item.get("skill_description_rich") for item in skills), *(item.get("skill_description_rich") for item in strengths)),
    }


def _compact_draw_card_source(
    row: dict[str, Any],
    *,
    item_by_id: dict[str, dict[str, Any]],
    compose_card_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    total_weight, entries = _parse_weighted_card_pool(row.get("drawCard"))
    item_id = row.get("itemId")
    return {
        "id": row.get("id"),
        "sort": row.get("sort"),
        "item_id": item_id,
        "item": _compact_item(item_by_id.get(str(item_id)), item_id),
        "total_weight": total_weight,
        "entries": [
            _compact_weighted_card_entry(compose_card_by_id.get(card_id), card_id, weight, total_weight)
            for card_id, weight in entries
        ],
        "rewards": _compact_reward_items(row.get("reward"), item_by_id),
    }


def _compact_compose_quality_source(
    row: dict[str, Any],
    *,
    quality_name_by_id: dict[int, str],
    compose_card_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    total_weight, entries = _parse_weighted_card_pool(row.get("composeCard"))
    quality = _as_int(row.get("quality"))
    return {
        "id": row.get("id"),
        "quality": row.get("quality"),
        "quality_name": quality_name_by_id.get(quality or -1) or "",
        "total_weight": total_weight,
        "entries": [
            _compact_weighted_card_entry(compose_card_by_id.get(card_id), card_id, weight, total_weight)
            for card_id, weight in entries
        ],
    }


def _compact_compose_progress(row: dict[str, Any], item_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "progress": row.get("progress"),
        "rewards": _compact_reward_items(row.get("reward"), item_by_id),
    }


def _compact_compose_book_entry(
    row: dict[str, Any],
    *,
    quality_name_by_id: dict[int, str],
    compose_card_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    card_id = _as_int(row.get("cardId"))
    quality = _as_int(row.get("quality"))
    card = compose_card_by_id.get(card_id or 0)
    return {
        "id": row.get("id"),
        "quality": row.get("quality"),
        "quality_name": quality_name_by_id.get(quality or -1) or "",
        "sort": row.get("sort"),
        "card_id": row.get("cardId"),
        "title": card.get("title") if card else str(row.get("cardId") or ""),
        "partner_id": card.get("char_id") if card else None,
        "partner_name": card.get("partner_name") if card else "",
    }


def _filter_source_entries_for_partner(source: dict[str, Any], partner_id: int) -> dict[str, Any] | None:
    entries = [entry for entry in source.get("entries") or [] if _as_int(entry.get("partner_id")) == partner_id]
    if not entries:
        return None
    return {
        **{key: value for key, value in source.items() if key != "entries"},
        "entries": entries,
    }


def _attach_partner_source_summaries(
    cards: list[dict[str, Any]],
    *,
    draw_sources: list[dict[str, Any]],
    compose_quality_sources: list[dict[str, Any]],
    compose_book_entries: list[dict[str, Any]],
    compose_progress_rewards: list[dict[str, Any]],
) -> None:
    for card in cards:
        partner_id = _as_int(card.get("id")) or 0
        card["draw_sources"] = [
            source
            for source in (_filter_source_entries_for_partner(source, partner_id) for source in draw_sources)
            if source
        ]
        card["compose_quality_sources"] = [
            source
            for source in (_filter_source_entries_for_partner(source, partner_id) for source in compose_quality_sources)
            if source
        ]
        card["compose_book_entries"] = [
            entry
            for entry in compose_book_entries
            if _as_int(entry.get("partner_id")) == partner_id
        ]
        card["compose_progress_rewards"] = compose_progress_rewards


def _source_files(root: Path, tower_dir: Path, card_dir: Path, lang_path: Path | None) -> list[Path]:
    names_by_dir = {
        tower_dir: ["CharacterMainInfo", "CharacterLevel", "CharacterSkillInfo", "CharacterSkillShow", "CharacterSkillStrength", "AttrName"],
        card_dir: ["ComposeCard", "ComposeType", "ComposeProgress", "ComposePartnerChoose", "ComposeCardQuality", "DrawCard", "ConfigValue", "ComposeBook"],
    }
    files: list[Path] = []
    for config_dir, names in names_by_dir.items():
        files.extend(path for name in names if (path := config_dir / f"{name}.lua").is_file())
    if lang_path and lang_path.is_file() and _is_relative_to(lang_path.resolve(), root):
        files.append(lang_path)
    return files


def _default_catalog_source_files(root: Path) -> list[Path]:
    try:
        tower_dir = _find_default_config_dir(root, DEFAULT_TOWER_DEFENSE_DIR_PATTERN, "DoupoTowerDefense")
        card_dir = _find_default_config_dir(root, DEFAULT_CARD_COMPOSE_DIR_PATTERN, "DoupoCardCompose")
    except FanxiuResourceError:
        return []
    return _source_files(root, tower_dir, card_dir, _find_default_lang_path(root))


def _is_default_catalog_stale(catalog_path: Path, root: Path) -> bool:
    if not catalog_path.is_file():
        return True
    try:
        data = _read_json(catalog_path)
    except Exception:
        return True
    if data.get("schema_version") != DOUPOTD_CATALOG_SCHEMA_VERSION:
        return True
    catalog_mtime_ns = catalog_path.stat().st_mtime_ns
    return any(path.is_file() and path.stat().st_mtime_ns > catalog_mtime_ns for path in _default_catalog_source_files(root))


def _resolve_catalog_file(export_root: str | Path | None = None, *, rebuild_missing: bool = True) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    path = root / DEFAULT_CATALOG
    if rebuild_missing and _is_default_catalog_stale(path, root):
        build_fanxiu_doupotd_catalog(export_root=export_root)
    if not path.is_file():
        raise FanxiuResourceError(f"斗破图鉴尚未生成：{path}")
    return path


def _build_search_doc(card: dict[str, Any], index: int) -> dict[str, Any]:
    text_parts = [
        card.get("id"),
        card.get("name"),
        card.get("positioning"),
        card.get("skill_name"),
        card.get("skill_description"),
        card.get("unlock_description"),
        card.get("unlock_description1"),
        " ".join(card.get("terms") or []),
    ]
    text_parts.extend(item.get("skill_name") for item in card.get("skills") or [])
    text_parts.extend(item.get("skill_description") for item in card.get("skills") or [])
    text_parts.extend(item.get("skill_name") for item in card.get("strengths") or [])
    text_parts.extend(item.get("skill_description") for item in card.get("strengths") or [])
    text_parts.extend(item.get("title") for item in card.get("compose_cards") or [])
    return {
        "index": index,
        "card": card,
        "combined": _normalize_search_text(" ".join(str(item or "") for item in text_parts)),
        "name": _normalize_search_text(card.get("name")),
    }


def _score_doc(doc: dict[str, Any], terms: tuple[str, ...]) -> int:
    if not terms:
        return 1
    if not all(term in doc["combined"] for term in terms):
        return 0
    score = 0
    for term in terms:
        if doc["name"] == term:
            score += 100
        if term in doc["name"]:
            score += 60
        score += 8 + min(doc["combined"].count(term), 8)
    return score


def _format_search_item(card: dict[str, Any], score: int) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "name": card.get("name"),
        "icon": card.get("icon"),
        "head_icon": card.get("head_icon"),
        "big_icon": card.get("big_icon"),
        "positioning": card.get("positioning"),
        "career_type": card.get("career_type"),
        "position_type": card.get("position_type"),
        "skill_name": card.get("skill_name"),
        "skill_description_preview": _preview(card.get("skill_description")),
        "compose_card_count": card.get("compose_card_count") or 0,
        "skill_count": card.get("skill_count") or 0,
        "strength_count": card.get("strength_count") or 0,
        "terms": card.get("terms") or [],
        "score": score,
    }


@lru_cache(maxsize=8)
def _load_catalog_cached(path_text: str, mtime_ns: int, size: int, export_root_text: str) -> dict[str, Any]:
    catalog = _read_json(Path(path_text))
    cards = [card for card in catalog.get("cards") or [] if isinstance(card, dict)]
    return {
        "catalog": {
            **catalog,
            "export_root": export_root_text,
            "catalog_path": path_text,
        },
        "cards": cards,
        "cards_by_id": {str(card.get("id")): card for card in cards},
        "search_docs": [_build_search_doc(card, index) for index, card in enumerate(cards)],
    }


def load_fanxiu_doupotd_runtime_index(
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    catalog_path = _resolve_catalog_file(export_root, rebuild_missing=rebuild_missing)
    root = resolve_fanxiu_export_root(export_root)
    stat = catalog_path.stat()
    return _load_catalog_cached(str(catalog_path), stat.st_mtime_ns, stat.st_size, str(root))


def search_fanxiu_doupotd_partner_cards(
    *,
    query: str = "",
    limit: int = 80,
    offset: int = 0,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    runtime = load_fanxiu_doupotd_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    catalog = runtime["catalog"]
    terms = tuple(item.strip().lower() for item in re.split(r"\s+", query or "") if item.strip())
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for doc in runtime["search_docs"]:
        score = _score_doc(doc, terms)
        if score <= 0:
            continue
        scored.append((score, int(doc["index"]), doc["card"]))
    if terms:
        scored.sort(key=lambda item: (-item[0], _sort_value(item[2].get("sort")), _sort_value(item[2].get("id"))))
    else:
        scored.sort(key=lambda item: (_sort_value(item[2].get("sort")), _sort_value(item[2].get("id")), item[1]))
    page_rows = scored[offset: offset + limit]
    return {
        "query": query,
        "limit": limit,
        "offset": offset,
        "total": len(scored),
        "stats": catalog.get("stats") or {},
        "catalog_path": catalog["catalog_path"],
        "items": [_format_search_item(card, score) for score, _index, card in page_rows],
    }


def get_fanxiu_doupotd_partner_card(
    partner_id: str | int,
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    requested = str(partner_id)
    runtime = load_fanxiu_doupotd_runtime_index(export_root=export_root, rebuild_missing=rebuild_missing)
    card = runtime["cards_by_id"].get(requested)
    if not card:
        raise FanxiuResourceError(f"没有找到斗破角色：{partner_id}")
    return {
        "catalog_path": runtime["catalog"]["catalog_path"],
        "card": card,
    }


def _resolve_lang_path(root: Path, lang_path: str | Path | None) -> Path | None:
    resolved_lang_path = Path(lang_path).expanduser().resolve() if lang_path else _find_default_lang_path(root)
    if resolved_lang_path and not _is_relative_to(resolved_lang_path, root):
        raise FanxiuResourceError(f"语言文件必须位于导出根目录内：{root}")
    if resolved_lang_path and not resolved_lang_path.is_file():
        raise FanxiuResourceError(f"语言文件不存在：{resolved_lang_path}")
    return resolved_lang_path


def _find_timeline_lua_files(root: Path) -> tuple[dict[int, list[Path]], dict[int, list[Path]]]:
    lscript_root = root / "by_source" / "lscripts"
    all_named: dict[int, list[Path]] = {}
    doupotd_named: dict[int, list[Path]] = {}
    if not lscript_root.is_dir():
        return all_named, doupotd_named
    pattern = re.compile(r"^(?P<id>\d+)(?:__[-\d]+)?\.lua$")
    for path in lscript_root.rglob("*.lua"):
        match = pattern.match(path.name)
        if not match:
            continue
        timeline_id = int(match.group("id"))
        all_named.setdefault(timeline_id, []).append(path)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if DOUPOTD_EFFECT_TYPE_REQUIRE in text:
            doupotd_named.setdefault(timeline_id, []).append(path)
    for paths in all_named.values():
        paths.sort(key=lambda item: ("__" in item.name, str(item)))
    for paths in doupotd_named.values():
        paths.sort(key=lambda item: ("__" in item.name, str(item)))
    return all_named, doupotd_named


def _find_doupotd_effect_class_map(root: Path) -> dict[str, str]:
    lscript_root = root / "by_source" / "lscripts"
    if not lscript_root.is_dir():
        return {}
    for path in lscript_root.rglob("DoupoTDEffectType*.lua"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = re.search(r"_M\.EffectClass\s*=\s*\{(?P<body>.*?)\}\s*_M\.", text, flags=re.S)
        if not match:
            continue
        return {
            key: value
            for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([^\"]+)\"", match.group("body"))
        }
    return {}


def _find_doupotd_type_text(root: Path) -> str:
    lscript_root = root / "by_source" / "lscripts"
    if not lscript_root.is_dir():
        return ""
    for path in lscript_root.rglob("DoupoTDType*.lua"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "_M.SkillBuffType" in text and "_M.BuffPath" in text:
            return text
    return ""


def _parse_lua_enum_values(text: str, enum_name: str) -> dict[int | str, str]:
    match = re.search(rf"_M\.{re.escape(enum_name)}\s*=\s*\{{(?P<body>.*?)\}}", text, flags=re.S)
    if not match:
        return {}
    values: dict[int | str, str] = {}
    for key, raw_value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^,\n]+)", match.group("body")):
        value_text = raw_value.strip().strip('"')
        parsed = _as_int(value_text)
        values[parsed if parsed is not None else value_text] = key
    return values


def _parse_doupotd_buff_path_map(type_text: str) -> dict[str, str]:
    return {
        type_name: class_path
        for type_name, class_path in re.findall(
            r"\[_M\.SkillBuffType\.([A-Za-z_][A-Za-z0-9_]*)\]\s*=\s*\"([^\"]+)\"",
            type_text,
        )
    }


def _find_lscript_files_by_asset(root: Path) -> dict[str, list[Path]]:
    lscript_root = root / "by_source" / "lscripts"
    files_by_name: dict[str, list[Path]] = {}
    if not lscript_root.is_dir():
        return files_by_name
    for path in lscript_root.rglob("*.lua"):
        files_by_name.setdefault(path.name, []).append(path)
    for paths in files_by_name.values():
        paths.sort(key=lambda item: str(item))
    return files_by_name


def _parse_doupotd_timeline_file(path: Path, effect_class_map: dict[str, str]) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    sections: list[str] = []
    effect_entries: list[dict[str, Any]] = []
    current_section = ""
    for line_no, line in enumerate(text.splitlines(), start=1):
        section_match = re.search(r"\[EffectType\.SkillEffect\.([A-Za-z_][A-Za-z0-9_]*)\]", line)
        if section_match:
            current_section = section_match.group(1)
            sections.append(current_section)
        class_match = re.search(r"class\s*=\s*EffectType\.EffectClass\.([A-Za-z_][A-Za-z0-9_]*)", line)
        if class_match:
            class_key = class_match.group(1)
            effect_entries.append(
                {
                    "line": line_no,
                    "section": current_section,
                    "class_key": class_key,
                    "class_name": effect_class_map.get(class_key, class_key),
                }
            )
    return {
        "timeline_file": path,
        "sections": _dedupe_preserve(sections),
        "res_paths": _dedupe_preserve(re.findall(r"resPath\s*=\s*\"([^\"]+)\"", text)),
        "audios": _dedupe_preserve(re.findall(r"\baudio\s*=\s*(\d+)", text)),
        "effect_entries": effect_entries,
        "class_keys": _dedupe_preserve([entry["class_key"] for entry in effect_entries]),
        "class_names": _dedupe_preserve([entry["class_name"] for entry in effect_entries]),
    }


def _timeline_ids_for_skill(row: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for field in ("timeLineId", "pvpTimeLineId", "yuanyaoTimeLine"):
        timeline_id = _as_int(row.get(field))
        if timeline_id is not None:
            ids.append(timeline_id)
    return _dedupe_preserve(ids)


def _format_list_value(value: Any) -> str:
    if isinstance(value, list):
        return "|".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else str(value)


def _write_doupotd_skill_timeline_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    sample_skill_rows: list[dict[str, Any]],
    sample_timeline_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# doupotd skill timeline link report",
        "",
        "This is a static, read-only link map from `DoupoTowerDefense.CharacterSkillInfo` to `Generate.Timeline.Doupo.Config.<timelineId>` Lua configs and their runtime effect classes.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Key Runtime Chain",
            "",
            "- `DoupoTDSkillActor:SkillCreator` reads `skillCfg.timeLineId` or `skillCfg.pvpTimeLineId` and calls `skill:AddSkillEffectClassPath(timelineId)`.",
            "- `DoupoTDBaseSkill:AddSkillEffectClassPath` requires `Generate.Timeline.Doupo.Config.<timelineId>`, reads `EffectType.SkillEffect.*` buckets, and turns each timeline `class` into `GameSystem.Game.DoupoTD.Core.Fight.SkillEffect.Effect.<class>`.",
            "- `DoupoTDEffectType.EffectClass` maps compact timeline names such as `BoomEffect` to actual classes such as `DoupoTDBoomEffect`.",
            "- `buffId` and `extSkill` are adjacent links: `buffId` is added through `DoupoTDSkillActor:AddBuffData` / `DoupoTDBaseSkill:AddBuffData`, while `extSkill` becomes an attached skill via `AddToAttachedSkill`.",
            "",
            "## Sample Skill Rows",
            "",
        ]
    )
    for row in sample_skill_rows[:12]:
        lines.append(
            f"- `{row.get('skill_id')}` `{row.get('partner_name')}` timeline `{row.get('timeline_ids')}` -> `{row.get('effect_classes')}`"
        )
    lines.extend(["", "## Sample Timeline Rows", ""])
    for row in sample_timeline_rows[:12]:
        lines.append(
            f"- `{row.get('timeline_id')}` `{row.get('timeline_files')}` sections `{row.get('sections')}` classes `{row.get('effect_classes')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This report proves static client wiring from character skill config to client-side visual/effect classes. It does not prove server-side damage authority or final combat formula ownership.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_skill_timeline_probe(
    *,
    tower_defense_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    tower_dir = _resolve_export_dir(tower_defense_config_dir, export_root=export_root) or _find_default_config_dir(
        root,
        DEFAULT_TOWER_DEFENSE_DIR_PATTERN,
        "DoupoTowerDefense",
    )
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    partner_rows = _parse_config_rows(tower_dir, "CharacterMainInfo", resolved_lang_path, lang_map)
    skill_rows = _parse_config_rows(tower_dir, "CharacterSkillInfo", resolved_lang_path, lang_map)
    partner_name_by_id = {
        _as_int(row.get("id")): str(row.get("name") or "")
        for row in partner_rows
        if _as_int(row.get("id")) is not None
    }
    all_timeline_files, doupotd_timeline_files = _find_timeline_lua_files(root)
    effect_class_map = _find_doupotd_effect_class_map(root)
    files_by_asset = _find_lscript_files_by_asset(root)

    requested_timeline_ids = sorted({_as_int(row.get(field)) for row in skill_rows for field in ("timeLineId", "pvpTimeLineId", "yuanyaoTimeLine")} - {None})
    timeline_rows: list[dict[str, Any]] = []
    effect_rows: list[dict[str, Any]] = []
    timeline_summary_by_id: dict[int, dict[str, Any]] = {}
    exact_collision_ids: set[int] = set()
    for timeline_id in requested_timeline_ids:
        files = doupotd_timeline_files.get(int(timeline_id), [])
        if any(path.name == f"{timeline_id}.lua" for path in all_timeline_files.get(int(timeline_id), [])) and not any(path.name == f"{timeline_id}.lua" for path in files):
            exact_collision_ids.add(int(timeline_id))
        parsed_files = [_parse_doupotd_timeline_file(path, effect_class_map) for path in files]
        class_names = _dedupe_preserve([class_name for item in parsed_files for class_name in item["class_names"]])
        sections = _dedupe_preserve([section for item in parsed_files for section in item["sections"]])
        res_paths = _dedupe_preserve([res_path for item in parsed_files for res_path in item["res_paths"]])
        timeline_row = {
            "timeline_id": timeline_id,
            "timeline_file_count": len(files),
            "timeline_files": "|".join(str(path.relative_to(root)) for path in files),
            "has_exact_doupotd_file": any(path.name == f"{timeline_id}.lua" for path in files),
            "has_exact_non_doupotd_collision": int(timeline_id) in exact_collision_ids,
            "sections": "|".join(sections),
            "effect_classes": "|".join(class_names),
            "effect_class_file_count": sum(len(files_by_asset.get(f"{class_name}.lua", [])) for class_name in class_names),
            "res_paths": "|".join(res_paths[:12]),
        }
        timeline_rows.append(timeline_row)
        timeline_summary_by_id[int(timeline_id)] = timeline_row
        for parsed in parsed_files:
            for entry in parsed["effect_entries"]:
                class_name = entry["class_name"]
                effect_files = files_by_asset.get(f"{class_name}.lua", [])
                effect_rows.append(
                    {
                        "timeline_id": timeline_id,
                        "timeline_file": str(parsed["timeline_file"].relative_to(root)),
                        "line": entry["line"],
                        "section": entry["section"],
                        "class_key": entry["class_key"],
                        "class_name": class_name,
                        "effect_files": "|".join(str(path.relative_to(root)) for path in effect_files[:6]),
                        "effect_file_count": len(effect_files),
                    }
                )

    skill_link_rows: list[dict[str, Any]] = []
    for row in sorted(skill_rows, key=lambda item: (_sort_value(item.get("charId")), _sort_value(item.get("skillType")), _sort_value(item.get("id")))):
        timeline_ids = _timeline_ids_for_skill(row)
        linked_timeline_rows = [timeline_summary_by_id.get(timeline_id, {}) for timeline_id in timeline_ids]
        effect_classes = _dedupe_preserve(
            [
                class_name
                for timeline_row in linked_timeline_rows
                for class_name in str(timeline_row.get("effect_classes") or "").split("|")
                if class_name
            ]
        )
        skill_link_rows.append(
            {
                "skill_id": row.get("id"),
                "partner_id": row.get("charId"),
                "partner_name": partner_name_by_id.get(_as_int(row.get("charId")), ""),
                "skill_group": row.get("skillGroup"),
                "level": row.get("level"),
                "skill_type": row.get("skillType"),
                "timeline_ids": "|".join(str(item) for item in timeline_ids),
                "missing_timeline_ids": "|".join(str(item) for item in timeline_ids if item not in timeline_summary_by_id),
                "effect_classes": "|".join(effect_classes),
                "buff_ids": _format_list_value(row.get("buffId")),
                "ext_skill": row.get("extSkill") or "",
                "related_skill_group": row.get("relatedSkillGroup") or "",
                "related_skill_type": row.get("relatedSkillType") or "",
                "attach_skill_start_type": row.get("attachSkillStartType") or "",
                "damage": row.get("damage") or "",
                "cd": row.get("cd") or "",
                "duration": row.get("duration") or "",
                "interval": row.get("interval") or "",
                "range": row.get("range") or "",
            }
        )

    stats = {
        "skill_row_count": len(skill_rows),
        "partner_count": len(partner_rows),
        "requested_timeline_count": len(requested_timeline_ids),
        "timeline_found_count": sum(1 for row in timeline_rows if row["timeline_file_count"]),
        "timeline_missing_count": sum(1 for row in timeline_rows if not row["timeline_file_count"]),
        "effect_entry_count": len(effect_rows),
        "effect_class_count": len({row["class_name"] for row in effect_rows}),
        "effect_class_map_count": len(effect_class_map),
        "exact_non_doupotd_collision_count": len(exact_collision_ids),
        "skills_with_buff_ids": sum(1 for row in skill_rows if row.get("buffId")),
        "skills_with_ext_skill": sum(1 for row in skill_rows if row.get("extSkill")),
    }
    verdict = {
        "all_character_skill_timelines_have_doupotd_config": stats["timeline_missing_count"] == 0,
        "effect_class_map_found": bool(effect_class_map),
        "hash_suffixed_timeline_exports_present": any("__" in path.name for paths in doupotd_timeline_files.values() for path in paths),
        "exact_filename_collisions_present": bool(exact_collision_ids),
        "static_client_effect_wiring_only": True,
    }

    output_dir = root / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    skill_tsv = output_dir / "lua_lscript_module_doupotd_skill_timeline_links.tsv"
    timeline_tsv = output_dir / "lua_lscript_module_doupotd_skill_timeline_timelines.tsv"
    effects_tsv = output_dir / "lua_lscript_module_doupotd_skill_timeline_effects.tsv"
    report_path = output_dir / "lua_lscript_module_doupotd_skill_timeline_link_report.md"
    json_path = output_dir / "lua_lscript_module_doupotd_skill_timeline_link_report.json"
    _write_tsv(
        skill_tsv,
        skill_link_rows,
        [
            "skill_id",
            "partner_id",
            "partner_name",
            "skill_group",
            "level",
            "skill_type",
            "timeline_ids",
            "missing_timeline_ids",
            "effect_classes",
            "buff_ids",
            "ext_skill",
            "related_skill_group",
            "related_skill_type",
            "attach_skill_start_type",
            "damage",
            "cd",
            "duration",
            "interval",
            "range",
        ],
    )
    _write_tsv(
        timeline_tsv,
        timeline_rows,
        [
            "timeline_id",
            "timeline_file_count",
            "timeline_files",
            "has_exact_doupotd_file",
            "has_exact_non_doupotd_collision",
            "sections",
            "effect_classes",
            "effect_class_file_count",
            "res_paths",
        ],
    )
    _write_tsv(
        effects_tsv,
        effect_rows,
        [
            "timeline_id",
            "timeline_file",
            "line",
            "section",
            "class_key",
            "class_name",
            "effect_files",
            "effect_file_count",
        ],
    )
    _write_doupotd_skill_timeline_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        sample_skill_rows=skill_link_rows,
        sample_timeline_rows=timeline_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "verdict": verdict,
                "files": {
                    "skill_links": str(skill_tsv),
                    "timelines": str(timeline_tsv),
                    "effects": str(effects_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "skill_links": str(skill_tsv),
            "timelines": str(timeline_tsv),
            "effects": str(effects_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _buff_ids_from_value(value: Any) -> list[int]:
    if isinstance(value, list):
        return [item for item in (_as_int(item) for item in value) if item is not None]
    parsed = _as_int(value)
    return [parsed] if parsed is not None and parsed > 0 else []


def _class_asset_from_path(class_path: str) -> str:
    return class_path.rsplit(".", 1)[-1] if class_path else ""


def _write_doupotd_buff_effect_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    sample_link_rows: list[dict[str, Any]],
    sample_buff_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# doupotd buff effect link report",
        "",
        "This is a static, read-only link map from `CharacterSkillInfo.buffId` to `BuffEffect` rows and `DoupoTDType.BuffPath` runtime buff classes.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Key Runtime Chain",
            "",
            "- `DoupoTDSkillActor:AddSkill` reads `skillCfg.buffId` and calls `AddBuffData(skillId, buffId)` for ordinary skills.",
            "- `DoupoTDBaseSkill:AddBuffData` loads `DoupoTowerDefense_BuffEffect` by id, then either applies immediate passive/self buffs or records release/hit buff ids for later timeline effects.",
            "- `DoupoTDPartnerView:AddBuff` and `DoupoTDBotView:AddBuff` create the runtime buff class via `DoupoTDType.BuffPath[buffCfg.type]`, falling back to `SkillBuffType.None` when no path is registered.",
            "",
            "## Sample Skill Buff Links",
            "",
        ]
    )
    for row in sample_link_rows[:12]:
        lines.append(
            f"- skill `{row.get('skill_id')}` `{row.get('partner_name')}` -> buff `{row.get('buff_id')}` `{row.get('buff_type_name')}` `{row.get('buff_class')}`"
        )
    lines.extend(["", "## Sample Buff Rows", ""])
    for row in sample_buff_rows[:12]:
        lines.append(
            f"- buff `{row.get('buff_id')}` type `{row.get('buff_type_name')}` trigger `{row.get('trigger_type')}` target `{row.get('target_type_name')}` class `{row.get('buff_class')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This report maps client-side buff config and class dispatch. It does not prove final damage authority, because server-side validation/final arithmetic may still own authoritative combat results.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_buff_effect_probe(
    *,
    tower_defense_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    tower_dir = _resolve_export_dir(tower_defense_config_dir, export_root=export_root) or _find_default_config_dir(
        root,
        DEFAULT_TOWER_DEFENSE_DIR_PATTERN,
        "DoupoTowerDefense",
    )
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    partner_rows = _parse_config_rows(tower_dir, "CharacterMainInfo", resolved_lang_path, lang_map)
    skill_rows = _parse_config_rows(tower_dir, "CharacterSkillInfo", resolved_lang_path, lang_map)
    buff_rows = _parse_config_rows(tower_dir, "BuffEffect", resolved_lang_path, lang_map)
    partner_name_by_id = {
        _as_int(row.get("id")): str(row.get("name") or "")
        for row in partner_rows
        if _as_int(row.get("id")) is not None
    }
    buff_by_id = {
        _as_int(row.get("id")): row
        for row in buff_rows
        if _as_int(row.get("id")) is not None
    }
    type_text = _find_doupotd_type_text(root)
    buff_type_name_by_value = _parse_lua_enum_values(type_text, "SkillBuffType")
    target_type_name_by_value = _parse_lua_enum_values(type_text, "BuffTargetType")
    layer_type_name_by_value = _parse_lua_enum_values(type_text, "BuffLayerType")
    buff_path_by_type_name = _parse_doupotd_buff_path_map(type_text)
    files_by_asset = _find_lscript_files_by_asset(root)

    link_rows: list[dict[str, Any]] = []
    direct_buff_ids: set[int] = set()
    for row in sorted(skill_rows, key=lambda item: (_sort_value(item.get("charId")), _sort_value(item.get("skillType")), _sort_value(item.get("id")))):
        for buff_id in _buff_ids_from_value(row.get("buffId")):
            direct_buff_ids.add(buff_id)
            buff = buff_by_id.get(buff_id, {})
            type_id = _as_int(buff.get("type")) or 0
            type_name = str(buff_type_name_by_value.get(type_id, "None"))
            class_path = buff_path_by_type_name.get(type_name) or buff_path_by_type_name.get("None", "")
            class_asset = _class_asset_from_path(class_path)
            link_rows.append(
                {
                    "skill_id": row.get("id"),
                    "partner_id": row.get("charId"),
                    "partner_name": partner_name_by_id.get(_as_int(row.get("charId")), ""),
                    "skill_type": row.get("skillType"),
                    "buff_id": buff_id,
                    "buff_found": bool(buff),
                    "buff_type_id": type_id,
                    "buff_type_name": type_name,
                    "buff_class": class_asset,
                    "trigger_type": buff.get("triggerType") or "",
                    "target_type": buff.get("targetType") or "",
                    "duration": buff.get("duration") or "",
                    "damage": buff.get("damage") or "",
                    "add_attr": buff.get("addAttr") or "",
                }
            )

    secondary_buff_ids: set[int] = set()
    for buff_id in sorted(direct_buff_ids):
        buff = buff_by_id.get(buff_id, {})
        for field in ("triggerBuffId", "killAddBuffId", "buffEndSkillId"):
            secondary_buff_ids.update(_buff_ids_from_value(buff.get(field)))
    selected_buff_ids = sorted(direct_buff_ids | secondary_buff_ids)

    buff_effect_rows: list[dict[str, Any]] = []
    for buff_id in selected_buff_ids:
        buff = buff_by_id.get(buff_id, {})
        type_id = _as_int(buff.get("type")) or 0
        target_type_id = _as_int(buff.get("targetType")) or 0
        layer_type_id = _as_int(buff.get("effType")) or 0
        type_name = str(buff_type_name_by_value.get(type_id, "None"))
        class_path = buff_path_by_type_name.get(type_name) or buff_path_by_type_name.get("None", "")
        class_asset = _class_asset_from_path(class_path)
        class_files = files_by_asset.get(f"{class_asset}.lua", []) if class_asset else []
        buff_effect_rows.append(
            {
                "buff_id": buff_id,
                "source_kind": "direct_skill_buff" if buff_id in direct_buff_ids else "secondary_buff",
                "buff_found": bool(buff),
                "buff_type_id": type_id,
                "buff_type_name": type_name,
                "buff_class_path": class_path,
                "buff_class": class_asset,
                "buff_class_file_count": len(class_files),
                "buff_class_files": "|".join(str(path.relative_to(root)) for path in class_files[:6]),
                "target_type": buff.get("targetType") or "",
                "target_type_name": target_type_name_by_value.get(target_type_id, ""),
                "trigger_type": buff.get("triggerType") or "",
                "layer_type": buff.get("effType") or "",
                "layer_type_name": layer_type_name_by_value.get(layer_type_id, ""),
                "passive": buff.get("passive") or "",
                "duration": buff.get("duration") or "",
                "interval": buff.get("interval") or "",
                "plies_limit": buff.get("pliesLimit") or "",
                "damage": buff.get("damage") or "",
                "add_attr": buff.get("addAttr") or "",
                "timeline_id": buff.get("timelineId") or "",
                "trigger_buff_id": _format_list_value(buff.get("triggerBuffId")),
                "kill_add_buff_id": _format_list_value(buff.get("killAddBuffId")),
                "buff_end_skill_id": _format_list_value(buff.get("buffEndSkillId")),
            }
        )

    stats = {
        "skill_row_count": len(skill_rows),
        "skills_with_buff_ids": sum(1 for row in skill_rows if row.get("buffId")),
        "direct_skill_buff_link_count": len(link_rows),
        "direct_skill_buff_id_count": len(direct_buff_ids),
        "secondary_buff_id_count": len(secondary_buff_ids),
        "selected_buff_effect_count": len(buff_effect_rows),
        "missing_buff_effect_count": sum(1 for row in buff_effect_rows if not row["buff_found"]),
        "buff_type_map_count": len(buff_type_name_by_value),
        "buff_path_map_count": len(buff_path_by_type_name),
        "buff_class_count": len({row["buff_class"] for row in buff_effect_rows if row.get("buff_class")}),
        "buff_classes_missing_files": sum(1 for row in buff_effect_rows if row.get("buff_class") and not row.get("buff_class_file_count")),
    }
    verdict = {
        "all_direct_skill_buff_ids_have_buff_effect": all(buff_id in buff_by_id for buff_id in direct_buff_ids),
        "buff_path_map_found": bool(buff_path_by_type_name),
        "all_selected_buff_classes_have_files": stats["buff_classes_missing_files"] == 0,
        "static_client_buff_dispatch_only": True,
    }

    output_dir = root / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    links_tsv = output_dir / "lua_lscript_module_doupotd_buff_effect_skill_links.tsv"
    effects_tsv = output_dir / "lua_lscript_module_doupotd_buff_effects.tsv"
    report_path = output_dir / "lua_lscript_module_doupotd_buff_effect_link_report.md"
    json_path = output_dir / "lua_lscript_module_doupotd_buff_effect_link_report.json"
    _write_tsv(
        links_tsv,
        link_rows,
        [
            "skill_id",
            "partner_id",
            "partner_name",
            "skill_type",
            "buff_id",
            "buff_found",
            "buff_type_id",
            "buff_type_name",
            "buff_class",
            "trigger_type",
            "target_type",
            "duration",
            "damage",
            "add_attr",
        ],
    )
    _write_tsv(
        effects_tsv,
        buff_effect_rows,
        [
            "buff_id",
            "source_kind",
            "buff_found",
            "buff_type_id",
            "buff_type_name",
            "buff_class_path",
            "buff_class",
            "buff_class_file_count",
            "buff_class_files",
            "target_type",
            "target_type_name",
            "trigger_type",
            "layer_type",
            "layer_type_name",
            "passive",
            "duration",
            "interval",
            "plies_limit",
            "damage",
            "add_attr",
            "timeline_id",
            "trigger_buff_id",
            "kill_add_buff_id",
            "buff_end_skill_id",
        ],
    )
    _write_doupotd_buff_effect_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        sample_link_rows=link_rows,
        sample_buff_rows=buff_effect_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "verdict": verdict,
                "files": {
                    "skill_links": str(links_tsv),
                    "buff_effects": str(effects_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "skill_links": str(links_tsv),
            "buff_effects": str(effects_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _read_tsv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _split_cell_values(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [part.strip() for part in re.split(r"[|,]", str(value)) if part.strip()]


def _join_unique_cell(values: list[Any]) -> str:
    return "|".join(str(value) for value in _dedupe_preserve([value for value in values if value not in (None, "")]))


def _extract_lua_function_signatures(text: str) -> list[str]:
    functions: list[str] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        match = re.search(r"\bfunction\s+([A-Za-z0-9_:.]+)\s*\(([^)]*)\)", stripped)
        if match:
            functions.append(f"{line_no}:{match.group(1)}({match.group(2)})")
            continue
        match = re.search(r"([A-Za-z0-9_:.]+)\s*=\s*function\s*\(([^)]*)\)", stripped)
        if match:
            functions.append(f"{line_no}:{match.group(1)}({match.group(2)})")
    return functions


def _doupotd_buff_class_flags(text: str) -> list[str]:
    checks = [
        ("starts_buff", r"\bfunction\s+_M\.Start\b"),
        ("updates_timer", r"\bfunction\s+_M\.Update\b"),
        ("custom_do_buff_logic", r"\bfunction\s+_M\.DoBuffLogic\b"),
        ("layer_logic", r"\bAddBuffLayer\b|plies|layer", re.I),
        ("uses_trigger_buff", r"triggerBuffId"),
        ("uses_timeline", r"timelineId"),
        ("uses_add_attr", r"addAttr|AddAttr|Attr", re.I),
        ("uses_damage", r"\bdamage\b", re.I),
        ("adds_runtime_buff", r"\bAddBuff\b"),
        ("removes_runtime_buff", r"\bRemoveBuff\b|RemoveSelf"),
        ("has_percent_trigger", r"TriggerPercentBuff|Percent"),
        ("controls_release_skill", r"ReleaseSkill|skillId|skillCfg", re.I),
        ("controls_status", r"Stun|Frost|SlowDown|TimeScale|Poison", re.I),
    ]
    flags: list[str] = []
    for item in checks:
        if len(item) == 2:
            name, pattern = item
            regex_flags = 0
        else:
            name, pattern, regex_flags = item
        if re.search(pattern, text, regex_flags):
            flags.append(name)
    return flags


def _extract_doupotd_buff_class_evidence(
    buff_class: str,
    text: str,
    *,
    max_rows: int = 18,
) -> list[dict[str, Any]]:
    patterns = [
        ("function", r"\bfunction\s+_M\.|=\s*function\s*\("),
        ("trigger", r"triggerBuffId|TriggerPercentBuff|AnalysisTriggerType|triggerType"),
        ("timeline", r"timelineId|SkillEffect|Timeline|PlaySkill"),
        ("runtime_buff", r"AddBuff|RemoveBuff|RemoveSelf|DeleteBuffEffect|AddBuffEffect"),
        ("attribute", r"addAttr|AddAttr|Attr", re.I),
        ("damage", r"\bdamage\b|DoHit|Hurt", re.I),
        ("layer", r"AddBuffLayer|plies|layer", re.I),
        ("status", r"Stun|Frost|SlowDown|TimeScale|Poison", re.I),
    ]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        for category, pattern, *flags in patterns:
            regex_flags = flags[0] if flags else 0
            if re.search(pattern, stripped, regex_flags):
                key = (line_no, category)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "buff_class": buff_class,
                        "line": line_no,
                        "category": category,
                        "code": stripped[:220],
                    }
                )
                break
        if len(rows) >= max_rows:
            break
    return rows


def _write_doupotd_buff_class_semantics_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    class_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# doupotd buff class semantics report",
        "",
        "This is a static, read-only summary of the selected `DoupoTDBuff*` Lua runtime classes referenced by doupotd `BuffEffect` rows.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Class Summary", ""])
    for row in class_rows:
        lines.append(
            f"- `{row.get('buff_class')}`: buffs `{row.get('buff_ids')}`, types `{row.get('buff_types')}`, flags `{row.get('semantic_flags')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in evidence_rows[:30]:
        lines.append(
            f"- `{row.get('buff_class')}` line `{row.get('line')}` `{row.get('category')}`: `{row.get('code')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This report explains visible client buff class behavior and dispatch. It does not prove server authority, final combat arithmetic, or live trigger frequency.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_buff_class_semantics_probe(
    *,
    tower_defense_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    link_result = build_fanxiu_doupotd_buff_effect_probe(
        tower_defense_config_dir=tower_defense_config_dir,
        lang_path=lang_path,
        export_root=export_root,
    )
    effect_rows = _read_tsv_dicts(Path(link_result["files"]["buff_effects"]))

    grouped: dict[str, list[dict[str, str]]] = {}
    class_file_by_class: dict[str, Path] = {}
    for row in effect_rows:
        buff_class = row.get("buff_class") or ""
        if not buff_class:
            continue
        grouped.setdefault(buff_class, []).append(row)
        if buff_class in class_file_by_class:
            continue
        for rel_path in _split_cell_values(row.get("buff_class_files")):
            candidate = root / rel_path
            if candidate.is_file():
                class_file_by_class[buff_class] = candidate
                break

    class_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for buff_class, rows in sorted(grouped.items()):
        source_path = class_file_by_class.get(buff_class)
        text = source_path.read_text(encoding="utf-8", errors="ignore") if source_path else ""
        functions = _extract_lua_function_signatures(text)
        flags = _doupotd_buff_class_flags(text)
        evidence = _extract_doupotd_buff_class_evidence(buff_class, text)
        evidence_rows.extend(evidence)
        class_rows.append(
            {
                "buff_class": buff_class,
                "buff_ids": _join_unique_cell([row.get("buff_id") for row in rows]),
                "source_kinds": _join_unique_cell([row.get("source_kind") for row in rows]),
                "buff_types": _join_unique_cell([row.get("buff_type_name") for row in rows]),
                "trigger_types": _join_unique_cell([row.get("trigger_type") for row in rows]),
                "layer_types": _join_unique_cell([row.get("layer_type_name") for row in rows]),
                "target_types": _join_unique_cell([row.get("target_type_name") for row in rows]),
                "timeline_ids": _join_unique_cell([row.get("timeline_id") for row in rows]),
                "trigger_buff_ids": _join_unique_cell([row.get("trigger_buff_id") for row in rows]),
                "kill_add_buff_ids": _join_unique_cell([row.get("kill_add_buff_id") for row in rows]),
                "buff_end_skill_ids": _join_unique_cell([row.get("buff_end_skill_id") for row in rows]),
                "add_attrs": _join_unique_cell([row.get("add_attr") for row in rows]),
                "function_count": len(functions),
                "functions": " | ".join(functions[:20]),
                "semantic_flags": "|".join(flags),
                "evidence_line_count": len(evidence),
                "source_file": str(source_path.relative_to(root)) if source_path else "",
            }
        )

    stats = {
        "selected_buff_effect_count": len(effect_rows),
        "buff_class_count": len(grouped),
        "class_file_found_count": len(class_file_by_class),
        "class_file_missing_count": len(grouped) - len(class_file_by_class),
        "function_count": sum(_as_int(row.get("function_count")) or 0 for row in class_rows),
        "evidence_line_count": len(evidence_rows),
        "classes_with_trigger_buff": sum(1 for row in class_rows if "uses_trigger_buff" in str(row.get("semantic_flags"))),
        "classes_with_timeline": sum(1 for row in class_rows if "uses_timeline" in str(row.get("semantic_flags"))),
        "classes_with_runtime_buff_mutation": sum(
            1
            for row in class_rows
            if "adds_runtime_buff" in str(row.get("semantic_flags")) or "removes_runtime_buff" in str(row.get("semantic_flags"))
        ),
    }
    verdict = {
        "all_selected_buff_classes_have_source": stats["class_file_missing_count"] == 0,
        "has_base_class_semantics": "DoupoTDBuffBase" in grouped,
        "has_runtime_buff_mutation_classes": stats["classes_with_runtime_buff_mutation"] > 0,
        "static_client_class_semantics_only": True,
    }

    output_dir = root / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    class_tsv = output_dir / "lua_lscript_module_doupotd_buff_class_semantics.tsv"
    evidence_tsv = output_dir / "lua_lscript_module_doupotd_buff_class_semantics_evidence.tsv"
    report_path = output_dir / "lua_lscript_module_doupotd_buff_class_semantics_report.md"
    json_path = output_dir / "lua_lscript_module_doupotd_buff_class_semantics_report.json"
    _write_tsv(
        class_tsv,
        class_rows,
        [
            "buff_class",
            "buff_ids",
            "source_kinds",
            "buff_types",
            "trigger_types",
            "layer_types",
            "target_types",
            "timeline_ids",
            "trigger_buff_ids",
            "kill_add_buff_ids",
            "buff_end_skill_ids",
            "add_attrs",
            "function_count",
            "functions",
            "semantic_flags",
            "evidence_line_count",
            "source_file",
        ],
    )
    _write_tsv(evidence_tsv, evidence_rows, ["buff_class", "line", "category", "code"])
    _write_doupotd_buff_class_semantics_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        class_rows=class_rows,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "verdict": verdict,
                "files": {
                    "classes": str(class_tsv),
                    "evidence": str(evidence_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "classes": str(class_tsv),
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _extract_lua_function_blocks(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    starts: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        match = re.search(r"\bfunction\s+_M\.([A-Za-z0-9_]+)\s*\(([^)]*)\)", line.strip())
        if not match:
            continue
        starts.append(
            {
                "name": match.group(1),
                "params": match.group(2),
                "start_index": index,
                "start_line": index + 1,
            }
        )
    blocks: list[dict[str, Any]] = []
    for i, start in enumerate(starts):
        end_index = starts[i + 1]["start_index"] if i + 1 < len(starts) else len(lines)
        block_lines = [
            {"line": line_no + 1, "code": lines[line_no].strip()}
            for line_no in range(start["start_index"], end_index)
            if lines[line_no].strip() and not lines[line_no].strip().startswith("--")
        ]
        blocks.append(
            {
                "function": start["name"],
                "params": start["params"],
                "start_line": start["start_line"],
                "end_line": end_index,
                "lines": block_lines,
            }
        )
    return blocks


def _classify_doupotd_flow_line(code: str) -> str | None:
    checks = [
        ("entry", r"^function\s+_M\."),
        ("guard", r"\bif\s+not\b|\breturn\b"),
        ("skill_filter", r"TriggerSkillDic|skill\.skillGroup|skill\.skillType|needAdd"),
        ("target_buff_check", r"BuffIdCheckList|GetBuffDic|V_ConfigId"),
        ("random_gate", r"GetTriggerPercent|math\.random|percentVal|triggerPercent"),
        ("trigger_buff_ids", r"GetTriggerBuffIds|triggerBuffIds"),
        ("buff_config_lookup", r"GetConfigTableByIdWithLog|ConfigName\.DoupoTowerDefense_BuffEffect"),
        ("target_selection", r"targetType|BuffTargetType|GetBuffTargetList|targetList"),
        ("add_buff", r":AddBuff\("),
        ("remove_buff", r":RemoveBuff\(|RemoveSelf"),
        ("layer", r"AddBuffLayer|GetLayer|SetLayer|CheckBuffLayerTrigger|V_LayerLimit"),
        ("timeline", r"timelineId|Timeline|PlayElement|PlaySkill"),
        ("lifetime", r"StartTime|lifeTime|Duration|fDTime|startUpdate"),
        ("dispatch", r"DoBuffLogic|TriggerPercentBuff|DoHitTargetAddBuff"),
        ("super_call", r"_super_"),
    ]
    for category, pattern in checks:
        if re.search(pattern, code, re.I):
            return category
    return None


def _summarize_doupotd_flow_function(block: dict[str, Any]) -> dict[str, Any]:
    step_rows = [
        row
        for row in block.get("lines") or []
        if _classify_doupotd_flow_line(str(row.get("code") or "")) is not None
    ]
    text = "\n".join(str(row.get("code") or "") for row in block.get("lines") or [])
    calls = re.findall(r"\bself:([A-Za-z0-9_]+)\s*\(", text)
    calls.extend(re.findall(r"\b[A-Za-z0-9_]+:([A-Za-z0-9_]+)\s*\(", text))
    categories = [_classify_doupotd_flow_line(str(row.get("code") or "")) for row in step_rows]
    return {
        "function": block.get("function") or "",
        "params": block.get("params") or "",
        "start_line": block.get("start_line") or "",
        "end_line": block.get("end_line") or "",
        "step_count": len(step_rows),
        "categories": _join_unique_cell([category for category in categories if category]),
        "calls": _join_unique_cell(calls),
        "adds_buff": ":AddBuff(" in text,
        "removes_buff": ":RemoveBuff(" in text or "RemoveSelf" in text,
        "uses_random_gate": "math.random" in text or "GetTriggerPercent" in text,
        "uses_skill_filter": "TriggerSkillDic" in text or "skill.skillGroup" in text or "skill.skillType" in text,
        "uses_target_buff_check": "BuffIdCheckList" in text or "GetBuffDic" in text,
        "uses_friend_target_expansion": "BuffTargetType.Friend" in text or "GetBuffTargetList" in text,
    }


def _doupotd_class_flow_hint(class_name: str, function_rows: list[dict[str, Any]]) -> str:
    function_by_name = {str(row.get("function") or ""): row for row in function_rows}
    if class_name == "DoupoTDBuffHitAddBuff":
        return (
            "命中触发型：DoBuffLogic 转入 DoHitTargetAddBuff；可按 skillGroup/skillType 和目标已有 buff 过滤，"
            "再经 triggerPercent 随机门槛，最终给命中目标或目标列表追加 triggerBuffId。"
        )
    if class_name == "DoupoTDBuffMingxin":
        return (
            "持续光环型：Start 立即按概率给友方目标补 triggerBuffId；Update 到持续时间后移除这些友方 buff，"
            "然后移除自身。"
        )
    if function_by_name.get("AddBuffLayer") or function_by_name.get("CheckBuffLayerTrigger"):
        return "叠层触发型：层数变化后检查 MaxLayer 条件，满足后进入 DoBuffLogic/TriggerPercentBuff。"
    if any(row.get("adds_buff") for row in function_rows) and any(row.get("removes_buff") for row in function_rows):
        return "运行时 buff 变更型：类内同时存在 AddBuff 与 RemoveBuff 路径，需要结合生命周期判断持续边界。"
    if any(row.get("adds_buff") for row in function_rows):
        return "补 buff 型：类内存在 AddBuff 路径，通常由触发条件或启动逻辑进入。"
    if any(row.get("removes_buff") for row in function_rows):
        return "移除/失效型：类内存在 RemoveBuff/RemoveSelf 路径，主要描述生命周期结束或状态解除。"
    return "基础/属性型：当前静态片段未发现复杂运行时 buff 派发。"


def _write_doupotd_buff_class_flow_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    class_rows: list[dict[str, Any]],
    function_rows: list[dict[str, Any]],
    step_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# doupotd buff class flow report",
        "",
        "This is a static, read-only control-flow summary for selected `DoupoTDBuff*` Lua classes.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Class Flow Hints", ""])
    for row in class_rows:
        lines.append(
            f"- `{row.get('buff_class')}` buffs `{row.get('buff_ids')}` types `{row.get('buff_types')}`: {row.get('flow_hint')}"
        )
    lines.extend(["", "## Key Functions", ""])
    for row in function_rows:
        if not row.get("step_count"):
            continue
        lines.append(
            f"- `{row.get('buff_class')}.{row.get('function')}` lines `{row.get('start_line')}-{row.get('end_line')}` "
            f"categories `{row.get('categories')}` calls `{row.get('calls')}`"
        )
    lines.extend(["", "## Step Samples", ""])
    for row in step_rows[:60]:
        lines.append(
            f"- `{row.get('buff_class')}.{row.get('function')}` line `{row.get('line')}` `{row.get('category')}`: `{row.get('code')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This report describes visible client Lua flow only. It does not modify runtime state, prove server-side authority, or validate live combat outcomes.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_buff_class_flow_probe(
    *,
    tower_defense_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
    buff_classes: list[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    semantics_result = build_fanxiu_doupotd_buff_class_semantics_probe(
        tower_defense_config_dir=tower_defense_config_dir,
        lang_path=lang_path,
        export_root=export_root,
    )
    semantic_rows = _read_tsv_dicts(Path(semantics_result["files"]["classes"]))
    requested = {item.strip() for item in buff_classes or [] if item and item.strip()}

    selected_rows: list[dict[str, str]] = []
    for row in semantic_rows:
        class_name = row.get("buff_class") or ""
        flags = set(_split_cell_values(row.get("semantic_flags")))
        if requested:
            include = class_name in requested
        else:
            include = bool(
                {
                    "custom_do_buff_logic",
                    "uses_trigger_buff",
                    "layer_logic",
                    "adds_runtime_buff",
                    "removes_runtime_buff",
                    "has_percent_trigger",
                }
                & flags
            )
        if include:
            selected_rows.append(row)

    class_rows: list[dict[str, Any]] = []
    function_rows: list[dict[str, Any]] = []
    step_rows: list[dict[str, Any]] = []
    missing_sources: list[str] = []
    for row in selected_rows:
        class_name = row.get("buff_class") or ""
        source_rel = row.get("source_file") or ""
        source_path = root / source_rel if source_rel else None
        if not source_path or not source_path.is_file():
            missing_sources.append(class_name)
            class_rows.append(
                {
                    "buff_class": class_name,
                    "buff_ids": row.get("buff_ids") or "",
                    "buff_types": row.get("buff_types") or "",
                    "source_file": source_rel,
                    "function_count": 0,
                    "flow_step_count": 0,
                    "flow_categories": "",
                    "flow_hint": "source file missing",
                }
            )
            continue
        text = source_path.read_text(encoding="utf-8", errors="ignore")
        blocks = _extract_lua_function_blocks(text)
        current_function_rows: list[dict[str, Any]] = []
        current_step_rows: list[dict[str, Any]] = []
        for block in blocks:
            summary = _summarize_doupotd_flow_function(block)
            summary.update(
                {
                    "buff_class": class_name,
                    "buff_ids": row.get("buff_ids") or "",
                    "buff_types": row.get("buff_types") or "",
                    "source_file": source_rel,
                }
            )
            current_function_rows.append(summary)
            order = 0
            for line_row in block.get("lines") or []:
                code = str(line_row.get("code") or "")
                category = _classify_doupotd_flow_line(code)
                if not category:
                    continue
                order += 1
                current_step_rows.append(
                    {
                        "buff_class": class_name,
                        "function": block.get("function") or "",
                        "line": line_row.get("line") or "",
                        "step_order": order,
                        "category": category,
                        "code": code[:260],
                    }
                )
        function_rows.extend(current_function_rows)
        step_rows.extend(current_step_rows)
        class_rows.append(
            {
                "buff_class": class_name,
                "buff_ids": row.get("buff_ids") or "",
                "buff_types": row.get("buff_types") or "",
                "source_file": source_rel,
                "function_count": len(blocks),
                "flow_step_count": len(current_step_rows),
                "flow_categories": _join_unique_cell([step.get("category") for step in current_step_rows]),
                "flow_hint": _doupotd_class_flow_hint(class_name, current_function_rows),
            }
        )

    category_counts = Counter(str(row.get("category") or "") for row in step_rows)
    stats = {
        "available_semantic_class_count": len(semantic_rows),
        "requested_class_count": len(requested),
        "selected_class_count": len(selected_rows),
        "class_file_missing_count": len(missing_sources),
        "flow_function_count": len(function_rows),
        "flow_step_count": len(step_rows),
        "classes_with_skill_filter": sum(1 for row in class_rows if row.get("buff_class") and any(f.get("buff_class") == row.get("buff_class") and f.get("uses_skill_filter") for f in function_rows)),
        "classes_with_target_buff_check": sum(
            1
            for row in class_rows
            if row.get("buff_class") and any(f.get("buff_class") == row.get("buff_class") and f.get("uses_target_buff_check") for f in function_rows)
        ),
        "classes_with_friend_target_expansion": sum(
            1
            for row in class_rows
            if row.get("buff_class") and any(f.get("buff_class") == row.get("buff_class") and f.get("uses_friend_target_expansion") for f in function_rows)
        ),
        "classes_with_add_buff": sum(1 for row in class_rows if row.get("buff_class") and any(f.get("buff_class") == row.get("buff_class") and f.get("adds_buff") for f in function_rows)),
        "classes_with_remove_buff": sum(
            1 for row in class_rows if row.get("buff_class") and any(f.get("buff_class") == row.get("buff_class") and f.get("removes_buff") for f in function_rows)
        ),
        "classes_with_random_gate": sum(
            1 for row in class_rows if row.get("buff_class") and any(f.get("buff_class") == row.get("buff_class") and f.get("uses_random_gate") for f in function_rows)
        ),
        "step_category_counts": dict(sorted(category_counts.items())),
    }
    verdict = {
        "all_selected_classes_have_source": stats["class_file_missing_count"] == 0,
        "has_buff_mutation_flow": stats["classes_with_add_buff"] > 0 or stats["classes_with_remove_buff"] > 0,
        "has_trigger_filter_flow": stats["classes_with_skill_filter"] > 0 or stats["classes_with_target_buff_check"] > 0,
        "static_client_flow_only": True,
    }

    output_dir = root / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    class_tsv = output_dir / "lua_lscript_module_doupotd_buff_class_flow_classes.tsv"
    function_tsv = output_dir / "lua_lscript_module_doupotd_buff_class_flow_functions.tsv"
    step_tsv = output_dir / "lua_lscript_module_doupotd_buff_class_flow_steps.tsv"
    report_path = output_dir / "lua_lscript_module_doupotd_buff_class_flow_report.md"
    json_path = output_dir / "lua_lscript_module_doupotd_buff_class_flow_report.json"
    _write_tsv(
        class_tsv,
        class_rows,
        ["buff_class", "buff_ids", "buff_types", "source_file", "function_count", "flow_step_count", "flow_categories", "flow_hint"],
    )
    _write_tsv(
        function_tsv,
        function_rows,
        [
            "buff_class",
            "buff_ids",
            "buff_types",
            "function",
            "params",
            "start_line",
            "end_line",
            "step_count",
            "categories",
            "calls",
            "adds_buff",
            "removes_buff",
            "uses_random_gate",
            "uses_skill_filter",
            "uses_target_buff_check",
            "uses_friend_target_expansion",
            "source_file",
        ],
    )
    _write_tsv(step_tsv, step_rows, ["buff_class", "function", "line", "step_order", "category", "code"])
    _write_doupotd_buff_class_flow_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        class_rows=class_rows,
        function_rows=function_rows,
        step_rows=step_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "verdict": verdict,
                "files": {
                    "classes": str(class_tsv),
                    "functions": str(function_tsv),
                    "steps": str(step_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "classes": str(class_tsv),
            "functions": str(function_tsv),
            "steps": str(step_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _doupotd_lscript_text_asset_dirs(root: Path) -> list[Path]:
    lscript_root = root / "by_source" / "lscripts" / "gamesystem" / "game"
    if not lscript_root.is_dir():
        return []
    return sorted(path / "text_assets" for path in lscript_root.glob("doupotd_*") if (path / "text_assets").is_dir())


def _scan_doupotd_authority_evidence(root: Path, *, max_rows: int = 120) -> list[dict[str, Any]]:
    patterns = [
        ("damage_result", r"AddDamageResult"),
        ("add_buff", r":AddBuff\("),
        ("remove_buff", r":RemoveBuff\(|RemoveBuffByType|RemoveSelf"),
        ("skill_buff_data", r"AddBuffData|skillToAddBuffList|killAddBuffId|hitBuffId"),
        ("game_player_packet", r"CM_DoupoTDGamePlayer|SM_DoupoTDGamePlayer|killNum|bossVoList|wavePercent"),
    ]
    rows: list[dict[str, Any]] = []
    for text_dir in _doupotd_lscript_text_asset_dirs(root):
        for path in sorted(text_dir.glob("*.lua")):
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue
                for category, pattern in patterns:
                    if not re.search(pattern, stripped):
                        continue
                    rows.append(
                        {
                            "category": category,
                            "file": str(path.relative_to(root)),
                            "line": line_no,
                            "code": stripped[:260],
                        }
                    )
                    break
                if len(rows) >= max_rows:
                    return rows
    return rows


def _write_doupotd_buff_authority_boundary_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    packet_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# doupotd buff authority boundary report",
        "",
        "This is a static, read-only boundary check between doupotd client buff flow and visible doupotd protocol schemas.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Relevant Packets", ""])
    for row in packet_rows[:30]:
        lines.append(
            f"- `{row.get('packet_name')}` `{row.get('packet_id')}` `{row.get('direction')}` fields `{row.get('schema_fields')}` reason `{row.get('match_reason')}`"
        )
    lines.extend(["", "## Lua Evidence Samples", ""])
    for row in evidence_rows[:50]:
        lines.append(
            f"- `{row.get('category')}` `{row.get('file')}:{row.get('line')}` `{row.get('code')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "Absence in visible Lua packet schemas is not proof of server internals. It only means the current exported doupotd Lua surface does not expose a per-buff state/result protocol analogous to the generic fight BuffVO/BuffResultVO families.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_buff_authority_boundary_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "apk_static_index"
    flow_classes = _read_tsv_dicts(output_dir / "lua_lscript_module_doupotd_buff_class_flow_classes.tsv")
    protocol_schemas = _read_tsv_dicts(output_dir / "lua_lscript_module_doupotd_protocol_schemas.tsv")
    protocol_fields = _read_tsv_dicts(output_dir / "lua_lscript_module_doupotd_protocol_fields.tsv")

    packet_rows: list[dict[str, Any]] = []
    packet_seen: set[str] = set()
    packet_terms = re.compile(r"Buff|buff|configId|Result|Reward|Finish|GamePlayer|Effect|Battle|End")
    buff_terms = re.compile(r"Buff|buff|configId|buffId")
    for row in protocol_schemas:
        text = " ".join(str(row.get(key) or "") for key in ("packet_name", "schema_fields", "assigned_fields"))
        if not packet_terms.search(text):
            continue
        packet_name = row.get("packet_name") or ""
        if packet_name in packet_seen:
            continue
        packet_seen.add(packet_name)
        reason_parts = []
        if buff_terms.search(text):
            reason_parts.append("buff_like")
        if re.search(r"GamePlayer|finishWave|killNum|bossVoList|wavePercent", text):
            reason_parts.append("game_progress_summary")
        if re.search(r"Result|Reward|Finish|Effect|Battle|End", text):
            reason_parts.append("result_or_effect_boundary")
        packet_rows.append(
            {
                "packet_name": packet_name,
                "packet_id": row.get("packet_id") or "",
                "direction": row.get("direction") or "",
                "field_count": row.get("field_count") or "",
                "schema_fields": row.get("schema_fields") or "",
                "netlogic_functions": row.get("netlogic_functions") or "",
                "match_reason": "|".join(reason_parts),
            }
        )

    field_buff_rows = [
        row
        for row in protocol_fields
        if buff_terms.search(" ".join(str(row.get(key) or "") for key in ("packet_name", "field_name", "field_type")))
    ]
    evidence_rows = _scan_doupotd_authority_evidence(root)
    evidence_counts = Counter(row.get("category") for row in evidence_rows)
    flow_classes_with_add = [
        row for row in flow_classes if "add_buff" in _split_cell_values(row.get("flow_categories"))
    ]
    flow_classes_with_remove = [
        row for row in flow_classes if "remove_buff" in _split_cell_values(row.get("flow_categories"))
    ]
    stats = {
        "flow_class_count": len(flow_classes),
        "flow_classes_with_add_buff": len(flow_classes_with_add),
        "flow_classes_with_remove_buff": len(flow_classes_with_remove),
        "protocol_packet_count": len(protocol_schemas),
        "relevant_packet_count": len(packet_rows),
        "buff_like_protocol_field_count": len(field_buff_rows),
        "lua_evidence_row_count": len(evidence_rows),
        "lua_damage_result_call_count": evidence_counts.get("damage_result", 0),
        "lua_add_buff_call_count": evidence_counts.get("add_buff", 0),
        "lua_remove_buff_call_count": evidence_counts.get("remove_buff", 0),
        "lua_game_player_packet_evidence_count": evidence_counts.get("game_player_packet", 0),
    }
    verdict = {
        "visible_doupotd_buff_flow_is_client_local": stats["flow_classes_with_add_buff"] > 0 or stats["flow_classes_with_remove_buff"] > 0,
        "visible_doupotd_protocol_has_buff_state_fields": stats["buff_like_protocol_field_count"] > 0,
        "visible_doupotd_protocol_has_game_progress_summary": any(
            row.get("packet_name") in {"CM_DoupoTDGamePlayer", "SM_DoupoTDGamePlayer"} for row in packet_rows
        ),
        "no_visible_per_buff_state_packet_in_doupotd_schema": stats["buff_like_protocol_field_count"] == 0,
        "static_boundary_only": True,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    packet_tsv = output_dir / "lua_lscript_module_doupotd_buff_authority_boundary_packets.tsv"
    evidence_tsv = output_dir / "lua_lscript_module_doupotd_buff_authority_boundary_evidence.tsv"
    report_path = output_dir / "lua_lscript_module_doupotd_buff_authority_boundary_report.md"
    json_path = output_dir / "lua_lscript_module_doupotd_buff_authority_boundary_report.json"
    _write_tsv(
        packet_tsv,
        packet_rows,
        ["packet_name", "packet_id", "direction", "field_count", "schema_fields", "netlogic_functions", "match_reason"],
    )
    _write_tsv(evidence_tsv, evidence_rows, ["category", "file", "line", "code"])
    _write_doupotd_buff_authority_boundary_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        packet_rows=packet_rows,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "verdict": verdict,
                "files": {
                    "packets": str(packet_tsv),
                    "evidence": str(evidence_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "packets": str(packet_tsv),
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _find_doupotd_message_asset(root: Path, asset_name: str) -> Path | None:
    candidates = sorted(
        path
        for path in (root / "by_source" / "lscripts" / "gamesystem" / "game").glob(
            f"message_*/text_assets/{asset_name}.lua"
        )
        if path.is_file()
    )
    return candidates[0] if candidates else None


def _parse_doupotd_message_asset_fields(root: Path, object_name: str) -> tuple[Path | None, list[dict[str, Any]]]:
    path = _find_doupotd_message_asset(root, object_name)
    if path is None:
        return None, []
    text = path.read_text(encoding="utf-8", errors="ignore")
    object_id = ""
    id_match = re.search(r"function\s+_M\.getId\(self\)\s*return\s+(-?\d+)", text)
    if id_match:
        object_id = id_match.group(1)
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), 1):
        match = re.search(r"self\.([A-Za-z0-9_]+)\s*=\s*self:read([A-Za-z0-9_]+)\s*\(", line.strip())
        if not match:
            continue
        rows.append(
            {
                "object_name": object_name,
                "object_id": object_id,
                "field_index": len(rows) + 1,
                "field_name": match.group(1),
                "read_method": match.group(2),
                "semantic": _doupotd_summary_object_field_semantic(object_name, match.group(1)),
                "source_file": str(path.relative_to(root)),
                "line": index,
            }
        )
    return path, rows


def _doupotd_summary_object_field_semantic(object_name: str, field_name: str) -> str:
    mapping = {
        ("DoupoTDEffectVO", "index"): "selected_candidate_index",
        ("DoupoTDEffectVO", "type"): "effect_kind_1_role_2_skill_enhance_3_turn_skill_enhance",
        ("DoupoTDEffectVO", "id"): "role_or_skill_enhance_id",
        ("DoupoBossVo", "id"): "boss_config_id",
        ("DoupoBossVo", "hp"): "hp_percent_x10000",
    }
    return mapping.get((object_name, field_name), "unknown")


def _doupotd_summary_packet_field_semantic(packet_name: str, field_name: str) -> tuple[str, str]:
    mapping = {
        ("CM_DoupoTDEffect", "index"): ("", "client_selected_candidate_index"),
        ("SM_DoupoTDEffect", "effectVO"): ("DoupoTDEffectVO", "server_applied_upgrade_effect"),
        ("CM_DoupoTDGamePlayer", "bossVoList"): ("DoupoBossVo", "client_boss_hp_percent_snapshot"),
        ("CM_DoupoTDRefreshWave", "bossVoList"): ("DoupoBossVo", "client_boss_hp_percent_snapshot"),
        ("CM_DoupoTDGamePlayer", "killNum"): ("", "client_small_monster_kill_count"),
        ("CM_DoupoTDGamePlayer", "currWave"): ("", "client_current_wave"),
        ("CM_DoupoTDGamePlayer", "wavePercent"): ("", "client_wave_progress_percent"),
        ("CM_DoupoTDRefreshWave", "wave"): ("", "client_refresh_wave"),
        ("CM_DoupoTDRefreshWave", "addExp"): ("", "client_added_exp_summary"),
        ("CM_DoupoTDRefreshWave", "killNum"): ("", "client_small_monster_kill_count"),
        ("SM_DoupoTDRefreshWave", "refreshWave"): ("", "server_refresh_wave"),
        ("SM_DoupoTDRefreshWave", "currExp"): ("", "server_current_exp"),
        ("SM_DoupoTDGamePlayer", "finishWave"): ("", "server_finish_wave"),
        ("SM_DoupoTDGamePlayer", "rewardResults"): ("", "server_reward_results"),
        ("SM_DoupoTDGamePlayer", "passLevelVOS"): ("", "server_pass_level_snapshot"),
        ("SM_DoupoTDGamePlayer", "levelId"): ("", "server_level_id"),
        ("SM_DoupoTDGamePlayer", "gameType"): ("", "server_game_type"),
        ("SM_DoupoTDGamePlayer", "isSkipLevel"): ("", "server_skip_level_result_flag"),
        ("SM_DoupoTDGamePlayer", "wavePercent"): ("", "server_wave_progress_percent"),
    }
    return mapping.get((packet_name, field_name), ("", "unknown"))


def _collect_doupotd_summary_packet_fields(output_dir: Path) -> list[dict[str, Any]]:
    paths = [
        output_dir / "lua_lscript_module_doupotd_cm_doupotdeffect_sm_doupotdeffect_pair_fields.tsv",
        output_dir / "lua_lscript_module_doupotd_cm_doupotdgameplayer_sm_doupotdgameplayer_pair_fields.tsv",
        output_dir / "lua_lscript_module_doupotd_cm_doupotdrefreshwave_sm_doupotdrefreshwave_pair_fields.tsv",
    ]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in paths:
        for row in _read_tsv_dicts(path):
            packet_name = row.get("packet_name") or ""
            field_name = row.get("field_name") or ""
            if not packet_name or not field_name:
                continue
            key = (packet_name, field_name)
            if key in seen:
                continue
            seen.add(key)
            target_object, semantic = _doupotd_summary_packet_field_semantic(packet_name, field_name)
            rows.append(
                {
                    "packet_name": packet_name,
                    "packet_id": row.get("packet_id") or "",
                    "direction": row.get("direction") or "",
                    "field_index": row.get("field_index") or "",
                    "field_name": field_name,
                    "read_method": row.get("read_method") or "",
                    "type_hint": row.get("type_hint") or "",
                    "target_object": target_object,
                    "semantic": semantic,
                    "assigned_in_netlogic": row.get("assigned_in_netlogic") or "",
                    "netlogic_functions": row.get("netlogic_functions") or "",
                }
            )
    return rows


def _collect_doupotd_summary_edge_evidence(output_dir: Path) -> list[dict[str, Any]]:
    paths = [
        output_dir / "lua_lscript_module_doupotd_cm_doupotdeffect_sm_doupotdeffect_pair_edges.tsv",
        output_dir / "lua_lscript_module_doupotd_cm_doupotdgameplayer_sm_doupotdgameplayer_pair_edges.tsv",
        output_dir / "lua_lscript_module_doupotd_cm_doupotdrefreshwave_sm_doupotdrefreshwave_pair_edges.tsv",
    ]
    rows: list[dict[str, Any]] = []
    terms = re.compile(
        r"effectVO|bossVoList|GetTotalBossDamageList|UpdateRoleSkillAttrList|DoupoTDExitGame|OpenDoupoTDResultInfoView|AddRewardResults|RefreshWave",
        re.I,
    )
    for path in paths:
        for row in _read_tsv_dicts(path):
            snippet = row.get("snippet") or ""
            if not terms.search(snippet):
                continue
            rows.append(
                {
                    "source": path.name,
                    "category": row.get("category") or "",
                    "function_name": row.get("function_name") or "",
                    "line": row.get("line") or "",
                    "target": row.get("target") or "",
                    "snippet": snippet[:260],
                }
            )
    return rows


def _collect_doupotd_summary_function_evidence(root: Path) -> list[dict[str, Any]]:
    targets = {
        "DoupoTDModel.lua": {"UpdateRoleSkillAttrList"},
        "DoupoTDEntityMgr.lua": {"GetTotalBossDamageList"},
        "DoupoTDNetLogic.lua": {
            "SM_DoupoTDEffectFun",
            "CM_DoupoTDGamePlayerFun",
            "SM_DoupoTDGamePlayerFun",
            "CM_DoupoTDRefreshWaveFun",
        },
    }
    terms = re.compile(
        r"vo\.type|V_RoleSkill|SkillEnhance|TurnSkillEnhance|UpdateSelectedList|DoupoBossVo|vo\.hp|GetCurrentHp|GetMaxHp|bossVoList|effectVO|rewardResults|killNum|wavePercent",
        re.I,
    )
    rows: list[dict[str, Any]] = []
    for text_dir in _doupotd_lscript_text_asset_dirs(root):
        for asset_name, function_names in targets.items():
            path = text_dir / asset_name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for block in _extract_lua_function_blocks(text):
                if block.get("function") not in function_names:
                    continue
                for item in block.get("lines") or []:
                    code = str(item.get("code") or "")
                    if not terms.search(code):
                        continue
                    rows.append(
                        {
                            "source": str(path.relative_to(root)),
                            "category": "function_body",
                            "function_name": block.get("function") or "",
                            "line": item.get("line") or "",
                            "target": "",
                            "snippet": code[:260],
                        }
                    )
    return rows


def _write_doupotd_effect_gameplayer_summary_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    packet_rows: list[dict[str, Any]],
    object_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# doupotd effect/gameplayer summary object report",
        "",
        "Static read-only drilldown for `SM_DoupoTDEffect.effectVO` and `CM/SM_DoupoTDGamePlayer` summary fields.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Packet Fields", ""])
    for row in packet_rows:
        lines.append(
            f"- `{row.get('packet_name')}.{row.get('field_name')}` `{row.get('read_method')}` -> `{row.get('target_object') or '-'}`: `{row.get('semantic')}`"
        )
    lines.extend(["", "## Object Fields", ""])
    for row in object_rows:
        lines.append(
            f"- `{row.get('object_name')}.{row.get('field_name')}` `{row.get('read_method')}`: `{row.get('semantic')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in evidence_rows[:60]:
        lines.append(
            f"- `{row.get('category')}` `{row.get('function_name')}` `{row.get('source')}:{row.get('line')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "`DoupoTDEffectVO` is a compact server-returned upgrade/effect selection result. `DoupoBossVo` is a client-submitted boss id plus hp-percent snapshot used by GamePlayer/RefreshWave summaries. Neither object exposes per-buff state in the visible doupotd schema.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_effect_gameplayer_summary_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "apk_static_index"
    packet_rows = _collect_doupotd_summary_packet_fields(output_dir)
    object_rows: list[dict[str, Any]] = []
    object_paths: dict[str, str] = {}
    for object_name in ("DoupoTDEffectVO", "DoupoBossVo"):
        path, rows = _parse_doupotd_message_asset_fields(root, object_name)
        if path is not None:
            object_paths[object_name] = str(path.relative_to(root))
        object_rows.extend(rows)

    evidence_rows = _collect_doupotd_summary_edge_evidence(output_dir)
    evidence_rows.extend(_collect_doupotd_summary_function_evidence(root))
    evidence_counts = Counter(row.get("function_name") or row.get("category") for row in evidence_rows)
    packet_semantics = {str(row.get("semantic") or "") for row in packet_rows}
    object_semantics = {str(row.get("semantic") or "") for row in object_rows}
    stats = {
        "summary_packet_field_count": len(packet_rows),
        "summary_object_field_count": len(object_rows),
        "effect_vo_field_count": sum(1 for row in object_rows if row.get("object_name") == "DoupoTDEffectVO"),
        "boss_vo_field_count": sum(1 for row in object_rows if row.get("object_name") == "DoupoBossVo"),
        "packet_fields_targeting_effect_vo": sum(1 for row in packet_rows if row.get("target_object") == "DoupoTDEffectVO"),
        "packet_fields_targeting_boss_vo": sum(1 for row in packet_rows if row.get("target_object") == "DoupoBossVo"),
        "evidence_row_count": len(evidence_rows),
        "update_role_skill_attr_evidence_count": evidence_counts.get("UpdateRoleSkillAttrList", 0),
        "boss_damage_list_evidence_count": evidence_counts.get("GetTotalBossDamageList", 0),
        "buff_like_summary_field_count": sum(
            1
            for row in [*packet_rows, *object_rows]
            if re.search(r"buff|configId|buffId", " ".join(str(value) for value in row.values()), re.I)
        ),
    }
    verdict = {
        "effect_vo_is_upgrade_selection_result": {
            "effect_kind_1_role_2_skill_enhance_3_turn_skill_enhance",
            "role_or_skill_enhance_id",
        }.issubset(object_semantics)
        and "server_applied_upgrade_effect" in packet_semantics,
        "boss_vo_list_is_hp_percent_snapshot": "hp_percent_x10000" in object_semantics
        and "client_boss_hp_percent_snapshot" in packet_semantics,
        "summary_objects_are_not_per_buff_state": stats["buff_like_summary_field_count"] == 0,
        "static_boundary_only": True,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    packet_tsv = output_dir / "lua_lscript_module_doupotd_effect_gameplayer_summary_packet_fields.tsv"
    object_tsv = output_dir / "lua_lscript_module_doupotd_effect_gameplayer_summary_object_fields.tsv"
    evidence_tsv = output_dir / "lua_lscript_module_doupotd_effect_gameplayer_summary_evidence.tsv"
    report_path = output_dir / "lua_lscript_module_doupotd_effect_gameplayer_summary_report.md"
    json_path = output_dir / "lua_lscript_module_doupotd_effect_gameplayer_summary_report.json"
    _write_tsv(
        packet_tsv,
        packet_rows,
        [
            "packet_name",
            "packet_id",
            "direction",
            "field_index",
            "field_name",
            "read_method",
            "type_hint",
            "target_object",
            "semantic",
            "assigned_in_netlogic",
            "netlogic_functions",
        ],
    )
    _write_tsv(
        object_tsv,
        object_rows,
        ["object_name", "object_id", "field_index", "field_name", "read_method", "semantic", "source_file", "line"],
    )
    _write_tsv(evidence_tsv, evidence_rows, ["source", "category", "function_name", "line", "target", "snippet"])
    _write_doupotd_effect_gameplayer_summary_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        packet_rows=packet_rows,
        object_rows=object_rows,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "verdict": verdict,
                "object_paths": object_paths,
                "files": {
                    "packet_fields": str(packet_tsv),
                    "object_fields": str(object_tsv),
                    "evidence": str(evidence_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "object_paths": object_paths,
        "files": {
            "packet_fields": str(packet_tsv),
            "object_fields": str(object_tsv),
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _parse_doupotd_message_io_fields(
    root: Path,
    object_name: str,
    semantic_map: dict[str, str] | None = None,
) -> tuple[Path | None, list[dict[str, Any]]]:
    path = _find_doupotd_message_asset(root, object_name)
    if path is None:
        return None, []
    text = path.read_text(encoding="utf-8", errors="ignore")
    object_id = ""
    id_match = re.search(r"function\s+_M\.getId\(self\)\s*return\s+(-?\d+)", text)
    if id_match:
        object_id = id_match.group(1)
    writing_methods: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        write_match = re.search(r"self:write([A-Za-z0-9_]+)\s*\(\s*self\.([A-Za-z0-9_]+)", stripped)
        if write_match:
            writing_methods[write_match.group(2)] = write_match.group(1)
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        field_name = ""
        read_method = ""
        assign_match = re.search(r"self\.([A-Za-z0-9_]+)\s*=\s*self:read([A-Za-z0-9_]+)\s*\(", stripped)
        if assign_match:
            field_name = assign_match.group(1)
            read_method = assign_match.group(2)
        else:
            list_match = re.search(r"self:read([A-Za-z0-9_]+)\s*\(\s*self\.([A-Za-z0-9_]+)", stripped)
            if list_match:
                read_method = list_match.group(1)
                field_name = list_match.group(2)
        if not field_name:
            continue
        rows.append(
            {
                "object_name": object_name,
                "object_id": object_id,
                "field_index": len(rows) + 1,
                "field_name": field_name,
                "read_method": read_method,
                "write_method": writing_methods.get(field_name, ""),
                "semantic": (semantic_map or {}).get(field_name, "unknown"),
                "source_file": str(path.relative_to(root)),
                "line": index,
            }
        )
    return path, rows


def _collect_doupotd_gameplayer_result_evidence(root: Path, output_dir: Path) -> list[dict[str, Any]]:
    evidence_rows: list[dict[str, Any]] = []
    edge_path = output_dir / "lua_lscript_module_doupotd_cm_doupotdgameplayer_sm_doupotdgameplayer_pair_edges.tsv"
    for row in _read_tsv_dicts(edge_path):
        snippet = row.get("snippet") or ""
        if not re.search(
            r"rewardResults|passLevelVOS|DoupoTDExitGame|OpenDoupoTDResultInfoView|AddRewardResults|DoupoTDInfoUpdate",
            snippet,
            re.I,
        ):
            continue
        evidence_rows.append(
            {
                "source": edge_path.name,
                "category": row.get("category") or "",
                "function_name": row.get("function_name") or "",
                "line": row.get("line") or "",
                "snippet": snippet[:260],
            }
        )

    targets = {
        "DoupoTDNetLogic.lua": {"SM_DoupoTDGamePlayerFun"},
        "DoupoTDMgr.lua": {"DoupoTDExitGame", "OpenDoupoTDResultInfoView"},
        "DoupoTDResultInfoView.lua": {"UpdateViewInfoShow"},
        "DoupoTDData.lua": {"SetFinishLevelInfo", "InitNewLevelDic", "IsFinishLevel"},
        "DoupoTDInfoPanel.lua": {"InitUI", "OnAddEventListener"},
    }
    terms = re.compile(
        r"rewardResults|passLevelVOS|finishWave|levelId|isSkipLevel|wavePercent|ItemScrollView|Contains|InitNewLevelDic|SetFinishLevelInfo|IsFinishLevel|DoupoTDInfoUpdate|OpenDoupoTDResultInfoView|AddRewardResults",
        re.I,
    )
    for text_dir in _doupotd_lscript_text_asset_dirs(root):
        for asset_name, function_names in targets.items():
            path = text_dir / asset_name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for block in _extract_lua_function_blocks(text):
                if block.get("function") not in function_names:
                    continue
                for item in block.get("lines") or []:
                    code = str(item.get("code") or "")
                    if not terms.search(code):
                        continue
                    evidence_rows.append(
                        {
                            "source": str(path.relative_to(root)),
                            "category": "function_body",
                            "function_name": block.get("function") or "",
                            "line": item.get("line") or "",
                            "snippet": code[:260],
                        }
                    )
    return evidence_rows


def _write_doupotd_gameplayer_result_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# doupotd GamePlayer result report",
        "",
        "Static read-only drilldown for `SM_DoupoTDGamePlayer.rewardResults/passLevelVOS` and result-view consumers.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Message Fields", ""])
    for row in field_rows:
        lines.append(
            f"- `{row.get('object_name')}.{row.get('field_name')}` read `{row.get('read_method')}` write `{row.get('write_method')}`: `{row.get('semantic')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in evidence_rows[:70]:
        lines.append(
            f"- `{row.get('category')}` `{row.get('function_name')}` `{row.get('source')}:{row.get('line')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "`rewardResults` is consumed by common reward UI helpers and result item scroll views. `passLevelVOS` is consumed by visible doupotd Lua as level-id membership/progress, despite the VO-like field name. This closes more of the result-display boundary, but it is still static evidence rather than a server implementation trace.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_gameplayer_result_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "apk_static_index"
    field_rows: list[dict[str, Any]] = []
    object_paths: dict[str, str] = {}
    semantic_maps = {
        "SM_DoupoTDGamePlayer": {
            "finishWave": "server_finished_wave",
            "rewardResults": "server_reward_result_list",
            "passLevelVOS": "server_passed_level_id_list",
            "levelId": "server_current_level_id",
            "gameType": "server_game_type",
            "isSkipLevel": "server_skip_level_flag",
            "wavePercent": "server_wave_progress_percent",
        },
        "RewardResult": {
            "type": "reward_type",
            "code": "reward_code",
            "amount": "reward_amount",
            "content": "optional_reward_content_bean",
            "mail": "mail_delivery_flag",
            "isFirstGet": "first_acquire_flag",
            "additions": "extra_reward_map",
            "extraMark": "extra_reward_marker",
        },
        "DoupoTDPassLevelVO": {
            "levelSourceType": "level_source_type",
            "passLevelIds": "passed_level_id_list",
        },
    }
    for object_name in ("SM_DoupoTDGamePlayer", "RewardResult", "DoupoTDPassLevelVO"):
        path, rows = _parse_doupotd_message_io_fields(root, object_name, semantic_maps.get(object_name))
        if path is not None:
            object_paths[object_name] = str(path.relative_to(root))
        field_rows.extend(rows)
    evidence_rows = _collect_doupotd_gameplayer_result_evidence(root, output_dir)
    evidence_text = "\n".join(str(row.get("snippet") or "") for row in evidence_rows)
    stats = {
        "message_field_count": len(field_rows),
        "sm_gameplayer_field_count": sum(1 for row in field_rows if row.get("object_name") == "SM_DoupoTDGamePlayer"),
        "reward_result_field_count": sum(1 for row in field_rows if row.get("object_name") == "RewardResult"),
        "pass_level_vo_field_count": sum(1 for row in field_rows if row.get("object_name") == "DoupoTDPassLevelVO"),
        "evidence_row_count": len(evidence_rows),
        "reward_result_consumer_evidence_count": len(
            [row for row in evidence_rows if re.search(r"rewardResults|AddRewardResults|ItemScrollView", str(row.get("snippet") or ""))]
        ),
        "pass_level_consumer_evidence_count": len(
            [row for row in evidence_rows if re.search(r"passLevelVOS|InitNewLevelDic|IsFinishLevel|Contains", str(row.get("snippet") or ""))]
        ),
        "skip_level_consumer_evidence_count": len(
            [row for row in evidence_rows if re.search(r"isSkipLevel|DoupoTDInfoUpdate", str(row.get("snippet") or ""))]
        ),
    }
    verdict = {
        "reward_results_are_server_returned_reward_list": "server_reward_result_list" in {
            str(row.get("semantic") or "") for row in field_rows
        }
        and bool(re.search(r"AddRewardResults|ItemScrollView", evidence_text)),
        "pass_level_vos_are_consumed_as_level_id_list": bool(
            re.search(r"writeIntList\(self\.passLevelVOS\)|passLevelVOS:Contains|InitNewLevelDic\(msg\.passLevelVOS", evidence_text)
        )
        or any(
            row.get("object_name") == "SM_DoupoTDGamePlayer"
            and row.get("field_name") == "passLevelVOS"
            and row.get("write_method") == "IntList"
            for row in field_rows
        ),
        "gameplayer_response_is_server_result_boundary": stats["reward_result_consumer_evidence_count"] > 0
        and stats["pass_level_consumer_evidence_count"] > 0,
        "static_boundary_only": True,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    fields_tsv = output_dir / "lua_lscript_module_doupotd_gameplayer_result_fields.tsv"
    evidence_tsv = output_dir / "lua_lscript_module_doupotd_gameplayer_result_evidence.tsv"
    report_path = output_dir / "lua_lscript_module_doupotd_gameplayer_result_report.md"
    json_path = output_dir / "lua_lscript_module_doupotd_gameplayer_result_report.json"
    _write_tsv(
        fields_tsv,
        field_rows,
        ["object_name", "object_id", "field_index", "field_name", "read_method", "write_method", "semantic", "source_file", "line"],
    )
    _write_tsv(evidence_tsv, evidence_rows, ["source", "category", "function_name", "line", "snippet"])
    _write_doupotd_gameplayer_result_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "verdict": verdict,
                "object_paths": object_paths,
                "files": {
                    "fields": str(fields_tsv),
                    "evidence": str(evidence_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "object_paths": object_paths,
        "files": {
            "fields": str(fields_tsv),
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _join_reward_text(rewards: list[dict[str, Any]]) -> str:
    return "；".join(str(reward.get("text") or reward.get("raw") or "") for reward in rewards if reward.get("text") or reward.get("raw"))


def _reward_config_summary_row(
    source_table: str,
    row: dict[str, Any],
    *,
    reward_field: str,
    item_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rewards = _compact_reward_items(row.get(reward_field), item_by_id)
    raw_value = row.get(reward_field)
    return {
        "source_table": source_table,
        "config_id": row.get("id"),
        "different": row.get("different"),
        "stage": row.get("stage"),
        "layer": row.get("layer"),
        "sub_layer": row.get("subLayer"),
        "show_pos_id": row.get("ShowPosId"),
        "name": _plain(row.get("name") or row.get("title") or row.get("rewardShowTitle_plain") or ""),
        "reward_title": row.get("rewardShowTitle_plain") or _plain(row.get("rewardShowTitle") or row.get("title") or ""),
        "show_img": row.get("showImg"),
        "reward_field": reward_field,
        "reward_count": len(rewards),
        "reward_item_ids": "|".join(str(reward.get("id") or "") for reward in rewards),
        "reward_items": _join_reward_text(rewards),
        "raw_rewards": json.dumps(raw_value, ensure_ascii=False, separators=(",", ":")) if raw_value not in (None, "") else "",
    }


def _flatten_reward_config_items(summary_row: dict[str, Any], rewards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, reward in enumerate(rewards, 1):
        item = reward.get("item") if isinstance(reward.get("item"), dict) else {}
        rows.append(
            {
                "source_table": summary_row.get("source_table"),
                "config_id": summary_row.get("config_id"),
                "different": summary_row.get("different"),
                "stage": summary_row.get("stage"),
                "layer": summary_row.get("layer"),
                "sub_layer": summary_row.get("sub_layer"),
                "reward_index": index,
                "reward_type": reward.get("type"),
                "item_id": reward.get("id"),
                "item_name": item.get("name") or "",
                "quality_name": item.get("quality_name") or "",
                "count": reward.get("count"),
                "extra_mark": reward.get("extra_mark"),
                "text": reward.get("text") or "",
                "raw": reward.get("raw") or "",
                "reward_title": summary_row.get("reward_title"),
            }
        )
    return rows


def _collect_doupotd_reward_config_evidence(root: Path) -> list[dict[str, Any]]:
    targets = {
        "DoupoTDData.lua": re.compile(r"FormatStrArr2Reward\(v\.reward\)|GetPreRewardDataList", re.I),
        "DoupoTDInfoPanel.lua": re.compile(r"showLevelCfg\.reward|FormatStrArr2Reward\(self\.showLevelCfg\.reward\)", re.I),
        "DoupoTDMgr.lua": re.compile(r"DoupoPreLevelReward|rewardShow|GetItemIcon", re.I),
        "DoupoTDNetLogic.lua": re.compile(r"SM_DoupoTDGamePlayerFun|rewardResults|AddRewardResults", re.I),
        "DoupoTDResultInfoView.lua": re.compile(r"rewardResults|ItemScrollView", re.I),
    }
    rows: list[dict[str, Any]] = []
    for text_dir in _doupotd_lscript_text_asset_dirs(root):
        for file_name, pattern in targets.items():
            path = text_dir / file_name
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("--") or not pattern.search(stripped):
                    continue
                rows.append(
                    {
                        "source_file": str(path.relative_to(root)),
                        "line": line_no,
                        "category": file_name.removesuffix(".lua"),
                        "snippet": stripped[:260],
                    }
                )
    return rows


def _write_doupotd_reward_config_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    level_rows: list[dict[str, Any]],
    prelevel_rows: list[dict[str, Any]],
    reward_item_rows: list[dict[str, Any]],
    monster_drop_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# doupotd Reward config report",
        "",
        "Static read-only drilldown for doupotd level reward config, preview reward config, monster drop group ids, and the client/server result boundary.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Level Reward Samples", ""])
    for row in level_rows[:20]:
        lines.append(
            f"- Level `{row.get('config_id')}` `{row.get('name')}` stage `{row.get('stage')}`: {row.get('reward_items')}"
        )
    lines.extend(["", "## Preview Reward Samples", ""])
    for row in prelevel_rows[:20]:
        lines.append(
            f"- Preview `{row.get('config_id')}` `{row.get('name')}` stage `{row.get('stage')}`: {row.get('reward_items')}"
        )
    lines.extend(["", "## Monster Drop Group Samples", ""])
    for row in monster_drop_rows[:20]:
        lines.append(
            f"- Monster `{row.get('monster_id')}` base `{row.get('base_id')}` type `{row.get('monster_type')}` drops group `{row.get('drops')}` weight `{row.get('weight')}`"
        )
    lines.extend(["", "## Reward Item Samples", ""])
    for row in reward_item_rows[:40]:
        lines.append(
            f"- `{row.get('source_table')}` `{row.get('config_id')}` #{row.get('reward_index')}: `{row.get('reward_type')}` `{row.get('item_id')}` `{row.get('item_name')}` count `{row.get('count')}` extra `{row.get('extra_mark')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in evidence_rows[:60]:
        lines.append(
            f"- `{row.get('category')}` `{row.get('source_file')}:{row.get('line')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "`Level.reward` and `DoupoPreLevelReward.rewardShow` are visible client config/display candidates parsed through common reward formatting helpers. Actual settlement still crosses the `SM_DoupoTDGamePlayer.rewardResults` server response boundary; this report does not prove server-side reward minting logic.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_reward_config_probe(
    *,
    tower_defense_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    tower_dir = _resolve_export_dir(tower_defense_config_dir, export_root=export_root) or _find_default_config_dir(
        root,
        DEFAULT_TOWER_DEFENSE_DIR_PATTERN,
        "DoupoTowerDefense",
    )
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None
    item_by_id = _load_items_by_id(root)

    raw_level_rows = _parse_config_rows(tower_dir, "Level", resolved_lang_path, lang_map)
    raw_prelevel_rows = _parse_config_rows(tower_dir, "DoupoPreLevelReward", resolved_lang_path, lang_map)
    raw_monster_rows = _parse_config_rows(tower_dir, "MonsterGroup", resolved_lang_path, lang_map)

    level_rows: list[dict[str, Any]] = []
    prelevel_rows: list[dict[str, Any]] = []
    reward_item_rows: list[dict[str, Any]] = []
    for row in sorted(raw_level_rows, key=lambda item: (_sort_value(item.get("different")), _sort_value(item.get("stage")), _sort_value(item.get("layer")), _sort_value(item.get("id")))):
        rewards = _compact_reward_items(row.get("reward"), item_by_id)
        if not rewards:
            continue
        summary_row = _reward_config_summary_row("Level", row, reward_field="reward", item_by_id=item_by_id)
        level_rows.append(summary_row)
        reward_item_rows.extend(_flatten_reward_config_items(summary_row, rewards))

    for row in sorted(raw_prelevel_rows, key=lambda item: (_sort_value(item.get("different")), _sort_value(item.get("stage")), _sort_value(item.get("id")))):
        rewards = _compact_reward_items(row.get("rewardShow"), item_by_id)
        if not rewards:
            continue
        summary_row = _reward_config_summary_row("DoupoPreLevelReward", row, reward_field="rewardShow", item_by_id=item_by_id)
        prelevel_rows.append(summary_row)
        reward_item_rows.extend(_flatten_reward_config_items(summary_row, rewards))

    monster_drop_rows = [
        {
            "monster_id": row.get("id"),
            "base_id": row.get("baseId"),
            "monster_type": row.get("type"),
            "model_id": row.get("modelId"),
            "drops": row.get("drops"),
            "weight": row.get("weight"),
        }
        for row in sorted(raw_monster_rows, key=lambda item: (_sort_value(item.get("id")), _sort_value(item.get("baseId"))))
        if row.get("drops") not in (None, "", 0)
    ]
    evidence_rows = _collect_doupotd_reward_config_evidence(root)
    evidence_text = "\n".join(str(row.get("snippet") or "") for row in evidence_rows)
    stats = {
        "level_config_count": len(raw_level_rows),
        "level_reward_row_count": len(level_rows),
        "prelevel_config_count": len(raw_prelevel_rows),
        "prelevel_reward_row_count": len(prelevel_rows),
        "reward_item_row_count": len(reward_item_rows),
        "unique_reward_item_count": len({str(row.get("item_id") or "") for row in reward_item_rows if row.get("item_id") not in (None, "")}),
        "monster_group_count": len(raw_monster_rows),
        "monster_drop_group_ref_count": len(monster_drop_rows),
        "evidence_row_count": len(evidence_rows),
    }
    verdict = {
        "level_reward_is_client_display_config": bool(re.search(r"FormatStrArr2Reward\(v\.reward\)|showLevelCfg\.reward", evidence_text)),
        "prelevel_reward_show_is_preview_config": bool(re.search(r"DoupoPreLevelReward|rewardShow|GetItemIcon", evidence_text)),
        "server_result_boundary_remains_rewardResults": bool(re.search(r"SM_DoupoTDGamePlayerFun|rewardResults|AddRewardResults", evidence_text)),
        "static_config_only": True,
    }

    output_dir = root / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    levels_tsv = output_dir / "lua_lscript_module_doupotd_reward_config_levels.tsv"
    prelevel_tsv = output_dir / "lua_lscript_module_doupotd_reward_config_prelevel_rewards.tsv"
    items_tsv = output_dir / "lua_lscript_module_doupotd_reward_config_items.tsv"
    monster_tsv = output_dir / "lua_lscript_module_doupotd_reward_config_monster_drops.tsv"
    evidence_tsv = output_dir / "lua_lscript_module_doupotd_reward_config_evidence.tsv"
    report_path = output_dir / "lua_lscript_module_doupotd_reward_config_report.md"
    json_path = output_dir / "lua_lscript_module_doupotd_reward_config_report.json"
    summary_fields = [
        "source_table",
        "config_id",
        "different",
        "stage",
        "layer",
        "sub_layer",
        "show_pos_id",
        "name",
        "reward_title",
        "show_img",
        "reward_field",
        "reward_count",
        "reward_item_ids",
        "reward_items",
        "raw_rewards",
    ]
    _write_tsv(levels_tsv, level_rows, summary_fields)
    _write_tsv(prelevel_tsv, prelevel_rows, summary_fields)
    _write_tsv(
        items_tsv,
        reward_item_rows,
        [
            "source_table",
            "config_id",
            "different",
            "stage",
            "layer",
            "sub_layer",
            "reward_index",
            "reward_type",
            "item_id",
            "item_name",
            "quality_name",
            "count",
            "extra_mark",
            "text",
            "raw",
            "reward_title",
        ],
    )
    _write_tsv(monster_tsv, monster_drop_rows, ["monster_id", "base_id", "monster_type", "model_id", "drops", "weight"])
    _write_tsv(evidence_tsv, evidence_rows, ["source_file", "line", "category", "snippet"])
    _write_doupotd_reward_config_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        level_rows=level_rows,
        prelevel_rows=prelevel_rows,
        reward_item_rows=reward_item_rows,
        monster_drop_rows=monster_drop_rows,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "source": {
                    "tower_defense_config_dir": str(tower_dir),
                    "lang_path": str(resolved_lang_path or ""),
                },
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "levels": level_rows[:30],
                    "prelevel_rewards": prelevel_rows[:30],
                    "reward_items": reward_item_rows[:80],
                    "monster_drops": monster_drop_rows[:80],
                    "evidence": evidence_rows[:80],
                },
                "files": {
                    "levels": str(levels_tsv),
                    "prelevel_rewards": str(prelevel_tsv),
                    "reward_items": str(items_tsv),
                    "monster_drops": str(monster_tsv),
                    "evidence": str(evidence_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "levels": str(levels_tsv),
            "prelevel_rewards": str(prelevel_tsv),
            "reward_items": str(items_tsv),
            "monster_drops": str(monster_tsv),
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _relative_source(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _find_lua_asset_by_name(root: Path, asset_name: str) -> Path | None:
    candidates = [path for path in root.glob(f"by_source/**/text_assets/{asset_name}") if path.is_file()]
    if not candidates:
        candidates = [path for path in root.glob(f"**/{asset_name}") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.stat().st_size, item.stat().st_mtime_ns))


def _find_lua_asset_by_name_containing(root: Path, asset_name: str, marker: str) -> Path | None:
    candidates = [path for path in root.glob(f"by_source/**/text_assets/{asset_name}") if path.is_file()]
    if not candidates:
        candidates = [path for path in root.glob(f"**/{asset_name}") if path.is_file()]
    matched: list[Path] = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if marker in text:
            matched.append(path)
    if not matched:
        return None
    return max(matched, key=lambda item: (item.stat().st_size, item.stat().st_mtime_ns))


def _scan_lua_evidence(
    root: Path,
    path: Path | None,
    patterns: list[tuple[str, re.Pattern[str]]],
    *,
    max_per_category: int = 8,
) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        for category, pattern in patterns:
            if counts[category] >= max_per_category or not pattern.search(stripped):
                continue
            counts[category] += 1
            rows.append(
                {
                    "source_file": _relative_source(path, root),
                    "line": line_no,
                    "category": category,
                    "target": path.name,
                    "snippet": stripped[:320],
                }
            )
    return rows


def _parse_reward_type_lua(path: Path | None) -> tuple[dict[str, int], dict[str, int]]:
    if path is None or not path.is_file():
        return {}, {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    reward_types: dict[str, int] = {}
    for match in re.finditer(r"_M\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+)", text):
        name = match.group(1)
        if name == "ExtraMark":
            continue
        reward_types[name] = int(match.group(2))
    extra_marks: dict[str, int] = {}
    extra_match = re.search(r"_M\.ExtraMark\s*=\s*\{(?P<body>.*?)\}", text, re.S)
    if extra_match:
        for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+)", extra_match.group("body")):
            extra_marks[match.group(1)] = int(match.group(2))
    return reward_types, extra_marks


def _find_item_corner_path(root: Path) -> Path | None:
    candidates = [path for path in root.glob(DEFAULT_ITEM_CORNER_PATTERN) if path.is_file()]
    if not candidates:
        candidates = [path for path in root.glob("by_source/**/text_assets/ItemCorner.lua") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.stat().st_size, item.stat().st_mtime_ns))


def _load_item_corner_rows(
    root: Path,
    *,
    lang_path: Path | None,
    lang_map: dict[int, str] | None,
) -> tuple[Path | None, list[dict[str, Any]], dict[int, dict[str, Any]]]:
    path = _find_item_corner_path(root)
    if path is None:
        return None, [], {}
    parsed = parse_fanxiu_generated_lua_config(path, lang_path=lang_path, lang_map=lang_map)
    rows = list(parsed.get("rows") or [])
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        row_id = _as_int(row.get("id") if row.get("id") not in (None, "") else row.get("_row_key"))
        if row_id is not None:
            by_id[row_id] = row
    return path, rows, by_id


def _collect_reward_result_resolution_flow(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reward_type_path = _find_lua_asset_by_name(root, "RewardType.lua")
    cost_path = _find_lua_asset_by_name(root, "CostAndRewardMgr.lua")
    game_util_path = _find_lua_asset_by_name(root, "GameUtil.lua")
    reward_result_path = _find_lua_asset_by_name(root, "RewardResult.lua")

    rows.extend(
        _scan_lua_evidence(
            root,
            reward_type_path,
            [
                ("reward_type_enum", re.compile(r"_M\.ITEM\s*=\s*0|_M\.ExtraMark", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            reward_result_path,
            [
                ("reward_result_schema", re.compile(r"self\.(type|code|amount|extraMark)|Read(Int|Long)|Write(Int|Long)", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            cost_path,
            [
                ("add_reward_results", re.compile(r"function\s+_M\.AddRewardResults|AddRewardResults", re.I)),
                ("format_str_to_reward", re.compile(r"function\s+_M\.FormatStr2Reward|_RewardResult\.new|reward\.code\s*=|reward\.type\s*=\s*RewardType\.ITEM", re.I)),
                ("amount_assignment", re.compile(r"reward\.amount\s*=", re.I)),
                ("extra_mark_assignment", re.compile(r"reward\.extraMark\s*=", re.I)),
                ("item_direct_resolution", re.compile(r"reward\.type\s*==\s*RewardType\.ITEM|ConfigName\.Item_Item\s*,\s*reward\.code", re.I)),
                ("extra_mark_faze", re.compile(r"RewardType\.ExtraMark\.Faze|reward\.extraMark", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            game_util_path,
            [
                ("get_item_icon", re.compile(r"function\s+_M\.GetItemIcon|extraMark\s*=|ConfigNameMap|string\.split", re.I)),
                ("get_reward_result", re.compile(r"function\s+_M\.GetRewardResult|FormatStr2Reward\(str\)", re.I)),
                ("reward_result_resolution", re.compile(r"function\s+_M\.GetItemCfgByRewardResult|GetItemCfgByRewardTypeAndCode\(rewardType,code\)", re.I)),
                ("reward_type_to_config", re.compile(r"function\s+_M\.GetItemCfgByRewardTypeAndCode|rewardType\s*==\s*RewardType\.ITEM|ConfigName\.Item_Item\s*,\s*code", re.I)),
                ("additions_split", re.compile(r"function\s+_M\.ConvertRewardByResource|data\.extraMark\s*=", re.I)),
                ("consume_merge", re.compile(r"function\s+_M\.ConsumeReward|v\.code\s*==\s*j\.code\s+and\s+v\.extraMark\s*==\s*j\.extraMark", re.I)),
                ("sort_extra_mark", re.compile(r"function\s+_M\.SortReward|RewardType\.ExtraMark\.FirstGet|extraMark", re.I)),
                ("extra_mark_corner", re.compile(r"function\s+_M\.UpdateItemCornet|ConfigName\.Item_ItemCorner", re.I)),
            ],
        )
    )

    protocol_path = root / "parsed_configs" / "lua_packet_index" / "protocol_catalog_canonical.tsv"
    for row in _read_tsv_dicts(protocol_path):
        if row.get("name") != "RewardResult":
            continue
        rows.append(
            {
                "source_file": _relative_source(protocol_path, root),
                "line": "",
                "category": "reward_result_schema",
                "target": "protocol_catalog_canonical.tsv",
                "snippet": f"RewardResult read_fields={row.get('read_fields') or ''} write_fields={row.get('write_fields') or ''}"[:320],
            }
        )
        break
    return rows


def _reward_type_from_token(token: Any, reward_types: dict[str, int]) -> tuple[int | None, str]:
    normalized = str(token or "").strip().upper()
    if normalized == "ITEM":
        return reward_types.get("ITEM", 0), "ITEM"
    if normalized in reward_types:
        return reward_types[normalized], normalized
    return None, normalized


def _extra_mark_label(
    extra_mark: int,
    *,
    extra_marks: dict[str, int],
    item_corner: dict[str, Any] | None,
) -> str:
    inverse_extra_marks = {value: key for key, value in extra_marks.items()}
    enum_name = inverse_extra_marks.get(extra_mark)
    if enum_name:
        return f"RewardType.ExtraMark.{enum_name}"
    if item_corner:
        name = _plain(item_corner.get("name") or item_corner.get("desc") or item_corner.get("title") or "")
        if name:
            return name
        return f"ItemCorner#{extra_mark}"
    return ""


def _reward_result_resolution_rows(
    reward_item_rows: list[dict[str, str]],
    *,
    reward_types: dict[str, int],
    extra_marks: dict[str, int],
    item_corner_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in reward_item_rows:
        runtime_type, runtime_type_name = _reward_type_from_token(item.get("reward_type"), reward_types)
        amount = _as_int(item.get("count"))
        extra_mark = _as_int(item.get("extra_mark"))
        runtime_extra_mark = extra_mark if extra_mark is not None else 0
        corner = item_corner_by_id.get(runtime_extra_mark)
        note = ""
        if amount is not None and amount < 0:
            note = "Static preview uses a negative amount sentinel; runtime settlement amount still comes from server RewardResult.amount."
        elif extra_mark is None:
            note = "Static reward omits extraMark; FormatStr2Reward defaults RewardResult.extraMark to 0."
        rows.append(
            {
                "source_table": item.get("source_table") or "",
                "config_id": item.get("config_id") or "",
                "stage": item.get("stage") or "",
                "layer": item.get("layer") or "",
                "reward_index": item.get("reward_index") or "",
                "raw": item.get("raw") or "",
                "static_reward_type_token": item.get("reward_type") or "",
                "runtime_reward_type": runtime_type if runtime_type is not None else "",
                "runtime_reward_type_name": runtime_type_name,
                "code": item.get("item_id") or "",
                "item_name": item.get("item_name") or "",
                "quality_name": item.get("quality_name") or "",
                "amount": amount if amount is not None else "",
                "extra_mark": runtime_extra_mark,
                "extra_mark_name": _extra_mark_label(runtime_extra_mark, extra_marks=extra_marks, item_corner=corner),
                "extra_mark_show_type": corner.get("showType") if corner else "",
                "extra_mark_eff_name": corner.get("effName") if corner else "",
                "resolution_rule": (
                    f"RewardType.{runtime_type_name}({runtime_type}) => ConfigName.Item_Item[code]"
                    if runtime_type_name == "ITEM" and runtime_type is not None
                    else "Non-ITEM reward type needs its own GameUtil.GetItemCfgByRewardTypeAndCode branch"
                ),
                "note": note,
            }
        )
    return rows


def _write_doupotd_reward_result_resolution_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    item_rows: list[dict[str, Any]],
    flow_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# doupotd RewardResult resolution report",
        "",
        "Static read-only drilldown for how doupotd reward config strings map into the shared RewardResult shape and then resolve to item config, amount, and extraMark corner/effect metadata.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Field Semantics",
            "",
            "- `type`: reward category. For doupotd static item strings, `Item|...` becomes `RewardType.ITEM` (`0`).",
            "- `code`: item id when `type == RewardType.ITEM`; `GameUtil.GetItemCfgByRewardTypeAndCode` resolves it through `ConfigName.Item_Item`.",
            "- `amount`: reward quantity. Static preview rows can contain display sentinels such as `-1`; server settlement still arrives as `RewardResult.amount`.",
            "- `extraMark`: not the count. It is preserved during split/merge/sort and can resolve to `ConfigName.Item_ItemCorner` for corner text/effects.",
            "",
            "## Reward Item Samples",
            "",
        ]
    )
    for row in item_rows[:80]:
        lines.append(
            f"- `{row.get('source_table')}` `{row.get('config_id')}` #{row.get('reward_index')}: type `{row.get('runtime_reward_type_name')}` code `{row.get('code')}` `{row.get('item_name')}` amount `{row.get('amount')}` extra `{row.get('extra_mark')}` `{row.get('extra_mark_name')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in flow_rows[:80]:
        lines.append(
            f"- `{row.get('category')}` `{row.get('source_file')}:{row.get('line')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This report proves the client-side static resolution/display chain. It does not prove the server-side settlement formula; live `SM_DoupoTDGamePlayer.rewardResults` samples are still required for final amount calibration.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_reward_result_resolution_probe(
    *,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    reward_config_paths = _doupotd_reward_config_file_paths(root)
    if not reward_config_paths["levels"].is_file() or not reward_config_paths["reward_items"].is_file():
        build_fanxiu_doupotd_reward_config_probe(lang_path=resolved_lang_path, export_root=root)
    reward_item_rows = _read_tsv_dicts(reward_config_paths["reward_items"])
    reward_type_path = _find_lua_asset_by_name(root, "RewardType.lua")
    reward_types, extra_marks = _parse_reward_type_lua(reward_type_path)
    item_corner_path, item_corner_rows, item_corner_by_id = _load_item_corner_rows(
        root,
        lang_path=resolved_lang_path,
        lang_map=lang_map,
    )
    resolution_rows = _reward_result_resolution_rows(
        reward_item_rows,
        reward_types=reward_types,
        extra_marks=extra_marks,
        item_corner_by_id=item_corner_by_id,
    )
    flow_rows = _collect_reward_result_resolution_flow(root)
    flow_text = "\n".join(str(row.get("snippet") or "") for row in flow_rows)
    stats = {
        "reward_config_item_count": len(reward_item_rows),
        "resolved_item_reward_count": sum(1 for row in resolution_rows if row.get("runtime_reward_type_name") == "ITEM"),
        "unique_item_count": len({str(row.get("code") or "") for row in resolution_rows if row.get("code") not in (None, "")}),
        "unique_runtime_extra_mark_count": len({str(row.get("extra_mark") or 0) for row in resolution_rows}),
        "nonzero_extra_mark_row_count": sum(1 for row in resolution_rows if _as_int(row.get("extra_mark")) not in (None, 0)),
        "negative_amount_row_count": sum(1 for row in resolution_rows if (_as_int(row.get("amount")) or 0) < 0),
        "reward_type_enum_count": len(reward_types),
        "extra_mark_enum_count": len(extra_marks),
        "item_corner_count": len(item_corner_rows),
        "flow_evidence_count": len(flow_rows),
    }
    verdict = {
        "static_reward_string_shape_matches_reward_result": bool(re.search(r"FormatStr2Reward|_RewardResult\.new|RewardResult read_fields", flow_text)),
        "item_reward_type_resolves_code_to_item_table": reward_types.get("ITEM") == 0 and bool(re.search(r"RewardType\.ITEM|ConfigName\.Item_Item", flow_text)),
        "amount_maps_to_reward_result_amount": bool(re.search(r"reward\.amount|amount:Long", flow_text)),
        "extra_mark_resolves_to_item_corner": bool(item_corner_rows) and bool(re.search(r"Item_ItemCorner|UpdateItemCornet", flow_text)),
        "extra_mark_preserved_in_split_merge_sort": bool(re.search(r"data\.extraMark|v\.extraMark|FirstGet", flow_text)),
        "runtime_values_still_require_server_sample": True,
    }

    output_dir = root / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    items_tsv = output_dir / "lua_lscript_module_doupotd_reward_result_resolution_items.tsv"
    flow_tsv = output_dir / "lua_lscript_module_doupotd_reward_result_resolution_flow.tsv"
    report_path = output_dir / "lua_lscript_module_doupotd_reward_result_resolution_report.md"
    json_path = output_dir / "lua_lscript_module_doupotd_reward_result_resolution_report.json"
    item_fields = [
        "source_table",
        "config_id",
        "stage",
        "layer",
        "reward_index",
        "raw",
        "static_reward_type_token",
        "runtime_reward_type",
        "runtime_reward_type_name",
        "code",
        "item_name",
        "quality_name",
        "amount",
        "extra_mark",
        "extra_mark_name",
        "extra_mark_show_type",
        "extra_mark_eff_name",
        "resolution_rule",
        "note",
    ]
    _write_tsv(items_tsv, resolution_rows, item_fields)
    _write_tsv(flow_tsv, flow_rows, ["source_file", "line", "category", "target", "snippet"])
    _write_doupotd_reward_result_resolution_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        item_rows=resolution_rows,
        flow_rows=flow_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "source": {
                    "reward_items": str(reward_config_paths["reward_items"]),
                    "reward_type_lua": str(reward_type_path or ""),
                    "item_corner_lua": str(item_corner_path or ""),
                    "lang_path": str(resolved_lang_path or ""),
                },
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "items": resolution_rows[:120],
                    "flow": flow_rows[:120],
                },
                "files": {
                    "items": str(items_tsv),
                    "flow": str(flow_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "items": str(items_tsv),
            "flow": str(flow_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _parse_drop_condition_segments(value: Any) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for raw_part in _iter_reward_parts(value):
        parts = [part.strip() for part in raw_part.split("|")]
        row: dict[str, Any] = {
            "raw": raw_part,
            "scope": parts[0] if len(parts) > 0 else "",
            "key": parts[1] if len(parts) > 1 else "",
            "range": parts[2] if len(parts) > 2 else "",
        }
        if row["range"]:
            lower, _, upper = str(row["range"]).partition("_")
            row["min"] = _as_int(lower)
            row["max"] = _as_int(upper)
        segments.append(row)
    return segments


def _format_drop_condition_summary(segments: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for segment in segments:
        key = str(segment.get("key") or "")
        minimum = segment.get("min")
        maximum = segment.get("max")
        if key and minimum is not None and maximum is not None:
            parts.append(f"{key} {minimum}-{maximum}")
        elif segment.get("raw"):
            parts.append(str(segment["raw"]))
    return "; ".join(parts)


def _find_drop_itemteam_usage_paths(root: Path) -> list[Path]:
    lscript_root = root / "by_source" / "lscripts"
    if not lscript_root.is_dir():
        return []
    matches: list[Path] = []
    for path in lscript_root.rglob("*.lua"):
        if path.name == "ItemTeam.lua":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "ItemTeam" in text or "Drop.ItemTeam" in text or "Drop_ItemTeam" in text:
            matches.append(path)
    matches.sort(key=lambda item: str(item))
    return matches


def _collect_doupotd_monster_drop_resolution_evidence(
    root: Path,
    *,
    itemteam_usage_paths: list[Path],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "DropNetLogic.lua"),
            [
                ("server_drop_objects_message", re.compile(r"SM_DropObjectsFun|DropItemMethod\(msg\.dropObjectVO\)", re.I)),
                ("pickup_reward_results_message", re.compile(r"SM_PickUpFun|pickUpVO\.rewardResults|AddRewardResults", re.I)),
                ("server_scene_drop_info_message", re.compile(r"SM_DropInfoFun|CreateSceneDrop", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "DropMgr.lua"),
            [
                ("client_drop_render_uses_server_unit", re.compile(r"function\s+_M\.DropItemMethod|dropMsgList\.dropMap|dropItem\.rewardItem\.code", re.I)),
                ("client_store_bag_uses_server_unit", re.compile(r"function\s+_M\.SetBagInfo|CreateStoreBag|bagUnitVO", re.I)),
                ("client_pickup_visual_only", re.compile(r"function\s+_M\.CreateDropView|GetDropItemEffect|V_Code=dropItem\.rewardItem\.code", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "DropObjectVO.lua"),
            [
                ("drop_object_schema", re.compile(r"dropWhich|dropMonster|dropMap|readMessageMap2Dic", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "DropUnitVO.lua"),
            [
                ("drop_unit_schema", re.compile(r"dropMonster|rewardItem|RewardItem|readBean", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "RewardItem.lua"),
            [
                ("reward_item_schema", re.compile(r"self\.(type|code|amount|extraMark)|read(Int|Long|String)", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "PickUpVO.lua"),
            [
                ("pickup_schema", re.compile(r"rewardResults|successList|readMessageList2List", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "ConfigName.lua"),
            [
                ("drop_config_names", re.compile(r"Drop_(DropTeam|DropItem|StoreContentBag|ConfigValue)|Drop\.(DropTeam|DropItem|StoreContentBag|ConfigValue)", re.I)),
            ],
        )
    )
    for path in itemteam_usage_paths[:40]:
        rows.append(
            {
                "source_file": _relative_source(path, root),
                "line": "",
                "category": "itemteam_client_usage",
                "target": path.name,
                "snippet": "contains ItemTeam reference outside generated Drop.ItemTeam config",
            }
        )
    return rows


def _write_doupotd_monster_drop_resolution_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    group_rows: list[dict[str, Any]],
    item_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# doupotd Monster drop resolution report",
        "",
        "Static read-only drilldown for `DoupoTowerDefense.MonsterGroup.drops`. The probe expands matching `Drop.ItemTeam.itemTeam` rows as candidate drop-table content, then records the client/server runtime boundary for actual drop spawning and pickup settlement.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Drop Group Samples", ""])
    for row in group_rows[:40]:
        lines.append(
            f"- Monster `{row.get('monster_id')}` base `{row.get('base_id')}` drops `{row.get('drop_group_id')}`: {row.get('candidate_itemteam_count')} candidate rows, {row.get('candidate_condition_summary')}"
        )
    lines.extend(["", "## Candidate Item Samples", ""])
    for row in item_rows[:80]:
        lines.append(
            f"- Monster `{row.get('monster_id')}` group `{row.get('drop_group_id')}` itemTeam row `{row.get('itemteam_row_id')}`: `{row.get('reward_type')}` `{row.get('item_id')}` `{row.get('item_name')}` count `{row.get('count')}` extra `{row.get('extra_mark')}` condition `{row.get('condition_summary')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in evidence_rows[:80]:
        lines.append(
            f"- `{row.get('category')}` `{row.get('source_file')}:{row.get('line')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "`MonsterGroup.drops` can be matched to static `Drop.ItemTeam.itemTeam` ids in the exported client config, but the observed client runtime path renders server-provided `DropObjectVO.dropMap -> DropUnitVO.rewardItem` and final pickup rewards arrive through `PickUpVO.rewardResults`. Treat the expanded ItemTeam rows as candidate/shared-static config, not proof of server-side drop-roll formulas.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_monster_drop_resolution_probe(
    *,
    tower_defense_config_dir: str | Path | None = None,
    drop_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    tower_dir = _resolve_export_dir(tower_defense_config_dir, export_root=export_root) or _find_default_config_dir(
        root,
        DEFAULT_TOWER_DEFENSE_DIR_PATTERN,
        "DoupoTowerDefense",
    )
    drop_dir = _resolve_export_dir(drop_config_dir, export_root=export_root) or _find_default_config_dir(
        root,
        DEFAULT_DROP_CONFIG_DIR_PATTERN,
        "Drop",
    )
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None
    item_by_id = _load_items_by_id(root)

    monster_rows = _parse_config_rows(tower_dir, "MonsterGroup", resolved_lang_path, lang_map)
    itemteam_rows = _parse_config_rows(drop_dir, "ItemTeam", resolved_lang_path, lang_map)
    itemteam_by_group = _group_by_int(itemteam_rows, "itemTeam")
    itemteam_usage_paths = _find_drop_itemteam_usage_paths(root)

    group_rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    for monster in sorted(monster_rows, key=lambda item: (_sort_value(item.get("id")), _sort_value(item.get("baseId")))):
        drop_group_id = _as_int(monster.get("drops"))
        if drop_group_id is None or drop_group_id == 0:
            continue
        candidates = sorted(itemteam_by_group.get(drop_group_id, []), key=lambda item: _sort_value(item.get("id")))
        condition_summaries: list[str] = []
        raw_items: list[str] = []
        for candidate in candidates:
            raw_reward = candidate.get("itemId") or ""
            raw_items.append(str(raw_reward))
            condition_segments = _parse_drop_condition_segments(candidate.get("condition"))
            condition_summary = _format_drop_condition_summary(condition_segments)
            if condition_summary:
                condition_summaries.append(condition_summary)
            rewards = _compact_reward_items(raw_reward, item_by_id)
            reward = rewards[0] if rewards else {}
            reward_item = reward.get("item") if isinstance(reward.get("item"), dict) else {}
            item_rows.append(
                {
                    "monster_id": monster.get("id"),
                    "base_id": monster.get("baseId"),
                    "monster_type": monster.get("type"),
                    "model_id": monster.get("modelId"),
                    "drop_group_id": drop_group_id,
                    "itemteam_row_id": candidate.get("id"),
                    "itemteam_id": candidate.get("itemTeam"),
                    "raw_reward": raw_reward,
                    "reward_type": reward.get("type") or "",
                    "item_id": reward.get("id") or "",
                    "item_name": reward_item.get("name") or str(reward.get("id") or ""),
                    "quality_name": reward_item.get("quality_name") or "",
                    "count": reward.get("count") if reward else "",
                    "extra_mark": reward.get("extra_mark") if reward else "",
                    "condition_raw": candidate.get("condition") or "",
                    "condition_summary": condition_summary,
                    "condition_parts_json": json.dumps(condition_segments, ensure_ascii=False, separators=(",", ":")),
                    "resolution_status": "candidate_static_itemteam" if candidates else "unresolved_drop_group",
                    "note": "Candidate from Drop.ItemTeam; final runtime drop unit and pickup settlement are server-provided.",
                }
            )
        group_rows.append(
            {
                "monster_id": monster.get("id"),
                "base_id": monster.get("baseId"),
                "monster_type": monster.get("type"),
                "model_id": monster.get("modelId"),
                "drop_group_id": drop_group_id,
                "monster_weight": monster.get("weight"),
                "candidate_itemteam_count": len(candidates),
                "candidate_raw_items": ",".join(raw_items),
                "candidate_condition_summary": " | ".join(_dedupe_preserve(condition_summaries)),
                "resolution_status": "candidate_static_itemteam" if candidates else "unresolved_drop_group",
                "note": "MonsterGroup.drops matched to Drop.ItemTeam.itemTeam" if candidates else "No Drop.ItemTeam row matched this drops id",
            }
        )

    evidence_rows = _collect_doupotd_monster_drop_resolution_evidence(root, itemteam_usage_paths=itemteam_usage_paths)
    evidence_text = "\n".join(str(row.get("snippet") or "") for row in evidence_rows)
    stats = {
        "monster_group_count": len(monster_rows),
        "monster_drop_group_ref_count": len(group_rows),
        "unique_monster_drop_group_ref_count": len({str(row.get("drop_group_id") or "") for row in group_rows}),
        "itemteam_row_count": len(itemteam_rows),
        "resolved_drop_group_ref_count": sum(1 for row in group_rows if row.get("resolution_status") == "candidate_static_itemteam"),
        "candidate_item_row_count": len(item_rows),
        "unique_candidate_item_count": len({str(row.get("item_id") or "") for row in item_rows if row.get("item_id") not in (None, "")}),
        "distinct_condition_count": len({str(row.get("condition_raw") or "") for row in item_rows if row.get("condition_raw")}),
        "itemteam_client_usage_evidence_count": len(itemteam_usage_paths),
        "evidence_row_count": len(evidence_rows),
    }
    verdict = {
        "monster_group_drops_has_static_itemteam_candidate": stats["resolved_drop_group_ref_count"] > 0,
        "drop_itemteam_has_no_client_runtime_consumer_seen": len(itemteam_usage_paths) == 0,
        "drop_spawn_uses_server_drop_objects": bool(re.search(r"SM_DropObjectsFun|DropItemMethod", evidence_text)),
        "client_drop_unit_contains_reward_item": bool(re.search(r"rewardItem|RewardItem", evidence_text)),
        "pickup_settlement_uses_server_reward_results": bool(re.search(r"rewardResults|SM_PickUpFun", evidence_text)),
        "static_candidate_not_server_formula": True,
    }

    output_dir = root / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    groups_tsv = output_dir / "lua_lscript_module_doupotd_monster_drop_resolution_groups.tsv"
    items_tsv = output_dir / "lua_lscript_module_doupotd_monster_drop_resolution_items.tsv"
    evidence_tsv = output_dir / "lua_lscript_module_doupotd_monster_drop_resolution_evidence.tsv"
    report_path = output_dir / "lua_lscript_module_doupotd_monster_drop_resolution_report.md"
    json_path = output_dir / "lua_lscript_module_doupotd_monster_drop_resolution_report.json"
    _write_tsv(
        groups_tsv,
        group_rows,
        [
            "monster_id",
            "base_id",
            "monster_type",
            "model_id",
            "drop_group_id",
            "monster_weight",
            "candidate_itemteam_count",
            "candidate_raw_items",
            "candidate_condition_summary",
            "resolution_status",
            "note",
        ],
    )
    _write_tsv(
        items_tsv,
        item_rows,
        [
            "monster_id",
            "base_id",
            "monster_type",
            "model_id",
            "drop_group_id",
            "itemteam_row_id",
            "itemteam_id",
            "raw_reward",
            "reward_type",
            "item_id",
            "item_name",
            "quality_name",
            "count",
            "extra_mark",
            "condition_raw",
            "condition_summary",
            "condition_parts_json",
            "resolution_status",
            "note",
        ],
    )
    _write_tsv(evidence_tsv, evidence_rows, ["source_file", "line", "category", "target", "snippet"])
    _write_doupotd_monster_drop_resolution_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        group_rows=group_rows,
        item_rows=item_rows,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "source": {
                    "tower_defense_config_dir": str(tower_dir),
                    "drop_config_dir": str(drop_dir),
                    "lang_path": str(resolved_lang_path or ""),
                },
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "groups": group_rows[:80],
                    "items": item_rows[:120],
                    "evidence": evidence_rows[:120],
                },
                "files": {
                    "groups": str(groups_tsv),
                    "items": str(items_tsv),
                    "evidence": str(evidence_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "groups": str(groups_tsv),
            "items": str(items_tsv),
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _collect_doupotd_store_bag_visual_evidence(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name_containing(root, "StoreContentBag.lua", "data.bagModel"),
            [
                ("store_bag_model_resolution", re.compile(r"data\.bagModel|ConfigName\.Drop_StoreContentBag|SetDisplayId|SetModelScale|V_Name", re.I)),
                ("store_bag_action_resolution", re.compile(r"appearAction|disappearAction|CtorActionByCfg|GetTriggerRange", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "StoreContentBagView.lua"),
            [
                ("store_bag_open_request", re.compile(r"Get_CM_OpenBag|OnInteract|F_Trigger", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "BagUnitVO.lua"),
            [
                ("bag_unit_schema", re.compile(r"bagModel|dropMonster|monsterId|canPickAccounts|read(Int|Long)", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "BagObjectVO.lua"),
            [
                ("bag_object_schema", re.compile(r"dropMonster|bagUnitVO|BagUnitVO|deadGrid", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "DropNetLogic.lua"),
            [
                ("bag_server_messages", re.compile(r"SM_BagInfo|SM_BagObjects|SM_OpenBag|SM_UnitOpenBag|CM_OpenBag|OpenStoreBag|SetBagInfo", re.I)),
                ("bag_open_request", re.compile(r"Get_CM_OpenBag|CM_OpenBag|bagIds:Add", re.I)),
            ],
        )
    )
    rows.extend(
        _scan_lua_evidence(
            root,
            _find_lua_asset_by_name(root, "DropMgr.lua"),
            [
                ("store_bag_creation", re.compile(r"CreateStoreBag|bagUnitVO|StoreContentBag\.new|LoadStoreBagViewCallBack", re.I)),
                ("store_bag_open_result", re.compile(r"OpenStoreBag|bagDrops|DoDisappearAction", re.I)),
            ],
        )
    )
    return rows


def _write_doupotd_store_bag_visual_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    bag_rows: list[dict[str, Any]],
    monster_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# doupotd StoreContentBag visual report",
        "",
        "Static read-only drilldown for `Drop.StoreContentBag` visual metadata and the server-provided `BagUnitVO.bagModel` runtime boundary.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Store Bag Samples", ""])
    for row in bag_rows[:40]:
        lines.append(
            f"- Bag `{row.get('bag_id')}` `{row.get('name')}` model `{row.get('model_id')}` zoom `{row.get('model_zoom')}` range `{row.get('range')}` appear `{row.get('appear_action')}` disappear `{row.get('disappear_action')}`"
        )
    lines.extend(["", "## Doupotd Monster Direct Match Samples", ""])
    for row in monster_rows[:60]:
        if row.get("match_status") == "no_direct_static_match":
            continue
        lines.append(
            f"- Monster `{row.get('monster_id')}` base `{row.get('base_id')}` -> `{row.get('candidate_bag_name')}` by `{row.get('match_kind')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in evidence_rows[:80]:
        lines.append(
            f"- `{row.get('category')}` `{row.get('source_file')}:{row.get('line')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "`Drop.StoreContentBag` explains the visual/interact metadata once `BagUnitVO.bagModel` is known. The client receives `bagModel` from server drop/bag messages; only a subset of doupotd monster ids/base ids directly match store-bag config ids, so static id matching must not be treated as the full monster-to-bag mapping.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_store_bag_visual_probe(
    *,
    tower_defense_config_dir: str | Path | None = None,
    drop_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    tower_dir = _resolve_export_dir(tower_defense_config_dir, export_root=export_root) or _find_default_config_dir(
        root,
        DEFAULT_TOWER_DEFENSE_DIR_PATTERN,
        "DoupoTowerDefense",
    )
    drop_dir = _resolve_export_dir(drop_config_dir, export_root=export_root) or _find_default_config_dir(
        root,
        DEFAULT_DROP_CONFIG_DIR_PATTERN,
        "Drop",
    )
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None
    monster_config_rows = _parse_config_rows(tower_dir, "MonsterGroup", resolved_lang_path, lang_map)
    store_bag_config_rows = _parse_config_rows(drop_dir, "StoreContentBag", resolved_lang_path, lang_map)
    bag_by_id: dict[int, dict[str, Any]] = {}
    for row in store_bag_config_rows:
        bag_id = _as_int(row.get("id") if row.get("id") not in (None, "") else row.get("_row_key"))
        if bag_id is not None:
            bag_by_id[bag_id] = row

    bag_rows = [
        {
            "bag_id": row.get("id") if row.get("id") not in (None, "") else row.get("_row_key"),
            "name": _plain(row.get("name") or ""),
            "model_id": row.get("modelId"),
            "model_zoom": row.get("modelZoom"),
            "range": row.get("range"),
            "interact_time": row.get("interactTime"),
            "appear_action": row.get("appearAction") or "",
            "disappear_action": row.get("disappearAction") or "",
            "effect": row.get("effect") or "",
            "interact_icon": row.get("interactIcon") or "",
            "interacting_icon": row.get("interactingIcon") or "",
        }
        for row in sorted(store_bag_config_rows, key=lambda item: _sort_value(item.get("id") if item.get("id") not in (None, "") else item.get("_row_key")))
    ]

    monster_rows: list[dict[str, Any]] = []
    direct_id_matches = 0
    direct_base_matches = 0
    for monster in sorted(monster_config_rows, key=lambda item: (_sort_value(item.get("id")), _sort_value(item.get("baseId")))):
        monster_id = _as_int(monster.get("id"))
        base_id = _as_int(monster.get("baseId"))
        match_kind = ""
        matched_bag: dict[str, Any] | None = None
        if monster_id is not None and monster_id in bag_by_id:
            match_kind = "monster_id"
            matched_bag = bag_by_id[monster_id]
            direct_id_matches += 1
        if base_id is not None and base_id in bag_by_id:
            if matched_bag is None:
                match_kind = "base_id"
                matched_bag = bag_by_id[base_id]
            elif base_id != monster_id:
                match_kind = "monster_id+base_id"
            direct_base_matches += 1
        monster_rows.append(
            {
                "monster_id": monster.get("id"),
                "base_id": monster.get("baseId"),
                "monster_type": monster.get("type"),
                "model_id": monster.get("modelId"),
                "drops": monster.get("drops"),
                "candidate_bag_id": matched_bag.get("id") if matched_bag else "",
                "candidate_bag_name": _plain(matched_bag.get("name") or "") if matched_bag else "",
                "candidate_bag_model_id": matched_bag.get("modelId") if matched_bag else "",
                "candidate_bag_model_zoom": matched_bag.get("modelZoom") if matched_bag else "",
                "candidate_bag_range": matched_bag.get("range") if matched_bag else "",
                "match_kind": match_kind,
                "match_status": "direct_static_match" if matched_bag else "no_direct_static_match",
                "note": (
                    "Direct id/baseId match is only a static visual hint; runtime uses server BagUnitVO.bagModel."
                    if matched_bag
                    else "No direct id/baseId match; runtime bagModel cannot be inferred from MonsterGroup alone."
                ),
            }
        )

    evidence_rows = _collect_doupotd_store_bag_visual_evidence(root)
    evidence_text = "\n".join(str(row.get("snippet") or "") for row in evidence_rows)
    stats = {
        "store_bag_config_count": len(store_bag_config_rows),
        "doupotd_monster_group_count": len(monster_config_rows),
        "direct_monster_id_bag_match_count": direct_id_matches,
        "direct_base_id_bag_match_count": direct_base_matches,
        "direct_static_match_row_count": sum(1 for row in monster_rows if row.get("match_status") == "direct_static_match"),
        "unmatched_monster_row_count": sum(1 for row in monster_rows if row.get("match_status") == "no_direct_static_match"),
        "evidence_row_count": len(evidence_rows),
    }
    verdict = {
        "store_bag_visual_resolves_by_server_bag_model": bool(re.search(r"data\.bagModel|ConfigName\.Drop_StoreContentBag", evidence_text)),
        "bag_unit_vo_carries_bag_model": bool(re.search(r"bagModel", evidence_text)),
        "open_bag_is_client_request_to_server": bool(re.search(r"Get_CM_OpenBag|CM_OpenBag|bagIds:Add", evidence_text)),
        "open_result_uses_server_bag_drops": bool(re.search(r"bagDrops|OpenStoreBag", evidence_text)),
        "monster_id_static_match_is_partial": stats["direct_static_match_row_count"] < stats["doupotd_monster_group_count"],
        "static_monster_to_bag_model_not_fully_determined": True,
    }

    output_dir = root / "apk_static_index"
    output_dir.mkdir(parents=True, exist_ok=True)
    bags_tsv = output_dir / "lua_lscript_module_doupotd_store_bag_visual_bags.tsv"
    monsters_tsv = output_dir / "lua_lscript_module_doupotd_store_bag_visual_monster_matches.tsv"
    evidence_tsv = output_dir / "lua_lscript_module_doupotd_store_bag_visual_evidence.tsv"
    report_path = output_dir / "lua_lscript_module_doupotd_store_bag_visual_report.md"
    json_path = output_dir / "lua_lscript_module_doupotd_store_bag_visual_report.json"
    _write_tsv(
        bags_tsv,
        bag_rows,
        [
            "bag_id",
            "name",
            "model_id",
            "model_zoom",
            "range",
            "interact_time",
            "appear_action",
            "disappear_action",
            "effect",
            "interact_icon",
            "interacting_icon",
        ],
    )
    _write_tsv(
        monsters_tsv,
        monster_rows,
        [
            "monster_id",
            "base_id",
            "monster_type",
            "model_id",
            "drops",
            "candidate_bag_id",
            "candidate_bag_name",
            "candidate_bag_model_id",
            "candidate_bag_model_zoom",
            "candidate_bag_range",
            "match_kind",
            "match_status",
            "note",
        ],
    )
    _write_tsv(evidence_tsv, evidence_rows, ["source_file", "line", "category", "target", "snippet"])
    _write_doupotd_store_bag_visual_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        bag_rows=bag_rows,
        monster_rows=monster_rows,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "source": {
                    "tower_defense_config_dir": str(tower_dir),
                    "drop_config_dir": str(drop_dir),
                    "lang_path": str(resolved_lang_path or ""),
                },
                "stats": stats,
                "verdict": verdict,
                "samples": {
                    "bags": bag_rows[:120],
                    "monster_matches": monster_rows[:120],
                    "evidence": evidence_rows[:120],
                },
                "files": {
                    "bags": str(bags_tsv),
                    "monster_matches": str(monsters_tsv),
                    "evidence": str(evidence_tsv),
                    "markdown": str(report_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "bags": str(bags_tsv),
            "monster_matches": str(monsters_tsv),
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _doupotd_reward_result_resolution_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "apk_static_index"
    return {
        "items": output_dir / "lua_lscript_module_doupotd_reward_result_resolution_items.tsv",
        "flow": output_dir / "lua_lscript_module_doupotd_reward_result_resolution_flow.tsv",
        "json": output_dir / "lua_lscript_module_doupotd_reward_result_resolution_report.json",
    }


def _load_doupotd_reward_result_resolution_files(root: Path) -> tuple[dict[str, Path], list[dict[str, str]], dict[str, Any]]:
    paths = _doupotd_reward_result_resolution_file_paths(root)
    if not paths["items"].is_file():
        build_fanxiu_doupotd_reward_result_resolution_probe(export_root=root)
    items = _read_tsv_dicts(paths["items"])
    report = _read_json(paths["json"])
    return paths, items, report


def _reward_result_resolution_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("source_table") or ""),
        str(row.get("config_id") or ""),
        str(row.get("reward_index") or ""),
    )


def _attach_reward_result_resolution(
    items: list[dict[str, str]],
    resolution_by_key: dict[tuple[str, str, str], dict[str, str]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in items:
        resolution = resolution_by_key.get(_reward_result_resolution_key(item))
        if not resolution:
            enriched.append(item)
            continue
        enriched.append(
            {
                **item,
                "reward_result": {
                    "runtime_reward_type": resolution.get("runtime_reward_type") or "",
                    "runtime_reward_type_name": resolution.get("runtime_reward_type_name") or "",
                    "code": resolution.get("code") or "",
                    "amount": resolution.get("amount") or "",
                    "extra_mark": resolution.get("extra_mark") or "",
                    "extra_mark_name": resolution.get("extra_mark_name") or "",
                    "extra_mark_show_type": resolution.get("extra_mark_show_type") or "",
                    "extra_mark_eff_name": resolution.get("extra_mark_eff_name") or "",
                    "resolution_rule": resolution.get("resolution_rule") or "",
                    "note": resolution.get("note") or "",
                },
            }
        )
    return enriched


def _doupotd_reward_config_file_paths(root: Path) -> dict[str, Path]:
    output_dir = root / "apk_static_index"
    return {
        "levels": output_dir / "lua_lscript_module_doupotd_reward_config_levels.tsv",
        "prelevel_rewards": output_dir / "lua_lscript_module_doupotd_reward_config_prelevel_rewards.tsv",
        "reward_items": output_dir / "lua_lscript_module_doupotd_reward_config_items.tsv",
        "monster_drops": output_dir / "lua_lscript_module_doupotd_reward_config_monster_drops.tsv",
        "json": output_dir / "lua_lscript_module_doupotd_reward_config_report.json",
    }


def _load_doupotd_reward_config_files(root: Path) -> tuple[dict[str, Path], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    paths = _doupotd_reward_config_file_paths(root)
    if not paths["levels"].is_file() or not paths["reward_items"].is_file():
        build_fanxiu_doupotd_reward_config_probe(export_root=root)
    levels = _read_tsv_dicts(paths["levels"])
    prelevel_rewards = _read_tsv_dicts(paths["prelevel_rewards"])
    reward_items = _read_tsv_dicts(paths["reward_items"])
    report = _read_json(paths["json"])
    return paths, levels, prelevel_rewards, reward_items, report


def _reward_config_sort_key(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
    source_rank = 0 if row.get("source_table") == "Level" else 1
    return (
        source_rank,
        _sort_value(row.get("different")),
        _sort_value(row.get("stage")),
        _sort_value(row.get("layer")),
        _sort_value(row.get("config_id")),
    )


def _attach_reward_items_to_summary(row: dict[str, str], items_by_key: dict[tuple[str, str], list[dict[str, str]]]) -> dict[str, Any]:
    key = (row.get("source_table") or "", row.get("config_id") or "")
    return {
        **row,
        "items": items_by_key.get(key, []),
    }


def search_fanxiu_doupotd_reward_configs(
    *,
    query: str = "",
    source_table: str = "",
    stage: str = "",
    item_id: str = "",
    limit: int = 80,
    offset: int = 0,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    paths, levels, prelevel_rewards, reward_items, report = _load_doupotd_reward_config_files(root)
    resolution_paths, resolution_items, _resolution_report = _load_doupotd_reward_result_resolution_files(root)
    resolution_by_key = {_reward_result_resolution_key(item): item for item in resolution_items}
    reward_items = _attach_reward_result_resolution(reward_items, resolution_by_key)
    items_by_key: dict[tuple[str, str], list[dict[str, str]]] = {}
    for item in reward_items:
        key = (item.get("source_table") or "", item.get("config_id") or "")
        items_by_key.setdefault(key, []).append(item)

    source_filter = source_table.strip()
    stage_filter = stage.strip()
    item_filter = item_id.strip()
    query_text = _normalize_search_text(query)
    rows = [*levels, *prelevel_rewards]
    filtered: list[dict[str, str]] = []
    for row in rows:
        if source_filter and row.get("source_table") != source_filter:
            continue
        if stage_filter and str(row.get("stage") or "") != stage_filter:
            continue
        key = (row.get("source_table") or "", row.get("config_id") or "")
        config_items = items_by_key.get(key, [])
        if item_filter and all(str(item.get("item_id") or "") != item_filter for item in config_items):
            continue
        haystack = _normalize_search_text(
            " ".join(
                [
                    str(row.get("source_table") or ""),
                    str(row.get("config_id") or ""),
                    str(row.get("name") or ""),
                    str(row.get("reward_title") or ""),
                    str(row.get("reward_items") or ""),
                    str(row.get("reward_item_ids") or ""),
                    " ".join(str(item.get("item_name") or "") for item in config_items),
                ]
            )
        )
        if query_text and query_text not in haystack:
            continue
        filtered.append(row)

    filtered.sort(key=_reward_config_sort_key)
    page = filtered[offset : offset + limit]
    return {
        "source": {
            "levels": str(paths["levels"]),
            "prelevel_rewards": str(paths["prelevel_rewards"]),
            "reward_items": str(paths["reward_items"]),
            "reward_result_resolution_items": str(resolution_paths["items"]),
        },
        "stats": report.get("stats") or {},
        "total": len(filtered),
        "items": [_attach_reward_items_to_summary(row, items_by_key) for row in page],
    }


def get_fanxiu_doupotd_reward_config(
    *,
    source_table: str,
    config_id: str,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    paths, levels, prelevel_rewards, reward_items, report = _load_doupotd_reward_config_files(root)
    resolution_paths, resolution_items, _resolution_report = _load_doupotd_reward_result_resolution_files(root)
    resolution_by_key = {_reward_result_resolution_key(item): item for item in resolution_items}
    rows = levels if source_table == "Level" else prelevel_rewards if source_table == "DoupoPreLevelReward" else []
    target = next((row for row in rows if str(row.get("config_id") or "") == str(config_id)), None)
    if target is None:
        raise FanxiuResourceError(f"未找到斗破TD奖励配置：{source_table}#{config_id}")
    items = _attach_reward_result_resolution(
        [
            item
            for item in reward_items
            if item.get("source_table") == source_table and str(item.get("config_id") or "") == str(config_id)
        ],
        resolution_by_key,
    )
    return {
        "source": {
            "levels": str(paths["levels"]),
            "prelevel_rewards": str(paths["prelevel_rewards"]),
            "reward_items": str(paths["reward_items"]),
            "reward_result_resolution_items": str(resolution_paths["items"]),
        },
        "stats": report.get("stats") or {},
        "item": {
            **target,
            "items": items,
        },
    }


def build_fanxiu_doupotd_catalog(
    *,
    tower_defense_config_dir: str | Path | None = None,
    card_compose_config_dir: str | Path | None = None,
    lang_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    tower_dir = _resolve_export_dir(tower_defense_config_dir, export_root=export_root) or _find_default_config_dir(
        root,
        DEFAULT_TOWER_DEFENSE_DIR_PATTERN,
        "DoupoTowerDefense",
    )
    card_dir = _resolve_export_dir(card_compose_config_dir, export_root=export_root) or _find_default_config_dir(
        root,
        DEFAULT_CARD_COMPOSE_DIR_PATTERN,
        "DoupoCardCompose",
    )
    resolved_lang_path = _resolve_lang_path(root, lang_path)
    lang_map = load_fanxiu_lang_map(resolved_lang_path) if resolved_lang_path else None

    partner_rows = _parse_config_rows(tower_dir, "CharacterMainInfo", resolved_lang_path, lang_map)
    level_rows = _parse_config_rows(tower_dir, "CharacterLevel", resolved_lang_path, lang_map)
    skill_rows = _parse_config_rows(tower_dir, "CharacterSkillShow", resolved_lang_path, lang_map)
    logic_skill_rows = _parse_config_rows(tower_dir, "CharacterSkillInfo", resolved_lang_path, lang_map)
    strength_rows = _parse_config_rows(tower_dir, "CharacterSkillStrength", resolved_lang_path, lang_map)
    buff_rows = _parse_config_rows(tower_dir, "BuffEffect", resolved_lang_path, lang_map)
    attr_rows = _parse_config_rows(tower_dir, "AttrName", resolved_lang_path, lang_map)
    compose_rows = _parse_config_rows(card_dir, "ComposeCard", resolved_lang_path, lang_map)
    compose_type_rows = _parse_config_rows(card_dir, "ComposeType", resolved_lang_path, lang_map)
    compose_progress_rows = _parse_config_rows(card_dir, "ComposeProgress", resolved_lang_path, lang_map)
    compose_partner_choose_rows = _parse_config_rows(card_dir, "ComposePartnerChoose", resolved_lang_path, lang_map)
    compose_card_quality_rows = _parse_config_rows(card_dir, "ComposeCardQuality", resolved_lang_path, lang_map)
    draw_card_rows = _parse_config_rows(card_dir, "DrawCard", resolved_lang_path, lang_map)
    compose_book_rows = _parse_config_rows(card_dir, "ComposeBook", resolved_lang_path, lang_map)

    compose_by_partner = _group_by_int(compose_rows, "charId")
    skill_by_partner = _group_by_int(skill_rows, "partnerId")
    logic_skill_by_partner = _group_by_int(logic_skill_rows, "charId")
    strength_by_partner = _group_by_int(strength_rows, "partnerId")
    level_by_partner = _group_by_int(level_rows, "charId")
    quality_name_by_id = {
        quality_id: str(row.get("qualityName") or "")
        for row in compose_type_rows
        if (quality_id := _as_int(row.get("quality"))) is not None
    }
    attr_by_char, attr_fallback = _attr_meta_maps(attr_rows)
    item_by_id = _load_items_by_id(root)
    skill_runtime_by_id = _build_doupotd_skill_runtime_map(root, logic_skill_rows=logic_skill_rows, buff_rows=buff_rows)
    cards = [
        _compact_partner_card(
            row,
            compose_rows=compose_by_partner.get(_as_int(row.get("id")) or 0, []),
            skill_rows=skill_by_partner.get(_as_int(row.get("id")) or 0, []),
            logic_skill_rows=logic_skill_by_partner.get(_as_int(row.get("id")) or 0, []),
            strength_rows=strength_by_partner.get(_as_int(row.get("id")) or 0, []),
            level_rows=level_by_partner.get(_as_int(row.get("id")) or 0, []),
            quality_name_by_id=quality_name_by_id,
            item_by_id=item_by_id,
            attr_by_char=attr_by_char,
            attr_fallback=attr_fallback,
            skill_runtime_by_id=skill_runtime_by_id,
        )
        for row in sorted(partner_rows, key=lambda item: (_sort_value(item.get("sort")), _sort_value(item.get("id"))))
    ]
    compose_card_by_id = {
        _as_int(compose_card.get("id")) or 0: compose_card
        for card in cards
        for compose_card in card.get("compose_cards") or []
        if _as_int(compose_card.get("id")) is not None
    }
    draw_sources = [
        _compact_draw_card_source(row, item_by_id=item_by_id, compose_card_by_id=compose_card_by_id)
        for row in sorted(draw_card_rows, key=lambda item: (_sort_value(item.get("sort")), _sort_value(item.get("id"))))
    ]
    compose_quality_sources = [
        _compact_compose_quality_source(row, quality_name_by_id=quality_name_by_id, compose_card_by_id=compose_card_by_id)
        for row in sorted(compose_card_quality_rows, key=lambda item: (_sort_value(item.get("quality")), _sort_value(item.get("id"))))
    ]
    compose_progress_rewards = [
        _compact_compose_progress(row, item_by_id)
        for row in sorted(compose_progress_rows, key=lambda item: (_sort_value(item.get("progress")), _sort_value(item.get("id"))))
    ]
    compose_book_entries = [
        _compact_compose_book_entry(row, quality_name_by_id=quality_name_by_id, compose_card_by_id=compose_card_by_id)
        for row in sorted(compose_book_rows, key=lambda item: (_sort_value(item.get("quality")), _sort_value(item.get("sort")), _sort_value(item.get("id"))))
    ]
    _attach_partner_source_summaries(
        cards,
        draw_sources=draw_sources,
        compose_quality_sources=compose_quality_sources,
        compose_book_entries=compose_book_entries,
        compose_progress_rewards=compose_progress_rewards,
    )
    stats = {
        "partner_count": len(cards),
        "compose_card_count": len(compose_rows),
        "skill_show_count": len(skill_rows),
        "skill_logic_count": len(logic_skill_rows),
        "skill_runtime_count": len(skill_runtime_by_id),
        "strength_count": len(strength_rows),
        "level_row_count": len(level_rows),
        "draw_card_count": len(draw_card_rows),
        "compose_progress_count": len(compose_progress_rows),
        "compose_book_count": len(compose_book_rows),
        "quality_count": len(compose_type_rows),
        "career_type_counts": dict(Counter(str(card.get("career_type") or "") for card in cards)),
    }

    out_dir = root / "parsed_configs" / "doupotd_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = out_dir / "doupotd_catalog.json"
    summary_tsv = out_dir / "partner_summary.tsv"
    catalog = {
        "schema_version": DOUPOTD_CATALOG_SCHEMA_VERSION,
        "source": {
            "tower_defense_config_dir": str(tower_dir),
            "card_compose_config_dir": str(card_dir),
            "lang_path": str(resolved_lang_path or ""),
        },
        "stats": stats,
        "compose_types": compose_type_rows,
        "compose_progress": compose_progress_rows,
        "compose_progress_rewards": compose_progress_rewards,
        "compose_partner_choose": compose_partner_choose_rows,
        "compose_card_quality": compose_card_quality_rows,
        "compose_quality_sources": compose_quality_sources,
        "compose_book": compose_book_rows,
        "compose_book_entries": compose_book_entries,
        "draw_cards": draw_card_rows,
        "draw_sources": draw_sources,
        "cards": cards,
    }
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_tsv(
        summary_tsv,
        [
            {
                "id": card.get("id"),
                "name": card.get("name"),
                "positioning": card.get("positioning"),
                "skill_name": card.get("skill_name"),
                "compose_card_count": card.get("compose_card_count"),
                "skill_count": card.get("skill_count"),
                "strength_count": card.get("strength_count"),
            }
            for card in cards
        ],
        ["id", "name", "positioning", "skill_name", "compose_card_count", "skill_count", "strength_count"],
    )
    return {
        "output_dir": str(out_dir),
        "stats": stats,
        "files": {
            "catalog": str(catalog_path),
            "summary_tsv": str(summary_tsv),
        },
    }
