from __future__ import annotations

import time
import re
from pathlib import Path
from typing import Any, Mapping

from backend.core.fanxiu.catalog.server_relations import classify_fanxiu_target_relation
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.runtime_gui import (
    DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD,
    normalize_ocr_name,
    ocr_name_similarity,
)
LINGMAI_SHENMAI_ROOM_ID = 10
LINGMAI_UNION_SHENMAI_ROOM_ID = 17
LINGMAI_UNION_SHENGMAI_ROOM_ID = 18
LINGMAI_SHENMAI_ROOM_IDS = (LINGMAI_SHENMAI_ROOM_ID, LINGMAI_UNION_SHENMAI_ROOM_ID)
LINGMAI_SUPPORTED_ROOM_IDS = (*LINGMAI_SHENMAI_ROOM_IDS, LINGMAI_UNION_SHENGMAI_ROOM_ID)
LINGMAI_SAFE_BATTLE_RATIO = 1.0
LINGMAI_SHENGMAI_MIN_STRENGTH = 300
LINGMAI_DEFAULT_RETRY_SECONDS = 1800
LINGMAI_PROTECTION_RETRY_GRACE_MS = 5000


def lingmai_name_variants(value: Any) -> list[str]:
    """Normalize the Runtime name and its server-prefix-free GUI variants."""

    name = _sanitize_ocr_text(value)
    return list(dict.fromkeys(
        variant
        for variant in [
            name,
            *(part.strip() for part in re.split(r"[|｜]+", name)),
        ]
        if len(normalize_ocr_name(variant)) >= 2
    ))


def select_visible_lingmai_target(
    eligible_targets: list[dict[str, Any]],
    visible_text: str,
    *,
    threshold: float = DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD,
) -> dict[str, Any] | None:
    """Return the weakest Runtime-authorized target still visible in the GUI."""

    for target in eligible_targets:
        variants = lingmai_name_variants(target.get("name"))
        if variants and max(ocr_name_similarity(item, visible_text) for item in variants) >= threshold:
            return target
    return None


