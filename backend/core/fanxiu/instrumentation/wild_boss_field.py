"""封魔之战现场只读快照：当前地图/线路、玩家位置、附近怪物与上古之魔目标对照。

只遍历已加载的 Lua 对象（SceneMgr.inst、EntityMgr.inst、WildBossMgr 快照），
不调用 Lua、不发包、不促成导航或加载。任何缺失字段保持 ``None`` 并给出原因，
未加载的怪物表不当作空列表。``map_type`` 为原生 MapType 数值（枚举表不在导出中，
由真实地图读数对照解释）。
"""

from __future__ import annotations

import time
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
    as_int,
    manager_index_fields,
    table_ref,
)

_MAX_MONSTERS = 200


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def grid_position(fields: dict) -> dict[str, float] | None:
    """Project a Lua Vector3-like table ({x,y,z}) or ``None`` when incomplete."""
    values = {axis: _number(fields.get(axis)) for axis in ("x", "y", "z")}
    return values if all(v is not None for v in values.values()) else None


def planar_distance(a: dict | None, b: dict | None) -> float | None:
    """Native TaskAndTeamComp uses Vector3.DistanceWithOutY (x/z plane)."""
    if not a or not b:
        return None
    return ((a["x"] - b["x"]) ** 2 + (a["z"] - b["z"]) ** 2) ** 0.5


def _instance(context, name: str) -> dict:
    ref = table_ref(context.field(context.binding.environment_address, name))
    if ref is None:
        raise FanxiuRuntimeMemoryError(f"{name} 未加载", code="data_not_loaded")
    inst = manager_index_fields(context.reader, ref.address, frozenset({"Inst_get"})).get("inst")
    if not isinstance(inst, LuaRef):
        raise FanxiuRuntimeMemoryError(f"{name}.inst 未创建", code="data_not_loaded")
    return context.reader.fields(inst)


def _entries(reader, value) -> dict | None:
    if not isinstance(value, LuaRef):
        return None
    wrapper = reader.fields(value)
    return reader.dictionary_fields(value) if "_dt_" in wrapper else wrapper


def _read(context) -> dict[str, Any]:
    reader = context.reader
    scene = _instance(context, "SceneMgr")
    entity = _instance(context, "EntityMgr")
    user_view = reader.fields(entity.get("UserView")) if isinstance(entity.get("UserView"), LuaRef) else {}
    user_entity = reader.fields(user_view.get("Entity")) if isinstance(user_view.get("Entity"), LuaRef) else {}
    user_pos = (grid_position(reader.fields(user_entity["V_GridPosition"]))
                if isinstance(user_entity.get("V_GridPosition"), LuaRef) else None)
    monsters_raw = _entries(reader, entity.get("MonsterViewDic"))
    monsters = None
    truncated = False
    if monsters_raw is not None:
        monsters = []
        for key, view in list(monsters_raw.items()):
            if len(monsters) >= _MAX_MONSTERS:
                truncated = True
                break
            if not isinstance(view, LuaRef):
                continue
            view_fields = reader.fields(view)
            ent = reader.fields(view_fields["Entity"]) if isinstance(view_fields.get("Entity"), LuaRef) else {}
            cfg = reader.fields(ent["monsterdata"]) if isinstance(ent.get("monsterdata"), LuaRef) else {}
            pos = grid_position(reader.fields(ent["V_GridPosition"])) if isinstance(ent.get("V_GridPosition"), LuaRef) else None
            unit = ent.get("V_ID")
            monsters.append({
                "key": as_int(key) if as_int(key) is not None else str(key),
                "unit_id": reader.long(unit) if isinstance(unit, LuaRef) else as_int(unit),
                "monster_id": as_int(cfg.get("id")), "monster_type": as_int(cfg.get("type")),
                "is_dead": view_fields.get("IsDead") if isinstance(view_fields.get("IsDead"), bool) else None,
                "position": pos, "distance": planar_distance(user_pos, pos)})
    result = {
        "read_only": True,
        "map_id": as_int(scene.get("V_CurrentMapId")), "map_type": as_int(scene.get("V_CurrentMapType")),
        "line_id": as_int(scene.get("V_SceneLineId")),
        "is_cross_scene": scene.get("V_IsCrossScene") if isinstance(scene.get("V_IsCrossScene"), bool) else None,
        "scene_loaded": scene.get("m_SceneLoaded") if isinstance(scene.get("m_SceneLoaded"), bool) else None,
        "user_position": user_pos,
        "user_dead": user_view.get("IsDead") if isinstance(user_view.get("IsDead"), bool) else None,
        "monsters": monsters,
        "monsters_reason": None if monsters is not None else "MonsterViewDic 未加载",
        "monsters_truncated": truncated,
        "pid": context.binding.pid, "process_start_ticks": context.binding.process_start_ticks,
    }
    return apply_field_completeness(result)


REQUIRED_FIELD_KEYS = ("map_id", "map_type", "line_id", "user_position", "monsters")


def apply_field_completeness(field: dict) -> dict:
    """complete only when every required live fact is known; missing keys are listed."""
    missing = [key for key in REQUIRED_FIELD_KEYS if field.get(key) is None]
    return {**field, "complete": not missing, "missing": missing}


