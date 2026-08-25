from __future__ import annotations

"""Pure one-mine research report for the Dongtian seating workflow.

The caller owns all Runtime reads.  This module never opens a session, walks
another mine, or performs a GUI action; it only composes already-decoded facts
into one dense report suitable for printing from a diagnostic Cell.
"""

from typing import Any, Mapping, Sequence

from backend.core.fanxiu.data_annotation.dongtian_seat_geometry import (
    DEFAULT_VIEWPORT,
    resolve_dongtian_attendant_seat,
)
from backend.core.fanxiu.data_annotation.dongtian_seating import (
    DONGTIAN_SEATING_ALLOW_NONFRIENDLY,
    DONGTIAN_SEATING_STRATEGY_NAME,
    classify_dongtian_mine_seats,
    choose_dongtian_probe_action,
)
from backend.core.fanxiu.data_annotation.tasks.dongtian_seating_executor import (
    CURRENT_DONGTIAN_SEATING_GUI_CAPABILITIES,
    DongtianSeatingGuiCapabilities,
    dongtian_seating_capability_blockers,
)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _teams(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in source.get("teams") or [] if isinstance(item, Mapping)]


def _idle_teams(teams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    idle = [
        item
        for item in teams
        if item.get("complete") is True
        and item.get("idle") is True
        and _positive_int(item.get("state")) == 1
        and item.get("mine_id") == 0
        and item.get("dead") is False
    ]
    return sorted(idle, key=lambda item: (_positive_int(item.get("id")) or 0))


def _occupied_mine_ids(teams: list[dict[str, Any]]) -> list[int]:
    return sorted(
        {
            mine_id
            for item in teams
            if _positive_int(item.get("state")) == 2
            and (mine_id := _positive_int(item.get("mine_id"))) is not None
        }
    )


def _detail_requirement(
    decision: Mapping[str, Any],
    *,
    native_seat_details: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Normalize the transaction's two-layer natural-detail progression."""

    effective = dict(decision)
    target = decision.get("target")
    if not isinstance(target, Mapping):
        return effective, None
    status = str(decision.get("status") or "")
    if status not in {"need_detail", "refresh_defender"}:
        return effective, None
    quality = _positive_int(target.get("quality"))
    seat_key = str(target.get("seat_key") or "")
    if quality == 1 and seat_key not in native_seat_details:
        return effective, {
            "required": True,
            "layer": "native_master_list",
            "action": "inspect_defender",
            "mine_id": _positive_int(target.get("mine_id")),
            "quality": quality,
            "seat_id": _positive_int(target.get("seat_id")),
            "seat_key": seat_key,
            "natural_trigger_scene_ids": [279, 341],
            "expected_scene_ids": [342],
        }
    effective.update(
        {
            "status": "need_final_detail",
            "action": "inspect_final_guard",
        }
    )
    return effective, {
        "required": True,
        "layer": "site_info_guard_team",
        "action": "inspect_final_guard",
        "mine_id": _positive_int(target.get("mine_id")),
        "quality": quality,
        "seat_id": _positive_int(target.get("seat_id")),
        "seat_key": seat_key,
        "natural_trigger_scene_ids": [279, 341, 342],
        "expected_scene_ids": [343],
    }


def _gui_coordinate_candidate(
    decision: Mapping[str, Any],
    *,
    mine_group: int | None,
    viewport: Sequence[int],
    origin: Sequence[float] | None,
    scale: float | Sequence[float] | None,
) -> dict[str, Any] | None:
    target = decision.get("target")
    if not isinstance(target, Mapping):
        return None
    quality = _positive_int(target.get("quality"))
    seat_id = _positive_int(target.get("seat_id"))
    route = str(target.get("ui_route") or "")
    if quality != 2:
        return {
            "available": False,
            "reason": "master_uses_asset_shape_or_native_shared_button",
            "ui_route": route or None,
            "point": None,
        }
    if mine_group is None:
        return {
            "available": False,
            "reason": "mine_group_missing",
            "ui_route": route or None,
            "seat_id": seat_id,
            "point": None,
        }
    try:
        geometry = resolve_dongtian_attendant_seat(
            int(seat_id or 0),
            group=int(mine_group),
            viewport=viewport,
            origin=origin,
            scale=scale,
        )
    except (TypeError, ValueError) as exc:
        return {
            "available": False,
            "reason": "seat_geometry_unavailable",
            "detail": str(exc),
            "ui_route": route or None,
            "seat_id": seat_id,
            "point": None,
        }
    return {
        "available": True,
        "verified_for_click": geometry.empty_hitbox_verified,
        "reason": (
            None
            if geometry.empty_hitbox_verified
            else "empty_hitbox_not_runtime_verified"
        ),
        "ui_route": route or None,
        "seat_id": geometry.seat_id,
        "group": geometry.group,
        "viewport": list(geometry.viewport),
        "point": list(geometry.point),
        "visual_rank": geometry.visual_rank,
        "calibration_source": geometry.calibration_source,
    }


def build_dongtian_seating_research_report(
    probe: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any] | None = None,
    native_seat_details: Mapping[str, Mapping[str, Any]] | None = None,
    final_guard_details: Mapping[str, Mapping[str, Any]] | None = None,
    capabilities: DongtianSeatingGuiCapabilities = (
        CURRENT_DONGTIAN_SEATING_GUI_CAPABILITIES
    ),
    mine_group: int | None = None,
    viewport: Sequence[int] = DEFAULT_VIEWPORT,
    origin: Sequence[float] | None = None,
    scale: float | Sequence[float] | None = None,
) -> dict[str, Any]:
    """Build one high-density report without reading or mutating game state.

    ``choose_dongtian_probe_action`` receives only ``probe.selected_mine``;
    even when a full snapshot is supplied, its mine list is never traversed.
    """

    native_details = dict(native_seat_details or {})
    final_details = dict(final_guard_details or {})
    summary_source = snapshot if isinstance(snapshot, Mapping) else probe
    teams = _teams(summary_source)
    if not teams and summary_source is not probe:
        teams = _teams(probe)
    idle_teams = _idle_teams(teams)
    occupied_mine_ids = _occupied_mine_ids(teams)

    raw_decision = choose_dongtian_probe_action(
        probe,
        seat_details=final_details,
    )
    decision, detail_requirement = _detail_requirement(
        raw_decision,
        native_seat_details=native_details,
    )
    target = decision.get("target")
    selected_mine = probe.get("selected_mine")
    selected_mine_id = (
        _positive_int(selected_mine.get("id"))
        if isinstance(selected_mine, Mapping)
        else None
    )
    seat_classification = (
        classify_dongtian_mine_seats(
            selected_mine,
            own_union_id=int(probe.get("own_union_id")),
        )
        if isinstance(selected_mine, Mapping)
        and _positive_int(probe.get("own_union_id")) is not None
        else None
    )
    coordinate = _gui_coordinate_candidate(
        decision,
        mine_group=mine_group,
        viewport=viewport,
        origin=origin,
        scale=scale,
    )
    blockers = dongtian_seating_capability_blockers(capabilities)
    return {
        "ok": bool(raw_decision.get("ok")),
        "status": str(decision.get("status") or "incomplete"),
        "protocol": "dongtian.seating.research-report.v1",
        "read_only": True,
        "single_mine_scan": True,
        "strategy_name": DONGTIAN_SEATING_STRATEGY_NAME,
        "allow_nonfriendly": DONGTIAN_SEATING_ALLOW_NONFRIENDLY,
        "idle_teams": idle_teams,
        "idle_team_ids": [
            team_id
            for item in idle_teams
            if (team_id := _positive_int(item.get("id"))) is not None
        ],
        "occupied_mine_ids": occupied_mine_ids,
        "selected_mine_id": selected_mine_id,
        "seat_classification": seat_classification,
        "next_action": decision,
        "target": dict(target) if isinstance(target, Mapping) else None,
        "detail_requirement": detail_requirement,
        "gui_coordinate_candidate": coordinate,
        "capability_blockers": blockers,
        "commit_enabled": False,
        "evidence": {
            "probe_status": probe.get("status"),
            "probe_protocol": probe.get("protocol"),
            "probe_captured_at_epoch": probe.get("captured_at_epoch"),
            "snapshot_supplied": isinstance(snapshot, Mapping),
            "snapshot_captured_at_epoch": (
                snapshot.get("captured_at_epoch")
                if isinstance(snapshot, Mapping)
                else None
            ),
            "selected_mine_count": 1 if selected_mine_id is not None else 0,
        },
    }


__all__ = ["build_dongtian_seating_research_report"]
