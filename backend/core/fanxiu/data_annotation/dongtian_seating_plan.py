"""Pure Dongtian seating plan: team objective, location, then seat difficulty.

One Runtime snapshot is enough to plan a team. Refresh after that team has
finished; GUI navigation must not poll expensive Runtime every few seconds.
Planning is not click authority. The executor still validates the destination
and handles combat/postconditions. Enemy Fudi conquest and friendly swapping
are separate state machines and remain explicitly gated until observed.
"""

from __future__ import annotations

from typing import Any, Mapping


# Miner.minesResource ordinary hourly resource amounts. Team 1 follows the
# user's yield-first preference (in particular Fudi master > Dongtian follower),
# not a raw sum of unrelated currencies. These values express that default
# utility ordering; they are NOT a prediction of uninterrupted realized income.
# Team 2/3 instead keep geographic top-down safety as their primary objective.
TEAM1_SEAT_YIELD_PRIORITY = {
    (1, 1): 7800, (1, 2): 5400,  # 白玉京
    (2, 1): 6000, (2, 2): 3600,  # 大罗 / 太明
    (3, 1): 4200, (3, 2): 2400,  # 洞天
    (4, 1): 3000,                # 福地: team 1 seeks masters only
}


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _seat_score(
    seat: Mapping[str, Any],
    defender_scores: Mapping[str, int],
    mine_id: int,
) -> tuple[int | None, str]:
    quality = _integer(seat.get("quality"))
    seat_id = _integer(seat.get("id"))
    if quality not in {1, 2} or seat_id is None or seat.get("complete") is not True:
        return None, "incomplete"
    if seat.get("empty") is True and seat.get("guarder_present") is False:
        return 0, "empty"
    key = f"{mine_id}:{quality}:{seat_id}"
    if seat.get("guarder_type") == 1:
        # Runtime's shallow seat list identifies 散仙 but has no combat
        # score. The live seat detail must supply the real defender score.
        score = _integer(defender_scores.get(key))
        return (score, "neutral") if score is not None and score > 0 else (None, "needs_detail")
    if seat.get("guarder_type") != 2:
        return None, "incomplete"
    score = _integer(defender_scores.get(key))
    return (score, "player") if score is not None and score > 0 else (None, "needs_detail")


