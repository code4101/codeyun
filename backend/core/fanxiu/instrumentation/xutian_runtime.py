from __future__ import annotations

"""Strict read-only projection of the already-loaded Xutian/Heaven model."""

from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)


_HEAVEN_METHODS = frozenset({"LuaHeavenMgr", "Inst_get"})
_DB_METHODS = frozenset({"DBMgr", "GetConfigTable", "GetConfigTableByIdWithLog", "Inst_get"})
_COUNT_CONFIG = "Heaven.HeavenCount"

# HeavenType.AutoKeyType from the current generated client.  Keep the raw
# numeric keys in evidence as well: the settings panel is only a view over
# HeavenData._AutoFightData, so Runtime is the authoritative switch state.
_AUTO_SETTING_KEYS = {
    3: "quality_3",
    4: "quality_4",
    5: "quality_5",
    6: "quality_6",
    7: "quality_7",
    8: "refill_challenge",
    9: "refill_explore",
    10: "challenge_count",
    14: "quick_auto",
    15: "quality_8",
    16: "skip_animation",
    99: "quality_player",
}


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value)


def _heaven_instance_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _HEAVEN_METHODS)
    instance = _fields(reader, manager.get("inst"))
    if not instance:
        raise FanxiuRuntimeMemoryError("虚天 Runtime Manager 尚未初始化")
    return instance


def _heaven_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    instance = _heaven_instance_fields(reader, root_address)
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("HeavenData"))
    if "_HeavenCountInfo" not in data:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 尚未初始化计数容器")
    return data


def _decode_counts(reader: LuaJitReader, data: dict[Any, Any]) -> dict[int, dict[str, int]]:
    rows = reader.dictionary_fields(data.get("_HeavenCountInfo"))
    result: dict[int, dict[str, int]] = {}
    for raw_key, raw_value in rows.items():
        row = _fields(reader, raw_value)
        item_type = as_int(row.get("type"))
        key_type = as_int(raw_key)
        count = as_int(row.get("count"))
        if item_type not in (1, 2) or count is None:
            continue
        if key_type != item_type:
            raise FanxiuRuntimeMemoryError(
                f"虚天计数字典键值身份冲突：key={key_type}, type={item_type}"
            )
        result[item_type] = {
            "type": item_type,
            "count": max(0, count),
            "recover_time": int(reader.long(row.get("recoverTime")) or 0),
        }
    missing = sorted({1, 2} - set(result))
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"虚天 Runtime 尚未同步完整探查/挑战计数：missing={missing}"
        )
    return result


def _decode_auto_settings(reader: LuaJitReader, data: dict[Any, Any]) -> dict[str, Any]:
    raw = data.get("_AutoFightData")
    if raw is None:
        raise FanxiuRuntimeMemoryError("虚天自动配置尚未初始化")
    rows = _fields(reader, raw)
    if hasattr(raw, "address"):
        table = reader.table(raw.address)
        for numeric_key, raw_value in enumerate(table.get("array", ())):
            if raw_value is not None:
                rows.setdefault(numeric_key, raw_value)
    if not rows:
        raise FanxiuRuntimeMemoryError("虚天自动配置为空")
    decoded: dict[str, Any] = {}
    raw_evidence: dict[str, Any] = {}
    for raw_key, raw_value in rows.items():
        key = as_int(raw_key)
        if key not in _AUTO_SETTING_KEYS:
            continue
        fields = _fields(reader, raw_value)
        auto_fight = fields.get("autoFight")
        if key == 10:
            count = as_int(auto_fight)
            if count is None or count < 1:
                raise FanxiuRuntimeMemoryError(
                    f"虚天自动挑战次数无效：{auto_fight!r}"
                )
            value: Any = count
        else:
            if not isinstance(auto_fight, bool):
                raise FanxiuRuntimeMemoryError(
                    f"虚天自动配置开关类型无效：key={key}, value={auto_fight!r}"
                )
            value = auto_fight
        name = _AUTO_SETTING_KEYS[key]
        decoded[name] = value
        raw_evidence[str(key)] = {
            "auto_fight": value,
            "use_item": fields.get("useItem"),
            "use_item_2": fields.get("useItem2"),
            "use_item_3": fields.get("useItem3"),
            "use_item_4": fields.get("useItem4"),
        }
    required = {
        "quality_3",
        "quality_4",
        "quality_5",
        "quality_6",
        "quality_7",
        "quality_8",
        "refill_challenge",
        "refill_explore",
        "challenge_count",
        "quick_auto",
        "skip_animation",
    }
    missing = sorted(required - set(decoded))
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"虚天自动配置尚未同步完整：missing={missing}"
        )
    return {"values": decoded, "raw": raw_evidence}


