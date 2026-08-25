from __future__ import annotations

from backend.core.fanxiu.data_annotation.dongtian_seating import (
    classify_dongtian_mine_seats,
    classify_dongtian_detail_freshness,
    choose_dongtian_seating_action,
    choose_dongtian_probe_action,
    dongtian_seat_key,
    scan_dongtian_friendly_locations_shallow,
    scan_dongtian_mine_next_action,
)


def _complete_seats(*, classifications: dict[int, str] | None = None) -> list[dict]:
    classifications = classifications or {}
    seats: list[dict] = []
    for quality, seat_ids in ((1, (1, 2, 3)), (2, tuple(range(4, 13)))):
        for display_order, seat_id in enumerate(seat_ids):
            classification = classifications.get(seat_id, "friendly")
            if classification == "empty":
                seat = _seat(seat_id, empty=True, quality=quality)
            elif classification == "enemy":
                seat = _seat(seat_id, empty=False, union_id=77, quality=quality)
            elif classification == "neutral_unknown":
                seat = _seat(seat_id, empty=False, quality=quality, guarder_type=1)
            else:
                seat = _seat(seat_id, empty=False, union_id=99, quality=quality)
            seat["display_order"] = display_order
            seats.append(seat)
    return seats


def _team(team_id: int, *, idle: bool, power: int) -> dict:
    return {
        "id": team_id,
        "state": 1 if idle else 2,
        "mine_id": 0 if idle else 9,
        "seat_index": 0 if idle else 10,
        "fight_score": power,
        "dead": False,
        "xianlv_ids": [1, 2, 3, 4, 5],
        "complete": True,
        "idle": idle,
    }


def _seat(
    seat_id: int,
    *,
    empty: bool,
    union_id: int | None = None,
    quality: int = 2,
    guarder_type: int | None = None,
    primary_master: bool | None = None,
    role_id: int | None = None,
    guarder_present: bool | None = None,
) -> dict:
    return {
        "id": seat_id,
        "quality": quality,
        "empty": empty,
        "guarder_type": 0 if empty else (2 if guarder_type is None else guarder_type),
        "guarder_cross_union_id": union_id,
        "guarder_role_id": role_id,
        "guarder_present": (not empty) if guarder_present is None else guarder_present,
        "primary_master": bool(quality == 1) if primary_master is None else primary_master,
        "complete": True,
    }


def _snapshot(
    *,
    teams: list[dict],
    mines: list[dict],
    own_union_id: int = 99,
    own_role_id: int = 123,
) -> dict:
    return {
        "available": True,
        "seating_summary_complete": True,
        "own_union_id": own_union_id,
        "own_role_id": own_role_id,
        "teams": teams,
        "mines": mines,
    }


def test_no_idle_team_is_an_idempotent_noop():
    result = choose_dongtian_seating_action(
        _snapshot(teams=[_team(1, idle=False, power=100)], mines=[]),
    )

    assert result["ok"] is True
    assert result["status"] == "noop_no_idle"
    assert result["action"] is None


def test_occupied_friendly_location_is_excluded_without_enemy_location_fallback():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[
                _team(1, idle=False, power=900),
                _team(2, idle=True, power=300),
            ],
            mines=[
                {
                    "id": 9,
                    "cross_union_id": 99,
                    "seats_complete": True,
                    "seats": [_seat(1, empty=True, quality=1)],
                },
                {
                    "id": 10,
                    "cross_union_id": 77,
                    "seats_complete": True,
                    "seats": [_seat(8, empty=True, quality=2)],
                },
            ],
        )
    )

    assert result["status"] == "no_safe_target"
    assert result["reason"] == "friendly_locations_exhausted"
    assert result["allow_nonfriendly"] is False


