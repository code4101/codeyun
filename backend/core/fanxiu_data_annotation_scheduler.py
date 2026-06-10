from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pyxllib.prog import (
    build_scheduled_task_plan,
    first_valid_schedule_time_text,
    merge_scheduled_task_updates,
    schedule_kind_rank,
    schedule_task_due_timestamp,
    schedule_task_order_key,
    scheduled_task_plan_reason,
    scheduled_task_run_copy,
    sync_scheduled_tasks_from_facts,
)

from backend.core.fanxiu_data_annotation_state import (
    next_data_annotation_scheduler_time,
    normalize_data_annotation_scheduler_task,
)


TaskSupported = Callable[[dict[str, Any]], bool]
TaskDue = Callable[[dict[str, Any]], bool]


def data_annotation_fact_time_text(fact: dict[str, Any], *keys: str) -> str | None:
    return first_valid_schedule_time_text(fact, *keys)


def data_annotation_scheduler_group_rank(task: dict[str, Any]) -> int:
    return schedule_kind_rank(task)


def data_annotation_scheduler_due_timestamp(task: dict[str, Any]) -> float:
    return schedule_task_due_timestamp(task)


def data_annotation_scheduler_order_key(task: dict[str, Any]) -> tuple[int, float, str]:
    return schedule_task_order_key(task)


