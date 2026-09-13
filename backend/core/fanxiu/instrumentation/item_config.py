from __future__ import annotations

"""Read already-loaded item definitions from the live client DBMgr.

The exported item catalog can lag behind server hot updates.  This reader does
not call game methods or initialize config tables; it only decodes the
``Item.Item`` table that the running client has already loaded.
"""

from collections.abc import Iterable, Mapping
import threading
import time
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
    unresolved_cached_names = any(
        not row.get("item_name") and row.get("runtime_name_id")
        for row in cached.values()
    )
    if not missing and not unresolved_cached_names:
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
        "descript",
        "effDescript",
        "effectValue",
        "type",
        "subType",
        "quality",
        "icon",
        "smallIcon",
        "useCondition",
        "breakObtain",
    )
    raw_rows = reader.numeric_fields(table.address, frozenset(missing)) if missing else {}
    for raw_key, raw in raw_rows.items():
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
            "runtime_description_id": as_int(values.get("descript")),
            "runtime_effect_description_id": as_int(values.get("effDescript")),
            "effect_value": values.get("effectValue") if isinstance(values.get("effectValue"), (str, int, float)) else None,
            "runtime_field_indexes": {field: indexes.get(field) for field in fields},
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
    # Item.Item stores localization keys after hot updates. Resolve all absent
    # names in one read using the same process/context as the metadata. Cached
    # nameless rows are retried: LangTable may load later in this process.
    name_ids = {
        int(row["runtime_name_id"]) for row in rows.values()
        if not row.get("item_name") and row.get("runtime_name_id")
    }
    name_error = ""
    unresolved_name_ids = sorted(name_ids)
    if name_ids:
        try:
            localization = read_loaded_item_text(
                name_ids, reader=reader, state_address=state_address,
                environment_address=environment,
            )
            texts = localization["texts_by_id"]
            for row in rows.values():
                name = texts.get(row.get("runtime_name_id"))
                if not row.get("item_name") and isinstance(name, str) and name.strip():
                    row.update(item_name=name.strip(), name_source="runtime_localization")
            unresolved_name_ids = sorted({
                int(row["runtime_name_id"]) for row in rows.values()
                if not row.get("item_name") and row.get("runtime_name_id")
            })
        except FanxiuRuntimeMemoryError as exc:
            # Missing localization must not erase already proven type facts,
            # nor invent a display name that would bypass unknown-item policy.
            name_error = str(exc)
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
        "unresolved_name_ids": unresolved_name_ids,
        "name_resolution_error": name_error,
        "read_only": True,
    }


def read_item_metadata_runtime(item_ids: Iterable[int], *, force: bool = False) -> dict[str, Any]:
    """一次批量读取已自然加载的物品类型；无需调用方管理内存或UI地址。

    不打开物品、不调用Lua、不初始化配置。complete=False与missing_ids保留
    未知事实；类型自选也仅证明箱子类型，具体稳定奖励仍需独立核实。
    返回进程身份、观测时间和每个base ID的metadata，缓存随进程变更失效。
    """
    from .ui_runtime_context import acquire_ui_runtime_context

    wanted = tuple(item_ids)
    if not wanted or any(type(value) is not int or value <= 0 for value in wanted):
        raise ValueError('需要至少一个正整数物品base ID')
    if force:
        with _ITEM_METADATA_CACHE_LOCK:
            for item_id in wanted:
                _ITEM_METADATA_CACHE.pop(item_id, None)
    started = time.perf_counter()
    context = acquire_ui_runtime_context(())
    rows, diagnostics = read_loaded_item_metadata(
        wanted, memory=context.memory, reader=context.reader,
        state_address=context.binding.state_address,
    )
    return {**diagnostics, 'items_by_id': rows,
            'pid': context.memory.pid,
            'process_start_ticks': context.memory.process_start_ticks,
            'captured_at': time.time(), 'read_only': True,
            'elapsed_seconds': round(time.perf_counter()-started, 4)}


def read_loaded_item_text(
    text_ids: Iterable[int], *, reader: LuaJitReader,
    state_address: int, environment_address: int,
) -> dict[str, Any]:
    """Read loaded LangTable text in the caller's existing Runtime context."""
    wanted = frozenset(text_ids)
    if any(type(value) is not int or value <= 0 for value in wanted):
        raise ValueError('需要正整数文本ID')
    if not wanted:
        return {'texts_by_id': {}, 'missing_ids': [], 'complete': True,
                'source': 'LuaLocalization.LangTable'}
    raw = reader.state_string_field(environment_address,
        'LuaLocalization', state_address=state_address)
    module = table_ref(raw)
    table = table_ref(reader.state_string_field(module.address, 'LangTable',
        state_address=state_address)) if module is not None else None
    if table is None:
        raise FanxiuRuntimeMemoryError('LuaLocalization.LangTable尚未自然加载')
    values = reader.numeric_fields(table.address, wanted)
    texts = {key: value for key, value in values.items() if isinstance(value, str)}
    return {'texts_by_id': texts, 'missing_ids': sorted(wanted-texts.keys()),
            'complete': wanted <= texts.keys(), 'source': 'LuaLocalization.LangTable'}


def read_item_text_runtime(text_ids: Iterable[int]) -> dict[str, Any]:
    """批量读取LuaLocalization.LangTable；不调用Text/GetLan或加载语言表。

    正式Core.UI.LuaLocalization.GetLan(key)直接返回_M.LangTable[key]；
    LangTable由客户端已加载语言模块提供。表缺失明确失败，单项缺失保留missing。
    """
    from .ui_runtime_context import acquire_ui_runtime_context
    wanted = tuple(text_ids)
    if not wanted or any(type(value) is not int or value <= 0 for value in wanted):
        raise ValueError('需要正整数文本ID')
    started = time.perf_counter()
    context = acquire_ui_runtime_context(())
    result = read_loaded_item_text(wanted, reader=context.reader,
        state_address=context.binding.state_address,
        environment_address=context.binding.environment_address)
    return {**result,
            'pid': context.memory.pid, 'process_start_ticks': context.memory.process_start_ticks,
            'captured_at': time.time(), 'read_only': True,
            'elapsed_seconds': round(time.perf_counter()-started, 4)}


__all__ = ["read_loaded_item_metadata", "read_loaded_item_text", "read_item_metadata_runtime", "read_item_text_runtime"]
