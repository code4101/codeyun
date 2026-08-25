from __future__ import annotations

"""Strictly read item counts from the game's already-loaded backpack model."""

from collections.abc import Iterable
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_manager_root,
    resolve_lua_global_manager_root,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses


_BACKPACK_MARKER = b"LuaBackpackMgr"
_BACKPACK_METHODS = frozenset({"LuaBackpackMgr", "Inst_get"})


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value)


def _backpack_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _BACKPACK_METHODS)
    data = _fields(
        reader,
        _fields(reader, _fields(reader, manager.get("inst")).get("Model")).get(
            "BackpackData"
        ),
    )
    if not _fields(reader, data.get("ItemVoDic")):
        raise FanxiuRuntimeMemoryError("背包物品索引尚未加载")
    return data


def read_backpack_item_counts(
    item_ids: Iterable[int],
    *,
    manager_key: str,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Aggregate selected base-item counts without invoking game-side methods."""

    requested_ids = {int(item_id) for item_id in item_ids}
    counts = {item_id: 0 for item_id in requested_ids}
    memory = MumuProcessMemory.discover_cached()
    reader = LuaJitReader(memory)
    try:
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key=manager_key,
            marker=_BACKPACK_MARKER,
            required_methods=_BACKPACK_METHODS,
            validate=_backpack_data_fields,
        )
        discovery = "marker"
    except FanxiuRuntimeMemoryError:
        # Recent clients no longer retain the historical ``LuaBackpackMgr``
        # marker even though the already-loaded global manager is healthy.
        # Resolving that global is still strict read-only: it only walks the
        # existing Lua state and never calls Inst_get or initializes a model.
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key=f"{manager_key}-global",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="BackpackMgr",
            required_methods=frozenset({"Inst_get"}),
            validate=_backpack_data_fields,
        )
        discovery = "loaded_global"
    item_index = _fields(reader, _backpack_data_fields(reader, root).get("ItemVoDic"))
    for raw_base_id, raw_dictionary in item_index.items():
        base_id = as_int(raw_base_id)
        if base_id not in counts:
            continue
        values = _fields(reader, _fields(reader, raw_dictionary).get("_valueTable_"))
        for raw_item in values.values():
            item = _fields(reader, raw_item)
            if as_int(item.get("baseId")) == base_id:
                counts[base_id] += max(0, as_int(item.get("num")) or 0)
    return counts, {
        "pid": memory.pid,
        "process_start_ticks": memory.process_start_ticks,
        "backpack_root": f"0x{root:x}",
        "backpack_root_cache_hit": cache_hit,
        "discovery": discovery,
        "read_only": True,
    }