def test_nonfriendly_empty_is_ignored_when_friendly_seats_are_exhausted():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[_team(2, idle=True, power=300)],
            mines=[
                {
                    "id": 5,
                    "cross_union_id": 99,
                    "seats_complete": True,
                    "seats": [_seat(1, empty=False, union_id=99)],
                },
                {
                    "id": 19,
                    "cross_union_id": 77,
                    "seats_complete": True,
                    "seats": [_seat(8, empty=True)],
                },
            ],
        )
    )

    assert result["status"] == "no_safe_target"
    assert result["target"] is None
    assert result["allow_nonfriendly"] is False


def test_nonfriendly_empty_is_not_fallback_for_occupied_friendly_master():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[_team(2, idle=True, power=300)],
            mines=[
                {
                    "id": 5,
                    "cross_union_id": 99,
                    "seats_complete": True,
                    "seats": [_seat(1, empty=False, union_id=77, quality=1)],
                },
                {
                    "id": 19,
                    "cross_union_id": 77,
                    "seats_complete": True,
                    "seats": [_seat(8, empty=True)],
                },
            ],
        )
    )

    assert result["status"] == "no_safe_target"
    assert result["target"] is None


def test_nonfriendly_primary_master_is_outside_seating_strategy():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[_team(2, idle=True, power=300)],
            mines=[{
                "id": 19,
                "cross_union_id": 77,
                "seats_complete": True,
                "seats": [_seat(1, empty=False, union_id=77, quality=1)],
            }],
        )
    )

    assert result["status"] == "no_safe_target"
    assert result["reason"] == "friendly_locations_exhausted"
    assert result["strategy_name"] == "friendly_top_down_only"
    assert result["allow_nonfriendly"] is False


def test_enemy_requires_strictly_lower_power():
    snapshot = _snapshot(
        teams=[_team(2, idle=True, power=300)],
        mines=[
            {
                "id": 19,
                "cross_union_id": 99,
                "seats_complete": True,
                "seats": [_seat(8, empty=False, union_id=66)],
            }
        ],
    )
    key = dongtian_seat_key(19, 2, 8)

    equal = choose_dongtian_seating_action(
        snapshot,
        seat_details={key: {"complete": True, "freshness": "fresh_after_absence", "mine_id": 19, "quality": 2, "seat_id": 8, "fight_score": 300}},
    )
    weaker = choose_dongtian_seating_action(
        snapshot,
        seat_details={key: {"complete": True, "freshness": "fresh_after_absence", "mine_id": 19, "quality": 2, "seat_id": 8, "fight_score": 299}},
    )

    assert equal["status"] == "no_safe_target"
    assert weaker["status"] == "ready"
    assert weaker["action"] == "replace_weaker_enemy"


def test_empty_seat_uses_native_lowest_team_id_not_weakest_power():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[
                _team(3, idle=True, power=100),
                _team(1, idle=True, power=900),
                _team(2, idle=True, power=300),
            ],
            mines=[{
                "id": 19,
                "cross_union_id": 99,
                "seats_complete": True,
                "seats": [_seat(8, empty=True)],
            }],
        )
    )

    assert result["status"] == "ready"
    assert result["action"] == "occupy_empty"
    assert result["target"]["team_id"] == 1
    assert result["target"]["team_fight_score"] == 900
    assert (
        result["target"]["team_selection_basis"]
        == "native_default_lowest_team_id"
    )


def test_enemy_replacement_still_uses_weakest_strictly_stronger_team():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[
                _team(1, idle=True, power=900),
                _team(2, idle=True, power=300),
                _team(3, idle=True, power=100),
            ],
            mines=[{
                "id": 19,
                "cross_union_id": 99,
                "seats_complete": True,
                "seats": [_seat(8, empty=False, union_id=66)],
            }],
        ),
        seat_details={
            "19:2:8": {
                "complete": True,
                "freshness": "fresh_packet",
                "mine_id": 19,
                "quality": 2,
                "seat_id": 8,
                "fight_score": 250,
            }
        },
    )

    assert result["status"] == "ready"
    assert result["action"] == "replace_weaker_enemy"
    assert result["target"]["team_id"] == 2
    assert result["target"]["team_fight_score"] == 300
    assert (
        result["target"]["team_selection_basis"]
        == "weakest_strictly_stronger"
    )


