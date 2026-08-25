from __future__ import annotations

"""Read the player's talisman hall from the live LuaJIT model.

This module is intentionally strict read-only.  It reads the already loaded
``TalismanMgr`` model and its generated-config caches; it never initializes a
manager, executes Lua, sends a game command, or installs a hook.
"""

import re
import time
from datetime import datetime
from typing import Any, Iterable

from backend.core.fanxiu.catalog.lua_config import (
    _find_default_lang_path,
    load_fanxiu_lang_map,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)


_TALISMAN_METHODS = frozenset({"LuaTalismanMgr", "Inst_get", "OpenUpgradeView"})
_SECTION_BY_CATEGORY = {
    "法宝": "fabao",
    "先天古宝": "xiantiangubao",
    "后天古宝": "houtiangubao",
}
_BAG_TYPE_LABELS = {1: "攻击", 2: "辅助", 3: "防御", 4: "灵力"}
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_COLOR_TAG_RE = re.compile(
    r"<color=(#[0-9A-Fa-f]{3,8})>(.*?)</color>",
    re.DOTALL,
)
_ROUTINE_UPGRADE_ATTRIBUTE_NAMES = (
    "攻击",
    "灵力",
    "气血",
    "气血上限",
    "守御",
    "灵力恢复",
    "功法增伤",
    "功法减伤",
    "攻击加成",
    "灵力加成",
    "气血加成",
    "攻击资质",
    "灵力资质",
    "气血资质",
)
_ROUTINE_UPGRADE_NUMBER_RE = r"(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?:万|亿)?%?"
_ROUTINE_UPGRADE_EFFECT_RE = re.compile(
    rf"^(?:{'|'.join(map(re.escape, _ROUTINE_UPGRADE_ATTRIBUTE_NAMES))})"
    rf"\s*\+\s*{_ROUTINE_UPGRADE_NUMBER_RE}$"
)
_ROUTINE_PERMANENT_EFFECT_RE = re.compile(
    rf"^角色永久增加(?:攻击|灵力|气血|气血上限)加成\s*"
    rf"{_ROUTINE_UPGRADE_NUMBER_RE}$"
)


def _is_routine_upgrade_effect(description: str) -> bool:
    text = str(description or "").strip()
    return bool(
        _ROUTINE_UPGRADE_EFFECT_RE.fullmatch(text)
        or _ROUTINE_PERMANENT_EFFECT_RE.fullmatch(text)
    )


_RICH_COLOR_ROLES = {
    "#864c00": "skill",
    "#2a4b10": "value",
    "#73123a": "quality",
    "#9e1e09": "attribute",
}


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value)


