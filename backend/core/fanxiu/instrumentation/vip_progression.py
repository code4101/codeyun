from __future__ import annotations

"""Strict read-only projection of the current VIP experience value.

The reverse source establishes the authoritative chain as
``ChargeMgr.Inst_get().Model.vipExp``.  ``SM_SyncVipInfoFun`` and
``SM_VipUpgradeFun`` update that field through ``ChargeModel:SetVipExp``;
``GetVipExp`` merely returns the field.  This adapter never invokes either
Lua method or a network command.
"""

from datetime import datetime
import time
from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)


CHARGE_MARKER = b"LuaChargeMgr"
CHARGE_METHODS = frozenset({"Inst_get", "LuaChargeMgr"})


def _required_fields(
    reader: LuaJitReader,
    value: Any,
    names: tuple[str, ...],
    context: str,
) -> dict[Any, Any]:
    fields = reader.fields(value)
    missing = [name for name in names if name not in fields]
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"{context} 尚未初始化，缺少字段：{','.join(missing)}",
            code="data_not_loaded",
        )
    return fields


def charge_vip_exp_value(reader: LuaJitReader, root_address: int) -> int:
    """Decode and validate ``ChargeModel.vipExp`` from one Manager root.

    ``ChargeModel`` initializes ``vipExp`` to zero before the first server
    sync and exposes no separate synchronization watermark.  A zero therefore
    cannot prove whether the account genuinely has zero progress or the model
    is still at its constructor default.  It is intentionally rejected rather
    than returned as an authoritative snapshot.
    """

    manager = manager_index_fields(reader, root_address, CHARGE_METHODS)
    instance = _required_fields(reader, manager.get("inst"), ("Model",), "ChargeMgr")
    model = _required_fields(
        reader,
        instance["Model"],
        ("ChargeData", "vipExp"),
        "ChargeModel",
    )
    # ChargeData.vipLevelCfg is constructed together with the VIP model and
    # acts as a narrow schema guard against resolving an unrelated table.
    _required_fields(reader, model["ChargeData"], ("vipLevelCfg",), "ChargeData")
    vip_exp = as_int(model.get("vipExp"))
    if vip_exp is None or vip_exp < 0:
        raise FanxiuRuntimeMemoryError(
            "ChargeModel.vipExp 类型或取值无效",
            code="snapshot_incomplete",
        )
    if vip_exp == 0:
        raise FanxiuRuntimeMemoryError(
            "ChargeModel.vipExp 仍为构造默认值 0，缺少服务端同步水位，不能证明当前值",
            code="data_not_loaded",
        )
    return vip_exp


def read_charge_vip_exp_snapshot(
    *,
    allow_discovery: bool = False,
) -> dict[str, Any]:
    """Read current VIP experience without Lua calls or GUI side effects.

    Normal reads use a cached process identity and resolve the already-loaded
    ``ChargeMgr`` from the main Lua global table.  If that path fails, a
    process-bound marker cache is consulted.  Expensive marker discovery is
    enabled only by the explicit ``allow_discovery`` diagnostic option.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    resolver = "lua_global"
    resolution_path = ["process_cache", "lua_global_cache", "lua_global"]
    try:
        memory = (
            MumuProcessMemory.discover()
            if allow_discovery
            else MumuProcessMemory.discover_cached(fallback_to_discovery=False)
        )
        state_address = int(_lua_addresses(memory)["state"], 16)
        try:
            root, cache_hit, environment = resolve_lua_global_manager_root(
                memory,
                manager_key="charge-vip-exp",
                state_address=state_address,
                global_name="ChargeMgr",
                required_methods=CHARGE_METHODS,
                validate=charge_vip_exp_value,
            )
        except FanxiuRuntimeMemoryError as exc:
            # A successfully resolved ChargeMgr whose model is incomplete
            # cannot be repaired by finding the same Manager through its
            # constructor marker.  Preserve the authoritative loading/error
            # classification instead of replacing it with a cache miss.
            if exc.code in {"data_not_loaded", "snapshot_incomplete"}:
                raise
            # This compatibility path still checks a PID/start_ticks-bound
            # cache on ordinary reads.  Only an explicitly requested
            # diagnostic read may start marker discovery.
            resolver = "constructor_marker"
            resolution_path.extend(
                ["marker_cache", "marker_discovery"]
                if allow_discovery
                else ["marker_cache"]
            )
            root, cache_hit = resolve_manager_root(
                memory,
                manager_key="charge-vip-exp-marker",
                marker=CHARGE_MARKER,
                required_methods=CHARGE_METHODS,
                validate=charge_vip_exp_value,
                allow_discovery=allow_discovery,
            )
            environment = None
        vip_exp = charge_vip_exp_value(LuaJitReader(memory), root)
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory.charge.vip_exp",
            "protocol": "ChargeMgr.inst.Model.vipExp",
            "vip_exp": vip_exp,
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_address": f"0x{root:x}",
                "root_cache_hit": cache_hit,
                "root_resolver": resolver,
                "lua_environment": environment,
                "resolution_path": resolution_path,
                "read_only": True,
            },
        }
    except Exception as exc:
        reason = str(exc)
        reason_code = (
            exc.code if isinstance(exc, FanxiuRuntimeMemoryError) else "runtime_unavailable"
        )
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory.charge.vip_exp",
            "vip_exp": None,
            "reason": reason,
            "reason_code": reason_code,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "root_resolver": resolver,
                "resolution_path": resolution_path,
                "read_only": True,
            },
        }


__all__ = [
    "CHARGE_MARKER",
    "CHARGE_METHODS",
    "charge_vip_exp_value",
    "read_charge_vip_exp_snapshot",
]
