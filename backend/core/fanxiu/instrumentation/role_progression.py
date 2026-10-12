from __future__ import annotations

import time
import struct
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_manager_root,
    table_ref,
)


_ROLE_MARKER = b"LuaRoleMgr"
_ROLE_METHODS = frozenset({"LuaRoleMgr", "Inst_get", "IsCanUpGrade"})
_PROPERTY_EXP = 3
_PROPERTY_FIGHT_POWER = 4
_PROPERTY_LEVEL = 6
_REALM_REQUIRED_EXP_SLOT = 26


def read_role_vip_source_snapshot(*, allow_discovery: bool = False) -> dict[str, Any]:
    """Inspect VIP scalar sources on the loaded account model, without Lua calls.

    This diagnostic deliberately returns candidates rather than a VIP level:
    a field name alone does not establish GetRoleViplevel's native semantics.
    It resolves the real RoleMgr through the existing account identity guard,
    reads only that model, and never traverses arbitrary tables or the heap.
    """
    memory = (MumuProcessMemory.discover() if allow_discovery
              else MumuProcessMemory.discover_cached(fallback_to_discovery=False))
    root, cache_hit = resolve_manager_root(
        memory, manager_key="role-profile", marker=_ROLE_MARKER,
        required_methods=_ROLE_METHODS, validate=_role_profile_values,
    )
    reader = LuaJitReader(memory)
    manager = manager_index_fields(reader, root, _ROLE_METHODS)
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    candidates = {
        str(key): value for key, value in model.items()
        if isinstance(key, str) and "vip" in key.lower()
        and isinstance(value, (str, int, float, bool, type(None)))
    }
    # Native account methods can be inherited. Follow only the class __index
    # chain (bounded, cycle checked), never unrelated object references.
    methods = {}
    model_ref = table_ref(instance.get("Model"))
    seen = set()
    while model_ref is not None and model_ref.address not in seen and len(seen) < 8:
        seen.add(model_ref.address)
        table = reader.table(model_ref.address)
        for name in ("GetRoleViplevel", "UpdateVipLevel"):
            value = table["fields"].get(name)
            if name not in methods and isinstance(value, LuaRef):
                try:
                    closure = reader.lua_closure_upvalues(value.address)
                    pc = closure["pc"]
                    prefix = memory.read(pc - 104, 104)
                    count = struct.unpack_from("<I", prefix, 12)[0]
                    if prefix[9] != 7 or not 1 <= count <= 64:
                        raise FanxiuRuntimeMemoryError("Role VIP 方法 prototype 身份/长度无效")
                    words = list(struct.unpack(f"<{count}I", memory.read(pc, count * 4)))
                    methods[name] = {"function_address": hex(value.address),
                                     "pc": hex(pc),
                                     "instruction_words": words}
                    # Version-pinned GC64 getter: FUNCF; TGETS r1,self,k0;
                    # RET1 r1. Exact operands prove that it returns this field,
                    # rather than deriving/normalizing it or calling Lua code.
                    if name == "GetRoleViplevel" and words == [601, 313, 131404]:
                        if (prefix[10:12] != bytes([1, 2])
                                or struct.unpack_from("<II", prefix, 48) != (1, 0)):
                            raise FanxiuRuntimeMemoryError("Role VIP getter 常量/参数契约失配")
                        constants = struct.unpack_from("<Q", prefix, 32)[0]
                        string_address = struct.unpack("<Q", memory.read(constants - 8, 8))[0]
                        if memory.read(string_address, 10)[9] != 4:
                            raise FanxiuRuntimeMemoryError("Role VIP getter 常量不是 GCstr")
                        methods[name]["verified_field"] = reader.string(string_address)
                except FanxiuRuntimeMemoryError as exc:
                    methods[name] = {"reason": str(exc)}
        meta = table.get("metatable")
        model_ref = table_ref(reader.table(meta)["fields"].get("__index")) if meta else None
    return {
        "ok": True, "source": "runtime_memory.role_model.vip_candidates",
        "semantic_verified": False, "candidates": candidates,
        "method_diagnostics": methods,
        "role_id": _role_profile_values(reader, root)["role_id"],
        "captured_at": datetime.now().astimezone().isoformat(),
        "evidence": {"pid": memory.pid,
                     "process_start_ticks": memory.process_start_ticks,
                     "root_cache_hit": cache_hit},
    }