def plan_dongtian_next_team(
    snapshot: Mapping[str, Any],
    *,
    defender_scores: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Return the next read-only decision, never a GUI click instruction.

    Map order is descending MinesPlace ``config_pos_y``: 白玉京, 大罗/太明,
    洞天, then 福地. Before 福地, only own-union mines are eligible. In 福地,
    team 2/3 tries own mines before enemy mines: safety beats short-term yield.
    Team 1 is yield-first, ordering seat tiers by TEAM1_SEAT_YIELD_PRIORITY;
    thus a Fudi master precedes a Dongtian follower. Within the same reward
    tier keep geographic order, and friendly Fudi before enemy conquest.
    Friend replacement always retains the strict 80%-of-team-3 threshold.
    Within one mine, empty=0 and neutral=its verified real combat score.
    Let B=max(all known seat combat scores)+1. A master empty/neutral maps to
    raw score, a follower empty/neutral to B+raw score. This single numeric
    key guarantees every master empty/neutral precedes every follower
    empty/neutral, without a fragile fixed divisor. Occupied players retain
    their real score; viability always compares unmapped combat scores.
    Missing neutral/player scores require a detail scan when they could
    change the minimum.
    """

    if snapshot.get("available") is not True or snapshot.get("seating_summary_complete") is not True:
        return {"status": "runtime_incomplete", "reason": snapshot.get("reason")}
    own_union_id = _integer(snapshot.get("own_union_id"))
    if own_union_id is None:
        return {"status": "runtime_incomplete", "reason": "own_union_id_missing"}
    teams = [row for row in snapshot.get("teams") or [] if isinstance(row, Mapping)]
    idle = sorted(
        (
            row for row in teams
            if row.get("complete") is True
            and row.get("state") == 1
            and row.get("mine_id") == 0
            and row.get("dead") is False
            and _integer(row.get("id")) is not None
            and (_integer(row.get("fight_score")) or 0) > 0
        ),
        key=lambda row: (int(row["fight_score"]), int(row["id"])),
    )
    if not idle:
        return {"status": "all_seated"}
    team = idle[0]
    team_id = int(team["id"])
    power = int(team["fight_score"])
    team3 = next((row for row in teams if row.get("id") == 3 and row.get("complete") is True), None)
    team3_power = _integer(team3.get("fight_score")) if team3 is not None else None
    if team3_power is None or team3_power <= 0:
        return {"status": "runtime_incomplete", "reason": "team3_fight_score_missing"}
    occupied_mines = {
        int(row["mine_id"])
        for row in teams
        if row.get("state") == 2 and (_integer(row.get("mine_id")) or 0) > 0
    }
    mines = sorted(
        (row for row in snapshot.get("mines") or [] if isinstance(row, Mapping)),
        key=lambda row: (-int(row.get("config_pos_y") or 0), int(row.get("id") or 0)),
    )

    # Safety-oriented weaker teams keep map order; yield-oriented team 1
    # compares seat classes across locations before minimizing combat cost.
    # Neither strategy is permission to displace a protected ally.
    stages = (
        [((group,), friendly, (quality,))
         for (group, quality), _ in sorted(TEAM1_SEAT_YIELD_PRIORITY.items(), key=lambda item: -item[1])
         for friendly in ((True, False) if group == 4 else (True,))]
        if team_id == 1 else
        [((1, 2, 3), True, (1, 2)), ((4,), True, (1, 2)), ((4,), False, (1, 2))]
    )
    scores = defender_scores or {}
    for groups, friendly, qualities in stages:
        for mine in mines:
            mine_id = _integer(mine.get("id"))
            group = _integer(mine.get("config_group"))
            if mine_id is None or group not in groups or mine_id in occupied_mines:
                continue
            if (mine.get("cross_union_id") == own_union_id) is not friendly:
                continue
            if mine.get("seats_complete") is not True:
                return {"status": "runtime_incomplete", "reason": "mine_seats_incomplete", "mine_id": mine_id}
            candidates = []
            missing = []
            for seat in mine.get("seats") or []:
                if not isinstance(seat, Mapping):
                    return {"status": "runtime_incomplete", "reason": "seat_not_mapping", "mine_id": mine_id}
                quality = _integer(seat.get("quality"))
                seat_id = _integer(seat.get("id"))
                if quality not in {1, 2} or seat_id is None:
                    return {"status": "runtime_incomplete", "reason": "seat_identity_missing", "mine_id": mine_id}
                if quality not in qualities:
                    continue
                score, kind = _seat_score(seat, scores, mine_id)
                if kind == "needs_detail":
                    missing.append(f"{mine_id}:{quality}:{seat_id}")
                elif kind == "incomplete":
                    return {"status": "runtime_incomplete", "reason": "seat_incomplete", "mine_id": mine_id}
                elif score is not None:
                    if power <= score:
                        # Ranking favors masters, but an unbeatable master
                        # cannot hide a feasible follower at this location.
                        continue
                    if (
                        kind == "player"
                        and seat.get("guarder_cross_union_id") == own_union_id
                        and score * 5 >= team3_power * 4
                    ):
                        # Friendly-seat replacement uses team 3 as the fixed
                        # social/safety baseline, regardless of whether the
                        # team being dispatched is the stronger 1 or 2.
                        continue
                    candidates.append((score, quality, seat_id, kind, seat))
            if not candidates and missing:
                return {"status": "needs_defender_scores", "mine_id": mine_id, "seat_keys": missing, "team_id": team_id}
            if not candidates:
                continue
            # B is derived from this mine's observed scores, never a guessed
            # universal power bound. Python ints avoid overflow at 兆 scale.
            base = max(score for score, *_ in candidates) + 1
            ranked = sorted(
                (
                    score + (base if quality == 2 else 0) if kind in {"empty", "neutral"} else score,
                    score, quality, seat_id, kind, seat,
                )
                for score, quality, seat_id, kind, seat in candidates
            )
            planning_score, score, quality, seat_id, kind, seat = ranked[0]
            if missing and (kind != "empty" or quality != 1):
                return {"status": "needs_defender_scores", "mine_id": mine_id, "seat_keys": missing, "team_id": team_id}
            if power <= score:
                continue
            friend_swap = kind == "player" and seat.get("guarder_cross_union_id") == own_union_id
            action = (
                "conquer_enemy_fudi" if group == 4 and not friendly else
                "stage_friendly_swap" if friend_swap else
                "occupy_empty" if kind == "empty" else "challenge_seat"
            )
            return {
                "status": (
                    "needs_conquest_research" if action == "conquer_enemy_fudi" else
                    "needs_friend_swap_research" if friend_swap else "ready"
                ),
                "action": action,
                "team_id": team_id,
                "objective": "yield" if team_id == 1 else "safety_top_down",
                "team_fight_score": power,
                "mine_id": mine_id,
                "mine_name": mine.get("name"),
                "config_group": group,
                "friendly": friendly,
                "quality": quality,
                "seat_id": seat_id,
                "seat_kind": kind,
                "planning_score": planning_score,
                "defender_fight_score": score,
            }
    return {"status": "no_safe_target", "team_id": team_id}


def conquer_enemy_fudi_after_each_master(*_args: Any, **_kwargs: Any) -> None:
    """TODO: after every defeated master, re-read defenders and reinforcements.

    Before any attack, prove the current team's score beats the strongest
    enemy master at this mine. Repeat after each master falls; if a reinforced
    master is too strong, abandon this mine and replan another Fudi. This
    branch intentionally has no click implementation until real-game evidence
    proves the master-list route and battle/result postconditions.
    """

    pass


def plan_dongtian_friend_staging(
    snapshot: Mapping[str, Any],
    *,
    friend_role_id: int,
    team_id: int,
    defender_scores: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Choose a spare friendly Fudi for a displaced ally, without clicking.

    This is staging, not the ordinary top-down final-seat planner. Prefer a
    master anywhere in the eligible Fudi set before an attendant. Only empty
    or neutral seats qualify: displacing another friend would recurse forever.
    Both players must be absent from the ENTIRE mine (masters + attendants),
    since each player may place only one team per mine. Unknown player IDs or
    incomplete seat lists cannot prove absence. The executor must revalidate
    both mine identities and observe the game's actual swap postconditions.
    """
    if snapshot.get("available") is not True or snapshot.get("seating_summary_complete") is not True:
        return {"status": "runtime_incomplete"}
    own_role = _integer(snapshot.get("own_role_id"))
    own_union = _integer(snapshot.get("own_union_id"))
    friend_role = _integer(friend_role_id)
    if not own_role or not own_union or not friend_role or friend_role == own_role:
        return {"status": "runtime_incomplete", "reason": "player_identity_missing_or_same"}
    team = next((t for t in snapshot.get("teams", []) if t.get("id") == team_id), None)
    if not team or team.get("complete") is not True or team.get("dead") is not False:
        return {"status": "runtime_incomplete", "reason": "team_incomplete"}
    power = _integer(team.get("fight_score"))
    if not power or team.get("state") != 1 or team.get("mine_id") != 0:
        return {"status": "team_not_idle"}
    occupied = {t.get("mine_id") for t in snapshot.get("teams", []) if t.get("mine_id")}
    candidates: dict[int, list[tuple]] = {1: [], 2: []}
    missing: dict[int, list[str]] = {1: [], 2: []}
    for mine in snapshot.get("mines", []):
        mine_id = _integer(mine.get("id"))
        if (not mine_id or mine.get("config_group") != 4
                or mine.get("cross_union_id") != own_union or mine_id in occupied):
            continue
        seats = mine.get("seats") or []
        if mine.get("seats_complete") is not True or not seats:
            continue
        if any(s.get("complete") is not True or
               (s.get("guarder_type") == 2 and not _integer(s.get("guarder_role_id")))
               for s in seats):
            continue
        if any(s.get("guarder_role_id") in (own_role, friend_role) for s in seats):
            continue
        for seat in seats:
            if seat.get("guarder_type") == 2:
                continue
            quality = seat.get("quality")
            if quality not in (1, 2):
                continue
            score, kind = _seat_score(seat, defender_scores or {}, mine_id)
            if kind == "needs_detail":
                missing[quality].append(f"{mine_id}:{quality}:{seat['id']}")
            elif kind in ("empty", "neutral") and score is not None and power > score:
                candidates[quality].append((score, -int(mine.get("config_pos_y") or 0), mine_id, seat["id"], mine, kind))
    for quality in (1, 2):
        if candidates[quality]:
            score, _, mine_id, seat_id, mine, kind = min(candidates[quality], key=lambda row: row[:4])
            return {"status": "ready", "action": "occupy_staging_seat", "team_id": team_id,
                    "friend_role_id": friend_role, "mine_id": mine_id, "mine_name": mine.get("name"),
                    "quality": quality, "seat_id": seat_id, "seat_kind": kind, "defender_fight_score": score}
        if missing[quality]:
            return {"status": "needs_defender_scores", "quality": quality, "seat_keys": missing[quality]}
    return {"status": "no_safe_staging_seat", "team_id": team_id, "friend_role_id": friend_role}


def stage_friendly_swap_with_lower_seat(*_args: Any, **_kwargs: Any) -> None:
    """TODO: do not directly replace a friendly seat.

    First use plan_dongtian_friend_staging: prefer a 福地尊主, otherwise a
    福地侍从, excluding mines containing any other team of either player.
    Then inspect the actual swap UI and prove where the
    displaced friend goes. If either destination is unknown, stop and ask
    for intervention. Runtime planning returns ``needs_friend_swap_research``
    until this route has real-game evidence and postcondition checks.
    """

    pass


__all__ = ["plan_dongtian_next_team", "plan_dongtian_friend_staging", "conquer_enemy_fudi_after_each_master", "stage_friendly_swap_with_lower_seat"]
