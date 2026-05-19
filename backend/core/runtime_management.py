from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.api.task_manager import task_manager
from backend.core.device import device_manager, get_device_id
from backend.core.runtime_units import (
    command_runtime_group,
    command_runtime_queue_name,
    infer_command_runtime_kind,
    resolve_builtin_job_runtime_policy,
    resolve_command_runtime_policy,
    runtime_policy_payload,
)
from backend.models import Task as TaskModel


def _model_dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _is_command_job(task: TaskModel) -> bool:
    return infer_command_runtime_kind(task) == "job"


def _command_group_for(task: TaskModel, kind: str) -> tuple[str, str]:
    return command_runtime_group(task, "job" if kind == "job" else "service")


def _runtime_group(kind: str, group_id: str, title: str) -> dict[str, Any]:
    return {
        "id": group_id,
        "kind": kind,
        "title": title,
        "queue_key": group_id if kind == "job" else None,
        "is_default": group_id in {"service:default", "job:default"},
    }


def _command_next_run_at(task_id: str) -> str | None:
    job = task_manager.scheduler.get_job(task_id)
    if not job or not job.next_run_time:
        return None
    return job.next_run_time.isoformat()


def _extract_command_option(command: str, option: str) -> str:
    pattern = rf'(?:^|\s){re.escape(option)}(?:=|\s+)("[^"]+"|\'[^\']+\'|\S+)'
    match = re.search(pattern, command)
    if not match:
        return ""
    return match.group(1).strip().strip('"\'')


def _short_device_name(value: str) -> str:
    name = value.strip()
    return name.removeprefix("codepc_") or name


def _command_runtime_title(task: TaskModel) -> str:
    title = task.name or task.id
    command = task.command or ""
    if "sync_rime_config.py" in command and (title == "小狼毫配置同步" or title.startswith("rime_")):
        target_name = _extract_command_option(command, "--target-name")
        target_entry_id = _extract_command_option(command, "--target-entry-id")
        target = _short_device_name(target_name or target_entry_id)
        return f"小狼毫到{target}" if target else "小狼毫命令同步"
    return title


def _find_queue_snapshot(queue: dict[str, Any] | None, task_name: str) -> dict[str, Any] | None:
    if not queue:
        return None
    running = queue.get("running")
    if isinstance(running, dict) and running.get("name") == task_name:
        return running
    for item in queue.get("pending") or []:
        if isinstance(item, dict) and item.get("name") == task_name:
            return item
    return None


def _serialize_command_runtime_item(task: TaskModel, queue: dict[str, Any] | None = None) -> dict[str, Any]:
    kind = "job" if _is_command_job(task) else "service"
    group_id, group_title = _command_group_for(task, kind)
    policy = resolve_command_runtime_policy(task)
    status = task_manager.get_task_status(task.id)
    status_payload = _model_dump(status)
    active = bool(status_payload.get("running"))
    next_run_at = _command_next_run_at(task.id)
    if next_run_at:
        status_payload["next_run_at"] = next_run_at
    queue_name = command_runtime_queue_name(task.id) if kind == "job" else ""
    queue_snapshot = _find_queue_snapshot(queue, queue_name)
    if queue_snapshot:
        status_payload["queued"] = True
        status_payload["queue_status"] = queue_snapshot.get("status")
        status_payload["queue_task_id"] = queue_snapshot.get("id")
    else:
        status_payload["queued"] = False

    return {
        "id": f"command:{task.id}",
        "key": task.id,
        "kind": kind,
        "source": "command",
        "group_id": group_id,
        "group_title": group_title,
        "title": _command_runtime_title(task),
        "description": task.description,
        "command": task.command,
        "cwd": task.cwd,
        "runtime_kind": kind,
        "schedule": task.schedule,
        "schedule_label": task.schedule or "",
        "next_run_at": next_run_at,
        "timeout": task.timeout,
        "order": task.order or 0,
        "active": active,
        "status": status_payload,
        "actions": (["trigger", "stop", "logs", "delete"] if kind == "job" else ["start", "stop", "logs", "delete", "reorder"]),
        "raw": task.model_dump(),
        **runtime_policy_payload(policy),
    }


