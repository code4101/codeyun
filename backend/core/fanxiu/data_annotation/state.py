from __future__ import annotations

import time
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any

from pyxllib.prog import (
    append_fact_event,
    append_status_log,
    append_status_log_once,
    ensure_mapping_bucket,
    fact_key,
    next_daily_time,
    normalize_guard_items,
    normalize_job_record,
    normalize_scheduled_task_record,
    parse_daily_clock,
    parse_schedule_time,
    read_json_state,
    read_json_state_dict,
    scheduled_task_state,
    schedule_task_due,
    status_live_empty,
    trim_fact_events,
    write_json_state,
)


def write_data_annotation_json(path: Path, payload: Any) -> None:
    write_json_state(path, payload)


def read_data_annotation_json(path: Path, default: Any) -> Any:
    return read_json_state(path, default)


def initial_data_annotation_world_facts() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": time.time(),
        "runtime": {
            "entry_id": "",
            "current_scene": None,
            "current_task": "",
            "current_task_id": "",
            "task_type": "",
            "phase": "",
            "status": "idle",
            "running": False,
            "message": "",
            "updated_at": None,
        },
        "guard": {
            "enabled": False,
            "running": False,
            "entry_id": "",
            "last_event": {},
            "updated_at": None,
        },
        "discoveries": {
            "scene": {},
            "popup": {},
            "occlusion": {},
            "task": {},
        },
        "events": [],
    }


def read_data_annotation_world_facts(path: Path) -> dict[str, Any]:
    raw = read_data_annotation_json(path, None)
    facts = initial_data_annotation_world_facts()
    if not isinstance(raw, dict):
        return facts
    for key, value in raw.items():
        if key in {"runtime", "guard", "discoveries"} and isinstance(value, dict):
            target = facts[key]
            if isinstance(target, dict):
                for sub_key, sub_value in value.items():
                    if sub_key == "discoveries":
                        continue
                    if isinstance(target.get(sub_key), dict) and isinstance(sub_value, dict):
                        target[sub_key].update(sub_value)
                    else:
                        target[sub_key] = sub_value
        elif key == "events" and isinstance(value, list):
            facts["events"] = [item for item in value if isinstance(item, dict)][-200:]
        elif key in facts:
            facts[key] = value

    # Backward compatibility for the previous flat mirror file.
    if "current_scene" in raw and not facts["runtime"].get("current_scene"):
        facts["runtime"].update({
            "entry_id": raw.get("entry_id") or "",
            "current_scene": raw.get("current_scene"),
            "current_task": raw.get("current_task") or "",
            "phase": raw.get("phase") or "",
            "running": bool(raw.get("running")),
        })
    if "last_guard_event" in raw and isinstance(raw.get("last_guard_event"), dict):
        facts["guard"]["last_event"] = raw.get("last_guard_event") or {}
    return facts


def write_data_annotation_world_facts(path: Path, facts: dict[str, Any]) -> None:
    facts["version"] = 1
    facts["updated_at"] = time.time()
    trim_fact_events(facts)
    write_data_annotation_json(path, facts)


def data_annotation_fact_key(prefix: str, *parts: Any) -> str:
    return fact_key(prefix, *parts)


def append_data_annotation_world_fact_event(facts: dict[str, Any], kind: str, payload: dict[str, Any]) -> None:
    append_fact_event(facts, kind, payload)


def record_data_annotation_scheduler_task_fact(path: Path, task: dict[str, Any], result: str) -> None:
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return
    facts = read_data_annotation_world_facts(path)
    task_facts = ensure_mapping_bucket(facts, "discoveries", "task")
    existing_fact = task_facts.get(task_id) if isinstance(task_facts.get(task_id), dict) else {}
    task_facts[task_id] = {
        **existing_fact,
        "id": task_id,
        "task_type": str(task.get("task_type") or ""),
        "label": str(task.get("label") or task_id),
        "source": str(task.get("source") or ""),
        "schedule_kind": str(task.get("schedule_kind") or ""),
        "last_result": result,
        "last_run_at": task.get("last_run_at") if task.get("last_run_at") else None,
        "next_time": task.get("next_time") if task.get("next_time") else None,
        "retry_after": task.get("retry_after") if task.get("retry_after") else None,
        "updated_at": time.time(),
    }
    append_data_annotation_world_fact_event(
        facts,
        "scheduler_task",
        {
            "task_id": task_id,
            "task_type": str(task.get("task_type") or ""),
            "result": result,
        },
    )
    write_data_annotation_world_facts(path, facts)


