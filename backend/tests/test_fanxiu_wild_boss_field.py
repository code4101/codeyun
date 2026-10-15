from backend.core.fanxiu.instrumentation.wild_boss import wild_boss_daily_admission
from backend.core.fanxiu.instrumentation.wild_boss_field import (
    apply_field_completeness,
    grid_position,
    match_wild_boss_units,
    planar_distance,
)


def test_grid_position_requires_all_numeric_axes():
    assert grid_position({"x": 1, "y": 2.5, "z": -3}) == {"x": 1.0, "y": 2.5, "z": -3.0}
    assert grid_position({"x": 1, "y": 2}) is None
    assert grid_position({"x": True, "y": 0, "z": 0}) is None


def test_planar_distance_ignores_height_and_unknowns():
    assert planar_distance({"x": 0.0, "y": 50.0, "z": 0.0}, {"x": 3.0, "y": -9.0, "z": 4.0}) == 5.0
    assert planar_distance(None, {"x": 0.0, "y": 0.0, "z": 0.0}) is None


def _wild(*vos):
    return {"boss_list_loaded": True, "groups": [{"group_id": i, "bosses": [vo]} for i, vo in enumerate(vos, 1)]}


ALIVE9 = {"monster_id": 9, "unit_id": 101, "scene_id": 1, "is_dead": False, "is_seal": None, "seal_applicable": False}


def test_duplicate_monster_vos_stay_ambiguous_and_dead_or_sealed_vos_never_bind():
    field = {"line_id": 1, "monsters": [{"monster_id": 9, "unit_id": 1}]}
    dead = {**ALIVE9, "unit_id": 2, "is_dead": True}
    result = match_wild_boss_units(field, _wild(ALIVE9, dead))
    assert result["state"] == "candidates" and result["candidates"][0]["wild_boss_vo"]["unit_id"] == 101
    # monster+line is candidate evidence only; differing unit ids are reported, not hidden.
    assert result["candidates"][0]["instance_identity"] == "unit_id_differs"
    assert match_wild_boss_units(field, _wild(dead))["state"] == "none_nearby"
    sealed6 = {**ALIVE9, "is_seal": True, "seal_applicable": True}
    assert match_wild_boss_units(field, _wild(sealed6))["state"] == "none_nearby"
    assert match_wild_boss_units(field, _wild(ALIVE9, {**ALIVE9, "unit_id": 102}))["state"] == "ambiguous"
    same = match_wild_boss_units({"line_id": 1, "monsters": [{"monster_id": 9, "unit_id": 101}]}, _wild(ALIVE9))
    assert same["candidates"][0]["instance_identity"] == "unit_id_equal"


def test_line_mismatch_and_unknown_inputs_never_become_known_empty():
    assert match_wild_boss_units({"line_id": 2, "monsters": [{"monster_id": 9}]}, _wild(ALIVE9))["state"] == "none_nearby"
    assert match_wild_boss_units({"line_id": 1, "monsters": None}, _wild(ALIVE9))["state"] == "unknown"
    assert match_wild_boss_units({"line_id": 1, "monsters": []}, {"boss_list_loaded": None})["state"] == "unknown"
    # reviewer case: unknown line must not become "none nearby"
    assert match_wild_boss_units({"line_id": None, "monsters": [{"monster_id": 9}]}, _wild(ALIVE9))["state"] == "unknown"
    assert match_wild_boss_units({"line_id": 1, "monsters": [], "complete": False, "missing": ["user_position"]},
                                 _wild(ALIVE9))["state"] == "unknown"
    assert match_wild_boss_units({"line_id": 1, "monsters": [], "monsters_truncated": True}, _wild(ALIVE9))["state"] == "unknown"


def test_preferred_target_reasons_distinguish_missing_unknown_and_closed():
    q = {"task_id": 1023, "turn": 0, "target_turn": 1, "done": False}
    other = {**ALIVE9, "monster_id": 7, "map_id": 14022}
    assert wild_boss_daily_admission(q, _wild(other), preferred_monster_id=20002351)["reason"] == "preferred_target_not_listed"
    assert wild_boss_daily_admission(q, _wild(other), preferred_monster_id=20002351)["state"] == "unknown"
    unknown = {**ALIVE9, "monster_id": 20002351, "map_id": 14001, "is_dead": None}
    assert wild_boss_daily_admission(q, _wild(unknown), preferred_monster_id=20002351)["reason"] == "preferred_target_facts_incomplete"
    closed = {**unknown, "is_dead": True}
    result = wild_boss_daily_admission(q, _wild(other, closed), preferred_monster_id=20002351)
    assert result["state"] == "no_open_boss" and result["reason"] == "preferred_target_sealed_or_dead"


def test_field_completeness_lists_missing_required_facts():
    full = {"map_id": 14001, "map_type": 51, "line_id": 1, "user_position": {"x": 0.0, "y": 0.0, "z": 0.0}, "monsters": []}
    assert apply_field_completeness(full)["complete"] is True
    partial = apply_field_completeness({**full, "monsters": None, "line_id": None})
    assert partial["complete"] is False and partial["missing"] == ["line_id", "monsters"]


def test_type9_leader_without_seal_field_is_open_for_admission():
    q = {"task_id": 1023, "turn": 0, "target_turn": 1, "done": False}
    leader = {**ALIVE9, "map_id": 14001}
    assert wild_boss_daily_admission(q, _wild(leader))["state"] == "eligible"
    assert wild_boss_daily_admission(q, _wild({**leader, "seal_applicable": True}))["state"] == "unknown"


def test_admission_binds_to_gui_selected_monster_not_first_group():
    q = {"task_id": 1023, "turn": 0, "target_turn": 1, "done": False}
    first = {**ALIVE9, "monster_id": 20002341, "map_id": 14022}
    chosen = {**ALIVE9, "monster_id": 20002351, "map_id": 14001}
    assert wild_boss_daily_admission(q, _wild(first, chosen))["target"]["monster_id"] == 20002341
    picked = wild_boss_daily_admission(q, _wild(first, chosen), preferred_monster_id=20002351)
    assert picked["state"] == "eligible" and picked["target"]["map_id"] == 14001
    dead = {**chosen, "is_dead": True}
    assert wild_boss_daily_admission(q, _wild(first, dead), preferred_monster_id=20002351)["state"] == "no_open_boss"
