from __future__ import annotations

import csv
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.catalog.lua_config import load_fanxiu_lang_map, parse_fanxiu_generated_lua_config
from backend.core.fanxiu.catalog.resources import FanxiuResourceError, resolve_fanxiu_export_root
from backend.core.fanxiu.catalog.wiki import strip_fanxiu_rich_text


DOUPOTD_CATALOG_SCHEMA_VERSION = 2
DEFAULT_CATALOG = Path("parsed_configs/doupotd_catalog/doupotd_catalog.json")
DEFAULT_TOWER_DEFENSE_DIR_PATTERN = "by_source/lscripts/generate/cfg/doupotowerdefense_*/text_assets"
DEFAULT_CARD_COMPOSE_DIR_PATTERN = "by_source/lscripts/generate/cfg/doupocardcompose_*/text_assets"
DEFAULT_DROP_CONFIG_DIR_PATTERN = "by_source/lscripts/generate/cfg/drop_*/text_assets"
DEFAULT_ITEM_CORNER_PATTERN = "by_source/lscripts/generate/cfg/item_*/text_assets/ItemCorner.lua"
DEFAULT_LANG_PATTERN = "by_source/lscripts/generate/localization/chinese/lang_*/text_assets/lang.lua"
DEFAULT_MESSAGE_TEXT_ASSETS = Path("by_source/lscripts/gamesystem/game/message_bf46a8de9ccefb33ec3f4d0545cc766e/text_assets")
DEFAULT_FANXIU_TCP_SERVER_HOST = "1.12.44.63"
DOUPOTD_EFFECT_TYPE_REQUIRE = "GameSystem.Game.DoupoTD.Core.Fight.SkillEffect.Const.DoupoTDEffectType"
_LUA_FUNCTION_RE = re.compile(r"function\s+([A-Za-z0-9_:.]+)\s*\(")
_WHITESPACE_RE = re.compile(r"\s+")
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


def _collect_doupotd_pvp_report_scene_evidence(root: Path) -> tuple[list[dict[str, Any]], set[str]]:
    rows: list[dict[str, Any]] = []
    payload_args: set[str] = set()
    arg_pattern = re.compile(
        r"\b(replayId|type|round|pkStage|zone|pkStep|time|atkVoList|defVoList|clientWinnerId|serverWinnerId)\b"
    )
    for text_dir in _doupotd_lscript_text_asset_dirs(root):
        for path in sorted(text_dir.glob("DoupoTDPVPSceneView*.lua")):
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            rel_path = str(path.relative_to(root))
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue
                if "CM_DoupoTDReportFun" in stripped:
                    rows.append(
                        {
                            "category": "scene_report_call",
                            "source": rel_path,
                            "line": line_no,
                            "target": "CM_DoupoTDReportFun",
                            "snippet": stripped[:320],
                        }
                    )
                if re.search(r"Scene_DoupoTDPVPIT|Scene_DoupoTDPVP", stripped):
                    rows.append(
                        {
                            "category": "scene_type_branch",
                            "source": rel_path,
                            "line": line_no,
                            "target": "DoupoTDPVP",
                            "snippet": stripped[:320],
                        }
                    )
                if re.search(r"DigitDoor(Simple|Attr)VO", stripped):
                    rows.append(
                        {
                            "category": "scene_digitdoor_vo_reuse",
                            "source": rel_path,
                            "line": line_no,
                            "target": "DigitDoorSimpleVO/DigitDoorAttrVO",
                            "snippet": stripped[:320],
                        }
                    )
                matches = {match.group(1) for match in arg_pattern.finditer(stripped)}
                if matches:
                    payload_args.update(matches)
                    if re.search(r"=|CM_DoupoTDReportFun|local\s+", stripped):
                        rows.append(
                            {
                                "category": "scene_payload_arg",
                                "source": rel_path,
                                "line": line_no,
                                "target": "|".join(sorted(matches)),
                                "snippet": stripped[:320],
                            }
                        )
    return rows, payload_args


def _collect_doupotd_pvp_report_index_evidence(root: Path) -> tuple[list[dict[str, Any]], int]:
    output_dir = root / "apk_static_index"
    rows: list[dict[str, Any]] = []
    report_row_count = 0

    for name in (
        "lua_lscript_module_doupotd_surface_markers.tsv",
        "lua_lscript_module_doupotd_surface_requires.tsv",
        "lua_lscript_module_doupotd_protocol_schemas.tsv",
        "lua_lscript_module_doupotd_protocol_fields.tsv",
        "lua_lscript_module_doupotd_netlogic_flow_edges.tsv",
        "lua_lscript_module_doupotd_surface_protocol_refs.tsv",
    ):
        path = output_dir / name
        for row in _read_tsv_dicts(path):
            text = " ".join(str(value or "") for value in row.values())
            if not re.search(r"CM_DoupoTDReport|SM_DoupoTDReport|CM_DoupoTDReportFun", text):
                continue
            category = "surface_marker_report_call" if "surface_markers" in name else "protocol_index_report_row"
            if category == "protocol_index_report_row":
                report_row_count += 1
            rows.append(
                {
                    "category": category,
                    "source": str(path.relative_to(root)),
                    "line": row.get("line") or row.get("line_no") or "",
                    "target": "CM_DoupoTDReport/SM_DoupoTDReport",
                    "snippet": text[:320],
                }
            )

    if report_row_count == 0:
        rows.append(
            {
                "category": "protocol_index_missing",
                "source": str(output_dir.relative_to(root)),
                "line": "",
                "target": "CM_DoupoTDReport/SM_DoupoTDReport",
                "snippet": "No generated doupotd protocol/schema/flow rows for CM_DoupoTDReport or SM_DoupoTDReport were found.",
            }
        )
    return rows, report_row_count


