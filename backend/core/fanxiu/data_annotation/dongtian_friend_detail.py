from __future__ import annotations

"""Read-only route for inspecting an occupied friendly Dongtian attendant.

This module is deliberately separate from the empty-seat and enemy-replacement
transaction.  It only turns an exact Runtime seat identity into the two
occupied-friendly hitboxes that have real ``#341 -> #607`` evidence.  Reaching
``#607`` is a hard stop: the page may expose ``互换采气``, but this module never
returns that Shape as an action and never authorizes a seat mutation.
"""

from typing import Any, Mapping, Sequence

from backend.core.fanxiu.data_annotation.dongtian_seat_geometry import (
    DEFAULT_VIEWPORT,
    resolve_dongtian_attendant_seat,
)


FRIEND_DETAIL_SCENE_ID = 607
FRIEND_DETAIL_SOURCE_SCENE_ID = 341
FRIEND_DETAIL_ROUTE_FAMILY = "friendly_occupied_detail"
VERIFIED_FRIEND_ATTENDANT_SEAT_IDS = frozenset({5, 12})


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _blocked(reason: str, **evidence: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "read_only_friend_detail_blocked",
        "reason": reason,
        "route_family": FRIEND_DETAIL_ROUTE_FAMILY,
        "read_only": True,
        "click_enabled": False,
        "step": None,
        "stop_scene_id": FRIEND_DETAIL_SCENE_ID,
        "allowed_detail_actions": [],
        "forbidden_detail_actions": ["互换采气"],
        "evidence": evidence,
    }


def _fresh_scene(
    evidence: Mapping[str, Any] | None,
    *,
    expected_scene_id: int,
) -> bool:
    if not isinstance(evidence, Mapping) or evidence.get("fresh") is not True:
        return False
    layer = str(evidence.get("layer") or "").strip().lower()
    status = str(evidence.get("status") or "").strip().lower()
    scene_id = evidence.get("scene_id")
    return bool(
        layer in {"layer0", "layer1", "layer2"}
        and status not in {"ambiguous", "no_match", "unknown", "unavailable"}
        and not isinstance(scene_id, bool)
        and isinstance(scene_id, int)
        and scene_id == expected_scene_id
    )


def _exact_seat(mine: Mapping[str, Any], seat_id: int) -> dict[str, Any] | None:
    matches = [
        dict(seat)
        for seat in mine.get("seats") or []
        if isinstance(seat, Mapping)
        and _positive_int(seat.get("quality")) == 2
        and _positive_int(seat.get("id")) == seat_id
    ]
    return matches[0] if len(matches) == 1 else None


def _exact_mine(
    source: Mapping[str, Any],
    mine_id: int | None,
) -> dict[str, Any] | None:
    selected = source.get("selected_mine")
    if isinstance(selected, Mapping):
        selected_id = _positive_int(selected.get("id"))
        if mine_id is None or selected_id == mine_id:
            return dict(selected)
        return None
    if mine_id is None:
        return None
    matches = [
        dict(mine)
        for mine in source.get("mines") or []
        if isinstance(mine, Mapping) and _positive_int(mine.get("id")) == mine_id
    ]
    return matches[0] if len(matches) == 1 else None


def _process_identity(source: Mapping[str, Any]) -> tuple[int, int] | None:
    evidence = source.get("evidence")
    if not isinstance(evidence, Mapping):
        return None
    pid = _positive_int(evidence.get("pid"))
    start_ticks = _positive_int(evidence.get("process_start_ticks"))
    return (pid, start_ticks) if pid is not None and start_ticks is not None else None


