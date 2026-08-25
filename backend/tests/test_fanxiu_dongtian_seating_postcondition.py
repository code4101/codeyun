from __future__ import annotations

from backend.core.fanxiu.data_annotation.dongtian_seating_postcondition import (
    evaluate_dongtian_seating_postcondition,
)


def _team(team_id: int, *, state: int, mine_id: int, seat_id: int) -> dict:
    return {
        "id": team_id,
        "state": state,
        "mine_id": mine_id,
        "seat_index": seat_id,
        "complete": True,
    }


def _seat(*, role_id: int | None, empty: bool = False) -> dict:
    return {
        "quality": 2,
        "id": 4,
        "complete": True,
        "empty": empty,
        "guarder_present": role_id is not None,
        "guarder_type": 0 if empty else 2,
        "guarder_role_id": role_id,
    }


def _snapshot(*, teams: list[dict], seat: dict) -> dict:
    return {
        "available": True,
        "seating_summary_complete": True,
        "teams_complete": True,
        "mines_seating_complete": True,
        "own_role_id": 1001,
        "teams": teams,
        "mines": [{"id": 7, "seats": [seat]}],
    }


def _target(*, mode: str = "occupy_empty", defender: int | None = None) -> dict:
    return {
        "mine_id": 7,
        "quality": 2,
        "seat_id": 4,
        "team_id": 3,
        "mode": mode,
        "guarder_role_id": defender,
    }


def test_exact_team_mine_seat_owner_and_location_exclusivity_prove_success():
    result = evaluate_dongtian_seating_postcondition(
        _snapshot(teams=[_team(3, state=2, mine_id=7, seat_id=4)], seat=_seat(role_id=1001)),
        _target(),
    )

    assert result["ok"] is True
    assert result["status"] == "occupied_confirmed"
    assert result["evidence"]["occupied_team_ids"] == [3]


def test_second_own_team_on_same_mine_is_invariant_violation():
    result = evaluate_dongtian_seating_postcondition(
        _snapshot(
            teams=[
                _team(3, state=2, mine_id=7, seat_id=4),
                _team(5, state=2, mine_id=7, seat_id=8),
            ],
            seat=_seat(role_id=1001),
        ),
        _target(),
    )

    assert result["status"] == "location_exclusivity_violated"
    assert result["outcome"] == "invariant_violation"
    assert result["retryable"] is False


def test_original_defender_remaining_is_structured_battle_failure():
    result = evaluate_dongtian_seating_postcondition(
        _snapshot(teams=[_team(3, state=1, mine_id=0, seat_id=0)], seat=_seat(role_id=2002)),
        _target(mode="replace_weaker_enemy", defender=2002),
    )

    assert result["status"] == "battle_failed"
    assert result["outcome"] == "battle_failed"
    assert result["reason"] == "original_defender_still_occupies_target_seat"


def test_different_role_on_target_is_structured_seat_taken():
    result = evaluate_dongtian_seating_postcondition(
        _snapshot(teams=[_team(3, state=1, mine_id=0, seat_id=0)], seat=_seat(role_id=3003)),
        _target(mode="replace_weaker_enemy", defender=2002),
    )

    assert result["status"] == "seat_taken"
    assert result["outcome"] == "seat_taken"
    assert result["retryable"] is True


def test_target_team_occupying_wrong_seat_is_not_success():
    result = evaluate_dongtian_seating_postcondition(
        _snapshot(teams=[_team(3, state=2, mine_id=7, seat_id=8)], seat=_seat(role_id=3003)),
        _target(),
    )

    assert result["status"] == "target_mismatch"
    assert result["outcome"] == "wrong_destination"


def test_empty_target_without_team_transition_is_not_committed():
    result = evaluate_dongtian_seating_postcondition(
        _snapshot(teams=[_team(3, state=1, mine_id=0, seat_id=0)], seat=_seat(role_id=None, empty=True)),
        _target(),
    )

    assert result["status"] == "occupy_not_committed"
    assert result["outcome"] == "no_state_change"


def test_incomplete_full_snapshot_fails_closed():
    snapshot = _snapshot(teams=[], seat=_seat(role_id=None, empty=True))
    snapshot["seating_summary_complete"] = False

    result = evaluate_dongtian_seating_postcondition(snapshot, _target())

    assert result["status"] == "incomplete"
    assert result["reason"] == "fresh_full_snapshot_incomplete"
    assert result["retryable"] is True
