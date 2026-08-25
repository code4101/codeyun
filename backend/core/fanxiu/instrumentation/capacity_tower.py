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


_CAPACITY_TOWER_MARKER = b"LuaCapacityTowerDungeonMgr"
_CAPACITY_TOWER_METHODS = frozenset(
    {
        "LuaCapacityTowerDungeonMgr",
        "Inst_get",
        "EnterCapacityTower",
    }
)


def _capacity_tower_model_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    _instance, model = _capacity_tower_loaded_fields(reader, root_address)
    return model


def _capacity_tower_loaded_fields(
    reader: LuaJitReader,
    root_address: int,
) -> tuple[dict[Any, Any], dict[Any, Any]]:
    manager = manager_index_fields(
        reader,
        root_address,
        _CAPACITY_TOWER_METHODS,
    )
    instance = reader.fields(manager.get("inst"))
    if not instance:
        raise FanxiuRuntimeMemoryError(
            "CapacityTowerDungeonMgr 已加载，但实例尚未初始化"
        )
    model = reader.fields(instance.get("Model"))
    if not {"chanllenge", "rewardList"}.issubset(model):
        raise FanxiuRuntimeMemoryError(
            "CapacityTowerDungeonMgr.Model 尚未初始化"
        )
    return instance, model


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    instance, model = _capacity_tower_loaded_fields(reader, root_address)
    current = reader.fields(model.get("curTowerMsg"))
    if not current:
        raise FanxiuRuntimeMemoryError(
            "混沌灵塔同步数据尚未加载；只读探针不会主动请求同步"
        )
    current_tower_id = as_int(current.get("curTowerId"))
    claimed_box_reward_id = as_int(current.get("gettedBoxRewardId"))
    if current_tower_id is None or current_tower_id <= 0:
        raise FanxiuRuntimeMemoryError("混沌灵塔当前层字段无效")
    rewards, declared_reward_count = reader.list_items(model.get("rewardList"))
    chain_pass_count = as_int(model.get("chanllenge"))
    if chain_pass_count is None or not 0 <= chain_pass_count <= 20:
        raise FanxiuRuntimeMemoryError("混沌灵塔本轮连续通关数字段无效")
    complete = (
        claimed_box_reward_id is not None
        and declared_reward_count is not None
        and declared_reward_count == len(rewards)
    )
    max_configured_tower_id = as_int(instance.get("maxTowerCfgId"))
    tower_cfg_list = reader.fields(instance.get("towerCfgList"))
    declared_tower_config_count = as_int(tower_cfg_list.get("count"))
    config_bounds_complete = bool(
        max_configured_tower_id
        and max_configured_tower_id > 0
        and declared_tower_config_count == max_configured_tower_id
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "CapacityTowerDungeonMgr.Model.curTowerMsg",
        "current_tower_id": current_tower_id,
        "max_configured_tower_id": max_configured_tower_id,
        "declared_tower_config_count": declared_tower_config_count,
        "config_bounds_complete": config_bounds_complete,
        "has_current_tower_config": (
            current_tower_id <= max_configured_tower_id
            if config_bounds_complete and max_configured_tower_id is not None
            else None
        ),
        "has_next_tower_config": (
            current_tower_id < max_configured_tower_id
            if config_bounds_complete and max_configured_tower_id is not None
            else None
        ),
        "claimed_box_reward_id": claimed_box_reward_id,
        # This is the client's current in-dungeon consecutive-win counter.  It
        # is cleared on scene Enter (and by the special failure panel), not by
        # the ordinary 61701 server-exit path.  It is still not a persisted
        # daily attempt counter.
        "chain_pass_count": chain_pass_count,
        "reward_result_count": len(rewards),
        "declared_reward_result_count": declared_reward_count,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_capacity_tower_snapshot() -> dict[str, Any]:
    """Read the already-loaded mixed-tower model without GUI or game actions.

    The adapter only walks external process memory.  In particular, it never
    calls ``Inst_get`` or sends ``CM_SyncCapacityTower`` when the model has not
    been loaded by normal gameplay.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root_address, root_cache_hit = resolve_manager_root(
            memory,
            manager_key="capacity_tower",
            marker=_CAPACITY_TOWER_MARKER,
            required_methods=_CAPACITY_TOWER_METHODS,
            validate=lambda reader, root: _capacity_tower_model_fields(
                reader,
                root,
            ),
        )
        result = _snapshot(
            memory,
            root_address,
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


__all__ = ["read_capacity_tower_snapshot"]
