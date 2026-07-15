import subprocess
import sys
import time
import threading
import uuid
import shlex
import socket
import datetime as dt
import os
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends
from pydantic import BaseModel, Field
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlmodel import Session, select
from pyxllib.prog.schedule_policy import (
    RESULT_FAILURE,
    RESULT_SUCCESS,
    apply_schedule_result,
    compute_next_trigger_at,
    initialize_schedule_state,
)

from backend.api.websocket_manager import manager as ws_manager
from backend.core.access.auth import verify_api_token
from backend.core.devices.device import BaseDevice, device_manager, TaskStatus
from backend.core.services.policy import is_legacy_codeyun_service
from backend.db import engine
from backend.models import Task as TaskModel

import asyncio

router = APIRouter()

_status_broadcaster_task: Optional[asyncio.Task] = None
DEFAULT_STALE_SCHEDULED_TASK_SECONDS = 12 * 60 * 60
RUNNING_TASK_SCAN_CACHE_TTL_SECONDS = 1.0
RUNNING_TASK_DEEP_SCAN_CACHE_TTL_SECONDS = 10.0


def _get_scheduled_task_misfire_grace_seconds() -> int:
    try:
        return max(1, int(os.environ.get("CODEYUN_TASK_SCHEDULE_MISFIRE_GRACE_SECONDS", "60")))
    except ValueError:
        return 60


SCHEDULED_TASK_MISFIRE_GRACE_SECONDS = _get_scheduled_task_misfire_grace_seconds()
SCHEDULED_TASK_JOB_KWARGS = {
    "misfire_grace_time": SCHEDULED_TASK_MISFIRE_GRACE_SECONDS,
    "coalesce": True,
    "max_instances": 1,
}


def _service_runtime_kind(value: str | None) -> str:
    normalized = str(value or "service").strip().lower()
    if normalized != "service":
        raise HTTPException(
            status_code=400,
            detail="命令条目只用于独立服务；作业必须注册为后端进程内 JobDefinition",
        )
    return "service"


def _is_service_task(task: TaskModel) -> bool:
    return str(task.runtime_kind or "service").strip().lower() == "service"


def _task_status_projection(task: TaskModel, status: TaskStatus) -> Dict[str, Any]:
    result = task.model_dump()
    result["status"] = status.model_dump()
    result["schedule_status"] = {
        "next_run_at": task.next_run_at,
        "configured": bool(task.schedule_policy or task.schedule),
    }
    return result


async def start_task_manager_services():
    global _status_broadcaster_task

    if _status_broadcaster_task and not _status_broadcaster_task.done():
        return

    loop = asyncio.get_running_loop()

    def thread_safe_log_callback(task_id, line):
        try:
            asyncio.run_coroutine_threadsafe(ws_manager.broadcast_log(task_id, line), loop)
        except Exception:
            pass

    try:
        local_id = task_manager._get_local_device_id()
        device = device_manager.get_device(local_id)
        if device:
            device.set_log_callback(thread_safe_log_callback)
    except Exception:
        pass

    task_manager.initialize_runtime_state(restore_timeouts=True)
    _status_broadcaster_task = asyncio.create_task(status_broadcaster())

async def stop_task_manager_services():
    global _status_broadcaster_task

    if _status_broadcaster_task:
        _status_broadcaster_task.cancel()
        _status_broadcaster_task = None

async def status_broadcaster():
    while True:
        try:
            # Broadcast task list if anyone is watching
            if "task_list" in ws_manager.rooms and ws_manager.rooms["task_list"]:
                await task_manager.broadcast_status()
            
            await asyncio.sleep(2) # 2s interval
        except Exception as e:
            print(f"Broadcaster error: {e}")
            await asyncio.sleep(5)

# --- API Models ---