def _table_items(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    """Decode a plain table or the project's Dictionary ``_dt_`` wrapper."""

    if not isinstance(value, LuaRef) or value.kind != "table":
        return {}
    outer = reader.table(value.address)
    fields = dict(outer.get("fields") or {})
    dt = fields.get("_dt_") or fields.get("_valueTable_")
    if isinstance(dt, LuaRef) and dt.kind == "table":
        outer = reader.table(dt.address)
        fields = dict(outer.get("fields") or {})
    result = dict(fields)
    for index, item in enumerate(outer.get("array") or ()):  # Lua arrays retain index 0 as None.
        if item is not None:
            result.setdefault(index, item)
    return result


def _list_items(reader: LuaJitReader, value: Any) -> list[Any]:
    items, _declared_count = reader.list_items(value)
    if items:
        return list(items)
    if isinstance(value, LuaRef) and value.kind == "table":
        return [item for item in reader.table(value.address).get("array") or () if item is not None]
    return []


def _config_indexes(
    reader: LuaJitReader,
    environment_address: int,
    table_name: str,
) -> dict[str, int]:
    environment = reader.string_fields(
        environment_address,
        frozenset({"s_globalCfgIdx"}),
    )
    root = environment.get("s_globalCfgIdx")
    talisman_group = _fields(reader, root).get("Talisman")
    raw_indexes = _fields(reader, talisman_group).get(table_name)
    return {
        str(key): int(index)
        for key, index in _fields(reader, raw_indexes).items()
        if isinstance(key, str) and as_int(index) is not None
    }


def _config_row(
    reader: LuaJitReader,
    value: Any,
    indexes: dict[str, int],
) -> dict[str, Any]:
    if not isinstance(value, LuaRef) or value.kind != "table":
        return {}
    direct = _fields(reader, value)
    array = list(reader.table(value.address).get("array") or ())
    row: dict[str, Any] = {}
    for field, index in indexes.items():
        current = direct.get(field)
        if current is None and 0 <= index < len(array):
            current = array[index]
        row[field] = current
    return row


def _format_localized_template(template: str, params: list[Any]) -> str:
    result = str(template or "")
    for size in range(len(params), -1, -1):
        try:
            result = result % tuple(params[:size])
            break
        except (TypeError, ValueError):
            continue
    for index, value in enumerate(params):
        result = result.replace("{" + str(index) + "}", str(value))
    return result.replace("%%", "%")


def _localized_argument(
    value: Any,
    *,
    reader: LuaJitReader,
    lang_map: dict[int, str],
    depth: int,
) -> Any:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        numeric = float(value)
        return int(numeric) if numeric.is_integer() else numeric
    if isinstance(value, str):
        return value
    return _localized_text(value, reader=reader, lang_map=lang_map, depth=depth + 1)


def _localized_text(
    value: Any,
    *,
    reader: LuaJitReader,
    lang_map: dict[int, str],
    depth: int = 0,
) -> str:
    if depth > 3 or value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    numeric = as_int(value)
    if numeric is not None:
        return str(lang_map.get(numeric) or "").strip()
    if not isinstance(value, LuaRef) or value.kind != "table":
        return ""

    table = reader.table(value.address)
    sequence = [item for item in table.get("array") or () if item is not None]
    if sequence:
        lang_id = as_int(sequence[0])
        if lang_id is not None and lang_id in lang_map:
            params = [
                _localized_argument(
                    item,
                    reader=reader,
                    lang_map=lang_map,
                    depth=depth,
                )
                for item in sequence[1:]
            ]
            return _format_localized_template(lang_map[lang_id], params).strip()

    candidates: list[Any] = []
    fields = dict(table.get("fields") or {})
    for key in ("key", "id", "langId", "languageId", "text", "value"):
        if key in fields:
            candidates.append(fields[key])
    candidates.extend(sequence)
    candidates.extend(
        item
        for key, item in fields.items()
        if key not in {"key", "id", "langId", "languageId", "text", "value"}
    )
    texts = [
        _localized_text(item, reader=reader, lang_map=lang_map, depth=depth + 1)
        for item in candidates
    ]
    texts = [text for text in texts if text]
    if not texts:
        return ""
    template = next((text for text in texts if "{" in text or "%s" in text), texts[0])
    params = [text for text in texts if text != template]
    return _format_localized_template(template, params).strip()


def _plain_text(value: str) -> str:
    return _HTML_TAG_RE.sub("", str(value or "")).replace("\r", "").strip()


def _rich_text_segments(value: str) -> list[dict[str, str]]:
    """Convert game color tags into safe, renderable text segments."""

    source = str(value or "").replace("\r", "")
    segments: list[dict[str, str]] = []
    cursor = 0
    for match in _COLOR_TAG_RE.finditer(source):
        if match.start() > cursor:
            text = _HTML_TAG_RE.sub("", source[cursor:match.start()])
            if text:
                segments.append({"text": text, "color": "", "role": ""})
        color = match.group(1).lower()
        text = _HTML_TAG_RE.sub("", match.group(2))
        if text:
            segments.append(
                {
                    "text": text,
                    "color": color,
                    "role": _RICH_COLOR_ROLES.get(color, "accent"),
                }
            )
        cursor = match.end()
    if cursor < len(source):
        text = _HTML_TAG_RE.sub("", source[cursor:])
        if text:
            segments.append({"text": text, "color": "", "role": ""})
    return segments


def _talisman_data_fields(reader: LuaJitReader, root: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root, _TALISMAN_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("TalismanData"))
    fight_data = _fields(reader, data.get("_TalismanFightData"))
    items, declared_count = reader.list_items(fight_data.get("talismanVos"))
    if not items or (declared_count is not None and int(declared_count) != len(items)):
        raise FanxiuRuntimeMemoryError(
            f"法宝清单尚未完整加载：count={declared_count}, rows={len(items)}"
        )
    return data


def _owned_talisman_rows(reader: LuaJitReader, data: dict[Any, Any]) -> list[dict[str, Any]]:
    fight_data = _fields(reader, data.get("_TalismanFightData"))
    items, declared_count = reader.list_items(fight_data.get("talismanVos"))
    rows: list[dict[str, Any]] = []
    for value in items:
        fields = _fields(reader, value)
        talisman_id = as_int(fields.get("baseId"))
        if talisman_id is None:
            raise FanxiuRuntimeMemoryError("法宝 Runtime 条目缺少 baseId")
        rows.append(
            {
                "talisman_id": talisman_id,
                "stage": as_int(fields.get("stage")) or 0,
                "wujing_level": as_int(fields.get("wujingLevel")) or 0,
                "mix_level": as_int(fields.get("mixLevel")) or 0,
                "bind_id": as_int(fields.get("bindId")) or 0,
                "num": as_int(fields.get("num")) or 0,
            }
        )
    if declared_count is not None and int(declared_count) != len(rows):
        raise FanxiuRuntimeMemoryError("法宝 Runtime 清单数量不一致")
    return rows


def _complete_talisman_rows(
    owned_rows: Iterable[dict[str, Any]],
    talisman_configs: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Overlay player state on the full loaded talisman configuration set."""

    owned_by_id = {
        int(row["talisman_id"]): dict(row)
        for row in owned_rows
        if as_int(row.get("talisman_id")) is not None
    }
    all_ids = sorted(set(talisman_configs) | set(owned_by_id))
    return [
        {
            "talisman_id": talisman_id,
            "stage": 0,
            "wujing_level": 0,
            "mix_level": 0,
            "bind_id": 0,
            "num": 0,
            **owned_by_id.get(talisman_id, {}),
            "owned": talisman_id in owned_by_id,
        }
        for talisman_id in all_ids
    ]


def _all_talisman_configs(
    reader: LuaJitReader,
    data: dict[Any, Any],
    indexes: dict[str, int],
    wanted_ids: set[int] | None = None,
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for raw_list in _table_items(reader, data.get("_AllTalismanDic")).values():
        for raw_row in _list_items(reader, raw_list):
            row = _config_row(reader, raw_row, indexes)
            talisman_id = as_int(row.get("id"))
            if talisman_id is not None and (wanted_ids is None or talisman_id in wanted_ids):
                result[talisman_id] = row
    return result


def _nested_config_rows(
    reader: LuaJitReader,
    root: Any,
    indexes: dict[str, int],
    wanted_ids: set[int] | None = None,
) -> dict[int, dict[int, dict[str, Any]]]:
    result: dict[int, dict[int, dict[str, Any]]] = {}
    for raw_id, raw_levels in _table_items(reader, root).items():
        talisman_id = as_int(raw_id)
        if talisman_id is None or (wanted_ids is not None and talisman_id not in wanted_ids):
            continue
        level_rows: dict[int, dict[str, Any]] = {}
        for raw_level, raw_row in _table_items(reader, raw_levels).items():
            row = _config_row(reader, raw_row, indexes)
            level = as_int(row.get("level")) or as_int(row.get("stage")) or as_int(raw_level)
            if level is not None:
                level_rows[level] = row
        result[talisman_id] = level_rows
    return result


def _selected_nested_config_rows(
    reader: LuaJitReader,
    root: Any,
    indexes: dict[str, int],
    target_levels: dict[int, int],
) -> dict[int, dict[int, dict[str, Any]]]:
    """Read one requested level per owned talisman instead of expanding the cache."""

    result: dict[int, dict[int, dict[str, Any]]] = {}
    for raw_id, raw_levels in _table_items(reader, root).items():
        talisman_id = as_int(raw_id)
        if talisman_id is None or talisman_id not in target_levels:
            continue
        target_level = int(target_levels[talisman_id])
        for raw_level, raw_row in _table_items(reader, raw_levels).items():
            if as_int(raw_level) != target_level:
                continue
            result[talisman_id] = {
                target_level: _config_row(reader, raw_row, indexes)
            }
            break
    return result


def _selected_nested_config_rows_multi(
    reader: LuaJitReader,
    root: Any,
    indexes: dict[str, int],
    target_levels: dict[int, set[int]],
) -> dict[int, dict[int, dict[str, Any]]]:
    """Read the base, current and key-point rows needed by the Shenlian table."""

    result: dict[int, dict[int, dict[str, Any]]] = {}
    for raw_id, raw_levels in _table_items(reader, root).items():
        talisman_id = as_int(raw_id)
        wanted_levels = target_levels.get(talisman_id or -1)
        if talisman_id is None or not wanted_levels:
            continue
        rows: dict[int, dict[str, Any]] = {}
        for raw_level, raw_row in _table_items(reader, raw_levels).items():
            level = as_int(raw_level)
            if level is None or level not in wanted_levels:
                continue
            rows[level] = _config_row(reader, raw_row, indexes)
        result[talisman_id] = rows
    return result


def _key_point_rows(
    reader: LuaJitReader,
    root: Any,
    indexes: dict[str, int],
    wanted_ids: set[int] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for raw_id, raw_list in _table_items(reader, root).items():
        talisman_id = as_int(raw_id)
        if talisman_id is None or (wanted_ids is not None and talisman_id not in wanted_ids):
            continue
        rows = [_config_row(reader, value, indexes) for value in _list_items(reader, raw_list)]
        result[talisman_id] = sorted(
            (row for row in rows if as_int(row.get("level")) is not None),
            key=lambda row: as_int(row.get("level")) or 0,
        )
    return result


def _break_nodes(
    reader: LuaJitReader,
    root: Any,
    wanted_ids: set[int] | None = None,
) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    for raw_id, raw_levels in _table_items(reader, root).items():
        talisman_id = as_int(raw_id)
        if talisman_id is None or (wanted_ids is not None and talisman_id not in wanted_ids):
            continue
        result[talisman_id] = sorted(
            level
            for value in _list_items(reader, raw_levels)
            if (level := as_int(value)) is not None
        )
    return result


def _row_text(
    row: dict[str, Any],
    field: str,
    *,
    reader: LuaJitReader,
    lang_map: dict[int, str],
) -> str:
    return _plain_text(_localized_text(row.get(field), reader=reader, lang_map=lang_map))


def _row_rich_text(
    row: dict[str, Any],
    field: str,
    *,
    reader: LuaJitReader,
    lang_map: dict[int, str],
) -> tuple[str, list[dict[str, str]]]:
    rich_text = _localized_text(row.get(field), reader=reader, lang_map=lang_map)
    return _plain_text(rich_text), _rich_text_segments(rich_text)


def _build_projection(
    *,
    owned_rows: Iterable[dict[str, Any]],
    talisman_configs: dict[int, dict[str, Any]],
    grade_rows: dict[int, dict[int, dict[str, Any]]],
    pin_rows: dict[int, dict[int, dict[str, Any]]],
    key_points: dict[int, list[dict[str, Any]]],
    break_nodes: dict[int, list[int]],
    reader: LuaJitReader,
    lang_map: dict[int, str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for owned in owned_rows:
        talisman_id = int(owned["talisman_id"])
        stage = int(owned.get("stage") or 0)
        wujing_level = int(owned.get("wujing_level") or 0)
        config = talisman_configs.get(talisman_id) or {}
        if not config:
            raise FanxiuRuntimeMemoryError(f"法宝 {talisman_id} 的基础配置缓存尚未加载")
        resolved_name = _row_text(
            config,
            "name",
            reader=reader,
            lang_map=lang_map,
        )
        # The stable talisman id and live progression fields are authoritative
        # for selection and ranking.  A newly shipped localization id can be
        # absent from the current exported language table without making those
        # business facts incomplete.  Preserve the gap explicitly instead of
        # aborting the whole hall projection and preventing an unconfigured
        # optional reward from ever being selected.
        name = resolved_name or f"法宝 #{talisman_id}"
        talisman_grade_rows = grade_rows.get(talisman_id) or {}
        baseline_grade = (
            talisman_grade_rows[min(talisman_grade_rows)]
            if talisman_grade_rows
            else {}
        )
        current_grade = talisman_grade_rows.get(stage) or baseline_grade
        levels = pin_rows.get(talisman_id) or {}
        current_pin_row = levels.get(wujing_level) or {}
        points = key_points.get(talisman_id) or []
        active_points = [row for row in points if (as_int(row.get("level")) or 0) <= wujing_level]
        current_pin = as_int(current_pin_row.get("pin"))
        if current_pin is None:
            current_pin = len(active_points)
        talisman_type = as_int(config.get("talismanType")) or 0
        if talisman_type == 1:
            category = "先天古宝"
        elif current_pin > 0:
            category = "后天古宝"
        else:
            category = "法宝"

        nodes = break_nodes.get(talisman_id) or []
        last_break = max((level for level in nodes if level <= wujing_level), default=0)
        next_break = min((level for level in nodes if level > wujing_level), default=0)
        progress_nodes = max(0, wujing_level - last_break - (1 if last_break else 0))
        remaining_nodes = max(0, next_break - wujing_level) if next_break else 0
        shenlian_effect, shenlian_effect_segments = _row_rich_text(
            current_pin_row, "baseSkillDes", reader=reader, lang_map=lang_map
        )
        shenlian_schedule, shenlian_schedule_segments = _row_rich_text(
            current_pin_row, "scheduleDes", reader=reader, lang_map=lang_map
        )
        gradients: list[dict[str, Any]] = []
        activation_row = levels.get(1) or {}
        if activation_row:
            activation_description, activation_segments = _row_rich_text(
                activation_row, "baseSkillDes", reader=reader, lang_map=lang_map
            )
            activation_schedule, activation_schedule_segments = _row_rich_text(
                activation_row, "scheduleDes", reader=reader, lang_map=lang_map
            )
            gradients.append(
                {
                    "pin": 0,
                    "level": 1,
                    "pin_label": "激活",
                    "unlock_label": "首次神炼激活",
                    "skill_name": "",
                    "summary_description": activation_description,
                    "summary_segments": activation_segments,
                    "effect_description": "",
                    "effect_segments": [],
                    "schedule_description": activation_schedule,
                    "schedule_segments": activation_schedule_segments,
                    "active": wujing_level >= 1,
                    "current": current_pin == 0 and wujing_level >= 1,
                }
            )
        for point in points:
            point_level = as_int(point.get("level")) or 0
            pin = as_int(point.get("pin")) or len(gradients)
            point_pin_row = current_pin_row if pin == current_pin else levels.get(point_level) or {}
            summary_description, summary_segments = _row_rich_text(
                point_pin_row, "baseSkillDes", reader=reader, lang_map=lang_map
            )
            schedule_description, schedule_segments = _row_rich_text(
                point_pin_row, "scheduleDes", reader=reader, lang_map=lang_map
            )
            effect_description, effect_segments = _row_rich_text(
                point, "keyPointDes", reader=reader, lang_map=lang_map
            )
            gradients.append(
                {
                    "pin": pin,
                    "level": point_level,
                    "pin_label": _row_text(point, "pinHanzi", reader=reader, lang_map=lang_map),
                    "unlock_label": _row_text(point, "keyPoint", reader=reader, lang_map=lang_map),
                    "skill_name": _row_text(point, "skillName", reader=reader, lang_map=lang_map),
                    "summary_description": summary_description,
                    "summary_segments": summary_segments,
                    "effect_description": effect_description,
                    "effect_segments": effect_segments,
                    "schedule_description": schedule_description,
                    "schedule_segments": schedule_segments,
                    "active": point_level <= wujing_level,
                    "current": pin == current_pin,
                }
            )
        next_gradient = next((item for item in gradients if item["level"] > 0 and not item["active"]), None)
        if next_gradient and int(next_gradient["level"]) == 1:
            remaining_nodes = 1
        upgrade_effects: list[dict[str, Any]] = []
        for effect_stage, effect_row in sorted(talisman_grade_rows.items()):
            description, segments = _row_rich_text(
                effect_row, "descript", reader=reader, lang_map=lang_map
            )
            is_near_current = abs(effect_stage - stage) <= 5
            if description and (
                is_near_current or not _is_routine_upgrade_effect(description)
            ):
                upgrade_effects.append(
                    {
                        "stage": effect_stage,
                        "description": description,
                        "segments": segments,
                        "unlocked": effect_stage <= stage,
                        "current": effect_stage == stage,
                    }
                )
        original_effect = "\n".join(item["description"] for item in upgrade_effects)
        if not original_effect:
            original_effect = _row_text(config, "descript", reader=reader, lang_map=lang_map)
        quality = as_int(current_pin_row.get("quality"))
        if quality is None or quality <= 0:
            quality = as_int(current_grade.get("quality"))
        result.append(
            {
                **owned,
                "id": f"talisman-{talisman_id}",
                "name": name,
                "name_resolved": bool(resolved_name),
                "rank": stage,
                "shenlian": wujing_level,
                "category": category,
                "section_key": _SECTION_BY_CATEGORY[category],
                "type": _BAG_TYPE_LABELS.get(as_int(config.get("type")) or 0, ""),
                "quality": quality,
                "original_effect": original_effect,
                "upgrade_effects": upgrade_effects,
                "shenlian_effect": shenlian_effect,
                "shenlian_effect_segments": shenlian_effect_segments,
                "shenlian_schedule": shenlian_schedule,
                "shenlian_schedule_segments": shenlian_schedule_segments,
                "shenlian_pin": current_pin,
                "shenlian_pin_label": (
                    _row_text(current_pin_row, "pinHanzi", reader=reader, lang_map=lang_map)
                    or ("激活" if wujing_level > 0 else "未激活")
                ),
                "shenlian_progress_nodes": progress_nodes,
                "shenlian_remaining_nodes": remaining_nodes,
                "shenlian_next_pin": int(next_gradient["pin"]) if next_gradient else 0,
                "shenlian_next_level": int(next_gradient["level"]) if next_gradient else 0,
                "shenlian_next_label": (
                    str(next_gradient["skill_name"] or next_gradient["unlock_label"])
                    if next_gradient
                    else ""
                ),
                "shenlian_next_skill_name": str(next_gradient["skill_name"]) if next_gradient else "",
                "shenlian_max_pin": max((int(item["pin"]) for item in gradients), default=0),
                "shenlian_gradients": gradients,
            }
        )
    return sorted(
        result,
        key=lambda item: (
            -int(bool(item.get("owned"))),
            -int(item.get("quality") or 0),
            -int(item.get("rank") or 0),
            -int(item.get("wujing_level") or 0),
            item["name"],
        ),
    )


def read_magic_treasure_hall_runtime() -> dict[str, Any]:
    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        root, cache_hit, environment_address = resolve_lua_global_manager_root(
            memory,
            manager_key="magic-treasure-talisman",
            state_address=state_address,
            global_name="TalismanMgr",
            required_methods=_TALISMAN_METHODS,
            validate=_talisman_data_fields,
        )
        reader = LuaJitReader(memory)
        data = _talisman_data_fields(reader, root)
        indexes = {
            name: _config_indexes(reader, environment_address, name)
            for name in ("Talisman", "TalismanGrade", "TalismanPin")
        }
        if not all(indexes.values()):
            raise FanxiuRuntimeMemoryError("法宝生成配置字段索引尚未加载")
        lang_path = _find_default_lang_path()
        if lang_path is None:
            raise FanxiuRuntimeMemoryError("未找到当前导出版本的语言表")
        lang_map = load_fanxiu_lang_map(lang_path)
        owned = _owned_talisman_rows(reader, data)
        # The live list is the authority for the player's current collection.
        # Reading every grade of every configured (including unowned) talisman
        # made an ordinary refresh spend minutes expanding `_TalismanGradeCfg`.
        # Keep the runtime read bounded to owned ids; the collector overlays
        # these changing facts on the durable hall catalogue.
        owned_ids = {int(item["talisman_id"]) for item in owned}
        talisman_configs = _all_talisman_configs(
            reader, data, indexes["Talisman"], owned_ids
        )
        projection_rows = _complete_talisman_rows(owned, talisman_configs)
        wanted_ids = owned_ids
        key_points = _key_point_rows(
            reader, data.get("_TalismanKeyPointDesDic"), indexes["TalismanPin"], wanted_ids
        )
        pin_target_levels = {
            int(item["talisman_id"]): {
                0,
                1,
                int(item["wujing_level"]),
                *(
                    as_int(point.get("level")) or 0
                    for point in key_points.get(int(item["talisman_id"]), [])
                ),
            }
            for item in projection_rows
        }
        items = _build_projection(
            owned_rows=projection_rows,
            talisman_configs=talisman_configs,
            grade_rows=_selected_nested_config_rows(
                reader,
                data.get("_TalismanGradeCfg"),
                indexes["TalismanGrade"],
                {
                    int(item["talisman_id"]): int(item.get("stage") or 0)
                    for item in projection_rows
                },
            ),
            pin_rows=_selected_nested_config_rows_multi(
                reader,
                data.get("_TalismanWuJingDic"),
                indexes["TalismanPin"],
                pin_target_levels,
            ),
            key_points=key_points,
            break_nodes=_break_nodes(reader, data.get("_BreakNodeDic"), wanted_ids),
            reader=reader,
            lang_map=lang_map,
        )
        if len(items) != len(talisman_configs):
            raise FanxiuRuntimeMemoryError(
                f"法宝投影不完整：configs={len(talisman_configs)}, projected={len(items)}"
            )
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory+version_pinned_localization",
            "items": items,
            "item_count": len(items),
            "owned_only": True,
            "effects_complete": False,
            "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "captured_timestamp": time.time(),
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "talisman_root_cache_hit": cache_hit,
                "lang_path": str(lang_path),
                "owned_talisman_count": len(owned),
                "configured_talisman_count": len(talisman_configs),
                "loaded_owned_talisman_config_count": len(talisman_configs),
            },
            "elapsed_seconds": time.perf_counter() - started_at,
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": str(exc) if isinstance(exc, FanxiuRuntimeMemoryError) else f"{type(exc).__name__}: {exc}",
            "items": [],
            "item_count": 0,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }
