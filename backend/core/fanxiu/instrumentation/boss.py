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
    resolve_lua_global_manager_root,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _lua_addresses,
)


_BOSS_MARKER = b"LuaBossMgr"
_BOSS_METHODS = frozenset(
    {
        "LuaBossMgr",
        "Inst_get",
        "GetBossChallengeTimesInfo",
    }
)


def _long_or_int(reader: LuaJitReader, value: Any) -> int | None:
    if isinstance(value, LuaRef):
        return reader.long(value)
    return as_int(value)


def _boss_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(
        reader,
        root_address,
        _BOSS_METHODS,
    )
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("BossData"))
    if "fatigue" not in data_fields:
        raise FanxiuRuntimeMemoryError(
            "BossMgr BossData 尚未初始化",
            code="data_not_loaded",
        )
    return data_fields


def _resolve_boss_root(
    memory: MumuProcessMemory,
) -> tuple[int, bool, str]:
    """Resolve the loaded BossMgr global before marker compatibility scan."""

    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="boss",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="BossMgr",
            required_methods=_BOSS_METHODS,
            validate=lambda reader, address: _boss_data_fields(
                reader,
                address,
            ),
        )
        return root, cache_hit, "lua_global"
    except FanxiuRuntimeMemoryError as exc:
        # A proven BossMgr whose model is not loaded is already a precise
        # result. Marker scanning cannot initialize it and would only turn a
        # fast failure into a slow heap walk.
        if exc.code == "data_not_loaded":
            raise
    root, cache_hit = resolve_manager_root(
        memory,
        manager_key="boss",
        marker=_BOSS_MARKER,
        required_methods=_BOSS_METHODS,
        validate=lambda reader, address: _boss_data_fields(
            reader,
            address,
        ),
    )
    return root, cache_hit, "constructor_marker"


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
    now_epoch_ms: int | None = None,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data_fields = _boss_data_fields(reader, root_address)
    boss_list_fields = reader.fields(data_fields.get("bossInfoVOS"))
    boss_list_count = as_int(boss_list_fields.get("count"))
    reward_remaining = as_int(data_fields.get("fatigue"))
    big_boss_reward_remaining = as_int(
        data_fields.get("bigBossRecRewardTimes")
    )
    kill_reward_remaining = as_int(
        data_fields.get("bigBossRecRewardTimesKill")
    )
    big_boss_fields = reader.fields(data_fields.get("bigBossInfoVo"))
    next_refresh_epoch_ms = _long_or_int(
        reader,
        big_boss_fields.get("bigBossNextRefreshTime"),
    )
    captured_epoch_ms = int(
        now_epoch_ms
        if now_epoch_ms is not None
        else time.time() * 1000
    )
    list_loaded = bool(
        boss_list_count is not None and boss_list_count > 0
    )
    complete = list_loaded and reward_remaining is not None
    refresh_remaining_seconds = (
        max(
            0,
            int(
                (
                    int(next_refresh_epoch_ms)
                    - captured_epoch_ms
                    + 999
                )
                // 1000
            ),
        )
        if next_refresh_epoch_ms is not None
        else None
    )
    next_refresh_at = (
        datetime.fromtimestamp(
            int(next_refresh_epoch_ms) / 1000
        ).strftime("%Y-%m-%d %H:%M:%S")
        if next_refresh_epoch_ms
        else None
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "BossMgr.Model.BossData",
        "list_loaded": list_loaded,
        "boss_list_count": boss_list_count,
        "reward_remaining": reward_remaining,
        "big_boss_reward_remaining": big_boss_reward_remaining,
        "kill_reward_remaining": kill_reward_remaining,
        "big_boss_dead": (
            big_boss_fields.get("isDead")
            if isinstance(big_boss_fields.get("isDead"), bool)
            else None
        ),
        "big_boss_group_id": as_int(
            big_boss_fields.get("bossGroupId")
        ),
        "big_boss_id": as_int(big_boss_fields.get("bossId")),
        "next_refresh_epoch_ms": next_refresh_epoch_ms,
        "next_refresh_at": next_refresh_at,
        "refresh_remaining_seconds": refresh_remaining_seconds,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": captured_epoch_ms / 1000,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_boss_snapshot() -> dict[str, Any]:
    """Read the locally loaded boss counters without GUI OCR."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root, root_cache_hit, resolver = _resolve_boss_root(memory)
        result = _snapshot(
            memory,
            root,
            root_cache_hit=root_cache_hit,
        )
        result["evidence"]["resolver"] = resolver
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