class CreateTaskRequest(BaseModel):
    name: str
    command: str
    cwd: Optional[str] = None
    description: Optional[str] = None
    device_id: Optional[str] = Field(default_factory=socket.gethostname)
    runtime_kind: Optional[str] = "service"
    schedule: Optional[str] = None
    schedule_policy: Optional[Dict[str, Any]] = None
    next_run_at: Optional[str] = None
    timeout: Optional[int] = None

class UpdateTaskRequest(BaseModel):
    name: Optional[str] = None
    command: Optional[str] = None
    cwd: Optional[str] = None
    description: Optional[str] = None
    device_id: Optional[str] = None
    runtime_kind: Optional[str] = None
    schedule: Optional[str] = None
    schedule_policy: Optional[Dict[str, Any]] = None
    next_run_at: Optional[str] = None
    timeout: Optional[int] = None

# --- Manager ---

class TaskManager:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
        self._runtime_state_initialized = False
        self._last_running_task_scan_at = 0.0
        self._last_running_task_deep_scan_at = 0.0
        self._running_task_scan_lock = threading.Lock()

    def initialize_runtime_state(self, *, restore_timeouts: bool = False):
        if self._runtime_state_initialized:
            return

        self.scan_running_tasks(restore_timeouts=restore_timeouts)
        self.load_schedules()
        self._runtime_state_initialized = True

    def _get_local_device_id(self) -> str:
        return device_manager.get_local_device_id()

    def _invalidate_running_task_scan_cache(self) -> None:
        with self._running_task_scan_lock:
            self._last_running_task_scan_at = 0.0
            self._last_running_task_deep_scan_at = 0.0

    def _mark_running_task_scan_at(self, scanned_at: float) -> None:
        with self._running_task_scan_lock:
            self._last_running_task_scan_at = scanned_at

    def _mark_running_task_deep_scan_at(self, scanned_at: float) -> None:
        with self._running_task_scan_lock:
            self._last_running_task_deep_scan_at = scanned_at

    def _should_skip_running_task_scan(self, *, now: float, restore_timeouts: bool) -> bool:
        if restore_timeouts:
            return False
        with self._running_task_scan_lock:
            return (
                self._last_running_task_scan_at > 0
                and now - self._last_running_task_scan_at <= RUNNING_TASK_SCAN_CACHE_TTL_SECONDS
            )

    def _should_run_running_task_deep_scan(self, *, now: float, restore_timeouts: bool) -> bool:
        if restore_timeouts:
            return True
        with self._running_task_scan_lock:
            return (
                self._last_running_task_deep_scan_at <= 0
                or now - self._last_running_task_deep_scan_at > RUNNING_TASK_DEEP_SCAN_CACHE_TTL_SECONDS
            )

    def load_schedules(self):
        with Session(engine) as session:
            tasks = session.exec(select(TaskModel)).all()
            for task in tasks:
                if not _is_service_task(task):
                    continue
                if task.next_run_at or task.schedule_policy or task.schedule:
                    self.update_schedule(task.id, task.schedule, task.schedule_policy)

    def _get_stale_scheduled_runtime_seconds(self, task: TaskModel) -> int:
        if task.timeout and task.timeout > 0:
            return task.timeout
        return DEFAULT_STALE_SCHEDULED_TASK_SECONDS

    def _stop_stale_scheduled_task(
        self,
        task: TaskModel,
        device: BaseDevice,
        *,
        reason: str,
    ) -> bool:
        if not (task.schedule or task.schedule_policy):
            return False

        status = device.get_task_status(task.id)
        if not status.running or not status.started_at:
            return False

        elapsed = time.time() - status.started_at
        stale_after = self._get_stale_scheduled_runtime_seconds(task)
        if elapsed < stale_after:
            return False

        print(
            f"Stopping stale scheduled task {task.id} ({task.name}) before {reason}: "
            f"elapsed={elapsed:.0f}s stale_after={stale_after}s pid={status.pid}"
        )
        try:
            result = device.stop_task(task.id)
        except Exception as e:
            print(f"Failed to stop stale scheduled task {task.id}: {e}")
            return False

        print(f"Stale scheduled task {task.id} stop result: {result}")
        return result.get("status") in {"stopped", "not_running"}

    def clear_schedule(self, task_id: str) -> None:
        job = self.scheduler.get_job(task_id)
        if job:
            self.scheduler.remove_job(task_id)

    def _parse_schedule_run_date(self, value: Any) -> dt.datetime:
        if isinstance(value, dt.datetime):
            return value
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1]
        return dt.datetime.fromisoformat(text)

    def _format_next_run_at(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        try:
            parsed = self._parse_schedule_run_date(value)
        except Exception as exc:
            raise ValueError(f"Invalid next_run_at: {value}") from exc
        return parsed.replace(microsecond=0).isoformat()

    def _is_next_run_at_stale(self, value: Any) -> bool:
        if not value:
            return False
        run_at = self._parse_schedule_run_date(value)
        timezone = run_at.tzinfo or getattr(self.scheduler, "timezone", None)
        now = dt.datetime.now(timezone) if timezone else dt.datetime.now()
        if run_at.tzinfo is None and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        return (now - run_at).total_seconds() > SCHEDULED_TASK_MISFIRE_GRACE_SECONDS

    def _install_next_run_job(self, task_id: str, next_run_at: Optional[str]) -> None:
        self.clear_schedule(task_id)
        if not next_run_at:
            return
        self.scheduler.add_job(
            self.trigger_scheduled_task,
            DateTrigger(run_date=self._parse_schedule_run_date(next_run_at)),
            id=task_id,
            args=[task_id],
            replace_existing=True,
            **SCHEDULED_TASK_JOB_KWARGS,
        )

    def _reap_stale_scheduled_tasks(self, device: BaseDevice, tasks: List[TaskModel], *, reason: str):
        for task in tasks:
            self._stop_stale_scheduled_task(task, device, reason=reason)

    def _ensure_policy_schedule_state(self, session: Session, task: TaskModel, *, force: bool = False) -> Optional[str]:
        if not task.schedule_policy:
            return None
        state = initialize_schedule_state(
            task.schedule_policy,
            task.schedule_state or {},
            force=force,
        )
        task.schedule_state = state
        session.add(task)
        session.commit()
        return state.get("next_trigger_at")

    def _compute_cron_next_run_at(self, cron_expression: str) -> Optional[str]:
        timezone = getattr(self.scheduler, "timezone", None)
        trigger = CronTrigger.from_crontab(cron_expression, timezone=timezone)
        now = dt.datetime.now(timezone) if timezone else dt.datetime.now()
        next_run_at = trigger.get_next_fire_time(None, now)
        return self._format_next_run_at(next_run_at) if next_run_at else None

    def _compute_rule_next_run_at(
        self,
        session: Session,
        task: TaskModel,
        *,
        result: Optional[str] = None,
        force: bool = False,
    ) -> Optional[str]:
        if task.schedule_policy:
            if result is None:
                next_run_at = self._ensure_policy_schedule_state(session, task, force=force)
            else:
                return self._apply_schedule_result_next_run_at(session, task, result)
            return self._format_next_run_at(next_run_at) if next_run_at else None
        if task.schedule:
            return self._compute_cron_next_run_at(task.schedule)
        return None

    def _apply_schedule_result_next_run_at(
        self,
        session: Session,
        task: TaskModel,
        result: str,
    ) -> Optional[str]:
        if not task.schedule_policy:
            return None
        task.schedule_state = apply_schedule_result(
            task.schedule_policy,
            task.schedule_state or {},
            result=result,
        )
        session.add(task)
        session.commit()
        next_run_at = (task.schedule_state or {}).get("next_trigger_at")
        return self._format_next_run_at(next_run_at) if next_run_at else None

    def _set_task_next_run_at(
        self,
        session: Session,
        task: TaskModel,
        next_run_at: Any,
        *,
        sync_schedule_state: bool = True,
    ) -> Optional[str]:
        formatted = self._format_next_run_at(next_run_at)
        task.next_run_at = formatted
        if sync_schedule_state and isinstance(task.schedule_state, dict):
            state = dict(task.schedule_state or {})
            state["next_trigger_at"] = formatted
            task.schedule_state = state
        session.add(task)
        session.commit()
        return formatted

    def set_next_run_at(self, task_id: str, next_run_at: Any) -> Optional[str]:
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            formatted = self._set_task_next_run_at(session, task, next_run_at)
        self._install_next_run_job(task_id, formatted)
        return formatted

    def _restore_missing_task_schedule(
        self,
        task_id: str,
        cron_expression: Optional[str],
        schedule_policy: Optional[Dict[str, Any]],
    ) -> bool:
        if schedule_policy:
            computed = compute_next_trigger_at(schedule_policy)
            next_run_at = self._format_next_run_at(computed) if computed else None
            self._install_next_run_job(task_id, next_run_at)
            return True
        if cron_expression:
            next_run_at = self._compute_cron_next_run_at(cron_expression)
            self._install_next_run_job(task_id, next_run_at)
            return True
        self.clear_schedule(task_id)
        return True

    def update_schedule(
        self,
        task_id: str,
        cron_expression: Optional[str] = None,
        schedule_policy: Optional[Dict[str, Any]] = None,
        *,
        reset_state: bool = False,
    ):
        try:
            with Session(engine) as session:
                task = session.get(TaskModel, task_id)
                if not task:
                    if self._restore_missing_task_schedule(task_id, cron_expression, schedule_policy):
                        return
                if schedule_policy is not None:
                    task.schedule_policy = schedule_policy
                if cron_expression is not None:
                    task.schedule = cron_expression

                if reset_state:
                    task.schedule_state = {}
                    next_run_at = self._compute_rule_next_run_at(session, task, force=True)
                    self._set_task_next_run_at(session, task, next_run_at)
                elif task.next_run_at:
                    if self._is_next_run_at_stale(task.next_run_at):
                        next_run_at = self._compute_rule_next_run_at(session, task, force=True)
                        self._set_task_next_run_at(session, task, next_run_at)
                    else:
                        next_run_at = self._format_next_run_at(task.next_run_at)
                else:
                    next_run_at = self._compute_rule_next_run_at(session, task)
                    self._set_task_next_run_at(session, task, next_run_at)

            self._install_next_run_job(task_id, next_run_at)
            if next_run_at:
                print(f"Scheduled task {task_id} next_run_at: {next_run_at}")
        except Exception as e:
            self.clear_schedule(task_id)
            print(f"Failed to schedule task {task_id}: {e}")

    def _scheduled_action_for_task(self, task: TaskModel) -> str:
        configured = (task.schedule_policy or {}).get("action") or {}
        action = str(configured.get("type") or "").strip().lower()
        if action in {"start", "restart", "ensure_running", "stop"}:
            return action
        return "restart"

    def _mark_scheduled_task_triggered(self, task_id: str) -> None:
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                return
            now = time.time()
            if task.schedule_policy:
                state = dict(task.schedule_state or {})
                state["last_triggered_at"] = now
                state["last_trigger_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
                state["next_trigger_at"] = None
                task.schedule_state = state
            task.next_run_at = None
            session.add(task)
            session.commit()

    def _result_next_run_at(self, result: Any) -> Any:
        if isinstance(result, dict):
            return result.get("next_run_at") or result.get("next_trigger_at")
        return getattr(result, "next_run_at", None)

    def _finish_scheduled_task(self, task_id: str, result: str, *, next_run_at: Any = None) -> None:
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                return
            if next_run_at is not None:
                formatted = self._set_task_next_run_at(session, task, next_run_at)
            else:
                formatted = self._compute_rule_next_run_at(session, task, result=result)
                self._set_task_next_run_at(session, task, formatted)
        self._install_next_run_job(task_id, formatted)

    def _reset_interval_schedule_after_manual_trigger(self, task_id: str) -> None:
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task or not task.schedule_policy:
                return
            trigger = task.schedule_policy.get("trigger") or {}
            trigger_type = str(trigger.get("type") or "").strip().lower()
            if trigger_type != "interval":
                return
            next_run_at = self._apply_schedule_result_next_run_at(session, task, RESULT_SUCCESS)
            formatted = self._set_task_next_run_at(
                session,
                task,
                next_run_at,
            )
        self._install_next_run_job(task_id, formatted)

    def trigger_scheduled_task(self, task_id: str):
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            action = self._scheduled_action_for_task(task)

        self._mark_scheduled_task_triggered(task_id)

        try:
            if action == "stop":
                result = self.stop_task(task_id)
            elif action == "ensure_running":
                status = self.get_task_status(task_id)
                result = {"status": "already_running", "pid": status.pid} if status.running else self.start_task(task_id, trigger_reason="scheduled")
            elif action == "restart":
                result = self.start_task(task_id, replace_running=True, trigger_reason="scheduled")
            elif action == "start":
                result = self.start_task(
                    task_id,
                    replace_running=False,
                    trigger_reason="scheduled",
                )
            else:
                raise HTTPException(status_code=400, detail="服务调度只支持 start/restart/ensure_running/stop")
            self._finish_scheduled_task(
                task_id,
                RESULT_SUCCESS,
                next_run_at=self._result_next_run_at(result),
            )
            return result
        except Exception:
            self._finish_scheduled_task(task_id, RESULT_FAILURE)
            raise

    def scan_running_tasks(self, restore_timeouts: bool = False):
        scanned_at = time.monotonic()
        if self._should_skip_running_task_scan(now=scanned_at, restore_timeouts=restore_timeouts):
            return
        should_run_deep_scan = self._should_run_running_task_deep_scan(
            now=scanned_at,
            restore_timeouts=restore_timeouts,
        )

        # Scan local tasks
        local_id = self._get_local_device_id()
        
        with Session(engine) as session:
            # Get tasks for local device
            # Note: We need to filter by device_id.
            # Assuming TaskModel has device_id.
            stmt = select(TaskModel).where(TaskModel.device_id == local_id)
            local_tasks = [
                task
                for task in session.exec(stmt).all()
                if _is_service_task(task) and not is_legacy_codeyun_service(task)
            ]

        device = device_manager.get_device(local_id)
        if device:
            device.scan_running_tasks(local_tasks, deep_scan=False)
            service_tasks = [task for task in local_tasks if not device.get_task_status(task.id).running]
            if service_tasks and should_run_deep_scan:
                device.scan_running_tasks(service_tasks, deep_scan=True)
                self._mark_running_task_deep_scan_at(scanned_at)

            if restore_timeouts:
                self._reap_stale_scheduled_tasks(device, local_tasks, reason="startup scan")
             
            # Only restore timeouts on startup (explicit request)
            if restore_timeouts:
                if hasattr(device, 'processes') and hasattr(device, '_watch_timeout'):
                     self._restore_timeouts(device, local_tasks)
        self._mark_running_task_scan_at(scanned_at)

    def _restore_timeouts(self, device, tasks):
        import psutil
        import threading
        import time

        print("Restoring timeout watchers for running tasks...")
        with device.lock:
            for task in tasks:
                if not task.timeout or task.timeout <= 0:
                    continue
                
                # Check if task is running
                if task.id in device.processes:
                    proc = device.processes[task.id]
                    try:
                        if not proc.is_running():
                            continue
                            
                        # Calculate remaining time
                        create_time = proc.create_time()
                        elapsed = time.time() - create_time
                        remaining = task.timeout - elapsed
                        
                        if remaining <= 0:
                            print(f"Task {task.id} expired during downtime (overdue by {-remaining:.1f}s). Stopping now.")
                            device.stop_task(task.id)
                        else:
                            print(f"Restoring watcher for Task {task.id} (PID {proc.pid}). Remaining: {remaining:.1f}s")
                            watcher = threading.Thread(
                                target=device._watch_timeout, 
                                args=(proc, remaining, task.id)
                            )
                            watcher.daemon = True
                            watcher.start()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

    async def broadcast_status(self):
        """Broadcast current status to all WS clients"""
        self.scan_running_tasks()
        
        with Session(engine) as session:
             # Sort by order, then created_at
             stmt = select(TaskModel).order_by(TaskModel.order, TaskModel.created_at)
             tasks = [task for task in session.exec(stmt).all() if _is_service_task(task)]
        
        results = []
        for t in tasks:
            status = self.get_task_status(t.id)
            results.append(_task_status_projection(t, status))
            
        await ws_manager.broadcast("task_list", results)

    def start_task(
        self,
        task_id: str,
        *,
        replace_running: bool = False,
        trigger_reason: str = "manual",
    ):
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                 raise HTTPException(status_code=404, detail="Task not found")
            _service_runtime_kind(task.runtime_kind)
            
            # Assuming task is local for now, or check task.device_id
            target_device_id = task.device_id
            
        device = device_manager.get_device(target_device_id)
        if not device:
            raise HTTPException(status_code=500, detail=f"Device {target_device_id} unavailable")

        if replace_running:
            status = device.get_task_status(task.id)
            if status.running:
                device.stop_task(task.id)
        else:
            self._stop_stale_scheduled_task(task, device, reason=trigger_reason or "task start")
             
        try:
            # Pass command and env from DB
            result = device.start_task(task.id, task.command, task.cwd, env={}, timeout=task.timeout)
            self._invalidate_running_task_scan_cache()
            if trigger_reason != "scheduled" and result.get("status") != "already_running":
                self._reset_interval_schedule_after_manual_trigger(task_id)
            if result.get("status") == "already_running":
                status = device.get_task_status(task.id)
                elapsed = None
                if status.started_at:
                    elapsed = time.time() - status.started_at
                print(
                    f"Task {task_id} skipped: already running "
                    f"(PID: {result.get('pid')} elapsed={elapsed:.0f}s)" if elapsed is not None
                    else f"Task {task_id} skipped: already running (PID: {result.get('pid')})"
                )
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def stop_task(self, task_id: str):
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                 return {"status": "not_found"}
            target_device_id = task.device_id

        device = device_manager.get_device(target_device_id)
        if not device:
             return {"status": "device_not_found"}
        result = device.stop_task(task_id)
        self._invalidate_running_task_scan_cache()
        return result

    def get_task_status(self, task_id: str):
        # We need to know which device this task belongs to
        # But get_task_status is often called in loop.
        # Optimization: The caller might already know the device?
        # For now, let's look it up.
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                 return TaskStatus(id=task_id, running=False, message="Task not found in DB")
            target_device_id = task.device_id

        device = device_manager.get_device(target_device_id)
        if not device:
            return TaskStatus(id=task_id, running=False, message="Device unavailable")
        return device.get_task_status(task_id)

    def get_logs(self, task_id: str, lines: int = 50):
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                 return ["Task not found"]
            target_device_id = task.device_id

        device = device_manager.get_device(target_device_id)
        if not device:
            return ["Device unavailable"]
        return device.get_logs(task_id, lines)
    
    def reorder_tasks(self, task_ids: List[str]):
        with Session(engine) as session:
            for idx, t_id in enumerate(task_ids):
                task = session.get(TaskModel, t_id)
                if task:
                    task.order = idx
                    session.add(task)
            session.commit()

    def find_related_processes(self, task_id: str) -> List[Dict[str, Any]]:
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                return []
            target_device_id = task.device_id
            
        device = device_manager.get_device(target_device_id)
        if not device:
            return []
            
        return device.find_related_processes(task.command)

    def kill_process(self, pid: int) -> bool:
        # Default to local device for generic kill?
        # Or we need to know which device.
        # The API /process/kill doesn't specify device.
        # Assuming local.
        local_id = self._get_local_device_id()
        device = device_manager.get_device(local_id)
        if not device:
            return False
        return device.kill_process_by_pid(pid)

    def associate_process(self, task_id: str, pid: int):
        with Session(engine) as session:
            task = session.get(TaskModel, task_id)
            if not task:
                 raise HTTPException(status_code=404, detail="Task not found")
            target_device_id = task.device_id
            
        device = device_manager.get_device(target_device_id)
        if not device:
             raise HTTPException(status_code=500, detail="Device unavailable")
             
        result = device.associate_process(task_id, pid)
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("message"))
        self._invalidate_running_task_scan_cache()
            
        # Update Task Config in DB
        with Session(engine) as session:
            task = session.get(TaskModel, task_id) # Re-fetch to be safe
            if task:
                # Update command
                cmd_list = result.get("cmdline")
                if cmd_list:
                    if sys.platform == 'win32':
                        task.command = subprocess.list2cmdline(cmd_list)
                    else:
                        task.command = shlex.join(cmd_list)
                
                # Update CWD
                cwd = result.get("cwd")
                if cwd:
                    task.cwd = cwd
                elif "cwd" in result and result["cwd"] is None:
                     task.cwd = "Unknown"
                
                session.add(task)
                session.commit()
            
        return result

task_manager = TaskManager()

# --- Routes ---

# --- Task Routes ---

@router.get("/")
def list_tasks(
    token_device: BaseDevice = Depends(verify_api_token)
):
    """
    List tasks for the authenticated node.
    """
    requesting_device_id = token_device.id
    local_id = task_manager._get_local_device_id()
    if requesting_device_id == local_id:
        task_manager.scan_running_tasks()
    
    with Session(engine) as session:
        stmt = select(TaskModel).where(TaskModel.device_id == requesting_device_id).order_by(TaskModel.order, TaskModel.created_at)
        tasks = [task for task in session.exec(stmt).all() if _is_service_task(task)]
    
    results = []
    for t in tasks:
        status = task_manager.get_task_status(t.id)
        results.append(_task_status_projection(t, status))
        
    return results

@router.get("/list")
def list_tasks_deprecated(
    token_device: BaseDevice = Depends(verify_api_token)
):
    return list_tasks(token_device)

@router.post("/create")
def create_task(
    req: CreateTaskRequest,
    token_device: BaseDevice = Depends(verify_api_token)
):
    target_device_id = token_device.device_id

    with Session(engine) as session:
        last_task = session.exec(
            select(TaskModel)
            .where(TaskModel.device_id == target_device_id)
            .order_by(TaskModel.order.desc(), TaskModel.created_at.desc())
        ).first()
        next_order = 0 if not last_task or last_task.order is None else last_task.order + 1

        new_task = TaskModel(
            id=str(uuid.uuid4()),
            name=req.name,
            command=req.command,
            cwd=req.cwd,
            description=req.description,
            device_id=target_device_id,
            runtime_kind=_service_runtime_kind(req.runtime_kind),
            schedule=req.schedule,
            schedule_policy=req.schedule_policy,
            next_run_at=task_manager._format_next_run_at(req.next_run_at),
            timeout=req.timeout,
            created_at=time.time(),
            order=next_order,
        )
        session.add(new_task)
        session.commit()
        session.refresh(new_task)
        
    if req.next_run_at:
        task_manager.set_next_run_at(new_task.id, req.next_run_at)
    elif req.schedule_policy or req.schedule:
        task_manager.update_schedule(new_task.id, req.schedule, req.schedule_policy, reset_state=True)
    
    task_manager.scan_running_tasks()
    return new_task

@router.delete("/{task_id}")
def delete_task(task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if task:
            task_manager.clear_schedule(task_id)
            task_manager.stop_task(task_id)
            session.delete(task)
            session.commit()
            return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Task not found")

@router.post("/{task_id}/start")
def start_task_route(task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

    return task_manager.start_task(task_id)

@router.post("/{task_id}/stop")
def stop_task_route(task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

    return task_manager.stop_task(task_id)

@router.post("/{task_id}/update")
def update_task_route(task_id: str, req: UpdateTaskRequest, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if task:
            if req.name is not None: task.name = req.name
            if req.command is not None: task.command = req.command
            if req.cwd is not None: task.cwd = req.cwd
            if req.description is not None: task.description = req.description
            if req.runtime_kind is not None: task.runtime_kind = _service_runtime_kind(req.runtime_kind)
            if req.schedule is not None:
                task.schedule = req.schedule
            if "schedule_policy" in req.model_fields_set:
                task.schedule_policy = req.schedule_policy
                task.schedule_state = {}
            if "next_run_at" in req.model_fields_set:
                task.next_run_at = task_manager._format_next_run_at(req.next_run_at)
            if req.timeout is not None:
                task.timeout = req.timeout
            
            session.add(task)
            session.commit()
            session.refresh(task)
            if "next_run_at" in req.model_fields_set:
                task_manager.set_next_run_at(task_id, req.next_run_at)
            elif req.schedule is not None or "schedule_policy" in req.model_fields_set:
                task_manager.update_schedule(
                    task_id,
                    task.schedule,
                    task.schedule_policy,
                    reset_state="schedule_policy" in req.model_fields_set,
                )
            return task
    raise HTTPException(status_code=404, detail="Task not found")

@router.post("/reorder")
def reorder_tasks_route(task_ids: List[str], token_device: BaseDevice = Depends(verify_api_token)):
    task_manager.reorder_tasks(task_ids)
    return {"status": "reordered"}

@router.get("/{task_id}")
def get_task_details(task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if task:
            status = task_manager.get_task_status(task_id)
            return _task_status_projection(task, status)
    raise HTTPException(status_code=404, detail="Task not found")

@router.get("/{task_id}/logs")
def get_task_logs(task_id: str, n: int = 500, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

    logs = task_manager.get_logs(task_id, n)
    return {"logs": logs}

@router.get("/{task_id}/related_processes")
def get_related_processes(task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

    return task_manager.find_related_processes(task_id)

@router.post("/process/kill")
def kill_process_route(req: Dict[str, int], token_device: BaseDevice = Depends(verify_api_token)):
    pid = req.get("pid")
    if not pid:
        raise HTTPException(status_code=400, detail="PID required")
    success = task_manager.kill_process(pid)
    if success:
        return {"status": "killed"}
    raise HTTPException(status_code=500, detail="Failed to kill process")

@router.post("/{task_id}/associate")
def associate_process_route(task_id: str, req: Dict[str, int], token_device: BaseDevice = Depends(verify_api_token)):
    with Session(engine) as session:
        task = session.get(TaskModel, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task.device_id != token_device.device_id:
            raise HTTPException(status_code=403, detail="Cannot access task of another device")

    pid = req.get("pid")
    if not pid:
        raise HTTPException(status_code=400, detail="PID required")
    return task_manager.associate_process(task_id, pid)

# --- WebSocket Endpoint ---

@router.websocket("/ws/logs/{task_id}")
async def websocket_logs(websocket: WebSocket, task_id: str, token_device: BaseDevice = Depends(verify_api_token)):
    room = f"task_logs:{task_id}"
    await ws_manager.connect(websocket, room)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, room)
    except Exception as e:
        print(f"WS error: {e}")
        ws_manager.disconnect(websocket, room)

@router.websocket("/ws/tasks")
async def websocket_tasks(websocket: WebSocket, token_device: BaseDevice = Depends(verify_api_token)):
    room = "task_list"
    await ws_manager.connect(websocket, room)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, room)
