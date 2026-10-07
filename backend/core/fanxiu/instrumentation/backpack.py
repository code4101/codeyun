from __future__ import annotations

"""Strictly read item counts from the game's already-loaded backpack model."""

from collections.abc import Iterable
from typing import Any
import time
import hashlib
import json
from datetime import datetime

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_manager_root,
    resolve_lua_global_manager_root,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses, FanxiuRedbagRuntimeLoadError


_BACKPACK_MARKER = b"LuaBackpackMgr"
_BACKPACK_METHODS = frozenset({"LuaBackpackMgr", "Inst_get"})


def read_equipment_gem_inventory_snapshot(*, allow_discovery: bool = False) -> dict[str, Any]:
    """Read the loaded spirit-ring inventory, without opening equipment UI.

    Native BackpackData.UpdateEquipMap stores ItemType.EquipItem (48),
    SubType_EquipGem (38) in _EquipGemItemDic. GetEquipGemItemDic returns that
    dictionary. Decode only its current value table and validate every item
    against loaded item metadata; an absent dictionary is not an empty bag.
    """
    from .item_config import read_item_metadata_runtime
    started = time.time()
    memory = (MumuProcessMemory.discover() if allow_discovery
              else MumuProcessMemory.discover_cached(fallback_to_discovery=False))
    reader = LuaJitReader(memory)
    root, cache_hit, environment = resolve_lua_global_manager_root(
        memory, manager_key="equipment-gem-global",
        state_address=int(_lua_addresses(memory)["state"], 16),
        global_name="BackpackMgr", required_methods=frozenset({"Inst_get"}),
        validate=_backpack_data_fields,
    )
    data = _backpack_data_fields(reader, root)
    dictionary = reader.fields(data.get("_EquipGemItemDic"))
    # The current native Lua Dictionary has LuaDic_count and _dt_; the older
    # ItemVoDic per-base wrapper uses _valueTable_. They are distinct schemas.
    values_ref = dictionary.get("_dt_")
    if values_ref is None:
        raise FanxiuRuntimeMemoryError(
            "灵环库存字典结构未验收；dictionary_keys="
            + repr(list(dictionary)[:20]) + ";related_data_keys="
            + repr([key for key in data if isinstance(key, str)
                    and any(word in key.lower() for word in ("gem", "equip", "ring"))]))
    values = reader.fields(values_ref)
    declared_count = as_int(dictionary.get("LuaDic_count"))
    if declared_count is None or declared_count != len(values):
        raise FanxiuRuntimeMemoryError("灵环字典声明数量与完整条目数不一致")
    if len(values) > 10000:
        raise FanxiuRuntimeMemoryError("灵环库存条目数超出边界")
    items, seen = [], set()
    for raw in values.values():
        fields = reader.fields(raw)
        instance_id = reader.long(fields.get("id"))
        base_id, quantity = as_int(fields.get("baseId")), as_int(fields.get("num"))
        if (instance_id is None or instance_id <= 0 or instance_id in seen
                or base_id is None or base_id <= 0 or quantity is None or quantity <= 0):
            raise FanxiuRuntimeMemoryError("灵环库存实例、数量无效或重复")
        seen.add(instance_id)
        items.append({"instance_id": str(instance_id), "base_id": base_id, "num": quantity})
    metadata = read_item_metadata_runtime({row["base_id"] for row in items}) if items else None
    if metadata is not None:
        if (metadata.get("complete") is not True or metadata.get("pid") != memory.pid
                or metadata.get("process_start_ticks") != memory.process_start_ticks):
            raise FanxiuRuntimeMemoryError("灵环配置与库存进程身份失配或不完整")
        for item in items:
            config = metadata["items_by_id"].get(item["base_id"],
                     metadata["items_by_id"].get(str(item["base_id"]), {}))
            if config.get("item_type_id") != 48 or config.get("item_sub_type_id") != 38:
                raise FanxiuRuntimeMemoryError("独立灵环库存包含未核实类型：" + repr(config))
    return {"complete": True, "source": "BackpackMgr.Model.BackpackData._EquipGemItemDic",
            "captured_at": datetime.now().astimezone().isoformat(), "items": items,
            "fingerprint": hashlib.sha256(json.dumps(sorted(items,
                key=lambda item: item["instance_id"]), sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            "metadata": metadata, "evidence": {"pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks, "read_only": True,
            "backpack_root": hex(root), "root_cache_hit": cache_hit,
            "lua_environment_address": environment, "started_at": started}}


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
    force_refresh: bool = False,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Aggregate selected base-item counts without invoking game-side methods.

    ``force_refresh=True`` diagnostically resolves the current Lua global rather
    than a cached manager root and includes the requested items' instance evidence.
    It does not scan for a legacy marker if that resolution fails: such a fallback
    would not prove current-global identity. Normal requests retain their existing
    discovery policy. Compare observations from the same process; a discrepancy
    alone does not prove a stale root rather than a concurrent inventory change.
    """

    started = time.time()
    requested_ids = {int(item_id) for item_id in item_ids}
    counts = {item_id: 0 for item_id in requested_ids}
    memory = MumuProcessMemory.discover_cached()
    reader = LuaJitReader(memory)
    try:
        # Validate the cached/loaded global first. Recent clients omit the old
        # LuaBackpackMgr marker; scanning for it before every inventory read
        # defeats the healthy global cache and can take minutes per batch.
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key=f"{manager_key}-global",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="BackpackMgr",
            required_methods=frozenset({"Inst_get"}),
            validate=_backpack_data_fields,
            force_refresh=force_refresh,
        )
        discovery = "loaded_global"
    except (FanxiuRuntimeMemoryError, FanxiuRedbagRuntimeLoadError):
        if force_refresh:
            raise
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key=manager_key,
            marker=_BACKPACK_MARKER,
            required_methods=_BACKPACK_METHODS,
            validate=_backpack_data_fields,
        )
        discovery = "marker"
    index_ref = _backpack_data_fields(reader, root).get("ItemVoDic")
    item_index = _fields(reader, index_ref)
    instance_evidence = []
    dictionary_evidence = []
    for raw_base_id, raw_dictionary in item_index.items():
        base_id = as_int(raw_base_id)
        if base_id not in counts:
            continue
        values_ref = _fields(reader, raw_dictionary).get("_valueTable_")
        values = _fields(reader, values_ref)
        if force_refresh:
            dictionary_evidence.append({
                "base_id": base_id,
                "dictionary_address": getattr(raw_dictionary, "address", None),
                "values_address": getattr(values_ref, "address", None),
                "decoded_entry_count": len(values),
            })
        for raw_item in values.values():
            item = _fields(reader, raw_item)
            if as_int(item.get("baseId")) == base_id:
                counts[base_id] += max(0, as_int(item.get("num")) or 0)
                if force_refresh:
                    uid = reader.long(item.get("id"))
                    instance_evidence.append({
                        "base_id": base_id, "item_id": str(uid) if uid is not None else None,
                        "num": as_int(item.get("num")),
                        "item_address": getattr(raw_item, "address", None),
                    })
    return counts, {
        "pid": memory.pid,
        "process_start_ticks": memory.process_start_ticks,
        "backpack_root": f"0x{root:x}",
        "backpack_root_cache_hit": cache_hit,
        "discovery": discovery,
        "read_only": True,
        "source": "BackpackMgr.Model.BackpackData.ItemVoDic",
        "force_refresh": force_refresh,
        "observed_at": started,
        "completed_at": time.time(),
        **({"lua_environment_address": _environment,
            "item_index_address": getattr(index_ref, "address", None),
            "requested_item_ids": sorted(requested_ids),
            "dictionaries": dictionary_evidence,
            "instances": instance_evidence} if force_refresh else {}),
    }
