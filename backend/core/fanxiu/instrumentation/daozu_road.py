from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _lua_addresses,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)


_DAOZU_ROAD_MARKER = b"LuaDaozuroadMgr"
_DAOZU_ROAD_METHODS = frozenset(
    {
        "LuaDaozuroadMgr",
        "Inst_get",
        "GetDayRemainCount",
    }
)
_DAOZU_ROAD_GLOBAL_NAME = "DaozuroadMgr"


def _daozu_road_loaded_fields(
    reader: LuaJitReader,
    root_address: int,
) -> tuple[dict[Any, Any], dict[Any, Any], dict[Any, Any]]:
    manager = manager_index_fields(
        reader,
        root_address,
        _DAOZU_ROAD_METHODS,
    )
    instance = reader.fields(manager.get("inst"))
    if not instance:
        raise FanxiuRuntimeMemoryError(
            "DaozuroadMgr 已加载，但实例尚未初始化",
            code="data_not_loaded",
        )
    model = reader.fields(instance.get("Model"))
    if not model:
        raise FanxiuRuntimeMemoryError(
            "DaozuroadMgr.Model 尚未初始化",
            code="data_not_loaded",
        )
    data = reader.fields(model.get("DaozuroadData"))
    if not data:
        raise FanxiuRuntimeMemoryError(
            "DaozuroadMgr.Model.DaozuroadData 尚未初始化",
            code="data_not_loaded",
        )
    return instance, model, data


def _daozu_road_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    _instance, _model, data = _daozu_road_loaded_fields(reader, root_address)
    return data


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
    manager_resolver: str = "constructor_marker",
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    _instance, model, data = _daozu_road_loaded_fields(reader, root_address)

    current_level_id = as_int(data.get("current"))
    challenge_max_level_id = as_int(data.get("challengeMax"))
    daily_pass_count = as_int(data.get("passCount"))
    dao_level = as_int(data.get("level"))
    yesterday_level_id = as_int(data.get("yesterdayId"))
    if (
        current_level_id is None
        or current_level_id <= 0
        or challenge_max_level_id is None
        or challenge_max_level_id < 0
        or daily_pass_count is None
        or daily_pass_count < 0
        or dao_level is None
        or dao_level <= 0
    ):
        raise FanxiuRuntimeMemoryError("道祖之路同步字段尚未完整加载或字段无效")
    if current_level_id != challenge_max_level_id + 1:
        raise FanxiuRuntimeMemoryError(
            "道祖之路 current/challengeMax 关系无效，拒绝使用可能错配的 Manager"
        )

    # GetMaxDayCount lazily stores the live config value here.  Reading the
    # table must not invoke that Lua method: when the field is absent, retain
    # the synchronized pass count but leave the daily limit incomplete.
    daily_limit = as_int(data.get("maxDayCount"))
    if daily_limit is not None and daily_limit <= 0:
        raise FanxiuRuntimeMemoryError("道祖之路每日上限缓存字段无效")
    if daily_limit is not None and daily_pass_count > daily_limit:
        raise FanxiuRuntimeMemoryError("道祖之路今日通过数超过每日上限")

    chain_pass_count = as_int(model.get("challengeNum"))
    if chain_pass_count is not None and chain_pass_count < 0:
        raise FanxiuRuntimeMemoryError("道祖之路本轮连续通关数字段无效")

    complete = daily_limit is not None
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "DaozuroadMgr.Model.DaozuroadData",
        "current_level_id": current_level_id,
        "challenge_max_level_id": challenge_max_level_id,
        "daily_pass_count": daily_pass_count,
        "dao_level": dao_level,
        "yesterday_level_id": yesterday_level_id,
        "daily_limit": daily_limit,
        "daily_remaining": (
            daily_limit - daily_pass_count if daily_limit is not None else None
        ),
        # This counter only describes the currently open dungeon chain.  It is
        # not the persisted daily pass count and may be absent outside battle.
        "chain_pass_count": chain_pass_count,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
            "manager_resolver": manager_resolver,
        },
    }


def _resolve_daozu_road_root(
    memory: MumuProcessMemory,
) -> tuple[int, bool, str, dict[str, float]]:
    """Resolve the exact loaded global before bounded marker discovery."""

    timings: dict[str, float] = {}
    state_started_at = time.perf_counter()
    state_address = int(_lua_addresses(memory)["state"], 16)
    timings["lua_state_seconds"] = time.perf_counter() - state_started_at

    resolve_started_at = time.perf_counter()
    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="daozu-road",
            state_address=state_address,
            global_name=_DAOZU_ROAD_GLOBAL_NAME,
            required_methods=_DAOZU_ROAD_METHODS,
            validate=lambda reader, root: _daozu_road_data_fields(reader, root),
        )
        timings["manager_resolution_seconds"] = (
            time.perf_counter() - resolve_started_at
        )
        return root, cache_hit, "lua_global", timings
    except FanxiuRuntimeMemoryError as exc:
        if exc.code == "data_not_loaded":
            raise

    root, cache_hit = resolve_manager_root(
        memory,
        manager_key="daozu-road",
        marker=_DAOZU_ROAD_MARKER,
        required_methods=_DAOZU_ROAD_METHODS,
        validate=lambda reader, candidate: _daozu_road_data_fields(
            reader, candidate
        ),
    )
    timings["manager_resolution_seconds"] = time.perf_counter() - resolve_started_at
    return root, cache_hit, "constructor_marker", timings


def read_daozu_road_snapshot() -> dict[str, Any]:
    """Read the already-loaded Daozu-road model without GUI or game actions.

    The adapter only walks external process memory.  It never calls
    ``Inst_get``/``GetDayRemainCount`` and never sends ``CM_DaoRealmSync``.
    Data that normal gameplay has not initialized is reported as unavailable
    or incomplete instead of being actively loaded.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    stage_timings: dict[str, float] = {}
    try:
        discovery_started_at = time.perf_counter()
        memory = MumuProcessMemory.discover_cached()
        stage_timings["process_discovery_seconds"] = (
            time.perf_counter() - discovery_started_at
        )
        root_address, root_cache_hit, manager_resolver, resolve_timings = (
            _resolve_daozu_road_root(memory)
        )
        stage_timings.update(resolve_timings)
        decode_started_at = time.perf_counter()
        result = _snapshot(
            memory,
            root_address,
            root_cache_hit=root_cache_hit,
            manager_resolver=manager_resolver,
        )
        stage_timings["snapshot_decode_seconds"] = (
            time.perf_counter() - decode_started_at
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result["stage_timings"] = stage_timings
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
            "stage_timings": stage_timings,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }


__all__ = ["read_daozu_road_snapshot"]