def persist_data_annotation_runtime_status(
    runtime_state_path: Path,
    world_facts_path: Path,
    status: dict[str, Any],
) -> None:
    write_data_annotation_json(runtime_state_path, status)
    now = time.time()
    facts = read_data_annotation_world_facts(world_facts_path)
    runtime = ensure_mapping_bucket(facts, "runtime")
    runtime.update({
        "entry_id": status.get("entry_id") or "",
        "current_scene": status.get("current_scene"),
        "current_task": status.get("current_task") or "",
        "current_task_id": status.get("current_task_id") or "",
        "task_type": status.get("task_type") or "",
        "phase": status.get("phase") or "",
        "status": status.get("status") or ("running" if status.get("running") else "idle"),
        "service_running": bool(status.get("service_running")),
        "running": bool(status.get("running")),
        "message": status.get("message") or "",
        "updated_at": now,
    })

    guard = ensure_mapping_bucket(facts, "guard")
    last_guard_event = status.get("last_guard_event") if isinstance(status.get("last_guard_event"), dict) else {}
    guard.update({
        "group_enabled": bool(status.get("guard_group_enabled", True)),
        "enabled": bool(status.get("guard_enabled")),
        "running": bool(status.get("guard_running")),
        "entry_id": status.get("guard_entry_id") or "",
        "last_event": last_guard_event,
        "updated_at": now,
    })

    scene_id = status.get("current_scene")
    if scene_id is not None:
        scene_facts = ensure_mapping_bucket(facts, "discoveries", "scene")
        scene_facts[str(scene_id)] = {
            "scene": scene_id,
            "entry_id": status.get("entry_id") or status.get("guard_entry_id") or "",
            "task_type": status.get("task_type") or "",
            "phase": status.get("phase") or "",
            "message": status.get("message") or "",
            "seen_at": now,
        }
    if last_guard_event:
        guard_kind = str(last_guard_event.get("kind") or "popup")
        bucket_key = "occlusion" if guard_kind == "occlusion" else "popup"
        bucket = ensure_mapping_bucket(facts, "discoveries", bucket_key)
        popup_fact_key = data_annotation_fact_key(
            bucket_key,
            last_guard_event.get("image"),
            last_guard_event.get("title"),
            last_guard_event.get("folder_path"),
        )
        bucket[popup_fact_key] = {
            **last_guard_event,
            "updated_at": now,
        }
        append_data_annotation_world_fact_event(facts, f"guard_{bucket_key}", last_guard_event)
    write_data_annotation_world_facts(world_facts_path, facts)


def read_data_annotation_runtime_status(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return read_json_state_dict(path)


def initial_data_annotation_runtime_status() -> dict[str, Any]:
    return {
        "ok": True,
        "service_running": False,
        "running": False,
        "guard_group_enabled": True,
        "guard_enabled": False,
        "guard_running": False,
        "guard_entry_id": "",
        "guard_interval_seconds": 2.0,
        "guard_items": {},
        "status": "idle",
        "entry_id": "",
        "task_type": "",
        "current_task": "",
        "phase": "",
        "current_scene": None,
        "message": "",
        "current_index": 0,
        "total": 0,
        "current_code": "",
        "current_task_id": "",
        "interruptible": True,
        "last_guard_event": {},
        "started_at": 0,
        "updated_at": 0,
        "finished_at": 0,
        "error": "",
        "logs": [],
    }


def is_data_annotation_runtime_live_empty(status: dict[str, Any]) -> bool:
    return status_live_empty(status)


def append_data_annotation_runtime_status_log(
    status: dict[str, Any],
    kind: str,
    message: str,
    *,
    scope: str = "",
    item_id: str = "",
    time_text: str | None = None,
    updated_at: float | None = None,
) -> None:
    append_status_log(
        status,
        kind,
        message,
        scope=scope,
        item_id=item_id,
        time_text=time_text,
        updated_at=updated_at,
    )


def append_data_annotation_runtime_log_once(
    status: dict[str, Any],
    kind: str,
    message: str,
    *,
    time_text: str | None = None,
) -> None:
    append_status_log_once(status, kind, message, time_text=time_text)


def normalize_data_annotation_runtime_guard_items(
    status: dict[str, Any],
    guard_definitions: dict[str, dict[str, Any]],
) -> None:
    close_popups_override: dict[str, Any] = {
        "enabled": bool(status.get("guard_enabled")),
        "running": bool(status.get("guard_group_enabled", True) and status.get("guard_running")),
        "entry_id": str(status.get("guard_entry_id") or ""),
    }
    last_guard_event = status.get("last_guard_event")
    if isinstance(last_guard_event, dict) and last_guard_event.get("title"):
        close_popups_override["message"] = str(last_guard_event.get("title") or "")
    status["guard_items"] = normalize_guard_items(
        guard_definitions,
        status.get("guard_items"),
        overrides={"close_popups": close_popups_override},
    )


def normalize_data_annotation_scheduler_task(item: Any) -> dict[str, Any] | None:
    return normalize_scheduled_task_record(item, default_source="manual", default_schedule_kind="manual")


def data_annotation_scheduler_task_state(task: dict[str, Any]) -> dict[str, Any]:
    return scheduled_task_state(task)


def normalize_data_annotation_scheduler_settings(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    return {
        "job_group_enabled": bool(source.get("job_group_enabled", True)),
        "updated_at": float(source.get("updated_at") or 0),
    }


def normalize_data_annotation_manual_job(item: Any) -> dict[str, Any] | None:
    return normalize_job_record(item, default_group="manual_job")


def parse_data_annotation_task_time(value: Any) -> float | None:
    return parse_schedule_time(value)


def parse_data_annotation_daily_clock(value: Any) -> dt_time | None:
    return parse_daily_clock(value)


def next_data_annotation_scheduler_time(task: dict[str, Any], now: datetime | None = None) -> str | None:
    if str(task.get("schedule_kind") or "") != "daily":
        return None
    return next_daily_time(task.get("schedule_times", []), base_time=now)


def data_annotation_task_due(task: dict[str, Any]) -> bool:
    return schedule_task_due(task, now=time.time())
