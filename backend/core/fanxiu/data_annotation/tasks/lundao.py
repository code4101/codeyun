from __future__ import annotations

import json
import time
from datetime import datetime, time as time_cls, timedelta
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.catalog.server_relations import classify_fanxiu_target_relation
LUNDAO_DALUO_ROOM_ID = 15
LUNDAO_SANQING_ROOM_ID = 14
LUNDAO_SAFE_BATTLE_RATIO = 0.90
LUNDAO_FIRST_TRIGGER = time_cls(15, 30)
LUNDAO_PURCHASE_CUTOFF = time_cls(21, 0)
LUNDAO_CLOSE_TIME = time_cls(22, 0)
LUNDAO_SAFETY_THRESHOLD_CHANGE_TIMES = (
    time_cls(16, 30),
    time_cls(17, 0),
    time_cls(18, 0),
    time_cls(19, 30),
    time_cls(21, 0),
)
_FAZE_QUALITY_TO_CROSS = {quality: 1 << (quality - 1) for quality in range(1, 8)}


def lundao_purchase_allowed(at: datetime) -> bool:
    """Allow paid attempts only before 21:00 local game time."""

    return at.time() < LUNDAO_PURCHASE_CUTOFF


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


def _lundao_owner_faze_id(owner: dict[str, Any]) -> int | None:
    """Return the owner's equipped law id, treating explicit empty as no law.

    Runtime rosters expose each seat owner's law state.  ``0`` is the normal
    no-law value, but some projections may keep the key with an empty value
    for the same state.  A missing key is still incomplete data and must fail
    closed.
    """

    if "faze" not in owner:
        return None
    raw = owner.get("faze")
    parsed = _int_or_none(raw)
    if parsed is not None:
        return parsed
    if raw is None or str(raw).strip() == "":
        return 0
    return None


def _lundao_runtime_faze_id(value: Any) -> int | None:
    parsed = _int_or_none(value)
    if parsed is not None:
        return parsed
    if value is None or str(value).strip() == "":
        return 0
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
    """Read the current account profile from the live Lundao Runtime model."""

    from backend.core.fanxiu.instrumentation.lundao import read_lundao_snapshot

    return lundao_player_profile_from_runtime(
        read_lundao_snapshot(),
        export_root=export_root,
    )


