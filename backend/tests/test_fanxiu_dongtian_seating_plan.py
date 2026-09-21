from backend.core.fanxiu.data_annotation.dongtian_seating_plan import plan_dongtian_next_team, plan_dongtian_friend_staging
from backend.core.fanxiu.data_annotation.tasks.dongtian_seating_job import choose_dongtian_ranked_empty_target


OWN = 42


def _team(team_id, power, *, mine_id=0, seat_id=0):
    return {
        "id": team_id, "fight_score": power, "complete": True, "dead": False,
        "state": 1 if mine_id == 0 else 2, "mine_id": mine_id, "seat_index": seat_id,
    }


def _seat(seat_id, quality, kind="empty", *, union=99):
    return {
        "id": seat_id, "quality": quality, "complete": True,
        "empty": kind == "empty", "guarder_present": kind != "empty",
        "guarder_type": {"empty": 0, "neutral": 1, "player": 2}[kind],
        "guarder_cross_union_id": union if kind == "player" else 0,
    }


def _mine(mine_id, group, y, seats, union=OWN):
    return {
        "id": mine_id, "name": str(mine_id), "config_group": group,
        "config_pos_y": y, "cross_union_id": union,
        "seats_complete": True, "seats": seats,
    }


def _snapshot(teams, mines):
    if not any(row["id"] == 3 for row in teams):
        teams = [*teams, _team(3, 100, mine_id=999, seat_id=9)]
    return {
        "available": True, "seating_summary_complete": True,
        "own_union_id": OWN, "teams": teams, "mines": mines,
    }


def test_future_own_white_jade_precedes_cave_and_weakest_team_goes_first():
    snapshot = _snapshot(
        [_team(1, 900), _team(2, 300), _team(3, 100)],
        [_mine(7, 3, -1409, [_seat(4, 2)]), _mine(1, 1, 0, [_seat(2, 1)])],
    )
    decision = plan_dongtian_next_team(snapshot)
    assert (decision["mine_id"], decision["team_id"], decision["quality"], decision["planning_score"]) == (1, 3, 1, 0)


def test_nonfriendly_upper_levels_are_skipped_and_neutral_keeps_real_power():
    snapshot = _snapshot(
        [_team(2, 300), _team(3, 100, mine_id=7, seat_id=9)],
        [_mine(3, 2, -400, [_seat(4, 2)], union=99),
         _mine(7, 3, -1409, [_seat(9, 2)], union=OWN),
         _mine(8, 3, -1609, [_seat(5, 2, "neutral")])],
    )
    assert plan_dongtian_next_team(snapshot)["status"] == "needs_defender_scores"
    decision = plan_dongtian_next_team(snapshot, defender_scores={"8:2:5": 31})
    assert (decision["mine_id"], decision["team_id"], decision["defender_fight_score"], decision["action"]) == (8, 2, 31, "challenge_seat")


def test_same_mine_master_empty_beats_follower_empty_by_adaptive_base():
    snapshot = _snapshot([_team(2, 300)], [_mine(7, 3, -1409, [_seat(4, 2), _seat(3, 1)])])
    decision = plan_dongtian_next_team(snapshot)
    assert (decision["quality"], decision["seat_id"], decision["planning_score"]) == (1, 3, 0)


def test_master_neutral_always_beats_follower_empty_or_neutral_even_at_huge_power():
    snapshot = _snapshot(
        [_team(2, 10**30)],
        [_mine(7, 3, -1409, [
            _seat(1, 1, "neutral"), _seat(2, 2), _seat(3, 2, "neutral"),
        ])],
    )
    decision = plan_dongtian_next_team(
        snapshot, defender_scores={"7:1:1": 10**25, "7:2:3": 1},
    )
    assert (decision["quality"], decision["seat_id"], decision["defender_fight_score"]) == (1, 1, 10**25)


def test_real_combat_not_mapped_score_controls_whether_team_can_win():
    snapshot = _snapshot(
        [_team(2, 100)],
        [_mine(7, 3, -1409, [_seat(1, 1, "neutral"), _seat(2, 2)])],
    )
    decision = plan_dongtian_next_team(snapshot, defender_scores={"7:1:1": 101})
    assert (decision["quality"], decision["seat_id"]) == (2, 2)


def test_team_one_values_fudi_master_above_dongtian_follower():
    snapshot = _snapshot(
        [_team(1, 900)],
        [_mine(7, 3, -1409, [_seat(4, 2)]),
         _mine(27, 4, -3015, [_seat(4, 2)]),
         _mine(28, 4, -3200, [_seat(3, 1)], union=99)],
    )
    decision = plan_dongtian_next_team(snapshot)
    assert (decision["mine_id"], decision["quality"], decision["status"]) == (28, 1, "needs_conquest_research")


