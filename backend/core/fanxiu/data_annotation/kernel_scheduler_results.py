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


def preserve_and_report_scheduler_terminal(task_id, attempt_id, *, cell_result,
                                           scheduler_state_path=None, **receipt_fields):
    """After a winning terminal CAS, save native evidence then project reporting.

    Both the submitting caller and detached-terminal recovery use this provider.
    Failures are diagnostics, never an instruction to replay committed effects.
    """
    result = preserve_scheduler_cell_result(task_id, attempt_id, cell_result=cell_result,
                                           **receipt_fields)
    if (receipt_fields['scheduler_result'] == 'success' and task_id == 'ranking-lifecycle'
            and not result.get('terminal_receipt_error')):
        from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_kernel_scheduler_state_path
        root = Path(scheduler_state_path or fanxiu_kernel_scheduler_state_path()).parent
        try:
            from backend.core.fanxiu.activity.ranking_completion_reporting import publish_committed_ranking_attempt
            result = {**result, 'ranking_completion_reporting': publish_committed_ranking_attempt(
                task_id, attempt_id, configuration_path=root / 'ranking-completion-reporting.json',
                directory=root / 'terminal-cell-results')}
        except Exception as exc:
            result = {**result, 'terminal_reporting_error': {
                'error_type': type(exc).__name__, 'message': str(exc),
                'task_id': task_id, 'attempt_id': attempt_id}}
            try:
                from backend.core.fanxiu.activity.ranking_completion_reporting import record_ranking_reporting_obligation
                record_ranking_reporting_obligation(task_id, attempt_id, status='error', error=str(exc),
                    configuration_path=root / 'ranking-completion-reporting.json',
                    directory=root / 'terminal-cell-results')
            except Exception as obligation_error:
                result['terminal_reporting_error']['obligation_error'] = str(obligation_error)
    return result


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


def read_scheduler_task_terminal_result(task_id: str, *, scheduler_state_path=None) -> dict:
    """Read a task's explicitly committed terminal reference and exact receipt.

    No directory enumeration or timestamp guessing. Older tasks without a
    reference stay unknown. A live new attempt does not turn the older terminal
    receipt into evidence about current execution. Missing journals stay missing.
    """
    from .kernel_scheduler_control import read_scheduler_tasks
    task = next((row for row in read_scheduler_tasks(scheduler_state_path=scheduler_state_path)
                 if row.get("id") == task_id), None)
    if task is None:
        return {"found": False, "task_id": task_id, "reason": "scheduler_task_missing"}
    reference = task.get("last_terminal_attempt")
    if not isinstance(reference, dict) or not reference.get("attempt_id"):
        return {"found": False, "task_id": task_id, "reason": "terminal_reference_missing",
                "current_attempt_id": task.get("attempt_id")}
    if not task.get("attempt_id") and any(
        task.get(task_key) != reference.get(reference_key)
        for task_key, reference_key in (("last_run_at", "started_at"),
                                        ("finished_at", "finished_at"),
                                        ("last_result", "scheduler_result"))
    ):
        # Legacy reconciliation can finalize an attempt without journaling it.
        # Never present an earlier reference as that newer terminal result.
        return {"found": False, "task_id": task_id, "reason": "terminal_reference_stale",
                "terminal_reference": reference, "current_attempt_id": None}
    receipt = read_scheduler_cell_result(
        task_id, reference["attempt_id"],
        directory=(Path(scheduler_state_path).parent / "terminal-cell-results"
                   if scheduler_state_path is not None else None))
    if receipt["found"] and any(receipt.get(key) != reference.get(key)
                                for key in ("started_at", "finished_at", "scheduler_result")):
        raise ValueError("终态引用与原生凭证不一致")
    return {**receipt, "terminal_reference": reference,
            "current_attempt_id": task.get("attempt_id")}