def lingmai_facts_retry_seconds(payload: Mapping[str, Any]) -> int:
    """Back off incomplete Runtime facts without creating a minute-level loop."""

    return int(
        payload.get("lingmai_facts_retry_seconds")
        or payload.get("lingmai_no_target_retry_seconds")
        or 1800
    )


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
    self_group_facts: dict[str, Any] | None = None,
    player_profile: dict[str, Any],
    current_strength: int | float | None = None,
    target_room_id: int | None = None,
    data_dir: str | Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Choose an empty seat or the weakest beatable non-friendly target.

    Shengmai is an upgrade tier.  It is considered only with at least 300
    strength so one 150-point kick/seat action still leaves a second action in
    reserve if the player is immediately displaced.
    """

    if not self_seat_facts.get("available"):
        return {"ok": False, "status": "invalid_facts", "reason": "self_seat_missing", "action": None}
    if not player_profile.get("available"):
        return {"ok": False, "status": "invalid_profile", "reason": "self_profile_missing", "action": None}
    self_role_id = _int_or_none(player_profile.get("role_id"))
    self_battle = _number_or_none(player_profile.get("battle_score"))
    if self_role_id is None or self_battle is None:
        return {"ok": False, "status": "invalid_profile", "reason": "self_profile_incomplete", "action": None}

    self_seat = self_seat_facts.get("seat") if isinstance(self_seat_facts.get("seat"), dict) else None
    self_owner = self_seat.get("owner") if isinstance(self_seat, dict) and isinstance(self_seat.get("owner"), dict) else None
    self_seat_role_id = _int_or_none(self_owner.get("role_id")) if self_owner else None
    room_id = _int_or_none(seat_facts.get("room_id"))
    expected_room_id = _int_or_none(target_room_id) or room_id
    if expected_room_id not in LINGMAI_SUPPORTED_ROOM_IDS:
        return {"ok": False, "status": "invalid_facts", "reason": "unsupported_lingmai_room", "action": None}
    strength = _number_or_none(current_strength)
    if expected_room_id == LINGMAI_UNION_SHENGMAI_ROOM_ID:
        if strength is None:
            return {"ok": False, "status": "invalid_facts", "reason": "strength_missing", "action": None}
        if strength < LINGMAI_SHENGMAI_MIN_STRENGTH:
            return {
                "ok": True,
                "status": "fallback_shenmai",
                "reason": "shengmai_strength_reserve_insufficient",
                "action": "fallback_shenmai",
                "strength": strength,
                "minimum_strength": LINGMAI_SHENGMAI_MIN_STRENGTH,
                "room_id": expected_room_id,
            }

    if self_seat_facts.get("seated"):
        if self_seat_role_id != self_role_id:
            return {"ok": False, "status": "invalid_facts", "reason": "self_seat_owner_mismatch", "action": None}
        own_room_id = _int_or_none(self_seat_facts.get("room_id"))
        if own_room_id is None:
            own_room_id = _int_or_none(seat_facts.get("room_id"))
        if own_room_id not in LINGMAI_SUPPORTED_ROOM_IDS:
            return {"ok": False, "status": "invalid_facts", "reason": "not_shenmai_room", "action": None}
        if own_room_id == expected_room_id or (
            own_room_id == LINGMAI_UNION_SHENGMAI_ROOM_ID
            and expected_room_id in LINGMAI_SHENMAI_ROOM_IDS
        ):
            return {
                "ok": True,
                "status": "already_seated",
                "action": "already_seated",
                "target": None,
                "self_seat": self_seat,
                "room_id": own_room_id,
            }

    if not seat_facts.get("available"):
        return {"ok": False, "status": "invalid_facts", "reason": "seat_roster_missing", "action": None}
    if room_id != expected_room_id:
        return {"ok": False, "status": "invalid_facts", "reason": "target_room_roster_mismatch", "action": None}
    if room_id not in LINGMAI_SUPPORTED_ROOM_IDS:
        return {"ok": False, "status": "invalid_facts", "reason": "not_shenmai_room", "action": None}
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
    self_team_uid = _int_or_none(self_owner.get("team_uid")) if self_owner else None
    if self_team_uid is None:
        self_team_uid = _int_or_none(player_profile.get("team_uid"))
    if self_team_uid is None:
        # Compatibility for older snapshots/tests that exposed the player's
        # group only through ``union_group_facts``.
        self_team_uid = _int_or_none((self_group_facts or {}).get("veins_group"))
    if room_id in {LINGMAI_UNION_SHENMAI_ROOM_ID, LINGMAI_UNION_SHENGMAI_ROOM_ID} and self_team_uid is None:
        return {"ok": False, "status": "invalid_facts", "reason": "self_veins_group_missing", "action": None}
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
        target_group_uid = _int_or_none(owner.get("team_uid"))
        if self_team_uid is not None and target_group_uid == self_team_uid:
            rejected["friendly"] += 1
            continue
        relation = classify_fanxiu_target_relation(
            is_npc=False,
            server_id=owner.get("server_id"),
            data_dir=data_dir,
        )
        if relation.get("camp") != "non_friendly":
            rejected["friendly"] += 1
            continue
        if battle_score >= safe_battle_max:
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
            "team_uid": target_group_uid,
            "team_name": str(owner.get("team_name") or ""),
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
            retry_at_ms = min(
                int(future[0]["protect_end_time"]) + LINGMAI_PROTECTION_RETRY_GRACE_MS,
                current_ms + LINGMAI_DEFAULT_RETRY_SECONDS * 1000,
            )
            retry_reason = "earliest_beatable_protection_end"
        else:
            retry_at_ms = current_ms + LINGMAI_DEFAULT_RETRY_SECONDS * 1000
            retry_reason = "no_current_or_future_beatable_target"

    return {
        "ok": True,
        "status": "kick" if target is not None else "retry",
        "action": "kick" if target is not None else "retry",
        "target": target,
        # GUI rows can change between the Runtime snapshot and OCR.  Expose
        # every currently-authorized beatable target so the caller can choose
        # the weakest one that is still visible instead of binding the whole
        # action to a single rapidly-stale name.
        "eligible_targets": eligible,
        "eligible_count": len(eligible),
        "future_count": len(future),
        "future_target": future[0] if future else None,
        "retry_at_ms": retry_at_ms,
        "retry_reason": retry_reason,
        "rejected": rejected,
        "safe_battle_ratio": LINGMAI_SAFE_BATTLE_RATIO,
        "safe_battle_max": safe_battle_max,
        "strength": strength,
        "minimum_shengmai_strength": LINGMAI_SHENGMAI_MIN_STRENGTH,
        "available_count": available_count,
        "room_id": room_id,
    }


def read_and_select_lingmai_runtime_action(
    *,
    snapshot: dict[str, Any] | None = None,
    data_dir: str | Path | None = None,
    now_ms: int | None = None,
    target_room_id: int | None = None,
) -> dict[str, Any]:
    """Read Lingmai seats from memory and choose a safe action without GUI OCR."""

    if snapshot is None:
        from backend.core.fanxiu.instrumentation.lingmai import read_lingmai_snapshot

        snapshot = read_lingmai_snapshot()
    runtime_snapshot = dict(snapshot)
    if runtime_snapshot.get("available") is False:
        return {
            "ok": False,
            "status": "runtime_unavailable",
            "reason": str(runtime_snapshot.get("reason") or "lingmai_runtime_unavailable"),
            "action": None,
            "source": "runtime_memory",
            "runtime_snapshot": runtime_snapshot,
        }
    roster_key = (
        "shengmai_roster"
        if target_room_id is not None and int(target_room_id) == LINGMAI_UNION_SHENGMAI_ROOM_ID
        else "shenmai_roster"
    )
    seat_facts = runtime_snapshot.get(roster_key) if isinstance(runtime_snapshot.get(roster_key), dict) else {}
    self_seat = (
        runtime_snapshot.get("self_seat_facts")
        if isinstance(runtime_snapshot.get("self_seat_facts"), dict)
        else {}
    )
    self_group = (
        runtime_snapshot.get("union_group_facts")
        if isinstance(runtime_snapshot.get("union_group_facts"), dict)
        else {}
    )
    profile = (
        runtime_snapshot.get("self_profile")
        if isinstance(runtime_snapshot.get("self_profile"), dict)
        else {}
    )
    selection = select_lingmai_seat_action(
        seat_facts,
        self_seat_facts=self_seat,
        self_group_facts=self_group,
        player_profile=profile,
        current_strength=runtime_snapshot.get("strength"),
        target_room_id=target_room_id,
        data_dir=data_dir,
        now_ms=now_ms,
    )
    return {
        **selection,
        "source": "runtime_memory",
        "seat_facts": seat_facts,
        "self_seat_facts": self_seat,
        "self_group_facts": self_group,
        "player_profile": profile,
        "runtime_snapshot": runtime_snapshot,
    }


def refresh_and_select_lingmai_seat_action(
    *,
    data_dir: str | Path | None = None,
    since_seconds: int = 1200,
    target_room_id: int = LINGMAI_UNION_SHENMAI_ROOM_ID,
) -> dict[str, Any]:
    """Read live seat truth from Runtime memory and choose a safe action.

    ``since_seconds`` remains in the public signature for old callers, but is
    deliberately ignored. Seat selection must not start packet capture or
    interpret a missing Runtime roster as an empty room.  Stable account
    Missing account identity or battle score is a Runtime completeness defect;
    callers retry after the model is loaded instead of reading capture history.
    """

    _ = since_seconds
    return read_and_select_lingmai_runtime_action(
        data_dir=data_dir,
        target_room_id=target_room_id,
    )


def refresh_lingmai_daily_status(
    *,
    since_seconds: int = 300,
    wait_seconds: float = 30.0,
    union_only: bool = True,
) -> dict[str, Any]:
    """Read today's authoritative Lingmai status from Runtime memory only.

    The caller invokes this only after entering the Lingmai pages, where the
    game's ``LuaUnionVenisMgr`` model is loaded. Missing/incomplete Runtime
    fields are returned as unknown and must never be rewritten as completion.
    """

    from backend.core.fanxiu.instrumentation.lingmai import read_lingmai_snapshot

    _ = (since_seconds, wait_seconds, union_only)
    return read_lingmai_snapshot()
