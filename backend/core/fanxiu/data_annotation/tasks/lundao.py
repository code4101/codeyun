from __future__ import annotations

import json
import time
from datetime import datetime, time as time_cls, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.catalog.server_relations import classify_fanxiu_target_relation
from backend.core.fanxiu.data_annotation.tasks.daofa import current_player_battle_score
from backend.core.fanxiu.packet.current_facts import (
    get_latest_fanxiu_faze_show_facts,
    get_latest_fanxiu_lundao_scene_seat_facts,
    get_latest_fanxiu_lundao_status_facts,
)
from backend.core.fanxiu.packet.service_runtime import (
    request_fanxiu_packet_service_catch_up,
    start_fanxiu_packet_service,
)
from backend.db import engine


LUNDAO_DALUO_ROOM_ID = 15
LUNDAO_SANQING_ROOM_ID = 14
LUNDAO_SAFE_BATTLE_RATIO = 0.90
LUNDAO_FIRST_TRIGGER = time_cls(15, 55)
LUNDAO_CLOSE_TIME = time_cls(22, 0)
_FAZE_QUALITY_TO_CROSS = {quality: 1 << (quality - 1) for quality in range(1, 8)}


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


def load_lundao_faze_catalog(*, export_root: str | Path | None = None) -> dict[int, dict[str, Any]]:
    """Load the law quality used by the game's own lundao comparison."""

    path = resolve_fanxiu_export_root(export_root) / "parsed_configs" / "FazeResource" / "rows.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"论道法则配置不是行列表：{path}")
    result: dict[int, dict[str, Any]] = {
        0: {"faze_id": 0, "name": "无法则", "quality": 0, "cross": 0},
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        faze_id = _int_or_none(row.get("id"))
        quality = _int_or_none(row.get("quality"))
        if faze_id is None or quality not in _FAZE_QUALITY_TO_CROSS:
            continue
        result[faze_id] = {
            "faze_id": faze_id,
            "name": str(row.get("name") or ""),
            "quality": quality,
            "cross": _FAZE_QUALITY_TO_CROSS[quality],
        }
    return result


def current_lundao_player_profile(*, export_root: str | Path | None = None) -> dict[str, Any]:
    """Combine the current account power with its latest equipped law fact."""

    profile = current_player_battle_score()
    if not profile.get("available"):
        return {"ok": False, "available": False, "reason": "self_battle_score_missing", "profile": profile}
    with Session(engine) as session:
        faze = get_latest_fanxiu_faze_show_facts(session)
    if not faze.get("available"):
        return {"ok": False, "available": False, "reason": "self_faze_missing", "profile": profile, "faze": faze}
    faze_id = _int_or_none(faze.get("faze_id"))
    faze_fact = load_lundao_faze_catalog(export_root=export_root).get(faze_id if faze_id is not None else -1)
    if faze_fact is None:
        return {
            "ok": False,
            "available": False,
            "reason": "self_faze_quality_unknown",
            "profile": profile,
            "faze": faze,
        }
    return {
        "ok": True,
        "available": True,
        **profile,
        **faze_fact,
        "faze_captured_at": str(faze.get("captured_at") or ""),
        "faze_evidence": faze.get("evidence") or {},
    }


def lundao_safety_threshold(at: datetime) -> int | None:
    """Return the configured Daluo safety cushion for the current local time."""

    clock = at.time().replace(tzinfo=None)
    if clock < LUNDAO_FIRST_TRIGGER or clock >= LUNDAO_CLOSE_TIME:
        return None
    if clock < time_cls(16, 30):
        return 6
    if clock < time_cls(17, 0):
        return 5
    if clock < time_cls(18, 0):
        return 4
    if clock < time_cls(19, 30):
        return 3
    if clock < time_cls(21, 0):
        return 2
    return 1


def next_lundao_daily_trigger(at: datetime) -> datetime:
    tomorrow = at.date() + timedelta(days=1)
    return datetime.combine(tomorrow, LUNDAO_FIRST_TRIGGER)


def next_lundao_recheck(
    at: datetime,
    *,
    protect_end_time_ms: int | None = None,
) -> datetime:
    """Choose the next same-day recheck, clipped to the next daily trigger."""

    candidates = [at + timedelta(minutes=30)]
    if protect_end_time_ms and protect_end_time_ms > int(at.timestamp() * 1000):
        candidates.append(datetime.fromtimestamp(protect_end_time_ms / 1000.0))
    next_at = min(candidates)
    close_at = datetime.combine(at.date(), LUNDAO_CLOSE_TIME)
    return next_lundao_daily_trigger(at) if next_at >= close_at else next_at


def current_lundao_left_listen_time(
    status_facts: dict[str, Any],
    *,
    at: datetime,
) -> int | None:
    """Return the live remaining daily reward time in milliseconds.

    The server sends ``leftListenTime`` as the remaining allowance at the
    beginning of the current seat.  While the player is still seated, the
    client subtracts the elapsed time since ``sitDownTime`` locally.  When the
    player is not seated, the raw value is already the current remainder.
    """

    left_time = _int_or_none(status_facts.get("left_listen_time"))
    if left_time is None:
        return None
    if left_time <= 0:
        return 0
    if not status_facts.get("seated"):
        return left_time
    sit_down_time = _int_or_none(status_facts.get("sit_down_time"))
    if sit_down_time is None or sit_down_time <= 0:
        return left_time
    elapsed = max(0, int(at.timestamp() * 1000) - sit_down_time)
    return max(0, left_time - elapsed)


def _lundao_target_fact(
    seat: dict[str, Any],
    *,
    player_profile: dict[str, Any],
    catalog: dict[int, dict[str, Any]],
    data_dir: str | Path | None,
    current_ms: int,
) -> tuple[str, dict[str, Any] | None]:
    owner = seat.get("owner") if isinstance(seat.get("owner"), dict) else None
    seat_id = _int_or_none(seat.get("seat_id"))
    if owner is None or seat_id is None:
        return "invalid", None
    role_id = _int_or_none(owner.get("role_id"))
    name = str(owner.get("name") or "").strip()
    battle_score = _number_or_none(owner.get("battle_score"))
    target_faze_id = _int_or_none(owner.get("faze"))
    if role_id is None or not name or battle_score is None or target_faze_id is None:
        return "invalid", None
    if role_id == _int_or_none(player_profile.get("role_id")):
        return "self", None
    target_faze = catalog.get(target_faze_id)
    if target_faze is None:
        return "invalid", None
    self_quality = _int_or_none(player_profile.get("quality"))
    self_battle = _number_or_none(player_profile.get("battle_score"))
    target_quality = int(target_faze["quality"])
    if self_quality is None or self_battle is None:
        return "invalid_profile", None
    if self_quality < target_quality:
        return "stronger_law", None
    if self_quality == target_quality and battle_score > self_battle * LUNDAO_SAFE_BATTLE_RATIO:
        return "unsafe_same_law_power", None

    relation = classify_fanxiu_target_relation(
        is_npc=False,
        server_id=owner.get("server_id"),
        data_dir=data_dir,
    )
    protect_end_time = _int_or_none(owner.get("protect_end_time")) or 0
    target = {
        "id": seat_id,
        "seat_id": seat_id,
        "role_id": role_id,
        "name": name,
        "server_id": _int_or_none(owner.get("server_id")),
        "alliance_id": _int_or_none(owner.get("alliance_id")),
        "battle_score": battle_score,
        "faze_id": target_faze_id,
        "faze_name": target_faze["name"],
        "faze_quality": target_quality,
        "faze_cross": target_faze["cross"],
        "relation": relation,
        "is_ally": relation.get("camp") != "non_friendly",
        "protected": protect_end_time > current_ms,
        "protect_end_time": protect_end_time,
        "excluded": relation.get("camp") != "non_friendly" or protect_end_time > current_ms,
    }
    if relation.get("camp") != "non_friendly":
        return "friendly_weaker", target
    if protect_end_time > current_ms:
        return "protected_weaker", target
    return "eligible", target


def evaluate_lundao_room_opportunity(
    seat_facts: dict[str, Any],
    *,
    player_profile: dict[str, Any],
    available_count: int | None,
    at: datetime,
    room_id: int,
    require_safety_threshold: bool,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Evaluate safety contributors separately from currently legal actions."""

    if not seat_facts.get("available") or not seat_facts.get("complete"):
        return {"ok": False, "status": "invalid_facts", "reason": "seat_roster_incomplete", "target": None}
    if _int_or_none(seat_facts.get("room_id")) != int(room_id):
        return {"ok": False, "status": "invalid_facts", "reason": "wrong_room", "target": None}
    if available_count is None:
        return {"ok": False, "status": "invalid_facts", "reason": "room_available_count_missing", "target": None}
    if not player_profile.get("available"):
        return {"ok": False, "status": "invalid_profile", "reason": "self_profile_missing", "target": None}
    if any(_int_or_none(player_profile.get(key)) is None for key in ("role_id", "quality")) or _number_or_none(player_profile.get("battle_score")) is None:
        return {"ok": False, "status": "invalid_profile", "reason": "self_profile_incomplete", "target": None}

    catalog = load_lundao_faze_catalog(export_root=export_root)
    current_ms = int(at.timestamp() * 1000) if now_ms is None else int(now_ms)
    empty_count = max(0, int(available_count or 0))
    contributors: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    rejected = {
        "self": 0,
        "friendly": 0,
        "protected": 0,
        "stronger_law": 0,
        "unsafe_same_law_power": 0,
        "invalid": 0,
    }
    for seat in seat_facts.get("seats") or []:
        if not isinstance(seat, dict):
            rejected["invalid"] += 1
            continue
        reason, target = _lundao_target_fact(
            seat,
            player_profile=player_profile,
            catalog=catalog,
            data_dir=data_dir,
            current_ms=current_ms,
        )
        if target is not None:
            contributors.append(target)
        if reason == "eligible" and target is not None:
            eligible.append(target)
        elif reason == "protected_weaker" and target is not None:
            protected.append(target)
            rejected["protected"] += 1
        elif reason == "friendly_weaker":
            rejected["friendly"] += 1
        elif reason in rejected:
            rejected[reason] += 1
        else:
            rejected["invalid"] += 1

    eligible.sort(key=lambda item: (int(item["faze_quality"]), float(item["battle_score"]), int(item["seat_id"])))
    protected.sort(key=lambda item: (int(item["protect_end_time"]), int(item["faze_quality"]), float(item["battle_score"])))
    threshold = lundao_safety_threshold(at) if require_safety_threshold else 0
    safety_score = empty_count + len(contributors)
    threshold_met = threshold is not None and safety_score >= int(threshold)
    has_action = empty_count > 0 or bool(eligible)
    actionable = threshold_met and has_action
    target = None if empty_count > 0 else (eligible[0] if eligible else None)
    return {
        "ok": True,
        "status": "actionable" if actionable else "wait",
        "room_id": int(room_id),
        "available_count": empty_count,
        "weaker_count": len(contributors),
        "safety_score": safety_score,
        "threshold": threshold,
        "threshold_met": threshold_met,
        "has_action": has_action,
        "actionable": actionable,
        "action": "empty" if actionable and empty_count > 0 else ("kick" if actionable and target else "wait"),
        "target": target,
        "eligible_count": len(eligible),
        "contributors": contributors,
        "protected_candidates": protected,
        "earliest_protect_end_time": protected[0]["protect_end_time"] if protected else None,
        "rejected": rejected,
        "safe_battle_ratio": LUNDAO_SAFE_BATTLE_RATIO,
    }


def plan_lundao_strategy(
    status_facts: dict[str, Any],
    *,
    daluo_opportunity: dict[str, Any] | None,
    at: datetime,
) -> dict[str, Any]:
    """Turn fresh server facts and a Daluo evaluation into one GUI intent."""

    next_day = next_lundao_daily_trigger(at)
    threshold = lundao_safety_threshold(at)
    if threshold is None:
        return {"action": "done", "reason": "outside_window", "next_time": next_day}
    if not status_facts.get("available"):
        return {"action": "retry", "reason": "status_missing", "next_time": at + timedelta(minutes=5)}
    current_left_listen_time = _int_or_none(status_facts.get("current_left_listen_time"))
    if current_left_listen_time is None:
        current_left_listen_time = current_lundao_left_listen_time(status_facts, at=at)
    if current_left_listen_time is not None and current_left_listen_time <= 0:
        return {"action": "done", "reason": "listen_time_exhausted", "next_time": next_day}
    room_id = _int_or_none(status_facts.get("room_id"))
    if room_id == LUNDAO_DALUO_ROOM_ID:
        return {"action": "stay_daluo", "reason": "already_daluo", "next_time": next_lundao_recheck(at)}
    if not daluo_opportunity or not daluo_opportunity.get("ok"):
        return {"action": "retry", "reason": "daluo_facts_missing", "next_time": at + timedelta(minutes=5)}
    if daluo_opportunity.get("actionable"):
        return {
            "action": "seat_daluo",
            "reason": "daluo_safe",
            "room_id": LUNDAO_DALUO_ROOM_ID,
            "seat_action": daluo_opportunity.get("action"),
            "target": daluo_opportunity.get("target"),
            "next_time": next_lundao_recheck(at),
        }
    if room_id == LUNDAO_SANQING_ROOM_ID:
        return {
            "action": "stay_sanqing",
            "reason": "daluo_not_safe",
            "next_time": next_lundao_recheck(
                at,
                protect_end_time_ms=_int_or_none(daluo_opportunity.get("earliest_protect_end_time")),
            ),
        }
    return {
        "action": "seat_sanqing",
        "reason": "daluo_not_safe",
        "room_id": LUNDAO_SANQING_ROOM_ID,
        "next_time": next_lundao_recheck(
            at,
            protect_end_time_ms=_int_or_none(daluo_opportunity.get("earliest_protect_end_time")),
        ),
    }


def read_current_lundao_facts(*, since_seconds: int = 1200, at: datetime | None = None) -> dict[str, Any]:
    """Read current status and last viewed room roster without triggering capture work."""

    with Session(engine) as session:
        status = get_latest_fanxiu_lundao_status_facts(session, since_seconds=max(60, int(since_seconds)))
        roster = get_latest_fanxiu_lundao_scene_seat_facts(session, since_seconds=max(60, int(since_seconds)))
    current_at = at or datetime.now()
    status = {
        **status,
        "current_left_listen_time": current_lundao_left_listen_time(status, at=current_at),
        "current_left_listen_time_at": current_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if roster.get("room_id") is None:
        npc_id = _int_or_none(roster.get("npc_id"))
        theme_id = _int_or_none(roster.get("theme_id"))
        matches = [
            room
            for room in status.get("rooms") or []
            if isinstance(room, dict)
            and _int_or_none(room.get("npc_id")) == npc_id
            and _int_or_none(room.get("theme_id")) == theme_id
        ]
        if npc_id is not None and theme_id is not None and len(matches) == 1:
            roster = {**roster, "room_id": _int_or_none(matches[0].get("room_id")), "room_id_source": "npc_theme_match"}
    return {"status": status, "roster": roster}


def select_lundao_kick_target(
    seat_facts: dict[str, Any],
    *,
    player_profile: dict[str, Any],
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Select the weakest beatable non-friendly player in the Daluo room."""

    if not seat_facts.get("available") or not seat_facts.get("complete"):
        return {"ok": False, "status": "invalid_facts", "reason": "seat_roster_incomplete", "target": None}
    if _int_or_none(seat_facts.get("room_id")) != LUNDAO_DALUO_ROOM_ID:
        return {"ok": False, "status": "invalid_facts", "reason": "not_daluo_room", "target": None}
    if not player_profile.get("available"):
        return {"ok": False, "status": "invalid_profile", "reason": "self_profile_missing", "target": None}

    self_role_id = _int_or_none(player_profile.get("role_id"))
    self_quality = _int_or_none(player_profile.get("quality"))
    self_battle = _number_or_none(player_profile.get("battle_score"))
    if self_role_id is None or self_quality is None or self_battle is None:
        return {"ok": False, "status": "invalid_profile", "reason": "self_profile_incomplete", "target": None}

    catalog = load_lundao_faze_catalog(export_root=export_root)
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    eligible: list[dict[str, Any]] = []
    rejected = {
        "self": 0,
        "friendly": 0,
        "protected": 0,
        "stronger_law": 0,
        "unsafe_same_law_power": 0,
        "invalid": 0,
    }
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
        target_faze_id = _int_or_none(owner.get("faze"))
        if role_id is None or not name or battle_score is None or target_faze_id is None:
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
        protect_end_time = _int_or_none(owner.get("protect_end_time")) or 0
        if protect_end_time > current_ms:
            rejected["protected"] += 1
            continue
        target_faze = catalog.get(target_faze_id)
        if target_faze is None:
            rejected["invalid"] += 1
            continue
        target_quality = int(target_faze["quality"])
        if self_quality < target_quality:
            rejected["stronger_law"] += 1
            continue
        if self_quality == target_quality and battle_score > self_battle * LUNDAO_SAFE_BATTLE_RATIO:
            rejected["unsafe_same_law_power"] += 1
            continue
        eligible.append(
            {
                "id": seat_id,
                "seat_id": seat_id,
                "role_id": role_id,
                "name": name,
                "server_id": _int_or_none(owner.get("server_id")),
                "alliance_id": _int_or_none(owner.get("alliance_id")),
                "battle_score": battle_score,
                "faze_id": target_faze_id,
                "faze_name": target_faze["name"],
                "faze_quality": target_quality,
                "faze_cross": target_faze["cross"],
                "relation": relation,
                "is_ally": False,
                "excluded": False,
            }
        )

    eligible.sort(key=lambda item: (int(item["faze_quality"]), float(item["battle_score"]), int(item["seat_id"])))
    return {
        "ok": True,
        "status": "selected" if eligible else "no_target",
        "target": eligible[0] if eligible else None,
        "eligible_count": len(eligible),
        "rejected": rejected,
        "safe_battle_ratio": LUNDAO_SAFE_BATTLE_RATIO,
        "room_id": LUNDAO_DALUO_ROOM_ID,
    }


def refresh_and_select_lundao_kick_target(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    since_seconds: int = 1200,
) -> dict[str, Any]:
    """Catch up the live packet service and select from the current Daluo roster."""

    start_result = start_fanxiu_packet_service()
    catch_up = request_fanxiu_packet_service_catch_up(
        reason="daily-lundao-select-kick-target",
        wait_seconds=120.0,
    )
    with Session(engine) as session:
        seat_facts = get_latest_fanxiu_lundao_scene_seat_facts(
            session,
            since_seconds=max(60, int(since_seconds)),
        )
    profile = current_lundao_player_profile(export_root=export_root)
    if not profile.get("available"):
        selection = {
            "ok": False,
            "status": "invalid_profile",
            "reason": str(profile.get("reason") or "self_profile_missing"),
            "target": None,
        }
    else:
        selection = select_lundao_kick_target(
            seat_facts,
            player_profile=profile,
            data_dir=data_dir,
            export_root=export_root,
        )
    return {
        **selection,
        "start_result": start_result,
        "catch_up": catch_up,
        "seat_facts": seat_facts,
        "player_profile": profile,
    }