def read_role_vip_level_snapshot(*, allow_discovery: bool = False) -> dict[str, Any]:
    """Project GetRoleViplevel's proven direct field without invoking Lua.

    The live three-instruction getter binds the field name; account identity
    and process identity are checked in the same memory observation. A changed
    native getter or missing/non-integral field fails closed before VIP use.
    """
    snapshot = read_role_vip_source_snapshot(allow_discovery=allow_discovery)
    getter = snapshot["method_diagnostics"].get("GetRoleViplevel", {})
    field = getter.get("verified_field")
    if field != "viplv":
        raise FanxiuRuntimeMemoryError("Role VIP getter 尚未证明直接读取 viplv")
    raw = snapshot["candidates"].get(field)
    level = as_int(raw)
    if isinstance(raw, bool) or level is None or level < 0:
        raise FanxiuRuntimeMemoryError("Role 当前 VIP 等级不完整")
    return {"ok": True, "available": True, "complete": True,
            "source": "runtime_memory.role.vip_level", "vip_level": level,
            "role_id": snapshot["role_id"], "captured_at": snapshot["captured_at"],
            "evidence": {**snapshot["evidence"], "getter": getter,
                         "verified_field": field}}


def _numeric_table_value(reader: LuaJitReader, value: Any, key: int) -> Any:
    ref = table_ref(value)
    if ref is None:
        return None
    table = reader.table(ref.address)
    if 0 <= key < len(table["array"]):
        result = table["array"][key]
        if result is not None:
            return result
    return table["fields"].get(float(key), table["fields"].get(key))


def _role_progression_values(
    reader: LuaJitReader,
    root_address: int,
) -> tuple[int, int, int]:
    manager_fields = manager_index_fields(
        reader,
        root_address,
        _ROLE_METHODS,
    )
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    role_data_fields = reader.fields(model_fields.get("RoleData"))

    property_wrapper = reader.fields(model_fields.get("PropertyDic"))
    property_data = property_wrapper.get("_dt_")
    current_exp = as_int(
        _numeric_table_value(reader, property_data, _PROPERTY_EXP)
    )
    level = as_int(
        _numeric_table_value(reader, property_data, _PROPERTY_LEVEL)
    )
    if current_exp is None or current_exp < 0 or level is None or level <= 0:
        raise FanxiuRuntimeMemoryError("RoleMgr 修为或境界等级尚未加载")

    realm_cfg = role_data_fields.get("_RealmResourceCfg")
    next_level_cfg = _numeric_table_value(reader, realm_cfg, level + 1)
    required_by_channel = _numeric_table_value(
        reader,
        next_level_cfg,
        _REALM_REQUIRED_EXP_SLOT,
    )
    required_ref = table_ref(required_by_channel)
    if required_ref is None:
        raise FanxiuRuntimeMemoryError("RoleMgr 下一境界修为配置尚未加载")
    required_table = reader.table(required_ref.address)
    required_values = {
        parsed
        for raw in [
            *required_table["array"][1:],
            *required_table["fields"].values(),
        ]
        if (parsed := as_int(raw)) is not None and parsed > 0
    }
    if len(required_values) != 1:
        raise FanxiuRuntimeMemoryError(
            "RoleMgr 下一境界多渠道修为配置不一致，不能安全判定"
        )
    return level, current_exp, required_values.pop()


def _role_profile_values(
    reader: LuaJitReader,
    root_address: int,
) -> dict[str, Any]:
    manager_fields = manager_index_fields(
        reader,
        root_address,
        _ROLE_METHODS,
    )
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    property_wrapper = reader.fields(model_fields.get("PropertyDic"))
    property_data = property_wrapper.get("_dt_")
    role_id = reader.long(model_fields.get("V_ID"))
    battle_score = _numeric_table_value(
        reader,
        property_data,
        _PROPERTY_FIGHT_POWER,
    )
    faze = as_int(model_fields.get("V_FazeId"))
    if (
        role_id is None
        or role_id <= 0
        or not isinstance(battle_score, int | float)
        or battle_score < 0
        or faze is None
    ):
        raise FanxiuRuntimeMemoryError("RoleMgr 当前角色身份、战力或法则尚未加载")
    return {
        "role_id": role_id,
        "name": str(model_fields.get("V_Name") or ""),
        "battle_score": battle_score,
        "faze": faze,
    }


