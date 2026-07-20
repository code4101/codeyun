from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.server_relations import classify_fanxiu_target_relation
from backend.core.fanxiu.data_annotation.tasks.daofa import current_player_battle_score
from backend.core.fanxiu.packet.current_facts import (
    get_latest_fanxiu_lingmai_scene_seat_facts,
    get_latest_fanxiu_lingmai_self_seat_facts,
)
from backend.core.fanxiu.packet.service_runtime import (
    request_fanxiu_packet_service_catch_up,
    start_fanxiu_packet_service,
)
from backend.db import engine


LINGMAI_SHENMAI_ROOM_ID = 10
LINGMAI_UNION_SHENMAI_ROOM_ID = 17
LINGMAI_SHENMAI_ROOM_IDS = (LINGMAI_SHENMAI_ROOM_ID, LINGMAI_UNION_SHENMAI_ROOM_ID)
LINGMAI_SAFE_BATTLE_RATIO = 0.90
LINGMAI_DEFAULT_RETRY_SECONDS = 1800
LINGMAI_PROTECTION_RETRY_GRACE_MS = 5000


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def select_lingmai_seat_action(
    seat_facts: dict[str, Any],
    *,
    self_seat_facts: dict[str, Any],
    player_profile: dict[str, Any],
    data_dir: str | Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Choose an empty Shenmai seat or the weakest safe non-friendly target."""

    if not seat_facts.get("available"):
        return {"ok": False, "status": "invalid_facts", "reason": "seat_roster_missing", "action": None}
    room_id = _int_or_none(seat_facts.get("room_id"))
    if room_id not in LINGMAI_SHENMAI_ROOM_IDS:
        return {"ok": False, "status": "invalid_facts", "reason": "not_shenmai_room", "action": None}

    if not self_seat_facts.get("available"):
        return {"ok": False, "status": "invalid_facts", "reason": "self_seat_missing", "action": None}
    roster_pcap = str((seat_facts.get("evidence") or {}).get("pcap_name") or "")
    self_seat_pcap = str((self_seat_facts.get("evidence") or {}).get("pcap_name") or "")
    if roster_pcap and self_seat_pcap and roster_pcap != self_seat_pcap:
        return {"ok": False, "status": "invalid_facts", "reason": "self_seat_roster_round_mismatch", "action": None}
    if not player_profile.get("available"):
        return {"ok": False, "status": "invalid_profile", "reason": "self_profile_missing", "action": None}
    self_role_id = _int_or_none(player_profile.get("role_id"))
    self_battle = _number_or_none(player_profile.get("battle_score"))
    if self_role_id is None or self_battle is None:
        return {"ok": False, "status": "invalid_profile", "reason": "self_profile_incomplete", "action": None}

    self_seat = self_seat_facts.get("seat") if isinstance(self_seat_facts.get("seat"), dict) else None
    self_owner = self_seat.get("owner") if isinstance(self_seat, dict) and isinstance(self_seat.get("owner"), dict) else None
    self_seat_role_id = _int_or_none(self_owner.get("role_id")) if self_owner else None
    if self_seat_facts.get("seated"):
        if self_seat_role_id != self_role_id:
            return {"ok": False, "status": "invalid_facts", "reason": "self_seat_owner_mismatch", "action": None}
        return {
            "ok": True,
            "status": "already_seated",
            "action": "already_seated",
            "target": None,
            "self_seat": self_seat,
            "room_id": room_id,
        }

    available_count = _int_or_none(seat_facts.get("available_count"))
    if available_count is None:
        return {"ok": False, "status": "invalid_facts", "reason": "available_count_missing", "action": None}
    if available_count > 0:
        return {
            "ok": True,
            "status": "occupy_empty",
            "action": "occupy_empty",
            "target": None,
            "available_count": available_count,
            "room_id": room_id,
        }

    if not seat_facts.get("complete"):
        return {"ok": False, "status": "invalid_facts", "reason": "seat_roster_incomplete", "action": None}
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    safe_battle_max = self_battle * LINGMAI_SAFE_BATTLE_RATIO
    eligible: list[dict[str, Any]] = []
    future: list[dict[str, Any]] = []
    rejected = {"self": 0, "friendly": 0, "protected": 0, "unsafe_power": 0, "invalid": 0}

    for seat in seat_facts.get("seats") or []:
        if not isinstance(seat, dict):
            rejected["invalid"] += 1
            continue
        owner = seat.get("owner") if isinstance(seat.get("owner"), dict) else None
        seat_id = _int_or_none(seat.get("seat_id"))
        if owner is None or seat_id is None:
            rejected["invalid"] += 1
            continue
        role_id = _int_or_none(owner.get("role_id"))
        name = str(owner.get("name") or "").strip()
        battle_score = _number_or_none(owner.get("battle_score"))
        if role_id is None or not name or battle_score is None:
            rejected["invalid"] += 1
            continue
        if role_id == self_role_id:
            rejected["self"] += 1
            continue
        relation = classify_fanxiu_target_relation(
            is_npc=False,
            server_id=owner.get("server_id"),
            data_dir=data_dir,
        )
        if relation.get("camp") != "non_friendly":
            rejected["friendly"] += 1
            continue
        if battle_score > safe_battle_max:
            rejected["unsafe_power"] += 1
            continue

        protect_end_time = _int_or_none(owner.get("protect_end_time")) or 0
        candidate = {
            "id": seat_id,
            "seat_id": seat_id,
            "role_id": role_id,
            "name": name,
            "server_id": _int_or_none(owner.get("server_id")),
            "alliance_id": _int_or_none(owner.get("alliance_id")),
            "battle_score": battle_score,
            "protect_end_time": protect_end_time,
            "relation": relation,
            "is_ally": False,
            "excluded": False,
        }
        if protect_end_time > current_ms:
            rejected["protected"] += 1
            future.append(candidate)
        else:
            eligible.append(candidate)

    eligible.sort(key=lambda item: (float(item["battle_score"]), int(item["seat_id"])))
    future.sort(key=lambda item: (int(item["protect_end_time"]), float(item["battle_score"]), int(item["seat_id"])))
    target = eligible[0] if eligible else None
    retry_at_ms = None
    retry_reason = None
    if target is None:
        if future:
            retry_at_ms = int(future[0]["protect_end_time"]) + LINGMAI_PROTECTION_RETRY_GRACE_MS
            retry_reason = "earliest_beatable_protection_end"
        else:
            retry_at_ms = current_ms + LINGMAI_DEFAULT_RETRY_SECONDS * 1000
            retry_reason = "no_current_or_future_beatable_target"

    return {
        "ok": True,
        "status": "kick" if target is not None else "retry",
        "action": "kick" if target is not None else "retry",
        "target": target,
        "eligible_count": len(eligible),
        "future_count": len(future),
        "future_target": future[0] if future else None,
        "retry_at_ms": retry_at_ms,
        "retry_reason": retry_reason,
        "rejected": rejected,
        "safe_battle_ratio": LINGMAI_SAFE_BATTLE_RATIO,
        "safe_battle_max": safe_battle_max,
        "available_count": available_count,
        "room_id": room_id,
    }


def refresh_and_select_lingmai_seat_action(
    *,
    data_dir: str | Path | None = None,
    since_seconds: int = 1200,
) -> dict[str, Any]:
    """Catch up live packets, normalize the Shenmai roster, then choose one action."""

    start_result = start_fanxiu_packet_service()
    catch_up = request_fanxiu_packet_service_catch_up(
        reason="daily-lingmai-select-seat-action",
        wait_seconds=120.0,
    )
    with Session(engine) as session:
        seat_facts = get_latest_fanxiu_lingmai_scene_seat_facts(
            session,
            since_seconds=max(60, int(since_seconds)),
        )
        self_seat_facts = get_latest_fanxiu_lingmai_self_seat_facts(
            session,
            since_seconds=max(60, int(since_seconds)),
        )
    profile = current_player_battle_score()
    selection = select_lingmai_seat_action(
        seat_facts,
        self_seat_facts=self_seat_facts,
        player_profile=profile,
        data_dir=data_dir,
    )
    return {
        **selection,
        "start_result": start_result,
        "catch_up": catch_up,
        "seat_facts": seat_facts,
        "self_seat_facts": self_seat_facts,
        "player_profile": profile,
    }