def _decode_available_quality_keys(
    reader: LuaJitReader,
    data: dict[Any, Any],
    *,
    current_heaven: int,
) -> list[int]:
    heaven_rows = reader.dictionary_fields(data.get("_AutoFightCfgDic"))
    current = heaven_rows.get(current_heaven) or heaven_rows.get(float(current_heaven))
    if current is None:
        raise FanxiuRuntimeMemoryError(
            f"虚天当前地图自动挑战配置尚未加载：heaven={current_heaven}"
        )
    raw_keys = reader.dictionary_fields(current)
    keys = sorted({as_int(key) for key in raw_keys if as_int(key) is not None})
    normalized = [15 if key == 8 else int(key) for key in keys if key in {3, 4, 5, 6, 7, 8, 99}]
    if not normalized:
        raise FanxiuRuntimeMemoryError("虚天当前地图没有可识别的自动挑战品质")
    return normalized


def _decode_special_options(
    reader: LuaJitReader,
    data: dict[Any, Any],
) -> dict[str, Any]:
    """Project the two lower special-item toggles from HeavenInfo.checks.

    ``HeavenAutoSettingView`` does not store these rows in ``_AutoFightData``;
    their read-only authority is ``HeavenInfo.checks:Contains(itemId)``.  A
    missing checks list is the client's own uninitialized-empty state.
    """

    info = _fields(reader, data.get("_HeavenInfo"))
    if not info:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 业务数据尚未加载")
    first_item = as_int(data.get("_HeightDetectItem"))
    second_item = as_int(data.get("_HeightDetectItem2"))
    if not first_item or not second_item:
        raise FanxiuRuntimeMemoryError("虚天特殊探查道具身份尚未加载")
    raw_checks = info.get("checks")
    values: list[Any] = []
    declared_count = 0
    if raw_checks is not None:
        values, declared_count = reader.list_items(raw_checks)
        if int(declared_count or 0) != len(values):
            raise FanxiuRuntimeMemoryError("虚天特殊探查道具选中列表读取竞态")
    selected_ids = {
        int(item_id)
        for value in values
        if (item_id := as_int(value)) is not None and int(item_id) > 0
    }
    return {
        "find_demon_item_id": first_item,
        "native_soul_lock_item_id": second_item,
        "find_demon_selected": first_item in selected_ids,
        "native_soul_lock_selected": second_item in selected_ids,
        "find_demon_available": bool(data.get("_CanShowUseSpecialItem")),
        "native_soul_lock_available": bool(data.get("_CanShowUseSpecialItem2")),
        "selected_item_ids": sorted(selected_ids),
        "declared_count": int(declared_count or 0),
    }


def _config_table(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _DB_METHODS)
    instance = _fields(reader, manager.get("inst"))
    configs = reader.dictionary_fields(instance.get("ConfigDic"))
    table = _fields(reader, configs.get(_COUNT_CONFIG))
    if not table:
        raise FanxiuRuntimeMemoryError(f"虚天配置尚未加载：{_COUNT_CONFIG}")
    return table


def _decode_count_configs(reader: LuaJitReader, root_address: int) -> dict[int, dict[str, int]]:
    result: dict[int, dict[str, int]] = {}
    for raw_key, raw_value in _config_table(reader, root_address).items():
        item_type = as_int(raw_key)
        if item_type not in (1, 2):
            continue
        fields = _fields(reader, raw_value)
        array: list[Any] = []
        if hasattr(raw_value, "address"):
            array = list(reader.table(raw_value.address).get("array", ()))
        # Generated HeavenCount rows are compact positional arrays in the
        # current client: id, itemId1, automatic, initial, interval,
        # description, itemId2, number.  Prefer named fields when present,
        # otherwise use those exact generated slots (1-based Lua indexing).
        supplement_item_id = as_int(
            fields.get("itemId2") if fields.get("itemId2") is not None
            else (array[7] if len(array) > 7 else None)
        )
        automatic = as_int(
            fields.get("automatic") if fields.get("automatic") is not None
            else (array[3] if len(array) > 3 else None)
        )
        if supplement_item_id is None or supplement_item_id <= 0:
            raise FanxiuRuntimeMemoryError(
                f"虚天计数配置缺少补充道具：type={item_type}"
            )
        if automatic is None or automatic <= 0:
            raise FanxiuRuntimeMemoryError(
                f"虚天计数配置补充数量无效：type={item_type}, automatic={automatic}"
            )
        result[item_type] = {
            "type": item_type,
            "supplement_item_id": supplement_item_id,
            "automatic": automatic,
        }
    if set(result) != {1, 2}:
        raise FanxiuRuntimeMemoryError("虚天探查/挑战配置行不完整")
    return result


