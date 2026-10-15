"""封魔之战（原生 WildBossMgr / BossType 6 上古之魔）只读快照。

日常 Quest1023「参与封魔之战」的 playType 为 WildBoss，与秘境封魔杀
(HiddenWorld) 及坠魔谷 BossMgr 都不是同一系统。本适配器只遍历外部进程
中已存在的 Lua 对象：不调用 ``Inst_get``、不发送 ``CM_WildBossInfoList``、
不打开窗口。未由正常游戏流程加载的字段返回 ``None``（unknown），不补 0。
"""

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
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses


WILD_BOSS_TYPE = 6
_WILD_BOSS_METHODS = frozenset(
    {
        "LuaWildBossMgr",
        "Inst_get",
        "GetFatigueDesc",
    }
)


def _wild_boss_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _WILD_BOSS_METHODS)
    instance = reader.fields(manager.get("inst"))
    if not instance:
        raise FanxiuRuntimeMemoryError(
            "WildBossMgr 实例尚未创建；只读探针不会调用 Inst_get",
            code="data_not_loaded",
        )
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("Data"))
    if "bossGroupInfoVoDic" not in data:
        raise FanxiuRuntimeMemoryError(
            "WildBossMgr.Model.Data 尚未初始化",
            code="data_not_loaded",
        )
    return data


