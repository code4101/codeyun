from __future__ import annotations

"""Read already-loaded item definitions from the live client DBMgr.

The exported item catalog can lag behind server hot updates.  This reader does
not call game methods or initialize config tables; it only decodes the
``Item.Item`` table that the running client has already loaded.
"""

from collections.abc import Iterable, Mapping
import threading
from typing import Any

from backend.core.fanxiu.catalog.item import ITEM_TYPE_LABELS
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    table_ref,
)


_DB_METHODS = frozenset(
    {"DBMgr", "GetConfigTable", "GetConfigTableByIdWithLog", "Inst_get"}
)
_ITEM_TABLE_NAME = "Item.Item"
_ITEM_METADATA_CACHE_LOCK = threading.Lock()
_ITEM_METADATA_CACHE_IDENTITY: tuple[int, int] | None = None
_ITEM_METADATA_CACHE: dict[int, dict[str, Any]] = {}


def _item_config_table(reader: LuaJitReader, root_address: int) -> LuaRef:
    manager = manager_index_fields(reader, root_address, _DB_METHODS)
    instance = reader.fields(manager.get("inst"))
    configs_wrapper = reader.fields(instance.get("ConfigDic"))
    configs = table_ref(configs_wrapper.get("_dt_"))
    table = (
        table_ref(reader.string_fields(configs.address, frozenset({_ITEM_TABLE_NAME})).get(_ITEM_TABLE_NAME))
        if configs is not None
        else None
    )
    if table is None:
        raise FanxiuRuntimeMemoryError("DBMgr.ConfigDic['Item.Item'] 尚未自然加载")
    return table


def _item_config_indexes(
    reader: LuaJitReader,
    environment_address: int,
    state_address: int,
) -> dict[str, int]:
    root = table_ref(
        reader.state_string_field(
            environment_address,
            "s_globalCfgIdx",
            state_address=state_address,
        )
    )
    group = table_ref(
        reader.state_string_field(root.address, "Item", state_address=state_address)
    ) if root is not None else None
    indexes_ref = table_ref(
        reader.state_string_field(group.address, "Item", state_address=state_address)
    ) if group is not None else None
    indexes = reader.fields(indexes_ref)
    result = {
        str(key): int(index)
        for key, index in indexes.items()
        if isinstance(key, str) and as_int(index) is not None
    }
    if not result:
        raise FanxiuRuntimeMemoryError("s_globalCfgIdx['Item']['Item'] 尚未加载")
    return result


def _packed_value(
    reader: LuaJitReader,
    raw: Any,
    indexes: Mapping[str, int],
    field: str,
) -> Any:
    direct = reader.fields(raw)
    if direct.get(field) is not None:
        return direct[field]
    if not isinstance(raw, LuaRef) or raw.kind != "table":
        return None
    index = indexes.get(field)
    array = list(reader.table(raw.address).get("array") or ())
    return array[index] if index is not None and 0 <= index < len(array) else None


def _policy_resolution(item_type: int | None, sub_type: int | None, use_condition: str) -> str:
    """Return a narrow policy-safe class when display localization is absent.

    Subtype 49 is the client's activity-material bucket.  Requiring an explicit
    ActivitybaseId condition keeps this fallback narrower than the ordinary
    prayer resources retained by the mail policy.
    """

    if item_type == 23:
        return "faze"
    if item_type == 5 and sub_type == 49 and "ActivitybaseId|" in use_condition:
        return "temporary_activity_material"
    return ""


def read_loaded_item_metadata(
    item_ids: Iterable[int],
    *,
    memory: MumuProcessMemory,
    reader: LuaJitReader,
    state_address: int,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Resolve selected item ids from the authoritative live ``Item.Item`` table."""

    wanted = {int(item_id) for item_id in item_ids}
    if not wanted:
        return {}, {"complete": True, "source": "runtime_config", "resolved_count": 0}
    identity = (int(memory.pid), int(memory.process_start_ticks))
    global _ITEM_METADATA_CACHE_IDENTITY
    with _ITEM_METADATA_CACHE_LOCK:
        if _ITEM_METADATA_CACHE_IDENTITY != identity:
            _ITEM_METADATA_CACHE.clear()
            _ITEM_METADATA_CACHE_IDENTITY = identity
        cached = {
            item_id: dict(_ITEM_METADATA_CACHE[item_id])
            for item_id in wanted
            if item_id in _ITEM_METADATA_CACHE
        }
    missing = wanted - cached.keys()
    if not missing:
        return cached, {
            "complete": True,
            "source": "DBMgr.ConfigDic[Item.Item]",
            "requested_count": len(wanted),
            "resolved_count": len(cached),
            "missing_ids": [],
            "metadata_cache_hit_count": len(cached),
            "read_only": True,
        }
    root, cache_hit, environment = resolve_lua_global_manager_root(
        memory,
        manager_key="mail-item-config",
        state_address=state_address,
        global_name="DBMgr",
        required_methods=_DB_METHODS,
        validate=_item_config_table,
    )
    table = _item_config_table(reader, root)
    indexes = _item_config_indexes(reader, environment, state_address)
    rows: dict[int, dict[str, Any]] = dict(cached)
    fields = (
        "id",
        "name",
        "type",
        "subType",
        "quality",
        "icon",
        "smallIcon",
        "useCondition",
        "breakObtain",
    )
    for raw_key, raw in reader.numeric_fields(table.address, frozenset(missing)).items():
        item_id = as_int(_packed_value(reader, raw, indexes, "id")) or as_int(raw_key)
        if item_id is None:
            continue
        values = {field: _packed_value(reader, raw, indexes, field) for field in fields}
        item_type = as_int(values.get("type"))
        sub_type = as_int(values.get("subType"))
        raw_name = values.get("name")
        use_condition = str(values.get("useCondition") or "")
        rows[item_id] = {
            "item_id": str(item_id),
            "item_name": raw_name.strip() if isinstance(raw_name, str) else "",
            "runtime_name_id": as_int(raw_name),
            "item_type": ITEM_TYPE_LABELS.get(str(item_type), f"道具类型#{item_type}" if item_type is not None else ""),
            "item_type_id": item_type,
            "item_sub_type_id": sub_type,
            "quality": as_int(values.get("quality")),
            "icon": str(values.get("icon") or ""),
            "small_icon": str(values.get("smallIcon") or ""),
            "use_condition": use_condition,
            "break_obtain": str(values.get("breakObtain") or ""),
            "item_resolved": True,
            "policy_resolution": _policy_resolution(item_type, sub_type, use_condition),
            "name_source": "runtime_config",
        }
    with _ITEM_METADATA_CACHE_LOCK:
        if _ITEM_METADATA_CACHE_IDENTITY == identity:
            _ITEM_METADATA_CACHE.update(
                {item_id: dict(row) for item_id, row in rows.items()}
            )
    return rows, {
        "complete": len(rows) == len(wanted),
        "source": "DBMgr.ConfigDic[Item.Item]",
        "requested_count": len(wanted),
        "resolved_count": len(rows),
        "missing_ids": sorted(wanted - rows.keys()),
        "metadata_cache_hit_count": len(cached),
        "manager_cache_hit": bool(cache_hit),
        "read_only": True,
    }


__all__ = ["read_loaded_item_metadata"]
