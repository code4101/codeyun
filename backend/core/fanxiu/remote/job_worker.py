"""Client-triggered single-attempt dispatch using the existing Scheduler policy.

This controller is outside the Kernel; it never retains Task steps. Errors latch
until explicit maintenance resume. Device polling runs independently of jobs.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import threading

from .device_bridge import device_bridge
from .sessions import RemoteError


def parse_stop_at(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RemoteError(422, "stop_at 必须是带时区的日期时间") from exc
    if result.tzinfo is None:
        raise RemoteError(422, "stop_at 必须包含时区")
    if result > datetime.now(timezone.utc) + timedelta(hours=48):
        raise RemoteError(422, "本次连续运行最多 48 小时")
    return result


def run_one_due(worker_id: str, *, bootstrap: bool = False) -> dict:
    from backend.core.fanxiu.behavior_tree.kernel_scheduler import DEFAULT_FANXIU_ENTRY_ID, resolve_fanxiu_entry
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
        read_scheduler_tasks, select_due_kernel_scheduler_tasks,
        run_now_scheduler_task,
    )
    kernel = fanxiu_kernel_manager_status()
    tasks = read_scheduler_tasks()
    if kernel.get("execution_state") == "busy" or any(t.get("attempt_id") or t.get("last_result") == "running" for t in tasks):
        return {"status": "busy", "message": "Kernel 或正式 attempt 正在运行"}
    # Historical environment incidents alone cannot gate a new attempt: the
    # public circuit contract requires fresh observation by the owned Task.
    due = select_due_kernel_scheduler_tasks(tasks)
    if not due and not bootstrap:
        return {"status": "idle", "message": "当前没有到期作业"}
    task_id = "login-game" if bootstrap else str(due[0]["id"])
    payload = {"__remote_worker_id": worker_id}
    if bootstrap:
        payload["__remote_bootstrap"] = True
    result = run_now_scheduler_task(entry=resolve_fanxiu_entry(DEFAULT_FANXIU_ENTRY_ID),
        entry_id=DEFAULT_FANXIU_ENTRY_ID, task_id=task_id, business_time_mode="current",
        interrupt_same_group=False, payload_override=payload)
    terminal = result.get("task_result")
    if terminal == "success":
        state = "idle"
    elif terminal in {"error", "interrupted"}:
        state = "error"
    elif "busy" in str(result.get("phase")) or result.get("running"):
        state = "busy"
    else:
        state = "paused"
    return {"status": state, "task_id": task_id, "task_result": terminal,
            "bootstrap_completed": bool(bootstrap and terminal == "success"),
            "message": str(result.get("message") or result.get("error") or "未形成明确业务终态")[:2000]}


class JobWorker:
    def __init__(self, *, operation=None):
        self.lock = threading.Lock()
        self.operation = operation
        self.prepared: set[str] = set()
        self.states: dict[str, dict] = {}
        self.active: str | None = None

    def status(self, worker_id):
        with self.lock:
            return copy.deepcopy(self.states.get(worker_id, {"status": "idle"}))

    def resume(self, worker_id):
        with self.lock:
            if self.active is not None:
                raise RemoteError(409, "作业执行中不能解除暂停")
            self.states[worker_id] = {"status": "idle", "message": "维护后已解除暂停"}
            return copy.deepcopy(self.states[worker_id])

    def next(self, worker_id, stop_at):
        deadline = parse_stop_at(stop_at)
        with self.lock:
            if self.active is not None:
                return {"status": "busy", "message": "已有客户端作业正在执行"}
            current = self.states.get(worker_id, {})
            if current.get("status") in {"error", "paused"}:
                return copy.deepcopy(current)
            if datetime.now(timezone.utc) >= deadline:
                self.states[worker_id] = {"status": "completed", "message": "已到截止时间，不再派发作业"}
                return copy.deepcopy(self.states[worker_id])
            self.active = worker_id
            self.states[worker_id] = {"status": "busy", "message": "正在选择并执行一个正式到期作业"}
            threading.Thread(target=self._run, args=(worker_id,), daemon=True, name="fanxiu-client-job").start()
            return copy.deepcopy(self.states[worker_id])

    def _run(self, worker_id):
        try:
            state = self.operation(worker_id) if self.operation is not None else run_one_due(worker_id, bootstrap=worker_id not in self.prepared)
        except Exception as exc:
            state = {"status": "error", "message": f"{type(exc).__name__}: {exc}"[:2000]}
        with self.lock:
            if state.get("bootstrap_completed"):
                self.prepared.add(worker_id)
            self.states[worker_id] = state
            self.active = None


job_worker = JobWorker()
