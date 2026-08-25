from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable

from pyxllib.prog import process_runtime
from sqlmodel import Session, select

from backend.api.task_manager import task_manager
from backend.core.devices.device import device_manager, get_device_id
from backend.db import engine
from backend.models import Task as TaskModel


logger = logging.getLogger(__name__)
DEFAULT_SERVICE_CHECK_INTERVAL_SECONDS = 300.0
CRITICAL_LOCAL_COMMAND_SERVICE_NAMES = {"frpc", "nginx"}


def _find_running_service_by_executable(command: str) -> dict[str, Any] | None:
    try:
        args = process_runtime.parse_cmdline(command)
    except Exception:
        args = []
    if not args:
        return None
    executable = Path(str(args[0]).strip('"')).expanduser()
    if not executable.is_absolute():
        return None
    executable_key = os.path.normcase(os.path.abspath(os.fspath(executable)))
    for proc in process_runtime.process_candidates_by_name({executable.name}):
        try:
            proc_exe = proc.exe()
        except Exception:
            continue
        if os.path.normcase(os.path.abspath(proc_exe)) == executable_key:
            return {"pid": proc.pid, "matched_by": "executable", "exe": proc_exe}
    return None


def ensure_local_critical_command_services() -> dict[str, Any]:
    """Recover explicitly configured always-on services outside the job queue."""

    local_device_id = get_device_id()
    started: list[dict[str, Any]] = []
    already_running: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    task_manager.scan_running_tasks()
    with Session(engine) as session:
        stmt = (
            select(TaskModel)
            .where(TaskModel.device_id == local_device_id)
            .order_by(TaskModel.order, TaskModel.created_at)
        )
        services = [
            task
            for task in session.exec(stmt).all()
            if str(task.name or "").strip().lower() in CRITICAL_LOCAL_COMMAND_SERVICE_NAMES
            and str(task.runtime_kind or "service").strip().lower() == "service"
        ]

    for service in services:
        status = task_manager.get_task_status(service.id)
        item = {"id": service.id, "name": service.name, "pid": status.pid}
        if status.running:
            already_running.append(item)
            continue
        executable_match = _find_running_service_by_executable(service.command)
        if executable_match:
            device = device_manager.get_device(local_device_id)
            if device is not None:
                try:
                    device.associate_process(service.id, int(executable_match["pid"]))
                except Exception:
                    pass
            already_running.append({**item, **executable_match})
            continue
        try:
            result = task_manager.start_task(
                service.id,
                replace_running=False,
                trigger_reason="service_monitor",
            )
            started.append({"id": service.id, "name": service.name, "result": result})
        except Exception as exc:
            errors.append({"id": service.id, "name": service.name, "error": str(exc)})
    return {
        "status": "error" if errors else ("started" if started else "ok"),
        "started": started,
        "already_running": already_running,
        "errors": errors,
    }


def ensure_local_monitored_services() -> dict[str, Any]:
    """Recover all CodeYun-owned always-on services through one monitor."""

    results: dict[str, Any] = {
        "critical-command-services": ensure_local_critical_command_services(),
    }
    # Import lazily so the generic service monitor stays independent from the
    # runtime-management module that also consumes it during application boot.
    from backend.core.attendance.behavior_tree_service import (
        ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY,
        ensure_attendance_behavior_tree_service,
        is_attendance_behavior_tree_service_enabled,
    )

    if is_attendance_behavior_tree_service_enabled():
        try:
            results[ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY] = ensure_attendance_behavior_tree_service()
        except Exception as exc:
            results[ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY] = {"status": "error", "error": str(exc)}
            logger.exception("Attendance behavior tree monitor check failed")
    return results


class ServiceMonitor:
    """Observe and recover opted-in services without entering the job queue."""

    def __init__(
        self,
        ensure_services: Callable[[], dict[str, Any]],
        *,
        interval_seconds: float = DEFAULT_SERVICE_CHECK_INTERVAL_SECONDS,
    ) -> None:
        self._ensure_services = ensure_services
        self._interval_seconds = max(1.0, float(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="codeyun-service-monitor",
            daemon=True,
        )
        self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def check_now(self) -> dict[str, Any]:
        return self._ensure_services()

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self.check_now()
            except Exception:
                logger.exception("Service monitor check failed")


_service_monitor: ServiceMonitor | None = None


def init_service_monitor() -> None:
    global _service_monitor
    if _service_monitor is None:
        _service_monitor = ServiceMonitor(ensure_local_monitored_services)
    _service_monitor.start()


def shutdown_service_monitor() -> None:
    if _service_monitor is not None:
        _service_monitor.shutdown()
