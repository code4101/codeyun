from __future__ import annotations

"""Read the already-loaded ImmHole (仙窍) model from process memory.

The game itself computes 五行周天 from ``CoreMainDic[type]`` and counts only
``coreEquipVO.wear`` items.  Keep that same boundary here: inventory items and
other仙窍体系 must not silently affect the trial drop-element decision.
"""

import time
from datetime import datetime
from typing import Any

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


_XIANQIAO_METHODS = frozenset({"Inst_get", "OpenWareTrialEnterView"})
XIANQIAO_ELEMENT_NAMES = {1: "金", 2: "木", 3: "水", 4: "火", 5: "土"}
XIANQIAO_DESIRED_ELEMENT_IDS = (1, 3, 4)


def _object_fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    fields: dict[Any, Any] = {}
    seen: set[int] = set()
    current = value
    while isinstance(current, LuaRef) and current.kind == "table":
        if current.address in seen:
            break
        seen.add(current.address)
        current_fields = reader.fields(current)
        fields = {**current_fields, **fields}
        current = current_fields.get("_super")
    return fields


def _table_entries(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    """Return both Lua hash fields and positive integer array entries."""

    if not isinstance(value, LuaRef) or value.kind != "table":
        return {}
    table = reader.table(value.address)
    entries = dict(table["fields"])
    entries.update(
        (index, item)
        for index, item in enumerate(table["array"][1:], start=1)
        if item is not None
    )
    return entries


def _xianqiao_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager_fields = manager_index_fields(reader, root_address, _XIANQIAO_METHODS)
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("ImmHoleData"))
    if "CoreMainDic" not in data_fields:
        raise FanxiuRuntimeMemoryError("ImmHoleMgr 本地仙窍模型尚未初始化")
    return data_fields


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
    state_address: int,
    environment_address: int,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    core_main = _table_entries(reader, _xianqiao_data_fields(reader, root_address)["CoreMainDic"])
    systems: list[dict[str, Any]] = []
    malformed_count = 0

    for raw_type, raw_parts in core_main.items():
        system_type = as_int(raw_type)
        if system_type is None:
            continue
        counts = {element_id: 0 for element_id in XIANQIAO_ELEMENT_NAMES}
        worn_parts = 0
        equipped: list[dict[str, Any]] = []
        parts = _table_entries(reader, raw_parts)
        for raw_part_id, raw_part in parts.items():
            part_id = as_int(raw_part_id)
            if part_id is None:
                continue
            part_fields = _object_fields(reader, raw_part)
            equip_fields = _object_fields(reader, part_fields.get("coreEquipVO"))
            if equip_fields.get("wear") is not True:
                continue
            elements, declared_count = reader.list_items(equip_fields.get("elements"))
            if declared_count is None or len(elements) != declared_count:
                malformed_count += 1
                continue
            worn_parts += 1
            side_attrs: list[dict[str, Any]] = []
            raw_side_attrs, declared_side_attr_count = reader.list_items(
                equip_fields.get("sideAttrVOList")
            )
            if declared_side_attr_count is not None:
                for raw_side_attr in raw_side_attrs:
                    side_fields = _object_fields(reader, raw_side_attr)
                    side_attrs.append(
                        {
                            "bank_id": as_int(side_fields.get("sideAttrBankId")),
                            "value": side_fields.get("attrValue"),
                        }
                    )
            for raw_element in elements:
                element_id = as_int(raw_element)
                if element_id in counts:
                    counts[element_id] += 1
                else:
                    malformed_count += 1
            equipped.append(
                {
                    "part_id": part_id,
                    "base_id": as_int(equip_fields.get("baseId")),
                    "level": as_int(equip_fields.get("level")),
                    "elements": [as_int(item) for item in elements],
                    "side_attrs": side_attrs,
                }
            )
        systems.append(
            {
                "type": system_type,
                "worn_parts": worn_parts,
                "element_counts": {
                    XIANQIAO_ELEMENT_NAMES[element_id]: counts[element_id]
                    for element_id in XIANQIAO_ELEMENT_NAMES
                },
                "element_counts_by_id": counts,
                "equipped": sorted(equipped, key=lambda item: item["part_id"]),
            }
        )

    systems.sort(key=lambda item: item["type"])
    # A player can retain equipment in older systems.  The newest system with
    # worn仙纹 is the current progression system and matches GetElementLvDic(type).
    active = next((item for item in reversed(systems) if item["worn_parts"]), None)
    complete = active is not None and malformed_count == 0
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "ImmHoleMgr.Model.ImmHoleData.CoreMainDic",
        "active_system_type": active["type"] if active else None,
        "element_counts": active["element_counts"] if active else {},
        "element_counts_by_id": active["element_counts_by_id"] if active else {},
        "worn_parts": active["worn_parts"] if active else 0,
        "equipped": active["equipped"] if active else [],
        "systems": systems,
        "malformed_count": malformed_count,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": time.time(),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "state_address": f"0x{state_address:x}",
            "environment_address": f"0x{environment_address:x}",
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def select_xianqiao_trial_drop_element(counts: dict[Any, Any]) -> dict[str, Any]:
    """Choose the least represented desired element; 金→水→火 breaks ties."""

    normalized = {
        element_id: int(counts.get(element_id, counts.get(str(element_id), 0)) or 0)
        for element_id in XIANQIAO_DESIRED_ELEMENT_IDS
    }
    element_id = min(XIANQIAO_DESIRED_ELEMENT_IDS, key=lambda item: (normalized[item], XIANQIAO_DESIRED_ELEMENT_IDS.index(item)))
    return {
        "element_id": element_id,
        "element": XIANQIAO_ELEMENT_NAMES[element_id],
        "desired_counts": {
            XIANQIAO_ELEMENT_NAMES[item]: normalized[item]
            for item in XIANQIAO_DESIRED_ELEMENT_IDS
        },
    }


def read_xianqiao_snapshot() -> dict[str, Any]:
    """Return current equipped仙纹 element counts without any game action."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        root, root_cache_hit, environment_address = resolve_lua_global_manager_root(
            memory,
            manager_key="xianqiao",
            state_address=state_address,
            global_name="ImmHoleMgr",
            required_methods=_XIANQIAO_METHODS,
            validate=_xianqiao_data_fields,
        )
        result = _snapshot(
            memory,
            root,
            root_cache_hit=root_cache_hit,
            state_address=state_address,
            environment_address=environment_address,
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        return result
    except Exception as exc:
        reason = str(exc) if isinstance(exc, FanxiuRuntimeMemoryError) else f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }


__all__ = [
    "XIANQIAO_DESIRED_ELEMENT_IDS",
    "XIANQIAO_ELEMENT_NAMES",
    "read_xianqiao_snapshot",
    "select_xianqiao_trial_drop_element",
]