def lundao_player_profile_from_runtime(
    snapshot: dict[str, Any],
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build the self comparison profile from the read-only Lundao model."""

    raw_profile = (
        snapshot.get("self_profile")
        if isinstance(snapshot.get("self_profile"), dict)
        else {}
    )
    faze_id = (
        _lundao_runtime_faze_id(raw_profile.get("faze"))
        if "faze" in raw_profile
        else None
    )
    faze = load_lundao_faze_catalog(export_root=export_root).get(
        faze_id if faze_id is not None else -1
    )
    available = (
        bool(raw_profile.get("available"))
        and _int_or_none(raw_profile.get("role_id")) is not None
        and _number_or_none(raw_profile.get("battle_score")) is not None
        and faze is not None
    )
    return {
        **raw_profile,
        **(faze or {}),
        "ok": available,
        "available": available,
        "source": "runtime_memory",
        "reason": None if available else "runtime_self_profile_incomplete",
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
    """Return the next 15:30 opening that has not already started."""

    today = datetime.combine(at.date(), LUNDAO_FIRST_TRIGGER)
    if at < today:
        return today
    return datetime.combine(at.date() + timedelta(days=1), LUNDAO_FIRST_TRIGGER)


def next_lundao_recheck(
    at: datetime,
    *,
    protect_end_time_ms: int | None = None,
) -> datetime:
    """Recheck at a calm 30-minute cadence, clipped to the next daily trigger."""

    # A safety-threshold boundary or one player's protection expiry does not
    # mean a seat will actually be available.  Using either as an earlier
    # trigger made a stable Sanqing seat bounce through the GUI repeatedly.
    del protect_end_time_ms
    next_at = at + timedelta(minutes=30)
    close_at = datetime.combine(at.date(), LUNDAO_CLOSE_TIME)
    return next_lundao_daily_trigger(at) if next_at >= close_at else next_at


def next_lundao_unseated_retry(at: datetime) -> datetime:
    """Recheck an unseated player every ten minutes during the open window."""

    clock = at.time().replace(tzinfo=None)
    if LUNDAO_FIRST_TRIGGER <= clock < LUNDAO_CLOSE_TIME:
        next_at = at + timedelta(minutes=10)
        close_at = datetime.combine(at.date(), LUNDAO_CLOSE_TIME)
        return next_lundao_daily_trigger(at) if next_at >= close_at else next_at
    return next_lundao_daily_trigger(at)


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
    target_faze_id = _lundao_owner_faze_id(owner)
    if role_id is None or not name or battle_score is None or target_faze_id is None:
        return "invalid", None
    if role_id == _int_or_none(player_profile.get("role_id")):
        return "self", None
    target_faze = catalog.get(target_faze_id)
    if target_faze is None:
        return "invalid", None
    self_battle = _number_or_none(player_profile.get("battle_score"))
    target_quality = int(target_faze["quality"])
    if self_battle is None:
        return "invalid_profile", None
    self_quality = _int_or_none(player_profile.get("quality"))
    if target_quality > 0 and self_quality is None:
        return "invalid_profile", None
    if target_quality > 0 and self_quality < target_quality:
        return "stronger_law", None
    if target_quality > 0 and self_quality == target_quality and battle_score > self_battle * LUNDAO_SAFE_BATTLE_RATIO:
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
    if _int_or_none(player_profile.get("role_id")) is None or _number_or_none(player_profile.get("battle_score")) is None:
        return {"ok": False, "status": "invalid_profile", "reason": "self_profile_incomplete", "target": None}

    catalog = load_lundao_faze_catalog(export_root=export_root)
    current_ms = int(at.timestamp() * 1000) if now_ms is None else int(now_ms)
    empty_count = max(0, int(available_count or 0))
    contributors: list[dict[str, Any]] = []
    eligible_no_law: list[dict[str, Any]] = []
    eligible_with_law: list[dict[str, Any]] = []
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
            if int(target.get("faze_id") or 0) == 0:
                eligible_no_law.append(target)
            else:
                eligible_with_law.append(target)
        elif reason == "protected_weaker" and target is not None:
            protected.append(target)
            rejected["protected"] += 1
        elif reason == "friendly_weaker":
            rejected["friendly"] += 1
        elif reason in rejected:
            rejected[reason] += 1
        else:
            rejected["invalid"] += 1

    eligible_no_law.sort(key=lambda item: (float(item["battle_score"]), int(item["seat_id"])))
    eligible_with_law.sort(key=lambda item: (int(item["faze_quality"]), float(item["battle_score"]), int(item["seat_id"])))
    protected.sort(key=lambda item: (int(item["protect_end_time"]), int(item["faze_quality"]), float(item["battle_score"])))
    threshold = lundao_safety_threshold(at) if require_safety_threshold else 0
    safety_score = empty_count + len(contributors)
    threshold_met = threshold is not None and safety_score >= int(threshold)
    has_action = empty_count > 0 or bool(eligible_no_law)
    actionable = threshold_met and has_action
    target = None if empty_count > 0 else (eligible_no_law[0] if eligible_no_law else None)
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
        "eligible_count": len(eligible_no_law),
        "eligible_no_law_count": len(eligible_no_law),
        "eligible_with_law_count": len(eligible_with_law),
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
        return {"action": "stay_daluo", "reason": "already_daluo", "next_time": next_day}
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
    """Read current status and room roster exclusively from Runtime memory."""

    del since_seconds
    from backend.core.fanxiu.instrumentation.lundao import read_lundao_snapshot

    status = read_lundao_snapshot()
    current_at = at or datetime.now()
    status = {
        **status,
        "current_left_listen_time_at": current_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
    room_id = _int_or_none(status.get("room_id"))
    if room_id == LUNDAO_DALUO_ROOM_ID:
        roster = status.get("daluo_roster")
    elif room_id == LUNDAO_SANQING_ROOM_ID:
        roster = status.get("sanqing_roster")
    else:
        roster = None
    if not isinstance(roster, dict):
        roster = {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": "current_room_roster_not_loaded",
            "room_id": room_id,
        }
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
    self_battle = _number_or_none(player_profile.get("battle_score"))
    if self_role_id is None or self_battle is None:
        return {"ok": False, "status": "invalid_profile", "reason": "self_profile_incomplete", "target": None}
    self_quality = _int_or_none(player_profile.get("quality"))

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
        target_faze_id = _lundao_owner_faze_id(owner)
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
        if target_quality > 0 and self_quality is None:
            rejected["invalid"] += 1
            continue
        if target_quality > 0 and self_quality < target_quality:
            rejected["stronger_law"] += 1
            continue
        if target_quality > 0 and self_quality == target_quality and battle_score > self_battle * LUNDAO_SAFE_BATTLE_RATIO:
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

    eligible_no_law = [item for item in eligible if int(item.get("faze_id") or 0) == 0]
    eligible_with_law = [item for item in eligible if int(item.get("faze_id") or 0) != 0]
    eligible_no_law.sort(key=lambda item: (float(item["battle_score"]), int(item["seat_id"])))
    eligible_with_law.sort(key=lambda item: (int(item["faze_quality"]), float(item["battle_score"]), int(item["seat_id"])))
    return {
        "ok": True,
        "status": "selected" if eligible_no_law else "no_target",
        "target": eligible_no_law[0] if eligible_no_law else None,
        "eligible_count": len(eligible_no_law),
        "eligible_no_law_count": len(eligible_no_law),
        "eligible_with_law_count": len(eligible_with_law),
        "rejected": rejected,
        "safe_battle_ratio": LUNDAO_SAFE_BATTLE_RATIO,
        "room_id": LUNDAO_DALUO_ROOM_ID,
    }


def read_and_select_lundao_runtime_target(
    *,
    snapshot: dict[str, Any] | None = None,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Read and select a Daluo target without packets or GUI actions."""

    if snapshot is None:
        from backend.core.fanxiu.instrumentation.lundao import read_lundao_snapshot

        snapshot = read_lundao_snapshot()
    runtime_snapshot = dict(snapshot)
    seat_facts = (
        runtime_snapshot.get("daluo_roster")
        if isinstance(runtime_snapshot.get("daluo_roster"), dict)
        else {}
    )
    profile = lundao_player_profile_from_runtime(
        runtime_snapshot,
        export_root=export_root,
    )
    selection = select_lundao_kick_target(
        seat_facts,
        player_profile=profile,
        data_dir=data_dir,
        export_root=export_root,
        now_ms=now_ms,
    )
    return {
        **selection,
        "source": "runtime_memory",
        "seat_facts": seat_facts,
        "player_profile": profile,
        "runtime_snapshot": runtime_snapshot,
    }


def refresh_and_select_lundao_kick_target(
    *,
    data_dir: str | Path | None = None,
    export_root: str | Path | None = None,
    since_seconds: int = 1200,
) -> dict[str, Any]:
    """Select exclusively from the live read-only Runtime model."""

    del since_seconds
    return read_and_select_lundao_runtime_target(
        data_dir=data_dir,
        export_root=export_root,
    )
