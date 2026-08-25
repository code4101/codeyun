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


_MEDICIAL_MARKER = b"LuaMedicialMgr"
_MEDICIAL_METHODS = frozenset(
    {
        "LuaMedicialMgr",
        "Inst_get",
        "CheckMedicine",
    }
)
_INFO_FIELDS = frozenset(
    {
        "formulas",
        "realmId",
        "proficiency",
        "operateVOs",
        "startTime",
        "endTime",
    }
)


def _runtime_table(value: Any, *, label: str) -> LuaRef:
    if not isinstance(value, LuaRef) or value.kind != "table":
        raise FanxiuRuntimeMemoryError(
            f"{label} 尚未初始化",
            code="data_not_loaded",
        )
    return value


def _manager_model_and_data(
    reader: LuaJitReader,
    root_address: int,
) -> tuple[dict[Any, Any], dict[Any, Any]]:
    manager = manager_index_fields(reader, root_address, _MEDICIAL_METHODS)
    instance = reader.fields(_runtime_table(manager.get("inst"), label="MedicialMgr 实例"))
    model = reader.fields(_runtime_table(instance.get("Model"), label="MedicialMgr Model"))
    data = reader.fields(
        _runtime_table(model.get("MedicialData"), label="MedicialMgr MedicialData")
    )
    return model, data


def _info_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    _model, data = _manager_model_and_data(reader, root_address)
    info = reader.fields(
        _runtime_table(data.get("_MedicialVo"), label="炼丹 MedicalInfoVO")
    )
    missing = sorted(_INFO_FIELDS.difference(info))
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"炼丹 MedicalInfoVO 字段不完整: {', '.join(missing)}",
            code="snapshot_incomplete",
        )
    return info


def _main_lua_state_address(memory: MumuProcessMemory) -> int:
    from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
        _lua_addresses,
    )

    return int(_lua_addresses(memory)["state"], 16)


def _package_loaded_root(
    memory: MumuProcessMemory,
    *,
    state_address: int,
) -> int:
    """Resolve an already-loaded MedicialMgr module without heap discovery."""

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
    typed_error: FanxiuRuntimeMemoryError | None = None
    for value in [*loaded_table["array"], *loaded_table["fields"].values()]:
        module = table_ref(value)
        if module is None or module.address in candidates:
            continue
        try:
            manager_index_fields(reader, module.address, _MEDICIAL_METHODS)
            _info_fields(reader, module.address)
        except FanxiuRuntimeMemoryError as exc:
            if exc.code in {"data_not_loaded", "snapshot_incomplete"}:
                typed_error = typed_error or exc
            continue
        candidates[module.address] = module.address
    if len(candidates) == 1:
        return next(iter(candidates))
    if len(candidates) > 1:
        raise FanxiuRuntimeMemoryError(
            "Lua package.loaded 中 MedicialMgr 候选不唯一",
            code="manager_ambiguous",
        )
    if typed_error is not None:
        raise typed_error
    raise FanxiuRuntimeMemoryError(
        "Lua package.loaded 中没有已加载的 MedicialMgr",
        code="manager_not_found",
    )


def _resolve_medicial_root(
    memory: MumuProcessMemory,
    *,
    allow_diagnostic_discovery: bool,
) -> tuple[int, bool, str]:
    state_address = _main_lua_state_address(memory)
    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="alchemy-medicial",
            state_address=state_address,
            global_name="MedicialMgr",
            required_methods=_MEDICIAL_METHODS,
            validate=_info_fields,
        )
        return root, cache_hit, "lua_global"
    except FanxiuRuntimeMemoryError as exc:
        if exc.code in {"data_not_loaded", "snapshot_incomplete"}:
            raise

    try:
        return (
            _package_loaded_root(memory, state_address=state_address),
            False,
            "package_loaded",
        )
    except FanxiuRuntimeMemoryError as exc:
        if exc.code in {"data_not_loaded", "snapshot_incomplete"}:
            raise

    try:
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key="alchemy-medicial",
            marker=_MEDICIAL_MARKER,
            required_methods=_MEDICIAL_METHODS,
            validate=_info_fields,
            allow_discovery=False,
        )
        return root, cache_hit, "constructor_marker_cache"
    except FanxiuRuntimeMemoryError:
        if not allow_diagnostic_discovery:
            raise FanxiuRuntimeMemoryError(
                "MedicialMgr 未出现在 Lua global/package.loaded，且 marker 缓存未命中",
                code="manager_not_found",
            )

    root, cache_hit = resolve_manager_root(
        memory,
        manager_key="alchemy-medicial",
        marker=_MEDICIAL_MARKER,
        required_methods=_MEDICIAL_METHODS,
        validate=_info_fields,
        allow_discovery=True,
    )
    return root, cache_hit, "constructor_marker_diagnostic"


def _required_integer(
    reader: LuaJitReader,
    value: Any,
    *,
    label: str,
) -> int:
    number = reader.long(value) if isinstance(value, LuaRef) else as_int(value)
    if number is None:
        raise FanxiuRuntimeMemoryError(
            f"{label} 不是有效整数",
            code="snapshot_incomplete",
        )
    return int(number)


