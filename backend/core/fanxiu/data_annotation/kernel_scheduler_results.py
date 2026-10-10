"""Immutable terminal Cell receipts; never a resumable game cursor."""
from __future__ import annotations

import re
from pathlib import Path
from filelock import FileLock
from .state import read_data_annotation_json, write_data_annotation_json


def _path(attempt_id, directory):
    if not isinstance(attempt_id, str) or not re.fullmatch(r"[a-f0-9]{32}", attempt_id):
        raise ValueError("结果查询需要精确的正式attempt_id")
    if directory is None:
        from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_kernel_scheduler_state_path
        directory = fanxiu_kernel_scheduler_state_path().parent / "terminal-cell-results"
    return Path(directory) / (attempt_id + ".json")


def record_scheduler_cell_result(task_id, attempt_id, *, cell_result,
                                 business_result, started_at, finished_at,
                                 scheduler_result, directory=None):
    """Provider writer, called only after the attempt's terminal CAS succeeds.

    Preserve native Cell identity and returned payload independently of the
    latest Runtime status. No result is reconstructed from a future trigger.
    A failed journal write must be reported by the caller without changing an
    already committed game's/Scheduler's terminal outcome.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("终态结果需要task_id")
    path = _path(attempt_id, directory)
    value = {"task_id": task_id, "attempt_id": attempt_id,
             "started_at": started_at, "finished_at": finished_at,
             "scheduler_result": scheduler_result,
             "cell_result": cell_result, "business_result": business_result}
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=10):
        existing = read_data_annotation_json(path, None)
        if existing is not None:
            if existing != value:
                raise ValueError("同一attempt的终态凭证冲突")
            return existing
        write_data_annotation_json(path, value)
    return value


def preserve_scheduler_cell_result(task_id, attempt_id, *, cell_result, **receipt_fields):
    """After terminal CAS, report journal faults without retrying game effects."""
    try:
        record_scheduler_cell_result(task_id, attempt_id, cell_result=cell_result, **receipt_fields)
    except Exception as exc:
        return {**cell_result, "terminal_receipt_error": str(exc)}
    return cell_result


def read_scheduler_cell_result(task_id: str, attempt_id: str, *, directory=None) -> dict:
    """Read one exact committed receipt; missing history stays missing.

    No directories, services, Runtime snapshots, GUI actions or Scheduler
    writes are created. A time, filename order or same-task latest receipt
    never substitutes for the requested attempt.
    """
    path = _path(attempt_id, directory)
    value = read_data_annotation_json(path, None)
    if value is None:
        return {"found": False, "reason": "terminal_receipt_missing",
                "task_id": task_id, "attempt_id": attempt_id}
    if value.get("task_id") != task_id or value.get("attempt_id") != attempt_id:
        raise ValueError("终态结果task/attempt身份不匹配")
    return {"found": True, **value}