def _collect_doupotd_pvp_report_packet_evidence(root: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    packet_paths = sorted(
        path
        for path in (root / "by_source" / "lscripts" / "gamesystem" / "game").glob(
            "message_*/text_assets/*DoupoTDReport*.lua"
        )
        if path.is_file()
    )
    for path in packet_paths:
        rows.append(
            {
                "category": "packet_file_visible",
                "source": str(path.relative_to(root)),
                "line": "",
                "target": path.stem,
                "snippet": "Visible message packet file.",
            }
        )
    if not packet_paths:
        rows.append(
            {
                "category": "packet_file_missing",
                "source": "by_source/lscripts/gamesystem/game/message_*/text_assets",
                "line": "",
                "target": "CM_DoupoTDReport/SM_DoupoTDReport",
                "snippet": "No visible CM_DoupoTDReport*.lua or SM_DoupoTDReport*.lua packet file under exported message text_assets.",
            }
        )
    return rows, len(packet_paths)


def _collect_doupotd_pvp_report_netlogic_evidence(root: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    visible_count = 0
    for text_dir in _doupotd_lscript_text_asset_dirs(root):
        for path in sorted(text_dir.glob("DoupoTDNetLogic*.lua")):
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            rel_path = str(path.relative_to(root))
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue
                if re.search(r"\bCM_DoupoTDReportFun\b", stripped):
                    visible_count += 1
                    rows.append(
                        {
                            "category": "netlogic_report_fun_visible",
                            "source": rel_path,
                            "line": line_no,
                            "target": "CM_DoupoTDReportFun",
                            "snippet": stripped[:320],
                        }
                    )
    if visible_count == 0:
        rows.append(
            {
                "category": "netlogic_report_fun_missing",
                "source": "DoupoTDNetLogic*.lua",
                "line": "",
                "target": "CM_DoupoTDReportFun",
                "snippet": "No visible DoupoTDNetLogic CM_DoupoTDReportFun implementation in exported doupotd Lua text_assets.",
            }
        )
    return rows, visible_count


def _write_doupotd_pvp_report_gap_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report gap report",
        "",
        "This is a static, read-only probe for the visible DoupoTD PVP replay/report request surface.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Evidence Samples", ""])
    for row in evidence_rows[:80]:
        location = row.get("source") or ""
        if row.get("line"):
            location = f"{location}:{row.get('line')}"
        lines.append(
            f"- `{row.get('category')}` `{location}` `{row.get('target')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "The scene-level call and payload shape are visible, but the current exported Lua/protocol index does not expose the matching DoupoTD report packet or NetLogic implementation. This is an export/static-index gap, not proof of server internals or absence of runtime handling.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_gap_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    scene_rows, payload_args = _collect_doupotd_pvp_report_scene_evidence(root)
    index_rows, protocol_index_report_row_count = _collect_doupotd_pvp_report_index_evidence(root)
    packet_rows, packet_file_count = _collect_doupotd_pvp_report_packet_evidence(root)
    netlogic_rows, netlogic_report_fun_count = _collect_doupotd_pvp_report_netlogic_evidence(root)
    evidence_rows = scene_rows + index_rows + packet_rows + netlogic_rows
    evidence_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    required_payload_args = {"atkVoList", "defVoList", "clientWinnerId", "serverWinnerId"}
    stats = {
        "evidence_row_count": len(evidence_rows),
        "scene_report_call_count": evidence_counts.get("scene_report_call", 0),
        "scene_payload_arg_count": len(payload_args),
        "scene_required_payload_arg_count": len(required_payload_args & payload_args),
        "scene_digitdoor_vo_reuse_count": evidence_counts.get("scene_digitdoor_vo_reuse", 0),
        "surface_marker_report_call_count": evidence_counts.get("surface_marker_report_call", 0),
        "packet_file_count": packet_file_count,
        "netlogic_report_fun_count": netlogic_report_fun_count,
        "protocol_index_report_row_count": protocol_index_report_row_count,
    }
    verdict = {
        "scene_report_call_visible": stats["scene_report_call_count"] > 0,
        "scene_common_payload_shape_visible": required_payload_args.issubset(payload_args)
        and stats["scene_digitdoor_vo_reuse_count"] > 0,
        "surface_marker_report_call_visible": stats["surface_marker_report_call_count"] > 0,
        "no_visible_doupotd_report_packet_file": stats["packet_file_count"] == 0,
        "no_visible_doupotd_report_netlogic_fun": stats["netlogic_report_fun_count"] == 0,
        "no_generated_doupotd_report_protocol_index": stats["protocol_index_report_row_count"] == 0,
        "static_boundary_only": True,
    }
    verdict["doupotd_pvp_report_gap_confirmed"] = bool(
        verdict["scene_report_call_visible"]
        and verdict["scene_common_payload_shape_visible"]
        and verdict["no_visible_doupotd_report_packet_file"]
        and verdict["no_visible_doupotd_report_netlogic_fun"]
        and verdict["no_generated_doupotd_report_protocol_index"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_tsv = output_dir / "doupotd_pvp_report_gap_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_gap_report.md"
    json_path = output_dir / "doupotd_pvp_report_gap_report.json"
    _write_tsv(evidence_tsv, evidence_rows, ["category", "source", "line", "target", "snippet"])
    _write_doupotd_pvp_report_gap_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_report_gap_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "payload_args": sorted(payload_args),
                "files": {
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
        "confirmed": verdict["doupotd_pvp_report_gap_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "payload_args": sorted(payload_args),
        "files": {
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _doupotd_pvp_scene_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for text_dir in _doupotd_lscript_text_asset_dirs(root):
        paths.extend(path for path in text_dir.glob("DoupoTDPVPSceneView*.lua") if path.is_file())
    return sorted(paths)


def _lua_method_blocks(lines: list[str]) -> dict[str, list[tuple[int, str]]]:
    blocks: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        match = re.search(r"\bfunction\s+_M\.([A-Za-z0-9_]+)\s*\(", stripped)
        if match:
            current = match.group(1)
            blocks.setdefault(current, [])
        if current is not None:
            blocks.setdefault(current, []).append((line_no, stripped))
    return blocks


def _collect_doupotd_pvp_report_scene_payload(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    field_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for path in _doupotd_pvp_scene_paths(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel_path = str(path.relative_to(root))
        blocks = _lua_method_blocks(lines)

        value_sources: dict[str, str] = {}
        for line_no, stripped in blocks.get("GetAttr", []):
            assign = re.match(r"local\s+([A-Za-z0-9_]+)\s*=\s*(.+)", stripped)
            if assign:
                value_sources[assign.group(1)] = assign.group(2)
            attr = re.search(r'self:GetAttrCode\("([^"]+)",\s*([A-Za-z0-9_]+),\s*clist\)', stripped)
            if attr:
                field_rows.append(
                    {
                        "section": "attr_snapshot",
                        "order": sum(1 for row in field_rows if row.get("section") == "attr_snapshot") + 1,
                        "name": attr.group(1),
                        "value": attr.group(2),
                        "expression": value_sources.get(attr.group(2), ""),
                        "source": rel_path,
                        "line": line_no,
                    }
                )
                evidence_rows.append(
                    {
                        "category": "attr_snapshot_order",
                        "source": rel_path,
                        "line": line_no,
                        "target": attr.group(1),
                        "snippet": stripped[:320],
                    }
                )

        for line_no, stripped in blocks.get("CreateEntityData", []):
            assign = re.match(r"data\.([A-Za-z0-9_]+)\s*=\s*(.+)", stripped)
            if assign:
                field_rows.append(
                    {
                        "section": "simple_vo_field",
                        "order": "",
                        "name": assign.group(1),
                        "value": "",
                        "expression": assign.group(2),
                        "source": rel_path,
                        "line": line_no,
                    }
                )
                evidence_rows.append(
                    {
                        "category": "simple_vo_field",
                        "source": rel_path,
                        "line": line_no,
                        "target": assign.group(1),
                        "snippet": stripped[:320],
                    }
                )
            if "data.attrVOList" in stripped and "GetAttr" in stripped:
                field_rows.append(
                    {
                        "section": "simple_vo_field",
                        "order": "",
                        "name": "attrVOList",
                        "value": "",
                        "expression": stripped,
                        "source": rel_path,
                        "line": line_no,
                    }
                )
                evidence_rows.append(
                    {
                        "category": "simple_vo_attr_list_fill",
                        "source": rel_path,
                        "line": line_no,
                        "target": "attrVOList",
                        "snippet": stripped[:320],
                    }
                )

        for line_no, stripped in blocks.get("CheckList", []):
            if re.search(r"Scene_DoupoTDPVPIT|Scene_DoupoTDPVP", stripped):
                evidence_rows.append(
                    {
                        "category": "scene_mode_branch",
                        "source": rel_path,
                        "line": line_no,
                        "target": "DoupoTDPVP",
                        "snippet": stripped[:320],
                    }
                )
            report_arg = re.match(
                r"(replayId|type|round|pkStage|zone|pkStep|time|atkVoList|defVoList|clientWinnerId|serverWinnerId)\s*=\s*(.+)",
                stripped,
            )
            if report_arg:
                field_rows.append(
                    {
                        "section": "report_arg",
                        "order": "",
                        "name": report_arg.group(1),
                        "value": "",
                        "expression": report_arg.group(2),
                        "source": rel_path,
                        "line": line_no,
                    }
                )
                evidence_rows.append(
                    {
                        "category": "report_arg_assignment",
                        "source": rel_path,
                        "line": line_no,
                        "target": report_arg.group(1),
                        "snippet": stripped[:320],
                    }
                )
            if re.search(r"GetDefenseViewList|GetAttackViewList|tbDefense|tbAttack|defenseList:Add|attackList:Add|CreateEntityData", stripped):
                evidence_rows.append(
                    {
                        "category": "snapshot_list_fill",
                        "source": rel_path,
                        "line": line_no,
                        "target": "attackList/defenseList",
                        "snippet": stripped[:320],
                    }
                )
            if "CM_DoupoTDReportFun" in stripped:
                evidence_rows.append(
                    {
                        "category": "report_send_call",
                        "source": rel_path,
                        "line": line_no,
                        "target": "CM_DoupoTDReportFun",
                        "snippet": stripped[:320],
                    }
                )

        for line_no, stripped in blocks.get("SaveEntityData", []):
            if re.search(r"tbDefense|tbAttack|defenseList:Add|attackList:Add|CreateEntityData", stripped):
                evidence_rows.append(
                    {
                        "category": "dead_entity_snapshot_fill",
                        "source": rel_path,
                        "line": line_no,
                        "target": "attackList/defenseList",
                        "snippet": stripped[:320],
                    }
                )
    return field_rows, evidence_rows


def _write_doupotd_pvp_report_scene_payload_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    attr_rows: list[dict[str, Any]],
    field_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report scene payload report",
        "",
        "This is a static, read-only scene-level payload map for `DoupoTDPVPSceneView`.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Attribute Snapshot Order", ""])
    for row in attr_rows:
        lines.append(
            f"- `{row.get('order')}` `{row.get('name')}` from `{row.get('value')}` = `{row.get('expression')}`"
        )
    lines.extend(["", "## Report Fields", ""])
    for row in field_rows:
        if row.get("section") == "attr_snapshot":
            continue
        location = row.get("source") or ""
        if row.get("line"):
            location = f"{location}:{row.get('line')}"
        lines.append(
            f"- `{row.get('section')}` `{row.get('name')}` `{row.get('expression')}` at `{location}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in evidence_rows[:80]:
        location = row.get("source") or ""
        if row.get("line"):
            location = f"{location}:{row.get('line')}"
        lines.append(
            f"- `{row.get('category')}` `{location}` `{row.get('target')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This closes the client scene payload construction that is visible in Lua. It still does not expose the missing DoupoTD report packet class, NetLogic sender implementation, or server acceptance logic.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_scene_payload_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    field_rows, evidence_rows = _collect_doupotd_pvp_report_scene_payload(root)
    attr_rows = [row for row in field_rows if row.get("section") == "attr_snapshot"]
    attr_codes = [str(row.get("name") or "") for row in sorted(attr_rows, key=lambda item: _sort_value(item.get("order")))]
    simple_fields = {str(row.get("name") or "") for row in field_rows if row.get("section") == "simple_vo_field"}
    report_args = {str(row.get("name") or "") for row in field_rows if row.get("section") == "report_arg"}
    expected_attr_codes = [
        "HP",
        "MAXHP",
        "ATTACK",
        "PVPATTACK",
        "ATKSPEED",
        "CRITICAL",
        "ANTICRITICAL",
        "INCREASEDAMAGE",
        "REDUCEDAMAGE",
        "PVPINCREASE",
        "PVPREDUCE",
        "ADDDAMAGE",
    ]
    stats = {
        "scene_file_count": len(_doupotd_pvp_scene_paths(root)),
        "field_row_count": len(field_rows),
        "evidence_row_count": len(evidence_rows),
        "attr_snapshot_count": len(attr_rows),
        "simple_vo_field_count": len(simple_fields),
        "report_arg_count": len(report_args),
        "snapshot_list_evidence_count": sum(1 for row in evidence_rows if row.get("category") == "snapshot_list_fill"),
        "dead_entity_snapshot_evidence_count": sum(1 for row in evidence_rows if row.get("category") == "dead_entity_snapshot_fill"),
    }
    verdict = {
        "fixed_attr_snapshot_order_visible": attr_codes == expected_attr_codes,
        "simple_vo_fields_visible": {"ownerId", "resourceId", "index", "lv", "attrVOList"}.issubset(simple_fields),
        "winner_projection_visible": {"clientWinnerId", "serverWinnerId"}.issubset(report_args),
        "pvp_mode_projection_visible": {"replayId", "type", "round", "pkStage", "zone", "pkStep", "time"}.issubset(report_args)
        and any(row.get("category") == "scene_mode_branch" for row in evidence_rows),
        "snapshot_lists_visible": {"atkVoList", "defVoList"}.issubset(report_args)
        and stats["snapshot_list_evidence_count"] > 0,
        "static_scene_payload_only": True,
    }
    verdict["doupotd_pvp_scene_payload_confirmed"] = bool(
        verdict["fixed_attr_snapshot_order_visible"]
        and verdict["simple_vo_fields_visible"]
        and verdict["winner_projection_visible"]
        and verdict["pvp_mode_projection_visible"]
        and verdict["snapshot_lists_visible"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    fields_tsv = output_dir / "doupotd_pvp_report_scene_payload_fields.tsv"
    evidence_tsv = output_dir / "doupotd_pvp_report_scene_payload_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_scene_payload_report.md"
    json_path = output_dir / "doupotd_pvp_report_scene_payload_report.json"
    _write_tsv(fields_tsv, field_rows, ["section", "order", "name", "value", "expression", "source", "line"])
    _write_tsv(evidence_tsv, evidence_rows, ["category", "source", "line", "target", "snippet"])
    _write_doupotd_pvp_report_scene_payload_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        attr_rows=attr_rows,
        field_rows=field_rows,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_scene_payload_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "attr_codes": attr_codes,
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
        "confirmed": verdict["doupotd_pvp_scene_payload_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "attr_codes": attr_codes,
        "files": {
            "fields": str(fields_tsv),
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


DOUPOTD_PVP_REPORT_EXPECTED_FIELDS = [
    ("replayId", "Long"),
    ("type", "Int"),
    ("round", "Int"),
    ("pkStage", "Int"),
    ("zone", "Int"),
    ("pkStep", "Int"),
    ("time", "Long"),
    ("atkVoList", "MessageList2List"),
    ("defVoList", "MessageList2List"),
    ("clientWinnerId", "Long"),
    ("serverWinnerId", "Long"),
]


def _parse_lua_message_packet_shape(path: Path, root: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None, []
    rel_path = str(path.relative_to(root))
    packet_name = path.stem.split("__", 1)[0]
    packet_id = ""
    package_name = ""
    read_fields: list[tuple[str, str]] = []
    write_fields: list[tuple[str, str]] = []
    evidence_rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        package_match = re.search(r'package\.loaded\["([^"]+)"\]', stripped)
        if package_match:
            package_name = package_match.group(1)
        id_match = re.search(r"return\s+(-?\d+)\s*$", stripped)
        if id_match and lines[max(0, line_no - 2)].strip().startswith("function _M.getId"):
            packet_id = id_match.group(1)
        name_match = re.search(r'return\s*"([^"]+)"', stripped)
        if name_match and lines[max(0, line_no - 2)].strip().startswith("function _M.getName"):
            packet_name = name_match.group(1)
        read_match = re.search(r"self\.([A-Za-z0-9_]+)\s*=\s*self:read([A-Za-z0-9_]+)\(", stripped)
        if read_match:
            read_fields.append((read_match.group(1), read_match.group(2)))
        list_read_match = re.search(r"self:readMessageList2List\(self\.([A-Za-z0-9_]+)\)", stripped)
        if list_read_match:
            read_fields.append((list_read_match.group(1), "MessageList2List"))
        write_match = re.search(r"self:write([A-Za-z0-9_]+)\(self\.([A-Za-z0-9_]+)\)", stripped)
        if write_match:
            write_fields.append((write_match.group(2), write_match.group(1)))
        list_write_match = re.search(r"self:writeList\(self\.([A-Za-z0-9_]+)\)", stripped)
        if list_write_match:
            write_fields.append((list_write_match.group(1), "List"))
    if not read_fields and not write_fields:
        return None, []
    expected_names = [name for name, _kind in DOUPOTD_PVP_REPORT_EXPECTED_FIELDS]
    read_names = [name for name, _kind in read_fields]
    matched_names = [name for name in expected_names if name in read_names]
    if len(matched_names) < 4 and not re.search(r"DoupoReport|DigitDoorReport", packet_name):
        return None, []
    expected_types = dict(DOUPOTD_PVP_REPORT_EXPECTED_FIELDS)
    read_types = dict(read_fields)
    exact_order_match = read_fields[: len(DOUPOTD_PVP_REPORT_EXPECTED_FIELDS)] == DOUPOTD_PVP_REPORT_EXPECTED_FIELDS
    exact_name_order_match = read_names[: len(expected_names)] == expected_names
    type_match_count = sum(1 for name, kind in DOUPOTD_PVP_REPORT_EXPECTED_FIELDS if read_types.get(name) == kind)
    row = {
        "packet_name": packet_name,
        "packet_id": packet_id,
        "package": package_name,
        "source": rel_path,
        "field_count": len(read_fields),
        "matched_field_count": len(matched_names),
        "type_match_count": type_match_count,
        "field_order": " | ".join(read_names),
        "read_types": " | ".join(f"{name}:{kind}" for name, kind in read_fields),
        "write_types": " | ".join(f"{name}:{kind}" for name, kind in write_fields),
        "exact_order_match": exact_order_match,
        "exact_name_order_match": exact_name_order_match,
        "is_doupo_alias_candidate": packet_name == "CM_DoupoReport" and exact_order_match,
        "is_digitdoor_baseline": packet_name == "CM_DigitDoorReport" and exact_order_match,
    }
    if len(matched_names) >= 8 or row["is_doupo_alias_candidate"] or row["is_digitdoor_baseline"]:
        evidence_rows.append(
            {
                "category": "packet_shape_candidate",
                "source": rel_path,
                "line": "",
                "target": packet_name,
                "snippet": f"id={packet_id} fields={' | '.join(read_names)}",
            }
        )
    return row, evidence_rows


def _collect_doupotd_pvp_report_shape_alias(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lscript_root = root / "by_source" / "lscripts" / "gamesystem" / "game"
    candidates: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    if lscript_root.is_dir():
        message_files = sorted(
            path
            for path in lscript_root.glob("message_*/text_assets/*.lua")
            if path.is_file() and not path.name.startswith("VO_URL")
        )
        for path in message_files:
            row, rows = _parse_lua_message_packet_shape(path, root)
            if row is None:
                continue
            candidates.append(row)
            evidence_rows.extend(rows)

        for path in sorted(lscript_root.glob("message_*/text_assets/VO_URL*.lua")):
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            rel_path = str(path.relative_to(root))
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if "93671" in stripped or "CM_DoupoReport" in stripped:
                    evidence_rows.append(
                        {
                            "category": "vo_url_registration",
                            "source": rel_path,
                            "line": line_no,
                            "target": "93671/CM_DoupoReport",
                            "snippet": stripped[:320],
                        }
                    )

    for text_dir in _doupotd_lscript_text_asset_dirs(root):
        for path in sorted(text_dir.glob("*.lua")):
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            rel_path = str(path.relative_to(root))
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if re.search(r"CM_DoupoReportFun|CM_DoupoReport\b|CM_DoupoTDReportFun", stripped):
                    evidence_rows.append(
                        {
                            "category": "doupotd_lua_report_reference",
                            "source": rel_path,
                            "line": line_no,
                            "target": "report_reference",
                            "snippet": stripped[:320],
                        }
                    )
    return candidates, evidence_rows


def _write_doupotd_pvp_report_shape_alias_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    candidates: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report shape alias report",
        "",
        "This static probe matches the visible DoupoTD PVP scene payload shape against all exported message packet classes, independent of packet name.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Exact Shape Candidates", ""])
    for row in candidates:
        if not row.get("exact_order_match"):
            continue
        lines.append(
            f"- `{row.get('packet_name')}` id `{row.get('packet_id')}` package `{row.get('package')}` source `{row.get('source')}` fields `{row.get('field_order')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in evidence_rows[:80]:
        location = row.get("source") or ""
        if row.get("line"):
            location = f"{location}:{row.get('line')}"
        lines.append(
            f"- `{row.get('category')}` `{location}` `{row.get('target')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "`CM_DoupoReport(93671)` has the exact same packet field shape as the DoupoTD PVP scene payload, so the earlier name-exact `CM_DoupoTDReport` miss should be treated as a naming/export gap rather than absence of a packet body. The visible DoupoTD NetLogic sender implementation is still missing, so the final call-to-send mapping remains unresolved.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_shape_alias_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    candidates, evidence_rows = _collect_doupotd_pvp_report_shape_alias(root)
    exact_candidates = [row for row in candidates if row.get("exact_order_match")]
    doupo_candidates = [row for row in exact_candidates if row.get("packet_name") == "CM_DoupoReport"]
    digitdoor_candidates = [row for row in exact_candidates if row.get("packet_name") == "CM_DigitDoorReport"]
    netlogic_alias_refs = [
        row
        for row in evidence_rows
        if row.get("category") == "doupotd_lua_report_reference" and "CM_DoupoReport" in str(row.get("snippet") or "")
    ]
    scene_td_refs = [
        row
        for row in evidence_rows
        if row.get("category") == "doupotd_lua_report_reference" and "CM_DoupoTDReportFun" in str(row.get("snippet") or "")
    ]
    stats = {
        "candidate_packet_count": len(candidates),
        "exact_shape_candidate_count": len(exact_candidates),
        "cm_doupo_report_exact_shape_count": len(doupo_candidates),
        "cm_digitdoor_report_exact_shape_count": len(digitdoor_candidates),
        "vo_url_registration_count": sum(1 for row in evidence_rows if row.get("category") == "vo_url_registration"),
        "doupotd_lua_report_reference_count": len(
            [row for row in evidence_rows if row.get("category") == "doupotd_lua_report_reference"]
        ),
        "doupotd_lua_cm_doupo_report_reference_count": len(netlogic_alias_refs),
        "doupotd_lua_cm_doupotd_reportfun_reference_count": len(scene_td_refs),
        "evidence_row_count": len(evidence_rows),
    }
    verdict = {
        "cm_doupo_report_packet_visible": stats["cm_doupo_report_exact_shape_count"] > 0,
        "cm_doupo_report_exact_shape_match": bool(doupo_candidates),
        "cm_doupo_report_registered_in_vo_url": stats["vo_url_registration_count"] > 0,
        "digitdoor_baseline_exact_shape_visible": bool(digitdoor_candidates),
        "scene_still_calls_cm_doupotd_reportfun": stats["doupotd_lua_cm_doupotd_reportfun_reference_count"] > 0,
        "visible_doupotd_lua_sender_for_cm_doupo_report_missing": stats["doupotd_lua_cm_doupo_report_reference_count"] == 0,
        "static_shape_alias_only": True,
    }
    verdict["doupotd_pvp_report_shape_alias_confirmed"] = bool(
        verdict["cm_doupo_report_exact_shape_match"]
        and verdict["cm_doupo_report_registered_in_vo_url"]
        and verdict["scene_still_calls_cm_doupotd_reportfun"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_tsv = output_dir / "doupotd_pvp_report_shape_alias_candidates.tsv"
    evidence_tsv = output_dir / "doupotd_pvp_report_shape_alias_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_shape_alias_report.md"
    json_path = output_dir / "doupotd_pvp_report_shape_alias_report.json"
    _write_tsv(
        candidates_tsv,
        candidates,
        [
            "packet_name",
            "packet_id",
            "package",
            "source",
            "field_count",
            "matched_field_count",
            "type_match_count",
            "field_order",
            "read_types",
            "write_types",
            "exact_order_match",
            "exact_name_order_match",
            "is_doupo_alias_candidate",
            "is_digitdoor_baseline",
        ],
    )
    _write_tsv(evidence_tsv, evidence_rows, ["category", "source", "line", "target", "snippet"])
    _write_doupotd_pvp_report_shape_alias_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        candidates=candidates,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_report_shape_alias_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "files": {
                    "candidates": str(candidates_tsv),
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
        "confirmed": verdict["doupotd_pvp_report_shape_alias_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "candidates": str(candidates_tsv),
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _collect_doupotd_pvp_report_sender_alias_gap(
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    candidates, alias_evidence = _collect_doupotd_pvp_report_shape_alias(root)
    scene_rows, payload_args = _collect_doupotd_pvp_report_scene_evidence(root)
    evidence_rows: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "scene_payload_arg_count": len(payload_args),
        "scene_report_call_count": sum(1 for row in scene_rows if row.get("category") == "scene_report_call"),
        "packet_alias_vo_url_registration_count": 0,
        "global_surface_cm_doupo_report_row_count": 0,
        "doupotd_index_cm_doupo_report_row_count": 0,
        "doupotd_index_reportfun_row_count": 0,
        "netlogic_file_count": 0,
        "netlogic_function_count": 0,
        "netlogic_report_function_count": 0,
        "netlogic_cm_doupotd_reportfun_ref_count": 0,
        "netlogic_cm_doupo_report_ref_count": 0,
        "netlogic_cm_doupo_report_send_ref_count": 0,
        "netlogic_cm_doupo_report_register_ref_count": 0,
    }

    for row in scene_rows:
        if row.get("category") not in {"scene_report_call", "scene_payload_arg", "scene_digitdoor_vo_reuse"}:
            continue
        evidence_rows.append(
            {
                "category": f"scene_{row.get('category')}",
                "source": row.get("source", ""),
                "line": row.get("line", ""),
                "target": row.get("target", ""),
                "snippet": row.get("snippet", ""),
            }
        )

    for row in candidates:
        if not row.get("exact_order_match"):
            continue
        if row.get("packet_name") not in {"CM_DoupoReport", "CM_DigitDoorReport"}:
            continue
        evidence_rows.append(
            {
                "category": "packet_exact_shape_alias",
                "source": row.get("source", ""),
                "line": "",
                "target": f"{row.get('packet_name')}:{row.get('packet_id')}",
                "snippet": str(row.get("field_order") or "")[:320],
            }
        )

    for row in alias_evidence:
        category = str(row.get("category") or "")
        snippet = str(row.get("snippet") or "")
        if category == "vo_url_registration":
            stats["packet_alias_vo_url_registration_count"] += 1
            evidence_rows.append(
                {
                    "category": "packet_alias_vo_url_registration",
                    "source": row.get("source", ""),
                    "line": row.get("line", ""),
                    "target": row.get("target", ""),
                    "snippet": snippet,
                }
            )
        elif category == "doupotd_lua_report_reference" and (
            "CM_DoupoTDReportFun" in snippet or "CM_DoupoReport" in snippet
        ):
            evidence_rows.append(
                {
                    "category": "doupotd_lua_report_reference",
                    "source": row.get("source", ""),
                    "line": row.get("line", ""),
                    "target": row.get("target", ""),
                    "snippet": snippet,
                }
            )

    surface_path = root / "apk_static_index" / "lua_lscript_surface_assets.tsv"
    for row in _read_tsv_dicts(surface_path):
        text = " ".join(str(value or "") for value in row.values())
        if not re.search(r"\b93671\b|CM_DoupoReport", text):
            continue
        stats["global_surface_cm_doupo_report_row_count"] += 1
        evidence_rows.append(
            {
                "category": "global_surface_cm_doupo_report_row",
                "source": str(surface_path.relative_to(root)),
                "line": row.get("line") or row.get("line_no") or "",
                "target": row.get("packet_name") or row.get("name") or "CM_DoupoReport",
                "snippet": text[:320],
            }
        )

    index_dir = root / "apk_static_index"
    for path in sorted(index_dir.glob("lua_lscript_module_doupotd_*.tsv")):
        for row in _read_tsv_dicts(path):
            text = " ".join(str(value or "") for value in row.values())
            alias_hit = bool(re.search(r"\b93671\b|CM_DoupoReport", text))
            reportfun_hit = "CM_DoupoTDReportFun" in text
            if not alias_hit and not reportfun_hit:
                continue
            if alias_hit:
                stats["doupotd_index_cm_doupo_report_row_count"] += 1
            if reportfun_hit:
                stats["doupotd_index_reportfun_row_count"] += 1
            evidence_rows.append(
                {
                    "category": "doupotd_index_alias_or_reportfun_row",
                    "source": str(path.relative_to(root)),
                    "line": row.get("line") or row.get("line_no") or "",
                    "target": "CM_DoupoReport/93671/CM_DoupoTDReportFun",
                    "snippet": text[:320],
                }
            )

    netlogic_function_names: set[str] = set()
    for text_dir in _doupotd_lscript_text_asset_dirs(root):
        for path in sorted(text_dir.glob("DoupoTDNetLogic*.lua")):
            stats["netlogic_file_count"] += 1
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            rel_path = str(path.relative_to(root))
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue
                func_match = re.search(r"\bfunction\s+_M\.([A-Za-z0-9_]+)\s*\(", stripped)
                if func_match:
                    function_name = func_match.group(1)
                    netlogic_function_names.add(function_name)
                    if function_name == "CM_DoupoTDReportFun":
                        stats["netlogic_report_function_count"] += 1
                        evidence_rows.append(
                            {
                                "category": "netlogic_report_function_signature",
                                "source": rel_path,
                                "line": line_no,
                                "target": function_name,
                                "snippet": stripped[:320],
                            }
                        )
                has_td_reportfun = "CM_DoupoTDReportFun" in stripped
                has_alias = bool(re.search(r"\b93671\b|_?CM_DoupoReport\b", stripped))
                if has_td_reportfun:
                    stats["netlogic_cm_doupotd_reportfun_ref_count"] += 1
                    evidence_rows.append(
                        {
                            "category": "netlogic_cm_doupotd_reportfun_ref",
                            "source": rel_path,
                            "line": line_no,
                            "target": "CM_DoupoTDReportFun",
                            "snippet": stripped[:320],
                        }
                    )
                if not has_alias:
                    continue
                stats["netlogic_cm_doupo_report_ref_count"] += 1
                category = "netlogic_cm_doupo_report_ref"
                if re.search(r"F_SendMsg|GetMessageFromPools", stripped):
                    stats["netlogic_cm_doupo_report_send_ref_count"] += 1
                    category = "netlogic_cm_doupo_report_send_ref"
                if re.search(r"Regist|Register|getId", stripped):
                    stats["netlogic_cm_doupo_report_register_ref_count"] += 1
                    category = "netlogic_cm_doupo_report_register_ref"
                evidence_rows.append(
                    {
                        "category": category,
                        "source": rel_path,
                        "line": line_no,
                        "target": "CM_DoupoReport/93671",
                        "snippet": stripped[:320],
                    }
                )

    stats["netlogic_function_count"] = len(netlogic_function_names)
    if stats["netlogic_report_function_count"] == 0:
        evidence_rows.append(
            {
                "category": "netlogic_report_function_missing",
                "source": "DoupoTDNetLogic*.lua",
                "line": "",
                "target": "CM_DoupoTDReportFun",
                "snippet": "No visible function _M.CM_DoupoTDReportFun implementation in exported DoupoTDNetLogic Lua.",
            }
        )
    if stats["netlogic_cm_doupo_report_ref_count"] == 0:
        evidence_rows.append(
            {
                "category": "netlogic_alias_sender_missing",
                "source": "DoupoTDNetLogic*.lua",
                "line": "",
                "target": "CM_DoupoReport/93671",
                "snippet": "No visible DoupoTDNetLogic reference to CM_DoupoReport, _CM_DoupoReport, or protocol id 93671.",
            }
        )
    if stats["netlogic_cm_doupo_report_register_ref_count"] == 0:
        evidence_rows.append(
            {
                "category": "netlogic_alias_register_missing",
                "source": "DoupoTDNetLogic*.lua",
                "line": "",
                "target": "CM_DoupoReport/93671",
                "snippet": "No visible DoupoTDNetLogic register/unregister line for CM_DoupoReport or protocol id 93671.",
            }
        )

    return candidates, evidence_rows, stats


def _write_doupotd_pvp_report_sender_alias_gap_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    candidates: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report sender alias gap",
        "",
        "This static probe connects the scene call `CM_DoupoTDReportFun(...)` with the exact-shape packet alias `CM_DoupoReport(93671)` and checks whether the visible DoupoTD NetLogic export contains a sender/register mapping.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Exact Alias Candidates", ""])
    for row in candidates:
        if row.get("packet_name") != "CM_DoupoReport" or not row.get("exact_order_match"):
            continue
        lines.append(
            f"- `{row.get('packet_name')}` id `{row.get('packet_id')}` package `{row.get('package')}` source `{row.get('source')}` fields `{row.get('field_order')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in evidence_rows[:100]:
        location = row.get("source") or ""
        if row.get("line"):
            location = f"{location}:{row.get('line')}"
        lines.append(
            f"- `{row.get('category')}` `{location}` `{row.get('target')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "The packet body alias is visible, but the exported DoupoTD NetLogic Lua still does not show the function body or send/register mapping for the scene-level `CM_DoupoTDReportFun` call. This is a static export boundary; read-only Runtime state or a deeper Lua/IL2CPP dispatcher trace is still needed to prove the final sender path.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_sender_alias_gap_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    candidates, evidence_rows, collected_stats = _collect_doupotd_pvp_report_sender_alias_gap(root)
    exact_candidates = [row for row in candidates if row.get("exact_order_match")]
    doupo_candidates = [row for row in exact_candidates if row.get("packet_name") == "CM_DoupoReport"]
    digitdoor_candidates = [row for row in exact_candidates if row.get("packet_name") == "CM_DigitDoorReport"]
    evidence_counts = Counter(str(row.get("category") or "") for row in evidence_rows)
    stats = {
        "evidence_row_count": len(evidence_rows),
        "exact_shape_candidate_count": len(exact_candidates),
        "cm_doupo_report_exact_shape_count": len(doupo_candidates),
        "cm_digitdoor_report_exact_shape_count": len(digitdoor_candidates),
        "packet_alias_vo_url_registration_count": collected_stats["packet_alias_vo_url_registration_count"],
        "global_surface_cm_doupo_report_row_count": collected_stats["global_surface_cm_doupo_report_row_count"],
        "scene_report_call_count": collected_stats["scene_report_call_count"],
        "scene_payload_arg_count": collected_stats["scene_payload_arg_count"],
        "doupotd_index_cm_doupo_report_row_count": collected_stats["doupotd_index_cm_doupo_report_row_count"],
        "doupotd_index_reportfun_row_count": collected_stats["doupotd_index_reportfun_row_count"],
        "netlogic_file_count": collected_stats["netlogic_file_count"],
        "netlogic_function_count": collected_stats["netlogic_function_count"],
        "netlogic_report_function_count": collected_stats["netlogic_report_function_count"],
        "netlogic_cm_doupotd_reportfun_ref_count": collected_stats["netlogic_cm_doupotd_reportfun_ref_count"],
        "netlogic_cm_doupo_report_ref_count": collected_stats["netlogic_cm_doupo_report_ref_count"],
        "netlogic_cm_doupo_report_send_ref_count": collected_stats["netlogic_cm_doupo_report_send_ref_count"],
        "netlogic_cm_doupo_report_register_ref_count": collected_stats[
            "netlogic_cm_doupo_report_register_ref_count"
        ],
        "missing_evidence_row_count": sum(
            evidence_counts.get(category, 0)
            for category in (
                "netlogic_report_function_missing",
                "netlogic_alias_sender_missing",
                "netlogic_alias_register_missing",
            )
        ),
    }
    verdict = {
        "scene_calls_report_function": stats["scene_report_call_count"] > 0,
        "packet_alias_body_visible": stats["cm_doupo_report_exact_shape_count"] > 0,
        "packet_alias_registered_globally": stats["packet_alias_vo_url_registration_count"] > 0
        or stats["global_surface_cm_doupo_report_row_count"] > 0,
        "digitdoor_baseline_exact_shape_visible": stats["cm_digitdoor_report_exact_shape_count"] > 0,
        "visible_doupotd_netlogic_report_function_missing": stats["netlogic_report_function_count"] == 0,
        "visible_doupotd_netlogic_alias_sender_missing": stats["netlogic_cm_doupo_report_send_ref_count"] == 0
        and stats["netlogic_cm_doupo_report_ref_count"] == 0,
        "visible_doupotd_netlogic_alias_register_missing": stats["netlogic_cm_doupo_report_register_ref_count"] == 0,
        "visible_doupotd_module_alias_index_missing": stats["doupotd_index_cm_doupo_report_row_count"] == 0,
        "static_boundary_only": True,
    }
    verdict["doupotd_pvp_report_sender_alias_gap_confirmed"] = bool(
        verdict["scene_calls_report_function"]
        and verdict["packet_alias_body_visible"]
        and verdict["packet_alias_registered_globally"]
        and verdict["visible_doupotd_netlogic_report_function_missing"]
        and verdict["visible_doupotd_netlogic_alias_sender_missing"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_tsv = output_dir / "doupotd_pvp_report_sender_alias_gap_candidates.tsv"
    evidence_tsv = output_dir / "doupotd_pvp_report_sender_alias_gap_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_sender_alias_gap_report.md"
    json_path = output_dir / "doupotd_pvp_report_sender_alias_gap_report.json"
    _write_tsv(
        candidates_tsv,
        candidates,
        [
            "packet_name",
            "packet_id",
            "package",
            "source",
            "field_count",
            "matched_field_count",
            "type_match_count",
            "field_order",
            "read_types",
            "write_types",
            "exact_order_match",
            "exact_name_order_match",
            "is_doupo_alias_candidate",
            "is_digitdoor_baseline",
        ],
    )
    _write_tsv(evidence_tsv, evidence_rows, ["category", "source", "line", "target", "snippet"])
    _write_doupotd_pvp_report_sender_alias_gap_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        candidates=candidates,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_report_sender_alias_gap_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "files": {
                    "candidates": str(candidates_tsv),
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
        "confirmed": verdict["doupotd_pvp_report_sender_alias_gap_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "candidates": str(candidates_tsv),
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _classify_doupotd_pvp_report_global_lua_hit(path: Path, stripped: str) -> str:
    if "DoupoTDNetLogic" in path.name:
        return "netlogic_symbol_hit"
    if "DoupoTDPVPSceneView" in path.name and "CM_DoupoTDReportFun" in stripped:
        return "scene_report_call"
    if path.name.startswith("CM_DoupoReport"):
        return "packet_alias_file_hit"
    if path.name.startswith("VO_URL") and "CM_DoupoReport" in stripped:
        return "vo_url_registration"
    return "other_symbol_hit"


def _collect_doupotd_pvp_report_global_lua_surface(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lscript_root = root / "by_source" / "lscripts"
    evidence_rows: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "scanned_lua_file_count": 0,
        "distinct_hit_file_count": 0,
        "symbol_hit_count": 0,
        "scene_report_call_count": 0,
        "packet_alias_file_hit_count": 0,
        "vo_url_registration_count": 0,
        "netlogic_symbol_hit_count": 0,
        "other_symbol_hit_count": 0,
    }
    if not lscript_root.is_dir():
        return evidence_rows, stats

    hit_files: set[str] = set()
    pattern = re.compile(r"CM_DoupoTDReportFun|CM_DoupoReport|DoupoTDReport")
    for path in sorted(lscript_root.rglob("*.lua")):
        stats["scanned_lua_file_count"] += 1
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel_path = str(path.relative_to(root))
        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("--") or not pattern.search(stripped):
                continue
            category = _classify_doupotd_pvp_report_global_lua_hit(path, stripped)
            stats["symbol_hit_count"] += 1
            stats[f"{category}_count"] = int(stats.get(f"{category}_count", 0)) + 1
            hit_files.add(rel_path)
            evidence_rows.append(
                {
                    "category": category,
                    "source": rel_path,
                    "line": line_no,
                    "target": "CM_DoupoTDReportFun/CM_DoupoReport/DoupoTDReport",
                    "snippet": stripped[:320],
                }
            )
    stats["distinct_hit_file_count"] = len(hit_files)
    return evidence_rows, stats


def _write_doupotd_pvp_report_global_lua_surface_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report global Lua surface",
        "",
        "This probe scans all exported Lua text under `by_source/lscripts` for the report symbols `CM_DoupoTDReportFun`, `CM_DoupoReport`, and `DoupoTDReport`, independent of module-specific generated indexes.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Evidence", ""])
    for row in evidence_rows[:120]:
        location = row.get("source") or ""
        if row.get("line"):
            location = f"{location}:{row.get('line')}"
        lines.append(
            f"- `{row.get('category')}` `{location}` `{row.get('target')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "A clean global Lua symbol scan reduces the chance that the sender was simply missed by the doupotd module index. It still does not inspect runtime-generated functions, binary-only Lua chunks, or server-side handling.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_global_lua_surface_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    evidence_rows, stats = _collect_doupotd_pvp_report_global_lua_surface(root)
    verdict = {
        "scene_call_visible_in_global_lua": stats["scene_report_call_count"] > 0,
        "packet_alias_visible_in_global_lua": stats["packet_alias_file_hit_count"] > 0,
        "vo_url_alias_visible_in_global_lua": stats["vo_url_registration_count"] > 0,
        "no_netlogic_symbol_hit_in_global_lua": stats["netlogic_symbol_hit_count"] == 0,
        "no_other_symbolic_sender_surface": stats["other_symbol_hit_count"] == 0,
        "static_symbol_scan_only": True,
    }
    verdict["doupotd_pvp_report_global_lua_surface_gap_confirmed"] = bool(
        verdict["scene_call_visible_in_global_lua"]
        and verdict["packet_alias_visible_in_global_lua"]
        and verdict["vo_url_alias_visible_in_global_lua"]
        and verdict["no_netlogic_symbol_hit_in_global_lua"]
        and verdict["no_other_symbolic_sender_surface"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_tsv = output_dir / "doupotd_pvp_report_global_lua_surface_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_global_lua_surface_report.md"
    json_path = output_dir / "doupotd_pvp_report_global_lua_surface_report.json"
    _write_tsv(evidence_tsv, evidence_rows, ["category", "source", "line", "target", "snippet"])
    _write_doupotd_pvp_report_global_lua_surface_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_report_global_lua_surface_gap_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "files": {
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
        "confirmed": verdict["doupotd_pvp_report_global_lua_surface_gap_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _collect_doupotd_pvp_report_netlogic_family(
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    game_root = root / "by_source" / "lscripts" / "gamesystem" / "game"
    function_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "netlogic_file_count": 0,
        "netlogic_report_function_count": 0,
        "netlogic_report_send_ref_count": 0,
        "cm_report_function_count": 0,
        "sm_report_function_count": 0,
        "digitdoor_report_function_count": 0,
        "doupotd_report_function_count": 0,
        "doupotd_cm_sender_function_count": 0,
        "doupotd_dynamic_dispatch_hint_count": 0,
        "callsite_digitdoor_reportfun_count": 0,
        "callsite_doupotd_reportfun_count": 0,
    }
    if not game_root.is_dir():
        return function_rows, evidence_rows, stats

    for path in sorted(game_root.glob("*/text_assets/*NetLogic*.lua")):
        stats["netlogic_file_count"] += 1
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel_path = str(path.relative_to(root))
        module = path.parent.parent.name.split("_", 1)[0]
        current_report_function = ""
        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            func_match = re.search(r"\bfunction\s+_M\.([A-Za-z0-9_]+)\s*\(", stripped)
            if func_match:
                function_name = func_match.group(1)
                current_report_function = function_name if "ReportFun" in function_name else ""
                if path.name.startswith("DoupoTDNetLogic") and re.match(r"CM_DoupoTD[A-Za-z0-9_]*Fun$", function_name):
                    stats["doupotd_cm_sender_function_count"] += 1
                if "ReportFun" not in function_name:
                    continue
                direction = "client_to_server" if function_name.startswith("CM_") else "server_to_client"
                if function_name.startswith("CM_"):
                    stats["cm_report_function_count"] += 1
                if function_name.startswith("SM_"):
                    stats["sm_report_function_count"] += 1
                if function_name == "CM_DigitDoorReportFun":
                    stats["digitdoor_report_function_count"] += 1
                if function_name == "CM_DoupoTDReportFun":
                    stats["doupotd_report_function_count"] += 1
                row = {
                    "module": module,
                    "function_name": function_name,
                    "direction": direction,
                    "source": rel_path,
                    "line": line_no,
                    "snippet": stripped[:320],
                }
                function_rows.append(row)
                evidence_rows.append(
                    {
                        "category": "netlogic_report_function",
                        "source": rel_path,
                        "line": line_no,
                        "target": function_name,
                        "snippet": stripped[:320],
                    }
                )
                continue
            if path.name.startswith("DoupoTDNetLogic") and re.search(r"__index|__newindex|\brawget\b", stripped):
                stats["doupotd_dynamic_dispatch_hint_count"] += 1
                evidence_rows.append(
                    {
                        "category": "doupotd_dynamic_dispatch_hint",
                        "source": rel_path,
                        "line": line_no,
                        "target": "__index/rawget",
                        "snippet": stripped[:320],
                    }
                )
            if current_report_function and re.search(r"GetMessageFromPools|F_SendMsg", stripped):
                stats["netlogic_report_send_ref_count"] += 1
                evidence_rows.append(
                    {
                        "category": "netlogic_report_send_ref",
                        "source": rel_path,
                        "line": line_no,
                        "target": current_report_function,
                        "snippet": stripped[:320],
                    }
                )

    for path in sorted(game_root.glob("*/text_assets/*.lua")):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel_path = str(path.relative_to(root))
        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            if "CM_DigitDoorReportFun" in stripped:
                stats["callsite_digitdoor_reportfun_count"] += 1
                evidence_rows.append(
                    {
                        "category": "callsite_digitdoor_reportfun",
                        "source": rel_path,
                        "line": line_no,
                        "target": "CM_DigitDoorReportFun",
                        "snippet": stripped[:320],
                    }
                )
            if "CM_DoupoTDReportFun" in stripped:
                stats["callsite_doupotd_reportfun_count"] += 1
                evidence_rows.append(
                    {
                        "category": "callsite_doupotd_reportfun",
                        "source": rel_path,
                        "line": line_no,
                        "target": "CM_DoupoTDReportFun",
                        "snippet": stripped[:320],
                    }
                )

    stats["netlogic_report_function_count"] = len(function_rows)
    return function_rows, evidence_rows, stats


def _write_doupotd_pvp_report_netlogic_family_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    function_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report NetLogic family",
        "",
        "This probe compares visible NetLogic report sender functions across exported Lua modules, using DigitDoor PVP as the nearest baseline for the missing DoupoTD PVP report sender.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Focus Functions", ""])
    for row in function_rows:
        name = str(row.get("function_name") or "")
        if name not in {"CM_DigitDoorReportFun", "CM_DoupoTDReportFun"} and "XianLvMine" not in name:
            continue
        lines.append(
            f"- `{name}` module `{row.get('module')}` direction `{row.get('direction')}` source `{row.get('source')}:{row.get('line')}`"
        )
    lines.extend(["", "## Evidence Samples", ""])
    for row in evidence_rows[:120]:
        location = row.get("source") or ""
        if row.get("line"):
            location = f"{location}:{row.get('line')}"
        lines.append(
            f"- `{row.get('category')}` `{location}` `{row.get('target')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This closes only the visible exported-Lua NetLogic pattern. It does not disprove a runtime-generated method, native bridge, or server-side numeric dispatch, but it shows that nearby report senders are normally explicit in Lua exports.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_netlogic_family_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    function_rows, evidence_rows, stats = _collect_doupotd_pvp_report_netlogic_family(root)
    verdict = {
        "explicit_netlogic_report_family_visible": stats["netlogic_report_function_count"] > 0,
        "explicit_report_senders_have_send_refs": stats["netlogic_report_send_ref_count"] > 0,
        "digitdoor_pvp_report_sender_explicit": stats["digitdoor_report_function_count"] > 0,
        "doupotd_has_explicit_cm_senders": stats["doupotd_cm_sender_function_count"] > 0,
        "doupotd_pvp_report_callsite_visible": stats["callsite_doupotd_reportfun_count"] > 0,
        "doupotd_pvp_report_sender_missing_from_family": stats["doupotd_report_function_count"] == 0,
        "doupotd_netlogic_has_no_dynamic_dispatch_hint": stats["doupotd_dynamic_dispatch_hint_count"] == 0,
        "static_netlogic_family_only": True,
    }
    verdict["doupotd_pvp_report_netlogic_family_gap_confirmed"] = bool(
        verdict["explicit_netlogic_report_family_visible"]
        and verdict["digitdoor_pvp_report_sender_explicit"]
        and verdict["doupotd_has_explicit_cm_senders"]
        and verdict["doupotd_pvp_report_callsite_visible"]
        and verdict["doupotd_pvp_report_sender_missing_from_family"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    functions_tsv = output_dir / "doupotd_pvp_report_netlogic_family_functions.tsv"
    evidence_tsv = output_dir / "doupotd_pvp_report_netlogic_family_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_netlogic_family_report.md"
    json_path = output_dir / "doupotd_pvp_report_netlogic_family_report.json"
    _write_tsv(functions_tsv, function_rows, ["module", "function_name", "direction", "source", "line", "snippet"])
    _write_tsv(evidence_tsv, evidence_rows, ["category", "source", "line", "target", "snippet"])
    _write_doupotd_pvp_report_netlogic_family_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        function_rows=function_rows,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_report_netlogic_family_gap_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "files": {
                    "functions": str(functions_tsv),
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
        "confirmed": verdict["doupotd_pvp_report_netlogic_family_gap_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "functions": str(functions_tsv),
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _collect_doupotd_pvp_report_raw_export_coverage(
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index_dir = root / "apk_static_index"
    evidence_rows: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "coverage_row_count": 0,
        "raw_missing_export_by_hash_count": 0,
        "doupotd_raw_bundle_count": 0,
        "doupotd_raw_export_match_count": 0,
        "doupotd_raw_hot_update_index_rows": 0,
        "doupotd_raw_status_covered_count": 0,
        "doupotd_raw_text_asset_row_count": 0,
        "doupotd_target_raw_text_asset_count": 0,
        "doupotd_target_surface_asset_count": 0,
        "doupotd_target_hot_update_row_count": 0,
        "message_alias_hot_update_row_count": 0,
        "message_alias_surface_row_count": 0,
        "actual_netlogic_function_count": 0,
        "actual_netlogic_reportfun_hit_count": 0,
        "actual_scene_report_call_hit_count": 0,
    }
    target_doupotd_assets = {"DoupoTDNetLogic.lua", "DoupoTDPVPSceneView.lua"}
    target_message_assets = {
        "CM_DoupoReport.lua",
        "CM_DoupoReport__-5002418535717797156.lua",
        "VO_URL.lua",
        "VO_URL__-2797871209586775078.lua",
    }

    for row in _read_tsv_dicts(index_dir / "lua_raw_lscript_export_coverage.tsv"):
        stats["coverage_row_count"] += 1
        if row.get("status") == "missing_export_by_hash":
            stats["raw_missing_export_by_hash_count"] += 1
        if row.get("module") != "doupotd" and "doupotd_" not in str(row.get("raw_path") or ""):
            continue
        stats["doupotd_raw_bundle_count"] += 1
        stats["doupotd_raw_export_match_count"] += _as_int(row.get("export_match_count")) or 0
        stats["doupotd_raw_hot_update_index_rows"] += _as_int(row.get("hot_update_index_rows")) or 0
        if row.get("status") == "covered_by_hash":
            stats["doupotd_raw_status_covered_count"] += 1
        evidence_rows.append(
            {
                "category": "raw_bundle_coverage",
                "source": "apk_static_index/lua_raw_lscript_export_coverage.tsv",
                "line": "",
                "target": row.get("raw_path", ""),
                "snippet": (
                    f"status={row.get('status')} export_match_count={row.get('export_match_count')} "
                    f"hot_update_index_rows={row.get('hot_update_index_rows')} byte_size={row.get('byte_size')}"
                ),
            }
        )

    target_output_paths: dict[str, str] = {}
    for row in _read_tsv_dicts(index_dir / "lua_raw_lscript_missing_export_text_assets.tsv"):
        if row.get("module") != "doupotd" and "doupotd_" not in str(row.get("raw_path") or ""):
            continue
        stats["doupotd_raw_text_asset_row_count"] += 1
        asset_name = str(row.get("asset_name") or "")
        if asset_name not in target_doupotd_assets:
            continue
        stats["doupotd_target_raw_text_asset_count"] += 1
        target_output_paths[asset_name] = str(row.get("output_path") or "")
        evidence_rows.append(
            {
                "category": "raw_target_text_asset_exported",
                "source": "apk_static_index/lua_raw_lscript_missing_export_text_assets.tsv",
                "line": "",
                "target": asset_name,
                "snippet": (
                    f"path_id={row.get('path_id')} byte_size={row.get('byte_size')} "
                    f"line_count={row.get('line_count')} function_count={row.get('function_count')}"
                ),
            }
        )

    for row in _read_tsv_dicts(index_dir / "hot_update_lscripts_text_assets.tsv"):
        asset_name = str(row.get("asset_name") or "")
        if asset_name in target_doupotd_assets:
            stats["doupotd_target_hot_update_row_count"] += 1
            evidence_rows.append(
                {
                    "category": "hot_update_doupotd_target_row",
                    "source": "apk_static_index/hot_update_lscripts_text_assets.tsv",
                    "line": "",
                    "target": asset_name,
                    "snippet": f"status={row.get('status')} actual_path={row.get('actual_path')}",
                }
            )
        if asset_name in {"CM_DoupoReport.lua", "VO_URL.lua"} and row.get("module") == "message":
            stats["message_alias_hot_update_row_count"] += 1
            evidence_rows.append(
                {
                    "category": "hot_update_message_alias_row",
                    "source": "apk_static_index/hot_update_lscripts_text_assets.tsv",
                    "line": "",
                    "target": asset_name,
                    "snippet": f"status={row.get('status')} output_path={row.get('output_path')}",
                }
            )

    for row in _read_tsv_dicts(index_dir / "lua_lscript_surface_assets.tsv"):
        asset_name = str(row.get("asset_name") or "")
        if row.get("module") == "doupotd" and asset_name in target_doupotd_assets:
            stats["doupotd_target_surface_asset_count"] += 1
            evidence_rows.append(
                {
                    "category": "surface_doupotd_target_row",
                    "source": "apk_static_index/lua_lscript_surface_assets.tsv",
                    "line": "",
                    "target": asset_name,
                    "snippet": (
                        f"package={row.get('package')} line_count={row.get('line_count')} "
                        f"function_count={row.get('function_count')}"
                    ),
                }
            )
        if row.get("module") == "message" and asset_name in target_message_assets:
            stats["message_alias_surface_row_count"] += 1
            evidence_rows.append(
                {
                    "category": "surface_message_alias_row",
                    "source": "apk_static_index/lua_lscript_surface_assets.tsv",
                    "line": "",
                    "target": asset_name,
                    "snippet": (
                        f"packet={row.get('packet_name')} pro_id={row.get('pro_id')} "
                        f"direction={row.get('direction')} package={row.get('package')}"
                    ),
                }
            )

    netlogic_path_text = target_output_paths.get("DoupoTDNetLogic.lua", "")
    if netlogic_path_text:
        netlogic_path = Path(netlogic_path_text)
        if netlogic_path.is_file():
            try:
                text = netlogic_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            stats["actual_netlogic_function_count"] = len(re.findall(r"\bfunction\s+_M\.", text))
            stats["actual_netlogic_reportfun_hit_count"] = len(re.findall(r"\bCM_DoupoTDReportFun\b", text))
            evidence_rows.append(
                {
                    "category": "actual_netlogic_file_scan",
                    "source": str(netlogic_path.relative_to(root)) if _is_relative_to(netlogic_path, root) else str(netlogic_path),
                    "line": "",
                    "target": "CM_DoupoTDReportFun",
                    "snippet": (
                        f"function_count={stats['actual_netlogic_function_count']} "
                        f"CM_DoupoTDReportFun_hits={stats['actual_netlogic_reportfun_hit_count']}"
                    ),
                }
            )

    scene_path_text = target_output_paths.get("DoupoTDPVPSceneView.lua", "")
    if scene_path_text:
        scene_path = Path(scene_path_text)
        if scene_path.is_file():
            try:
                text = scene_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            stats["actual_scene_report_call_hit_count"] = len(re.findall(r"\bCM_DoupoTDReportFun\b", text))
            evidence_rows.append(
                {
                    "category": "actual_scene_file_scan",
                    "source": str(scene_path.relative_to(root)) if _is_relative_to(scene_path, root) else str(scene_path),
                    "line": "",
                    "target": "CM_DoupoTDReportFun",
                    "snippet": f"CM_DoupoTDReportFun_hits={stats['actual_scene_report_call_hit_count']}",
                }
            )

    return evidence_rows, stats


def _write_doupotd_pvp_report_raw_export_coverage_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report raw export coverage",
        "",
        "This probe checks whether the missing visible `CM_DoupoTDReportFun` sender can be explained by raw lscript export coverage gaps.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Evidence", ""])
    for row in evidence_rows[:100]:
        location = row.get("source") or ""
        if row.get("line"):
            location = f"{location}:{row.get('line')}"
        lines.append(
            f"- `{row.get('category')}` `{location}` `{row.get('target')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This only proves the current exported raw Lua text surface is covered. It does not rule out runtime-generated Lua methods, native bridges, or server-side handling.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_raw_export_coverage_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    evidence_rows, stats = _collect_doupotd_pvp_report_raw_export_coverage(root)
    verdict = {
        "raw_lscript_coverage_has_no_missing_bundles": stats["raw_missing_export_by_hash_count"] == 0,
        "doupotd_raw_bundle_covered": stats["doupotd_raw_bundle_count"] > 0
        and stats["doupotd_raw_bundle_count"] == stats["doupotd_raw_status_covered_count"],
        "doupotd_scene_and_netlogic_exported_from_raw": stats["doupotd_target_raw_text_asset_count"] >= 2,
        "doupotd_targets_present_in_surface_index": stats["doupotd_target_surface_asset_count"] >= 2,
        "doupotd_targets_not_direct_hot_update_rows": stats["doupotd_target_hot_update_row_count"] == 0,
        "packet_alias_message_hot_update_visible": stats["message_alias_hot_update_row_count"] >= 2,
        "packet_alias_surface_visible": stats["message_alias_surface_row_count"] >= 2,
        "scene_call_visible_in_actual_raw_export": stats["actual_scene_report_call_hit_count"] > 0,
        "netlogic_sender_absent_in_actual_raw_export": stats["actual_netlogic_reportfun_hit_count"] == 0,
        "static_export_coverage_only": True,
    }
    verdict["doupotd_pvp_report_sender_gap_not_raw_export_coverage_gap"] = bool(
        verdict["raw_lscript_coverage_has_no_missing_bundles"]
        and verdict["doupotd_raw_bundle_covered"]
        and verdict["doupotd_scene_and_netlogic_exported_from_raw"]
        and verdict["scene_call_visible_in_actual_raw_export"]
        and verdict["netlogic_sender_absent_in_actual_raw_export"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_tsv = output_dir / "doupotd_pvp_report_raw_export_coverage_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_raw_export_coverage_report.md"
    json_path = output_dir / "doupotd_pvp_report_raw_export_coverage_report.json"
    _write_tsv(evidence_tsv, evidence_rows, ["category", "source", "line", "target", "snippet"])
    _write_doupotd_pvp_report_raw_export_coverage_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_report_sender_gap_not_raw_export_coverage_gap"],
                "stats": stats,
                "verdict": verdict,
                "files": {
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
        "confirmed": verdict["doupotd_pvp_report_sender_gap_not_raw_export_coverage_gap"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _collect_doupotd_pvp_report_lua_binding_boundary(
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    evidence_rows: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "class_file_count": 0,
        "class_static_vtbl_index_line_count": 0,
        "class_dynamic_method_generation_hint_count": 0,
        "lua_engine_bridge_addsingleton_count": 0,
        "lua_engine_bridge_lifecycle_call_count": 0,
        "lua_engine_bridge_netlogic_mutation_count": 0,
        "lua_initializer_doupotd_singleton_count": 0,
        "doupotd_mgr_netlogic_new_count": 0,
        "doupotd_netlogic_file_count": 0,
        "doupotd_netlogic_class_nil_count": 0,
        "doupotd_netlogic_total_function_count": 0,
        "doupotd_netlogic_cm_sender_function_count": 0,
        "doupotd_netlogic_report_require_count": 0,
        "doupotd_netlogic_report_register_count": 0,
        "doupotd_netlogic_report_function_count": 0,
        "digitdoor_report_require_count": 0,
        "digitdoor_report_register_count": 0,
        "digitdoor_report_function_count": 0,
        "global_report_function_assignment_count": 0,
        "global_report_runtime_generation_hint_count": 0,
    }
    lscript_root = root / "by_source" / "lscripts"

    def add_row(category: str, path: Path, line_no: int | str, target: str, snippet: str) -> None:
        evidence_rows.append(
            {
                "category": category,
                "source": str(path.relative_to(root)) if _is_relative_to(path, root) else str(path),
                "line": line_no,
                "target": target,
                "snippet": snippet[:320],
            }
        )

    for class_path in sorted(lscript_root.glob("**/text_assets/class.lua")) if lscript_root.is_dir() else []:
        stats["class_file_count"] += 1
        try:
            lines = class_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if "__index=_class[class_type]" in stripped or "setmetatable(vtbl,{__index=" in stripped:
                stats["class_static_vtbl_index_line_count"] += 1
                add_row("class_static_vtbl_lookup", class_path, line_no, "class.lua", stripped)
            if re.search(r"loadstring|load\(|_G\s*\[|rawset\s*\([^)]*CM_DoupoTDReportFun", stripped):
                stats["class_dynamic_method_generation_hint_count"] += 1
                add_row("class_dynamic_generation_hint", class_path, line_no, "class.lua", stripped)

    for bridge_path in sorted(lscript_root.glob("**/text_assets/LuaEngineBridge*.lua")) if lscript_root.is_dir() else []:
        try:
            lines = bridge_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if "function _M.AddSingleton" in stripped or "_sins[#_sins+1]=sin" in stripped:
                stats["lua_engine_bridge_addsingleton_count"] += 1
                add_row("lua_engine_bridge_addsingleton", bridge_path, line_no, "LuaEngineBridge.AddSingleton", stripped)
            if re.search(r":(InitSingleton|UnInitSingleton|Update|LateUpdate|FixedUpdate|Destroy)\(", stripped):
                stats["lua_engine_bridge_lifecycle_call_count"] += 1
                add_row("lua_engine_bridge_lifecycle_call", bridge_path, line_no, "singleton lifecycle", stripped)
            if re.search(r"NetLogic\s*=|CM_DoupoTDReportFun|rawset|__index|loadstring|load\(", stripped):
                stats["lua_engine_bridge_netlogic_mutation_count"] += 1
                add_row("lua_engine_bridge_netlogic_mutation", bridge_path, line_no, "NetLogic/method mutation", stripped)

    for initializer_path in sorted(lscript_root.glob("**/text_assets/LuaInitializer*.lua")) if lscript_root.is_dir() else []:
        try:
            lines = initializer_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if "DoupoTDMgr" in stripped and ("require" in stripped or "AddSingleton" in stripped):
                stats["lua_initializer_doupotd_singleton_count"] += 1
                add_row("lua_initializer_singleton", initializer_path, line_no, "DoupoTDMgr", stripped)

    doupotd_netlogic_paths: list[Path] = []
    for text_dir in _doupotd_lscript_text_asset_dirs(root):
        for mgr_path in sorted(text_dir.glob("DoupoTDMgr*.lua")):
            try:
                lines = mgr_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if "DoupoTDNetLogic" in stripped and ("require" in stripped or "NetLogic=DoupoTDNetLogic.new" in stripped):
                    stats["doupotd_mgr_netlogic_new_count"] += 1
                    add_row("doupotd_mgr_netlogic_new", mgr_path, line_no, "DoupoTDNetLogic", stripped)

        for netlogic_path in sorted(text_dir.glob("DoupoTDNetLogic*.lua")):
            doupotd_netlogic_paths.append(netlogic_path)
            stats["doupotd_netlogic_file_count"] += 1
            try:
                text = netlogic_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            lines = text.splitlines()
            stats["doupotd_netlogic_total_function_count"] += len(re.findall(r"\bfunction\s+_M\.", text))
            stats["doupotd_netlogic_cm_sender_function_count"] += len(
                re.findall(r"\bfunction\s+_M\.CM_DoupoTD|\bfunction\s+_M\.CM_DouPoCard", text)
            )
            stats["doupotd_netlogic_report_require_count"] += len(
                re.findall(r"CM_DoupoTDReport|CM_DoupoReport|93671", text)
            )
            stats["doupotd_netlogic_report_register_count"] += len(
                re.findall(r"F_Register[^\n]*(CM_DoupoTDReport|CM_DoupoReport|93671)", text)
            )
            stats["doupotd_netlogic_report_function_count"] += len(re.findall(r"\bCM_DoupoTDReportFun\b", text))
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if "_M=class(nil,_M)" in stripped:
                    stats["doupotd_netlogic_class_nil_count"] += 1
                    add_row("doupotd_netlogic_class_base", netlogic_path, line_no, "class(nil)", stripped)
                if "CM_DoupoTDReport" in stripped or "CM_DoupoReport" in stripped or "93671" in stripped:
                    add_row("doupotd_netlogic_report_symbol", netlogic_path, line_no, "DoupoTDReport/CM_DoupoReport", stripped)
                if "CM_DoupoTDReportFun" in stripped:
                    add_row("doupotd_netlogic_report_function", netlogic_path, line_no, "CM_DoupoTDReportFun", stripped)

    for digitdoor_path in sorted(lscript_root.glob("**/text_assets/DigitDoorNetLogic*.lua")) if lscript_root.is_dir() else []:
        try:
            lines = digitdoor_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if "_CM_DigitDoorReport" in stripped and "require" in stripped:
                stats["digitdoor_report_require_count"] += 1
                add_row("digitdoor_report_require", digitdoor_path, line_no, "CM_DigitDoorReport", stripped)
            if "F_Register" in stripped and "_CM_DigitDoorReport" in stripped:
                stats["digitdoor_report_register_count"] += 1
                add_row("digitdoor_report_register", digitdoor_path, line_no, "CM_DigitDoorReport", stripped)
            if "function _M.CM_DigitDoorReportFun" in stripped:
                stats["digitdoor_report_function_count"] += 1
                add_row("digitdoor_report_function", digitdoor_path, line_no, "CM_DigitDoorReportFun", stripped)

    report_assignment_pattern = re.compile(
        r"(CM_DoupoTDReportFun\s*=|function\s+[^\\n]*CM_DoupoTDReportFun|rawset\s*\([^)]*CM_DoupoTDReportFun)"
    )
    runtime_generation_pattern = re.compile(
        r"(CM_DoupoTDReportFun|CM_DoupoReport).*(loadstring|load\(|rawset|__index|_G\s*\[)"
        r"|(loadstring|load\(|rawset|__index|_G\s*\[).*(CM_DoupoTDReportFun|CM_DoupoReport)"
    )
    for path in sorted(lscript_root.glob("**/text_assets/*.lua")) if lscript_root.is_dir() else []:
        if path in doupotd_netlogic_paths:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if report_assignment_pattern.search(stripped):
                stats["global_report_function_assignment_count"] += 1
                add_row("global_report_function_assignment", path, line_no, "CM_DoupoTDReportFun", stripped)
            if runtime_generation_pattern.search(stripped):
                stats["global_report_runtime_generation_hint_count"] += 1
                add_row("global_report_runtime_generation_hint", path, line_no, "CM_DoupoTDReportFun/CM_DoupoReport", stripped)

    return evidence_rows, stats


def _write_doupotd_pvp_report_lua_binding_boundary_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report Lua binding boundary",
        "",
        "This probe checks whether visible Lua class/mgr binding can explain `CM_DoupoTDReportFun` being callable from the scene while absent from `DoupoTDNetLogic.lua`.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Evidence", ""])
    for row in evidence_rows[:120]:
        location = row.get("source") or ""
        if row.get("line"):
            location = f"{location}:{row.get('line')}"
        lines.append(
            f"- `{row.get('category')}` `{location}` `{row.get('target')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This closes only the readable Lua binding surface. It still cannot rule out native runtime behavior or a server-side acceptance path; read-only Runtime evidence is required.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_lua_binding_boundary_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    evidence_rows, stats = _collect_doupotd_pvp_report_lua_binding_boundary(root)
    verdict = {
        "lua_class_uses_static_vtbl_lookup": stats["class_file_count"] > 0
        and stats["class_static_vtbl_index_line_count"] > 0
        and stats["class_dynamic_method_generation_hint_count"] == 0,
        "lua_engine_bridge_only_tracks_lifecycle_singletons": stats["lua_engine_bridge_addsingleton_count"] > 0
        and stats["lua_engine_bridge_lifecycle_call_count"] > 0
        and stats["lua_engine_bridge_netlogic_mutation_count"] == 0,
        "doupotd_mgr_instantiates_doupotd_netlogic_directly": stats["doupotd_mgr_netlogic_new_count"] >= 2,
        "doupotd_netlogic_has_no_report_binding_triplet": stats["doupotd_netlogic_report_require_count"] == 0
        and stats["doupotd_netlogic_report_register_count"] == 0
        and stats["doupotd_netlogic_report_function_count"] == 0,
        "digitdoor_baseline_report_binding_triplet_visible": stats["digitdoor_report_require_count"] > 0
        and stats["digitdoor_report_register_count"] > 0
        and stats["digitdoor_report_function_count"] > 0,
        "no_visible_global_report_function_assignment": stats["global_report_function_assignment_count"] == 0,
        "no_visible_runtime_generation_hint_for_report_binding": stats["global_report_runtime_generation_hint_count"] == 0,
        "static_lua_binding_only": True,
    }
    verdict["doupotd_pvp_report_lua_binding_gap_confirmed"] = bool(
        verdict["lua_class_uses_static_vtbl_lookup"]
        and verdict["lua_engine_bridge_only_tracks_lifecycle_singletons"]
        and verdict["doupotd_mgr_instantiates_doupotd_netlogic_directly"]
        and verdict["doupotd_netlogic_has_no_report_binding_triplet"]
        and verdict["digitdoor_baseline_report_binding_triplet_visible"]
        and verdict["no_visible_global_report_function_assignment"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_tsv = output_dir / "doupotd_pvp_report_lua_binding_boundary_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_lua_binding_boundary_report.md"
    json_path = output_dir / "doupotd_pvp_report_lua_binding_boundary_report.json"
    _write_tsv(evidence_tsv, evidence_rows, ["category", "source", "line", "target", "snippet"])
    _write_doupotd_pvp_report_lua_binding_boundary_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_report_lua_binding_gap_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "files": {
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
        "confirmed": verdict["doupotd_pvp_report_lua_binding_gap_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }




def _doupotd_pvp_report_trigger_lifecycle_files(root: Path) -> list[Path]:
    patterns = [
        "by_source/lscripts/gamesystem/game/doupotd_*/text_assets/DoupoTDPVPSceneView.lua",
        "by_source/lscripts/gamesystem/game/doupotd_*/text_assets/DoupoTDMgr.lua",
        "by_source/lscripts/gamesystem/game/digitdoor_*/text_assets/DigitDoorPVPSceneView.lua",
        "by_source/lscripts/gamesystem/game/towerdefense_*/text_assets/TowerDefensePVPSceneView.lua",
    ]
    unique: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _doupotd_trigger_source_role(path: Path) -> str:
    name = path.name
    if name == "DoupoTDPVPSceneView.lua":
        return "doupotd_scene"
    if name == "DoupoTDMgr.lua":
        return "doupotd_mgr"
    if name == "DigitDoorPVPSceneView.lua":
        return "digitdoor_baseline_scene"
    if name == "TowerDefensePVPSceneView.lua":
        return "towerdefense_baseline_scene"
    return "other"


def _append_trigger_row(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    path: Path,
    role: str,
    category: str,
    target: str,
    line_no: int | str,
    function_name: str,
    snippet: str,
    note: str,
) -> None:
    rows.append(
        {
            "role": role,
            "category": category,
            "target": target,
            "file": str(path.relative_to(root)) if _is_relative_to(path, root) else str(path),
            "line": line_no,
            "function": function_name,
            "snippet": snippet,
            "note": note,
        }
    )


def _doupotd_pvp_report_trigger_lifecycle_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scene_roles = {"doupotd_scene", "digitdoor_baseline_scene", "towerdefense_baseline_scene"}
    for path in _doupotd_pvp_report_trigger_lifecycle_files(root):
        role = _doupotd_trigger_source_role(path)
        current_function = ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        save_call_count = 0
        checklist_call_count = 0
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            compact = _WHITESPACE_RE.sub("", line)
            categories: list[tuple[str, str, str]] = []

            if role == "doupotd_mgr":
                if "OpenDoupoTDPVPSceneView" in current_function:
                    if "F_ShowBottonWin(Window.DoupoTDPVPSceneView" in line:
                        categories.append(("mgr_opens_pvp_scene_window", "DoupoTDPVPSceneView", "DoupoTDMgr opens the PVP scene view window."))
                if "CloseById(Window.DoupoTDPVPSceneView" in line:
                    categories.append(("mgr_closes_pvp_scene_window", "DoupoTDPVPSceneView", "DoupoTDMgr closes the PVP scene view window."))

            if role in scene_roles:
                if "self:SaveEntityData(entityView)" in compact:
                    save_call_count += 1
                if "self:CheckList(fightComponent)" in compact:
                    checklist_call_count += 1

                if current_function == "_M._init_":
                    if "self.tbAttack={}" in compact:
                        categories.append(("dedupe_map_initialized", "tbAttack", "Attack-side dedupe map is initialized."))
                    if "self.tbDefense={}" in compact:
                        categories.append(("dedupe_map_initialized", "tbDefense", "Defense-side dedupe map is initialized."))
                    if "self.attackList=CList.new()" in compact:
                        categories.append(("snapshot_list_initialized", "attackList", "Attack snapshot list is initialized."))
                    if "self.defenseList=CList.new()" in compact:
                        categories.append(("snapshot_list_initialized", "defenseList", "Defense snapshot list is initialized."))
                if current_function == "_M.AddEvent":
                    if "self.entityDead=function" in compact:
                        categories.append(("dead_event_handler_defined", "entityDead", "Shared death handler is defined."))
                    if "self:SaveEntityData(entityView)" in compact:
                        categories.append(("dead_event_saves_entity_data", "SaveEntityData", "Death handler snapshots the dead entity."))
                    if "self:UpdateHp()" in compact:
                        categories.append(("dead_event_updates_hp", "UpdateHp", "Death handler re-checks HP/report state."))
                    if "ENTITY_ENTER_DEAD" in line or "AFTER_ENTITY_DEAD_ANIM" in line:
                        categories.append(("dead_event_registered", "entityDead", "Entity death event is registered."))
                if current_function == "_M.Update":
                    if "self:UpdateHp()" in compact:
                        categories.append(("update_ticks_hp", "UpdateHp", "Per-frame update re-checks HP/report state."))
                if current_function == "_M.SaveEntityData":
                    if "LuaEntityType.DoupoTDPartner" in compact or "LuaEntityType.DigitDoorPartner" in compact:
                        categories.append(("save_entity_partner_guard", "partner", "SaveEntityData is limited to partner entity snapshots."))
                    if "CampGroup.Attack" in compact:
                        categories.append(("save_entity_attack_branch", "attackList", "SaveEntityData has an attack-side branch."))
                    if "notself.tbAttack[entityView.Entity.V_RoleId]" in compact:
                        categories.append(("save_entity_dedupe_guard", "tbAttack", "Attack snapshot is deduped by role id."))
                    if "notself.tbDefense[entityView.Entity.V_RoleId]" in compact:
                        categories.append(("save_entity_dedupe_guard", "tbDefense", "Defense snapshot is deduped by role id."))
                    if "self.attackList:Add(data)" in compact:
                        categories.append(("save_entity_adds_snapshot", "attackList", "SaveEntityData appends attack snapshot."))
                    if "self.defenseList:Add(data)" in compact:
                        categories.append(("save_entity_adds_snapshot", "defenseList", "SaveEntityData appends defense snapshot."))
                if current_function == "_M.UpdateHp":
                    if "GetDefenseHPMsg()" in compact:
                        categories.append(("updatehp_defense_hp_source", "GetDefenseHPMsg", "UpdateHp reads defense HP summary."))
                    if "GetAttackHPMsg()" in compact:
                        categories.append(("updatehp_attack_hp_source", "GetAttackHPMsg", "UpdateHp reads attack HP summary."))
                    if "curHp==0" in compact:
                        categories.append(("updatehp_zero_hp_gate", "curHp", "UpdateHp observes zero HP."))
                    if "self.isDead=true" in compact:
                        categories.append(("updatehp_sets_dead_flag", "isDead", "UpdateHp marks scene as dead/finished."))
                    if "self:CheckList(fightComponent)" in compact:
                        categories.append(("updatehp_triggers_checklist", "CheckList", "UpdateHp directly calls CheckList."))
                if current_function == "_M.CheckList":
                    if "ifnotself.curFinishVothen" in compact:
                        categories.append(("checklist_requires_finish_vo", "curFinishVo", "CheckList exits without finish VO."))
                    if "fightComponent:GetDefenseViewList()" in compact:
                        categories.append(("checklist_view_source", "defenseList", "CheckList reads remaining defense-side views."))
                    if "fightComponent:GetAttackViewList()" in compact:
                        categories.append(("checklist_view_source", "attackList", "CheckList reads remaining attack-side views."))
                    if "self.defenseList:Add(data)" in compact:
                        categories.append(("checklist_backfills_snapshot", "defenseList", "CheckList appends remaining defense snapshot."))
                    if "self.attackList:Add(data)" in compact:
                        categories.append(("checklist_backfills_snapshot", "attackList", "CheckList appends remaining attack snapshot."))
                    if "atkVoList=self.attackList" in compact:
                        categories.append(("checklist_assigns_request_list", "atkVoList", "Report request uses accumulated attackList."))
                    if "defVoList=self.defenseList" in compact:
                        categories.append(("checklist_assigns_request_list", "defVoList", "Report request uses accumulated defenseList."))
                    if "clientWinnerId=" in compact:
                        categories.append(("checklist_assigns_winner", "clientWinnerId", "CheckList assigns clientWinnerId."))
                    if "serverWinnerId=" in compact:
                        categories.append(("checklist_assigns_winner", "serverWinnerId", "CheckList assigns serverWinnerId."))
                    if "CM_DoupoTDReportFun(" in line:
                        categories.append(("checklist_sends_report", "CM_DoupoTDReportFun", "CheckList calls the DoupoTD report sender surface."))
                    if "CM_DigitDoorReportFun(" in line:
                        categories.append(("baseline_checklist_sends_report", "CM_DigitDoorReportFun", "Baseline CheckList calls the DigitDoor report sender."))
                if current_function == "_M.Destroy":
                    if "RemoveEventHandler(CommonEventType.ENTITY_ENTER_DEAD" in line:
                        categories.append(("dead_event_unregistered", "ENTITY_ENTER_DEAD", "Destroy unregisters death event."))
                    if "RemoveEventHandler(CommonEventType.AFTER_ENTITY_DEAD_ANIM" in line:
                        categories.append(("dead_event_unregistered", "AFTER_ENTITY_DEAD_ANIM", "Destroy unregisters death animation event."))
                    if "CList:Recyle(self.attackList)" in compact:
                        categories.append(("snapshot_list_recycled", "attackList", "Attack snapshot list is recycled."))
                    if "CList:Recyle(self.defenseList)" in compact:
                        categories.append(("snapshot_list_recycled", "defenseList", "Defense snapshot list is recycled."))

            for category, target, note in categories:
                _append_trigger_row(
                    rows,
                    root=root,
                    path=path,
                    role=role,
                    category=category,
                    target=target,
                    line_no=line_no,
                    function_name=current_function,
                    snippet=stripped,
                    note=note,
                )

        if role in scene_roles:
            _append_trigger_row(
                rows,
                root=root,
                path=path,
                role=role,
                category="scene_save_entity_callsite_count",
                target="SaveEntityData",
                line_no="summary",
                function_name="",
                snippet=str(save_call_count),
                note="Visible self:SaveEntityData(entityView) callsite count in this scene file.",
            )
            _append_trigger_row(
                rows,
                root=root,
                path=path,
                role=role,
                category="scene_checklist_callsite_count",
                target="CheckList",
                line_no="summary",
                function_name="",
                snippet=str(checklist_call_count),
                note="Visible self:CheckList(fightComponent) callsite count in this scene file.",
            )
    return rows


def _write_doupotd_pvp_report_trigger_lifecycle_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report trigger lifecycle",
        "",
        "This report checks the visible trigger path leading to `DoupoTDPVPSceneView:CheckList(...)` and `CM_DoupoTDReportFun(...)`, using DigitDoor/TowerDefense PVP scene views as nearby baselines.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Key Evidence", "", "| Role | Category | Target | File | Line | Snippet |", "| --- | --- | --- | --- | ---: | --- |"])
    priority = {
        "mgr_opens_pvp_scene_window",
        "dead_event_saves_entity_data",
        "dead_event_updates_hp",
        "updatehp_zero_hp_gate",
        "updatehp_sets_dead_flag",
        "updatehp_triggers_checklist",
        "checklist_backfills_snapshot",
        "checklist_assigns_request_list",
        "checklist_sends_report",
        "baseline_checklist_sends_report",
        "scene_save_entity_callsite_count",
        "scene_checklist_callsite_count",
    }
    for row in rows:
        if row.get("category") not in priority:
            continue
        lines.append(
            "| "
            f"{row.get('role', '')} | "
            f"{row.get('category', '')} | "
            f"{row.get('target', '')} | "
            f"`{row.get('file', '')}` | "
            f"{row.get('line', '')} | "
            f"`{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `DoupoTDMgr` visibly opens `Window.DoupoTDPVPSceneView`, so the scene view itself is a real runtime surface.",
            "- `DoupoTDPVPSceneView:CheckList` visibly builds `atkVoList/defVoList`, assigns winner fields, and calls `CM_DoupoTDReportFun(...)`.",
            "- Unlike the nearby DigitDoor/TowerDefense baselines, the current visible DoupoTD scene has no `self:CheckList(fightComponent)` callsite in `UpdateHp`, and its death event handler only calls `UpdateHp()` rather than `SaveEntityData(entityView)`.",
            "- `SaveEntityData` exists in DoupoTD and can append attack/defense snapshots, but in this visible scene file it is not wired by a visible callsite.",
            "- This narrows the runtime question: read-only Runtime evidence should prove whether an unseen runtime/base/native path invokes `CheckList`, or whether the visible DoupoTD scene report body is currently orphaned.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_trigger_lifecycle_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    rows = _doupotd_pvp_report_trigger_lifecycle_rows(root)
    category_counts = Counter((str(row.get("role") or ""), str(row.get("category") or "")) for row in rows)

    def count(role: str, category: str) -> int:
        return category_counts.get((role, category), 0)

    def summary_int(role: str, category: str) -> int:
        total = 0
        for row in rows:
            if row.get("role") == role and row.get("category") == category:
                try:
                    total += int(str(row.get("snippet") or "0"))
                except ValueError:
                    pass
        return total

    baseline_save_calls = summary_int("digitdoor_baseline_scene", "scene_save_entity_callsite_count") + summary_int(
        "towerdefense_baseline_scene",
        "scene_save_entity_callsite_count",
    )
    baseline_checklist_calls = summary_int("digitdoor_baseline_scene", "scene_checklist_callsite_count") + summary_int(
        "towerdefense_baseline_scene",
        "scene_checklist_callsite_count",
    )
    stats = {
        "source_file_count": len(_doupotd_pvp_report_trigger_lifecycle_files(root)),
        "evidence_row_count": len(rows),
        "doupotd_mgr_open_scene_rows": count("doupotd_mgr", "mgr_opens_pvp_scene_window"),
        "doupotd_dead_event_registered_rows": count("doupotd_scene", "dead_event_registered"),
        "doupotd_dead_event_updates_hp_rows": count("doupotd_scene", "dead_event_updates_hp"),
        "doupotd_dead_event_saves_entity_data_rows": count("doupotd_scene", "dead_event_saves_entity_data"),
        "doupotd_updatehp_zero_hp_gate_rows": count("doupotd_scene", "updatehp_zero_hp_gate"),
        "doupotd_updatehp_triggers_checklist_rows": count("doupotd_scene", "updatehp_triggers_checklist"),
        "doupotd_visible_save_entity_callsite_count": summary_int("doupotd_scene", "scene_save_entity_callsite_count"),
        "doupotd_visible_checklist_callsite_count": summary_int("doupotd_scene", "scene_checklist_callsite_count"),
        "doupotd_save_entity_function_rows": count("doupotd_scene", "save_entity_adds_snapshot"),
        "doupotd_checklist_report_send_rows": count("doupotd_scene", "checklist_sends_report"),
        "doupotd_checklist_backfill_rows": count("doupotd_scene", "checklist_backfills_snapshot"),
        "baseline_visible_save_entity_callsite_count": baseline_save_calls,
        "baseline_visible_checklist_callsite_count": baseline_checklist_calls,
        "baseline_updatehp_triggers_checklist_rows": count("digitdoor_baseline_scene", "updatehp_triggers_checklist")
        + count("towerdefense_baseline_scene", "updatehp_triggers_checklist"),
    }
    verdict = {
        "doupotd_pvp_scene_window_open_visible": stats["doupotd_mgr_open_scene_rows"] > 0,
        "doupotd_report_body_visible": stats["doupotd_checklist_report_send_rows"] > 0
        and stats["doupotd_checklist_backfill_rows"] >= 2,
        "doupotd_hp_zero_gate_visible": stats["doupotd_updatehp_zero_hp_gate_rows"] > 0,
        "doupotd_visible_checklist_callsite_missing": stats["doupotd_visible_checklist_callsite_count"] == 0,
        "doupotd_death_event_snapshot_call_missing": stats["doupotd_dead_event_saves_entity_data_rows"] == 0
        and stats["doupotd_visible_save_entity_callsite_count"] == 0,
        "doupotd_save_entity_function_present_but_unwired": stats["doupotd_save_entity_function_rows"] >= 2
        and stats["doupotd_visible_save_entity_callsite_count"] == 0,
        "nearby_baseline_trigger_pattern_visible": stats["baseline_visible_save_entity_callsite_count"] > 0
        and stats["baseline_visible_checklist_callsite_count"] > 0
        and stats["baseline_updatehp_triggers_checklist_rows"] > 0,
    }
    verdict["doupotd_pvp_report_visible_trigger_gap_confirmed"] = (
        verdict["doupotd_pvp_scene_window_open_visible"]
        and verdict["doupotd_report_body_visible"]
        and verdict["doupotd_hp_zero_gate_visible"]
        and verdict["doupotd_visible_checklist_callsite_missing"]
        and verdict["doupotd_death_event_snapshot_call_missing"]
        and verdict["doupotd_save_entity_function_present_but_unwired"]
        and verdict["nearby_baseline_trigger_pattern_visible"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_tsv = output_dir / "doupotd_pvp_report_trigger_lifecycle_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_trigger_lifecycle_report.md"
    json_path = output_dir / "doupotd_pvp_report_trigger_lifecycle_report.json"
    _write_tsv(evidence_tsv, rows, ["role", "category", "target", "file", "line", "function", "snippet", "note"])
    _write_doupotd_pvp_report_trigger_lifecycle_markdown(report_path, stats=stats, verdict=verdict, rows=rows)
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_report_visible_trigger_gap_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "files": {
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
        "confirmed": verdict["doupotd_pvp_report_visible_trigger_gap_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _doupotd_trigger_lifecycle_visible_gap_from_rows(rows: list[dict[str, Any]]) -> bool:
    category_counts = Counter((str(row.get("role") or ""), str(row.get("category") or "")) for row in rows)

    def count(role: str, category: str) -> int:
        return category_counts.get((role, category), 0)

    def summary_int(role: str, category: str) -> int:
        total = 0
        for row in rows:
            if row.get("role") == role and row.get("category") == category:
                try:
                    total += int(str(row.get("snippet") or "0"))
                except ValueError:
                    pass
        return total

    baseline_save_calls = summary_int("digitdoor_baseline_scene", "scene_save_entity_callsite_count") + summary_int(
        "towerdefense_baseline_scene",
        "scene_save_entity_callsite_count",
    )
    baseline_checklist_calls = summary_int("digitdoor_baseline_scene", "scene_checklist_callsite_count") + summary_int(
        "towerdefense_baseline_scene",
        "scene_checklist_callsite_count",
    )
    return bool(
        count("doupotd_mgr", "mgr_opens_pvp_scene_window") > 0
        and count("doupotd_scene", "checklist_sends_report") > 0
        and count("doupotd_scene", "checklist_backfills_snapshot") >= 2
        and count("doupotd_scene", "updatehp_zero_hp_gate") > 0
        and summary_int("doupotd_scene", "scene_checklist_callsite_count") == 0
        and count("doupotd_scene", "dead_event_saves_entity_data") == 0
        and summary_int("doupotd_scene", "scene_save_entity_callsite_count") == 0
        and count("doupotd_scene", "save_entity_adds_snapshot") >= 2
        and baseline_save_calls > 0
        and baseline_checklist_calls > 0
        and (
            count("digitdoor_baseline_scene", "updatehp_triggers_checklist")
            + count("towerdefense_baseline_scene", "updatehp_triggers_checklist")
        )
        > 0
    )


def _doupotd_pvp_report_trigger_base_dynamic_add_row(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    surface: str,
    category: str,
    target: str,
    path: Path,
    line_no: int | str,
    function_name: str,
    snippet: str,
    note: str,
) -> None:
    rows.append(
        {
            "surface": surface,
            "category": category,
            "target": target,
            "file": str(path.relative_to(root)) if _is_relative_to(path, root) else str(path),
            "line": line_no,
            "function": function_name,
            "snippet": _WHITESPACE_RE.sub(" ", snippet.strip())[:360],
            "note": note,
        }
    )


def _doupotd_pvp_report_trigger_base_dynamic_rows(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "lua_scanned_file_count": 0,
        "activity_base_scene_file_count": 0,
        "cpp2il_scanned_file_count": 0,
    }
    lscript_root = root / "by_source" / "lscripts"
    dynamic_tokens = ("rawget", "rawset", "__index", "__newindex", "pcall", "xpcall", "Invoke", "SendMessage")
    if lscript_root.is_dir():
        for path in sorted(lscript_root.rglob("*.lua")):
            if "\\gamesystem\\game\\" not in str(path).lower().replace("/", "\\"):
                continue
            stats["lua_scanned_file_count"] += 1
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            current_function = ""
            rel_lower = str(path.relative_to(lscript_root) if _is_relative_to(path, lscript_root) else path.name).lower().replace("/", "\\")
            is_activity_base = path.name == "ActivityBaseSceneView.lua"
            is_doupotd_scene = path.name == "DoupoTDPVPSceneView.lua"
            is_doupotd_context = "\\doupotd_" in rel_lower or is_doupotd_scene
            if is_activity_base:
                stats["activity_base_scene_file_count"] += 1

            for line_no, line in enumerate(lines, 1):
                if match := _LUA_FUNCTION_RE.search(line):
                    current_function = match.group(1).strip()
                stripped = line.strip()
                compact = _WHITESPACE_RE.sub("", line)
                if not stripped or stripped.startswith("--"):
                    continue

                if is_activity_base:
                    if "function _M." in stripped:
                        _doupotd_pvp_report_trigger_base_dynamic_add_row(
                            rows,
                            root=root,
                            surface="activity_base",
                            category="activity_base_function",
                            target=current_function,
                            path=path,
                            line_no=line_no,
                            function_name=current_function,
                            snippet=stripped,
                            note="Visible ActivityBaseSceneView function.",
                        )
                    if "AddUpdateCallback" in stripped or "self:Update(fTime,fDTime)" in compact:
                        _doupotd_pvp_report_trigger_base_dynamic_add_row(
                            rows,
                            root=root,
                            surface="activity_base",
                            category="activity_base_update_tick_surface",
                            target="Update",
                            path=path,
                            line_no=line_no,
                            function_name=current_function,
                            snippet=stripped,
                            note="Base scene registers or forwards per-frame Update calls.",
                        )
                    if "CheckList" in stripped:
                        _doupotd_pvp_report_trigger_base_dynamic_add_row(
                            rows,
                            root=root,
                            surface="activity_base",
                            category="activity_base_checklist_symbol",
                            target="CheckList",
                            path=path,
                            line_no=line_no,
                            function_name=current_function,
                            snippet=stripped,
                            note="ActivityBaseSceneView references CheckList.",
                        )
                    if "SaveEntityData" in stripped:
                        _doupotd_pvp_report_trigger_base_dynamic_add_row(
                            rows,
                            root=root,
                            surface="activity_base",
                            category="activity_base_save_entity_symbol",
                            target="SaveEntityData",
                            path=path,
                            line_no=line_no,
                            function_name=current_function,
                            snippet=stripped,
                            note="ActivityBaseSceneView references SaveEntityData.",
                        )

                if is_doupotd_scene:
                    if "ActivityBaseSceneView=require" in compact:
                        _doupotd_pvp_report_trigger_base_dynamic_add_row(
                            rows,
                            root=root,
                            surface="doupotd_scene",
                            category="doupotd_scene_requires_activity_base",
                            target="ActivityBaseSceneView",
                            path=path,
                            line_no=line_no,
                            function_name=current_function,
                            snippet=stripped,
                            note="DoupoTDPVPSceneView inherits the shared activity scene base.",
                        )
                    if "_M=class(ActivityBaseSceneView,_M)" in compact:
                        _doupotd_pvp_report_trigger_base_dynamic_add_row(
                            rows,
                            root=root,
                            surface="doupotd_scene",
                            category="doupotd_scene_activity_base_class",
                            target="ActivityBaseSceneView",
                            path=path,
                            line_no=line_no,
                            function_name=current_function,
                            snippet=stripped,
                            note="DoupoTDPVPSceneView is constructed with ActivityBaseSceneView as its parent.",
                        )
                    if re.search(r"\bfunction\s+_M\.CheckList\s*\(", stripped):
                        _doupotd_pvp_report_trigger_base_dynamic_add_row(
                            rows,
                            root=root,
                            surface="doupotd_scene",
                            category="doupotd_checklist_function",
                            target="CheckList",
                            path=path,
                            line_no=line_no,
                            function_name=current_function,
                            snippet=stripped,
                            note="DoupoTD report body function is visible in the scene.",
                        )
                    if "CM_DoupoTDReportFun" in stripped:
                        _doupotd_pvp_report_trigger_base_dynamic_add_row(
                            rows,
                            root=root,
                            surface="doupotd_scene",
                            category="doupotd_checklist_report_call",
                            target="CM_DoupoTDReportFun",
                            path=path,
                            line_no=line_no,
                            function_name=current_function,
                            snippet=stripped,
                            note="DoupoTD CheckList calls the visible report sender surface.",
                        )

                if ":CheckList(" in compact and "function_M.CheckList" not in compact:
                    category = "global_doupotd_checklist_callsite" if is_doupotd_context or "DoupoTD" in stripped else "global_other_checklist_callsite"
                    _doupotd_pvp_report_trigger_base_dynamic_add_row(
                        rows,
                        root=root,
                        surface="global_lua",
                        category=category,
                        target="CheckList",
                        path=path,
                        line_no=line_no,
                        function_name=current_function,
                        snippet=stripped,
                        note="Visible Lua callsite to a CheckList method.",
                    )
                if ":SaveEntityData(" in compact and "function_M.SaveEntityData" not in compact:
                    category = "global_doupotd_save_entity_callsite" if is_doupotd_context or "DoupoTD" in stripped else "global_other_save_entity_callsite"
                    _doupotd_pvp_report_trigger_base_dynamic_add_row(
                        rows,
                        root=root,
                        surface="global_lua",
                        category=category,
                        target="SaveEntityData",
                        path=path,
                        line_no=line_no,
                        function_name=current_function,
                        snippet=stripped,
                        note="Visible Lua callsite to a SaveEntityData method.",
                    )

                dynamic_line = any(token in stripped for token in dynamic_tokens)
                if dynamic_line and ("CheckList" in stripped or "SaveEntityData" in stripped or "DoupoTDPVPSceneView" in stripped):
                    if "DoupoTDPVPSceneView" in stripped:
                        category = "dynamic_doupotd_scene_symbol"
                        target = "DoupoTDPVPSceneView"
                    elif ("CheckList" in stripped or "SaveEntityData" in stripped) and (is_doupotd_context or "DoupoTD" in stripped):
                        category = "dynamic_doupotd_checklist_symbol"
                        target = "CheckList/SaveEntityData"
                    else:
                        category = "dynamic_other_checklist_symbol"
                        target = "CheckList/SaveEntityData"
                    _doupotd_pvp_report_trigger_base_dynamic_add_row(
                        rows,
                        root=root,
                        surface="dynamic_lua",
                        category=category,
                        target=target,
                        path=path,
                        line_no=line_no,
                        function_name=current_function,
                        snippet=stripped,
                        note="Line combines a target symbol with a dynamic-dispatch token.",
                    )

    cpp2il_root = root / "apk_static_index"
    cpp2il_patterns = ("*.cs", "*.txt", "*.isil", "*.json", "*.tsv")
    cpp2il_targets = ("DoupoTDPVPSceneView", "CheckList", "SaveEntityData", "CM_DoupoTDReportFun")
    for cpp2il_dir in sorted(cpp2il_root.glob("cpp2il_*")):
        if not cpp2il_dir.is_dir():
            continue
        for pattern in cpp2il_patterns:
            for path in sorted(cpp2il_dir.rglob(pattern)):
                if not path.is_file():
                    continue
                stats["cpp2il_scanned_file_count"] += 1
                try:
                    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                except OSError:
                    continue
                for line_no, line in enumerate(lines, 1):
                    if not any(target in line for target in cpp2il_targets):
                        continue
                    _doupotd_pvp_report_trigger_base_dynamic_add_row(
                        rows,
                        root=root,
                        surface="cpp2il",
                        category="cpp2il_target_symbol_hit",
                        target="/".join(target for target in cpp2il_targets if target in line),
                        path=path,
                        line_no=line_no,
                        function_name="",
                        snippet=line,
                        note="Readable Cpp2IL export contains a target trigger symbol.",
                    )
    return rows, stats


def _write_doupotd_pvp_report_trigger_base_dynamic_gap_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report trigger base/dynamic gap",
        "",
        "This report extends the scene-level lifecycle probe by checking whether the visible parent class, global Lua callsites, dynamic-dispatch hints, or readable Cpp2IL exports provide an alternate trigger for `DoupoTDPVPSceneView:CheckList(...)`.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Key Evidence", "", "| Surface | Category | Target | File | Line | Snippet |", "| --- | --- | --- | --- | ---: | --- |"])
    priority = {
        "activity_base_function",
        "activity_base_update_tick_surface",
        "activity_base_checklist_symbol",
        "activity_base_save_entity_symbol",
        "doupotd_scene_requires_activity_base",
        "doupotd_scene_activity_base_class",
        "doupotd_checklist_function",
        "doupotd_checklist_report_call",
        "global_doupotd_checklist_callsite",
        "global_doupotd_save_entity_callsite",
        "dynamic_doupotd_checklist_symbol",
        "dynamic_doupotd_scene_symbol",
        "cpp2il_target_symbol_hit",
    }
    for row in rows:
        if row.get("category") not in priority:
            continue
        lines.append(
            "| "
            f"{row.get('surface', '')} | "
            f"{row.get('category', '')} | "
            f"{row.get('target', '')} | "
            f"`{row.get('file', '')}` | "
            f"{row.get('line', '')} | "
            f"`{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `ActivityBaseSceneView` is visible and only provides generic UI/list/update plumbing; no visible `CheckList` or `SaveEntityData` trigger is present there.",
            "- The DoupoTD PVP scene does inherit that base and does define the report body, but the scan still finds no DoupoTD-context `:CheckList(...)` or `:SaveEntityData(...)` caller outside the already-known scene-local gap.",
            "- No target `CheckList`/`SaveEntityData`/`DoupoTDPVPSceneView` dynamic-dispatch hint or readable Cpp2IL named trigger is visible in this export.",
            "- The practical next evidence step is read-only Runtime observation of the Lua/runtime callback path that calls `CheckList`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_trigger_base_dynamic_gap_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    rows, collected_stats = _doupotd_pvp_report_trigger_base_dynamic_rows(root)
    lifecycle_rows = _doupotd_pvp_report_trigger_lifecycle_rows(root)
    category_counts = Counter(str(row.get("category") or "") for row in rows)
    stats = {
        "lua_scanned_file_count": collected_stats["lua_scanned_file_count"],
        "activity_base_scene_file_count": collected_stats["activity_base_scene_file_count"],
        "evidence_row_count": len(rows),
        "lifecycle_evidence_row_count": len(lifecycle_rows),
        "visible_trigger_gap_from_lifecycle_rows": _doupotd_trigger_lifecycle_visible_gap_from_rows(lifecycle_rows),
        "activity_base_function_rows": category_counts.get("activity_base_function", 0),
        "activity_base_update_tick_rows": category_counts.get("activity_base_update_tick_surface", 0),
        "activity_base_checklist_symbol_rows": category_counts.get("activity_base_checklist_symbol", 0),
        "activity_base_save_entity_symbol_rows": category_counts.get("activity_base_save_entity_symbol", 0),
        "doupotd_scene_activity_base_rows": category_counts.get("doupotd_scene_requires_activity_base", 0)
        + category_counts.get("doupotd_scene_activity_base_class", 0),
        "doupotd_scene_checklist_function_rows": category_counts.get("doupotd_checklist_function", 0),
        "doupotd_scene_report_call_rows": category_counts.get("doupotd_checklist_report_call", 0),
        "global_doupotd_checklist_call_rows": category_counts.get("global_doupotd_checklist_callsite", 0),
        "global_doupotd_save_entity_call_rows": category_counts.get("global_doupotd_save_entity_callsite", 0),
        "dynamic_doupotd_checklist_symbol_rows": category_counts.get("dynamic_doupotd_checklist_symbol", 0),
        "dynamic_doupotd_scene_symbol_rows": category_counts.get("dynamic_doupotd_scene_symbol", 0),
        "cpp2il_scanned_file_count": collected_stats["cpp2il_scanned_file_count"],
        "cpp2il_target_symbol_hit_count": category_counts.get("cpp2il_target_symbol_hit", 0),
    }
    verdict = {
        "visible_scene_trigger_gap_already_confirmed": stats["visible_trigger_gap_from_lifecycle_rows"],
        "doupotd_inherits_activity_base": stats["doupotd_scene_activity_base_rows"] >= 2,
        "activity_base_has_no_checklist_trigger": stats["activity_base_scene_file_count"] > 0
        and stats["activity_base_checklist_symbol_rows"] == 0
        and stats["activity_base_save_entity_symbol_rows"] == 0,
        "activity_base_only_exposes_generic_update_tick": stats["activity_base_update_tick_rows"] > 0,
        "global_lua_has_no_doupotd_checklist_callsite": stats["global_doupotd_checklist_call_rows"] == 0,
        "global_lua_has_no_doupotd_save_entity_callsite": stats["global_doupotd_save_entity_call_rows"] == 0,
        "dynamic_lua_generation_hint_absent_or_unresolved": stats["dynamic_doupotd_checklist_symbol_rows"] == 0
        and stats["dynamic_doupotd_scene_symbol_rows"] == 0,
        "readable_cpp2il_has_no_named_trigger": stats["cpp2il_target_symbol_hit_count"] == 0,
        "static_base_dynamic_scan_only": True,
    }
    verdict["doupotd_pvp_report_trigger_base_dynamic_gap_confirmed"] = bool(
        verdict["visible_scene_trigger_gap_already_confirmed"]
        and verdict["doupotd_inherits_activity_base"]
        and verdict["activity_base_has_no_checklist_trigger"]
        and verdict["global_lua_has_no_doupotd_checklist_callsite"]
        and verdict["global_lua_has_no_doupotd_save_entity_callsite"]
        and verdict["dynamic_lua_generation_hint_absent_or_unresolved"]
        and verdict["readable_cpp2il_has_no_named_trigger"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_tsv = output_dir / "doupotd_pvp_report_trigger_base_dynamic_gap_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_trigger_base_dynamic_gap_report.md"
    json_path = output_dir / "doupotd_pvp_report_trigger_base_dynamic_gap_report.json"
    _write_tsv(evidence_tsv, rows, ["surface", "category", "target", "file", "line", "function", "snippet", "note"])
    _write_doupotd_pvp_report_trigger_base_dynamic_gap_markdown(report_path, stats=stats, verdict=verdict, rows=rows)
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_report_trigger_base_dynamic_gap_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "files": {
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
        "confirmed": verdict["doupotd_pvp_report_trigger_base_dynamic_gap_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _doupotd_pvp_report_trigger_delta_scene_files(root: Path) -> list[Path]:
    patterns = [
        "by_source/lscripts/gamesystem/game/doupotd_*/text_assets/DoupoTDPVPSceneView.lua",
        "by_source/lscripts/gamesystem/game/digitdoor_*/text_assets/DigitDoorPVPSceneView.lua",
        "by_source/lscripts/gamesystem/game/towerdefense_*/text_assets/TowerDefensePVPSceneView.lua",
    ]
    unique: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda item: str(item).lower())


def _doupotd_pvp_report_trigger_delta_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _doupotd_pvp_report_trigger_delta_scene_files(root):
        role = _doupotd_trigger_source_role(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        current_function = ""
        for line_no, line in enumerate(text.splitlines(), 1):
            if match := _LUA_FUNCTION_RE.search(line):
                current_function = match.group(1).strip()
            stripped = _WHITESPACE_RE.sub(" ", line.strip())
            compact = _WHITESPACE_RE.sub("", line)
            if not stripped or stripped.startswith("--"):
                continue
            categories: list[tuple[str, str, str]] = []

            if current_function == "_M.AddEvent":
                if "self.entityDead=function" in compact:
                    categories.append(("death_handler_defined", "entityDead", "Death handler is defined."))
                if "self:SaveEntityData(entityView)" in compact:
                    categories.append(("death_handler_snapshots_entity", "SaveEntityData", "Death handler snapshots dead entity data."))
                if "self:UpdateHp()" in compact:
                    categories.append(("death_handler_updates_hp", "UpdateHp", "Death handler updates HP/report state."))
                if "ENTITY_ENTER_DEAD" in line or "AFTER_ENTITY_DEAD_ANIM" in line:
                    categories.append(("death_handler_registered", "entityDead", "Death handler is registered to entity-death events."))
            elif current_function == "_M.UpdateHp":
                if "FightMgr.Inst_get().UserFightComponent" in compact:
                    categories.append(("updatehp_uses_user_fight_component", "UserFightComponent", "UpdateHp reads the module fight component."))
                if "GetDefenseHPMsg()" in compact:
                    categories.append(("updatehp_reads_defense_hp", "GetDefenseHPMsg", "UpdateHp reads defense-side HP."))
                if "GetAttackHPMsg()" in compact:
                    categories.append(("updatehp_reads_attack_hp", "GetAttackHPMsg", "UpdateHp reads attack-side HP."))
                if "curHp==0" in compact:
                    categories.append(("updatehp_zero_hp_gate", "curHp", "UpdateHp checks zero HP."))
                if "GetUserId()" in compact:
                    categories.append(("updatehp_user_id_lookup", "GetUserId", "UpdateHp reads current user id for winner gating."))
                if "self.winnerId" in compact:
                    categories.append(("updatehp_winner_guard", "winnerId", "UpdateHp gates report trigger against finish-VO winner."))
                if "self:CheckList(fightComponent)" in compact:
                    categories.append(("updatehp_triggers_checklist", "CheckList", "UpdateHp calls CheckList after a zero-HP/winner gate."))
            elif current_function == "_M.SaveEntityData":
                if "LuaEntityType.DoupoTDPartner" in compact:
                    categories.append(("save_entity_partner_guard", "DoupoTDPartner", "SaveEntityData accepts DoupoTD partner entities."))
                if "LuaEntityType.DigitDoorPartner" in compact:
                    categories.append(("save_entity_partner_guard", "DigitDoorPartner", "SaveEntityData accepts DigitDoor partner entities."))
                if "CampGroup.Attack" in compact:
                    categories.append(("save_entity_attack_branch", "attackList", "SaveEntityData has an attack-side snapshot branch."))
                if "self.attackList:Add(data)" in compact:
                    categories.append(("save_entity_adds_attack_snapshot", "attackList", "SaveEntityData appends attack snapshot."))
                if "self.defenseList:Add(data)" in compact:
                    categories.append(("save_entity_adds_defense_snapshot", "defenseList", "SaveEntityData appends defense snapshot."))
            elif current_function == "_M.CheckList":
                if "fightComponent:GetDefenseViewList()" in compact:
                    categories.append(("checklist_backfills_defense_views", "defenseList", "CheckList backfills remaining defense views."))
                if "fightComponent:GetAttackViewList()" in compact:
                    categories.append(("checklist_backfills_attack_views", "attackList", "CheckList backfills remaining attack views."))
                if "clientWinnerId=" in compact:
                    categories.append(("checklist_assigns_client_winner", "clientWinnerId", "CheckList assigns clientWinnerId."))
                if "serverWinnerId=" in compact:
                    categories.append(("checklist_assigns_server_winner", "serverWinnerId", "CheckList assigns serverWinnerId."))
                if "CM_DoupoTDReportFun(" in line:
                    categories.append(("checklist_sends_doupotd_report", "CM_DoupoTDReportFun", "CheckList calls the DoupoTD report sender surface."))
                if "CM_DigitDoorReportFun(" in line:
                    categories.append(("checklist_sends_digitdoor_report", "CM_DigitDoorReportFun", "CheckList calls the DigitDoor report sender surface."))

            for category, target, note in categories:
                _append_trigger_row(
                    rows,
                    root=root,
                    path=path,
                    role=role,
                    category=category,
                    target=target,
                    line_no=line_no,
                    function_name=current_function,
                    snippet=stripped,
                    note=note,
                )
    category_counts = Counter((str(row.get("role") or ""), str(row.get("category") or "")) for row in rows)

    def count(role: str, category: str) -> int:
        return category_counts.get((role, category), 0)

    baseline_roles = ("digitdoor_baseline_scene", "towerdefense_baseline_scene")
    baseline_death_snapshots = sum(count(role, "death_handler_snapshots_entity") for role in baseline_roles)
    baseline_updatehp_triggers = sum(count(role, "updatehp_triggers_checklist") for role in baseline_roles)
    baseline_winner_guards = sum(count(role, "updatehp_winner_guard") for role in baseline_roles)
    doupotd_missing: list[tuple[str, str, str]] = []
    if baseline_death_snapshots > 0 and count("doupotd_scene", "death_handler_snapshots_entity") == 0:
        doupotd_missing.append(
            (
                "delta_missing_death_snapshot_edge",
                "SaveEntityData",
                "DigitDoor/TowerDefense death handlers snapshot dead entities, but DoupoTD death handler does not.",
            )
        )
    if baseline_updatehp_triggers > 0 and count("doupotd_scene", "updatehp_triggers_checklist") == 0:
        doupotd_missing.append(
            (
                "delta_missing_updatehp_checklist_edge",
                "CheckList",
                "DigitDoor/TowerDefense UpdateHp call CheckList after zero-HP/winner gates, but DoupoTD UpdateHp does not.",
            )
        )
    if baseline_winner_guards > 0 and count("doupotd_scene", "updatehp_winner_guard") == 0:
        doupotd_missing.append(
            (
                "delta_missing_updatehp_winner_guard",
                "winnerId",
                "DigitDoor/TowerDefense UpdateHp compare winner/user ids before report trigger, but DoupoTD UpdateHp has no visible winner guard.",
            )
        )
    doupotd_paths = [path for path in _doupotd_pvp_report_trigger_delta_scene_files(root) if path.name == "DoupoTDPVPSceneView.lua"]
    synthetic_path = doupotd_paths[0] if doupotd_paths else root
    for category, target, note in doupotd_missing:
        _append_trigger_row(
            rows,
            root=root,
            path=synthetic_path,
            role="doupotd_scene",
            category=category,
            target=target,
            line_no="delta",
            function_name="",
            snippet=note,
            note=note,
        )
    return rows


def _write_doupotd_pvp_report_trigger_delta_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report trigger delta",
        "",
        "This report compares the trigger-relevant scene functions in `DoupoTDPVPSceneView` against the nearest visible PVP report baselines, `DigitDoorPVPSceneView` and `TowerDefensePVPSceneView`.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Delta Evidence", "", "| Role | Category | Target | Function | Line | Snippet |", "| --- | --- | --- | --- | ---: | --- |"])
    priority = {
        "death_handler_snapshots_entity",
        "death_handler_updates_hp",
        "updatehp_zero_hp_gate",
        "updatehp_winner_guard",
        "updatehp_triggers_checklist",
        "checklist_sends_doupotd_report",
        "checklist_sends_digitdoor_report",
        "delta_missing_death_snapshot_edge",
        "delta_missing_updatehp_checklist_edge",
        "delta_missing_updatehp_winner_guard",
    }
    for row in rows:
        if row.get("category") not in priority:
            continue
        lines.append(
            "| "
            f"{row.get('role', '')} | "
            f"{row.get('category', '')} | "
            f"{row.get('target', '')} | "
            f"`{row.get('function', '')}` | "
            f"{row.get('line', '')} | "
            f"`{str(row.get('snippet', '')).replace('|', '\\|')}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- The three scene files share the same report-body skeleton: dead/live entity snapshots are accumulated, winner fields are assigned, and a report sender function is called.",
            "- The divergence is before the body: visible DoupoTD lacks the death-handler snapshot edge and the UpdateHp-to-CheckList edge that exist in the nearby baselines.",
            "- Treat this as a static trigger-delta finding, not as proof of server trust or runtime execution. Read-only Runtime state is still the next evidence source for whether `93671/CM_DoupoReport` appears.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_trigger_delta_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    rows = _doupotd_pvp_report_trigger_delta_rows(root)
    category_counts = Counter((str(row.get("role") or ""), str(row.get("category") or "")) for row in rows)

    def count(role: str, category: str) -> int:
        return category_counts.get((role, category), 0)

    baseline_roles = ("digitdoor_baseline_scene", "towerdefense_baseline_scene")
    stats = {
        "source_file_count": len(_doupotd_pvp_report_trigger_delta_scene_files(root)),
        "evidence_row_count": len(rows),
        "doupotd_death_handler_snapshot_rows": count("doupotd_scene", "death_handler_snapshots_entity"),
        "doupotd_death_handler_updatehp_rows": count("doupotd_scene", "death_handler_updates_hp"),
        "doupotd_updatehp_zero_hp_gate_rows": count("doupotd_scene", "updatehp_zero_hp_gate"),
        "doupotd_updatehp_winner_guard_rows": count("doupotd_scene", "updatehp_winner_guard"),
        "doupotd_updatehp_checklist_rows": count("doupotd_scene", "updatehp_triggers_checklist"),
        "doupotd_checklist_report_rows": count("doupotd_scene", "checklist_sends_doupotd_report"),
        "baseline_death_handler_snapshot_rows": sum(count(role, "death_handler_snapshots_entity") for role in baseline_roles),
        "baseline_updatehp_winner_guard_rows": sum(count(role, "updatehp_winner_guard") for role in baseline_roles),
        "baseline_updatehp_checklist_rows": sum(count(role, "updatehp_triggers_checklist") for role in baseline_roles),
        "baseline_checklist_report_rows": sum(count(role, "checklist_sends_digitdoor_report") for role in baseline_roles),
        "delta_missing_edge_rows": count("doupotd_scene", "delta_missing_death_snapshot_edge")
        + count("doupotd_scene", "delta_missing_updatehp_checklist_edge")
        + count("doupotd_scene", "delta_missing_updatehp_winner_guard"),
    }
    verdict = {
        "doupotd_report_body_visible": stats["doupotd_checklist_report_rows"] > 0,
        "baseline_report_trigger_edges_visible": stats["baseline_death_handler_snapshot_rows"] > 0
        and stats["baseline_updatehp_checklist_rows"] > 0,
        "doupotd_death_snapshot_edge_missing_vs_baseline": stats["doupotd_death_handler_snapshot_rows"] == 0
        and stats["baseline_death_handler_snapshot_rows"] > 0,
        "doupotd_updatehp_checklist_edge_missing_vs_baseline": stats["doupotd_updatehp_checklist_rows"] == 0
        and stats["baseline_updatehp_checklist_rows"] > 0,
        "doupotd_updatehp_winner_guard_missing_vs_baseline": stats["doupotd_updatehp_winner_guard_rows"] == 0
        and stats["baseline_updatehp_winner_guard_rows"] > 0,
        "static_trigger_delta_only": True,
    }
    verdict["doupotd_pvp_report_trigger_delta_confirmed"] = bool(
        verdict["doupotd_report_body_visible"]
        and verdict["baseline_report_trigger_edges_visible"]
        and verdict["doupotd_death_snapshot_edge_missing_vs_baseline"]
        and verdict["doupotd_updatehp_checklist_edge_missing_vs_baseline"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_tsv = output_dir / "doupotd_pvp_report_trigger_delta_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_trigger_delta_report.md"
    json_path = output_dir / "doupotd_pvp_report_trigger_delta_report.json"
    _write_tsv(evidence_tsv, rows, ["role", "category", "target", "file", "line", "function", "snippet", "note"])
    _write_doupotd_pvp_report_trigger_delta_markdown(report_path, stats=stats, verdict=verdict, rows=rows)
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_report_trigger_delta_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "files": {
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
        "confirmed": verdict["doupotd_pvp_report_trigger_delta_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "evidence": str(evidence_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _doupotd_pvp_report_native_symbol_surface_files(root: Path) -> list[tuple[str, list[Path]]]:
    output_dir = root / "apk_static_index"
    cpp2il_dirs = [path for path in output_dir.glob("cpp2il_*") if path.is_dir()]
    cpp2il_files: list[Path] = []
    for cpp2il_dir in cpp2il_dirs:
        cpp2il_files.extend(path for path in cpp2il_dir.rglob("*.txt") if path.is_file())
    metadata_names = {
        "il2cpp_types.tsv",
        "il2cpp_methods.tsv",
        "il2cpp_fields.tsv",
        "il2cpp_parameters.tsv",
        "il2cpp_strings.tsv",
        "il2cpp_string_literals.tsv",
        "il2cpp_keyword_hits.tsv",
        "il2cpp_gameplay_symbol_types.tsv",
        "il2cpp_gameplay_symbol_methods.tsv",
        "il2cpp_gameplay_symbol_fields.tsv",
        "il2cpp_gameplay_symbol_strings.tsv",
    }
    metadata_files = [output_dir / name for name in sorted(metadata_names) if (output_dir / name).is_file()]
    binary_files = sorted(path for path in output_dir.glob("apk_il2cpp_binary_boundary_*.tsv") if path.is_file())
    return [
        ("il2cpp_metadata_tsv", metadata_files),
        ("il2cpp_binary_boundary_tsv", binary_files),
        ("cpp2il_isil_dump", sorted(cpp2il_files)),
    ]


def _collect_doupotd_pvp_report_native_symbol_hits(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    terms = ["CM_DoupoTDReport", "SM_DoupoTDReport", "CM_DoupoTDReportFun", "DoupoTDReport"]
    term_re = re.compile("|".join(re.escape(term) for term in terms))
    surfaces: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    for surface_name, files in _doupotd_pvp_report_native_symbol_surface_files(root):
        file_count = 0
        hit_count = 0
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            file_count += 1
            if not term_re.search(text):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                match = term_re.search(line)
                if not match:
                    continue
                hit_count += 1
                if len(hits) < 200:
                    hits.append(
                        {
                            "surface": surface_name,
                            "source": str(path.relative_to(root)),
                            "line": line_no,
                            "term": match.group(0),
                            "snippet": line.strip()[:360],
                        }
                    )
        surfaces.append(
            {
                "surface": surface_name,
                "file_count": file_count,
                "hit_count": hit_count,
            }
        )
    return surfaces, hits


def _write_doupotd_pvp_report_native_symbol_gap_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    surfaces: list[dict[str, Any]],
    hits: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report native symbol gap report",
        "",
        "This is a static exact-symbol scan over readable IL2CPP metadata, binary-boundary TSVs, and Cpp2IL ISIL text dumps.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Surfaces", ""])
    for row in surfaces:
        lines.append(f"- `{row.get('surface')}` files `{row.get('file_count')}` hits `{row.get('hit_count')}`")
    lines.extend(["", "## Hits", ""])
    if hits:
        for row in hits[:80]:
            lines.append(
                f"- `{row.get('surface')}` `{row.get('source')}:{row.get('line')}` `{row.get('term')}` `{row.get('snippet')}`"
            )
    else:
        lines.append("- No exact native/metadata symbol hit for `CM_DoupoTDReport`, `SM_DoupoTDReport`, `CM_DoupoTDReportFun`, or `DoupoTDReport`.")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "A zero exact-symbol hit only closes the current readable static surfaces. It does not disprove runtime dispatch through numeric ids, obfuscated tables, generated Lua not present in the export, or server-side handling.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_native_symbol_gap_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    surfaces, hits = _collect_doupotd_pvp_report_native_symbol_hits(root)
    stats = {
        "surface_count": len(surfaces),
        "surface_file_count": sum(int(row.get("file_count") or 0) for row in surfaces),
        "exact_symbol_hit_count": len(hits),
        "metadata_tsv_file_count": sum(
            int(row.get("file_count") or 0) for row in surfaces if row.get("surface") == "il2cpp_metadata_tsv"
        ),
        "binary_boundary_tsv_file_count": sum(
            int(row.get("file_count") or 0) for row in surfaces if row.get("surface") == "il2cpp_binary_boundary_tsv"
        ),
        "cpp2il_isil_file_count": sum(
            int(row.get("file_count") or 0) for row in surfaces if row.get("surface") == "cpp2il_isil_dump"
        ),
    }
    verdict = {
        "native_readable_surfaces_scanned": stats["surface_file_count"] > 0,
        "no_exact_doupotd_report_symbol_in_native_readable_surfaces": stats["exact_symbol_hit_count"] == 0,
        "static_exact_symbol_boundary_only": True,
    }
    verdict["doupotd_pvp_native_symbol_gap_confirmed"] = bool(
        verdict["native_readable_surfaces_scanned"]
        and verdict["no_exact_doupotd_report_symbol_in_native_readable_surfaces"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    surfaces_tsv = output_dir / "doupotd_pvp_report_native_symbol_gap_surfaces.tsv"
    hits_tsv = output_dir / "doupotd_pvp_report_native_symbol_gap_hits.tsv"
    report_path = output_dir / "doupotd_pvp_report_native_symbol_gap_report.md"
    json_path = output_dir / "doupotd_pvp_report_native_symbol_gap_report.json"
    _write_tsv(surfaces_tsv, surfaces, ["surface", "file_count", "hit_count"])
    _write_tsv(hits_tsv, hits, ["surface", "source", "line", "term", "snippet"])
    _write_doupotd_pvp_report_native_symbol_gap_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        surfaces=surfaces,
        hits=hits,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_native_symbol_gap_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "files": {
                    "surfaces": str(surfaces_tsv),
                    "hits": str(hits_tsv),
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
        "confirmed": verdict["doupotd_pvp_native_symbol_gap_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "surfaces": str(surfaces_tsv),
            "hits": str(hits_tsv),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }


def _doupotd_cpp2il_assembly_surface_files(root: Path) -> list[tuple[str, list[Path]]]:
    index_dir = root / "apk_static_index"
    surfaces: list[tuple[str, list[Path]]] = []
    for cpp2il_dir in sorted(path for path in index_dir.glob("cpp2il_*") if path.is_dir()):
        name = cpp2il_dir.name.lower()
        if "diffable" in name:
            assembly_root = cpp2il_dir / "DiffableCs" / "Assembly-CSharp"
            files = sorted(assembly_root.rglob("*.cs")) if assembly_root.is_dir() else sorted(cpp2il_dir.rglob("*.cs"))
            surfaces.append(("cpp2il_diffable_cs_assembly", [path for path in files if path.is_file()]))
        elif "isil" in name:
            assembly_root = cpp2il_dir / "IsilDump" / "Assembly-CSharp"
            files = sorted(assembly_root.rglob("*.txt")) if assembly_root.is_dir() else sorted(cpp2il_dir.rglob("*.txt"))
            surfaces.append(("cpp2il_isil_assembly", [path for path in files if path.is_file()]))
    return surfaces


def _collect_doupotd_pvp_report_native_lua_bridge_boundary(
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    evidence_rows: list[dict[str, Any]] = []
    surface_rows: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "cpp2il_diffable_cs_file_count": 0,
        "cpp2il_isil_file_count": 0,
        "cpp2il_lua_singleton_surface_count": 0,
        "cpp2il_lua_singleton_method_hit_count": 0,
        "cpp2il_lua_table_lookup_hit_count": 0,
        "cpp2il_lua_function_call_hit_count": 0,
        "native_report_symbol_hit_count": 0,
        "native_doupotd_netlogic_symbol_hit_count": 0,
        "lua_engine_bridge_file_count": 0,
        "lua_engine_bridge_addsingleton_count": 0,
        "lua_engine_bridge_lifecycle_call_count": 0,
        "lua_engine_bridge_netlogic_mutation_count": 0,
    }
    report_terms = ("CM_DoupoTDReportFun", "CM_DoupoTDReport", "SM_DoupoTDReport", "CM_DoupoReport")
    lscript_root = root / "by_source" / "lscripts"

    def add_evidence(category: str, surface: str, path: Path, line_no: int | str, target: str, snippet: str) -> None:
        evidence_rows.append(
            {
                "category": category,
                "surface": surface,
                "source": str(path.relative_to(root)) if _is_relative_to(path, root) else str(path),
                "line": line_no,
                "target": target,
                "snippet": snippet[:360],
            }
        )

    for surface_name, files in _doupotd_cpp2il_assembly_surface_files(root):
        if surface_name == "cpp2il_diffable_cs_assembly":
            stats["cpp2il_diffable_cs_file_count"] += len(files)
        if surface_name == "cpp2il_isil_assembly":
            stats["cpp2il_isil_file_count"] += len(files)
        hit_count = 0
        for path in files:
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            text = "\n".join(lines)
            is_lua_singleton_surface = "LuaSingleton" in path.name or "LuaBeginAddSingleton" in text
            if is_lua_singleton_surface:
                stats["cpp2il_lua_singleton_surface_count"] += 1
                hit_count += 1
                add_evidence("cpp2il_lua_singleton_surface", surface_name, path, "", "LuaSingleton", path.name)
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if "LuaBeginAddSingleton" in stripped or "LuaEndAddSingleton" in stripped:
                    stats["cpp2il_lua_singleton_method_hit_count"] += 1
                    hit_count += 1
                    add_evidence("cpp2il_lua_singleton_method", surface_name, path, line_no, "LuaSingleton", stripped)
                if "LuaTable.get_Item" in stripped:
                    stats["cpp2il_lua_table_lookup_hit_count"] += 1
                    hit_count += 1
                    add_evidence("cpp2il_lua_table_lookup", surface_name, path, line_no, "LuaEngineBridge table lookup", stripped)
                if "LuaFunction.Call" in stripped:
                    stats["cpp2il_lua_function_call_hit_count"] += 1
                    hit_count += 1
                    add_evidence("cpp2il_lua_function_call", surface_name, path, line_no, "LuaEngineBridge function call", stripped)
                for term in report_terms:
                    if term in stripped:
                        stats["native_report_symbol_hit_count"] += 1
                        hit_count += 1
                        add_evidence("native_report_symbol_hit", surface_name, path, line_no, term, stripped)
                if "DoupoTDNetLogic" in stripped:
                    stats["native_doupotd_netlogic_symbol_hit_count"] += 1
                    hit_count += 1
                    add_evidence("native_doupotd_netlogic_symbol_hit", surface_name, path, line_no, "DoupoTDNetLogic", stripped)
        surface_rows.append(
            {
                "surface": surface_name,
                "file_count": len(files),
                "hit_count": hit_count,
            }
        )

    if lscript_root.is_dir():
        for bridge_path in sorted(lscript_root.glob("**/text_assets/LuaEngineBridge*.lua")):
            stats["lua_engine_bridge_file_count"] += 1
            try:
                lines = bridge_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if "function _M.AddSingleton" in stripped or "_sins[#_sins+1]=sin" in stripped:
                    stats["lua_engine_bridge_addsingleton_count"] += 1
                    add_evidence("lua_engine_bridge_addsingleton", "lua_text_asset", bridge_path, line_no, "AddSingleton", stripped)
                if re.search(r":(InitSingleton|UnInitSingleton|Update|LateUpdate|FixedUpdate|Destroy)\(", stripped):
                    stats["lua_engine_bridge_lifecycle_call_count"] += 1
                    add_evidence("lua_engine_bridge_lifecycle_call", "lua_text_asset", bridge_path, line_no, "singleton lifecycle", stripped)
                if re.search(r"NetLogic\s*=|CM_DoupoTDReportFun|CM_DoupoReport|rawset|__index|loadstring|load\(", stripped):
                    stats["lua_engine_bridge_netlogic_mutation_count"] += 1
                    add_evidence("lua_engine_bridge_netlogic_mutation", "lua_text_asset", bridge_path, line_no, "NetLogic/method mutation", stripped)

    return surface_rows, evidence_rows, stats


def _write_doupotd_pvp_report_native_lua_bridge_boundary_markdown(
    path: Path,
    *,
    stats: dict[str, Any],
    verdict: dict[str, Any],
    surface_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# DoupoTD PVP report native Lua bridge boundary",
        "",
        "This probe checks whether readable Cpp2IL Assembly-CSharp surfaces or the LuaEngineBridge singleton bridge expose a native/Lua bridge that could synthesize `CM_DoupoTDReportFun`.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Stats", ""])
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Surfaces", ""])
    for row in surface_rows:
        lines.append(f"- `{row.get('surface')}` files `{row.get('file_count')}` hits `{row.get('hit_count')}`")
    lines.extend(["", "## Evidence", ""])
    for row in evidence_rows[:120]:
        location = row.get("source") or ""
        if row.get("line"):
            location = f"{location}:{row.get('line')}"
        lines.append(
            f"- `{row.get('category')}` `{row.get('surface')}` `{location}` `{row.get('target')}` `{row.get('snippet')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This is still a readable static-surface boundary. It does not replace read-only Runtime evidence and cannot prove server acceptance behavior.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_doupotd_pvp_report_native_lua_bridge_boundary_probe(
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    output_dir = root / "parsed_configs" / "doupotd_catalog"
    surface_rows, evidence_rows, stats = _collect_doupotd_pvp_report_native_lua_bridge_boundary(root)
    verdict = {
        "cpp2il_assembly_surfaces_scanned": stats["cpp2il_diffable_cs_file_count"] > 0
        or stats["cpp2il_isil_file_count"] > 0,
        "cpp2il_lua_singleton_bridge_visible": stats["cpp2il_lua_singleton_surface_count"] > 0
        and stats["cpp2il_lua_singleton_method_hit_count"] > 0,
        "cpp2il_bridge_invokes_lua_functions": stats["cpp2il_lua_function_call_hit_count"] > 0,
        "lua_engine_bridge_only_tracks_lifecycle_singletons": stats["lua_engine_bridge_file_count"] > 0
        and stats["lua_engine_bridge_addsingleton_count"] > 0
        and stats["lua_engine_bridge_lifecycle_call_count"] > 0
        and stats["lua_engine_bridge_netlogic_mutation_count"] == 0,
        "no_native_exact_report_symbol_hits": stats["native_report_symbol_hit_count"] == 0,
        "no_native_doupotd_netlogic_symbol_hits": stats["native_doupotd_netlogic_symbol_hit_count"] == 0,
        "static_readable_bridge_boundary_only": True,
    }
    verdict["doupotd_pvp_report_native_lua_bridge_gap_confirmed"] = bool(
        verdict["cpp2il_assembly_surfaces_scanned"]
        and verdict["cpp2il_lua_singleton_bridge_visible"]
        and verdict["cpp2il_bridge_invokes_lua_functions"]
        and verdict["lua_engine_bridge_only_tracks_lifecycle_singletons"]
        and verdict["no_native_exact_report_symbol_hits"]
        and verdict["no_native_doupotd_netlogic_symbol_hits"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    surfaces_tsv = output_dir / "doupotd_pvp_report_native_lua_bridge_boundary_surfaces.tsv"
    evidence_tsv = output_dir / "doupotd_pvp_report_native_lua_bridge_boundary_evidence.tsv"
    report_path = output_dir / "doupotd_pvp_report_native_lua_bridge_boundary_report.md"
    json_path = output_dir / "doupotd_pvp_report_native_lua_bridge_boundary_report.json"
    _write_tsv(surfaces_tsv, surface_rows, ["surface", "file_count", "hit_count"])
    _write_tsv(evidence_tsv, evidence_rows, ["category", "surface", "source", "line", "target", "snippet"])
    _write_doupotd_pvp_report_native_lua_bridge_boundary_markdown(
        report_path,
        stats=stats,
        verdict=verdict,
        surface_rows=surface_rows,
        evidence_rows=evidence_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "confirmed": verdict["doupotd_pvp_report_native_lua_bridge_gap_confirmed"],
                "stats": stats,
                "verdict": verdict,
                "files": {
                    "surfaces": str(surfaces_tsv),
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
        "confirmed": verdict["doupotd_pvp_report_native_lua_bridge_gap_confirmed"],
        "output_dir": str(output_dir),
        "stats": stats,
        "verdict": verdict,
        "files": {
            "surfaces": str(surfaces_tsv),
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
