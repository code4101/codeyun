"""韩立指导（原生 CapacityTower / DungeonType 4 NengLiTa）只读快照。

``CapacityTowerDungeonMgr`` 服务日常 Quest1002「接受韩立指导」及其
``CapacityTowerListView``；它不是日常行「挑战或扫荡混沌灵塔」。实测混沌灵塔
连续通关期间该模型保持 ``curTowerId=1426``、``chanllenge=0`` 不变，因此任何
混沌灵塔流程都不得把本快照作为进度、终态或安全门证据。
"""

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
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses


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


def hanli_guidance_admission(snapshot: dict[str, Any], player_level: Any) -> dict[str, Any]:
    """Classify whether today's guidance entry is natively possible.

    Mirrors CapacityTowerDetailView: only the current course has a Start
    button, and only when the player's level reaches its ``dungeonLevel``.
    Missing facts stay ``unknown``; this never authorizes spending.
    """

    gate = snapshot.get("current_course_gate") if isinstance(snapshot, dict) else None
    level = player_level if isinstance(player_level, int) and not isinstance(player_level, bool) else None
    if not isinstance(snapshot, dict) or snapshot.get("complete") is not True:
        return {"state": "unknown", "reason": "course_snapshot_incomplete"}
    if snapshot.get("has_current_course_config") is False:
        return {"state": "all_passed", "reason": "current_course_beyond_config"}
    required = gate.get("dungeon_level") if isinstance(gate, dict) else None
    if not isinstance(required, int) or isinstance(required, bool) or level is None:
        return {"state": "unknown", "reason": "gate_or_player_level_missing"}
    if level < required:
        return {"state": "realm_gated", "required_level": required, "player_level": level}
    return {"state": "challengeable", "required_level": required, "player_level": level}


def _current_course_gate(
    reader: LuaJitReader,
    instance: dict[Any, Any],
    course_id: int,
    *,
    environment_address: int,
    state_address: int | None,
) -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.resource_auto_use import read_runtime_config_rows

    rows = [value for key, value in reader.indexed_list_items(instance.get("towerCfgList"))[0] if key == course_id]
    if len(rows) != 1:
        raise FanxiuRuntimeMemoryError("韩立指导当前课程配置未唯一定位")
    (config,) = read_runtime_config_rows(
        reader, rows, environment_address=environment_address, group_name="CapacityTower",
        table_name="CapacityTower", fields=("id", "dungeonLevel", "dungeonsId", "next"),
        state_address=state_address,
    )
    if as_int(config.get("id")) != course_id:
        raise FanxiuRuntimeMemoryError("韩立指导课程配置身份不一致")
    return {"course_id": course_id, "dungeon_level": as_int(config.get("dungeonLevel")),
            "dungeons_id": as_int(config.get("dungeonsId")), "next": as_int(config.get("next"))}


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
    environment_address: int | None = None,
    state_address: int | None = None,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    instance, model = _capacity_tower_loaded_fields(reader, root_address)
    current = reader.fields(model.get("curTowerMsg"))
    if not current:
        raise FanxiuRuntimeMemoryError(
            "韩立指导同步数据尚未加载；只读探针不会主动请求同步"
        )
    current_course_id = as_int(current.get("curTowerId"))
    claimed_box_reward_id = as_int(current.get("gettedBoxRewardId"))
    if current_course_id is None or current_course_id <= 0:
        raise FanxiuRuntimeMemoryError("韩立指导当前课程字段无效")
    rewards, declared_reward_count = reader.list_items(model.get("rewardList"))
    chain_pass_count = as_int(model.get("chanllenge"))
    if chain_pass_count is None or not 0 <= chain_pass_count <= 20:
        raise FanxiuRuntimeMemoryError("韩立指导本轮连续通关数字段无效")
    complete = (
        claimed_box_reward_id is not None
        and declared_reward_count is not None
        and declared_reward_count == len(rewards)
    )
    max_configured_course_id = as_int(instance.get("maxTowerCfgId"))
    tower_cfg_list = reader.fields(instance.get("towerCfgList"))
    declared_course_config_count = as_int(tower_cfg_list.get("count"))
    config_bounds_complete = bool(
        max_configured_course_id
        and max_configured_course_id > 0
        and declared_course_config_count == max_configured_course_id
    )
    gate: dict[str, Any] | None = None
    gate_error: str | None = None
    if environment_address:
        try:
            gate = _current_course_gate(reader, instance, current_course_id,
                                        environment_address=environment_address, state_address=state_address)
        except Exception as exc:  # Gate is additive evidence; never invent it.
            gate_error = str(exc)[:200]
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "CapacityTowerDungeonMgr.Model.curTowerMsg",
        "current_course_gate": gate,
        "current_course_gate_error": gate_error,
        "business": "hanli_guidance",
        "current_course_id": current_course_id,
        "max_configured_course_id": max_configured_course_id,
        "declared_course_config_count": declared_course_config_count,
        "config_bounds_complete": config_bounds_complete,
        "has_current_course_config": (
            current_course_id <= max_configured_course_id
            if config_bounds_complete and max_configured_course_id is not None
            else None
        ),
        "has_next_course_config": (
            current_course_id < max_configured_course_id
            if config_bounds_complete and max_configured_course_id is not None
            else None
        ),
        "claimed_box_reward_id": claimed_box_reward_id,
        # Native ``chanllenge``: the in-dungeon consecutive-win counter of the
        # Hanli guidance dungeon (DungeonSceneNengLiTa).  It is not a
        # persisted daily attempt counter and says nothing about 混沌灵塔.
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


def read_hanli_guidance_course_snapshot() -> dict[str, Any]:
    """Read the already-loaded 韩立指导 course model without GUI or game actions.

    Identity: ``CapacityTowerDungeonMgr`` = Quest1002 韩立指导 (daily,
    ``EnterDgType|4``).  It must never be used as 混沌灵塔 progress.

    The adapter only walks external process memory.  In particular, it never
    calls ``Inst_get`` or sends ``CM_SyncCapacityTower`` when the model has not
    been loaded by normal gameplay.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        root_address, root_cache_hit, environment = resolve_lua_global_manager_root(
            memory,
            manager_key="capacity_tower",
            state_address=state_address,
            global_name="CapacityTowerDungeonMgr",
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
            environment_address=environment,
            state_address=state_address,
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


__all__ = ["hanli_guidance_admission", "read_hanli_guidance_course_snapshot"]
