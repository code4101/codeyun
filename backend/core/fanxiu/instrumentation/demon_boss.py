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
)


_DEMON_BOSS_MARKER = b"LuaDemonBossMgr"
_DEMON_BOSS_METHODS = frozenset(
    {
        "LuaDemonBossMgr",
        "Inst_get",
        "UpdateDemonBossSync",
    }
)


def _long_or_int(reader: LuaJitReader, value: Any) -> int | None:
    if isinstance(value, LuaRef):
        return reader.long(value)
    return as_int(value)


def _demon_boss_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(
        reader,
        root_address,
        _DEMON_BOSS_METHODS,
    )
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("DemonBossData"))
    sync_fields = reader.fields(data_fields.get("V_DemonBossSync"))
    if as_int(sync_fields.get("leftTimes")) is None:
        raise FanxiuRuntimeMemoryError(
            "DemonBossMgr 同步数据尚未初始化"
        )
    return data_fields


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data_fields = _demon_boss_data_fields(reader, root_address)
    sync_fields = reader.fields(data_fields.get("V_DemonBossSync"))
    activity_fields = reader.fields(data_fields.get("V_ActivityVO"))
    left_times = as_int(sync_fields.get("leftTimes"))
    activity_state = as_int(activity_fields.get("state"))
    activity_start_ms = _long_or_int(
        reader,
        activity_fields.get("startTime"),
    )
    activity_end_ms = _long_or_int(
        reader,
        activity_fields.get("endTime"),
    )
    complete = left_times is not None
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": (
            "DemonBossMgr.Model.DemonBossData."
            "V_DemonBossSync"
        ),
        "left_times": left_times,
        "exhausted": left_times == 0 if complete else None,
        "buy_inspire_times": as_int(
            sync_fields.get("buyInspireTimes")
        ),
        "current_corps_inspire_times": as_int(
            sync_fields.get("curCorpsByInspireTimes")
        ),
        "next_different_cross_epoch_ms": _long_or_int(
            reader,
            sync_fields.get("nextDifferentCrossTime"),
        ),
        "activity_loaded": bool(activity_fields),
        "activity_state": activity_state,
        "activity_start_epoch_ms": activity_start_ms,
        "activity_end_epoch_ms": activity_end_ms,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_demon_boss_snapshot() -> dict[str, Any]:
    """Read the locally loaded Demon Boss participation counters."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root, root_cache_hit = resolve_manager_root(
            memory,
            manager_key="demon-boss",
            marker=_DEMON_BOSS_MARKER,
            required_methods=_DEMON_BOSS_METHODS,
            validate=lambda reader, address: _demon_boss_data_fields(
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
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks
                    if memory is not None
                    else None
                ),
            },
        }