def _serialize_builtin_job_item(item: dict[str, Any]) -> dict[str, Any]:
    category = str(item.get("category") or "默认")
    group_id = f"job:{category}"
    policy = resolve_builtin_job_runtime_policy()
    return {
        "id": f"builtin:{item.get('key')}",
        "key": item.get("key"),
        "kind": "job",
        "source": "builtin",
        "group_id": group_id,
        "group_title": category,
        "title": item.get("title") or item.get("key"),
        "description": item.get("description") or "",
        "command": "",
        "cwd": "",
        "schedule": item.get("cron_expression") or "",
        "schedule_label": item.get("schedule_label") or "",
        "next_run_at": item.get("next_run_at"),
        "timeout": None,
        "order": 0,
        "enabled": bool(item.get("enabled")),
        "active": bool(item.get("active")),
        "status": {
            "running": bool(item.get("active")),
            "enabled": bool(item.get("enabled")),
            "runner_running": bool(item.get("runner_running")),
            "next_run_at": item.get("next_run_at"),
            "latest_run": item.get("latest_run"),
            "retry_policy": item.get("retry_policy"),
            "trigger_warning": item.get("trigger_warning"),
        },
        "actions": ["trigger", "toggle", "delete", "reset_schedule"],
        "raw": item,
        **runtime_policy_payload(policy),
    }


def _collect_builtin_jobs(session: Session) -> dict[str, Any]:
    from backend.api.admin import get_background_task_status

    status = get_background_task_status(session)
    payload = status.model_dump() if hasattr(status, "model_dump") else dict(status)
    items = [
        _serialize_builtin_job_item(item)
        for item in payload.get("tasks", [])
        if isinstance(item, dict)
    ]
    return {
        "items": items,
        "queue": payload.get("queue"),
        "runner_running": payload.get("runner_running"),
        "next_wake_at": payload.get("next_wake_at"),
        "runner_error": payload.get("runner_error"),
    }


def build_runtime_status(session: Session, device_id: str | None = None) -> dict[str, Any]:
    target_device_id = device_id or get_device_id()
    local_device_id = get_device_id()

    if target_device_id == local_device_id:
        task_manager.scan_running_tasks()

    stmt = (
        select(TaskModel)
        .where(TaskModel.device_id == target_device_id)
        .order_by(TaskModel.order, TaskModel.created_at)
    )
    builtin = _collect_builtin_jobs(session) if target_device_id == local_device_id else {
        "items": [],
        "queue": None,
        "runner_running": False,
        "next_wake_at": None,
        "runner_error": None,
    }
    queue = builtin["queue"] if target_device_id == local_device_id else None
    command_items = [_serialize_command_runtime_item(task, queue=queue) for task in session.exec(stmt).all()]

    items = command_items + builtin["items"]
    group_by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        group_by_id[item["group_id"]] = _runtime_group(item["kind"], item["group_id"], item["group_title"])

    device = device_manager.get_device(target_device_id)
    return {
        "device_id": target_device_id,
        "device": device.to_dict() if device else {"id": target_device_id, "name": target_device_id},
        "groups": sorted(group_by_id.values(), key=lambda group: (group["kind"], group["title"])),
        "items": items,
        "queue": builtin["queue"],
        "runner_running": builtin["runner_running"],
        "next_wake_at": builtin["next_wake_at"],
        "runner_error": builtin["runner_error"],
    }


def trigger_builtin_runtime_job(task_key: str, session: Session) -> dict[str, Any]:
    from backend.api.admin import trigger_background_task

    result = trigger_background_task(task_key, session=session)
    return result.model_dump() if hasattr(result, "model_dump") else dict(result)


def trigger_command_runtime_item(task_key: str, session: Session) -> dict[str, Any]:
    task = session.get(TaskModel, task_key)
    if task is None:
        raise HTTPException(status_code=404, detail="运行单元不存在")
    policy = resolve_command_runtime_policy(task)
    if policy.kind == "job":
        return task_manager.enqueue_task_run(task_key, trigger_reason="manual_runtime")
    return task_manager.start_task(
        task_key,
        replace_running=policy.overlap_policy == "replace",
        trigger_reason="manual_runtime",
    )


def toggle_builtin_runtime_job(task_key: str, enabled: bool, session: Session) -> dict[str, Any]:
    from backend.api.admin import BackgroundTaskToggleRequest, toggle_background_task

    return toggle_background_task(
        task_key,
        BackgroundTaskToggleRequest(enabled=enabled),
        session=session,
    )


def delete_builtin_runtime_job(task_key: str) -> dict[str, Any]:
    from backend.api.admin import delete_background_task

    return delete_background_task(task_key)


def delete_builtin_runtime_queue_task(task_id: str) -> dict[str, Any]:
    from backend.api.admin import delete_background_queue_task

    return delete_background_queue_task(task_id)


def reset_builtin_runtime_job_schedule(task_key: str) -> dict[str, Any]:
    from backend.api.admin import reset_background_task_schedule_api

    return reset_background_task_schedule_api(task_key)


def ensure_builtin_source(source: str) -> None:
    if source != "builtin":
        raise HTTPException(status_code=400, detail="该操作仅支持内置作业")
