from __future__ import annotations

import re
import time
from datetime import time as dt_time
from pathlib import Path
from typing import Any

from filelock import FileLock
from pyxllib.prog import (
    append_fact_event,
    append_status_log,
    append_status_log_once,
    ensure_mapping_bucket,
    fact_key,
    normalize_guard_items,
    normalize_job_record,
    normalize_scheduled_task_record,
    parse_daily_clock,
    parse_schedule_time,
    read_json_state,
    read_json_state_dict,
    scheduled_task_state,
    status_live_empty,
    trim_fact_events,
    write_json_state,
)
from backend.core.fanxiu.data_annotation.scheduler_time import (
    normalize_time_sequence,
)

_RUNTIME_PHASE_LABELS = {
    "idle": "空闲",
    "waiting_context": "等待运行环境",
    "starting": "启动中",
    "stopped": "已停止",
    "behavior_tree_disabled": "已关闭",
}


def behavior_tree_runtime_phase_label(phase: Any) -> str:
    key = str(phase or "").strip()
    return _RUNTIME_PHASE_LABELS.get(key, key)


def behavior_tree_runtime_display_message(message: Any) -> str:
    text = str(message or "")
    if not text:
        return text
    for key, label in sorted(_RUNTIME_PHASE_LABELS.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", label, text)
    return text


def normalize_behavior_tree_runtime_display(status: dict[str, Any]) -> None:
    status["message"] = behavior_tree_runtime_display_message(status.get("message") or "")
    logs = status.get("logs")
    if isinstance(logs, list):
        status["logs"] = normalize_behavior_tree_runtime_logs_for_display([item for item in logs if isinstance(item, dict)])
    status.pop("framework_status", None)
    status.pop("engine_status", None)
    status.pop("framework_tick", None)
    status.pop("engine_tick", None)


def select_behavior_tree_runtime_status(
    live_status: dict[str, Any],
    persisted_status: dict[str, Any],
) -> dict[str, Any]:
    """Select the freshest snapshot shared by the backend and resident Kernel."""
    live = dict(live_status or {})
    persisted = dict(persisted_status or {})
    if not persisted:
        return live
    if is_behavior_tree_runtime_live_empty(live):
        return persisted
    try:
        live_updated_at = float(live.get("updated_at") or 0.0)
    except (TypeError, ValueError):
        live_updated_at = 0.0
    try:
        persisted_updated_at = float(persisted.get("updated_at") or 0.0)
    except (TypeError, ValueError):
        persisted_updated_at = 0.0
    return persisted if persisted_updated_at > live_updated_at else live






def normalize_behavior_tree_runtime_logs_for_display(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in logs:
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        next_item["message"] = behavior_tree_runtime_display_message(next_item.get("message") or "")
        normalized.append(next_item)
    return normalized


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
        "availability": {
            "game": {
                "active": False,
                "state": "available",
                "reason": "",
                "scene_id": None,
                "opened_at": None,
                "last_observed_at": None,
                "resolved_at": None,
                "evidence": {},
            },
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
        if key in {"runtime", "guard", "availability", "discoveries"} and isinstance(value, dict):
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


def _task_fact_updated_at(fact: Any) -> float:
    if not isinstance(fact, dict):
        return 0.0
    try:
        return float(fact.get("updated_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def _merge_existing_scheduler_task_facts(path: Path, facts: dict[str, Any]) -> None:
    existing = read_data_annotation_world_facts(path)
    existing_discoveries = existing.get("discoveries") if isinstance(existing.get("discoveries"), dict) else {}
    existing_tasks = existing_discoveries.get("task") if isinstance(existing_discoveries.get("task"), dict) else {}
    task_facts = ensure_mapping_bucket(facts, "discoveries", "task")
    for task_id, existing_fact in existing_tasks.items():
        if not isinstance(existing_fact, dict):
            continue
        incoming_fact = task_facts.get(task_id)
        if (
            not isinstance(incoming_fact, dict)
            or _task_fact_updated_at(existing_fact) > _task_fact_updated_at(incoming_fact)
        ):
            task_facts[task_id] = dict(existing_fact)

    # Bubble lifecycle is a transaction token, not disposable Runtime
    # telemetry. Preserve the newer side when a stale Runtime snapshot is
    # persisted after a restart/claim/hide fact.
    existing_bubble = existing_discoveries.get("bubble_lifecycle")
    incoming_bubble = (
        facts.get("discoveries", {}).get("bubble_lifecycle")
        if isinstance(facts.get("discoveries"), dict)
        else None
    )
    if isinstance(existing_bubble, dict) and (
        not isinstance(incoming_bubble, dict)
        or _task_fact_updated_at(existing_bubble) > _task_fact_updated_at(incoming_bubble)
    ):
        ensure_mapping_bucket(facts, "discoveries")["bubble_lifecycle"] = dict(existing_bubble)


def write_data_annotation_world_facts(
    path: Path,
    facts: dict[str, Any],
    *,
    preserve_existing_task_facts: bool = True,
    _lock_already_held: bool = False,
) -> None:
    def _write() -> None:
        if preserve_existing_task_facts:
            _merge_existing_scheduler_task_facts(path, facts)
        facts["version"] = 1
        facts["updated_at"] = time.time()
        trim_fact_events(facts)
        write_data_annotation_json(path, facts)

    if _lock_already_held:
        _write()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.with_name(f"{path.name}.lock")), timeout=30):
        _write()


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
    fact = {
        **existing_fact,
        "id": task_id,
        "task_type": str(task.get("task_type") or ""),
        "label": str(task.get("label") or task_id),
        "source": str(task.get("source") or ""),
        "trigger_description": str(task.get("trigger_description") or ""),
        "last_result": result,
        "last_run_at": task.get("last_run_at") if task.get("last_run_at") else None,
        "last_message": task.get("last_message") if task.get("last_message") else None,
        "finished_at": task.get("finished_at") if task.get("finished_at") else None,
        "updated_at": time.time(),
    }
    # World facts describe what was observed during a run.  The Job-owned
    # trigger belongs only to scheduler_tasks.json; also remove legacy mirrors
    # when this fact is refreshed so the two sources cannot drift again.
    fact.pop("next_time", None)
    task_facts[task_id] = fact
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


def persist_behavior_tree_runtime_status(
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
        "running": bool(status.get("running")),
        "message": status.get("message") or "",
        "updated_at": now,
    })

    guard = ensure_mapping_bucket(facts, "guard")
    last_guard_event = status.get("last_guard_event") if isinstance(status.get("last_guard_event"), dict) else {}
    previous_guard_event = guard.get("last_event") if isinstance(guard.get("last_event"), dict) else {}
    guard_event_changed = bool(last_guard_event) and last_guard_event != previous_guard_event
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
        if guard_event_changed:
            append_data_annotation_world_fact_event(facts, f"guard_{bucket_key}", last_guard_event)
    write_data_annotation_world_facts(world_facts_path, facts)


def read_behavior_tree_runtime_status(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return read_json_state_dict(path)


def initial_behavior_tree_runtime_status() -> dict[str, Any]:
    return {
        "ok": True,
        "running": False,
        "guard_group_enabled": True,
        "guard_enabled": False,
        "guard_running": False,
        "guard_entry_id": "",
        "guard_interval_seconds": 2.0,
        "guard_items": {},
        "device_health": {},
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
        "cell_logs": [],
    }


def is_behavior_tree_runtime_live_empty(status: dict[str, Any]) -> bool:
    return status_live_empty(status)


def append_behavior_tree_runtime_status_log(
    status: dict[str, Any],
    kind: str,
    message: str,
    *,
    scope: str = "",
    item_id: str = "",
    time_text: str | None = None,
    updated_at: float | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = append_status_log(
        status,
        kind,
        message,
        scope=scope,
        item_id=item_id,
        time_text=time_text,
        updated_at=updated_at,
    )
    if isinstance(extra, dict):
        item.update({str(key): value for key, value in extra.items() if value not in (None, "")})
    return item


def append_behavior_tree_runtime_log_once(
    status: dict[str, Any],
    kind: str,
    message: str,
    *,
    time_text: str | None = None,
) -> None:
    append_status_log_once(status, kind, message, time_text=time_text)


def normalize_behavior_tree_runtime_guard_items(
    status: dict[str, Any],
    guard_definitions: dict[str, dict[str, Any]],
) -> None:
    raw_guard_items = status.get("guard_items")
    guard_items = dict(raw_guard_items) if isinstance(raw_guard_items, dict) else {}
    for guard_id, definition in guard_definitions.items():
        default_enabled = bool(definition.get("default_enabled", definition.get("enabled", False)))
        raw_item = guard_items.get(guard_id)
        if not isinstance(raw_item, dict):
            guard_items[guard_id] = {"enabled": default_enabled}
            continue
        if "enabled" not in raw_item or (not bool(raw_item.get("enabled")) and not float(raw_item.get("updated_at") or 0)):
            guard_items[guard_id] = {**raw_item, "enabled": default_enabled}

    status["guard_enabled"] = False
    status.pop("close_popups_guard_config_version", None)
    guard_items.pop("close_popups", None)
    status["guard_items"] = normalize_guard_items(guard_definitions, guard_items)


def normalize_data_annotation_scheduler_task(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    task_id = str(item.get("id") or "").strip()
    task_type = str(item.get("task_type") or "").strip()
    if not task_id or not task_type:
        return None
    try:
        dispatch_level = min(5, max(0, int(item.get("dispatch_level") or 0)))
    except (TypeError, ValueError):
        dispatch_level = 0
    try:
        dispatch_order = min(9999, max(0, int(item.get("dispatch_order") or 0)))
    except (TypeError, ValueError):
        dispatch_order = 0
    try:
        raw_retry_delay = item.get("error_retry_delay_seconds")
        retry_delay = max(
            0,
            int(600 if raw_retry_delay in {None, ""} else raw_retry_delay),
        )
    except (TypeError, ValueError):
        retry_delay = 600
    label = str(item.get("label") or task_id)
    # Keep the historical serialized source value readable across upgrades;
    # the owning subsystem is named Behavior Tree Runtime in current code.
    source = str(item.get("source") or "data_annotation_runtime")
    scheduler_meta = (
        dict(item["scheduler_meta"])
        if isinstance(item.get("scheduler_meta"), dict)
        else None
    )
    if scheduler_meta is not None:
        # Legacy patrol implementations transported Job business facts through
        # Scheduler state.  Facts now stay in their owning Job domain; keep
        # normalization as the one-way migration that removes the old payload.
        scheduler_meta.pop("state_inspection", None)
        if not scheduler_meta:
            scheduler_meta = None
    task = {
        "id": task_id,
        "task_type": task_type,
        "label": label,
        "template_id": str(item.get("template_id") or task_type),
        "template_label": str(item.get("template_label") or label),
        "template_source": str(
            item.get("template_source")
            or ("custom" if source in {"ai", "custom", "debug_eval"} else "preset")
        ),
        "trigger_description": str(item.get("trigger_description") or ""),
        "source": source,
        "legacy_name": str(item.get("legacy_name") or label),
        "interruptible": bool(item.get("interruptible", True)),
        "dispatch_level": dispatch_level,
        "dispatch_order": dispatch_order,
        "next_time": str(item["next_time"]) if item.get("next_time") else None,
        "last_run_at": item.get("last_run_at"),
        "last_result": str(item.get("last_result") or ""),
        "last_message": str(item.get("last_message") or ""),
        "error_retry_delay_seconds": retry_delay,
        "payload": dict(item.get("payload") or {}) if isinstance(item.get("payload"), dict) else {},
        "scheduler_meta": scheduler_meta,
        "attempt_id": item.get("attempt_id"),
        "attempt_kernel_generation": item.get("attempt_kernel_generation"),
        "attempt_kernel_idle_since": item.get("attempt_kernel_idle_since"),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
    }
    if item.get("system_task"):
        task["system_task"] = True
    # Absence is meaningful for attempts started by an older process: callers
    # can then fall back to the current next_time.  A present ``None`` means a
    # new attempt intentionally started without a trigger and must restore it.
    if "attempt_original_trigger" in item:
        task["attempt_original_trigger"] = item.get("attempt_original_trigger")
    return task


def data_annotation_scheduler_task_state(task: dict[str, Any]) -> dict[str, Any]:
    return normalize_data_annotation_scheduler_task(task) or {}


def normalize_data_annotation_scheduler_settings(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    return {
        "job_group_enabled": bool(source.get("job_group_enabled", True)),
        "behavior_tree_enabled": bool(source.get("behavior_tree_enabled", True)),
        "time_sequence": normalize_time_sequence(source.get("time_sequence")),
        "updated_at": float(source.get("updated_at") or 0),
    }


def parse_data_annotation_task_time(value: Any) -> float | None:
    return parse_schedule_time(value)


def parse_data_annotation_daily_clock(value: Any) -> dt_time | None:
    return parse_daily_clock(value)


def data_annotation_task_due(task: dict[str, Any]) -> bool:
    next_ts = parse_schedule_time(task.get("next_time"))
    return bool(next_ts is not None and next_ts <= time.time())
