from __future__ import annotations

from typing import Any, Iterable, Mapping

from backend.core.fanxiu.data_annotation.dongtian_seat_geometry import (
    ATTENDANT_VISUAL_ORDER,
)


FRESH_DONGTIAN_DETAIL_STATES = frozenset(
    {"fresh_packet", "fresh_runtime_generation", "fresh_after_absence"}
)
DONGTIAN_SEATING_STRATEGY_NAME = "friendly_top_down_only"
DONGTIAN_SEATING_ALLOW_NONFRIENDLY = False


def _with_seating_strategy(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **result,
        "strategy_name": DONGTIAN_SEATING_STRATEGY_NAME,
        "allow_nonfriendly": DONGTIAN_SEATING_ALLOW_NONFRIENDLY,
    }


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def dongtian_seat_key(mine_id: int, quality: int, seat_id: int) -> str:
    """Return the stable Runtime identity for one Dongtian seat."""

    return f"{int(mine_id)}:{int(quality)}:{int(seat_id)}"


def _native_seat_route(*, quality: int, friendly_place: bool) -> str:
    """Describe the normal GUI route that can address this Runtime seat.

    A follower is opened directly.  The master entry opens the shared list:
    on a friendly mine its bottom button selects the first empty row, while on
    any other mine it selects only the first displayed (primary) row.
    """

    if int(quality) == 2:
        return "follower_seat_direct"
    return "master_list_first_empty" if friendly_place else "master_list_primary"