def test_follower_held_by_location_owner_is_alliance_protected_and_skipped():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[_team(2, idle=True, power=300)],
            mines=[{
                "id": 19,
                "cross_union_id": 99,
                "seats_complete": True,
                "seats": [_seat(8, empty=False, union_id=99)],
            }],
        )
    )

    assert result["status"] == "no_safe_target"
    assert result["action"] is None


def test_non_primary_enemy_master_is_read_only_and_skipped():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[_team(2, idle=True, power=300)],
            mines=[{
                "id": 1,
                "cross_union_id": 99,
                "seats_complete": True,
                "seats": [
                    _seat(1, empty=False, union_id=77, quality=1, primary_master=True),
                    _seat(3, empty=False, union_id=77, quality=1, primary_master=False),
                ],
            }],
        ),
        seat_details={
            "1:1:1": {"complete": True, "freshness": "fresh_packet", "mine_id": 1, "quality": 1, "seat_id": 1, "fight_score": 999},
            "1:1:3": {"complete": True, "freshness": "fresh_packet", "mine_id": 1, "quality": 1, "seat_id": 3, "fight_score": 1},
        },
    )

    assert result["status"] == "no_safe_target"


def test_friendly_master_entry_uses_first_empty_master_even_when_not_primary():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[_team(2, idle=True, power=300)],
            mines=[{
                "id": 5,
                "cross_union_id": 99,
                "seats_complete": True,
                "seats": [
                    _seat(1, empty=False, union_id=99, quality=1, primary_master=True),
                    _seat(2, empty=True, quality=1, primary_master=False),
                ],
            }],
        )
    )

    assert result["status"] == "ready"
    assert result["action"] == "occupy_empty"
    assert result["target"]["seat_key"] == "5:1:2"
    assert result["target"]["ui_route"] == "master_list_first_empty"


def test_friendly_master_empty_is_skipped_when_own_role_already_has_master():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[_team(2, idle=True, power=300)],
            mines=[{
                "id": 5,
                "cross_union_id": 99,
                "seats_complete": True,
                "seats": [
                    _seat(
                        1,
                        empty=False,
                        union_id=99,
                        quality=1,
                        primary_master=True,
                        role_id=123,
                    ),
                    _seat(2, empty=True, quality=1, primary_master=False),
                    _seat(8, empty=True, quality=2),
                ],
            }],
        )
    )

    assert result["status"] == "ready"
    assert result["action"] == "occupy_empty"
    assert result["target"]["seat_key"] == "5:2:8"


def test_friendly_master_requires_guarder_to_be_actually_absent():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[_team(2, idle=True, power=300)],
            mines=[{
                "id": 5,
                "cross_union_id": 99,
                "seats_complete": True,
                "seats": [
                    _seat(
                        2,
                        empty=True,
                        quality=1,
                        primary_master=False,
                        guarder_present=True,
                    ),
                    _seat(8, empty=True, quality=2),
                ],
            }],
        )
    )

    assert result["status"] == "ready"
    assert result["target"]["seat_key"] == "5:2:8"


def test_missing_own_role_identity_never_authorizes_friendly_master_button():
    result = choose_dongtian_seating_action(
        _snapshot(
            own_role_id=0,
            teams=[_team(2, idle=True, power=300)],
            mines=[{
                "id": 5,
                "cross_union_id": 99,
                "seats_complete": True,
                "seats": [
                    _seat(2, empty=True, quality=1, primary_master=False),
                    _seat(8, empty=True, quality=2),
                ],
            }],
        )
    )

    assert result["status"] == "ready"
    assert result["target"]["seat_key"] == "5:2:8"