def read_xutian_resource_snapshot() -> dict[str, Any]:
    """Read live counters, current map and supplement-item capacity."""

    memory = MumuProcessMemory.discover_cached()
    state_address = int(_lua_addresses(memory)["state"], 16)
    reader = LuaJitReader(memory)
    heaven_root, heaven_cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="xutian-resources",
        state_address=state_address,
        global_name="HeavenMgr",
        required_methods=_HEAVEN_METHODS,
        validate=lambda current_reader, address: _decode_counts(
            current_reader, _heaven_data_fields(current_reader, address)
        ),
    )
    db_root, db_cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="xutian-db-config",
        state_address=state_address,
        global_name="DBMgr",
        required_methods=_DB_METHODS,
        validate=lambda current_reader, address: _config_table(current_reader, address),
    )
    data = _heaven_data_fields(reader, heaven_root)
    instance = _heaven_instance_fields(reader, heaven_root)
    counts = _decode_counts(reader, data)
    auto_settings = _decode_auto_settings(reader, data)
    configs = _decode_count_configs(reader, db_root)
    info = _fields(reader, data.get("_HeavenInfo"))
    if not info:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 业务数据尚未加载")
    current_heaven = as_int(info.get("heaven"))
    if current_heaven is None or current_heaven < 0:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 当前地图字段无效")
    available_quality_keys = _decode_available_quality_keys(
        reader,
        data,
        current_heaven=current_heaven,
    )
    item_ids = {row["supplement_item_id"] for row in configs.values()}
    item_counts, backpack_evidence = read_backpack_item_counts(
        item_ids, manager_key="xutian-resource-budget"
    )
    explore_item = configs[1]
    challenge_item = configs[2]
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "runtime_memory",
        "read_only": True,
        "explore": counts[1],
        "challenge": counts[2],
        "current_heaven": current_heaven,
        "detect_num": max(0, as_int(info.get("detectNum")) or 0),
        "multiple_enabled": bool(info.get("multiple")),
        "quick_check_enabled": bool(info.get("quickCheck")),
        "auto_progress": {
            "running": bool(instance.get("_quickAutoStart")),
            "completed_challenges": max(0, as_int(data.get("_AutoFightCount")) or 0),
        },
        "auto_settings": auto_settings["values"],
        "special_options": _decode_special_options(reader, data),
        "available_quality_keys": available_quality_keys,
        "count_configs": configs,
        "supplement_item_counts": item_counts,
        "capacity": {
            "explore_without_items": counts[1]["count"],
            "explore_with_items": counts[1]["count"] + item_counts.get(explore_item["supplement_item_id"], 0) * explore_item["automatic"],
            "challenge_without_items": counts[2]["count"],
            "challenge_with_items": counts[2]["count"] + item_counts.get(challenge_item["supplement_item_id"], 0) * challenge_item["automatic"],
        },
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "heaven_root": f"0x{heaven_root:x}",
            "heaven_root_cache_hit": heaven_cache_hit,
            "db_root": f"0x{db_root:x}",
            "db_root_cache_hit": db_cache_hit,
            "backpack": backpack_evidence,
            "auto_settings_raw": auto_settings["raw"],
        },
    }


def read_xutian_auto_settings_snapshot() -> dict[str, Any]:
    """Read only the live auto panel model for bounded click verification.

    Unlike :func:`read_xutian_resource_snapshot`, this deliberately avoids the
    DB config and backpack walks.  A settings reconciler calls it before and
    after each GUI action, so keeping this projection narrow is both faster and
    less exposed to unrelated Lua table replacement.
    """

    memory = MumuProcessMemory.discover_cached()
    state_address = int(_lua_addresses(memory)["state"], 16)
    reader = LuaJitReader(memory)
    heaven_root, heaven_cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="xutian-auto-settings",
        state_address=state_address,
        global_name="HeavenMgr",
        required_methods=_HEAVEN_METHODS,
        validate=lambda current_reader, address: _decode_auto_settings(
            current_reader, _heaven_data_fields(current_reader, address)
        ),
    )
    instance = _heaven_instance_fields(reader, heaven_root)
    data = _heaven_data_fields(reader, heaven_root)
    info = _fields(reader, data.get("_HeavenInfo"))
    if not info:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 业务数据尚未加载")
    current_heaven = as_int(info.get("heaven"))
    if current_heaven is None or current_heaven < 0:
        raise FanxiuRuntimeMemoryError("虚天 Runtime 当前地图字段无效")
    auto_settings = _decode_auto_settings(reader, data)
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "runtime_memory",
        "read_only": True,
        "current_heaven": current_heaven,
        "auto_progress": {
            "running": bool(instance.get("_quickAutoStart")),
            "completed_challenges": max(
                0, as_int(data.get("_AutoFightCount")) or 0
            ),
        },
        "auto_settings": auto_settings["values"],
        "special_options": _decode_special_options(reader, data),
        "available_quality_keys": _decode_available_quality_keys(
            reader,
            data,
            current_heaven=current_heaven,
        ),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "heaven_root": f"0x{heaven_root:x}",
            "heaven_root_cache_hit": heaven_cache_hit,
            "auto_settings_raw": auto_settings["raw"],
            "projection": "auto_settings_only",
        },
    }


__all__ = [
    "read_xutian_auto_settings_snapshot",
    "read_xutian_resource_snapshot",
]
