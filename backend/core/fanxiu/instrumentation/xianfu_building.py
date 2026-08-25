from __future__ import annotations

import struct
import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    lua_jit_intern_state,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
    table_ref,
)


_BUILDING_MARKER = b"LuaBuildingMgr"
_BUILDING_METHODS = frozenset(
    {
        "LuaBuildingMgr",
        "Inst_get",
        "XianFuIsOpen",
    }
)
_BUILDING_FIELDS = frozenset(
    {
        "type",
        "level",
        "level2",
        "jie",
        "partners",
        "endTime",
        "items",
        "scienceMap",
    }
)


class _BuildingDataUnavailable(FanxiuRuntimeMemoryError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="data_not_loaded")


class _BuildingDataInvalid(FanxiuRuntimeMemoryError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="snapshot_incomplete")


def _table_ref(value: Any, *, label: str) -> LuaRef:
    if not isinstance(value, LuaRef) or value.kind != "table":
        raise _BuildingDataUnavailable(f"{label} 尚未初始化")
    return value


def _data_table_ref(value: Any, *, label: str) -> LuaRef:
    if not isinstance(value, LuaRef) or value.kind != "table":
        raise _BuildingDataInvalid(f"{label} 不是有效数据表")
    return value


def _building_manager_model_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(
        reader,
        root_address,
        _BUILDING_METHODS,
    )
    instance_ref = _table_ref(manager.get("inst"), label="BuildingMgr 实例")
    instance = reader.fields(instance_ref)
    model_ref = _table_ref(instance.get("Model"), label="BuildingMgr Model")
    return reader.fields(model_ref)


def _building_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    model = _building_manager_model_fields(reader, root_address)
    data_ref = _table_ref(model.get("BuildingData"), label="BuildingData")
    data = reader.fields(data_ref)
    building_info_ref = _table_ref(
        data.get("buildingInfoDic"),
        label="BuildingData.buildingInfoDic",
    )
    suits_ref = _table_ref(data.get("suits"), label="BuildingData.suits")
    building_info = reader.dictionary_fields(building_info_ref)
    suits, suit_count = reader.list_items(suits_ref)
    if not building_info and suit_count == 0 and not suits:
        raise _BuildingDataUnavailable(
            "BuildingData 已构造但 buildingInfoDic/suits 尚未同步"
        )
    return data


def _main_lua_state_address(memory: MumuProcessMemory) -> int:
    from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
        _lua_addresses,
    )

    return int(_lua_addresses(memory)["state"], 16)


def _package_loaded_building_root(
    memory: MumuProcessMemory,
    *,
    state_address: int,
) -> int:
    """Resolve the already-loaded BuildingMgr module without heap discovery."""

    state = memory.read(int(state_address), 96)
    environment_address = struct.unpack_from("<Q", state, 72)[0]
    reader = LuaJitReader(memory)
    _global, string_table, string_mask, string_seed = lua_jit_intern_state(
        memory,
        int(state_address),
    )
    exact = {
        "string_table_address": string_table,
        "string_mask": string_mask,
        "string_seed": string_seed,
    }
    package = table_ref(
        reader.interned_string_field(environment_address, "package", **exact)
    )
    loaded = (
        table_ref(reader.interned_string_field(package.address, "loaded", **exact))
        if package
        else None
    )
    if loaded is None:
        raise FanxiuRuntimeMemoryError(
            "Lua package.loaded 尚未加载",
            code="data_not_loaded",
        )

    loaded_table = reader.table(loaded.address)
    candidates: dict[int, int] = {}
    data_not_loaded: FanxiuRuntimeMemoryError | None = None
    snapshot_incomplete: FanxiuRuntimeMemoryError | None = None
    for value in [*loaded_table["array"], *loaded_table["fields"].values()]:
        module = table_ref(value)
        if module is None or module.address in candidates:
            continue
        try:
            manager_index_fields(reader, module.address, _BUILDING_METHODS)
            _building_data_fields(reader, module.address)
        except FanxiuRuntimeMemoryError as exc:
            if exc.code == "data_not_loaded":
                data_not_loaded = exc
            elif exc.code == "snapshot_incomplete":
                snapshot_incomplete = exc
            continue
        candidates[module.address] = module.address
    if len(candidates) == 1:
        return next(iter(candidates))
    if len(candidates) > 1:
        raise FanxiuRuntimeMemoryError(
            "Lua package.loaded 中 BuildingMgr 候选不唯一",
            code="manager_ambiguous",
        )
    if data_not_loaded is not None:
        raise data_not_loaded
    if snapshot_incomplete is not None:
        raise snapshot_incomplete
    raise FanxiuRuntimeMemoryError(
        "Lua package.loaded 中没有已加载的 BuildingMgr",
        code="manager_not_found",
    )


