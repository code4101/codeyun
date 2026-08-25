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


_REVENUE_MARKER = b"LuaRevenueMgr"
_REVENUE_METHODS = frozenset(
    {
        "LuaRevenueMgr",
        "GetRevenueDataInfo",
        "Inst_get",
    }
)
_XIANPIN_SKILL_ACTIVITY_ID = 670002


def _long_or_int(reader: LuaJitReader, value: Any) -> int | None:
    if isinstance(value, LuaRef):
        return reader.long(value)
    return as_int(value)


def _revenue_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(
        reader,
        root_address,
        _REVENUE_METHODS,
    )
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("RevenueData"))
    if not reader.dictionary_fields(data_fields.get("V_ActivityDic")):
        raise FanxiuRuntimeMemoryError("Revenue Runtime 活动表尚未初始化")
    return data_fields


def _activity_fields(
    reader: LuaJitReader,
    data_fields: dict[Any, Any],
    activity_id: int,
) -> dict[Any, Any]:
    activities = reader.dictionary_fields(data_fields.get("V_ActivityDic"))
    for key, value in activities.items():
        if as_int(key) == int(activity_id):
            return reader.fields(value)
    raise FanxiuRuntimeMemoryError(
        f"Revenue Runtime 中没有活动 {activity_id}"
    )


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
    now_epoch_ms: int | None = None,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data_fields = _revenue_data_fields(reader, root_address)
    activity_fields = _activity_fields(
        reader,
        data_fields,
        _XIANPIN_SKILL_ACTIVITY_ID,
    )
    play_fields = reader.fields(activity_fields.get("revenuePlayVO"))
    base_fields = reader.fields(activity_fields.get("revenueBaseVO"))
    free_flag = play_fields.get("free")
    next_free_time_ms = _long_or_int(
        reader,
        play_fields.get("nextFreeTime"),
    )
    free_cd_minutes = as_int(base_fields.get("freeCD"))
    captured_epoch_ms = int(
        now_epoch_ms
        if now_epoch_ms is not None
        else time.time() * 1000
    )
    complete = (
        isinstance(free_flag, bool)
        and next_free_time_ms is not None
        and free_cd_minutes is not None
        and free_cd_minutes > 0
    )
    free_available = (
        bool(free_flag)
        or (
            bool(next_free_time_ms)
            and captured_epoch_ms >= int(next_free_time_ms)
        )
        if complete
        else None
    )
    remaining_seconds = (
        max(
            0,
            int(
                (int(next_free_time_ms) - captured_epoch_ms + 999)
                // 1000
            ),
        )
        if complete and not free_available
        else 0 if complete else None
    )
    next_free_at = (
        datetime.fromtimestamp(
            int(next_free_time_ms) / 1000
        ).strftime("%Y-%m-%d %H:%M:%S")
        if next_free_time_ms
        else None
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": (
            "RevenueMgr.Model.RevenueData.V_ActivityDic"
            "[670002].revenuePlayVO"
        ),
        "activity_id": _XIANPIN_SKILL_ACTIVITY_ID,
        "free_available": free_available,
        "free_flag": free_flag if isinstance(free_flag, bool) else None,
        "free_cd_minutes": free_cd_minutes,
        "next_free_time_epoch_ms": next_free_time_ms,
        "next_free_at": next_free_at,
        "remaining_seconds": remaining_seconds,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": captured_epoch_ms / 1000,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_xianfu_skill_draw_snapshot() -> dict[str, Any]:
    """Read the Xianpin skill free-draw state without packets or GUI OCR."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover()
        root, root_cache_hit = resolve_manager_root(
            memory,
            manager_key="xianfu-revenue",
            marker=_REVENUE_MARKER,
            required_methods=_REVENUE_METHODS,
            validate=lambda reader, address: _revenue_data_fields(
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