def classify_dongtian_mine_seats(
    mine: Mapping[str, Any],
    *,
    own_union_id: int,
) -> dict[str, Any]:
    """Classify one mine's complete 3-master/9-follower shallow Runtime facts.

    This is deliberately a shallow, read-only projection.  It can prove an
    empty seat or a same-union guarder immediately.  An enemy player or a
    neutral/NPC guarder is only an inspection candidate; combat still requires
    a fresh detail and the strict ``idle_team_power > defender_power`` gate in
    :func:`scan_dongtian_mine_next_action`.

    Native order is retained from ``display_order`` inside each quality group.
    Followers also expose the independently calibrated #341 visual rank, so a
    Runtime identity is never replaced by a guessed screen index.
    """

    mine_id = _int_or_none(mine.get("id"))
    mine_union_id = _int_or_none(mine.get("cross_union_id"))
    normalized_own_union_id = _int_or_none(own_union_id)
    source_seats = mine.get("seats") or []
    seats = [seat for seat in source_seats if isinstance(seat, Mapping)]
    source_is_complete = bool(
        mine.get("seats_complete")
        and mine_id is not None
        and mine_union_id is not None
        and normalized_own_union_id is not None
        and len(seats) == len(source_seats)
    )
    friendly_place = bool(
        mine_union_id is not None
        and normalized_own_union_id is not None
        and mine_union_id == normalized_own_union_id
    )

    rows: list[dict[str, Any]] = []
    identities: list[tuple[int, int]] = []
    for input_order, seat in enumerate(seats):
        quality = _int_or_none(seat.get("quality"))
        seat_id = _int_or_none(seat.get("id"))
        display_order = _int_or_none(seat.get("display_order"))
        if display_order is None:
            # A defensive fallback for old snapshots and unit fixtures.  Live
            # Runtime always supplies the native per-quality display order.
            display_order = sum(
                1
                for previous in rows
                if previous.get("quality") == quality
            )
        identity_complete = bool(
            seat.get("complete")
            and quality in {1, 2}
            and seat_id is not None
            and display_order >= 0
        )
        guarder_present = seat.get("guarder_present")
        guarder_type = _int_or_none(seat.get("guarder_type"))
        guarder_union_id = _int_or_none(seat.get("guarder_cross_union_id"))
        explicit_empty = seat.get("empty") is True

        if not identity_complete:
            seat_class = "neutral_unknown"
            reason = "seat_identity_or_completeness_missing"
            shallow_action = "fail_closed"
        elif explicit_empty and guarder_present is False and guarder_type in {None, 0}:
            seat_class = "empty"
            reason = "guarder_absent"
            shallow_action = "occupy_empty"
        elif guarder_type == 2 and guarder_union_id == normalized_own_union_id:
            seat_class = "friendly"
            reason = "guarder_same_union"
            shallow_action = "skip_friendly"
        elif guarder_type == 2 and guarder_union_id is not None:
            seat_class = "enemy"
            reason = "guarder_other_union"
            shallow_action = "inspect_defender"
        elif guarder_type == 1:
            seat_class = "neutral_unknown"
            reason = "neutral_or_npc_guarder"
            shallow_action = "inspect_defender"
        else:
            seat_class = "neutral_unknown"
            reason = "guarder_relation_incomplete"
            shallow_action = "fail_closed"

        visual_order: int | None = None
        if quality == 1:
            visual_order = display_order
        elif quality == 2 and seat_id in ATTENDANT_VISUAL_ORDER:
            visual_order = ATTENDANT_VISUAL_ORDER.index(int(seat_id))

        if quality in {1, 2} and seat_id is not None:
            identities.append((int(quality), int(seat_id)))
        rows.append(
            {
                "seat_key": (
                    dongtian_seat_key(int(mine_id), int(quality), int(seat_id))
                    if mine_id is not None and quality in {1, 2} and seat_id is not None
                    else None
                ),
                "quality": quality,
                "seat_kind": "master" if quality == 1 else "follower" if quality == 2 else "unknown",
                "seat_id": seat_id,
                "input_order": input_order,
                "native_display_order": display_order,
                "visual_order": visual_order,
                "ui_route": (
                    _native_seat_route(quality=int(quality), friendly_place=friendly_place)
                    if quality in {1, 2}
                    else None
                ),
                "classification": seat_class,
                "classification_reason": reason,
                "shallow_action": shallow_action,
                "guarder_role_id": _int_or_none(seat.get("guarder_role_id")),
                "guarder_type": guarder_type,
                "guarder_cross_union_id": guarder_union_id,
                "complete": identity_complete,
            }
        )

    master_count = sum(row.get("quality") == 1 for row in rows)
    follower_count = sum(row.get("quality") == 2 for row in rows)
    class_counts = {
        name: sum(row.get("classification") == name for row in rows)
        for name in ("empty", "friendly", "enemy", "neutral_unknown")
    }
    complete = bool(
        source_is_complete
        and master_count == 3
        and follower_count == 9
        and len(identities) == 12
        and len(set(identities)) == 12
        and all(bool(row.get("complete")) for row in rows)
        and all(row.get("visual_order") is not None for row in rows)
    )
    rows.sort(
        key=lambda row: (
            int(row.get("quality") or 99),
            int(row.get("native_display_order") or 0),
            int(row.get("input_order") or 0),
        )
    )
    return {
        "ok": complete,
        "complete": complete,
        "mine_id": mine_id,
        "mine_union_id": mine_union_id,
        "own_union_id": normalized_own_union_id,
        "friendly_place": friendly_place,
        "observed_seat_count": len(rows),
        "master_count": master_count,
        "follower_count": follower_count,
        "class_counts": class_counts,
        "all_occupied_friendly": bool(
            complete
            and class_counts["friendly"] == 12
        ),
        "has_shallow_candidate": bool(
            complete
            and any(
                row.get("shallow_action") in {"occupy_empty", "inspect_defender"}
                for row in rows
            )
        ),
        "native_order_seat_keys": [row["seat_key"] for row in rows],
        "follower_visual_order_seat_ids": [
            row["seat_id"]
            for row in sorted(
                (row for row in rows if row.get("quality") == 2),
                key=lambda row: int(row.get("visual_order") or 0),
            )
        ],
        "seats": rows,
    }


