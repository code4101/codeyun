from __future__ import annotations

from backend.core.fanxiu.data_annotation.dongtian_seating_research import (
    build_dongtian_seating_research_report,
)


def _team(team_id: int, *, idle: bool, mine_id: int = 0) -> dict:
    return {
        "id": team_id,
        "state": 1 if idle else 2,
        "mine_id": mine_id,
        "dead": False,
        "complete": True,
        "idle": idle,
        "fight_score": 500,
        "xianlv_ids": [1, 2, 3, 4, 5],
    }


def _probe(*, quality: int = 2, empty: bool = True) -> dict:
    return {
        "available": True,
        "complete": True,
        "status": "ready",
        "protocol": "dongtian.seating.probe.v1",
        "own_union_id": 99,
        "own_role_id": 1001,
        "teams": [_team(3, idle=True), _team(5, idle=False, mine_id=22)],
        "selected_mine": {
            "id": 7,
            "cross_union_id": 99,
            "seats_complete": True,
            "seats": [
                {
                    "id": 4 if quality == 2 else 1,
                    "quality": quality,
                    "primary_master": quality == 1,
                    "empty": empty,
                    "guarder_present": not empty,
                    "guarder_type": 0 if empty else 2,
                    "guarder_cross_union_id": None if empty else 77,
                    "guarder_role_id": None if empty else 2002,
                    "complete": True,
                }
            ],
        },
    }


def test_report_summarizes_teams_and_only_selected_probe_mine():
    snapshot = {
        "teams": [_team(3, idle=True), _team(5, idle=False, mine_id=22)],
        # The report must not inspect or decide from this unrelated mine list.
        "mines": [{"id": 999, "seats": "deliberately-invalid"}],
        "captured_at_epoch": 123.0,
    }

    report = build_dongtian_seating_research_report(
        _probe(),
        snapshot=snapshot,
        mine_group=1,
    )

    assert report["single_mine_scan"] is True
    assert report["selected_mine_id"] == 7
    assert report["idle_team_ids"] == [3]
    assert report["occupied_mine_ids"] == [22]
    assert report["next_action"]["action"] == "occupy_empty"


def test_follower_target_reports_geometry_but_not_verified_click_authority():
    report = build_dongtian_seating_research_report(_probe(), mine_group=1)

    candidate = report["gui_coordinate_candidate"]
    assert candidate["available"] is True
    assert candidate["seat_id"] == 4
    assert candidate["point"] == [447, 886]
    assert candidate["verified_for_click"] is False
    assert candidate["reason"] == "empty_hitbox_not_runtime_verified"
    assert report["commit_enabled"] is False
    assert len(report["capability_blockers"]) == 4


def test_nonfriendly_selected_mine_is_rejected_by_research_policy():
    probe = _probe(quality=1, empty=False)
    probe["selected_mine"]["cross_union_id"] = 77
    report = build_dongtian_seating_research_report(
        probe,
    )

    assert report["status"] == "no_safe_target"
    assert report["next_action"]["reason"] == "nonfriendly_location_disallowed"
    assert report["target"] is None
    assert report["strategy_name"] == "friendly_top_down_only"
    assert report["allow_nonfriendly"] is False


def test_nonfriendly_native_detail_cannot_reenable_seating_target():
    probe = _probe(quality=1, empty=False)
    probe["selected_mine"]["cross_union_id"] = 77
    report = build_dongtian_seating_research_report(
        probe,
        native_seat_details={"7:1:1": {"complete": True}},
    )

    assert report["status"] == "no_safe_target"
    assert report["next_action"]["action"] is None
    assert report["target"] is None
    assert report["allow_nonfriendly"] is False


def test_follower_defender_goes_directly_to_final_guard_detail():
    report = build_dongtian_seating_research_report(
        _probe(quality=2, empty=False),
        mine_group=1,
    )

    assert report["status"] == "need_final_detail"
    assert report["detail_requirement"]["layer"] == "site_info_guard_team"
    assert report["detail_requirement"]["expected_scene_ids"] == [343]


def test_report_exposes_shallow_seat_classification_even_when_fixture_is_partial():
    report = build_dongtian_seating_research_report(_probe(), mine_group=1)

    classification = report["seat_classification"]
    assert classification["mine_id"] == 7
    assert classification["friendly_place"] is True
    assert classification["complete"] is False
    assert classification["class_counts"]["empty"] == 1
    assert classification["seats"][0]["seat_key"] == "7:2:4"
