from __future__ import annotations

"""Pure, fail-closed postcondition for one Dongtian seating commit.

This module performs no Runtime read and no GUI action.  A future executor may
pass it one freshly decoded full Dongtian snapshot after an occupy/battle
transition.  Success requires the chosen team, the exact location and seat,
the visible seat owner, and the one-team-per-location invariant to agree.
"""

from typing import Any, Mapping


TEAM_STATE_OCCUPY = 2


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _result(
    *,
    ok: bool,
    status: str,
    outcome: str,
    reason: str,
    retryable: bool,
    target: Mapping[str, Any],
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "outcome": outcome,
        "reason": reason,
        "retryable": retryable,
        "target": dict(target),
        "evidence": dict(evidence or {}),
    }


def evaluate_dongtian_seating_postcondition(
    snapshot: Mapping[str, Any],
    target: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove the exact result of one authorized occupy/battle transition.

    ``snapshot`` must be a fresh full ``read_dongtian_snapshot`` result.  A
    shallow probe cannot prove that no second own team occupies the location.
    The helper never treats a missing/incoherent fact as success.
    """

    mine_id = _positive_int(target.get("mine_id"))
    quality = _positive_int(target.get("quality"))
    seat_id = _positive_int(target.get("seat_id"))
    team_id = _positive_int(target.get("team_id"))
    expected = {
        "mine_id": mine_id,
        "quality": quality,
        "seat_id": seat_id,
        "team_id": team_id,
        "mode": str(target.get("mode") or ""),
    }
    if None in {mine_id, quality, seat_id, team_id} or quality not in {1, 2}:
        return _result(
            ok=False,
            status="incomplete",
            outcome="unknown",
            reason="target_identity_incomplete",
            retryable=False,
            target=expected,
        )
    if not (
        snapshot.get("available") is True
        and snapshot.get("seating_summary_complete") is True
        and snapshot.get("teams_complete") is True
        and snapshot.get("mines_seating_complete") is True
    ):
        return _result(
            ok=False,
            status="incomplete",
            outcome="unknown",
            reason="fresh_full_snapshot_incomplete",
            retryable=True,
            target=expected,
        )

    teams = [item for item in snapshot.get("teams") or [] if isinstance(item, Mapping)]
    team_matches = [item for item in teams if _positive_int(item.get("id")) == team_id]
    mines = [item for item in snapshot.get("mines") or [] if isinstance(item, Mapping)]
    mine_matches = [item for item in mines if _positive_int(item.get("id")) == mine_id]
    if len(team_matches) != 1 or len(mine_matches) != 1:
        return _result(
            ok=False,
            status="incomplete",
            outcome="unknown",
            reason=(
                "target_team_not_unique"
                if len(team_matches) != 1
                else "target_mine_not_unique"
            ),
            retryable=True,
            target=expected,
            evidence={
                "target_team_match_count": len(team_matches),
                "target_mine_match_count": len(mine_matches),
            },
        )

    team = team_matches[0]
    mine = mine_matches[0]
    seats = [item for item in mine.get("seats") or [] if isinstance(item, Mapping)]
    seat_matches = [
        item
        for item in seats
        if _positive_int(item.get("quality")) == quality
        and _positive_int(item.get("id")) == seat_id
    ]
    if len(seat_matches) != 1 or seat_matches[0].get("complete") is not True:
        return _result(
            ok=False,
            status="incomplete",
            outcome="unknown",
            reason="target_seat_not_unique_or_incomplete",
            retryable=True,
            target=expected,
            evidence={"target_seat_match_count": len(seat_matches)},
        )
    seat = seat_matches[0]

    occupied_here = [
        item
        for item in teams
        if _positive_int(item.get("state")) == TEAM_STATE_OCCUPY
        and _positive_int(item.get("mine_id")) == mine_id
    ]
    occupied_team_ids = sorted(
        team_identity
        for item in occupied_here
        if (team_identity := _positive_int(item.get("id"))) is not None
    )
    other_team_ids = [item for item in occupied_team_ids if item != team_id]
    if other_team_ids:
        return _result(
            ok=False,
            status="location_exclusivity_violated",
            outcome="invariant_violation",
            reason="second_own_team_occupies_target_mine",
            retryable=False,
            target=expected,
            evidence={"occupied_team_ids": occupied_team_ids},
        )

    team_state = _positive_int(team.get("state"))
    observed_team_mine_id = _positive_int(team.get("mine_id"))
    observed_team_seat_id = _positive_int(team.get("seat_index"))
    guarder_role_id = _positive_int(seat.get("guarder_role_id"))
    own_role_id = _positive_int(snapshot.get("own_role_id"))
    guarder_present = seat.get("guarder_present") is True
    seat_empty = bool(seat.get("empty")) or _positive_int(seat.get("guarder_type")) is None
    evidence = {
        "team_state": team_state,
        "team_mine_id": observed_team_mine_id,
        "team_seat_id": observed_team_seat_id,
        "occupied_team_ids": occupied_team_ids,
        "seat_empty": seat_empty,
        "guarder_present": guarder_present,
        "guarder_role_id": guarder_role_id,
        "own_role_id": own_role_id,
    }

    if team_state == TEAM_STATE_OCCUPY and (
        observed_team_mine_id != mine_id or observed_team_seat_id != seat_id
    ):
        return _result(
            ok=False,
            status="target_mismatch",
            outcome="wrong_destination",
            reason="target_team_occupied_different_mine_or_seat",
            retryable=False,
            target=expected,
            evidence=evidence,
        )

    exact_team = bool(
        team_state == TEAM_STATE_OCCUPY
        and observed_team_mine_id == mine_id
        and observed_team_seat_id == seat_id
    )
    exact_owner = bool(
        own_role_id is not None
        and guarder_present
        and not seat_empty
        and guarder_role_id == own_role_id
    )
    if exact_team and exact_owner:
        return _result(
            ok=True,
            status="occupied_confirmed",
            outcome="success",
            reason="exact_team_mine_seat_and_owner_confirmed",
            retryable=False,
            target=expected,
            evidence=evidence,
        )
    if exact_team:
        return _result(
            ok=False,
            status="postcondition_conflict",
            outcome="unknown",
            reason="team_occupy_fact_conflicts_with_seat_owner",
            retryable=True,
            target=expected,
            evidence=evidence,
        )

    original_guarder_role_id = _positive_int(target.get("guarder_role_id"))
    if guarder_present and not seat_empty:
        if (
            expected["mode"] == "replace_weaker_enemy"
            and original_guarder_role_id is not None
            and guarder_role_id == original_guarder_role_id
        ):
            return _result(
                ok=False,
                status="battle_failed",
                outcome="battle_failed",
                reason="original_defender_still_occupies_target_seat",
                retryable=True,
                target=expected,
                evidence=evidence,
            )
        return _result(
            ok=False,
            status="seat_taken",
            outcome="seat_taken",
            reason="target_seat_is_occupied_by_another_role",
            retryable=True,
            target=expected,
            evidence=evidence,
        )

    return _result(
        ok=False,
        status="occupy_not_committed",
        outcome="no_state_change",
        reason="target_team_not_occupying_and_target_seat_still_empty",
        retryable=True,
        target=expected,
        evidence=evidence,
    )


__all__ = ["evaluate_dongtian_seating_postcondition"]