def scan_dongtian_friendly_locations_shallow(
    mines: Iterable[Mapping[str, Any]],
    *,
    own_union_id: int,
    occupied_mine_ids: Iterable[int] = (),
) -> dict[str, Any]:
    """Stream Runtime map order until the first interesting friendly mine.

    A friendly mine whose complete twelve seats are all occupied by the same
    union is summarized and immediately released so the caller can scroll to
    the next location.  The first friendly mine containing an empty, enemy,
    or neutral/unknown seat stops the stream.  No defender detail is accepted
    or requested here; that remains a later one-mine transaction.

    ``mines`` intentionally accepts an iterable rather than a sequence.  The
    function never materializes it, which makes the early-stop boundary both
    explicit and testable.
    """

    normalized_own_union_id = _int_or_none(own_union_id)
    if normalized_own_union_id is None:
        return {
            "ok": False,
            "status": "incomplete",
            "reason": "own_union_missing",
            "location_summaries": [],
            "stop_target": None,
        }
    occupied = {
        mine_id
        for value in occupied_mine_ids
        if (mine_id := _int_or_none(value)) is not None and mine_id > 0
    }
    summaries: list[dict[str, Any]] = []
    scanned_friendly_count = 0

    for map_order, mine in enumerate(mines):
        if not isinstance(mine, Mapping):
            summaries.append(
                {
                    "map_order": map_order,
                    "mine_id": None,
                    "outcome": "incomplete",
                    "reason": "mine_not_mapping",
                }
            )
            return {
                "ok": False,
                "status": "incomplete",
                "reason": "mine_not_mapping",
                "location_summaries": summaries,
                "stop_target": None,
                "scanned_friendly_mine_count": scanned_friendly_count,
            }
        mine_id = _int_or_none(mine.get("id"))
        mine_union_id = _int_or_none(mine.get("cross_union_id"))
        header = {
            "map_order": map_order,
            "mine_id": mine_id,
            "mine_union_id": mine_union_id,
            "mine_name": str(mine.get("name") or mine.get("cross_union_name") or "").strip(),
        }
        if mine_id is None or mine_union_id is None:
            summaries.append(
                {**header, "outcome": "incomplete", "reason": "mine_identity_missing"}
            )
            return {
                "ok": False,
                "status": "incomplete",
                "reason": "mine_identity_missing",
                "location_summaries": summaries,
                "stop_target": None,
                "scanned_friendly_mine_count": scanned_friendly_count,
            }
        if mine_id in occupied:
            summaries.append(
                {**header, "outcome": "skip_own_team_present", "friendly_place": mine_union_id == normalized_own_union_id}
            )
            continue
        if mine_union_id != normalized_own_union_id:
            # Only header identity is consumed.  Seat facts in a non-friendly
            # mine belong to the separate stamina-clearing workflow.
            summaries.append(
                {**header, "outcome": "skip_nonfriendly", "friendly_place": False}
            )
            continue

        scanned_friendly_count += 1
        classification = classify_dongtian_mine_seats(
            mine,
            own_union_id=normalized_own_union_id,
        )
        summary = {
            **header,
            "friendly_place": True,
            "classification_complete": bool(classification.get("complete")),
            "class_counts": dict(classification.get("class_counts") or {}),
            "all_occupied_friendly": bool(classification.get("all_occupied_friendly")),
        }
        if not classification.get("complete"):
            summaries.append(
                {**summary, "outcome": "incomplete", "reason": "twelve_seat_contract_incomplete"}
            )
            return {
                "ok": False,
                "status": "incomplete",
                "reason": "twelve_seat_contract_incomplete",
                "location_summaries": summaries,
                "stop_target": {"mine_id": mine_id, "map_order": map_order, "seat": None},
                "scanned_friendly_mine_count": scanned_friendly_count,
            }
        if classification.get("all_occupied_friendly"):
            summaries.append({**summary, "outcome": "continue_all_friendly_full"})
            continue

        classified_seats = list(classification.get("seats") or [])
        # Empty wins inside a mine, matching the main strategy's no-detail
        # fast path.  Otherwise retain native order for the first defender or
        # neutral/unknown seat that needs one-mine investigation.
        stop_seat = next(
            (row for row in classified_seats if row.get("classification") == "empty"),
            None,
        )
        if stop_seat is None:
            stop_seat = next(
                (
                    row
                    for row in classified_seats
                    if row.get("classification") in {"enemy", "neutral_unknown"}
                ),
                None,
            )
        if stop_seat is None:
            summaries.append(
                {**summary, "outcome": "incomplete", "reason": "classification_has_no_stop_seat"}
            )
            return {
                "ok": False,
                "status": "incomplete",
                "reason": "classification_has_no_stop_seat",
                "location_summaries": summaries,
                "stop_target": {"mine_id": mine_id, "map_order": map_order, "seat": None},
                "scanned_friendly_mine_count": scanned_friendly_count,
            }
        summaries.append(
            {
                **summary,
                "outcome": "stop_for_candidate",
                "stop_classification": stop_seat.get("classification"),
                "stop_seat_key": stop_seat.get("seat_key"),
            }
        )
        return {
            "ok": True,
            "status": "stop_for_candidate",
            "reason": f"friendly_mine_has_{stop_seat.get('classification')}",
            "location_summaries": summaries,
            "stop_target": {
                "mine_id": mine_id,
                "map_order": map_order,
                "seat": dict(stop_seat),
                "class_counts": dict(classification.get("class_counts") or {}),
            },
            "scanned_friendly_mine_count": scanned_friendly_count,
        }

    return {
        "ok": True,
        "status": "friendly_locations_exhausted",
        "reason": "no_shallow_candidate",
        "location_summaries": summaries,
        "stop_target": None,
        "scanned_friendly_mine_count": scanned_friendly_count,
    }