def test_neutral_guarder_requires_runtime_detail_without_union_id():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[_team(2, idle=True, power=300)],
            mines=[{
                "id": 2,
                "cross_union_id": 99,
                "seats_complete": True,
                "seats": [_seat(4, empty=False, guarder_type=1)],
            }],
        )
    )

    assert result["status"] == "need_detail"
    assert result["target"]["seat_key"] == "2:2:4"
    assert result["target"]["ui_route"] == "follower_seat_direct"


def test_detail_freshness_requires_new_packet_or_cache_absence_transition():
    compatible = {"complete": True, "mine_id": 1, "quality": 2, "seat_id": 4, "fight_score": 100}

    old = classify_dongtian_detail_freshness(
        before=compatible,
        after=compatible,
        expected_mine_id=1,
        expected_quality=2,
        expected_seat_id=4,
        request_watermark=10,
    )
    appeared = classify_dongtian_detail_freshness(
        before=None,
        after=compatible,
        expected_mine_id=1,
        expected_quality=2,
        expected_seat_id=4,
        before_absence_proven=True,
    )
    packet = classify_dongtian_detail_freshness(
        before=compatible,
        after={
            **compatible,
            "response_packet_id": 11,
            "response_mine_id": 1,
            "response_quality": 2,
            "response_seat_id": 4,
        },
        expected_mine_id=1,
        expected_quality=2,
        expected_seat_id=4,
        request_watermark=10,
    )
    generation = classify_dongtian_detail_freshness(
        before={**compatible, "cache_generation_address": 100},
        after={**compatible, "cache_generation_address": 200},
        expected_mine_id=1,
        expected_quality=2,
        expected_seat_id=4,
    )

    assert old["freshness"] == "compatible_unproven"
    assert old["fresh"] is False
    assert appeared["freshness"] == "fresh_after_absence"
    assert packet["freshness"] == "fresh_packet"
    assert generation["freshness"] == "fresh_runtime_generation"
    assert generation["fresh"] is True


def test_explicit_wrong_response_echo_cannot_fall_back_to_new_generation():
    compatible = {
        "complete": True,
        "mine_id": 1,
        "quality": 2,
        "seat_id": 4,
        "fight_score": 100,
    }

    result = classify_dongtian_detail_freshness(
        before={**compatible, "cache_generation_address": 100},
        after={
            **compatible,
            "cache_generation_address": 200,
            "response_packet_id": 11,
            "response_mine_id": 999,
            "response_quality": 2,
            "response_seat_id": 4,
        },
        expected_mine_id=1,
        expected_quality=2,
        expected_seat_id=4,
        request_watermark=10,
    )

    assert result["ok"] is False
    assert result["fresh"] is False
    assert result["freshness"] == "response_identity_mismatch"


def test_probe_advances_one_mine_at_a_time_after_proving_no_target():
    probe = {
        "available": True,
        "complete": True,
        "status": "ready",
        "own_union_id": 99,
        "teams": [_team(2, idle=True, power=300)],
        "selected_mine": {
            "id": 1,
            "cross_union_id": 99,
            "seats_complete": True,
            "seats": [
                _seat(4, empty=False, union_id=77, quality=2),
            ],
        },
    }

    result = choose_dongtian_probe_action(
        probe,
        seat_details={
            "1:2:4": {
                "complete": True,
                "freshness": "fresh_after_absence",
                "mine_id": 1,
                "quality": 2,
                "seat_id": 4,
                "fight_score": 999,
            }
        },
    )

    assert result["status"] == "advance_mine"
    assert result["excluded_mine_id"] == 1


def test_friendly_location_group_is_preferred_then_keeps_display_order():
    result = choose_dongtian_seating_action(
        _snapshot(
            teams=[_team(2, idle=True, power=300)],
            mines=[
                {
                    "id": 1,
                    "cross_union_id": 77,
                    "seats_complete": True,
                    "seats": [_seat(1, empty=False, union_id=66, quality=1)],
                },
                {
                    "id": 2,
                    "cross_union_id": 99,
                    "seats_complete": True,
                    "seats": [_seat(4, empty=True)],
                },
            ],
        )
    )

    assert result["status"] == "ready"
    assert result["target"]["seat_key"] == "2:2:4"
    assert result["strategy_name"] == "friendly_top_down_only"
    assert result["allow_nonfriendly"] is False