def _resolve_building_root(
    memory: MumuProcessMemory,
    *,
    allow_diagnostic_discovery: bool,
) -> tuple[int, bool, str]:
    state_address = _main_lua_state_address(memory)
    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="xianfu-building",
            state_address=state_address,
            global_name="BuildingMgr",
            required_methods=_BUILDING_METHODS,
            validate=_building_data_fields,
        )
        return root, cache_hit, "lua_global"
    except FanxiuRuntimeMemoryError as exc:
        if exc.code in {"data_not_loaded", "snapshot_incomplete"}:
            raise

    try:
        return (
            _package_loaded_building_root(
                memory,
                state_address=state_address,
            ),
            False,
            "package_loaded",
        )
    except FanxiuRuntimeMemoryError as exc:
        if exc.code in {"data_not_loaded", "snapshot_incomplete"}:
            raise

    try:
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key="xianfu-building",
            marker=_BUILDING_MARKER,
            required_methods=_BUILDING_METHODS,
            validate=_building_manager_model_fields,
            allow_discovery=False,
        )
        return root, cache_hit, "constructor_marker_cache"
    except FanxiuRuntimeMemoryError:
        if not allow_diagnostic_discovery:
            raise FanxiuRuntimeMemoryError(
                "BuildingMgr 未出现在 Lua global/package.loaded，且 marker 缓存未命中",
                code="manager_not_found",
            )

    root, cache_hit = resolve_manager_root(
        memory,
        manager_key="xianfu-building",
        marker=_BUILDING_MARKER,
        required_methods=_BUILDING_METHODS,
        validate=_building_manager_model_fields,
        allow_discovery=True,
    )
    return root, cache_hit, "constructor_marker_diagnostic"


def _integer(reader: LuaJitReader, value: Any, *, label: str) -> int:
    number = reader.long(value) if isinstance(value, LuaRef) else as_int(value)
    if number is None:
        raise _BuildingDataInvalid(f"{label} 不是有效整数")
    return int(number)


def _optional_integer(
    reader: LuaJitReader,
    value: Any,
    *,
    label: str,
) -> int | None:
    if value is None:
        return None
    return _integer(reader, value, label=label)


def _map_key(value: Any, *, label: str) -> str:
    number = as_int(value)
    if number is not None:
        return str(number)
    if isinstance(value, str) and value:
        return value
    raise _BuildingDataInvalid(f"{label} 含无效键")


def _integer_map(
    reader: LuaJitReader,
    value: Any,
    *,
    label: str,
) -> dict[str, int]:
    _data_table_ref(value, label=label)
    normalized: dict[str, int] = {}
    for raw_key, raw_value in reader.dictionary_fields(value).items():
        key = _map_key(raw_key, label=label)
        if key in normalized:
            raise _BuildingDataInvalid(f"{label} 含重复键 {key}")
        normalized[key] = _integer(
            reader,
            raw_value,
            label=f"{label}[{key}]",
        )
    return dict(
        sorted(
            normalized.items(),
            key=lambda item: (
                0,
                int(item[0]),
            )
            if item[0].lstrip("-").isdigit()
            else (1, item[0]),
        )
    )


def _optional_integer_map(
    reader: LuaJitReader,
    value: Any,
    *,
    label: str,
) -> dict[str, int] | None:
    if value is None:
        return None
    return _integer_map(reader, value, label=label)


def _partners(
    reader: LuaJitReader,
    value: Any,
    *,
    building_type: int,
) -> list[dict[str, int]]:
    label = f"buildingInfoDic[{building_type}].partners"
    _data_table_ref(value, label=label)
    raw_partners, declared_count = reader.list_items(value)
    if declared_count is None or declared_count != len(raw_partners):
        raise _BuildingDataInvalid(f"{label} 的 CList 结构不完整")
    normalized: list[dict[str, int]] = []
    grids: set[int] = set()
    for index, raw_partner in enumerate(raw_partners):
        fields = reader.fields(raw_partner)
        if not {"grid", "partner", "endTime"}.issubset(fields):
            raise _BuildingDataInvalid(f"{label}[{index}] 字段不完整")
        grid = _integer(reader, fields.get("grid"), label=f"{label}[{index}].grid")
        if grid in grids:
            raise _BuildingDataInvalid(f"{label} 含重复槽位 {grid}")
        grids.add(grid)
        normalized.append(
            {
                "grid": grid,
                "partner": _integer(
                    reader,
                    fields.get("partner"),
                    label=f"{label}[{index}].partner",
                ),
                "end_time": _integer(
                    reader,
                    fields.get("endTime"),
                    label=f"{label}[{index}].endTime",
                ),
            }
        )
    return sorted(normalized, key=lambda item: item["grid"])