def test_team_two_keeps_safe_dongtian_follower_above_fudi_master():
    snapshot = _snapshot([_team(2, 300)], [
        _mine(7, 3, -1409, [_seat(4, 2)]),
        _mine(28, 4, -3200, [_seat(3, 1)]),
    ])
    decision = plan_dongtian_next_team(snapshot)
    assert (decision["mine_id"], decision["quality"], decision["objective"]) == (7, 2, "safety_top_down")


def test_team_one_yield_order_keeps_high_tier_options_and_protects_allies():
    snapshot = _snapshot([_team(1, 900)], [
        _mine(1, 1, 0, [_seat(4, 2)]),
        _mine(7, 3, -1409, [_seat(1, 1, "player", union=OWN)]),
        _mine(28, 4, -3200, [_seat(3, 1)]),
    ])
    assert plan_dongtian_next_team(snapshot)["mine_id"] == 1
    snapshot["mines"].pop(0)
    assert plan_dongtian_next_team(snapshot, defender_scores={"7:1:1": 80})["mine_id"] == 28
    decision = plan_dongtian_next_team(snapshot, defender_scores={"7:1:1": 79})
    assert (decision["mine_id"], decision["action"]) == (7, "stage_friendly_swap")


def test_team_one_prefers_fudi_master_and_enemy_conquest_only_after_upper_map():
    snapshot = _snapshot(
        [_team(1, 900)],
        [_mine(27, 4, -3015, [_seat(4, 2)]),
         _mine(28, 4, -3200, [_seat(3, 1)], union=99)],
    )
    decision = plan_dongtian_next_team(snapshot)
    assert (decision["mine_id"], decision["quality"], decision["status"]) == (28, 1, "needs_conquest_research")


def test_player_defenders_need_complete_scores_before_positive_comparison():
    snapshot = _snapshot([_team(2, 300)], [_mine(7, 3, -1409, [_seat(4, 2, "player"), _seat(5, 2, "player")])])
    assert plan_dongtian_next_team(snapshot)["status"] == "needs_defender_scores"
    decision = plan_dongtian_next_team(snapshot, defender_scores={"7:2:4": 100, "7:2:5": 200})
    assert (decision["seat_id"], decision["planning_score"]) == (4, 100)


def test_friendly_replacement_uses_strict_80_percent_of_team3_even_for_stronger_team1():
    snapshot = _snapshot(
        [_team(1, 900), _team(3, 100, mine_id=999, seat_id=9)],
        [_mine(7, 3, -1409, [_seat(4, 2, "player", union=OWN), _seat(5, 2, "player", union=OWN)])],
    )
    decision = plan_dongtian_next_team(snapshot, defender_scores={"7:2:4": 80, "7:2:5": 79})
    assert (decision["seat_id"], decision["planning_score"], decision["status"]) == (5, 79, "needs_friend_swap_research")


def test_engineering_empty_fast_path_never_chooses_enemy_mine_or_team1_fudi_follower():
    snapshot = _snapshot(
        [_team(1, 900)],
        [_mine(20, 4, -2500, [_seat(4, 2)], union=99),
         _mine(21, 4, -2600, [_seat(4, 2)])],
    )
    assert choose_dongtian_ranked_empty_target(snapshot) is None


def test_friend_staging_prefers_master_and_excludes_entire_mine_with_friend_team():
    other_team = {**_seat(12, 2, "player", union=OWN), "guarder_role_id": 200}
    snapshot = {**_snapshot([_team(2, 300)], [
        _mine(20, 4, -2000, [_seat(4, 2)]),
        _mine(21, 4, -2100, [_seat(2, 1), other_team]),
        _mine(22, 4, -2200, [_seat(3, 1)]),
    ]), "own_role_id": 100}
    decision = plan_dongtian_friend_staging(snapshot, friend_role_id=200, team_id=2)
    assert (decision["mine_id"], decision["quality"], decision["seat_id"]) == (22, 1, 3)
    snapshot["mines"].pop()
    decision = plan_dongtian_friend_staging(snapshot, friend_role_id=200, team_id=2)
    assert (decision["mine_id"], decision["quality"]) == (20, 2)


def test_friend_staging_requires_master_power_before_falling_back_and_never_kicks_second_friend():
    unknown_player = _seat(3, 1, "player", union=OWN)
    snapshot = {**_snapshot([_team(2, 300)], [
        _mine(20, 4, -2000, [_seat(4, 2)]),
        _mine(21, 4, -2100, [_seat(2, 1, "neutral")]),
        _mine(22, 4, -2200, [_seat(2, 1), unknown_player]),
    ]), "own_role_id": 100}
    assert plan_dongtian_friend_staging(snapshot, friend_role_id=200, team_id=2)["status"] == "needs_defender_scores"
    decision = plan_dongtian_friend_staging(snapshot, friend_role_id=200, team_id=2, defender_scores={"21:1:2": 301})
    assert (decision["mine_id"], decision["quality"]) == (20, 2)
