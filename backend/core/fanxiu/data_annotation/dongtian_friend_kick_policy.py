"""Pure, fail-closed policy for conservative Dongtian friend-seat swaps.

The rule is stricter than the client: team 3 is always the baseline; a
defender must be below 80% of its current score; and only the lowest current
defender in each mine/location is considered. Personal battle score is not a
factor. This module selects candidates but never authorizes a GUI action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping


TEAM3_SAFE_RATIO_NUMERATOR = 4
TEAM3_SAFE_RATIO_DENOMINATOR = 5


@dataclass(frozen=True)
class DongtianFriendSeatCandidate:
    """The unique lowest-score, safely eligible seat in one mine."""

    mine_id: int
    quality: int
    seat_id: int
    role_id: int
    defender_xianlv_fight_score: int


@dataclass(frozen=True)
class DongtianFriendKickPolicyDecision:
    """Read-only result; ``candidate`` is never permission to click swap."""

    candidate: bool
    status: Literal["eligible", "ineligible", "ambiguous"]
    complete: bool
    reason: str
    my_team3_fight_score: int | None = None
    defender_current_xianlv_fight_score: int | None = None
    strict_upper_bound_score: int | None = None


@dataclass(frozen=True)
class DongtianFriendMineSelection:
    """One conservative candidate at most per mine/location."""

    complete: bool
    status: Literal["ready", "no_candidate", "ambiguous"]
    reason: str
    candidates: tuple[DongtianFriendSeatCandidate, ...] = ()


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def evaluate_dongtian_friend_xianlv_policy(
    *,
    my_team3_fight_score: Any,
    defender_current_xianlv_fight_score: Any,
    current_observation_confirmed: bool,
) -> DongtianFriendKickPolicyDecision:
    """Evaluate the strict 80%-of-team-3 safety boundary without floats."""

    my_score = _positive_int(my_team3_fight_score)
    defender_score = _positive_int(defender_current_xianlv_fight_score)
    if my_score is None:
        return DongtianFriendKickPolicyDecision(
            candidate=False,
            status="ambiguous",
            complete=False,
            reason="my_team3_fight_score_missing_or_nonpositive",
        )
    if defender_score is None:
        return DongtianFriendKickPolicyDecision(
            candidate=False,
            status="ambiguous",
            complete=False,
            reason="defender_current_xianlv_fight_score_missing_or_nonpositive",
            my_team3_fight_score=my_score,
        )
    if current_observation_confirmed is not True:
        return DongtianFriendKickPolicyDecision(
            candidate=False,
            status="ambiguous",
            complete=False,
            reason="defender_current_observation_not_confirmed",
            my_team3_fight_score=my_score,
            defender_current_xianlv_fight_score=defender_score,
        )

    strict_upper_bound = (
        my_score * TEAM3_SAFE_RATIO_NUMERATOR // TEAM3_SAFE_RATIO_DENOMINATOR
    )
    candidate = (
        defender_score * TEAM3_SAFE_RATIO_DENOMINATOR
        < my_score * TEAM3_SAFE_RATIO_NUMERATOR
    )
    return DongtianFriendKickPolicyDecision(
        candidate=candidate,
        status="eligible" if candidate else "ineligible",
        complete=True,
        reason=(
            "defender_strictly_below_80_percent_of_team3"
            if candidate
            else "defender_not_strictly_below_80_percent_of_team3"
        ),
        my_team3_fight_score=my_score,
        defender_current_xianlv_fight_score=defender_score,
        strict_upper_bound_score=strict_upper_bound,
    )


def select_dongtian_friend_swap_candidates(
    *,
    my_team3_fight_score: Any,
    seats: Iterable[Mapping[str, Any]],
    own_occupied_mine_ids: Iterable[Any] = (),
) -> DongtianFriendMineSelection:
    """Select the lowest current defender in each unoccupied mine.

    Each supplied row must be fresh and identity-confirmed. An incomplete row
    makes the whole result ambiguous rather than selecting from a partial set.
    Callers must supply every occupied friend seat for each evaluated mine.
    """

    my_score = _positive_int(my_team3_fight_score)
    if my_score is None:
        return DongtianFriendMineSelection(
            complete=False,
            status="ambiguous",
            reason="my_team3_fight_score_missing_or_nonpositive",
        )

    occupied_mines: set[int] = set()
    for raw_mine_id in own_occupied_mine_ids:
        mine_id = _positive_int(raw_mine_id)
        if mine_id is None:
            return DongtianFriendMineSelection(
                complete=False,
                status="ambiguous",
                reason="own_occupied_mine_id_invalid",
            )
        occupied_mines.add(mine_id)

    per_mine: dict[int, DongtianFriendSeatCandidate] = {}
    seen_keys: set[tuple[int, int, int]] = set()
    for row in seats:
        mine_id = _positive_int(row.get("mine_id"))
        quality = _positive_int(row.get("quality"))
        seat_id = _positive_int(row.get("seat_id"))
        role_id = _positive_int(row.get("role_id"))
        score = _positive_int(row.get("current_xianlv_fight_score"))
        confirmed = row.get("current_observation_confirmed") is True
        if None in {mine_id, quality, seat_id, role_id, score} or not confirmed:
            return DongtianFriendMineSelection(
                complete=False,
                status="ambiguous",
                reason="seat_observation_incomplete_or_stale",
            )
        key = (mine_id, quality, seat_id)
        if key in seen_keys:
            return DongtianFriendMineSelection(
                complete=False,
                status="ambiguous",
                reason="duplicate_seat_observation",
            )
        seen_keys.add(key)
        if mine_id in occupied_mines:
            continue
        candidate = DongtianFriendSeatCandidate(
            mine_id=mine_id,
            quality=quality,
            seat_id=seat_id,
            role_id=role_id,
            defender_xianlv_fight_score=score,
        )
        previous = per_mine.get(mine_id)
        if previous is None or (
            candidate.defender_xianlv_fight_score,
            candidate.quality,
            candidate.seat_id,
        ) < (
            previous.defender_xianlv_fight_score,
            previous.quality,
            previous.seat_id,
        ):
            per_mine[mine_id] = candidate

    selected = tuple(
        candidate
        for _, candidate in sorted(per_mine.items())
        if evaluate_dongtian_friend_xianlv_policy(
            my_team3_fight_score=my_score,
            defender_current_xianlv_fight_score=(
                candidate.defender_xianlv_fight_score
            ),
            current_observation_confirmed=True,
        ).candidate
    )
    return DongtianFriendMineSelection(
        complete=True,
        status="ready" if selected else "no_candidate",
        reason=(
            "lowest_safe_candidate_selected_per_mine"
            if selected
            else "no_mine_minimum_below_80_percent_of_team3"
        ),
        candidates=selected,
    )


__all__ = [
    "DongtianFriendKickPolicyDecision",
    "DongtianFriendMineSelection",
    "DongtianFriendSeatCandidate",
    "TEAM3_SAFE_RATIO_DENOMINATOR",
    "TEAM3_SAFE_RATIO_NUMERATOR",
    "evaluate_dongtian_friend_xianlv_policy",
    "select_dongtian_friend_swap_candidates",
]