def build_dongtian_friend_detail_plan(
    runtime_source: Mapping[str, Any],
    *,
    mine_id: int | None = None,
    seat_id: int,
    foreground_evidence: Mapping[str, Any] | None,
    viewport: Sequence[int] = DEFAULT_VIEWPORT,
    scroll_offset_verified: bool = False,
) -> dict[str, Any]:
    """Build one reversible ``#341 -> #607`` inspection plan.

    ``runtime_source`` may be a one-mine seating probe or a complete Dongtian
    snapshot plus explicit ``mine_id``.  The exact mine must be owned by the
    current union and the attendant must still be occupied by another member
    of that union.  Enemy, neutral, empty, own-role and master seats are
    rejected here; they belong to different business routes.
    """

    if (
        runtime_source.get("available") is not True
        or runtime_source.get("complete") is not True
        or str(runtime_source.get("source") or "") != "runtime_memory"
    ):
        return _blocked("runtime_probe_incomplete")
    normalized_mine_id = _positive_int(mine_id) if mine_id is not None else None
    if mine_id is not None and normalized_mine_id is None:
        return _blocked("mine_id_invalid")
    if not isinstance(seat_id, int) or isinstance(seat_id, bool):
        return _blocked("seat_id_invalid")
    if seat_id not in VERIFIED_FRIEND_ATTENDANT_SEAT_IDS:
        return _blocked(
            "friendly_occupied_hitbox_unverified",
            seat_id=seat_id,
            verified_seat_ids=sorted(VERIFIED_FRIEND_ATTENDANT_SEAT_IDS),
        )
    if scroll_offset_verified is not True:
        return _blocked("scroll_offset_unverified")
    if not _fresh_scene(
        foreground_evidence,
        expected_scene_id=FRIEND_DETAIL_SOURCE_SCENE_ID,
    ):
        return _blocked(
            "foreground_scene_not_fresh_341",
            foreground=(
                dict(foreground_evidence)
                if isinstance(foreground_evidence, Mapping)
                else None
            ),
        )

    own_union_id = _positive_int(runtime_source.get("own_union_id"))
    own_role_id = _positive_int(runtime_source.get("own_role_id"))
    mine = _exact_mine(runtime_source, normalized_mine_id)
    if own_union_id is None or own_role_id is None or not isinstance(mine, Mapping):
        return _blocked("runtime_identity_incomplete")
    mine_id = _positive_int(mine.get("id"))
    mine_union_id = _positive_int(mine.get("cross_union_id"))
    mine_group = _positive_int(mine.get("config_group"))
    if (
        mine_id is None
        or mine_group is None
        or mine.get("seats_complete") is not True
    ):
        return _blocked("mine_identity_or_seats_incomplete")
    if mine_union_id != own_union_id:
        return _blocked(
            "mine_not_friendly",
            mine_id=mine_id,
            mine_union_id=mine_union_id,
            own_union_id=own_union_id,
        )

    seat = _exact_seat(mine, seat_id)
    if not isinstance(seat, Mapping) or seat.get("complete") is not True:
        return _blocked("friend_attendant_seat_missing_or_incomplete")
    guarder_role_id = _positive_int(seat.get("guarder_role_id"))
    guarder_union_id = _positive_int(seat.get("guarder_cross_union_id"))
    guarder_type = _positive_int(seat.get("guarder_type"))
    if (
        bool(seat.get("empty"))
        or seat.get("guarder_present") is not True
        or guarder_type != 2
        or guarder_role_id is None
    ):
        return _blocked("seat_not_occupied_player_attendant")
    if guarder_union_id != own_union_id:
        return _blocked(
            "seat_occupant_not_friendly",
            guarder_union_id=guarder_union_id,
            own_union_id=own_union_id,
        )
    if guarder_role_id == own_role_id:
        return _blocked("seat_is_own_role")

    try:
        geometry = resolve_dongtian_attendant_seat(
            seat_id,
            group=mine_group,
            viewport=viewport,
        )
    except (TypeError, ValueError) as exc:
        return _blocked("friend_attendant_geometry_unavailable", detail=str(exc))

    target = {
        "mine_id": mine_id,
        "mine_union_id": mine_union_id,
        "quality": 2,
        "seat_id": seat_id,
        "seat_key": f"{mine_id}:2:{seat_id}",
        "guarder_role_id": guarder_role_id,
        "guarder_cross_union_id": guarder_union_id,
        "mode": "read_only_friend_detail",
        "ui_route": "friendly_occupied_attendant_direct",
    }
    return {
        "ok": True,
        "status": "read_only_friend_detail",
        "reason": "",
        "protocol": "dongtian.friend-detail-plan.v1",
        "route_family": FRIEND_DETAIL_ROUTE_FAMILY,
        "read_only": True,
        "click_enabled": True,
        "irreversible": False,
        "target": target,
        "step": {
            "scene_id": FRIEND_DETAIL_SOURCE_SCENE_ID,
            "locator_kind": "projected_point",
            "point": list(geometry.point),
            "expected_scene_ids": [FRIEND_DETAIL_SCENE_ID],
            "verified_for_click": True,
        },
        "stop_scene_id": FRIEND_DETAIL_SCENE_ID,
        "allowed_detail_actions": [],
        "forbidden_detail_actions": ["互换采气"],
        "runtime_collection": {
            "source_cache": "V_GuarderTeamDic",
            "protocol": "dongtian.seat-detail.final-guard-team-cache.v1",
            "detail_layer": "site_info_guard_team",
            "target": dict(target),
            "persist_field": "xianlv_team_fight_score_max",
        },
        "evidence": {
            "runtime_process_identity": list(_process_identity(runtime_source) or ()),
            "foreground": dict(foreground_evidence),
            "mine_group": mine_group,
            "visual_rank": geometry.visual_rank,
            "calibration_source": geometry.calibration_source,
        },
    }


