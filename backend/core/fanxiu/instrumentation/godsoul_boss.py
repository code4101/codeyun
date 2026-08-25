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
    resolve_lua_global_manager_root,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _lua_addresses,
)


_GODSOUL_BOSS_MARKER = b"LuaGodSoulBossMgr"
_GODSOUL_BOSS_METHODS = frozenset(
    {
        "LuaGodSoulBossMgr",
        "Inst_get",
    }
)


def _godsoul_boss_sync_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    data_fields = _godsoul_boss_data_fields(reader, root_address)
    sync_fields = reader.fields(data_fields.get("_GodSoulBossData"))
    if "roundRewardInfoList" not in sync_fields:
        raise FanxiuRuntimeMemoryError(
            "GodSoulBossMgr 排名奖励数据尚未加载",
            code="data_not_loaded",
        )
    return sync_fields


def _godsoul_boss_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(
        reader,
        root_address,
        _GODSOUL_BOSS_METHODS,
    )
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("Data"))
    if "_GodSoulBossData" not in data_fields:
        raise FanxiuRuntimeMemoryError(
            "GodSoulBossMgr 同步数据尚未加载",
            code="data_not_loaded",
        )
    return data_fields


def _resolve_godsoul_boss_root(
    memory: MumuProcessMemory,
    *,
    validate: Any,
) -> tuple[int, bool, str]:
    """Resolve the exact loaded global before legacy heap-marker discovery."""

    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="godsoul-boss",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="GodSoulBossMgr",
            required_methods=_GODSOUL_BOSS_METHODS,
            validate=validate,
        )
        return root, cache_hit, "lua_global"
    except FanxiuRuntimeMemoryError as exc:
        # A proven global with unloaded business data is authoritative.  A
        # heap walk cannot initialize it and would only hide the real state.
        if exc.code == "data_not_loaded":
            raise
    root, cache_hit = resolve_manager_root(
        memory,
        manager_key="godsoul-boss",
        marker=_GODSOUL_BOSS_MARKER,
        required_methods=_GODSOUL_BOSS_METHODS,
        validate=validate,
    )
    return root, cache_hit, "constructor_marker"


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    sync_fields = _godsoul_boss_sync_fields(reader, root_address)
    round_items, round_count = reader.list_items(
        sync_fields.get("roundRewardInfoList")
    )
    rewards: list[dict[str, Any]] = []
    for round_value in round_items:
        round_fields = reader.fields(round_value)
        round_number = as_int(round_fields.get("round"))
        reward_items, reward_count = reader.list_items(
            round_fields.get("rewardInfoList")
        )
        for reward_value in reward_items:
            reward_fields = reader.fields(reward_value)
            rank = as_int(reward_fields.get("rank"))
            activity_id = as_int(reward_fields.get("activityId"))
            claimed_value = reward_fields.get("reward")
            rewards.append(
                {
                    "round": round_number,
                    "activity_id": activity_id,
                    "rank": rank,
                    "sum_jie": as_int(reward_fields.get("sumJie")),
                    "claimed": (
                        claimed_value
                        if isinstance(claimed_value, bool)
                        else None
                    ),
                    "round_reward_count": reward_count,
                }
            )

    complete = (
        round_count is not None
        and round_count >= 0
        and all(
            isinstance(item.get("round"), int)
            and isinstance(item.get("activity_id"), int)
            and isinstance(item.get("rank"), int)
            and item["rank"] > 0
            and isinstance(item.get("claimed"), bool)
            for item in rewards
        )
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": (
            "GodSoulBossMgr.Model.Data._GodSoulBossData."
            "roundRewardInfoList"
        ),
        "round_count": round_count,
        "rewards": rewards,
        "already_claimed": any(item.get("claimed") is True for item in rewards),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_godsoul_boss_reward_snapshot() -> dict[str, Any]:
    """Read already-loaded per-round ranks without invoking Lua or sending data."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root, root_cache_hit, resolver = _resolve_godsoul_boss_root(
            memory,
            validate=lambda reader, address: _godsoul_boss_sync_fields(
                reader,
                address,
            ),
        )
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


def _challenge_snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data_fields = _godsoul_boss_data_fields(reader, root_address)
    sync_fields = reader.fields(data_fields.get("_GodSoulBossData"))
    enter_fields = reader.fields(data_fields.get("_GodSoulBossEnter"))
    settle_fields = reader.fields(data_fields.get("_GodSoulBossSettle"))

    def numeric_dictionary(name: str) -> dict[int, int | float]:
        result: dict[int, int | float] = {}
        for raw_key, raw_value in reader.dictionary_fields(
            sync_fields.get(name)
        ).items():
            key = as_int(raw_key)
            if key is None or not isinstance(raw_value, (int, float)):
                continue
            result[key] = raw_value
        return result

    points: dict[int, int] = {}
    for raw_key, raw_value in reader.dictionary_fields(
        sync_fields.get("selfPointOfMapId")
    ).items():
        key = as_int(raw_key)
        value = reader.long(raw_value)
        if key is not None and value is not None:
            points[key] = value

    settle_map_id = as_int(settle_fields.get("mapId"))
    settle_damage = settle_fields.get("totalDamage")
    settled = bool(
        as_int(settle_fields.get("code")) == 0
        and settle_map_id is not None
        and settle_map_id > 0
        and isinstance(settle_damage, (int, float))
        and settle_damage > 0
    )
    return {
        "ok": True,
        "available": True,
        "complete": bool(sync_fields),
        "source": "runtime_memory",
        "protocol": "GodSoulBossMgr.Model.Data entry/settle/runtime maps",
        "entered_map_id": as_int(enter_fields.get("mapId")),
        "enter_code": as_int(enter_fields.get("code")),
        "settled": settled,
        "settlement": {
            "code": as_int(settle_fields.get("code")),
            "map_id": settle_map_id,
            "rank": as_int(settle_fields.get("rank")),
            "total_damage": settle_damage,
            "desc": as_int(settle_fields.get("desc")),
        },
        "boss_hp_by_map_id": numeric_dictionary("bossHpOfMapId"),
        "self_point_by_map_id": points,
        "self_rank_by_map_id": numeric_dictionary("selfRankOfMapId"),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_godsoul_boss_challenge_snapshot() -> dict[str, Any]:
    """Read loaded entry/settlement facts without invoking the game Runtime."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root, root_cache_hit, resolver = _resolve_godsoul_boss_root(
            memory,
            validate=lambda reader, address: _godsoul_boss_data_fields(
                reader,
                address,
            ),
        )
        result = _challenge_snapshot(
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


__all__ = [
    "read_godsoul_boss_challenge_snapshot",
    "read_godsoul_boss_reward_snapshot",
]