def test_same_location_empty_seat_avoids_an_unneeded_defender_request():
    mine = {
        "id": 1,
        "cross_union_id": 99,
        "seats_complete": True,
        "seats": [
            _seat(1, empty=False, union_id=66, quality=1),
            _seat(4, empty=True),
        ],
    }

    result = scan_dongtian_mine_next_action(
        mine,
        own_union_id=99,
        idle_teams=[_team(2, idle=True, power=300)],
    )

    assert result["status"] == "ready"
    assert result["action"] == "occupy_empty"
    assert result["target"]["seat_key"] == "1:2:4"


def test_old_compatible_cache_cannot_bypass_first_defender():
    mine = {
        "id": 1,
        "cross_union_id": 99,
        "seats_complete": True,
        "seats": [
            _seat(1, empty=False, union_id=66, quality=1),
            _seat(4, empty=False, union_id=77),
        ],
    }

    result = scan_dongtian_mine_next_action(
        mine,
        own_union_id=99,
        idle_teams=[_team(2, idle=True, power=300)],
        seat_details={
            "1:2:4": {
                "complete": True,
                "freshness": "compatible_unproven",
                "mine_id": 1,
                "quality": 2,
                "seat_id": 4,
                "fight_score": 1,
            }
        },
    )

    assert result["status"] == "need_detail"
    assert result["action"] == "refresh_defender"
    assert result["target"]["seat_key"] == "1:2:4"


def test_scan_advances_only_after_fresh_detail_proves_first_seat_unsafe():
    mine = {
        "id": 1,
        "cross_union_id": 99,
        "seats_complete": True,
        "seats": [
            _seat(1, empty=False, union_id=66, quality=1),
            _seat(4, empty=False, union_id=77),
            _seat(5, empty=True),
        ],
    }

    result = scan_dongtian_mine_next_action(
        mine,
        own_union_id=99,
        idle_teams=[_team(2, idle=True, power=300)],
        seat_details={
            "1:1:1": {
                "complete": True,
                "freshness": "fresh_packet",
                "mine_id": 1,
                "quality": 1,
                "seat_id": 1,
                "fight_score": 300,
            }
        },
    )

    assert result["status"] == "ready"
    assert result["action"] == "occupy_empty"
    assert result["target"]["seat_key"] == "1:2:5"


def test_complete_friendly_mine_reports_all_twelve_seats_as_friendly():
    result = classify_dongtian_mine_seats(
        {
            "id": 5,
            "cross_union_id": 99,
            "seats_complete": True,
            "seats": _complete_seats(),
        },
        own_union_id=99,
    )

    assert result["ok"] is True
    assert result["friendly_place"] is True
    assert result["master_count"] == 3
    assert result["follower_count"] == 9
    assert result["class_counts"] == {
        "empty": 0,
        "friendly": 12,
        "enemy": 0,
        "neutral_unknown": 0,
    }
    assert result["all_occupied_friendly"] is True
    assert result["has_shallow_candidate"] is False
    assert [row["seat_id"] for row in result["seats"][:3]] == [1, 2, 3]
    assert [row["ui_route"] for row in result["seats"][:3]] == [
        "master_list_first_empty",
        "master_list_first_empty",
        "master_list_first_empty",
    ]
    assert result["follower_visual_order_seat_ids"] == [5, 12, 7, 11, 8, 4, 10, 9, 6]