def classify_dongtian_detail_freshness(
    *,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    expected_mine_id: int,
    expected_quality: int,
    expected_seat_id: int,
    request_watermark: int | None = None,
    before_absence_proven: bool = False,
) -> dict[str, Any]:
    """Classify one GUI-triggered defender detail without trusting old cache.

    Runtime detail dictionaries carry no native timestamp.  A response packet
    observed after the click is authoritative; a previously absent cache that
    appears with the exact target identity is acceptable secondary evidence.
    An unchanged compatible cache remains unproven and cannot authorize a
    battle.
    """

    if not isinstance(after, Mapping) or not after.get("complete"):
        return {"ok": False, "freshness": "missing_or_incomplete", "fresh": False}
    identity = (
        _int_or_none(after.get("mine_id")),
        _int_or_none(after.get("quality")),
        _int_or_none(after.get("seat_id")),
    )
    expected = (int(expected_mine_id), int(expected_quality), int(expected_seat_id))
    if identity != expected:
        return {
            "ok": False,
            "freshness": "identity_mismatch",
            "fresh": False,
            "observed_identity": identity,
            "expected_identity": expected,
        }

    packet_id = _int_or_none(after.get("response_packet_id"))
    response_echo = (
        _int_or_none(after.get("response_mine_id")),
        _int_or_none(after.get("response_quality")),
        _int_or_none(after.get("response_seat_id")),
    )
    response_echo_present = any(
        key in after
        for key in (
            "response_mine_id",
            "response_quality",
            "response_seat_id",
        )
    )
    if response_echo_present and response_echo != expected:
        return {
            "ok": False,
            "freshness": "response_identity_mismatch",
            "fresh": False,
            "observed_response_identity": response_echo,
            "expected_identity": expected,
        }
    if (
        request_watermark is not None
        and packet_id is not None
        and packet_id > int(request_watermark)
        and response_echo == expected
    ):
        return {"ok": True, "freshness": "fresh_packet", "fresh": True, "detail": dict(after)}
    before_generation = (
        _int_or_none(before.get("cache_generation_address"))
        if isinstance(before, Mapping)
        else None
    )
    after_generation = _int_or_none(after.get("cache_generation_address"))
    if (
        before_generation is not None
        and after_generation is not None
        and after_generation != before_generation
    ):
        return {
            "ok": True,
            "freshness": "fresh_runtime_generation",
            "fresh": True,
            "detail": dict(after),
        }
    if before is None and before_absence_proven:
        return {"ok": True, "freshness": "fresh_after_absence", "fresh": True, "detail": dict(after)}
    return {"ok": True, "freshness": "compatible_unproven", "fresh": False, "detail": dict(after)}


