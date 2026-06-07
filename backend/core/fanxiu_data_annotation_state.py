from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any


def write_data_annotation_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        for attempt in range(8):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt >= 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def read_data_annotation_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


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
    events = facts.get("events")
    if isinstance(events, list):
        facts["events"] = [item for item in events if isinstance(item, dict)][-200:]
    write_data_annotation_json(path, facts)


def data_annotation_fact_key(prefix: str, *parts: Any) -> str:
    text = ":".join(str(part or "").strip() for part in parts if str(part or "").strip())
    return f"{prefix}:{text}" if text else prefix


def append_data_annotation_world_fact_event(facts: dict[str, Any], kind: str, payload: dict[str, Any]) -> None:
    event = {**payload, "time": time.time(), "kind": kind}
    events = facts.setdefault("events", [])
    if isinstance(events, list):
        events.append(event)


def record_data_annotation_scheduler_task_fact(path: Path, task: dict[str, Any], result: str) -> None:
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return
    facts = read_data_annotation_world_facts(path)
    discoveries = facts.setdefault("discoveries", {})
    if not isinstance(discoveries, dict):
        discoveries = {}
        facts["discoveries"] = discoveries
    task_facts = discoveries.setdefault("task", {})
    if not isinstance(task_facts, dict):
        task_facts = {}
        discoveries["task"] = task_facts
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
    runtime = facts.setdefault("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
        facts["runtime"] = runtime
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

    guard = facts.setdefault("guard", {})
    if not isinstance(guard, dict):
        guard = {}
        facts["guard"] = guard
    last_guard_event = status.get("last_guard_event") if isinstance(status.get("last_guard_event"), dict) else {}
    guard.update({
        "enabled": bool(status.get("guard_enabled")),
        "running": bool(status.get("guard_running")),
        "entry_id": status.get("guard_entry_id") or "",
        "last_event": last_guard_event,
        "updated_at": now,
    })

    discoveries = facts.setdefault("discoveries", {})
    if not isinstance(discoveries, dict):
        discoveries = {}
        facts["discoveries"] = discoveries
    scene_id = status.get("current_scene")
    if scene_id is not None:
        scene_facts = discoveries.setdefault("scene", {})
        if isinstance(scene_facts, dict):
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
        bucket = discoveries.setdefault(bucket_key, {})
        if isinstance(bucket, dict):
            fact_key = data_annotation_fact_key(
                bucket_key,
                last_guard_event.get("image"),
                last_guard_event.get("title"),
                last_guard_event.get("folder_path"),
            )
            bucket[fact_key] = {
                **last_guard_event,
                "updated_at": now,
            }
        append_data_annotation_world_fact_event(facts, f"guard_{bucket_key}", last_guard_event)
    write_data_annotation_world_facts(world_facts_path, facts)


def read_data_annotation_runtime_status(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = read_data_annotation_json(path, {})
    return payload if isinstance(payload, dict) else {}


def initial_data_annotation_runtime_status() -> dict[str, Any]:
    return {
        "ok": True,
        "service_running": False,
        "running": False,
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
    return (
        not bool(status.get("running"))
        and str(status.get("status") or "idle") == "idle"
        and not str(status.get("task_type") or "")
        and not str(status.get("current_task") or "")
        and not status.get("logs")
        and not status.get("started_at")
    )


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
    logs = list(status.get("logs") or [])
    logs.append({
        "time": time_text or datetime.now().strftime("%H:%M:%S"),
        "kind": kind,
        "scope": scope,
        "item_id": item_id,
        "message": message,
    })
    status["logs"] = logs[-500:]
    status["updated_at"] = time.time() if updated_at is None else updated_at


def append_data_annotation_runtime_log_once(
    status: dict[str, Any],
    kind: str,
    message: str,
    *,
    time_text: str | None = None,
) -> None:
    logs = status.get("logs")
    if not isinstance(logs, list):
        logs = []
    if not any(isinstance(item, dict) and item.get("kind") == kind and item.get("message") == message for item in logs):
        logs.append({"time": time_text or datetime.now().strftime("%H:%M:%S"), "kind": kind, "message": message})
    status["logs"] = logs[-500:]


def normalize_data_annotation_runtime_guard_items(
    status: dict[str, Any],
    guard_definitions: dict[str, dict[str, Any]],
) -> None:
    raw_items = status.get("guard_items")
    if not isinstance(raw_items, dict):
        raw_items = {}
    normalized: dict[str, dict[str, Any]] = {}
    for guard_id, definition in guard_definitions.items():
        raw_item = raw_items.get(guard_id)
        if not isinstance(raw_item, dict):
            raw_item = {}
        enabled = bool(raw_item.get("enabled"))
        running = bool(raw_item.get("running"))
        entry_id = str(raw_item.get("entry_id") or "")
        message = str(raw_item.get("message") or definition.get("message") or "")
        if guard_id == "close_popups":
            enabled = bool(status.get("guard_enabled"))
            running = bool(status.get("guard_running"))
            entry_id = str(status.get("guard_entry_id") or "")
            last_guard_event = status.get("last_guard_event")
            if isinstance(last_guard_event, dict) and last_guard_event.get("title"):
                message = str(last_guard_event.get("title") or "")
        normalized[guard_id] = {
            **definition,
            "enabled": enabled,
            "running": running,
            "entry_id": entry_id,
            "updated_at": float(raw_item.get("updated_at") or 0),
            "message": message,
        }
    status["guard_items"] = normalized


def normalize_data_annotation_scheduler_task(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    task_id = str(item.get("id") or "").strip()
    task_type = str(item.get("task_type") or "").strip()
    if not task_id or not task_type:
        return None
    return {
        "id": task_id,
        "task_type": task_type,
        "label": str(item.get("label") or task_id),
        "source": str(item.get("source") or "manual"),
        "schedule_kind": str(item.get("schedule_kind") or "manual"),
        "legacy_name": str(item.get("legacy_name") or ""),
        "enabled": bool(item.get("enabled")),
        "interruptible": bool(item.get("interruptible", True)),
        "next_time": item.get("next_time") if item.get("next_time") else None,
        "schedule_times": [str(value) for value in item.get("schedule_times", [])] if isinstance(item.get("schedule_times"), list) else [],
        "window": [str(value) for value in item.get("window", [])[:2]] if isinstance(item.get("window"), list) else None,
        "last_run_at": item.get("last_run_at") if item.get("last_run_at") else None,
        "last_result": str(item.get("last_result") or ""),
        "retry_after": item.get("retry_after") if item.get("retry_after") else None,
        "cooldown_seconds": int(item.get("cooldown_seconds") or 0),
        "payload": item.get("payload") if isinstance(item.get("payload"), dict) else {},
        "checkpoint": item.get("checkpoint") if isinstance(item.get("checkpoint"), dict) else None,
    }


def data_annotation_scheduler_task_state(task: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in task.items() if key not in {"supported"}}


def normalize_data_annotation_manual_job(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    task_type = str(item.get("task_type") or "").strip()
    if not task_type:
        return None
    created_at = float(item.get("created_at") or time.time())
    task_id = str(item.get("id") or f"manual-{uuid.uuid4().hex}")
    return {
        "id": task_id,
        "task_type": task_type,
        "label": str(item.get("label") or task_type),
        "group": "manual_job",
        "status": str(item.get("status") or "pending"),
        "interruptible": bool(item.get("interruptible", True)),
        "payload": item.get("payload") if isinstance(item.get("payload"), dict) else {},
        "created_at": created_at,
        "updated_at": float(item.get("updated_at") or created_at),
    }


def parse_data_annotation_task_time(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).timestamp()
        except ValueError:
            pass
    return None


def parse_data_annotation_daily_clock(value: Any) -> dt_time | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    return None


def next_data_annotation_scheduler_time(task: dict[str, Any], now: datetime | None = None) -> str | None:
    if str(task.get("schedule_kind") or "") != "daily":
        return None
    clocks = [
        clock
        for value in task.get("schedule_times", [])
        if (clock := parse_data_annotation_daily_clock(value)) is not None
    ]
    if not clocks:
        return None
    base = now or datetime.now()
    candidates: list[datetime] = []
    for day_offset in (0, 1):
        current_date = base.date() + timedelta(days=day_offset)
        for clock in clocks:
            candidate = datetime.combine(current_date, clock)
            if candidate > base:
                candidates.append(candidate)
    if not candidates:
        return None
    return min(candidates).strftime("%Y-%m-%d %H:%M:%S")


def data_annotation_task_due(task: dict[str, Any]) -> bool:
    if not task.get("enabled"):
        return False
    next_at = parse_data_annotation_task_time(task.get("next_time"))
    retry_at = parse_data_annotation_task_time(task.get("retry_after"))
    due_at = retry_at if retry_at is not None else next_at
    return due_at is None or due_at <= time.time()
