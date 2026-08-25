from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_manager_root,
)


_XUANHUANG_MARKER = b"LuaGodsoultowerMgr"
_XUANHUANG_METHODS = frozenset(
    {
        "LuaGodsoultowerMgr",
        "Inst_get",
    }
)


def _xuanhuang_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(
        reader,
        root_address,
        _XUANHUANG_METHODS,
    )
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("GodsoultowerData"))
    if "leftChangeTimes" not in data_fields:
        raise FanxiuRuntimeMemoryError(
            "GodsoultowerMgr 挑战页计数尚未加载"
        )
    return data_fields


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data_fields = _xuanhuang_data_fields(reader, root_address)
    remaining = as_int(data_fields.get("leftChangeTimes"))
    complete = remaining is not None and remaining >= 0
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": (
            "GodsoultowerMgr.Model.GodsoultowerData.leftChangeTimes"
        ),
        "counter_loaded": True,
        "remaining": remaining,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_xuanhuang_snapshot() -> dict[str, Any]:
    """Read the locally loaded Xuanhuang remaining count without OCR."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root, root_cache_hit = resolve_manager_root(
            memory,
            manager_key="xuanhuang",
            marker=_XUANHUANG_MARKER,
            required_methods=_XUANHUANG_METHODS,
            validate=lambda reader, address: _xuanhuang_data_fields(
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
            "counter_loaded": False,
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
