from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backend.core.fanxiu_data_annotation_state import (
    next_data_annotation_scheduler_time,
    normalize_data_annotation_scheduler_task,
    parse_data_annotation_task_time,
)


TaskSupported = Callable[[dict[str, Any]], bool]
TaskDue = Callable[[dict[str, Any]], bool]


def data_annotation_fact_time_text(fact: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = fact.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and parse_data_annotation_task_time(text) is not None:
            return text
    return None


def data_annotation_scheduler_group_rank(task: dict[str, Any]) -> int:
    schedule_kind = str(task.get("schedule_kind") or "").strip()
    return {
        "daily": 10,
        "dynamic": 20,
        "manual": 30,
    }.get(schedule_kind, 90)


def data_annotation_scheduler_due_timestamp(task: dict[str, Any]) -> float:
    retry_at = parse_data_annotation_task_time(task.get("retry_after"))
    if retry_at is not None:
        return retry_at
    next_at = parse_data_annotation_task_time(task.get("next_time"))
    if next_at is not None:
        return next_at
    clocks = [
        value
        for value in task.get("schedule_times", [])
        if str(value or "").strip()
    ] if isinstance(task.get("schedule_times"), list) else []
    if clocks:
        parsed = sorted(str(value) for value in clocks)
        return parse_data_annotation_task_time(f"1970-01-01 {parsed[0]}") or 0.0
    return 0.0


def data_annotation_scheduler_order_key(task: dict[str, Any]) -> tuple[int, float, str]:
    return (
        data_annotation_scheduler_group_rank(task),
        data_annotation_scheduler_due_timestamp(task),
        str(task.get("id") or ""),
    )


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
    changed = False
    sync_time = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    for task in tasks:
        task_id = str(task.get("id") or "")
        fact = task_facts.get(task_id)
        if not isinstance(fact, dict):
            continue
        task_changed = False
        next_time = data_annotation_fact_time_text(fact, "discovered_next_time", "next_time")
        if next_time and task.get("next_time") != next_time:
            task["next_time"] = next_time
            task_changed = True
            changed = True
        retry_after = data_annotation_fact_time_text(fact, "discovered_retry_after", "retry_after")
        if retry_after and task.get("retry_after") != retry_after:
            task["retry_after"] = retry_after
            task_changed = True
            changed = True
        last_run_at = data_annotation_fact_time_text(fact, "last_run_at")
        if last_run_at and task.get("last_run_at") != last_run_at:
            task["last_run_at"] = last_run_at
            task_changed = True
            changed = True
        last_result = str(fact.get("last_result") or "").strip()
        if last_result and str(task.get("last_result") or "") != last_result:
            task["last_result"] = last_result
            task_changed = True
            changed = True
        if task_changed:
            checkpoint = task.get("checkpoint") if isinstance(task.get("checkpoint"), dict) else {}
            checkpoint["world_fact_synced_at"] = sync_time
            checkpoint["world_fact_updated_at"] = fact.get("updated_at")
            task["checkpoint"] = checkpoint
    return changed


def merge_data_annotation_scheduler_task_updates(
    current_tasks: list[dict[str, Any]],
    incoming_tasks: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current_by_id = {str(task.get("id") or ""): task for task in current_tasks if str(task.get("id") or "")}
    merged: list[dict[str, Any]] = []
    runtime_keys = {"last_run_at", "last_result", "retry_after", "next_time", "checkpoint"}
    current_time = now or datetime.now()
    for incoming in incoming_tasks:
        normalized = normalize_data_annotation_scheduler_task(incoming)
        if normalized is None:
            continue
        current = current_by_id.get(str(normalized.get("id") or ""))
        if current is None:
            merged.append(normalized)
            continue
        was_enabled = bool(current.get("enabled"))
        task = {**normalized}
        for key in runtime_keys:
            task[key] = current.get(key)
        if bool(task.get("enabled")) and not was_enabled and not task.get("retry_after"):
            task["next_time"] = next_data_annotation_scheduler_time(task, current_time)
        elif not bool(task.get("enabled")):
            task["retry_after"] = None
            task["next_time"] = None
        merged.append(task)
    return merged


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
    if not task.get("enabled"):
        return "未启用"
    current_ts = time.time() if now_ts is None else now_ts
    retry_at = parse_data_annotation_task_time(task.get("retry_after"))
    next_at = parse_data_annotation_task_time(task.get("next_time"))
    if retry_at is not None and retry_at > current_ts:
        return f"等待重试：{task.get('retry_after')}"
    if next_at is not None and next_at > current_ts:
        return f"未到时间：{task.get('next_time')}"
    if not task_supported(task):
        return "尚未纳入当前框架验收"
    if due:
        return "已到期"
    return "可手动执行"


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
    plan_items: list[dict[str, Any]] = []
    current_ts = time.time() if now_ts is None else now_ts
    for task in tasks:
        task_id = str(task.get("id") or "")
        due = task_due(task)
        task_type = str(task.get("task_type") or "")
        unsupported = not task_supported(task)
        runnable = bool(task.get("enabled")) and due and not unsupported
        if runtime_running:
            runnable = False
        item = {
            "id": task_id,
            "task_type": task_type,
            "label": str(task.get("label") or task_id),
            "supported": not unsupported,
            "enabled": bool(task.get("enabled")),
            "due": due,
            "runnable": runnable,
            "reason": data_annotation_scheduler_task_plan_reason(
                task,
                due,
                task_supported=task_supported,
                now_ts=current_ts,
            ),
            "next_time": task.get("next_time") if task.get("next_time") else None,
            "retry_after": task.get("retry_after") if task.get("retry_after") else None,
            "last_result": str(task.get("last_result") or ""),
            "fact": task_facts.get(task_id) if isinstance(task_facts.get(task_id), dict) else {},
        }
        plan_items.append(item)
    plan_items.sort(
        key=lambda item: (
            not item["due"],
            data_annotation_scheduler_order_key(item),
        )
    )
    due_tasks = [item for item in plan_items if item["due"] and item["enabled"]]
    runnable_tasks = [item for item in due_tasks if item["runnable"]]
    if runtime_running:
        next_action = "wait"
        message = f"Runtime 正在运行：{runtime.get('current_task') or runtime.get('task_type') or '任务'}"
    elif runnable_tasks:
        next_action = "run_due"
        message = f"建议执行到期任务：{runnable_tasks[0]['label']}"
    elif due_tasks:
        next_action = "blocked"
        message = "存在到期任务，但当前均不可执行"
    else:
        next_action = "idle"
        message = "没有到期任务"
    return {
        "next_action": next_action,
        "message": message,
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
        "due_tasks": due_tasks,
        "tasks": plan_items,
        "path": str(scheduler_state_path),
    }


def data_annotation_scheduler_run_now_task(
    tasks: list[dict[str, Any]],
    task_id: str,
    payload_override: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        return None
    override = payload_override if isinstance(payload_override, dict) else {}
    if not override:
        return task
    original_payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    return {**task, "payload": {**original_payload, **override}}