def _instance_identity(view: dict, vo: dict) -> str:
    """Compare unit ids only as evidence: field V_ID vs VO unitId equality is not yet a proven rule."""
    a, b = view.get("unit_id"), vo.get("unit_id")
    if not isinstance(a, int) or not isinstance(b, int) or isinstance(a, bool) or isinstance(b, bool):
        return "unit_id_unknown"
    return "unit_id_equal" if a == b else "unit_id_differs"


def match_wild_boss_units(field: dict, wild_boss: dict) -> dict:
    """List loaded monster views that are *candidates* for open WildBoss group leaders.

    Returns ``{"state": "candidates"|"none_nearby"|"ambiguous"|"unknown", ...}``.
    A candidate shares monster_id with a leader of the snapshot's boss type that
    is on the field's line (VO.scene_id) and proven alive and unsealed (or seal
    not applicable). This is monster+line evidence, not a proven instance match:
    each candidate carries ``instance_identity`` (unit_id_equal/differs/unknown).
    Several VOs or views for one monster stay ambiguous. An incomplete or
    truncated field read, unknown line, or a missing WildBoss list is unknown.
    """
    if not isinstance(field, dict) or not isinstance(field.get("monsters"), list):
        return {"state": "unknown", "reason": "field_monsters_unknown"}
    if field.get("complete") is False:
        return {"state": "unknown", "reason": "field_snapshot_incomplete", "missing": field.get("missing")}
    if field.get("monsters_truncated") is True:
        return {"state": "unknown", "reason": "field_monsters_truncated"}
    line = field.get("line_id")
    if not isinstance(line, int) or isinstance(line, bool):
        return {"state": "unknown", "reason": "field_line_unknown"}
    if not isinstance(wild_boss, dict) or wild_boss.get("boss_list_loaded") is not True \
            or not isinstance(wild_boss.get("groups"), list):
        return {"state": "unknown", "reason": "wild_boss_list_unknown"}
    vos_by_monster: dict[int, list[dict]] = {}
    for group in wild_boss["groups"]:
        bosses = group.get("bosses") if isinstance(group, dict) else None
        if not bosses or not isinstance(bosses[0], dict):
            continue
        vo = bosses[0]
        sealed = vo.get("is_seal")
        open_seal = sealed is False or (sealed is None and vo.get("seal_applicable") is False)
        if vo.get("is_dead") is False and open_seal and vo.get("scene_id") == line \
                and isinstance(vo.get("monster_id"), int):
            vos_by_monster.setdefault(vo["monster_id"], []).append(vo)
    candidates, ambiguous = [], []
    for view in field["monsters"]:
        vos = vos_by_monster.get(view.get("monster_id"))
        if not vos:
            continue
        same_monster_views = sum(1 for m in field["monsters"] if m.get("monster_id") == view.get("monster_id"))
        if len(vos) == 1 and same_monster_views == 1:
            candidates.append({**view, "wild_boss_vo": vos[0], "instance_identity": _instance_identity(view, vos[0])})
        else:
            ambiguous.append({**view, "candidate_vos": vos})
    if ambiguous:
        return {"state": "ambiguous", "candidates": candidates, "ambiguous": ambiguous}
    return {"state": "candidates" if candidates else "none_nearby", "candidates": candidates}


def read_wild_boss_field_snapshot(boss_type: int = 6) -> dict[str, Any]:
    """Read where the player is and which WildBoss units are actually loaded nearby.

    Combines SceneMgr/EntityMgr live facts with :func:`read_wild_boss_snapshot`
    (same process identity required). Never calls Lua, sends packets or moves.
    """
    from backend.core.fanxiu.instrumentation.ui_runtime_context import acquire_lua_global_runtime_context
    from backend.core.fanxiu.instrumentation.wild_boss import read_wild_boss_snapshot

    started = time.perf_counter()
    try:
        context = acquire_lua_global_runtime_context({"SceneMgr", "EntityMgr"})
        field = _read(context)
    except Exception as exc:  # noqa: BLE001
        return {"complete": False, "read_only": True, "reason": str(exc)[:300],
                "elapsed_seconds": time.perf_counter() - started}
    wild = read_wild_boss_snapshot(boss_type)
    same_process = (wild.get("evidence") or {}).get("pid") == field["pid"] and \
        (wild.get("evidence") or {}).get("process_start_ticks") == field["process_start_ticks"]
    field["boss_type"] = boss_type
    field["wild_boss_complete"] = bool(wild.get("complete")) and same_process
    field["wild_boss_reward_remaining"] = wild.get("reward_remaining") if same_process else None
    field["wild_boss_reward_scope"] = wild.get("reward_remaining_scope")
    field["wild_boss_units_nearby"] = (match_wild_boss_units(field, wild) if same_process
                                       else {"state": "unknown", "reason": "wild_boss_snapshot_other_process"})
    field["elapsed_seconds"] = time.perf_counter() - started
    return field


__all__ = ["apply_field_completeness", "grid_position", "match_wild_boss_units", "planar_distance",
           "read_wild_boss_field_snapshot"]