def verify_dongtian_friend_detail_result(
    plan: Mapping[str, Any],
    *,
    landing_evidence: Mapping[str, Any] | None,
    after_probe: Mapping[str, Any],
    observation_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Verify #607 and one fresh team-score observation, then stop there."""

    common = {
        "route_family": FRIEND_DETAIL_ROUTE_FAMILY,
        "read_only": True,
        "click_enabled": False,
        "stop_scene_id": FRIEND_DETAIL_SCENE_ID,
        "allowed_detail_actions": [],
        "forbidden_detail_actions": ["互换采气"],
    }

    def rejected(reason: str, **evidence: Any) -> dict[str, Any]:
        return {
            **common,
            "ok": False,
            "status": "read_only_friend_detail",
            "reason": reason,
            "fight_score": None,
            "observation": None,
            "evidence": evidence,
        }

    if plan.get("ok") is not True or plan.get("status") != "read_only_friend_detail":
        return rejected("friend_detail_plan_not_ready")
    if not _fresh_scene(landing_evidence, expected_scene_id=FRIEND_DETAIL_SCENE_ID):
        return rejected(
            "friend_detail_scene_607_not_proven",
            landing=(dict(landing_evidence) if isinstance(landing_evidence, Mapping) else None),
        )
    target = plan.get("target")
    if not isinstance(target, Mapping):
        return rejected("friend_detail_target_missing")

    plan_evidence = plan.get("evidence")
    before_identity = tuple(
        plan_evidence.get("runtime_process_identity") or ()
        if isinstance(plan_evidence, Mapping)
        else ()
    )
    after_identity = _process_identity(after_probe)
    if len(before_identity) != 2 or after_identity != before_identity:
        return rejected(
            "runtime_process_identity_changed",
            before=list(before_identity),
            after=list(after_identity or ()),
        )
    mine = _exact_mine(after_probe, _positive_int(target.get("mine_id")))
    if (
        after_probe.get("available") is not True
        or after_probe.get("complete") is not True
        or not isinstance(mine, Mapping)
        or _positive_int(mine.get("id")) != _positive_int(target.get("mine_id"))
    ):
        return rejected("after_probe_target_mine_missing")
    seat_id = _positive_int(target.get("seat_id"))
    seat = _exact_seat(mine, int(seat_id or 0))
    if (
        not isinstance(seat, Mapping)
        or _positive_int(seat.get("guarder_role_id"))
        != _positive_int(target.get("guarder_role_id"))
        or bool(seat.get("empty"))
    ):
        return rejected("friend_detail_occupant_changed")

    if not isinstance(observation_result, Mapping) or observation_result.get("ok") is not True:
        return rejected("fresh_xianlv_team_observation_missing")
    observation = observation_result.get("observation")
    if not isinstance(observation, Mapping):
        return rejected("fresh_xianlv_team_observation_missing")
    observation_role_id = _positive_int(
        observation.get("role_id_text") or observation.get("role_id")
    )
    fight_score = _positive_int(observation.get("xianlv_team_fight_score_max"))
    observation_evidence = observation.get("evidence")
    if (
        observation_role_id != _positive_int(target.get("guarder_role_id"))
        or fight_score is None
        or not isinstance(observation_evidence, Mapping)
        or _positive_int(observation_evidence.get("mine_id"))
        != _positive_int(target.get("mine_id"))
        or _positive_int(observation_evidence.get("quality")) != 2
        or _positive_int(observation_evidence.get("seat_id")) != seat_id
    ):
        return rejected("xianlv_team_observation_identity_mismatch")

    return {
        **common,
        "ok": True,
        "status": "read_only_friend_detail",
        "reason": "",
        "fight_score": fight_score,
        "observation": dict(observation),
        "evidence": {
            "landing": dict(landing_evidence),
            "runtime_process_identity": list(after_identity),
            "occupant_role_id": observation_role_id,
        },
    }


__all__ = [
    "FRIEND_DETAIL_ROUTE_FAMILY",
    "FRIEND_DETAIL_SCENE_ID",
    "FRIEND_DETAIL_SOURCE_SCENE_ID",
    "VERIFIED_FRIEND_ATTENDANT_SEAT_IDS",
    "build_dongtian_friend_detail_plan",
    "verify_dongtian_friend_detail_result",
]
