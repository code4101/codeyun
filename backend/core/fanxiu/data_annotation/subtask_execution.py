"""Attempt-owned observations for aggregate jobs, never resumable execution cursors.

The only durable business authority remains each domain's completion receipt.
This module writes a small current/last subtask observation under the Scheduler
lock. Readers accept a running observation only while its parent attempt is live.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from filelock import FileLock

from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_kernel_scheduler_state_path
from backend.core.fanxiu.data_annotation.state import read_data_annotation_json, write_data_annotation_json

_SUBTASK_LOG_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar("fanxiu_subtask_log", default=None)
_JOB_LOG_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar("fanxiu_job_log", default=None)


def current_job_log_context() -> dict[str, str]:
    """Formal Cell ownership for the logging provider, without runner coupling."""
    return dict(_JOB_LOG_CONTEXT.get() or {})


@contextmanager
def job_log_context(task_id: str, attempt_id: str):
    token = _JOB_LOG_CONTEXT.set({"task_id": task_id, "attempt_id": attempt_id} if task_id else None)
    try:
        yield
    finally:
        _JOB_LOG_CONTEXT.reset(token)


def current_subtask_log_context() -> dict[str, str]:
    """Current call's log identity, isolated across threads and nested calls."""
    return dict(_SUBTASK_LOG_CONTEXT.get() or {})


@contextmanager
def subtask_log_context(task_id: str, attempt_id: str, node_id: str):
    """Annotate receipt reuse as well as actions, without claiming a running node."""
    token = _SUBTASK_LOG_CONTEXT.set({"task_id": task_id, "subtask_id": node_id, "attempt_id": attempt_id})
    try:
        yield
    finally:
        _SUBTASK_LOG_CONTEXT.reset(token)


def subtask_node_id(task_id: str, *identity: str) -> str:
    """Stable occurrence/cycle-qualified identity shared by plans and execution."""
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
    return f"{task_id}:{digest}"


def record_subtask_execution(task_id: str, attempt_id: str, node_id: str,
                             label: str, status: str, *, message: str = "",
                             business_time: str = "",
                             scheduler_state_path: Path | None = None) -> bool:
    """Narrow-write an observation only for the live parent; reject stale writers.

An unowned R&D Cell has no Scheduler attempt and simply produces no observation.
Late terminal writes cannot resurrect an ended attempt or overwrite a sibling.
"""
    if status not in {"running", "finished", "error", "interrupted"}:
        raise ValueError("未知子任务执行状态")
    if not attempt_id:
        return False
    path = scheduler_state_path or fanxiu_kernel_scheduler_state_path()
    with FileLock(str(path.with_name(f"{path.name}.lock")), timeout=30):
        tasks = read_data_annotation_json(path, [])
        task = next((t for t in tasks if t.get("id") == task_id), None)
        if task is None or task.get("attempt_id") != attempt_id or task.get("last_result") != "running":
            return False
        payload = dict(task.get("payload") or {})
        previous = payload.get("subtask_execution") or {}
        if status != "running" and (previous.get("node_id") != node_id or previous.get("attempt_id") != attempt_id):
            return False
        payload["subtask_execution"] = {
            "attempt_id": attempt_id, "node_id": node_id, "label": label,
            "status": status, "message": message[:2000],
            "business_time": business_time,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        task["payload"] = payload
        write_data_annotation_json(path, tasks)
    return True


@contextmanager
def observe_subtask(task_id: str, attempt_id: str, node_id: str, label: str,
                    *, log=None, scheduler_state_path: Path | None = None, business_time: str = ""):
    """Annotate one fresh business call; completion is committed by its owner."""
    def record(status, message=""):
        record_subtask_execution(task_id, attempt_id, node_id, label, status,
                                 message=message, scheduler_state_path=scheduler_state_path, business_time=business_time)
        if log:
            log(f"[subtask:{node_id}] {label} · {status}{': ' + message if message else ''}")
    token = _SUBTASK_LOG_CONTEXT.set({"task_id": task_id, "subtask_id": node_id, "attempt_id": attempt_id})
    try:
        record("running")
        try:
            yield
        except BaseException as exc:
            record("interrupted" if isinstance(exc, (KeyboardInterrupt, InterruptedError, GeneratorExit)) else "error", str(exc))
            raise
        else:
            record("finished")
    finally:
        _SUBTASK_LOG_CONTEXT.reset(token)
