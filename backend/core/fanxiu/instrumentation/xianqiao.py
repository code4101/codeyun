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
    from backend.core.fanxiu.catalog.xianqiao import build_fanxiu_xianqiao_mechanics

    # Element IDs belong to a progression system: e.g. 中期 uses 6..10.
    # Resolve their five-element meaning from the game's configuration, rather
    # than treating every later system as corrupt or folding IDs arithmetically.
    canonical_ids = {name: key for key, name in XIANQIAO_ELEMENT_NAMES.items()}
    element_maps = {
        int(system["id"]): {
            int(element["id"]): canonical_ids.get(element["name"])
            for element in system["elements"]
        }
        for system in build_fanxiu_xianqiao_mechanics()["systems"]
    }
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
        element_map = element_maps.get(system_type, {})
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
                element_id = element_map.get(as_int(raw_element))
                if element_id in counts:
                    counts[element_id] += 1
                else:
                    malformed_count += 1
            equipped.append(
                {
                    "part_id": part_id,
                    "base_id": as_int(equip_fields.get("baseId")),
                    "equip_id": reader.long(equip_fields.get("equipId")),
                    "sum_exp": reader.long(equip_fields.get("sumExp")),
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


def read_xianqiao_equipment_inventory_snapshot() -> dict[str, Any]:
    """Read the complete loaded equipment bag without choosing a replacement.

    Equipment instance IDs and experience use native Long decoding. Worn
    equipment remains owned inventory; no row may authorize feeding old gear
    as material. Quality/part configuration and an actual equip transaction
    are separate contracts.
    """
    from .ui_runtime_context import acquire_lua_global_runtime_context
    context = acquire_lua_global_runtime_context({'ImmHoleMgr'})
    manager = context.loaded_manager_fields('ImmHoleMgr', _XIANQIAO_METHODS)
    instance = context.reader.fields(manager.get('inst'))
    model = context.reader.fields(instance.get('Model'))
    data = context.reader.fields(model.get('ImmHoleData'))
    bag = context.reader.dictionary_fields(data.get('coreEquipMap'))
    values, count = context.reader.list_items(data.get('allCoreList'))
    if count is None or count != len(values) or len(bag) != count:
        raise FanxiuRuntimeMemoryError('仙纹库存字典与完整列表不一致', code='runtime_incomplete')
    items = []
    for value in values:
        row = _object_fields(context.reader, value)
        ident = context.reader.long(row.get('equipId'))
        base = as_int(row.get('baseId'))
        level = as_int(row.get('level'))
        quantity = as_int(row.get('num'))
        exp = context.reader.long(row.get('sumExp'))
        worn = row.get('wear')
        if (ident is None or ident <= 0 or base is None or base <= 0
                or level is None or level < 0 or quantity is None or quantity <= 0
                or exp is None or exp < 0 or type(worn) is not bool):
            raise FanxiuRuntimeMemoryError('仙纹库存实例字段不完整', code='runtime_incomplete')
        items.append({'equip_id': ident, 'base_id': base, 'level': level,
                      'quantity': quantity, 'sum_exp': exp, 'worn': worn})
    if len({row['equip_id'] for row in items}) != len(items):
        raise FanxiuRuntimeMemoryError('仙纹库存实例身份重复', code='snapshot_incoherent')
    return {'complete': True, 'read_only': True, 'source': 'ImmHoleData.coreEquipMap/allCoreList',
            'items': items, 'count': count,
            'captured_at': datetime.now().astimezone().isoformat(),
            'evidence': {'pid': context.binding.pid,
                         'process_start_ticks': context.binding.process_start_ticks}}


def read_xianqiao_equipment_config_snapshot() -> dict[str, Any]:
    """Read current CoreWareBase identities, parts and qualities from Runtime.

    Static catalog candidates are research hints only. This naturally loaded
    configuration supplies the exact version used for replacement admission;
    it never loads a table or invokes DBMgr methods.
    """
    from .ui_runtime_context import acquire_lua_global_runtime_context
    from .runtime_memory import table_ref
    from .item_config import _DB_METHODS
    from .resource_auto_use import read_runtime_config_rows
    context = acquire_lua_global_runtime_context({'DBMgr'})
    manager = context.loaded_manager_fields('DBMgr', _DB_METHODS)
    instance = context.reader.fields(manager.get('inst'))
    config = context.reader.fields(instance.get('ConfigDic'))
    dictionary = table_ref(config.get('_dt_'))
    if dictionary is None:
        raise FanxiuRuntimeMemoryError('仙纹配置字典未自然加载', code='data_not_loaded')
    table = table_ref(context.reader.string_fields(dictionary.address,
        frozenset({'Core.CoreWareBase'})).get('Core.CoreWareBase'))
    if table is None:
        raise FanxiuRuntimeMemoryError('CoreWareBase未自然加载', code='data_not_loaded')
    packed = context.reader.table(table.address)
    refs = {ref.address: ref for value in [*packed['fields'].values(), *packed['array']]
            if (ref := table_ref(value)) is not None}
    if not 1 <= len(refs) <= 512:
        raise FanxiuRuntimeMemoryError('仙纹完整配置行缺失或越界', code='runtime_incomplete')
    rows = read_runtime_config_rows(context.reader, list(refs.values()),
        environment_address=context.binding.environment_address,
        state_address=context.binding.state_address,
        group_name='Core', table_name='CoreWareBase', fields=('itemId', 'type', 'parts', 'quality'))
    result = {}
    for row in rows:
        item, system, part, quality = (as_int(row[k]) for k in ('itemId', 'type', 'parts', 'quality'))
        if (item is None or item <= 0 or item in result or system is None or system <= 0
                or part not in range(1, 7) or quality is None or quality <= 0):
            raise FanxiuRuntimeMemoryError('仙纹原生配置身份不完整', code='runtime_incomplete')
        result[item] = {'base_id': item, 'system_type': system, 'part_id': part, 'quality': quality}
    return {'complete': True, 'read_only': True, 'source': 'Runtime Core.CoreWareBase',
            'by_base_id': result, 'captured_at': datetime.now().astimezone().isoformat(),
            'evidence': {'pid': context.binding.pid,
                         'process_start_ticks': context.binding.process_start_ticks}}


def read_xianqiao_opening_snapshot() -> dict[str, Any]:
    """Read every loaded hole's opening progress, including locked equipment.

    Opening uses activeLevel/unLockEquip; equipped仙纹 levels are a different
    resource system. Native GetImmHoleMaxLevelByType returns the highest open
    part ID, so expose that value separately from the number of open holes.
    No GUI, Lua invocation or resource consumption occurs.
    """
    from .ui_runtime_context import acquire_lua_global_runtime_context

    context = acquire_lua_global_runtime_context({"ImmHoleMgr"})
    manager = context.loaded_manager_fields("ImmHoleMgr", _XIANQIAO_METHODS)
    instance = context.reader.fields(manager.get("inst"))
    model = context.reader.fields(instance.get("Model"))
    data = context.reader.fields(model.get("ImmHoleData"))
    core = _table_entries(context.reader, data.get("CoreMainDic"))
    if not core:
        raise FanxiuRuntimeMemoryError("仙窍开窍模型尚未加载", code="data_not_loaded")
    systems = []
    for raw_type, raw_parts in core.items():
        system_type = as_int(raw_type)
        if system_type is None or system_type <= 0:
            raise FanxiuRuntimeMemoryError("仙窍体系身份无效", code="runtime_incomplete")
        parts = []
        for raw_id, value in _table_entries(context.reader, raw_parts).items():
            part_id = as_int(raw_id)
            fields = _object_fields(context.reader, value)
            level = as_int(fields.get("activeLevel"))
            opened = fields.get("unLockEquip")
            if part_id is None or part_id <= 0 or level is None or level < 0 or not isinstance(opened, bool):
                raise FanxiuRuntimeMemoryError("仙窍开窍进度不完整", code="runtime_incomplete")
            parts.append({"part_id": part_id, "active_level": level, "opened": opened})
        if not parts:
            raise FanxiuRuntimeMemoryError("仙窍体系部位未加载", code="data_not_loaded")
        opened_ids = [p["part_id"] for p in parts if p["opened"]]
        systems.append({"type": system_type, "parts": sorted(parts, key=lambda p: p["part_id"]),
                        "opened_count": len(opened_ids), "highest_open_part_id": max(opened_ids, default=0)})
    return {"complete": True, "read_only": True,
            "source": "current_ImmHoleMgr.CoreMainDic.activeLevel/unLockEquip",
            "systems": sorted(systems, key=lambda s: s["type"]),
            "pid": context.binding.pid, "process_start_ticks": context.binding.process_start_ticks}


def read_xianqiao_opening_cost_snapshot(*, system_type: int, part_id: int) -> dict[str, Any]:
    """Read native cached opening costs; never initialize a missing config.

    A complete, contiguous configuration chain and its row identities must
    agree with the selected live hole. Costs describe opening, not仙纹升级.
    Availability/authorization and post-click progress belong to the caller.
    """
    from .ui_runtime_context import acquire_lua_global_runtime_context
    from .exchange_shop import project_exchange_shop_numeric_row

    if (not isinstance(system_type, int) or isinstance(system_type, bool)
            or not isinstance(part_id, int) or isinstance(part_id, bool)
            or system_type <= 0 or part_id <= 0):
        raise ValueError("仙窍体系和部位必须为正整数")
    context = acquire_lua_global_runtime_context({"ImmHoleMgr"})
    manager = context.loaded_manager_fields("ImmHoleMgr", _XIANQIAO_METHODS)
    instance = context.reader.fields(manager.get("inst"))
    model = context.reader.fields(instance.get("Model"))
    data = context.reader.fields(model.get("ImmHoleData"))
    parts = _table_entries(context.reader, _table_entries(context.reader, data.get("CoreMainDic")).get(system_type))
    hole = _object_fields(context.reader, parts.get(part_id))
    active = as_int(hole.get("activeLevel"))
    opened = hole.get("unLockEquip")
    if active is None or active < 0 or not isinstance(opened, bool):
        raise FanxiuRuntimeMemoryError("指定仙窍进度未加载", code="data_not_loaded")
    systems = _table_entries(context.reader, data.get("_ImmHoleLeveleDic"))
    levels = _table_entries(context.reader, _table_entries(context.reader, systems.get(system_type)).get(part_id))
    rows = []
    for key, value in levels.items():
        level = as_int(key)
        if level is None:
            continue  # Native maxGrade is metadata, not a level entry.
        if not isinstance(value, LuaRef) or value.kind != "table":
            raise FanxiuRuntimeMemoryError("仙窍开窍等级行无效", code="runtime_incomplete")
        row = project_exchange_shop_numeric_row(context.reader.table(value.address))
        if len(row) <= 6 or [as_int(row[i]) for i in (2, 3, 4)] != [system_type, part_id, level]:
            raise FanxiuRuntimeMemoryError("仙窍开窍配置身份不符", code="runtime_incomplete")
        cost = as_int(row[6])
        if cost is None or cost < 0:
            raise FanxiuRuntimeMemoryError("仙窍开窍成本无效", code="runtime_incomplete")
        rows.append({"level": level, "consume_exp": cost})
    rows.sort(key=lambda r: r["level"])
    if not rows:
        raise FanxiuRuntimeMemoryError("仙窍开窍等级缓存尚未自然加载", code="data_not_loaded")
    maximum = rows[-1]["level"]
    if [r["level"] for r in rows] != list(range(1, maximum + 1)) or active > maximum:
        raise FanxiuRuntimeMemoryError("仙窍开窍配置链不完整", code="runtime_incomplete")
    remaining = [r for r in rows if r["level"] > active]
    return {"complete": True, "read_only": True, "system_type": system_type, "part_id": part_id,
            "active_level": active, "opened": opened, "max_level": maximum,
            "next_level_exp": remaining[0]["consume_exp"] if remaining else None,
            "remaining_exp": sum(r["consume_exp"] for r in remaining), "remaining_levels": remaining,
            "source": "current_ImmHoleMgr.ImmHoleData._ImmHoleLeveleDic",
            "pid": context.binding.pid, "process_start_ticks": context.binding.process_start_ticks}


__all__ = [
    "XIANQIAO_DESIRED_ELEMENT_IDS",
    "XIANQIAO_ELEMENT_NAMES",
    "read_xianqiao_snapshot",
    "read_xianqiao_opening_snapshot",
    "read_xianqiao_opening_cost_snapshot",
    "select_xianqiao_trial_drop_element",
]