def scan_dongtian_mine_next_action(
    mine: Mapping[str, Any],
    *,
    own_union_id: int,
    own_role_id: int | None = None,
    idle_teams: list[Mapping[str, Any]],
    seat_details: Mapping[str, Mapping[str, Any]] | None = None,
    mine_order: int = 0,
) -> dict[str, Any]:
    """Stream one location top-to-bottom and stop at its first live candidate.

    Empty seats are authoritative shallow facts and are preferred because they
    need no detail request and no battle.  If this location has no empty seat,
    occupied non-protected seats are inspected one at a time in display order.
    A compatible old cache never permits combat.  Only after a fresh detail
    proves that no idle team is stronger do we continue to the next defender.
    """

    mine_id = _int_or_none(mine.get("id"))
    mine_union_id = _int_or_none(mine.get("cross_union_id"))
    if mine_id is None or mine_union_id is None or not mine.get("seats_complete"):
        return {"ok": False, "status": "incomplete", "reason": "mine_or_seats_incomplete", "action": None}
    teams = [dict(team) for team in idle_teams]
    if not teams:
        return {"ok": True, "status": "noop_no_idle", "action": None, "target": None}
    # #343 AutoChangeHot selects the lowest-numbered complete idle team when
    # an empty seat is opened.  Keep that native ordering separate from the
    # battle policy: replacement still spends the weakest team that is
    # strictly stronger than the freshly observed defender.
    native_default_teams = sorted(teams, key=lambda item: int(item.get("id") or 0))
    battle_teams = sorted(
        teams,
        key=lambda item: (
            int(item.get("fight_score") or 0),
            int(item.get("id") or 0),
        ),
    )
    details = seat_details or {}
    friendly_place = mine_union_id == int(own_union_id)
    seats = [seat for seat in mine.get("seats") or [] if isinstance(seat, Mapping)]
    if len(seats) != len(mine.get("seats") or []):
        return {"ok": False, "status": "incomplete", "reason": "seat_incomplete", "action": None}
    friendly_self_master_present = bool(
        friendly_place
        and own_role_id is not None
        and any(
            _int_or_none(seat.get("quality")) == 1
            and bool(seat.get("guarder_present"))
            and _int_or_none(seat.get("guarder_role_id")) == int(own_role_id)
            for seat in seats
        )
    )

    occupied_candidates: list[tuple[int, Mapping[str, Any], dict[str, Any]]] = []
    for seat_order, seat in enumerate(seats):
        if not seat.get("complete"):
            return {"ok": False, "status": "incomplete", "reason": "seat_incomplete", "action": None}
        quality = _int_or_none(seat.get("quality"))
        seat_id = _int_or_none(seat.get("id"))
        if quality not in {1, 2} or seat_id is None:
            return {"ok": False, "status": "incomplete", "reason": "seat_identity_missing", "action": None}

        # On an enemy mine the shared 尊主「占领」button always targets the
        # first displayed master.  Other master rows are lineup views only.
        if quality == 1 and not friendly_place and not bool(seat.get("primary_master")):
            continue

        key = dongtian_seat_key(mine_id, quality, seat_id)
        candidate = {
            "mine_id": mine_id,
            "mine_union_id": mine_union_id,
            "friendly_place": friendly_place,
            "quality": quality,
            "seat_id": seat_id,
            "seat_key": key,
            "mine_order": int(mine_order),
            "seat_order": seat_order,
            "ui_route": _native_seat_route(
                quality=quality,
                friendly_place=friendly_place,
            ),
            "guarder_role_id": _int_or_none(seat.get("guarder_role_id")),
            "guarder_type": _int_or_none(seat.get("guarder_type")),
            "guarder_cross_union_id": _int_or_none(
                seat.get("guarder_cross_union_id")
            ),
        }
        guarder_type = _int_or_none(seat.get("guarder_type"))
        shallow_empty = bool(seat.get("empty")) or guarder_type in {None, 0}
        if quality == 1 and friendly_place:
            if own_role_id is None:
                # Without the current role identity we cannot distinguish
                # "occupy first empty" from the native recall-my-team path.
                continue
            if friendly_self_master_present:
                # The native shared button recalls our existing master team
                # before it ever searches for an empty master row.
                continue
            if shallow_empty and seat.get("guarder_present") is not False:
                # Native first-empty means ``not v.guarder``; a present
                # guarder object with type=0 is not equivalent.
                continue
        if shallow_empty:
            team = native_default_teams[0]
            return {
                "ok": True,
                "status": "ready",
                "action": "occupy_empty",
                "target": {
                    **candidate,
                    "team_id": int(team["id"]),
                    "team_fight_score": int(team["fight_score"]),
                    "mode": "occupy_empty",
                    "team_selection_basis": "native_default_lowest_team_id",
                },
            }

        if quality == 1 and friendly_place:
            # The friendly-mine master button searches for the first empty
            # master row.  When all rows are occupied and none belongs to the
            # current role, the native button is disabled; an occupied master
            # therefore cannot be challenged through this route.
            continue

        guarder_union_id = _int_or_none(seat.get("guarder_cross_union_id"))
        if guarder_type == 2 and guarder_union_id is None:
            return {"ok": False, "status": "incomplete", "reason": "guarder_union_missing", "action": None}
        if guarder_type == 2 and guarder_union_id == int(own_union_id):
            continue
        if quality == 2 and guarder_type == 2 and guarder_union_id == mine_union_id:
            # The native follower UI rejects this shallow relationship as
            # 联盟保护; do not request details and do not try to occupy it.
            continue
        if guarder_type not in {1, 2}:
            return {"ok": False, "status": "incomplete", "reason": "guarder_type_unsupported", "action": None}

        occupied_candidates.append((seat_order, seat, candidate))

    for _seat_order, seat, candidate in occupied_candidates:
        key = str(candidate["seat_key"])
        detail = details.get(key)
        if not isinstance(detail, Mapping) or not detail.get("complete"):
            return {
                "ok": True,
                "status": "need_detail",
                "action": "inspect_defender",
                "target": {
                    **candidate,
                    "mode": "inspect_defender",
                    "eligible_team_ids": [int(team["id"]) for team in battle_teams],
                },
            }
        if str(detail.get("freshness") or "") not in FRESH_DONGTIAN_DETAIL_STATES:
            return {
                "ok": True,
                "status": "need_detail",
                "action": "refresh_defender",
                "target": {
                    **candidate,
                    "mode": "refresh_defender",
                    "eligible_team_ids": [int(team["id"]) for team in battle_teams],
                },
            }
        if _int_or_none(detail.get("mine_id")) != mine_id:
            return {"ok": False, "status": "incomplete", "reason": "seat_detail_mine_mismatch", "action": None}
        if (
            _int_or_none(detail.get("quality"))
            != _int_or_none(candidate.get("quality"))
            or _int_or_none(detail.get("seat_id"))
            != _int_or_none(candidate.get("seat_id"))
        ):
            return {"ok": False, "status": "incomplete", "reason": "seat_detail_identity_mismatch", "action": None}
        defender_power = _int_or_none(detail.get("fight_score"))
        if defender_power is None:
            return {"ok": False, "status": "incomplete", "reason": "defender_power_missing", "action": None}
        eligible_teams = [
            team
            for team in battle_teams
            if int(team["fight_score"]) > defender_power
        ]
        if eligible_teams:
            team = eligible_teams[0]
            return {
                "ok": True,
                "status": "ready",
                "action": "replace_weaker_enemy",
                "target": {
                    **candidate,
                    "team_id": int(team["id"]),
                    "team_fight_score": int(team["fight_score"]),
                    "mode": "replace_weaker_enemy",
                    "defender_fight_score": defender_power,
                    "team_selection_basis": "weakest_strictly_stronger",
                },
            }
        # This defender is freshly proven unsafe for every idle team.  Only
        # now may the state machine advance to the next displayed defender.

    return {"ok": True, "status": "mine_exhausted", "action": None, "target": None, "mine_id": mine_id}


