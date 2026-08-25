from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.state import (
    append_data_annotation_world_fact_event,
    read_data_annotation_world_facts,
    write_data_annotation_world_facts,
)


MAINTENANCE_RECOVERY_TASK_ID = "system-maintenance-recovery"
MAINTENANCE_RECOVERY_TASK_TYPE = "maintenance_recovery"
MAINTENANCE_SCENE_ID = 415
LOGIN_MAINTENANCE_PROMPT_SCENE_ID = 546
MAINTENANCE_REASON = "game_maintenance"
MAINTENANCE_CHECK_INTERVAL_MINUTES = 5
MAINTENANCE_PROBE_INTERVAL_SECONDS = 5.0
MAINTENANCE_PROBE_DURATION_SECONDS = 30.0
GAME_STARTUP_TIMEOUT_SECONDS = 300.0
GAME_STARTUP_POLL_SECONDS = 5.0


class FanxiuMaintenanceDetected(RuntimeError):
    """Signal that the game service is unavailable because of maintenance."""

    def __init__(self, message: str, *, evidence: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = message
        self.evidence = dict(evidence or {})


def infer_game_startup_scene(scene_id: int | None, text: Any) -> int | None:
    """Infer only stable game startup pages from full-frame OCR."""

    if scene_id in {14, 18, 34, 415, LOGIN_MAINTENANCE_PROMPT_SCENE_ID}:
        return scene_id
    compact = "".join(str(text or "").split())
    if "停更码字中" in compact and "敬请期待更新" in compact:
        return LOGIN_MAINTENANCE_PROMPT_SCENE_ID
    if "AppVer" in compact and "进入游戏" in compact and "健康游戏忠告" in compact:
        return 18
    if "游戏公告" in compact and ("更新公告" in compact or "停服维护" in compact):
        return 14
    return scene_id


def resolve_game_startup_scene(
    scene_id: int | None,
    score: float,
    text: Any,
) -> tuple[int | None, float]:
    """Apply authoritative full-frame startup semantics to a graph result."""

    resolved_scene_id = infer_game_startup_scene(scene_id, text)
    if resolved_scene_id != scene_id:
        return resolved_scene_id, 100.0
    return scene_id, float(score or 0.0)


def next_maintenance_check_time(now: datetime | None = None) -> datetime:
    """Return the next five-minute behavioral maintenance probe.

    Availability is proved by clicking the cover action and observing its
    successor, not by the button's visual style.  Restart policy is owned
    separately by the recovery Job.
    """

    current = now or datetime.now()
    minutes = (
        current.minute // MAINTENANCE_CHECK_INTERVAL_MINUTES + 1
    ) * MAINTENANCE_CHECK_INTERVAL_MINUTES
    candidate = current.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)
    if candidate <= current:
        candidate += timedelta(minutes=MAINTENANCE_CHECK_INTERVAL_MINUTES)
    return candidate


def maintenance_check_time_text(now: datetime | None = None) -> str:
    return next_maintenance_check_time(now).strftime("%Y-%m-%d %H:%M:%S")


def normalize_maintenance_gate(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        **source,
        "active": bool(source.get("active")),
        "state": str(source.get("state") or ("maintenance" if source.get("active") else "available")),
        "reason": str(source.get("reason") or ""),
        "scene_id": source.get("scene_id"),
        "opened_at": source.get("opened_at"),
        "last_observed_at": source.get("last_observed_at"),
        "resolved_at": source.get("resolved_at"),
        "evidence": dict(source.get("evidence") or {}) if isinstance(source.get("evidence"), dict) else {},
    }


def read_maintenance_gate(world_facts_path: Path) -> dict[str, Any]:
    facts = read_data_annotation_world_facts(world_facts_path)
    availability = facts.get("availability") if isinstance(facts.get("availability"), dict) else {}
    return normalize_maintenance_gate(availability.get("game"))


def open_maintenance_gate(
    world_facts_path: Path,
    *,
    observed_at: datetime | None = None,
    scene_id: int | None = MAINTENANCE_SCENE_ID,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = observed_at or datetime.now()
    now_ts = current.timestamp()
    facts = read_data_annotation_world_facts(world_facts_path)
    availability = facts.setdefault("availability", {})
    if not isinstance(availability, dict):
        availability = {}
        facts["availability"] = availability
    previous = normalize_maintenance_gate(availability.get("game"))
    gate = {
        **previous,
        "active": True,
        "state": "maintenance",
        "reason": MAINTENANCE_REASON,
        "scene_id": scene_id,
        "opened_at": previous.get("opened_at") or now_ts,
        "last_observed_at": now_ts,
        "resolved_at": None,
        "evidence": dict(evidence or {}),
    }
    availability["game"] = gate
    append_data_annotation_world_fact_event(
        facts,
        "game_availability",
        {
            "state": "maintenance",
            "reason": MAINTENANCE_REASON,
            "scene_id": scene_id,
            "observed_at": now_ts,
        },
    )
    write_data_annotation_world_facts(world_facts_path, facts)
    return normalize_maintenance_gate(gate)


def clear_maintenance_gate(
    world_facts_path: Path,
    *,
    resolved_at: datetime | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = resolved_at or datetime.now()
    now_ts = current.timestamp()
    facts = read_data_annotation_world_facts(world_facts_path)
    availability = facts.setdefault("availability", {})
    if not isinstance(availability, dict):
        availability = {}
        facts["availability"] = availability
    previous = normalize_maintenance_gate(availability.get("game"))
    gate = {
        **previous,
        "active": False,
        "state": "available",
        "reason": "",
        "scene_id": None,
        "last_observed_at": previous.get("last_observed_at"),
        "resolved_at": now_ts,
        "evidence": dict(evidence or {}),
    }
    availability["game"] = gate
    append_data_annotation_world_fact_event(
        facts,
        "game_availability",
        {
            "state": "available",
            "resolved_at": now_ts,
        },
    )
    write_data_annotation_world_facts(world_facts_path, facts)
    return normalize_maintenance_gate(gate)


def maintenance_gate_blocks_task(gate: dict[str, Any], task: dict[str, Any]) -> bool:
    if not bool(normalize_maintenance_gate(gate).get("active")):
        return False
    return (
        str(task.get("id") or "") != MAINTENANCE_RECOVERY_TASK_ID
        and str(task.get("task_type") or "") != MAINTENANCE_RECOVERY_TASK_TYPE
    )
