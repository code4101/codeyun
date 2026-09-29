"""Resident inspection, independent of engineering/AI execution ownership.

The manager stays alive when the engineering dispatcher exits for AI control.
Only Cell submission and execution are activity; status polling is not activity.
Runtime probes maintain facts and trigger times; they never dispatch Cells.
Ownership checks run independently so slow Runtime recovery cannot delay them.
"""
from __future__ import annotations

import logging
import threading
import time

from . import kernel_scheduler_control as control
from .ai_assistance import assistance_control_lock

AI_IDLE_TIMEOUT_SECONDS = 30 * 60
logger = logging.getLogger(__name__)


def recover_abandoned_ai_control() -> dict:
    """Restore engineering after both inactivity and an overdue Job reach 30m.

    Ownership and normal Cell submissions share a short cross-process lock.
    Unknown/dead/busy Kernel states never release ownership. A fresh manager
    grants a new grace period because its previous Cell history is unavailable.
    """
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

    with assistance_control_lock():
        settings = control.read_scheduler_settings()
        if settings["job_group_enabled"] or not settings["behavior_tree_enabled"]:
            return {"recovered": False, "reason": "not_ai_control"}
        kernel = fanxiu_kernel_manager_status()
        if not kernel.get("alive") or kernel.get("execution_state") != "idle":
            return {"recovered": False, "reason": "kernel_not_idle"}
        activity = float(kernel.get("last_cell_submitted_at") or 0)
        changed = float(settings.get("control_changed_at") or settings.get("updated_at") or 0)
        if not changed:
            control.write_scheduler_settings(settings)
            return {"recovered": False, "reason": "initialized_grace_period"}
        now = time.time()
        if not activity or not changed or now - max(activity, changed) < AI_IDLE_TIMEOUT_SECONDS:
            return {"recovered": False, "reason": "recent_activity"}
        due = control.select_due_kernel_scheduler_tasks(control.read_scheduler_tasks())
        overdue = [
            task for task in due
            if (trigger := control.parse_data_annotation_task_time(task.get("next_time"))) is not None
            and now - trigger >= AI_IDLE_TIMEOUT_SECONDS
        ]
        if not overdue:
            return {"recovered": False, "reason": "no_overdue_job"}
        result = control.resume_engineering_control()
        logger.warning("AI 超时交权：30 分钟未提交 Cell，逾期作业 %s", [t.get("id") for t in overdue])
        return {"recovered": True, "reason": "ai_idle_timeout", "settings": result}


def inspect_runtime_once() -> dict:
    """Reconcile completed attempts, then update triggers from read-only facts."""
    from .game_state_inspection import inspect_game_state_once

    control.reconcile_stale_scheduler_attempts(control.read_scheduler_tasks())
    return inspect_game_state_once(asynchronous_recovery=True)


def _inspect_runtime_loop(stop: threading.Event) -> None:
    from .game_state_inspection import GAME_STATE_INSPECTION_INTERVAL_SECONDS

    while not stop.wait(GAME_STATE_INSPECTION_INTERVAL_SECONDS):
        try:
            inspect_runtime_once()
        except Exception:
            logger.exception("Runtime 巡检失败，下一轮重试")


def run_inspection_service(stop: threading.Event) -> None:
    """Host read-only Runtime patrol and five-minute ownership checks.

    Both stay resident in AI mode. The engineering dispatcher is separate;
    neither loop submits Cells or performs GUI actions. Slow address discovery
    uses the Runtime probe's existing asynchronous recovery and backoff.
    """
    runtime_thread = threading.Thread(
        target=_inspect_runtime_loop, args=(stop,),
        name="fanxiu-runtime-inspection", daemon=True,
    )
    runtime_thread.start()
    supervise_scheduler_ownership(stop)


def supervise_scheduler_ownership(stop: threading.Event) -> None:
    """Check ownership every five minutes, separately from Runtime latency."""
    retry_dispatcher = False
    while not stop.wait(5 * 60):
        try:
            # Handoff can enable engineering before dispatcher startup fails.
            # Retry that startup without waiting another 30m or requiring UI.
            if retry_dispatcher:
                settings = control.read_scheduler_settings()
                if settings["job_group_enabled"] and settings["behavior_tree_enabled"]:
                    result = control.ensure_doctor_watch_background()
                    if not result.get("ok"):
                        raise RuntimeError(f"工程派发未恢复：{result}")
                retry_dispatcher = False
            recover_abandoned_ai_control()
        except Exception:
            retry_dispatcher = True
            logger.exception("AI 超时交权检查失败，下一轮重试")
