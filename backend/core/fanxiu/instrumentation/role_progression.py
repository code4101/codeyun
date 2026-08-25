from __future__ import annotations

import time
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
    """Read the current account identity and combat profile without GUI state."""

    try:
        root, root_cache_hit = resolve_manager_root(
            memory,
            manager_key="role-profile",
            marker=_ROLE_MARKER,
            required_methods=_ROLE_METHODS,
            validate=lambda reader, address: _role_profile_values(reader, address),
        )
        return _role_profile_snapshot(
            memory,
            root,
            root_cache_hit=root_cache_hit,
        )
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
    """Read whether role experience can still be consumed without OCR."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root, root_cache_hit = resolve_manager_root(
            memory,
            manager_key="role-progression",
            marker=_ROLE_MARKER,
            required_methods=_ROLE_METHODS,
            validate=lambda reader, address: _role_progression_values(
                reader,
                address,
            ),
        )
        result = _snapshot(
            memory,
            root,
            root_cache_hit=root_cache_hit,
        )
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