def _long_or_int(reader: LuaJitReader, value: Any) -> int | None:
    if isinstance(value, LuaRef):
        return reader.long(value)
    return as_int(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _dictionary_entries(
    reader: LuaJitReader,
    value: Any,
    *,
    label: str,
) -> dict[int, Any]:
    entries: dict[int, Any] = {}
    for raw_key, item in reader.dictionary_fields(value).items():
        key = as_int(raw_key)
        if key is None or item is None:
            raise FanxiuRuntimeMemoryError(f"{label} 含非整数键或空值")
        entries[key] = item
    declared = as_int(reader.fields(value).get("LuaDic_count"))
    if declared is not None and declared != len(entries):
        raise FanxiuRuntimeMemoryError(
            f"{label} 声明 {declared} 项，实际解码 {len(entries)} 项",
            code="runtime_incomplete",
        )
    return entries


def _project_boss(reader: LuaJitReader, value: Any) -> dict[str, Any]:
    fields = reader.fields(value)
    end_epoch_ms = _long_or_int(reader, fields.get("endTime"))
    return {
        "unit_id": _long_or_int(reader, fields.get("unitId")),
        "group_id": as_int(fields.get("groupId")),
        "monster_id": as_int(fields.get("monsterId")),
        "map_id": as_int(fields.get("mapId")),
        "scene_id": as_int(fields.get("sceneId")),
        "curr_hp": _number(fields.get("currHp")),
        "max_hp": _number(fields.get("maxHp")),
        "is_dead": fields.get("isDead") if isinstance(fields.get("isDead"), bool) else None,
        "is_seal": fields.get("isSeal") if isinstance(fields.get("isSeal"), bool) else None,
        "end_epoch_ms": end_epoch_ms,
        "end_at": (
            datetime.fromtimestamp(end_epoch_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
            if end_epoch_ms
            else None
        ),
    }


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data = _wild_boss_data_fields(reader, root_address)
    # ``fatigue`` stays nil until SM_WildBossInfoList/CurrentFatigue arrives;
    # the client's GetFatigue() ``or 0`` must not be reproduced here.
    reward_remaining = as_int(data.get("fatigue"))
    by_type = _dictionary_entries(
        reader,
        data.get("bossGroupInfoVoDic"),
        label="WildBoss bossGroupInfoVoDic",
    )
    loaded_boss_types = sorted(by_type)
    groups_value = by_type.get(WILD_BOSS_TYPE)
    # SetBossDataList creates the type entry on the first list response and
    # only clears it afterwards, so a missing entry means "not loaded".
    boss_list_loaded = groups_value is not None
    groups: list[dict[str, Any]] | None = None
    if boss_list_loaded:
        groups = []
        for group_id, item_list in sorted(
            _dictionary_entries(
                reader,
                groups_value,
                label="WildBoss 上古之魔分组",
            ).items()
        ):
            items, declared = reader.list_items(item_list)
            if declared is not None and declared != len(items):
                raise FanxiuRuntimeMemoryError(
                    "WildBoss 分组 CList 数量不完整",
                    code="runtime_incomplete",
                )
            groups.append(
                {
                    "group_id": group_id,
                    "bosses": [_project_boss(reader, item) for item in items],
                }
            )
    # The native list shows only each group's first VO (GetBossDataList).
    leaders = [group["bosses"][0] for group in groups or [] if group["bosses"]]
    complete = boss_list_loaded and reward_remaining is not None
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "WildBossMgr.Model.Data",
        "business": "wild_boss_fengmo",
        "boss_type": WILD_BOSS_TYPE,
        "read_only": True,
        "reward_remaining": reward_remaining,
        "reward_remaining_known": reward_remaining is not None,
        "boss_list_loaded": boss_list_loaded,
        "loaded_boss_types": loaded_boss_types,
        "group_count": len(groups) if groups is not None else None,
        "unsealed_group_count": (
            sum(1 for boss in leaders if boss["is_seal"] is False)
            if groups is not None
            else None
        ),
        "sealed_group_count": (
            sum(1 for boss in leaders if boss["is_seal"] is True)
            if groups is not None
            else None
        ),
        "groups": groups,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


WILD_BOSS_DAILY_QUEST_ID = 1023


def _strict_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _classify_leader(leader: Any) -> str:
    """``open`` / ``closed`` (proven sealed or dead) / ``unknown``."""

    if not isinstance(leader, dict):
        return "unknown"
    seal, dead = leader.get("is_seal"), leader.get("is_dead")
    if seal is True or dead is True:
        return "closed"
    map_id = _strict_int(leader.get("map_id"))
    if seal is False and dead is False and map_id is not None and map_id > 0:
        return "open"
    return "unknown"


def _scan_wild_boss_groups(snapshot: Any) -> tuple[str, dict[str, Any] | None]:
    """Return (``open``|``none_open``|``unknown``, first open target).

    Groups follow native GetBossDataList: only each group's first VO is shown.
    A missing/invalid group list is never treated as an empty one; an empty
    loaded list is a proven "no boss".
    """

    if not isinstance(snapshot, dict) or snapshot.get("boss_list_loaded") is not True:
        return "unknown", None
    groups = snapshot.get("groups")
    if not isinstance(groups, list):
        return "unknown", None
    candidates, uncertain = [], False
    for group in groups:
        group_id = _strict_int(group.get("group_id")) if isinstance(group, dict) else None
        bosses = group.get("bosses") if isinstance(group, dict) else None
        if group_id is None or not isinstance(bosses, list):
            uncertain = True
            continue
        if not bosses:
            continue  # native list shows nothing for an empty group
        state = _classify_leader(bosses[0])
        if state == "open":
            leader = bosses[0]
            candidates.append({"group_id": group_id, "map_id": leader["map_id"],
                               "monster_id": _strict_int(leader.get("monster_id"))})
        elif state == "unknown":
            uncertain = True
    if candidates:
        return "open", min(candidates, key=lambda c: c["group_id"])
    return ("unknown" if uncertain else "none_open"), None


def select_wild_boss_target(snapshot: Any) -> dict[str, Any] | None:
    """Deterministic first group leader proven open (not sealed, not dead, valid map)."""

    return _scan_wild_boss_groups(snapshot)[1]


def wild_boss_daily_admission(quest_row: Any, snapshot: Any) -> dict[str, Any]:
    """Decide whether Quest1023 (参与封魔之战) still needs a free participation.

    ``quest_row`` must be the daily-task inventory row of task 1023
    (task_id/turn/target_turn[/done]); ``snapshot`` is a
    :func:`read_wild_boss_snapshot` result.  ``reward_remaining`` is drop
    eligibility only — never quest progress, never a spending authorization —
    and does not gate admission.  ``no_open_boss`` requires every visible
    leader to be proven sealed or dead (or a loaded empty list); any missing
    or malformed candidate fact yields ``unknown``.  The counted event
    (participation/kill/seal) is still unverified in the real game.
    """

    if not isinstance(quest_row, dict) or _strict_int(quest_row.get("task_id")) != WILD_BOSS_DAILY_QUEST_ID:
        return {"state": "unknown", "reason": "quest_identity_invalid"}
    turn, target = _strict_int(quest_row.get("turn")), _strict_int(quest_row.get("target_turn"))
    if turn is None or target is None or turn < 0 or target <= 0:
        return {"state": "unknown", "reason": "quest_progress_invalid"}
    reached = turn >= target
    done_flag = quest_row.get("done")
    if done_flag is not None and (not isinstance(done_flag, bool) or done_flag is not reached):
        return {"state": "unknown", "reason": "quest_done_inconsistent", "turn": turn, "target_turn": target}
    if reached:
        return {"state": "done", "turn": turn, "target_turn": target}
    scan, target_boss = _scan_wild_boss_groups(snapshot)
    if scan == "open":
        return {"state": "eligible", "turn": turn, "target_turn": target, "target": target_boss,
                "reward_remaining": snapshot.get("reward_remaining")}
    if scan == "none_open":
        return {"state": "no_open_boss", "reason": "all_visible_leaders_sealed_or_dead",
                "turn": turn, "target_turn": target}
    return {"state": "unknown", "reason": "boss_candidates_incomplete", "turn": turn, "target_turn": target}


def read_wild_boss_snapshot() -> dict[str, Any]:
    """Read already-loaded 封魔之战 (WildBoss 上古之魔) facts without side effects.

    Returns ``reward_remaining`` (native ``fatigue``: remaining reward times)
    and the BossType 6 group list only when normal gameplay has loaded them;
    otherwise the value is ``None`` / ``boss_list_loaded=False``.  It never
    requests a sync, opens WildBossView, or authorizes a challenge.  Whether a
    Quest1023 count requires a kill, seal or mere participation is unknown.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root_address, root_cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="wild_boss",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="WildBossMgr",
            required_methods=_WILD_BOSS_METHODS,
            validate=_wild_boss_data_fields,
        )
        result = _snapshot(memory, root_address, root_cache_hit=root_cache_hit)
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
            "business": "wild_boss_fengmo",
            "read_only": True,
            "reason": reason,
            "code": getattr(exc, "code", None),
            "reward_remaining": None,
            "boss_list_loaded": None,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }


__all__ = ["WILD_BOSS_DAILY_QUEST_ID", "WILD_BOSS_TYPE", "read_wild_boss_snapshot",
           "select_wild_boss_target", "wild_boss_daily_admission"]
