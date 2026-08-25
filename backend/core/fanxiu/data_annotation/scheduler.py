from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from pyxllib.prog import (
    scheduled_task_run_copy,
)

from backend.core.fanxiu.data_annotation.state import (
    normalize_data_annotation_scheduler_task,
    parse_data_annotation_task_time,
)
TaskSupported = Callable[[dict[str, Any]], bool]
TaskDue = Callable[[dict[str, Any]], bool]


def set_scheduler_task_trigger_time(
    tasks: list[dict[str, Any]],
    task_name: str,
    trigger_time: datetime | str | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Set or clear one task's sole trigger timestamp.

    This command does not interpret business outcomes. The caller may target
    the current task or any other task.
    """

    selector = str(task_name or "").strip()
    if not selector:
        raise ValueError("作业名称不能为空")
    matches = [
        task
        for task in tasks
        if selector
        in {
            str(task.get("id") or "").strip(),
            str(task.get("task_type") or "").strip(),
            str(task.get("label") or "").strip(),
        }
    ]
    if not matches:
        raise LookupError(f"未找到 Scheduler 作业：{selector}")
    if len(matches) > 1:
        raise ValueError(f"Scheduler 作业名称不唯一：{selector}")

    current = now or datetime.now()
    if trigger_time is None:
        resolved = None
    elif isinstance(trigger_time, datetime):
        resolved = trigger_time
    else:
        text = str(trigger_time or "").strip()
        resolved = None
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                resolved = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        if resolved is None:
            try:
                clock = datetime.strptime(text, "%H:%M").time()
            except ValueError as exc:
                raise ValueError(f"无效触发时间：{text}") from exc
            resolved = datetime.combine(current.date(), clock)
            if resolved < current:
                resolved += timedelta(days=1)

    task = matches[0]
    task["next_time"] = resolved.strftime("%Y-%m-%d %H:%M:%S") if resolved is not None else None
    return task

SCHEDULER_RUNTIME_STATE_FIELDS = (
    "last_run_at",
    "last_result",
    "last_message",
    "next_time",
    "scheduler_meta",
    "attempt_id",
    "attempt_original_trigger",
    "attempt_kernel_generation",
    "attempt_kernel_idle_since",
    "queued_at",
    "started_at",
    "finished_at",
    "world_fact_synced_at",
    "world_fact_updated_at",
)
_SCHEDULER_RUNTIME_STATE_FIELDS = SCHEDULER_RUNTIME_STATE_FIELDS


def _scheduler_task_has_runtime_state(task: dict[str, Any]) -> bool:
    for key in _SCHEDULER_RUNTIME_STATE_FIELDS:
        value = task.get(key)
        if value is None or value == "" or value == {}:
            continue
        return True
    return False


def preserve_data_annotation_scheduler_runtime_state(
    incoming_tasks: list[dict[str, Any]],
    existing_tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_by_id = {
        str(task.get("id") or ""): task
        for task in existing_tasks
        if isinstance(task, dict) and str(task.get("id") or "")
    }
    if not existing_by_id:
        return incoming_tasks
    preserved: list[dict[str, Any]] = []
    for task in incoming_tasks:
        if not isinstance(task, dict):
            preserved.append(task)
            continue
        existing = existing_by_id.get(str(task.get("id") or ""))
        if not isinstance(existing, dict) or not _scheduler_task_has_runtime_state(existing):
            preserved.append(task)
            continue
        incoming_last_run_at = parse_data_annotation_task_time(task.get("last_run_at"))
        existing_last_run_at = parse_data_annotation_task_time(existing.get("last_run_at"))
        incoming_has_runtime = _scheduler_task_has_runtime_state(task)
        existing_is_newer = (
            existing_last_run_at is not None
            and (incoming_last_run_at is None or existing_last_run_at > incoming_last_run_at)
        )
        incoming_is_default_backfill = not incoming_has_runtime or (
            not task.get("last_run_at")
            and not task.get("last_result")
            and existing_last_run_at is not None
        )
        if not incoming_is_default_backfill and not existing_is_newer:
            preserved.append(task)
            continue
        merged = dict(task)
        for key in _SCHEDULER_RUNTIME_STATE_FIELDS:
            value = existing.get(key)
            if value is not None and value != "" and value != {}:
                merged[key] = value
        preserved.append(merged)
    return preserved


def data_annotation_scheduler_due_timestamp(task: dict[str, Any]) -> float:
    return parse_data_annotation_task_time(task.get("next_time")) or 0.0


def data_annotation_scheduler_dispatch_level(task: dict[str, Any]) -> int:
    try:
        return max(0, min(5, int(task.get("dispatch_level") or 0)))
    except (TypeError, ValueError):
        return 0


def data_annotation_scheduler_dispatch_order(task: dict[str, Any]) -> int:
    try:
        return max(0, min(9999, int(task.get("dispatch_order") or 0)))
    except (TypeError, ValueError):
        return 0


def data_annotation_scheduler_dispatch_sort_key(task: dict[str, Any]) -> tuple[int, float, int, str]:
    due_ts = data_annotation_scheduler_due_timestamp(task)
    order = data_annotation_scheduler_dispatch_order(task)
    return (
        -data_annotation_scheduler_dispatch_level(task),
        due_ts,
        order if order > 0 else 10000,
        str(task.get("id") or ""),
    )


def data_annotation_scheduler_order_key(task: dict[str, Any]) -> tuple[int, float, str]:
    due_ts = data_annotation_scheduler_due_timestamp(task)
    return (
        0 if due_ts > 0 else 1,
        due_ts if due_ts > 0 else float("inf"),
        str(task.get("id") or ""),
    )


def data_annotation_scheduler_time_order_key(task: dict[str, Any]) -> tuple[int, float, str]:
    due_ts = data_annotation_scheduler_due_timestamp(task)
    return (
        0 if due_ts > 0 else 1,
        due_ts if due_ts > 0 else float("inf"),
        str(task.get("id") or ""),
    )


def merge_data_annotation_scheduler_task_updates(
    current_tasks: list[dict[str, Any]],
    incoming_tasks: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Apply id-addressed upserts; omission is never an implicit deletion."""
    incoming_by_id = {
        str(task.get("id") or ""): task
        for task in incoming_tasks
        if isinstance(task, dict) and str(task.get("id") or "")
    }
    # This JSON is live shared state.  A stale browser snapshot may have the
    # same length as the current list while containing a different id set.
    # Length-based replacement would silently erase omitted jobs.  Deletion
    # must use a future explicit action; this endpoint only updates/adds ids.
    merged_input: list[dict[str, Any]] = []
    seen: set[str] = set()
    for current in current_tasks:
        task_id = str(current.get("id") or "")
        if not task_id:
            continue
        seen.add(task_id)
        incoming = incoming_by_id.get(task_id)
        # Callers frequently update only runtime/schedule facts for one task.
        # Normalizing that sparse record as a complete definition silently
        # resets omitted user configuration (notably dispatch_level) to its
        # default.  Upsert means field-wise patch for an existing id.
        merged_input.append({**current, **incoming} if incoming is not None else current)
    for task_id, incoming in incoming_by_id.items():
        if task_id not in seen:
            merged_input.append(incoming)
    return [
        task
        for item in merged_input
        if (task := normalize_data_annotation_scheduler_task(item)) is not None
    ]


def repair_data_annotation_scheduler_tasks(
    raw: Any,
    default_tasks: list[dict[str, Any]],
    facts: dict[str, Any],
    *,
    task_supported: TaskSupported,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    current = now or datetime.now()
    discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
    task_facts = discoveries.get("task") if isinstance(discoveries.get("task"), dict) else {}
    raw_by_id = {
        str(item.get("id") or ""): item
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    existing = {
        str(task.get("id") or ""): task
        for item in (raw if isinstance(raw, list) else [])
        if (task := normalize_data_annotation_scheduler_task(item)) is not None
    }
    defaults = [
        task
        for item in default_tasks
        if (task := normalize_data_annotation_scheduler_task(item)) is not None
    ]
    tasks: list[dict[str, Any]] = []
    for default in defaults:
        task_id = str(default["id"])
        previous = existing.pop(task_id, None)
        if previous is None:
            recovered = dict(default)
            fact = task_facts.get(task_id) if isinstance(task_facts.get(task_id), dict) else None
            if fact is not None:
                result = str(fact.get("last_result") or "").strip()
                if result:
                    recovered["last_run_at"] = fact.get("last_run_at") or None
                    recovered["last_result"] = result
                    recovered["last_message"] = fact.get("last_message") or None
                    recovered["finished_at"] = fact.get("finished_at") or None
                    fact_next_time = str(fact.get("next_time") or "").strip()
                    if fact_next_time:
                        recovered["next_time"] = fact_next_time
                    elif result in {"error", "running", "interrupted"}:
                        # A missing standard record must not turn a known
                        # failed/incomplete run into a fresh future schedule.
                        # Re-queue it now so the durable failure fact remains
                        # visible and engineering mode can catch it up.
                        recovered["next_time"] = current.strftime("%Y-%m-%d %H:%M:%S")
                    if result == "running":
                        recovered["last_result"] = "error"
                        recovered["last_message"] = (
                            "上次运行记录中断，Scheduler 作业记录缺失后已从 world_facts 恢复"
                        )
                        recovered["next_time"] = current.strftime("%Y-%m-%d %H:%M:%S")
            tasks.append(recovered)
            continue
        raw_previous = raw_by_id.get(task_id, {})
        tasks.append({
            **default,
            **{
                key: previous.get(key)
                for key in _SCHEDULER_RUNTIME_STATE_FIELDS
                if key in raw_previous
            },
            "dispatch_level": (
                previous["dispatch_level"]
                if "dispatch_level" in raw_previous
                else default["dispatch_level"]
            ),
            "dispatch_order": (
                previous["dispatch_order"]
                if "dispatch_order" in raw_previous
                else default["dispatch_order"]
            ),
            "error_retry_delay_seconds": (
                previous["error_retry_delay_seconds"]
                if "error_retry_delay_seconds" in raw_previous
                else default["error_retry_delay_seconds"]
            ),
            "payload": {
                **default.get("payload", {}),
                **previous.get("payload", {}),
            },
        })
    tasks.extend(existing.values())
    return tasks, raw != tasks


def data_annotation_scheduler_task_plan_reason(
    task: dict[str, Any],
    due: bool,
    *,
    task_supported: TaskSupported,
    now_ts: float | None = None,
) -> str:
    if not task_supported(task):
        return "尚未纳入当前框架验收"
    next_time = task.get("next_time")
    if not next_time:
        return "未设置触发时间"
    if due:
        return "已到期"
    return f"未到时间：{next_time}"


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
    daily_audit = discoveries.get("daily_audit") if isinstance(discoveries.get("daily_audit"), dict) else {}
    runtime_running = bool(runtime.get("running"))
    current_ts = time.time() if now_ts is None else now_ts

    plan_items: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        due = bool(task_due(task))
        supported = bool(task_supported(task))
        plan_items.append({
            **task,
            "due": due,
            "runnable": due and supported and not runtime_running,
            "supported": supported,
            "reason": data_annotation_scheduler_task_plan_reason(
                task,
                due,
                task_supported=task_supported,
                now_ts=current_ts,
            ),
            "fact": (
                task_facts.get(task_id)
                if isinstance(task_facts.get(task_id), dict)
                else {}
            ),
        })
    plan_items.sort(
        key=lambda item: (
            not bool(item["due"]),
            data_annotation_scheduler_dispatch_sort_key(item),
        )
    )
    due_tasks = [item for item in plan_items if item["due"]]
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
        "daily_audit": daily_audit,
        "due_tasks": due_tasks,
        "tasks": plan_items,
        "path": str(scheduler_state_path),
    }


def data_annotation_scheduler_run_now_task(
    tasks: list[dict[str, Any]],
    task_id: str,
    payload_override: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return scheduled_task_run_copy(tasks, task_id, payload_override)