def sync_data_annotation_scheduler_tasks_from_world_facts(
    tasks: list[dict[str, Any]],
    facts: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
    task_facts = discoveries.get("task") if isinstance(discoveries.get("task"), dict) else {}
    if not isinstance(task_facts, dict) or not task_facts:
        return False
    sync_time = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return sync_scheduled_tasks_from_facts(
        tasks,
        task_facts,
        time_field_sources={
            "next_time": ("discovered_next_time", "next_time"),
            "retry_after": ("discovered_retry_after", "retry_after"),
            "last_run_at": ("last_run_at",),
        },
        text_field_sources={"last_result": ("last_result",)},
        synced_at_key="world_fact_synced_at",
        fact_updated_at_key="world_fact_updated_at",
        synced_at_text=sync_time,
    )


def merge_data_annotation_scheduler_task_updates(
    current_tasks: list[dict[str, Any]],
    incoming_tasks: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    return merge_scheduled_task_updates(
        current_tasks,
        incoming_tasks,
        normalizer=normalize_data_annotation_scheduler_task,
        next_time_resolver=lambda task, base_time: next_data_annotation_scheduler_time(task, base_time),
        base_time=now or datetime.now(),
    )


def repair_data_annotation_scheduler_tasks(
    raw: Any,
    default_tasks: list[dict[str, Any]],
    facts: dict[str, Any],
    *,
    task_supported: TaskSupported,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    source = raw if isinstance(raw, list) else default_tasks
    tasks = [task for item in source if (task := normalize_data_annotation_scheduler_task(item))]
    if not tasks:
        tasks = default_tasks
    obsolete_task_ids = {"gift-code-real-test", "gift-code-test-real", "real-test-gift-code", "mail-full-scan"}
    obsolete_task_labels = {"真实测试礼包码", "邮件_全量遍历"}
    before_cleanup_count = len(tasks)
    tasks = [
        task
        for task in tasks
        if str(task.get("id") or "") not in obsolete_task_ids
        and str(task.get("label") or "").strip() not in obsolete_task_labels
    ]
    changed = len(tasks) != before_cleanup_count
    defaults_by_id = {
        str(task.get("id") or ""): task
        for task in default_tasks
        if str(task.get("id") or "")
    }
    for task in tasks:
        default_task = defaults_by_id.get(str(task.get("id") or ""))
        if not default_task:
            continue
        previous_task_type = str(task.get("task_type") or "")
        default_task_type = str(default_task.get("task_type") or "")
        for key in ("task_type", "source", "schedule_kind", "legacy_name", "schedule_times", "window"):
            task[key] = default_task.get(key)
        default_payload = default_task.get("payload") if isinstance(default_task.get("payload"), dict) else {}
        task_payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        definition_marker = "__scheduler_definition_task_type"
        marker_matches = str(task_payload.get(definition_marker) or "") == default_task_type
        is_migrated_legacy_task = (
            previous_task_type in {"legacy_daily_task", "legacy_dynamic_task"}
            and default_task_type not in {"legacy_daily_task", "legacy_dynamic_task"}
        )
        if is_migrated_legacy_task and (
            previous_task_type != default_task_type or not marker_matches
        ):
            for key in ("label", "enabled", "interruptible", "cooldown_seconds"):
                task[key] = default_task.get(key)
            task_payload = {}
        task["payload"] = {**default_payload, **task_payload}
        task["payload"][definition_marker] = default_task_type
    by_id = {str(task.get("id") or ""): task for task in tasks}
    for default_task in defaults_by_id.values():
        task_id = str(default_task.get("id") or "")
        if task_id and task_id not in by_id:
            tasks.append(normalize_data_annotation_scheduler_task(default_task) or default_task)
            changed = True
    if sync_data_annotation_scheduler_tasks_from_world_facts(tasks, facts, now=now):
        changed = True
    current_time = now or datetime.now()
    for task in tasks:
        if str(task.get("schedule_kind") or "") == "manual" and task.get("enabled"):
            task["enabled"] = False
            changed = True
        if (
            task.get("enabled")
            and str(task.get("schedule_kind") or "") == "daily"
            and not task.get("next_time")
            and not task.get("retry_after")
        ):
            next_time = next_data_annotation_scheduler_time(task, current_time)
            if next_time:
                task["next_time"] = next_time
                changed = True
    for task in tasks:
        if not task_supported(task) and task.get("enabled"):
            task["enabled"] = False
            task["last_result"] = "unsupported"
            changed = True
    if raw != tasks:
        changed = True
    return tasks, changed


def data_annotation_scheduler_task_plan_reason(
    task: dict[str, Any],
    due: bool,
    *,
    task_supported: TaskSupported,
    now_ts: float | None = None,
) -> str:
    return scheduled_task_plan_reason(
        task,
        due,
        task_supported=task_supported,
        now=now_ts,
    )


def data_annotation_world_facts_summary(facts: dict[str, Any]) -> dict[str, Any]:
    discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
    runtime = facts.get("runtime") if isinstance(facts.get("runtime"), dict) else {}
    guard = facts.get("guard") if isinstance(facts.get("guard"), dict) else {}
    events = facts.get("events") if isinstance(facts.get("events"), list) else []
    return {
        "updated_at": facts.get("updated_at"),
        "current_scene": runtime.get("current_scene"),
        "runtime_status": runtime.get("status") or "",
        "runtime_task": runtime.get("current_task") or "",
        "guard_enabled": bool(guard.get("enabled")),
        "guard_running": bool(guard.get("running")),
        "scene_count": len(discoveries.get("scene") or {}) if isinstance(discoveries.get("scene"), dict) else 0,
        "popup_count": len(discoveries.get("popup") or {}) if isinstance(discoveries.get("popup"), dict) else 0,
        "occlusion_count": len(discoveries.get("occlusion") or {}) if isinstance(discoveries.get("occlusion"), dict) else 0,
        "task_fact_count": len(discoveries.get("task") or {}) if isinstance(discoveries.get("task"), dict) else 0,
        "last_events": [item for item in events[-5:] if isinstance(item, dict)],
    }


def build_data_annotation_scheduler_plan(
    tasks: list[dict[str, Any]],
    runtime: dict[str, Any],
    facts: dict[str, Any],
    scheduler_state_path: Path,
    *,
    task_supported: TaskSupported,
    task_due: TaskDue,
    now_ts: float | None = None,
) -> dict[str, Any]:
    discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
    task_facts = discoveries.get("task") if isinstance(discoveries.get("task"), dict) else {}
    runtime_running = bool(runtime.get("running"))
    current_ts = time.time() if now_ts is None else now_ts
    plan = build_scheduled_task_plan(
        tasks,
        runtime_running=runtime_running,
        runtime_task=str(runtime.get("current_task") or runtime.get("task_type") or ""),
        task_supported=task_supported,
        task_due=task_due,
        task_facts=task_facts,
        now=current_ts,
    )
    return {
        "next_action": plan["next_action"],
        "message": plan["message"],
        "runtime": {
            "running": runtime_running,
            "status": runtime.get("status") or "",
            "current_task": runtime.get("current_task") or "",
            "current_task_id": runtime.get("current_task_id") or "",
            "task_type": runtime.get("task_type") or "",
            "phase": runtime.get("phase") or "",
            "current_scene": runtime.get("current_scene"),
            "interruptible": bool(runtime.get("interruptible", True)),
        },
        "facts_summary": data_annotation_world_facts_summary(facts),
        "due_tasks": plan["due_tasks"],
        "tasks": plan["tasks"],
        "path": str(scheduler_state_path),
    }


def data_annotation_scheduler_run_now_task(
    tasks: list[dict[str, Any]],
    task_id: str,
    payload_override: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return scheduled_task_run_copy(tasks, task_id, payload_override)