def test_seat_classifier_distinguishes_empty_enemy_and_neutral_in_native_identity():
    result = classify_dongtian_mine_seats(
        {
            "id": 5,
            "cross_union_id": 99,
            "seats_complete": True,
            "seats": _complete_seats(
                classifications={4: "empty", 5: "enemy", 6: "neutral_unknown"}
            ),
        },
        own_union_id=99,
    )

    assert result["ok"] is True
    assert result["class_counts"] == {
        "empty": 1,
        "friendly": 9,
        "enemy": 1,
        "neutral_unknown": 1,
    }
    by_id = {row["seat_id"]: row for row in result["seats"]}
    assert by_id[4]["classification"] == "empty"
    assert by_id[4]["shallow_action"] == "occupy_empty"
    assert by_id[5]["classification"] == "enemy"
    assert by_id[5]["shallow_action"] == "inspect_defender"
    assert by_id[6]["classification"] == "neutral_unknown"
    assert by_id[6]["shallow_action"] == "inspect_defender"
    assert by_id[5]["ui_route"] == "follower_seat_direct"
    assert by_id[5]["visual_order"] == 0


def test_seat_classifier_fails_closed_when_three_plus_nine_contract_is_missing():
    result = classify_dongtian_mine_seats(
        {
            "id": 5,
            "cross_union_id": 99,
            "seats_complete": True,
            "seats": _complete_seats()[:-1],
        },
        own_union_id=99,
    )

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["observed_seat_count"] == 11
    assert result["all_occupied_friendly"] is False
    assert result["has_shallow_candidate"] is False


def _mine(mine_id: int, *, union_id: int, classifications: dict[int, str] | None = None) -> dict:
    return {
        "id": mine_id,
        "name": f"mine-{mine_id}",
        "cross_union_id": union_id,
        "seats_complete": True,
        "seats": _complete_seats(classifications=classifications),
    }


def test_shallow_friendly_scan_continues_all_friendly_then_stops_at_empty():
    result = scan_dongtian_friendly_locations_shallow(
        [
            _mine(1, union_id=77),
            _mine(5, union_id=99),
            _mine(6, union_id=99, classifications={8: "empty"}),
            _mine(7, union_id=99, classifications={4: "enemy"}),
        ],
        own_union_id=99,
    )

    assert result["status"] == "stop_for_candidate"
    assert result["scanned_friendly_mine_count"] == 2
    assert [item["outcome"] for item in result["location_summaries"]] == [
        "skip_nonfriendly",
        "continue_all_friendly_full",
        "stop_for_candidate",
    ]
    assert result["stop_target"]["mine_id"] == 6
    assert result["stop_target"]["seat"]["seat_id"] == 8
    assert result["stop_target"]["seat"]["classification"] == "empty"


def test_shallow_friendly_scan_is_lazy_and_does_not_touch_mines_after_stop():
    def mines():
        yield _mine(5, union_id=99)
        yield _mine(6, union_id=99, classifications={4: "enemy"})
        raise AssertionError("stream read past the first stop target")

    result = scan_dongtian_friendly_locations_shallow(
        mines(),
        own_union_id=99,
    )

    assert result["status"] == "stop_for_candidate"
    assert result["stop_target"]["mine_id"] == 6
    assert result["stop_target"]["seat"]["classification"] == "enemy"


def test_shallow_friendly_scan_stops_on_neutral_unknown_without_loading_detail():
    result = scan_dongtian_friendly_locations_shallow(
        [_mine(5, union_id=99, classifications={6: "neutral_unknown"})],
        own_union_id=99,
    )

    assert result["status"] == "stop_for_candidate"
    assert result["reason"] == "friendly_mine_has_neutral_unknown"
    assert result["stop_target"]["seat"]["shallow_action"] == "inspect_defender"


def test_shallow_friendly_scan_skips_location_already_used_by_own_team():
    result = scan_dongtian_friendly_locations_shallow(
        [
            _mine(5, union_id=99, classifications={4: "empty"}),
            _mine(6, union_id=99),
        ],
        own_union_id=99,
        occupied_mine_ids=[5],
    )

    assert result["status"] == "friendly_locations_exhausted"
    assert [item["outcome"] for item in result["location_summaries"]] == [
        "skip_own_team_present",
        "continue_all_friendly_full",
    ]
