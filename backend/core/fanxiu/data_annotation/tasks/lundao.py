from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.catalog.server_relations import classify_fanxiu_target_relation
from backend.core.fanxiu.data_annotation.tasks.daofa import current_player_battle_score
from backend.core.fanxiu.packet.current_facts import (
    get_latest_fanxiu_faze_show_facts,
    get_latest_fanxiu_lundao_scene_seat_facts,
)
from backend.core.fanxiu.packet.service_runtime import (
    request_fanxiu_packet_service_catch_up,
    start_fanxiu_packet_service,
)
from backend.db import engine


LUNDAO_DALUO_ROOM_ID = 15
LUNDAO_SAFE_BATTLE_RATIO = 0.90
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