def _role_profile_snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "available": True,
        **_role_profile_values(LuaJitReader(memory), root_address),
        "source": "runtime_memory",
        "protocol": "RoleMgr.Model[V_ID,V_Name,V_FazeId,PropertyDic[FIGHT_POWER]]",
        "reason": None,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_role_profile_from_memory(memory: MumuProcessMemory) -> dict[str, Any]:
    """Read the loaded RoleMgr profile, bound to the caller's process lifetime.

    Cached marker-root lookup can miss the loaded global manager.
    Reacquire a fresh main environment and require its PID/start ticks
    to match the enclosing observation; never let a newer process's profile
    validate an old seat/target snapshot.
    """

    try:
        from backend.core.fanxiu.instrumentation.ui_runtime_context import acquire_lua_global_runtime_context
        context = acquire_lua_global_runtime_context({'RoleMgr'})
        if (context.binding.pid != memory.pid
                or context.binding.process_start_ticks != memory.process_start_ticks):
            raise FanxiuRuntimeMemoryError('RoleMgr 与调用方进程生命周期不一致', code='process_identity_changed')
        context.loaded_manager_fields('RoleMgr', _ROLE_METHODS)
        root = table_ref(context.field(context.binding.environment_address, 'RoleMgr'))
        if root is None:
            raise FanxiuRuntimeMemoryError('RoleMgr 未加载', code='not_loaded')
        _role_profile_values(context.reader, root.address)
        result = _role_profile_snapshot(context.memory, root.address, root_cache_hit=False)
        result['evidence']['root_resolver'] = 'lua_global'
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
            "source": "runtime_memory",
            "reason": reason,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
            },
        }


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
) -> dict[str, Any]:
    level, current_exp, required_exp = _role_progression_values(
        LuaJitReader(memory),
        root_address,
    )
    can_upgrade = current_exp >= required_exp
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "runtime_memory",
        "protocol": (
            "RoleMgr.Model.PropertyDic[EXP,LEVEL]+"
            "RoleData._RealmResourceCfg[level+1][requiredExp]"
        ),
        "state": "realm_upgrade_ready" if can_upgrade else "experience_usable",
        "can_upgrade": can_upgrade,
        "level": level,
        "current_exp": current_exp,
        "required_exp": required_exp,
        "overflow_exp": max(0, current_exp - required_exp),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_role_progression_snapshot() -> dict[str, Any]:
    """Read current account experience from the fresh loaded RoleMgr global.

    Marker discovery can miss an already loaded manager; bind the current main
    Lua environment instead. No Lua calls, game initialization or UI required.
    Account identity and manager class are checked before projecting values.
    can_upgrade mirrors IsCanUpGrade's experience comparison only: native
    condition gates and realm challenges require separate live acceptance.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        from backend.core.fanxiu.instrumentation.ui_runtime_context import acquire_lua_global_runtime_context

        context = acquire_lua_global_runtime_context({"RoleMgr"})
        memory = context.memory
        context.loaded_manager_fields("RoleMgr", _ROLE_METHODS)
        role = table_ref(context.field(context.binding.environment_address, "RoleMgr"))
        if role is None:
            raise FanxiuRuntimeMemoryError("当前 RoleMgr 未加载", code="not_loaded")
        root = role.address
        profile = _role_profile_values(context.reader, root)
        result = _snapshot(
            memory,
            root,
            root_cache_hit=False,
        )
        result["role_id"] = profile["role_id"]
        result["evidence"]["root_source"] = "current_lua_global.RoleMgr"
        result["elapsed_seconds"] = time.perf_counter() - started_at
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
            "source": "runtime_memory",
            "state": "unknown",
            "can_upgrade": None,
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }
