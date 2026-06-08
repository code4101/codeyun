from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.fanxiu_behavior_tree import (
    data_annotation_asset_tree_path,
    ensure_fanxiu_behavior_tree_service,
    ensure_fanxiu_runtime_jobs_registered,
    fanxiu_data_annotation_manual_job_state_path,
    fanxiu_data_annotation_runtime_state_path,
    fanxiu_data_annotation_scheduler_state_path,
    fanxiu_data_annotation_world_facts_path,
    fanxiu_runtime_guard_definitions,
    fanxiu_runtime_runner_running,
    fanxiu_runtime_runner_status,
    fanxiu_runtime_runner_wake,
    fanxiu_runtime_task_label,
    replace_fanxiu_runtime_logs,
    set_fanxiu_runtime_guard,
    start_fanxiu_manual_runtime_task,
    stop_fanxiu_behavior_tree_current_task,
)
from backend.core.fanxiu_data_annotation_jobs import (
    create_data_annotation_manual_job,
    data_annotation_manual_jobs_state,
    get_fanxiu_data_annotation_manual_job_definition,
    pop_next_data_annotation_manual_job,
    read_data_annotation_manual_jobs,
    requeue_running_data_annotation_manual_jobs,
)
from backend.core.fanxiu_data_annotation_scheduler import (
    build_data_annotation_scheduler_plan,
    data_annotation_scheduler_order_key,
    merge_data_annotation_scheduler_task_updates,
    data_annotation_scheduler_run_now_task,
    data_annotation_scheduler_task_plan_reason,
    data_annotation_world_facts_summary,
    repair_data_annotation_scheduler_tasks,
    sync_data_annotation_scheduler_tasks_from_world_facts,
)
from backend.core.fanxiu_data_annotation_scheduler_defaults import default_data_annotation_scheduler_tasks
from backend.core.fanxiu_data_annotation_state import (
    append_data_annotation_runtime_log_once,
    data_annotation_scheduler_task_state,
    data_annotation_task_due,
    is_data_annotation_runtime_live_empty,
    next_data_annotation_scheduler_time,
    normalize_data_annotation_runtime_guard_items,
    parse_data_annotation_task_time,
    persist_data_annotation_runtime_status,
    read_data_annotation_json,
    read_data_annotation_runtime_status,
    read_data_annotation_world_facts,
    record_data_annotation_scheduler_task_fact,
    write_data_annotation_json,
    write_data_annotation_world_facts,
)


def read_world_facts(path: Path | None = None) -> dict[str, Any]:
    return read_data_annotation_world_facts(path or fanxiu_data_annotation_world_facts_path())


def write_world_facts(facts: dict[str, Any], path: Path | None = None) -> None:
    write_data_annotation_world_facts(path or fanxiu_data_annotation_world_facts_path(), facts)


def record_scheduler_task_fact(
    task: dict[str, Any],
    result: str,
    *,
    world_facts_path: Path | None = None,
) -> None:
    record_data_annotation_scheduler_task_fact(world_facts_path or fanxiu_data_annotation_world_facts_path(), task, result)