def _operation(reader: LuaJitReader, value: Any) -> dict[str, int]:
    fields = reader.fields(_runtime_table(value, label="MedicalOperateVO"))
    return {
        # Static protocol only proves the server VO field name ``id``.  Do not
        # rename it to recipe/product id until a live before/after probe proves
        # which namespace it uses.
        "id": _required_integer(reader, fields.get("id"), label="operate.id"),
        "count": _required_integer(reader, fields.get("num"), label="operate.num"),
        "end_time_ms": _required_integer(
            reader,
            fields.get("endTime"),
            label="operate.endTime",
        ),
    }


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
    manager_resolver: str,
) -> dict[str, Any]:
    captured_at_epoch = time.time()
    reader = LuaJitReader(memory)
    model, data = _manager_model_and_data(reader, root_address)
    info = _info_fields(reader, root_address)
    raw_formulas, declared_formula_count = reader.list_items(info["formulas"])
    formulas = []
    for value in raw_formulas:
        formula_id = as_int(value)
        if formula_id is None:
            raise FanxiuRuntimeMemoryError(
                "炼丹 formulas 含无效 ID",
                code="snapshot_incomplete",
            )
        formulas.append(int(formula_id))
    if declared_formula_count is not None and declared_formula_count != len(formulas):
        raise FanxiuRuntimeMemoryError(
            "炼丹 formulas 解码数量与声明数量不一致",
            code="snapshot_incomplete",
        )

    raw_operations, declared_operation_count = reader.list_items(info["operateVOs"])
    operations = [_operation(reader, value) for value in raw_operations]
    if (
        declared_operation_count is not None
        and declared_operation_count != len(operations)
    ):
        raise FanxiuRuntimeMemoryError(
            "炼丹 operateVOs 解码数量与声明数量不一致",
            code="snapshot_incomplete",
        )

    realm_id = _required_integer(reader, info["realmId"], label="realmId")
    proficiency = _required_integer(
        reader,
        info["proficiency"],
        label="proficiency",
    )
    start_time_ms = _required_integer(reader, info["startTime"], label="startTime")
    end_time_ms = _required_integer(reader, info["endTime"], label="endTime")
    if min(realm_id, proficiency, start_time_ms, end_time_ms) < 0:
        raise FanxiuRuntimeMemoryError(
            "炼丹状态含负数",
            code="snapshot_incomplete",
        )
    active = end_time_ms > 0
    if active and (not operations or end_time_ms < start_time_ms):
        raise FanxiuRuntimeMemoryError(
            "炼丹进行中但时间或操作列表不完整",
            code="snapshot_incomplete",
        )

    raw_quick_mode = data.get("_AlchemyIsFast")
    quick_mode_materialized = isinstance(raw_quick_mode, bool)
    quick_mode = raw_quick_mode if quick_mode_materialized else True
    model_active = model.get("isOperate")
    if not isinstance(model_active, bool):
        model_active = None
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "runtime_memory",
        "protocol": "MedicialMgr.Model.MedicialData._MedicialVo",
        "realm_id": realm_id,
        "proficiency": proficiency,
        "learned_formula_ids": formulas,
        "learned_formula_count": len(formulas),
        "operations": operations,
        "operation_count": len(operations),
        "total_operation_count": sum(item["count"] for item in operations),
        "start_time_ms": start_time_ms,
        "end_time_ms": end_time_ms,
        "active": active,
        "model_active": model_active,
        "quick_mode": bool(quick_mode),
        "quick_mode_materialized": quick_mode_materialized,
        "captured_at": datetime.fromtimestamp(captured_at_epoch).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "captured_at_epoch": captured_at_epoch,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
            "manager_resolver": manager_resolver,
            "read_only": True,
        },
    }


def classify_alchemy_transition(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    expected_count: int | None = None,
) -> dict[str, Any]:
    """Classify a strictly-read-only before/after UI transition."""

    if not before.get("complete") or not after.get("complete"):
        return {"ok": False, "kind": "incomplete", "reason": "快照不完整"}
    before_identity = (
        (before.get("evidence") or {}).get("pid"),
        (before.get("evidence") or {}).get("process_start_ticks"),
    )
    after_identity = (
        (after.get("evidence") or {}).get("pid"),
        (after.get("evidence") or {}).get("process_start_ticks"),
    )
    if before_identity != after_identity:
        return {"ok": False, "kind": "process_changed", "reason": "游戏进程已替换"}
    if not before.get("active") and after.get("active"):
        actual_count = int(after.get("total_operation_count") or 0)
        count_ok = expected_count is None or actual_count == int(expected_count)
        return {
            "ok": count_ok,
            "kind": "started" if count_ok else "count_mismatch",
            "actual_count": actual_count,
            "expected_count": expected_count,
        }
    if before.get("active") and not after.get("active"):
        before_proficiency = int(before.get("proficiency") or 0)
        after_proficiency = int(after.get("proficiency") or 0)
        return {
            "ok": after_proficiency >= before_proficiency,
            "kind": (
                "finished"
                if after_proficiency >= before_proficiency
                else "counter_regressed"
            ),
            "proficiency_delta": after_proficiency - before_proficiency,
        }
    return {"ok": True, "kind": "unchanged"}


def read_alchemy_snapshot(
    *,
    allow_diagnostic_discovery: bool = False,
) -> dict[str, Any]:
    """Read the loaded alchemy model without invoking Lua or sending packets."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached(max_age_seconds=None)
        root_address, root_cache_hit, resolver = _resolve_medicial_root(
            memory,
            allow_diagnostic_discovery=allow_diagnostic_discovery,
        )
        result = _snapshot(
            memory,
            root_address,
            root_cache_hit=root_cache_hit,
            manager_resolver=resolver,
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        return result
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}"
        )
        error_code = exc.code if isinstance(exc, FanxiuRuntimeMemoryError) else None
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": reason,
            "error_code": error_code,
            "load_state": (
                "data_not_loaded" if error_code == "data_not_loaded" else "error"
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
