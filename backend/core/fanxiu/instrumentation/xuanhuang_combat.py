"""Loaded combat diagnostics for the natural Xuanhuang battle observation.

Only reads current Lua objects. AutoFightComponent.enable alone does not prove
casting: the client also gates on status, loading, navigation and restrictions.
These facts explain the next investigation; they never adjudicate completion
or authorize a target selection, movement, retry, or reward consumption.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef, as_int
from backend.core.fanxiu.instrumentation.ui_runtime_context import acquire_lua_global_runtime_context
from backend.core.fanxiu.instrumentation.wild_boss_field import grid_position, planar_distance


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def read_xuanhuang_combat_snapshot() -> dict[str, Any]:
    """Read bounded player/AutoFight/nearby-monster facts without loading a UI.

    Missing fields remain unknown. ``complete`` covers the diagnostic fields,
    not the daily quest or boss fight. Monster positions and IDs come from the
    same process observation as the player; no WildBoss-domain match is used.
    Reads at most 32 monster views. The native status code is returned as-is;
    static client versions use 0/1/2/3/4 for none/start/running/pause/stop.
    """
    started = time.perf_counter()
    result: dict[str, Any] = {"read_only": True, "complete": False,
                              "captured_at": datetime.now().isoformat(timespec="seconds")}
    try:
        ctx = acquire_lua_global_runtime_context({"EntityMgr", "SceneMgr", "FightMgr"})
        reader = ctx.reader

        def fields(value: Any) -> dict:
            return reader.fields(value) if isinstance(value, LuaRef) else {}

        def instance(name: str) -> dict:
            manager = ctx.loaded_manager_fields(name, frozenset({"Inst_get"}))
            return fields(manager.get("inst"))

        entity, scene, fight = instance("EntityMgr"), instance("SceneMgr"), instance("FightMgr")
        user = fields(entity.get("UserView"))
        player = fields(user.get("Entity"))
        actor = fields(user.get("SkillActor"))
        auto = fields(actor.get("AutoFight"))
        searcher = fields(fight.get("Searcher"))
        position = grid_position(fields(player.get("V_GridPosition")))
        result.update({
            "pid": ctx.binding.pid, "process_start_ticks": ctx.binding.process_start_ticks,
            "map_id": as_int(scene.get("V_CurrentMapId")),
            "map_type": as_int(scene.get("V_CurrentMapType")),
            "scene_loaded": _bool(scene.get("m_SceneLoaded")),
            "role_id": reader.long(player.get("V_ID")), "position": position,
            "user_dead": _bool(user.get("IsDead")),
            "user_hidden": _bool(user.get("isHideUserView")),
            "restrict_code": as_int(user.get("restrictCode")),
            "auto_find_way": _bool(fields(user.get("BaseMotor")).get("AutoFindWay")),
            "auto_fight": {key: _bool(auto.get(key)) for key in (
                "enable", "InSceneLoading", "IsJoystickRunning", "CanCastSkill", "CanCastAttack", "CanCastDodge")},
            "auto_status": as_int(auto.get("status")),
            "target_id": reader.long(auto.get("targetId")),
            "monster_filter": as_int(auto.get("monsterId")),
            "search_alarm_range": searcher.get("AlarmRange") if isinstance(searcher.get("AlarmRange"), (int, float))
                and not isinstance(searcher.get("AlarmRange"), bool) else None,
        })
        raw = entity.get("MonsterViewDic")
        wrapper = fields(raw)
        entries = reader.dictionary_fields(raw) if "_dt_" in wrapper else wrapper
        monsters = None
        if isinstance(raw, LuaRef):
            monsters = []
            for ref in list(entries.values())[:32]:
                view = fields(ref)
                ent = fields(view.get("Entity"))
                cfg = fields(ent.get("monsterdata"))
                pos = grid_position(fields(ent.get("V_GridPosition")))
                monsters.append({"unit_id": reader.long(ent.get("V_ID")),
                    "monster_id": as_int(cfg.get("id")), "battle_id": as_int(ent.get("V_MonsterBattleId")),
                    "loaded": _bool(view.get("LoadFinish")), "dead": _bool(view.get("IsDead")),
                    "can_select": _bool(ent.get("V_CanSelect")), "unselect": as_int(cfg.get("unSelect")),
                    "restrict_code": as_int(view.get("restrictCode")),
                    "position": pos, "distance": planar_distance(position, pos)})
        result.update(monsters=monsters, monsters_truncated=len(entries) > 32)
        required = {"role_id": result["role_id"], "map_id": result["map_id"], "position": position,
                    "auto_fight.enable": result["auto_fight"]["enable"],
                    "auto_status": result["auto_status"], "monsters": monsters}
        result["missing"] = [name for name, value in required.items() if value is None]
        result["complete"] = not result["missing"]
    except Exception as exc:
        result.update(complete=False, reason=f"{type(exc).__name__}: {exc}"[:400])
    result["elapsed_seconds"] = time.perf_counter() - started
    return result