def persist_runtime_status(
    status: dict[str, Any],
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> None:
    persist_data_annotation_runtime_status(
        runtime_state_path or fanxiu_data_annotation_runtime_state_path(),
        world_facts_path or fanxiu_data_annotation_world_facts_path(),
        status,
    )


def read_runtime_status(path: Path | None = None) -> dict[str, Any]:
    return read_data_annotation_runtime_status(path or fanxiu_data_annotation_runtime_state_path())


def append_runtime_log_once(status: dict[str, Any], kind: str, message: str) -> None:
    append_data_annotation_runtime_log_once(status, kind, message, time_text=datetime.now().strftime("%H:%M:%S"))


def normalize_runtime_guard_items(status: dict[str, Any]) -> None:
    normalize_data_annotation_runtime_guard_items(status, fanxiu_runtime_guard_definitions())


def read_manual_jobs(path: Path | None = None) -> list[dict[str, Any]]:
    raw = read_data_annotation_json(path or fanxiu_data_annotation_manual_job_state_path(), [])
    return read_data_annotation_manual_jobs(raw)


def write_manual_jobs(jobs: list[dict[str, Any]], path: Path | None = None) -> None:
    write_data_annotation_json(path or fanxiu_data_annotation_manual_job_state_path(), data_annotation_manual_jobs_state(jobs))


def requeue_running_manual_jobs(path: Path | None = None) -> int:
    jobs = read_manual_jobs(path)
    updated, changed_count = requeue_running_data_annotation_manual_jobs(jobs)
    if changed_count:
        write_manual_jobs(updated, path)
    return changed_count


def remove_manual_job(job_id: str, path: Path | None = None) -> None:
    job_id = str(job_id or "")
    if not job_id:
        return
    jobs = [job for job in read_manual_jobs(path) if str(job.get("id") or "") != job_id]
    write_manual_jobs(jobs, path)


def enqueue_manual_job(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    label: str = "",
    interruptible: bool | None = None,
    manual_job_path: Path | None = None,
) -> dict[str, Any]:
    task_type = str(task_type or "detect_scene").strip() or "detect_scene"
    ensure_fanxiu_runtime_jobs_registered()
    definition = get_fanxiu_data_annotation_manual_job_definition(task_type)
    job = create_data_annotation_manual_job(
        task_type,
        payload,
        label=label,
        interruptible=interruptible,
        definition=definition,
        task_label=fanxiu_runtime_task_label,
        now=time.time(),
    )
    jobs = read_manual_jobs(manual_job_path)
    jobs.append(job)
    write_manual_jobs(jobs, manual_job_path)
    return job


def pop_next_manual_job(path: Path | None = None) -> dict[str, Any] | None:
    jobs = read_manual_jobs(path)
    selected, claimed_jobs = pop_next_data_annotation_manual_job(jobs)
    if selected is None:
        return None
    write_manual_jobs(claimed_jobs, path)
    return selected


def repair_orphaned_scheduler_runs(
    tasks: list[dict[str, Any]],
    *,
    manual_job_path: Path | None = None,
    now_ts: float | None = None,
    running: bool | None = None,
) -> bool:
    pending_scheduler_ids: set[str] = set()
    for job in read_manual_jobs(manual_job_path):
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "")
        if scheduler_task_id:
            pending_scheduler_ids.add(scheduler_task_id)
    if fanxiu_runtime_runner_running() if running is None else running:
        return False
    changed = False
    current_ts = time.time() if now_ts is None else now_ts
    for task in tasks:
        task_id = str(task.get("id") or "")
        if not task_id or task_id in pending_scheduler_ids:
            continue
        if str(task.get("last_result") or "") not in {"queued", "running"}:
            continue
        last_run_ts = parse_data_annotation_task_time(task.get("last_run_at"))
        if last_run_ts is not None and current_ts - last_run_ts < 60:
            continue
        task["last_result"] = "stopped"
        task["retry_after"] = None
        checkpoint = task.get("checkpoint") if isinstance(task.get("checkpoint"), dict) else {}
        checkpoint["recovered_from_orphaned_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        task["checkpoint"] = checkpoint
        changed = True
    return changed


def task_supported(task: dict[str, Any]) -> bool:
    ensure_fanxiu_runtime_jobs_registered()
    definition = get_fanxiu_data_annotation_manual_job_definition(str(task.get("task_type") or ""))
    return bool(definition and definition.scheduler_supported)


def read_scheduler_tasks(
    *,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    manual_job_path: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    path = scheduler_state_path or fanxiu_data_annotation_scheduler_state_path()
    raw = read_data_annotation_json(path, None)
    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        default_data_annotation_scheduler_tasks(),
        read_world_facts(world_facts_path),
        task_supported=task_supported,
        now=now or datetime.now(),
    )
    if repair_orphaned_scheduler_runs(tasks, manual_job_path=manual_job_path):
        changed = True
    if changed:
        write_scheduler_tasks(tasks, scheduler_state_path=path)
    return tasks


def write_scheduler_tasks(tasks: list[dict[str, Any]], *, scheduler_state_path: Path | None = None) -> None:
    write_data_annotation_json(
        scheduler_state_path or fanxiu_data_annotation_scheduler_state_path(),
        [data_annotation_scheduler_task_state(task) for task in tasks],
    )


def update_scheduler_tasks(
    updates: list[dict[str, Any]],
    *,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    manual_job_path: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    tasks = merge_data_annotation_scheduler_task_updates(
        read_scheduler_tasks(
            scheduler_state_path=scheduler_state_path,
            world_facts_path=world_facts_path,
            manual_job_path=manual_job_path,
            now=now,
        ),
        updates,
        now=now or datetime.now(),
    )
    write_scheduler_tasks(tasks, scheduler_state_path=scheduler_state_path)
    return read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
        manual_job_path=manual_job_path,
        now=now,
    )


def runtime_status(
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    status = fanxiu_runtime_runner_status()
    persisted = read_runtime_status(runtime_state_path)
    if persisted and is_data_annotation_runtime_live_empty(status):
        status.update(persisted)
        status["running"] = False
        status["guard_running"] = False
        status["service_running"] = False
        status["updated_at"] = time.time()
        if persisted.get("running"):
            status["status"] = "stopped"
            status["phase"] = "stopped"
            status["message"] = "后端已重载，运行状态已结束"
            status["finished_at"] = status.get("finished_at") or time.time()
            append_runtime_log_once(status, "stop", "后端已重载，运行状态已结束")
        elif persisted.get("guard_enabled") or persisted.get("guard_running"):
            status["status"] = "idle"
            status["message"] = "后端已重载，行为树服务待恢复"
            append_runtime_log_once(status, "stop", "后端已重载，行为树服务待恢复")
    normalize_runtime_guard_items(status)
    status.pop("priority", None)
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def ensure_runtime_service(
    *,
    entry: Any,
    entry_id: str,
    asset_tree_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or "")
    status = ensure_fanxiu_behavior_tree_service(
        entry,
        resolved_entry_id,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
    )
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def start_runtime_task(
    *,
    entry: Any,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
    asset_tree_path: Path | None = None,
    manual_job_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    return submit_manual_job(
        entry=entry,
        entry_id=entry_id,
        task_type=task_type,
        payload=payload,
        asset_tree_path=asset_tree_path,
        manual_job_path=manual_job_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def stop_current_task(
    entry_id: str,
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    status = stop_fanxiu_behavior_tree_current_task(entry_id)
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def set_runtime_guard(
    *,
    entry: Any,
    entry_id: str,
    guard_id: str,
    enabled: bool,
    interval_seconds: float,
    asset_tree_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or "")
    status = set_fanxiu_runtime_guard(
        entry=entry,
        entry_id=resolved_entry_id,
        guard_id=guard_id,
        enabled=enabled,
        interval_seconds=interval_seconds,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
    )
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def submit_tick_task(
    *,
    entry: Any,
    entry_id: str,
    task_type: str | None = None,
    payload: dict[str, Any] | None = None,
    asset_tree_path: Path | None = None,
    manual_job_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    resolved_task_type = str(task_type or "detect_scene").strip() or "detect_scene"
    if resolved_task_type == "manual_tick":
        resolved_task_type = "detect_scene"
    return submit_manual_job(
        entry=entry,
        entry_id=entry_id,
        task_type=resolved_task_type,
        payload=payload,
        label="单步识别" if resolved_task_type == "detect_scene" else "",
        asset_tree_path=asset_tree_path,
        manual_job_path=manual_job_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def runtime_logs(
    *,
    limit: int = 500,
    scope: str = "",
    item_id: str = "",
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> list[dict[str, Any]]:
    status = runtime_status(runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    log_items = [item for item in (status.get("logs") or []) if isinstance(item, dict)]
    resolved_scope = str(scope or "").strip()
    resolved_item_id = str(item_id or "").strip()
    if resolved_scope:
        log_items = [item for item in log_items if str(item.get("scope") or "") == resolved_scope]
    if resolved_item_id:
        log_items = [item for item in log_items if str(item.get("item_id") or "") == resolved_item_id]
    return log_items[-max(1, int(limit)) :]


def clear_runtime_logs(
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> list[dict[str, Any]]:
    status = fanxiu_runtime_runner_status()
    status["logs"] = []
    replace_fanxiu_runtime_logs([])
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return []


def queue_manual_job_status(
    *,
    entry: Any,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
    label: str = "",
    interruptible: bool | None = None,
    asset_tree_path: Path | None = None,
    manual_job_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    ensure_fanxiu_behavior_tree_service(entry, entry_id, asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(entry_id))
    job = enqueue_manual_job(task_type, payload, label=label, interruptible=interruptible, manual_job_path=manual_job_path)
    fanxiu_runtime_runner_wake()
    status = fanxiu_runtime_runner_status()
    status.update({
        "entry_id": entry_id,
        "phase": "manual_job_queued",
        "message": f"手动作业已排队：{job.get('label') or job.get('task_type')}",
        "queued_job": {
            "id": job.get("id"),
            "task_type": job.get("task_type"),
            "label": job.get("label"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
        },
        "updated_at": time.time(),
    })
    logs = list(status.get("logs") or [])
    logs.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "kind": "info",
        "scope": "manual_job",
        "item_id": "manual_job",
        "message": f"[{job.get('id')}] {status['message']}",
    })
    status["logs"] = logs[-500:]
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def submit_manual_job(
    *,
    entry: Any,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
    label: str = "",
    interruptible: bool | None = None,
    asset_tree_path: Path | None = None,
    manual_job_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    task_type = str(task_type or "detect_scene").strip() or "detect_scene"
    definition = get_fanxiu_data_annotation_manual_job_definition(task_type)
    if definition is None:
        raise ValueError(f"暂不支持的任务类型：{task_type}")
    return queue_manual_job_status(
        entry=entry,
        entry_id=entry_id,
        task_type=task_type,
        payload=payload,
        label=label,
        interruptible=interruptible if interruptible is not None else definition.interruptible,
        asset_tree_path=asset_tree_path,
        manual_job_path=manual_job_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def start_next_manual_job_if_idle(
    entry: Any,
    entry_id: str,
    *,
    manual_job_path: Path | None = None,
    asset_tree_path: Path | None = None,
) -> dict[str, Any] | None:
    if fanxiu_runtime_runner_running():
        return None
    task = pop_next_manual_job(manual_job_path)
    if task is None:
        return None
    return start_fanxiu_manual_runtime_task(
        entry=entry,
        entry_id=entry_id,
        task=task,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(entry_id),
    )


def sync_scheduler_tasks_from_world_facts(
    tasks: list[dict[str, Any]],
    *,
    world_facts_path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    return sync_data_annotation_scheduler_tasks_from_world_facts(
        tasks,
        read_world_facts(world_facts_path),
        now=now or datetime.now(),
    )


def scheduler_task_view(task: dict[str, Any]) -> dict[str, Any]:
    return {**task, "supported": task_supported(task)}


def scheduler_task_plan_reason(task: dict[str, Any], due: bool) -> str:
    return data_annotation_scheduler_task_plan_reason(
        task,
        due,
        task_supported=task_supported,
        now_ts=time.time(),
    )


def world_facts_summary(facts: dict[str, Any]) -> dict[str, Any]:
    return data_annotation_world_facts_summary(facts)


def build_scheduler_plan(
    *,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    manual_job_path: Path | None = None,
) -> dict[str, Any]:
    return build_data_annotation_scheduler_plan(
        read_scheduler_tasks(
            scheduler_state_path=scheduler_state_path,
            world_facts_path=world_facts_path,
            manual_job_path=manual_job_path,
        ),
        fanxiu_runtime_runner_status(),
        read_world_facts(world_facts_path),
        scheduler_state_path or fanxiu_data_annotation_scheduler_state_path(),
        task_supported=task_supported,
        task_due=data_annotation_task_due,
        now_ts=time.time(),
    )


def task_payload_with_meta(task: dict[str, Any]) -> dict[str, Any]:
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    return {
        **payload,
        "__scheduler_task_id": str(task.get("id") or ""),
        "__scheduler_interruptible": bool(task.get("interruptible", True)),
    }


def next_scheduler_time(task: dict[str, Any], now: datetime | None = None) -> str | None:
    return next_data_annotation_scheduler_time(task, now if now is not None else datetime.now())


def prepare_runtime_for_scheduler_task(
    task: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    scheduler_state_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any] | None:
    status = fanxiu_runtime_runner_status()
    if not status.get("running"):
        return None
    task_id = str(task.get("id") or "")
    task["last_result"] = "queued"
    record_scheduler_task_fact(task, "queued", world_facts_path=world_facts_path)
    write_scheduler_tasks(tasks, scheduler_state_path=scheduler_state_path)
    message = f"当前有任务运行，{task_id or task.get('label') or task.get('task_type')} 已排队"
    status.update({"message": message, "updated_at": time.time()})
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def run_now_scheduler_task(
    *,
    entry: Any,
    entry_id: str,
    task_id: str,
    payload_override: dict[str, Any] | None = None,
    scheduler_state_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    manual_job_path: Path | None = None,
    asset_tree_path: Path | None = None,
) -> dict[str, Any]:
    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
        manual_job_path=manual_job_path,
    )
    state_task = next((item for item in tasks if item.get("id") == task_id), None)
    run_task = data_annotation_scheduler_run_now_task(tasks, task_id, payload_override)
    if state_task is None or run_task is None:
        raise LookupError("任务不存在")
    if not task_supported(run_task):
        raise ValueError("任务尚未纳入当前框架验收")
    blocked_status = prepare_runtime_for_scheduler_task(
        state_task,
        tasks,
        scheduler_state_path=scheduler_state_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    if blocked_status is not None:
        return blocked_status
    state_task["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state_task["last_result"] = "queued"
    write_scheduler_tasks(tasks, scheduler_state_path=scheduler_state_path)
    return submit_manual_job(
        entry=entry,
        entry_id=entry_id,
        task_type=str(run_task.get("task_type") or ""),
        payload=task_payload_with_meta(run_task),
        label=f"手动任务：{run_task.get('label') or run_task.get('id') or run_task.get('task_type')}",
        interruptible=bool(run_task.get("interruptible", True)),
        asset_tree_path=asset_tree_path,
        manual_job_path=manual_job_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def run_due_scheduler_tasks(
    *,
    entry: Any,
    entry_id: str,
    scheduler_state_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    manual_job_path: Path | None = None,
    asset_tree_path: Path | None = None,
) -> dict[str, Any]:
    ensure_fanxiu_behavior_tree_service(entry, entry_id, asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(entry_id))
    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
        manual_job_path=manual_job_path,
    )
    due_tasks = sorted(
        [
            item
            for item in tasks
            if str(item.get("schedule_kind") or "") != "manual"
            and data_annotation_task_due(item)
            and task_supported(item)
        ],
        key=data_annotation_scheduler_order_key,
    )
    if not due_tasks:
        status = runtime_status(runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        status.update({"message": "没有可执行的到期任务", "updated_at": time.time()})
        persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    blocked_status = prepare_runtime_for_scheduler_task(
        due_tasks[0],
        tasks,
        scheduler_state_path=scheduler_state_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    if blocked_status is not None:
        return blocked_status
    fanxiu_runtime_runner_wake()
    status = fanxiu_runtime_runner_status()
    status.update({
        "entry_id": entry_id,
        "phase": "scheduler_due_queued",
        "message": f"已唤醒常驻行为树执行到期任务：{due_tasks[0].get('label') or due_tasks[0].get('id')}",
        "updated_at": time.time(),
    })
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status
