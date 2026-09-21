from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from pyxllib.prog import (
    scheduled_task_run_copy,
)

from backend.core.fanxiu.data_annotation.state import (
    normalize_kernel_scheduler_task,
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

SCHEDULER_EXECUTION_STATE_FIELDS = (
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
_SCHEDULER_EXECUTION_STATE_FIELDS = SCHEDULER_EXECUTION_STATE_FIELDS


def _scheduler_task_has_execution_state(task: dict[str, Any]) -> bool:
    for key in _SCHEDULER_EXECUTION_STATE_FIELDS:
        value = task.get(key)
        if value is None or value == "" or value == {}:
            continue
        return True
    return False


def preserve_kernel_scheduler_execution_state(
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
        if not isinstance(existing, dict) or not _scheduler_task_has_execution_state(existing):
            preserved.append(task)
            continue
        incoming_last_run_at = parse_data_annotation_task_time(task.get("last_run_at"))
        existing_last_run_at = parse_data_annotation_task_time(existing.get("last_run_at"))
        incoming_has_execution_state = _scheduler_task_has_execution_state(task)
        existing_is_newer = (
            existing_last_run_at is not None
            and (incoming_last_run_at is None or existing_last_run_at > incoming_last_run_at)
        )
        incoming_is_default_backfill = not incoming_has_execution_state or (
            not task.get("last_run_at")
            and not task.get("last_result")
            and existing_last_run_at is not None
        )
        if not incoming_is_default_backfill and not existing_is_newer:
            preserved.append(task)
            continue
        merged = dict(task)
        for key in _SCHEDULER_EXECUTION_STATE_FIELDS:
            value = existing.get(key)
            if value is not None and value != "" and value != {}:
                merged[key] = value
        preserved.append(merged)
    return preserved


def kernel_scheduler_due_timestamp(task: dict[str, Any]) -> float:
    return parse_data_annotation_task_time(task.get("next_time")) or 0.0


def kernel_scheduler_dispatch_level(task: dict[str, Any]) -> int:
    try:
        return max(0, min(5, int(task.get("dispatch_level") or 0)))
    except (TypeError, ValueError):
        return 0


def kernel_scheduler_dispatch_order(task: dict[str, Any]) -> int:
    try:
        return max(0, min(9999, int(task.get("dispatch_order") or 0)))
    except (TypeError, ValueError):
        return 0


def kernel_scheduler_dispatch_sort_key(task: dict[str, Any]) -> tuple[int, float, int, str]:
    due_ts = kernel_scheduler_due_timestamp(task)
    order = kernel_scheduler_dispatch_order(task)
    return (
        -kernel_scheduler_dispatch_level(task),
        due_ts,
        order if order > 0 else 10000,
        str(task.get("id") or ""),
    )


def kernel_scheduler_order_key(task: dict[str, Any]) -> tuple[int, float, str]:
    due_ts = kernel_scheduler_due_timestamp(task)
    return (
        0 if due_ts > 0 else 1,
        due_ts if due_ts > 0 else float("inf"),
        str(task.get("id") or ""),
    )


def kernel_scheduler_time_order_key(task: dict[str, Any]) -> tuple[int, float, str]:
    due_ts = kernel_scheduler_due_timestamp(task)
    return (
        0 if due_ts > 0 else 1,
        due_ts if due_ts > 0 else float("inf"),
        str(task.get("id") or ""),
    )


def merge_kernel_scheduler_task_updates(
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
        # Callers frequently update only context/schedule facts for one task.
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
        if (task := normalize_kernel_scheduler_task(item)) is not None
    ]


def repair_kernel_scheduler_tasks(
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
        if (task := normalize_kernel_scheduler_task(item)) is not None
    }
    # This former standalone job now runs inside daily activity synchronization.
    existing.pop("wanxiang-baoge-six-yuan", None)
    defaults = [
        task
        for item in default_tasks
        if (task := normalize_kernel_scheduler_task(item)) is not None
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
            if task_id == "legacy-daily-mojie-raid":
                # Losing this Job record also loses the only authoritative
                # business-owned next_time.  A catalogue bootstrap time cannot
                # prove that the current raid week completed, so fail safe by
                # re-queuing the idempotent Job instead of silently skipping to
                # the next Monday.
                recovered["next_time"] = current.strftime("%Y-%m-%d %H:%M:%S")
            tasks.append(recovered)
            continue
        raw_previous = raw_by_id.get(task_id, {})
        migrated_retry_delay = previous["error_retry_delay_seconds"]
        if (
            task_id == "bubble-weekly-pills"
            and raw_previous.get("error_retry_delay_seconds") == 0
        ):
            # The original standard record retried immediately.  A failed SDK
            # overlay click therefore formed a cross-attempt click storm.  Only
            # migrate that exact legacy value; preserve any user override.
            migrated_retry_delay = default["error_retry_delay_seconds"]
        migrated_dispatch_level = previous["dispatch_level"]
        migrated_execution_state = {
            key: previous.get(key)
            for key in _SCHEDULER_EXECUTION_STATE_FIELDS
            if key in raw_previous
        }
        if (
            task_id == "ranking-lifecycle"
            and "next_time" in raw_previous
            and previous.get("next_time") is None
            and str(previous.get("last_result") or "") != "running"
        ):
            # The lifecycle Job always computes a future daily/checkpoint wake;
            # ``null`` is therefore not a valid terminal schedule.  Older
            # attempts could lose that write in the Cell-terminal/reaper race.
            # Re-queue once so the Job can rediscover current activity occurrences and
            # select the authoritative 00:30/active-checkpoint wake itself.
            migrated_execution_state["next_time"] = current.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        if (
            task_id != "ranking-lifecycle"
            and "next_time" in raw_previous
            and previous.get("next_time") is None
            and str(previous.get("last_result") or "") == "error"
        ):
            # 2026-09-18：周常_活跃度 在失败重试期间被清空 next_time（前端
            # “取消执行”/内核 set_trigger_time(None)），此后本周的复查全部跳过，
            # 作业静默消失且没有任何告警——"取消一次执行"变成了"永久删除"。
            # catalogue 的 initial_times 只在作业记录缺失时生效，所以这里给
            # “失败后被清空”的周期性作业按目录锚点重新排队一次，并把原因写进
            # last_message，让恢复动作可见。手动类作业没有目录锚点，不受影响。
            catalogue_anchor = str(default.get("next_time") or "").strip()
            if catalogue_anchor:
                migrated_execution_state["next_time"] = catalogue_anchor
                recovered_note = (
                    f"下一次触发已按作业目录锚点恢复为 {catalogue_anchor}"
                )
                existing_message = str(previous.get("last_message") or "").strip()
                migrated_execution_state["last_message"] = (
                    f"{existing_message}｜{recovered_note}"
                    if existing_message
                    else recovered_note
                )
        migrated_payload = {
            **default.get("payload", {}),
            **previous.get("payload", {}),
        }
        tasks.append({
            **default,
            **migrated_execution_state,
            "dispatch_level": migrated_dispatch_level,
            "dispatch_order": (
                previous["dispatch_order"]
                if "dispatch_order" in raw_previous
                else default["dispatch_order"]
            ),
            "error_retry_delay_seconds": (
                migrated_retry_delay
                if "error_retry_delay_seconds" in raw_previous
                else default["error_retry_delay_seconds"]
            ),
            "payload": migrated_payload,
        })
    tasks.extend(existing.values())
    return tasks, raw != tasks


def kernel_scheduler_task_plan_reason(
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
    context = facts.get("context") if isinstance(facts.get("context"), dict) else {}
    guard = facts.get("guard") if isinstance(facts.get("guard"), dict) else {}
    events = facts.get("events") if isinstance(facts.get("events"), list) else []
    return {
        "updated_at": facts.get("updated_at"),
        "current_scene": context.get("current_scene"),
        "execution_status": context.get("status") or "",
        "execution_task": context.get("current_task") or "",
        "guard_enabled": bool(guard.get("enabled")),
        "guard_running": bool(guard.get("running")),
        "scene_count": len(discoveries.get("scene") or {}) if isinstance(discoveries.get("scene"), dict) else 0,
        "popup_count": len(discoveries.get("popup") or {}) if isinstance(discoveries.get("popup"), dict) else 0,
        "occlusion_count": len(discoveries.get("occlusion") or {}) if isinstance(discoveries.get("occlusion"), dict) else 0,
        "task_fact_count": len(discoveries.get("task") or {}) if isinstance(discoveries.get("task"), dict) else 0,
        "last_events": [item for item in events[-5:] if isinstance(item, dict)],
    }


def build_kernel_scheduler_plan(
    tasks: list[dict[str, Any]],
    context: dict[str, Any],
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
    execution_running = bool(context.get("running"))
    current_ts = time.time() if now_ts is None else now_ts

    plan_items: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        due = bool(task_due(task))
        supported = bool(task_supported(task))
        plan_items.append({
            **task,
            "due": due,
            "runnable": due and supported and not execution_running,
            "supported": supported,
            "reason": kernel_scheduler_task_plan_reason(
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
            kernel_scheduler_dispatch_sort_key(item),
        )
    )
    due_tasks = [item for item in plan_items if item["due"]]
    runnable_tasks = [item for item in due_tasks if item["runnable"]]
    if execution_running:
        next_action = "wait"
        message = f"Kernel 调度器正在执行：{context.get('current_task') or context.get('task_type') or '任务'}"
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
        "context": {
            "running": execution_running,
            "status": context.get("status") or "",
            "current_task": context.get("current_task") or "",
            "current_task_id": context.get("current_task_id") or "",
            "task_type": context.get("task_type") or "",
            "phase": context.get("phase") or "",
            "current_scene": context.get("current_scene"),
            "interruptible": bool(context.get("interruptible", True)),
        },
        "facts_summary": data_annotation_world_facts_summary(facts),
        "daily_audit": daily_audit,
        "due_tasks": due_tasks,
        "tasks": plan_items,
        "path": str(scheduler_state_path),
    }


def kernel_scheduler_run_now_task(
    tasks: list[dict[str, Any]],
    task_id: str,
    payload_override: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return scheduled_task_run_copy(tasks, task_id, payload_override)

