from __future__ import annotations

import re
import time
from datetime import datetime, time as dt_time, timedelta
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

_RUNTIME_PHASE_LABELS = {
    "service_owned_by_other": "由后端服务接管",
    "idle": "空闲",
    "idle_tick": "空闲",
    "idle_guard": "空闲巡检中",
    "idle_guard_done": "空闲巡检完成",
    "manual_job_poll": "检查 task cell 队列",
    "scheduler_poll": "检查定时作业",
    "scheduler_isolated": "执行定时作业",
    "waiting_context": "等待运行环境",
    "starting": "启动中",
    "stopped": "已停止",
    "behavior_tree_disabled": "已关闭",
}


def data_annotation_runtime_phase_label(phase: Any) -> str:
    key = str(phase or "").strip()
    return _RUNTIME_PHASE_LABELS.get(key, key)


def data_annotation_runtime_owner_message(pid: Any, step: Any = "") -> str:
    pid_text = str(pid or "").strip()
    step_label = data_annotation_runtime_phase_label(step)
    suffix = f"，{step_label}" if step_label and step_label not in {"unknown", "空闲巡检完成"} else ""
    return f"后台服务正在运行（进程 {pid_text}）{suffix}" if pid_text else f"后台服务正在运行{suffix}"


def data_annotation_runtime_display_message(message: Any) -> str:
    text = str(message or "")
    if not text:
        return text
    owner_match = re.search(r"行为树执行器已由后端进程\s+(\d+)\s+持有[：:]?\s*([A-Za-z0-9_\\-]*)", text)
    if owner_match:
        return data_annotation_runtime_owner_message(owner_match.group(1), owner_match.group(2))
    service_match = re.search(r"行为树常驻服务运行中[：:]?\s*进程\s+(\d+)\s+([A-Za-z0-9_\\-]+)", text)
    if service_match:
        return data_annotation_runtime_owner_message(service_match.group(1), service_match.group(2))
    stale_owner_match = re.search(r"owner\s+进程不是凡修常驻服务[：:]pid=(\d+)", text)
    if stale_owner_match:
        return f"原后台服务已失效（进程 {stale_owner_match.group(1)}）"
    for key, label in sorted(_RUNTIME_PHASE_LABELS.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", label, text)
    return text


def normalize_data_annotation_runtime_display(status: dict[str, Any]) -> None:
    status["message"] = data_annotation_runtime_display_message(status.get("message") or "")
    logs = status.get("logs")
    if isinstance(logs, list):
        status["logs"] = normalize_data_annotation_runtime_logs_for_display([item for item in logs if isinstance(item, dict)])
    if not isinstance(status.get("cell_tick"), dict):
        old_tick = status.get("engine_tick") if isinstance(status.get("engine_tick"), dict) else status.get("framework_tick")
        if isinstance(old_tick, dict):
            status["cell_tick"] = dict(old_tick)
    status.pop("framework_status", None)
    status.pop("engine_status", None)
    status.pop("framework_tick", None)
    status.pop("engine_tick", None)
    status.update(data_annotation_runtime_layer_status(status))


def _runtime_state_label(status: dict[str, Any]) -> str:
    if str(status.get("phase") or "") == "service_owned_by_other":
        return "后台接管"
    if bool(status.get("running")):
        return "执行中"
    if str(status.get("status") or "") == "stopping":
        return "中断中"
    if bool(status.get("service_running")):
        return "就绪"
    return "未运行"


def data_annotation_runtime_layer_status(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scene = status.get("current_scene")
    current_scene = f"#{scene}" if isinstance(scene, int) else ""
    phase = str(status.get("phase") or "")
    task_type = str(status.get("task_type") or "")
    current_task = str(status.get("current_task") or task_type or "")
    total = int(status.get("total") or 0)
    current_index = int(status.get("current_index") or 0)
    progress = f"{current_index}/{total}" if total else ""
    guard_items = status.get("guard_items") if isinstance(status.get("guard_items"), dict) else {}
    guard_enabled_count = sum(1 for item in guard_items.values() if isinstance(item, dict) and bool(item.get("enabled")))
    service_owned_by_other = phase == "service_owned_by_other"
    scheduler_enabled = bool(status.get("behavior_tree_enabled", True)) and bool(status.get("job_group_enabled", True))
    cell_status = {
        "label": "执行中" if bool(status.get("running")) else "空闲",
        "phase": data_annotation_runtime_phase_label(phase) if phase else "",
        "task_type": task_type,
        "current_task": current_task,
        "current_task_id": str(status.get("current_task_id") or ""),
        "progress": progress,
        "interruptible": bool(status.get("interruptible", True)),
    }
    return {
        "kernel_status": {
            "label": _runtime_state_label(status),
            "enabled": True,
            "running": bool(status.get("service_running")) or service_owned_by_other,
            "busy": bool(status.get("running")),
            "current_scene": current_scene,
            "message": status.get("message") or "",
            "can_restart": True,
            "can_interrupt": bool(status.get("running")),
        },
        "cell_status": cell_status,
        "scheduler_status": {
            "label": "运行中" if scheduler_enabled else "已暂停",
            "enabled": scheduler_enabled,
            "service_running": bool(status.get("service_running")),
            "job_group_enabled": scheduler_enabled,
            "guard_group_enabled": bool(status.get("guard_group_enabled", True)),
        },
        "orchestration_status": {
            "entry_id": str(status.get("entry_id") or ""),
            "guard_count": len(guard_items),
            "guard_enabled_count": guard_enabled_count,
            "guard_interval_seconds": float(status.get("guard_interval_seconds") or 0),
        },
    }


def normalize_data_annotation_runtime_logs_for_display(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in logs:
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        next_item["message"] = data_annotation_runtime_display_message(next_item.get("message") or "")
        if str(next_item.get("kind") or "") == "info" and str(next_item.get("message") or "").startswith("后台服务正在运行"):
            continue
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
    if not existing_tasks:
        return
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


def write_data_annotation_world_facts(
    path: Path,
    facts: dict[str, Any],
    *,
    preserve_existing_task_facts: bool = True,
) -> None:
    if preserve_existing_task_facts:
        _merge_existing_scheduler_task_facts(path, facts)
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
    fact = {
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
    if result == "success":
        fact.pop("discovered_retry_after", None)
        fact["retry_after"] = None
        if task.get("next_time"):
            fact["discovered_next_time"] = task.get("next_time")
    elif result in {"error", "stopped", "skipped", "unsupported"}:
        fact.pop("discovered_next_time", None)
        fact["next_time"] = None
        if task.get("retry_after"):
            fact["discovered_retry_after"] = task.get("retry_after")
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


CLOSE_POPUPS_GUARD_CONFIG_VERSION = 2


def close_popups_guard_enabled_from_status(status: dict[str, Any]) -> bool:
    if int(status.get("close_popups_guard_config_version") or 0) >= CLOSE_POPUPS_GUARD_CONFIG_VERSION:
        return bool(status.get("guard_enabled", True))
    return True


def initial_data_annotation_runtime_status() -> dict[str, Any]:
    return {
        "ok": True,
        "service_running": False,
        "running": False,
        "guard_group_enabled": True,
        "guard_enabled": True,
        "close_popups_guard_config_version": CLOSE_POPUPS_GUARD_CONFIG_VERSION,
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
    raw_guard_items = status.get("guard_items")
    guard_items = dict(raw_guard_items) if isinstance(raw_guard_items, dict) else {}
    for guard_id, definition in guard_definitions.items():
        if guard_id == "close_popups":
            continue
        default_enabled = bool(definition.get("default_enabled", definition.get("enabled", False)))
        raw_item = guard_items.get(guard_id)
        if not isinstance(raw_item, dict):
            guard_items[guard_id] = {"enabled": default_enabled}
            continue
        if "enabled" not in raw_item or (not bool(raw_item.get("enabled")) and not float(raw_item.get("updated_at") or 0)):
            guard_items[guard_id] = {**raw_item, "enabled": default_enabled}

    close_popups_enabled = close_popups_guard_enabled_from_status(status)
    status["guard_enabled"] = close_popups_enabled
    status["close_popups_guard_config_version"] = CLOSE_POPUPS_GUARD_CONFIG_VERSION
    close_popups_override: dict[str, Any] = {
        "enabled": close_popups_enabled,
        "running": bool(status.get("guard_group_enabled", True) and status.get("guard_running")),
        "entry_id": str(status.get("guard_entry_id") or ""),
    }
    last_guard_event = status.get("last_guard_event")
    if isinstance(last_guard_event, dict) and last_guard_event.get("title"):
        close_popups_override["message"] = str(last_guard_event.get("title") or "")
    status["guard_items"] = normalize_guard_items(
        guard_definitions,
        guard_items,
        overrides={"close_popups": close_popups_override},
    )


def normalize_data_annotation_scheduler_task(item: Any) -> dict[str, Any] | None:
    task = normalize_scheduled_task_record(item, default_source="data_annotation_runtime", default_schedule_kind="manual")
    if task is None:
        return None
    template_id = str(task.get("template_id") or task.get("task_type") or "").strip()
    template_label = str(task.get("template_label") or task.get("label") or template_id).strip()
    source = str(task.get("source") or "").strip()
    template_source = str(task.get("template_source") or "").strip()
    if not template_source:
        template_source = "custom" if source in {"ai", "custom", "debug_eval"} else "preset"
    task["template_id"] = template_id
    task["template_label"] = template_label
    task["template_source"] = template_source
    task["trigger_kind"] = str(task.get("trigger_kind") or task.get("schedule_kind") or "manual").strip() or "manual"
    weekdays = item.get("weekdays") if isinstance(item, dict) else None
    if isinstance(weekdays, list):
        parsed_weekdays: list[int] = []
        for value in weekdays:
            try:
                weekday = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= weekday <= 6 and weekday not in parsed_weekdays:
                parsed_weekdays.append(weekday)
        task["weekdays"] = parsed_weekdays
    return task


def data_annotation_scheduler_task_state(task: dict[str, Any]) -> dict[str, Any]:
    return scheduled_task_state(task)


def normalize_data_annotation_scheduler_settings(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    return {
        "job_group_enabled": bool(source.get("job_group_enabled", True)),
        "behavior_tree_enabled": bool(source.get("behavior_tree_enabled", True)),
        "updated_at": float(source.get("updated_at") or 0),
    }


def normalize_data_annotation_manual_job(item: Any) -> dict[str, Any] | None:
    return normalize_job_record(item, default_group="manual_job")


def parse_data_annotation_task_time(value: Any) -> float | None:
    return parse_schedule_time(value)


def parse_data_annotation_daily_clock(value: Any) -> dt_time | None:
    return parse_daily_clock(value)


def next_data_annotation_scheduler_time(task: dict[str, Any], now: datetime | None = None) -> str | None:
    schedule_kind = str(task.get("schedule_kind") or "")
    if schedule_kind == "daily":
        return next_daily_time(task.get("schedule_times", []), base_time=now)
    if schedule_kind == "weekly":
        return next_weekly_time(task.get("weekdays", []), task.get("schedule_times", []), base_time=now)
    return None


def next_weekly_time(weekdays: Any, times: Any, *, base_time: datetime | None = None) -> str | None:
    parsed_weekdays = []
    if isinstance(weekdays, list):
        for value in weekdays:
            try:
                weekday = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= weekday <= 6:
                parsed_weekdays.append(weekday)
    clocks = [clock for value in (times if isinstance(times, list) else []) if (clock := parse_daily_clock(value)) is not None]
    if not parsed_weekdays or not clocks:
        return None
    base = base_time or datetime.now()
    candidates: list[datetime] = []
    for day_offset in range(8):
        current_date = base.date() + timedelta(days=day_offset)
        if current_date.weekday() not in parsed_weekdays:
            continue
        for clock in clocks:
            candidate = datetime.combine(current_date, clock)
            if candidate > base:
                candidates.append(candidate)
    if not candidates:
        return None
    return min(candidates).strftime("%Y-%m-%d %H:%M:%S")


def data_annotation_task_due(task: dict[str, Any]) -> bool:
    if str(task.get("last_result") or "") == "success":
        next_ts = parse_schedule_time(task.get("next_time"))
        if next_ts is not None and next_ts > time.time():
            return False
    return schedule_task_due(task, now=time.time())
