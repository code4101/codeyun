from __future__ import annotations

import inspect

import pytest

from backend.core.fanxiu.data_annotation import dongtian_friend_detail
from backend.core.fanxiu.data_annotation.dongtian_friend_detail import (
    build_dongtian_friend_detail_plan,
    verify_dongtian_friend_detail_result,
)


def _seat(
    seat_id: int,
    *,
    role_id: int = 42,
    union_id: int = 99,
    empty: bool = False,
) -> dict:
    return {
        "id": seat_id,
        "quality": 2,
        "complete": True,
        "empty": empty,
        "guarder_present": not empty,
        "guarder_type": 2 if not empty else 0,
        "guarder_role_id": role_id if not empty else None,
        "guarder_cross_union_id": union_id if not empty else None,
    }


def _probe(seat: dict, *, mine_union_id: int = 99) -> dict:
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "runtime_memory",
        "protocol": "dongtian.seating.probe.v1",
        "own_union_id": 99,
        "own_role_id": 7,
        "selected_mine": {
            "id": 3,
            "config_group": 4,
            "cross_union_id": mine_union_id,
            "seats_complete": True,
            "seats": [seat],
        },
        "evidence": {"pid": 11, "process_start_ticks": 22},
    }


def _snapshot(seat: dict) -> dict:
    probe = _probe(seat)
    mine = probe.pop("selected_mine")
    probe["protocol"] = "XianLvMinesMgr.Model.Data + ClubMgr.Model.data + RoleMgr.Model.V_ID"
    probe["mines"] = [mine]
    return probe


def _scene(scene_id: int) -> dict:
    return {
        "fresh": True,
        "scene_id": scene_id,
        "layer": "layer0",
        "status": "matched",
    }


def _observation(*, role_id: int = 42, seat_id: int = 5) -> dict:
    return {
        "ok": True,
        "status": "observation_ready",
        "observation": {
            "role_id": str(role_id),
            "role_id_text": str(role_id),
            "xianlv_team_fight_score_max": 65724906006937,
            "evidence": {
                "mine_id": 3,
                "quality": 2,
                "seat_id": seat_id,
            },
        },
    }


@pytest.mark.parametrize(
    ("seat_id", "expected_point"),
    [(5, [447, 754]), (12, [309, 766])],
)
def test_only_live_verified_friend_attendant_points_enter_scene_607(
    seat_id: int,
    expected_point: list[int],
) -> None:
    plan = build_dongtian_friend_detail_plan(
        _probe(_seat(seat_id)),
        seat_id=seat_id,
        foreground_evidence=_scene(341),
        scroll_offset_verified=True,
    )

    assert plan["status"] == "read_only_friend_detail"
    assert plan["route_family"] == "friendly_occupied_detail"
    assert plan["step"]["point"] == expected_point
    assert plan["step"]["expected_scene_ids"] == [607]
    assert plan["stop_scene_id"] == 607
    assert plan["allowed_detail_actions"] == []
    assert plan["forbidden_detail_actions"] == ["互换采气"]
    assert plan["runtime_collection"]["source_cache"] == "V_GuarderTeamDic"


def test_unverified_friend_attendant_point_fails_closed() -> None:
    plan = build_dongtian_friend_detail_plan(
        _probe(_seat(8)),
        seat_id=8,
        foreground_evidence=_scene(341),
        scroll_offset_verified=True,
    )

    assert plan["click_enabled"] is False
    assert plan["reason"] == "friendly_occupied_hitbox_unverified"
    assert plan["evidence"]["verified_seat_ids"] == [5, 12]


def test_full_snapshot_can_select_exact_friendly_mine_without_seating_candidate() -> None:
    plan = build_dongtian_friend_detail_plan(
        _snapshot(_seat(5)),
        mine_id=3,
        seat_id=5,
        foreground_evidence=_scene(341),
        scroll_offset_verified=True,
    )

    assert plan["ok"] is True
    assert plan["target"]["mine_id"] == 3
    assert plan["status"] == "read_only_friend_detail"


@pytest.mark.parametrize(
    ("probe", "reason"),
    [
        (_probe(_seat(5, empty=True)), "seat_not_occupied_player_attendant"),
        (_probe(_seat(5, union_id=66)), "seat_occupant_not_friendly"),
        (_probe(_seat(5), mine_union_id=66), "mine_not_friendly"),
        (_probe(_seat(5, role_id=7)), "seat_is_own_role"),
    ],
)
def test_friend_detail_route_is_disjoint_from_empty_enemy_and_own_seats(
    probe: dict,
    reason: str,
) -> None:
    plan = build_dongtian_friend_detail_plan(
        probe,
        seat_id=5,
        foreground_evidence=_scene(341),
        scroll_offset_verified=True,
    )

    assert plan["click_enabled"] is False
    assert plan["reason"] == reason


def test_overlay_or_stale_scene_never_authorizes_friend_detail_click() -> None:
    overlay = build_dongtian_friend_detail_plan(
        _probe(_seat(5)),
        seat_id=5,
        foreground_evidence=_scene(284),
        scroll_offset_verified=True,
    )
    stale = build_dongtian_friend_detail_plan(
        _probe(_seat(5)),
        seat_id=5,
        foreground_evidence={**_scene(341), "fresh": False},
        scroll_offset_verified=True,
    )

    assert overlay["reason"] == "foreground_scene_not_fresh_341"
    assert stale["reason"] == "foreground_scene_not_fresh_341"
    assert overlay["forbidden_detail_actions"] == ["互换采气"]


def test_scene_607_result_collects_score_and_still_exposes_no_swap_action() -> None:
    probe = _probe(_seat(5))
    plan = build_dongtian_friend_detail_plan(
        probe,
        seat_id=5,
        foreground_evidence=_scene(341),
        scroll_offset_verified=True,
    )

    result = verify_dongtian_friend_detail_result(
        plan,
        landing_evidence=_scene(607),
        after_probe=probe,
        observation_result=_observation(),
    )

    assert result["ok"] is True
    assert result["status"] == "read_only_friend_detail"
    assert result["fight_score"] == 65724906006937
    assert result["click_enabled"] is False
    assert result["stop_scene_id"] == 607
    assert result["allowed_detail_actions"] == []
    assert result["forbidden_detail_actions"] == ["互换采气"]


def test_changed_occupant_or_mismatched_observation_fails_closed_at_607() -> None:
    before = _probe(_seat(12))
    plan = build_dongtian_friend_detail_plan(
        before,
        seat_id=12,
        foreground_evidence=_scene(341),
        scroll_offset_verified=True,
    )
    changed = _probe(_seat(12, role_id=43))
    occupant_changed = verify_dongtian_friend_detail_result(
        plan,
        landing_evidence=_scene(607),
        after_probe=changed,
        observation_result=_observation(seat_id=12),
    )
    observation_mismatch = verify_dongtian_friend_detail_result(
        plan,
        landing_evidence=_scene(607),
        after_probe=before,
        observation_result=_observation(role_id=43, seat_id=12),
    )

    assert occupant_changed["reason"] == "friend_detail_occupant_changed"
    assert observation_mismatch["reason"] == (
        "xianlv_team_observation_identity_mismatch"
    )
    assert occupant_changed["click_enabled"] is False
    assert observation_mismatch["click_enabled"] is False


def test_friend_detail_module_has_no_gui_or_runtime_mutation_primitive() -> None:
    source = inspect.getsource(dongtian_friend_detail)

    assert "ctx.click" not in source
    assert "runtime.click" not in source
    assert "adb" not in source.lower()
    assert "CM_" not in source
    assert "run_task" not in source
    assert "sceneJumpTarget" not in source
