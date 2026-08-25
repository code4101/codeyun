"""Fail-closed plans and observations for Dongtian ally battle scores.

This module is deliberately declarative.  It never drives the emulator and it
does not send ``CM_ShowOther`` itself.  A caller may use the returned dry-run
plan to research the game's natural search -> other-player-panel GUI route.
Only a response causally tied to that natural GUI request can be converted to
an atlas observation.
"""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Iterable, Mapping

from sqlmodel import Session

from backend.core.fanxiu.player_profiles import (
    ingest_fanxiu_player_battle_observation,
)


_NATURAL_GUI_ROUTE = "player_search_to_other_role_panel"
_RESPONSE_PROTOCOL = "SM_ShowOther"
_RESPONSE_SOURCE = "saved_packet_or_readonly_runtime"


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _positive_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(result) or result <= 0:
        return None
    return int(result) if result.is_integer() else result


def _timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _failure(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "observation_rejected",
        "reason": reason,
        "observation": None,
    }


def build_dongtian_friend_personal_battle_collection_plan(
    *,
    friend_seats: Iterable[Mapping[str, Any]],
    self_server_id: int,
    capabilities: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a non-executing collection plan from exact friendly occupants.

    Cross-server targets remain explicit blockers until that exact natural GUI
    search path has been truly verified.  Even a fully ready plan remains
    ``dry_run``; this module grants no GUI or seating action permission.
    """

    own_server = _positive_int(self_server_id)
    if own_server is None:
        return {
            "ok": False,
            "status": "dry_run_blocked",
            "reason": "self_server_identity_missing",
            "mode": "dry_run",
            "targets": [],
        }
    if (
        capabilities.get("natural_gui_only") is not True
        or str(capabilities.get("route") or "") != _NATURAL_GUI_ROUTE
        or capabilities.get("role_id_echo_available") is not True
        or str(capabilities.get("response_protocol") or "") != _RESPONSE_PROTOCOL
    ):
        return {
            "ok": False,
            "status": "dry_run_blocked",
            "reason": "natural_gui_capability_incomplete",
            "mode": "dry_run",
            "targets": [],
        }

    targets: list[dict[str, Any]] = []
    seen_role_ids: set[int] = set()
    invalid_count = 0
    for seat in friend_seats:
        role_id = _positive_int(seat.get("role_id") or seat.get("guarder_role_id"))
        server_id = _positive_int(seat.get("server_id"))
        name = str(seat.get("name") or "").strip()
        if (
            seat.get("strict_friend") is not True
            or role_id is None
            or server_id is None
            or not name
        ):
            invalid_count += 1
            continue
        if role_id in seen_role_ids:
            continue
        seen_role_ids.add(role_id)
        cross_server = server_id != own_server
        blocker = ""
        if cross_server and capabilities.get("cross_server_search_verified") is not True:
            blocker = "cross_server_search_unverified"
        elif not cross_server and capabilities.get("same_server_search_verified") is not True:
            blocker = "same_server_search_unverified"
        targets.append(
            {
                "strict_friend": True,
                "role_id": role_id,
                "name": name,
                "server_id": server_id,
                "mine_id": _positive_int(seat.get("mine_id")),
                "quality": _positive_int(seat.get("quality")),
                "seat_id": _positive_int(seat.get("seat_id") or seat.get("id")),
                "cross_server": cross_server,
                "status": "dry_run_blocked" if blocker else "ready_for_gui_research",
                "blocker": blocker,
                "steps": [
                    "open_verified_player_search_ui",
                    "search_exact_name_without_side_effect",
                    "verify_search_result_role_id",
                    "open_other_player_panel_naturally",
                    "capture_fresh_sm_show_other",
                    "verify_request_and_response_role_id_echo",
                    "ingest_personal_battle_observation",
                ],
            }
        )

    blockers = sorted({item["blocker"] for item in targets if item["blocker"]})
    if invalid_count:
        blockers.append("friend_seat_identity_incomplete")
    if not targets:
        blockers.append("no_exact_friend_target")
    return {
        "ok": bool(targets) and not blockers,
        "status": "dry_run_ready" if targets and not blockers else "dry_run_blocked",
        "reason": blockers[0] if blockers else "",
        "mode": "dry_run",
        "route": _NATURAL_GUI_ROUTE,
        "targets": targets,
        "blockers": blockers,
        "forbidden_actions": [
            "send_cm_show_other_directly",
            "write_game_memory",
            "kick_or_swap_friend",
            "occupy_dongtian_seat",
        ],
    }


def build_fresh_dongtian_friend_personal_battle_observation(
    *,
    target: Mapping[str, Any],
    gui_request: Mapping[str, Any],
    response_snapshot: Mapping[str, Any],
    self_server_id: int,
    cross_server_search_verified: bool = False,
    max_response_seconds: float = 30.0,
) -> dict[str, Any]:
    """Validate one natural GUI response and build a personal-score atlas row."""

    target_role_id = _positive_int(target.get("role_id") or target.get("guarder_role_id"))
    target_server_id = _positive_int(target.get("server_id"))
    own_server_id = _positive_int(self_server_id)
    target_name = str(target.get("name") or "").strip()
    if (
        target.get("strict_friend") is not True
        or target_role_id is None
        or target_server_id is None
        or own_server_id is None
        or not target_name
    ):
        return _failure("target_identity_incomplete_or_not_strict_friend")
    cross_server = target_server_id != own_server_id
    if target.get("cross_server") is not None and bool(target.get("cross_server")) != cross_server:
        return _failure("target_server_scope_mismatch")
    if cross_server and not cross_server_search_verified:
        return _failure("cross_server_search_unverified")

    if (
        gui_request.get("natural_gui") is not True
        or str(gui_request.get("route") or "") != _NATURAL_GUI_ROUTE
        or gui_request.get("search_result_identity_verified") is not True
    ):
        return _failure("natural_gui_request_unverified")
    request_role_id = _positive_int(gui_request.get("requested_role_id"))
    search_result_role_id = _positive_int(gui_request.get("search_result_role_id"))
    if request_role_id != target_role_id or search_result_role_id != target_role_id:
        return _failure("gui_target_role_id_mismatch")
    if str(gui_request.get("searched_name") or "").strip() != target_name:
        return _failure("gui_target_name_mismatch")

    if (
        response_snapshot.get("available") is not True
        or response_snapshot.get("complete") is not True
        or str(response_snapshot.get("protocol") or "") != _RESPONSE_PROTOCOL
        or str(response_snapshot.get("source") or "") != _RESPONSE_SOURCE
    ):
        return _failure("response_envelope_incomplete")
    event_id = str(response_snapshot.get("event_id") or "").strip()
    response_role_id = _positive_int(response_snapshot.get("role_id"))
    request_echo_role_id = _positive_int(response_snapshot.get("request_role_id_echo"))
    if not event_id:
        return _failure("response_event_identity_missing")
    if response_role_id != target_role_id or request_echo_role_id != target_role_id:
        return _failure("response_role_id_echo_mismatch")
    if str(response_snapshot.get("name") or "").strip() != target_name:
        return _failure("response_name_mismatch")

    requested_at = _timestamp(gui_request.get("requested_at"))
    observed_at = _timestamp(response_snapshot.get("observed_at"))
    if requested_at is None or observed_at is None:
        return _failure("observation_time_missing_or_naive")
    elapsed = (observed_at - requested_at).total_seconds()
    if elapsed < 0 or elapsed > max(0.0, float(max_response_seconds)):
        return _failure("response_not_fresh_for_request")

    battle_score = _positive_number(response_snapshot.get("battle_score"))
    if battle_score is None:
        return _failure("personal_battle_score_missing")
    observed_text = observed_at.isoformat()
    return {
        "ok": True,
        "status": "observation_ready",
        "reason": "",
        "observation": {
            "observation_id": f"dongtian-personal:{event_id}:{target_role_id}",
            "source_kind": "dongtian_friend_other_role_panel",
            "protocol": _RESPONSE_PROTOCOL,
            "role_id": str(target_role_id),
            "role_id_text": str(target_role_id),
            "name": target_name,
            "server_id": target_server_id,
            "battle_score": battle_score,
            "battle_score_text": str(response_snapshot.get("battle_score_text") or ""),
            # This is the independent observation time for personal battle
            # score.  It never reuses xianlv_team_observed_at.
            "captured_at": observed_text,
            "observed_at": observed_text,
            "evidence": {
                "route": _NATURAL_GUI_ROUTE,
                "event_id": event_id,
                "requested_at": requested_at.isoformat(),
                "response_elapsed_seconds": elapsed,
                "requested_role_id": request_role_id,
                "search_result_role_id": search_result_role_id,
                "response_role_id": response_role_id,
                "request_role_id_echo": request_echo_role_id,
                "cross_server": cross_server,
                "self_server_id": own_server_id,
                "mine_id": _positive_int(target.get("mine_id")),
                "quality": _positive_int(target.get("quality")),
                "seat_id": _positive_int(target.get("seat_id") or target.get("id")),
            },
        },
    }


def ingest_fresh_dongtian_friend_personal_battle_observation(
    session: Session,
    **kwargs: Any,
) -> dict[str, Any]:
    """Persist only a fully validated personal battle-score observation."""

    built = build_fresh_dongtian_friend_personal_battle_observation(**kwargs)
    if not built.get("ok"):
        return {**built, "ingest": None}
    observation = built.get("observation")
    assert isinstance(observation, dict)
    return {
        **built,
        "status": "observation_ingested",
        "ingest": ingest_fanxiu_player_battle_observation(session, observation),
    }


__all__ = [
    "build_dongtian_friend_personal_battle_collection_plan",
    "build_fresh_dongtian_friend_personal_battle_observation",
    "ingest_fresh_dongtian_friend_personal_battle_observation",
]