def _building(
    reader: LuaJitReader,
    raw_key: Any,
    raw_building: Any,
) -> dict[str, Any]:
    fields = reader.fields(raw_building)
    if not _BUILDING_FIELDS.issubset(fields):
        raise _BuildingDataInvalid("BuildingVO 字段不完整")
    key_type = _integer(reader, raw_key, label="buildingInfoDic key")
    building_type = _integer(reader, fields.get("type"), label="BuildingVO.type")
    if key_type != building_type:
        raise _BuildingDataInvalid(
            f"buildingInfoDic key={key_type} 与 BuildingVO.type={building_type} 不一致"
        )
    return {
        "type": building_type,
        "level": _integer(
            reader,
            fields.get("level"),
            label=f"BuildingVO[{building_type}].level",
        ),
        "level2": _integer(
            reader,
            fields.get("level2"),
            label=f"BuildingVO[{building_type}].level2",
        ),
        "jie": _integer(
            reader,
            fields.get("jie"),
            label=f"BuildingVO[{building_type}].jie",
        ),
        "partners": _partners(
            reader,
            fields.get("partners"),
            building_type=building_type,
        ),
        "end_time": _integer(
            reader,
            fields.get("endTime"),
            label=f"BuildingVO[{building_type}].endTime",
        ),
        "items": _integer_map(
            reader,
            fields.get("items"),
            label=f"BuildingVO[{building_type}].items",
        ),
        "science_map": _integer_map(
            reader,
            fields.get("scienceMap"),
            label=f"BuildingVO[{building_type}].scienceMap",
        ),
    }


def _suits(reader: LuaJitReader, value: Any) -> list[int]:
    _data_table_ref(value, label="BuildingData.suits")
    raw_suits, declared_count = reader.list_items(value)
    if declared_count is None or declared_count != len(raw_suits):
        raise _BuildingDataInvalid("BuildingData.suits 的 CList 结构不完整")
    return sorted(
        _integer(reader, value, label=f"BuildingData.suits[{index}]")
        for index, value in enumerate(raw_suits)
    )


def _golden_info(
    reader: LuaJitReader,
    value: Any,
) -> dict[str, Any] | None:
    if value is None:
        return None
    ref = _data_table_ref(value, label="BuildingData._goldenInfo")
    fields = reader.fields(ref)
    return {
        "attr": _optional_integer_map(
            reader,
            fields.get("attr"),
            label="BuildingData._goldenInfo.attr",
        ),
        "can_rec_exp": _optional_integer(
            reader,
            fields.get("canRecExp"),
            label="BuildingData._goldenInfo.canRecExp",
        ),
        "cal_exp_attr_map": _optional_integer_map(
            reader,
            fields.get("calExpAttrMap"),
            label="BuildingData._goldenInfo.calExpAttrMap",
        ),
    }


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data = _building_data_fields(reader, root_address)
    building_values = reader.dictionary_fields(data.get("buildingInfoDic"))
    buildings = [
        _building(reader, raw_key, raw_building)
        for raw_key, raw_building in building_values.items()
    ]
    buildings.sort(key=lambda item: item["type"])
    building_types = [item["type"] for item in buildings]
    if len(set(building_types)) != len(building_types):
        raise _BuildingDataInvalid("buildingInfoDic 含重复建筑类型")
    suits = _suits(reader, data.get("suits"))
    if not buildings and not suits:
        raise _BuildingDataUnavailable(
            "BuildingData 已构造但 buildingInfoDic/suits 尚未同步"
        )
    golden_info = _golden_info(reader, data.get("_goldenInfo"))
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "manager_available": True,
        "data_available": True,
        "load_state": "loaded",
        "source": "runtime_memory",
        "protocol": "BuildingMgr.Model.BuildingData",
        "buildings": buildings,
        "building_count": len(buildings),
        "suits": suits,
        "suit_count": len(suits),
        "golden_info": golden_info,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
            "read_only": True,
        },
    }


def read_xianfu_building_snapshot(
    *,
    allow_diagnostic_discovery: bool = False,
) -> dict[str, Any]:
    """Read the loaded Xianfu building model without invoking game methods."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    manager_available = False
    load_state = "manager_unavailable"
    try:
        memory = MumuProcessMemory.discover_cached()
        root, root_cache_hit, manager_resolver = _resolve_building_root(
            memory,
            allow_diagnostic_discovery=allow_diagnostic_discovery,
        )
        manager_available = True
        load_state = "data_unavailable"
        try:
            result = _snapshot(
                memory,
                root,
                root_cache_hit=root_cache_hit,
            )
        except _BuildingDataUnavailable:
            load_state = "data_not_loaded"
            raise
        except Exception:
            load_state = "data_invalid"
            raise
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result["evidence"]["manager_resolver"] = manager_resolver
        return result
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}"
        )
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "manager_available": manager_available,
            "data_available": False,
            "load_state": load_state,
            "source": "runtime_memory",
            "reason": reason,
            "reason_code": (
                exc.code
                if isinstance(exc, FanxiuRuntimeMemoryError)
                else "runtime_unavailable"
            ),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "read_only": True,
            },
        }