def choose_dongtian_seating_action(
    snapshot: Mapping[str, Any],
    *,
    seat_details: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Choose one safe idle-team seating action from authoritative Runtime facts.

    Only friendly-owned locations are eligible.  They keep Runtime native
    display order, and the first unresolved or actionable location stops the
    scan so the caller never preloads a global defender-detail matrix.
    """

    if not snapshot.get("available"):
        return _with_seating_strategy({"ok": False, "status": "incomplete", "reason": "runtime_unavailable", "action": None})
    if not snapshot.get("seating_summary_complete"):
        return _with_seating_strategy({"ok": False, "status": "incomplete", "reason": "seating_summary_incomplete", "action": None})
    own_union_id = _int_or_none(snapshot.get("own_union_id"))
    if own_union_id is None:
        return _with_seating_strategy({"ok": False, "status": "incomplete", "reason": "own_union_missing", "action": None})
    own_role_id = _int_or_none(snapshot.get("own_role_id"))
    if own_role_id is not None and own_role_id <= 0:
        own_role_id = None

    teams = [team for team in snapshot.get("teams") or [] if isinstance(team, Mapping)]
    idle_teams = [
        dict(team)
        for team in teams
        if bool(team.get("complete"))
        and bool(team.get("idle"))
        and _int_or_none(team.get("state")) == 1
        and _int_or_none(team.get("mine_id")) == 0
        and team.get("dead") is False
        and len(team.get("xianlv_ids") or []) == 5
        and _int_or_none(team.get("fight_score")) is not None
    ]
    if not idle_teams:
        return _with_seating_strategy({
            "ok": True,
            "status": "noop_no_idle",
            "reason": "all_teams_occupied_or_unavailable",
            "action": None,
            "idle_team_count": 0,
        })

    occupied_mine_ids = {
        int(mine_id)
        for team in teams
        if bool(team.get("complete"))
        and _int_or_none(team.get("state")) == 2
        and (mine_id := _int_or_none(team.get("mine_id"))) is not None
        and int(mine_id) > 0
    }

    mines = [
        mine
        for mine in snapshot.get("mines") or []
        if isinstance(mine, Mapping)
        and _int_or_none(mine.get("id")) not in occupied_mine_ids
        and _int_or_none(mine.get("cross_union_id")) == own_union_id
    ]
    for mine_order, mine in enumerate(mines):
        decision = scan_dongtian_mine_next_action(
            mine,
            own_union_id=own_union_id,
            own_role_id=own_role_id,
            idle_teams=idle_teams,
            seat_details=seat_details,
            mine_order=mine_order,
        )
        if decision.get("status") == "mine_exhausted":
            continue
        return _with_seating_strategy({**decision, "idle_team_count": len(idle_teams)})
    return _with_seating_strategy({"ok": True, "status": "no_safe_target", "reason": "friendly_locations_exhausted", "action": None, "target": None, "idle_team_count": len(idle_teams)})


def choose_dongtian_probe_action(
    probe: Mapping[str, Any],
    *,
    seat_details: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Choose only the next action for one early-stop mine probe.

    The caller keeps an ``excluded_mine_ids`` set.  A location is added only
    after this function returns ``advance_mine``; then the Runtime probe is
    rerun and naturally selects the next top-to-bottom location.  This avoids
    collecting a global detail matrix before any useful action can happen.
    """

    if not probe.get("available") or not probe.get("complete"):
        return _with_seating_strategy({"ok": False, "status": "incomplete", "reason": "probe_incomplete", "action": None})
    if str(probe.get("status") or "") == "noop_no_idle":
        return _with_seating_strategy({"ok": True, "status": "noop_no_idle", "action": None, "target": None})
    mine = probe.get("selected_mine")
    if not isinstance(mine, Mapping):
        return _with_seating_strategy({"ok": True, "status": "no_safe_target", "reason": "friendly_locations_exhausted", "action": None, "target": None})
    own_union_id = _int_or_none(probe.get("own_union_id"))
    if _int_or_none(mine.get("cross_union_id")) != own_union_id:
        return _with_seating_strategy({
            "ok": True,
            "status": "no_safe_target",
            "reason": "nonfriendly_location_disallowed",
            "action": None,
            "target": None,
        })
    snapshot = {
        "available": True,
        "seating_summary_complete": True,
        "own_union_id": probe.get("own_union_id"),
        "own_role_id": probe.get("own_role_id"),
        "teams": list(probe.get("teams") or []),
        "mines": [dict(mine)],
    }
    decision = choose_dongtian_seating_action(snapshot, seat_details=seat_details)
    if decision.get("status") == "no_safe_target":
        return _with_seating_strategy({
            **decision,
            "status": "advance_mine",
            "action": "exclude_mine_and_continue",
            "excluded_mine_id": _int_or_none(mine.get("id")),
        })
    return _with_seating_strategy(decision)
