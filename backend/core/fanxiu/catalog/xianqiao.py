from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

from backend.core.fanxiu.catalog.item import _find_attribute_lua, _format_single_attr_text
from backend.core.fanxiu.catalog.lua_config import (
    _find_default_lang_path,
    load_fanxiu_lang_map,
    parse_fanxiu_generated_lua_config,
)
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root


QUALITY_NAMES = {
    3: "尚品",
    4: "珍品",
    5: "绝品",
    6: "仙品",
}
CACHE_VERSION = 5


class FanxiuXianqiaoCatalogError(RuntimeError):
    pass


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _plain(row: dict[str, Any], field: str) -> str:
    value = row.get(f"{field}_plain", row.get(field, ""))
    if value is None:
        return ""
    text = re.sub(r"<[^>]+>", "", str(value)).strip()
    return text.replace("拾层", "十层").replace("|", "；")


def _compact_element_purpose(summary: str) -> str:
    marker = "在战斗中可大幅提高角色"
    marker_index = summary.rfind(marker)
    if marker_index >= 0:
        return summary[marker_index + len(marker) :].strip(" ，,。")
    return summary


def _latest_text_asset_dir(root: Path, prefix: str) -> Path:
    candidates = [
        path
        for path in root.glob(f"by_source/lscripts/generate/cfg/{prefix}_*/text_assets")
        if path.is_dir()
    ]
    if not candidates:
        raise FanxiuXianqiaoCatalogError(f"缺少 {prefix} 配置导出")
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _parse_tables(root: Path, directory: Path, names: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    lang_path = _find_default_lang_path(root)
    lang_map = load_fanxiu_lang_map(lang_path) if lang_path else None
    tables: dict[str, list[dict[str, Any]]] = {}
    for name in names:
        path = directory / f"{name}.lua"
        if not path.is_file():
            raise FanxiuXianqiaoCatalogError(f"缺少仙窍配置表：{name}")
        tables[name] = [
            row
            for row in parse_fanxiu_generated_lua_config(
                path,
                lang_path=lang_path,
                lang_map=lang_map,
            ).get("rows")
            or []
            if isinstance(row, dict)
        ]
    return tables


def _config_values(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(row.get("id") or row.get("_row_key") or ""): str(row.get("value") or row.get("content") or "")
        for row in rows
        if row.get("id") or row.get("_row_key")
    }


def _quality_summaries(
    ware_rows: list[dict[str, Any]],
    level_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    levels_by_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in level_rows:
        levels_by_item[_as_int(row.get("itemId"))].append(row)

    result: list[dict[str, Any]] = []
    for quality in sorted({_as_int(row.get("quality")) for row in ware_rows}):
        representative = next((row for row in ware_rows if _as_int(row.get("quality")) == quality), None)
        if not representative:
            continue
        item_id = _as_int(representative.get("itemId"))
        levels = sorted(levels_by_item.get(item_id, []), key=lambda row: _as_int(row.get("level")))
        element_unlocks = [
            _as_int(row.get("level"))
            for row in levels
            if _as_int(row.get("unlockElement")) > 0
        ]
        side_attr_unlocks = [
            _as_int(row.get("level"))
            for row in levels
            if _as_int(row.get("randomSideAttr")) > 0
        ]
        element_limit = _as_int(representative.get("elementNumLimit"))
        unlocked_by_upgrade = sum(_as_int(row.get("unlockElement")) for row in levels)
        result.append(
            {
                "quality": quality,
                "name": QUALITY_NAMES.get(quality, f"品质{quality}"),
                "max_level": max((_as_int(row.get("level")) for row in levels), default=0),
                "element_slots": element_limit,
                "initial_element_slots": max(0, element_limit - unlocked_by_upgrade),
                "element_unlock_levels": element_unlocks,
                # Live read-only verification found one initial side attribute
                # on every one of 1,524 unupgraded ware instances.
                "initial_side_attributes": 1,
                "side_attribute_unlock_levels": side_attr_unlocks,
                "base_feed_exp": _as_int(representative.get("exp")),
                "invested_exp_return_rate": _as_int(representative.get("expOff")) / 10000,
                "total_upgrade_exp": sum(_as_int(row.get("consumeExp")) for row in levels),
            }
        )
    return result


def _build_mechanics_from_rows(
    *,
    core: dict[str, list[dict[str, Any]]],
    trial: dict[str, list[dict[str, Any]]],
    attribute_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    maps = core["CoreMap"]
    bases = core["CoreBase"]
    core_levels = core["CoreLevel"]
    ware_rows = core["CoreWareBase"]
    ware_levels = core["CoreWareLevel"]
    element_bases = core["CoreWareElementBase"]
    elements = core["CoreWareElement"]
    side_attrs = core["CoreWareSideAttrBank"]
    core_values = _config_values(core["ConfigValue"])
    trial_values = _config_values(trial["ConfigValue"])

    attr_meta = {
        str(row.get("id")): {
            "name": _plain(row, "name") or str(row.get("id") or ""),
            "group": row.get("group"),
        }
        for row in attribute_rows
        if row.get("id") not in (None, "")
    }

    bases_by_type_part = {
        (_as_int(row.get("type")), _as_int(row.get("parts"))): row
        for row in bases
        if 1 <= _as_int(row.get("type")) <= 6
    }
    levels_by_type_part: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in core_levels:
        levels_by_type_part[(_as_int(row.get("type")), _as_int(row.get("parts")))].append(row)
    ware_by_type_part: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in ware_rows:
        ware_by_type_part[(_as_int(row.get("type")), _as_int(row.get("parts")))].append(row)
    ware_levels_by_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in ware_levels:
        ware_levels_by_item[_as_int(row.get("itemId"))].append(row)

    element_effects: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in elements:
        element_effects[(_as_int(row.get("type")), _as_int(row.get("element")))].append(row)
    element_base_by_id = {_as_int(row.get("element")): row for row in element_bases}

    systems: list[dict[str, Any]] = []
    for map_row in sorted(maps, key=lambda row: _as_int(row.get("type"))):
        system_id = _as_int(map_row.get("type") or map_row.get("id"))
        parts: list[dict[str, Any]] = []
        for part_id in range(1, 7):
            base = bases_by_type_part.get((system_id, part_id), {})
            levels = sorted(
                levels_by_type_part.get((system_id, part_id), []),
                key=lambda row: _as_int(row.get("level")),
            )
            cumulative_exp = 0
            grade_checkpoints: list[dict[str, Any]] = []
            for row in levels:
                cumulative_exp += _as_int(row.get("consumeExp"))
                level = _as_int(row.get("level"))
                if level > 0 and level % 10 == 0:
                    grade_checkpoints.append(
                        {
                            "grade": level // 10,
                            "level": level,
                            "cumulative_exp": cumulative_exp,
                        }
                    )

            representative_ware = min(
                ware_by_type_part.get((system_id, part_id), []),
                key=lambda row: _as_int(row.get("quality"), 999),
                default={},
            )
            main_attr_key = str(representative_ware.get("initialMainAttr") or "")
            representative_levels = sorted(
                ware_levels_by_item.get(_as_int(representative_ware.get("itemId")), []),
                key=lambda row: _as_int(row.get("level")),
            )
            parts.append(
                {
                    "id": part_id,
                    "name": _plain(base, "title") or _plain(base, "name") or f"部位{part_id}",
                    "unlock_text": _plain(base, "unlockDes"),
                    "max_level": max((_as_int(row.get("level")) for row in levels), default=0),
                    "total_exp": sum(_as_int(row.get("consumeExp")) for row in levels),
                    "grade_checkpoints": grade_checkpoints,
                    "core_attributes": levels[-1].get("attr", {}) if levels else {},
                    "ware_main_attribute": {
                        "key": main_attr_key,
                        "name": (attr_meta.get(main_attr_key) or {}).get("name", main_attr_key),
                        "initial_text": _format_single_attr_text(
                            main_attr_key,
                            representative_levels[0].get("addMainAttr") if representative_levels else None,
                            attr_meta,
                        ),
                        "max_text": _format_single_attr_text(
                            main_attr_key,
                            representative_levels[-1].get("addMainAttr") if representative_levels else None,
                            attr_meta,
                        ),
                    },
                }
            )

        system_elements: list[dict[str, Any]] = []
        for element_id, base in sorted(element_base_by_id.items(), key=lambda item: _as_int(item[1].get("sort"))):
            levels = sorted(
                element_effects.get((system_id, element_id), []),
                key=lambda row: _as_int(row.get("level")),
            )
            if not levels:
                continue
            summary = _plain(base, "des")
            system_elements.append(
                {
                    "id": element_id,
                    "name": _plain(base, "name"),
                    "summary": summary,
                    "purpose": _compact_element_purpose(summary),
                    "levels": [
                        {
                            "level": _as_int(row.get("level")),
                            "required_count": _as_int(row.get("elementNum")),
                            "effect": _plain(row, "effectTxt"),
                        }
                        for row in levels
                    ],
                }
            )

        systems.append(
            {
                "id": system_id,
                "name": _plain(map_row, "name"),
                "unlock_condition": str(map_row.get("condition") or ""),
                "unlock_text": _plain(map_row, "desc"),
                "parts": parts,
                "elements": system_elements,
            }
        )

    trial_bases = sorted(trial["CoreWareTrialBase"], key=lambda row: _as_int(row.get("sort")))
    buff_groups = sorted(trial["CoreWareTrialBuffGroup"], key=lambda row: _as_int(row.get("id")))
    buff_levels_by_group: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in trial["CoreWareTrialBuffLevel"]:
        buff_levels_by_group[_as_int(row.get("buffGroupId"))].append(row)
    rewards_by_dungeon: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in trial["CoreWareTrialReward"]:
        rewards_by_dungeon[_as_int(row.get("dungeonId"))].append(row)

    trial_modes = []
    for row in trial_bases:
        dungeon_id = _as_int(row.get("dungeonId"))
        rewards = rewards_by_dungeon.get(dungeon_id, [])
        trial_modes.append(
            {
                "id": _as_int(row.get("id")),
                "system_id": _as_int(row.get("coreMapId")),
                "group": "常规试炼" if _as_int(row.get("type")) == 1 else "限时试炼",
                "enemy": str(row.get("levelName") or ""),
                "unlock_text": _plain(row, "unlockDes"),
                "difficulty_min": min((_as_int(item.get("totalPoint")) for item in rewards), default=None),
                "difficulty_max": max((_as_int(item.get("totalPoint")) for item in rewards), default=None),
                "reward_tier_count": len(rewards),
            }
        )

    trial_buffs = []
    for row in buff_groups:
        group_id = _as_int(row.get("id"))
        levels = buff_levels_by_group.get(group_id, [])
        trial_buffs.append(
            {
                "id": group_id,
                "kind": "周天增益" if _as_int(row.get("type")) == 1 else "难度选项",
                "selection": {1: "指定元素", 2: "逐级选择", 3: "增益等级"}.get(
                    _as_int(row.get("selectType")),
                    "选项",
                ),
                "description": _plain(row, "des"),
                "max_level": max((_as_int(item.get("level")) for item in levels), default=0),
                "max_point": max((_as_int(item.get("point")) for item in levels), default=0),
            }
        )

    level_point_values = [
        _as_int(value)
        for value in trial_values.get("COREMAP_ELEMENT_LEVEL_POINT", "").split(",")
        if value.strip()
    ]
    return {
        "systems": systems,
        "qualities": _quality_summaries(ware_rows, ware_levels),
        "trial": {
            "daily_reward_times": _as_int(trial_values.get("DAILY_REWARD_TIMES")),
            "extra_time_cost": _as_int(trial_values.get("REWARD_TIMES_WAY")),
            "extra_time_item_id": _as_int(trial_values.get("REWARD_TIMES_GOODS")),
            "weekly_level_points": level_point_values,
            "default_weekly_points": _as_int(trial_values.get("COREMAP_ELEMENT_DEFAULT_POINT")),
            "modes": trial_modes,
            "buffs": trial_buffs,
        },
        "rules": {
            "part_count_per_system": 6,
            "core_max_level": 50,
            "core_grade_interval": 10,
            "element_level_thresholds": [3, 6, 9, 12],
            "feed_exp_return_rate": _as_int(next(iter(ware_rows), {}).get("expOff")) / 10000,
            "attribute_display_multiplier": _as_int(core_values.get("ATTR_UP_VALUE"), 1),
            "bag_limit": _as_int(core_values.get("COREWARE_BAG_NUMBER_LIMIT")),
        },
        "counts": {
            "systems": len(systems),
            "parts": sum(len(system["parts"]) for system in systems),
            "ware_templates": len(ware_rows),
            "ware_levels": len(ware_levels),
            "element_effects": len(elements),
            "side_attribute_entries": len(side_attrs),
            "trial_reward_tiers": len(trial["CoreWareTrialReward"]),
        },
    }


@lru_cache(maxsize=2)
def _build_cached(export_root: str, fingerprint: tuple[int, ...]) -> dict[str, Any]:
    del fingerprint
    root = Path(export_root)
    core_dir = _latest_text_asset_dir(root, "core")
    trial_dir = _latest_text_asset_dir(root, "corewaretrial")
    core = _parse_tables(
        root,
        core_dir,
        (
            "ConfigValue",
            "CoreBase",
            "CoreLevel",
            "CoreMap",
            "CoreWareBase",
            "CoreWareElement",
            "CoreWareElementBase",
            "CoreWareLevel",
            "CoreWareSideAttrBank",
        ),
    )
    trial = _parse_tables(
        root,
        trial_dir,
        (
            "ConfigValue",
            "CoreWareTrialBase",
            "CoreWareTrialBuffGroup",
            "CoreWareTrialBuffLevel",
            "CoreWareTrialReward",
        ),
    )
    attribute_path = _find_attribute_lua(root, "Attribute.lua")
    attribute_rows = (
        _parse_tables(root, attribute_path.parent, ("Attribute",))["Attribute"]
        if attribute_path is not None
        else []
    )
    return _build_mechanics_from_rows(core=core, trial=trial, attribute_rows=attribute_rows)


def build_fanxiu_xianqiao_mechanics(*, export_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    core_dir = _latest_text_asset_dir(root, "core")
    trial_dir = _latest_text_asset_dir(root, "corewaretrial")
    paths = sorted(core_dir.glob("*.lua")) + sorted(trial_dir.glob("*.lua"))
    fingerprint = tuple(path.stat().st_mtime_ns for path in paths)
    cache_path = root / "parsed_configs" / "xianqiao_catalog" / "mechanics.json"
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                cached.get("cache_version") == CACHE_VERSION
                and cached.get("source_fingerprint") == list(fingerprint)
                and isinstance(cached.get("mechanics"), dict)
            ):
                return cached["mechanics"]
        except (OSError, ValueError, TypeError):
            pass

    mechanics = _build_cached(str(root), fingerprint)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "cache_version": CACHE_VERSION,
                "source_fingerprint": list(fingerprint),
                "mechanics": mechanics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return mechanics


__all__ = [
    "FanxiuXianqiaoCatalogError",
    "build_fanxiu_xianqiao_mechanics",
]
