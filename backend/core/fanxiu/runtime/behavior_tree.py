from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import psutil
from filelock import FileLock, Timeout
from pyxllib.prog import (
    acquire_json_lease,
    clear_job_queue,
    clear_stale_json_lease,
    filter_status_logs,
    is_json_lease_active,
    owner_active_for_other_process,
    read_json_lease,
    read_json_object_status,
    release_json_lease,
    should_enqueue_local_run,
    write_json_command,
)

from backend.core.fanxiu.data_annotation.state import (
    append_data_annotation_runtime_log_once,
    data_annotation_runtime_owner_message,
    is_data_annotation_runtime_live_empty,
    normalize_data_annotation_runtime_display,
    normalize_data_annotation_runtime_logs_for_display,
    normalize_data_annotation_runtime_guard_items,
    persist_data_annotation_runtime_status,
    read_data_annotation_runtime_status,
)
from backend.core.fanxiu.data_annotation.jobs import (
    list_fanxiu_data_annotation_task_cell_definitions,
    parse_data_annotation_scene_id,
)
from backend.core.fanxiu.data_annotation.debug_eval import register_fanxiu_data_annotation_debug_eval_job
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.runner import (
    create_fanxiu_runtime_runner,
    get_fanxiu_runtime_runner_class,
    register_fanxiu_runtime_runner_class,
)
from backend.core.runtime.process_launcher import popen_python_script_service
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.data_annotation.storage import (
    DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID,
    data_annotation_asset_tree_path,
    fanxiu_data_annotation_dir,
)
from backend.core.settings import ROOT_DIR, get_settings


DEFAULT_FANXIU_ENTRY_ID = DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID
_RUNTIME_RUNNER: Any | None = None
FANXIU_EMBEDDED_SERVICE_ENV = "CODEYUN_FANXIU_EMBEDDED_BEHAVIOR_TREE_SERVICE"
FANXIU_EXTERNAL_SERVICE_ENV = "CODEYUN_FANXIU_EXTERNAL_BEHAVIOR_TREE_SERVICE"


@dataclass(frozen=True)
class FanxiuLocalRunRequest:
    task_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID
    isolate_jobs: bool = True
    tick_seconds: float = 0.2


@dataclass(frozen=True)
class FanxiuLocalServiceRequest:
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID
    tick_seconds: float = 1.0
    duration_seconds: float = 0.0


def _local_fanxiu_entry(entry_id: str) -> Any:
    return SimpleNamespace(
        entry_id=entry_id,
        user_id=0,
        device_id="local",
        name="codepc_mf",
        mode="local",
        token="",
        is_active=True,
        server_url="",
    )


def resolve_fanxiu_entry(entry_id: str = DEFAULT_FANXIU_ENTRY_ID) -> Any:
    resolved_entry_id = str(entry_id or DEFAULT_FANXIU_ENTRY_ID)
    try:
        from sqlmodel import Session

        from backend.db import engine
        from backend.models import UserDevice
    except Exception:
        return _local_fanxiu_entry(resolved_entry_id)
    try:
        with Session(engine) as session:
            entry = session.get(UserDevice, resolved_entry_id)
            if entry is not None:
                return entry
    except Exception:
        return _local_fanxiu_entry(resolved_entry_id)
    return UserDevice(
        entry_id=resolved_entry_id,
        user_id=0,
        device_id="local",
        name="codepc_mf",
        mode="local",
        token="",
        is_active=True,
    )


def fanxiu_data_annotation_runtime_dir() -> Path:
    path = fanxiu_data_annotation_dir() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fanxiu_data_annotation_dir() -> Path:
    return get_settings().data_dir / "fanxiu" / "data-annotation"


def fanxiu_data_annotation_runtime_state_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "runtime_state.json"


def fanxiu_data_annotation_world_facts_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "world_facts.json"


def fanxiu_data_annotation_scheduler_state_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "scheduler_tasks.json"


def fanxiu_data_annotation_scheduler_settings_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "scheduler_settings.json"


def fanxiu_data_annotation_task_cell_state_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "manual_jobs.json"


def fanxiu_data_annotation_mail_scan_state_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "mail_scan_state.json"


def fanxiu_job_group_isolation_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "job_group_isolation.json"


def read_fanxiu_job_group_isolation(path: Path | None = None) -> dict[str, Any]:
    isolation_path = path or fanxiu_job_group_isolation_path()
    return read_json_lease(isolation_path, invalid_message="isolation 文件不是 JSON object")


def _fanxiu_job_group_isolation_owner_dead(status: dict[str, Any]) -> bool:
    if not bool(status.get("active")):
        return False
    try:
        pid = int(status.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return bool(pid and not _fanxiu_process_exists(pid))


def fanxiu_behavior_tree_service_owner_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "behavior_tree_service_owner.json"


def fanxiu_behavior_tree_control_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "behavior_tree_control.json"


def fanxiu_behavior_tree_service_start_lock_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "behavior_tree_service_start.lock"


def _fanxiu_process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except Exception:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        if os.name != "nt":
            return False
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
        except Exception:
            return False
        return False
    return True


def _fanxiu_process_matches_service_owner(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        proc = psutil.Process(pid)
        name = str(proc.name() or "").lower()
        cmdline = [str(part) for part in proc.cmdline()]
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return False
    command = " ".join(cmdline).lower().replace("/", "\\")
    if not command:
        return False
    if "python" not in name and "uv" not in name:
        return False
    return "fanxiu_bt.py" in command and re.search(r"(^|\s)service(\s|$)", command) is not None


def _fanxiu_service_processes() -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = int(proc.info.get("pid") or 0)
            name = str(proc.info.get("name") or "")
            cmdline = [str(part) for part in (proc.info.get("cmdline") or [])]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError, TypeError, ValueError):
            continue
        command = " ".join(cmdline).lower().replace("/", "\\")
        if pid != os.getpid() and "fanxiu_bt.py" in command and re.search(r"(^|\s)service(\s|$)", command):
            processes.append({"pid": pid, "name": name, "cmdline": cmdline})
    return processes


def _restart_stuck_external_service_for_pending_jobs(owner: dict[str, Any]) -> dict[str, Any]:
    if not bool(owner.get("active")) or bool(owner.get("stale")):
        return {"restarted": False, "reason": "owner_inactive"}
    try:
        owner_pid = int(owner.get("pid") or 0)
    except (TypeError, ValueError):
        owner_pid = 0
    if owner_pid <= 0 or owner_pid == os.getpid() or not _fanxiu_process_matches_service_owner(owner_pid):
        return {"restarted": False, "reason": "owner_not_external_service", "pid": owner_pid}
    if str(owner.get("step") or "") != "task_running":
        return {"restarted": False, "reason": "owner_not_in_task_running", "step": owner.get("step") or ""}
    jobs = [
        item
        for item in fanxiu_data_annotation_task_cells()
        if str(item.get("status") or "") in {"pending", "queued", "running"}
    ]
    if not jobs:
        return {"restarted": False, "reason": "no_pending_jobs"}
    now_ts = time.time()
    recent_timestamps: list[float] = []
    for value in (owner.get("updated_at"), owner.get("heartbeat_at")):
        try:
            timestamp = float(value or 0)
        except (TypeError, ValueError):
            timestamp = 0.0
        if timestamp > 0:
            recent_timestamps.append(timestamp)
    for job in jobs:
        for value in (job.get("updated_at"), job.get("created_at")):
            try:
                timestamp = float(value or 0)
            except (TypeError, ValueError):
                timestamp = 0.0
            if timestamp > 0:
                recent_timestamps.append(timestamp)
    newest_activity_at = max(recent_timestamps) if recent_timestamps else 0.0
    if newest_activity_at > 0 and now_ts - newest_activity_at < 30.0:
        return {
            "restarted": False,
            "reason": "task_start_grace",
            "pid": owner_pid,
            "job_count": len(jobs),
            "age_seconds": max(0.0, now_ts - newest_activity_at),
        }
    # Read the raw persisted runner state here.  The public status facade
    # deliberately overlays an active external owner's heartbeat and can turn
    # a stopped runner back into service_running/task_running.  Using that
    # facade for recovery makes a dead task look healthy forever after the
    # backend reloads.
    persisted = read_fanxiu_runtime_status()
    persisted_running = bool((persisted or {}).get("running"))
    persisted_status = str((persisted or {}).get("status") or "")
    persisted_phase = str((persisted or {}).get("phase") or "")
    persisted_service_running = bool((persisted or {}).get("service_running"))
    looks_idle = (not persisted_running) and persisted_status in {"", "idle", "success", "stopped", "error"}
    service_state_split = looks_idle or not persisted_service_running
    if not service_state_split:
        return {
            "restarted": False,
            "reason": "runtime_not_idle",
            "status": persisted_status,
            "phase": persisted_phase,
        }
    request_fanxiu_behavior_tree_service_shutdown(
        entry_id=str(owner.get("entry_id") or ""),
        reason="pending_jobs_stuck_task_running",
    )
    deadline = time.time() + 3.0
    while time.time() < deadline and _fanxiu_process_exists(owner_pid):
        time.sleep(0.2)
    terminated = False
    killed = False
    if _fanxiu_process_exists(owner_pid):
        try:
            process = psutil.Process(owner_pid)
            process.terminate()
            terminated = True
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline and _fanxiu_process_exists(owner_pid):
            time.sleep(0.2)
    if _fanxiu_process_exists(owner_pid):
        try:
            process = psutil.Process(owner_pid)
            process.kill()
            killed = True
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline and _fanxiu_process_exists(owner_pid):
            time.sleep(0.2)
    return {
        "restarted": not _fanxiu_process_exists(owner_pid),
        "reason": "pending_jobs_stuck_task_running",
        "pid": owner_pid,
        "terminated": terminated,
        "killed": killed,
        "job_count": len(jobs),
        "status": persisted_status,
        "phase": persisted_phase,
        "service_running": persisted_service_running,
    }


def request_fanxiu_behavior_tree_stop(
    *,
    entry_id: str = "",
    reason: str = "local_cli",
    path: Path | None = None,
) -> dict[str, Any]:
    control_path = path or fanxiu_behavior_tree_control_path()
    return write_json_command(
        control_path,
        command="stop_current_task",
        request_id=uuid.uuid4().hex,
        created_at=time.time(),
        entry_id=str(entry_id or ""),
        reason=str(reason or "local_cli"),
    )


def request_fanxiu_behavior_tree_wake(
    *,
    entry_id: str = "",
    reason: str = "wake",
    path: Path | None = None,
) -> dict[str, Any]:
    control_path = path or fanxiu_behavior_tree_control_path()
    return write_json_command(
        control_path,
        command="wake_service",
        request_id=uuid.uuid4().hex,
        created_at=time.time(),
        entry_id=str(entry_id or ""),
        reason=str(reason or "wake"),
    )


def request_fanxiu_behavior_tree_service_shutdown(
    *,
    entry_id: str = "",
    reason: str = "service_migration",
    path: Path | None = None,
) -> dict[str, Any]:
    control_path = path or fanxiu_behavior_tree_control_path()
    return write_json_command(
        control_path,
        command="shutdown_service",
        request_id=uuid.uuid4().hex,
        created_at=time.time(),
        entry_id=str(entry_id or ""),
        reason=str(reason or "service_migration"),
    )


def restart_fanxiu_behavior_tree_service(
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    timeout_seconds: float = 15.0,
    tick_seconds: float = 1.0,
) -> dict[str, Any]:
    """Gracefully replace the resident Runtime/Jupyter process and wait for its owner."""
    if _current_process_is_fanxiu_service_host():
        raise RuntimeError("不能从 resident service 自身执行 restart")

    resolved_entry_id = str(entry_id or DEFAULT_FANXIU_ENTRY_ID)
    before = read_fanxiu_behavior_tree_service_owner(stale_after_seconds=max(5.0, timeout_seconds))
    old_pid = int(before.get("pid") or 0)
    shutdown_request: dict[str, Any] = {}
    if old_pid and bool(before.get("active")) and not bool(before.get("stale")):
        shutdown_request = request_fanxiu_behavior_tree_service_shutdown(
            entry_id=resolved_entry_id,
            reason="explicit_restart",
        )

    deadline = time.time() + max(1.0, float(timeout_seconds or 15.0))
    while old_pid and _fanxiu_process_exists(old_pid) and time.time() < deadline:
        time.sleep(0.2)

    if old_pid and _fanxiu_process_exists(old_pid):
        return {
            "restarted": False,
            "reason": "shutdown_timeout",
            "old_pid": old_pid,
            "shutdown_request": shutdown_request,
            "owner": read_fanxiu_behavior_tree_service_owner(),
        }

    _clear_stale_fanxiu_behavior_tree_shutdown_request()
    started = _start_external_fanxiu_behavior_tree_service(
        resolved_entry_id,
        tick_seconds=max(0.2, float(tick_seconds or 1.0)),
        wait_seconds=max(1.0, min(10.0, float(timeout_seconds or 15.0))),
    )
    owner = read_fanxiu_behavior_tree_service_owner()
    new_pid = int(owner.get("pid") or started.get("pid") or 0)
    active = bool(owner.get("active")) and not bool(owner.get("stale"))
    return {
        "restarted": active and (not old_pid or new_pid != old_pid),
        "reason": "restarted" if active else str(started.get("reason") or "start_failed"),
        "old_pid": old_pid,
        "new_pid": new_pid,
        "shutdown_request": shutdown_request,
        "start": started,
        "owner": owner,
    }


def _clear_stale_fanxiu_behavior_tree_shutdown_request() -> None:
    control_path = fanxiu_behavior_tree_control_path()
    try:
        payload = json.loads(control_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    if str(payload.get("command") or "") != "shutdown_service":
        return
    try:
        control_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def read_fanxiu_behavior_tree_service_owner(
    path: Path | None = None,
    *,
    stale_after_seconds: float = 120.0,
) -> dict[str, Any]:
    owner_path = path or fanxiu_behavior_tree_service_owner_path()
    status = read_json_object_status(
        owner_path,
        stale_after_seconds=stale_after_seconds,
        invalid_message="owner 文件不是 JSON object",
    )
    try:
        owner_pid = int(status.get("pid") or 0)
    except (TypeError, ValueError):
        owner_pid = 0
    if bool(status.get("active")) and owner_pid and owner_pid != os.getpid() and not _fanxiu_process_exists(owner_pid):
        return {
            **status,
            "active": False,
            "stale": True,
            "error": f"owner 进程不存在：pid={owner_pid}",
        }
    if bool(status.get("active")) and owner_pid and not _fanxiu_process_matches_service_owner(owner_pid):
        return {
            **status,
            "active": False,
            "stale": True,
            "error": f"owner 进程不是凡修常驻服务：pid={owner_pid}",
        }
    return status


def _current_process_is_fanxiu_service_host() -> bool:
    if str(os.environ.get(FANXIU_EMBEDDED_SERVICE_ENV) or "").strip() in {"1", "true", "True"}:
        return True
    command = " ".join(str(part) for part in sys.argv).lower().replace("/", "\\")
    return "fanxiu_bt.py" in command and re.search(r"(^|\s)service(\s|$)", command) is not None


def _external_behavior_tree_service_enabled() -> bool:
    value = str(os.environ.get(FANXIU_EXTERNAL_SERVICE_ENV) or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _start_external_fanxiu_behavior_tree_service(
    entry_id: str,
    *,
    tick_seconds: float = 1.0,
    wait_seconds: float = 5.0,
) -> dict[str, Any]:
    if _current_process_is_fanxiu_service_host():
        owner = read_fanxiu_behavior_tree_service_owner()
        return {"started": False, "reason": "current_process_is_service_host", "owner": owner}
    lock = FileLock(str(fanxiu_behavior_tree_service_start_lock_path()))
    try:
        with lock.acquire(timeout=8.0):
            return _start_external_fanxiu_behavior_tree_service_locked(
                entry_id,
                tick_seconds=tick_seconds,
                wait_seconds=wait_seconds,
            )
    except Timeout:
        owner = read_fanxiu_behavior_tree_service_owner()
        if bool(owner.get("active")) and not bool(owner.get("stale")):
            return {"started": False, "reason": "owner_already_active_after_lock_timeout", "owner": owner}
        return {"started": False, "reason": "service_start_lock_timeout", "owner": owner}


def _start_external_fanxiu_behavior_tree_service_locked(
    entry_id: str,
    *,
    tick_seconds: float = 1.0,
    wait_seconds: float = 5.0,
) -> dict[str, Any]:
    if not _external_behavior_tree_service_enabled():
        return {"started": False, "reason": "external_service_disabled"}
    script_path = ROOT_DIR / "scripts" / "fanxiu_bt.py"
    if not script_path.is_file():
        return {"started": False, "reason": f"service_script_missing:{script_path}"}
    before = read_fanxiu_behavior_tree_service_owner()
    if bool(before.get("active")) and not bool(before.get("stale")):
        return {"started": False, "reason": "owner_already_active", "owner": before}
    existing_services = _fanxiu_service_processes()
    if existing_services:
        owner_missing_or_stale = not bool(before.get("exists")) or bool(before.get("stale"))
        if not owner_missing_or_stale:
            return {"started": False, "reason": "service_process_already_running", "process": existing_services[0], "owner": before}
        request_fanxiu_behavior_tree_service_shutdown(entry_id=entry_id, reason="stale_external_service_takeover")
        deadline = time.time() + 3.0
        while time.time() < deadline:
            existing_services = _fanxiu_service_processes()
            if not existing_services:
                break
            time.sleep(0.2)
        if existing_services:
            stopped: list[int] = []
            for item in existing_services:
                try:
                    process = psutil.Process(int(item.get("pid") or 0))
                    process.terminate()
                    stopped.append(process.pid)
                except Exception:
                    continue
            deadline = time.time() + 3.0
            while time.time() < deadline:
                existing_services = _fanxiu_service_processes()
                if not existing_services:
                    break
                time.sleep(0.2)
            if existing_services:
                return {
                    "started": False,
                    "reason": "stale_service_process_stop_failed",
                    "stopped_pids": stopped,
                    "process": existing_services[0],
                    "owner": read_fanxiu_behavior_tree_service_owner(),
                }
    if bool(before.get("exists")) and before.get("pid"):
        request_fanxiu_behavior_tree_service_shutdown(entry_id=entry_id, reason="external_service_takeover")
        time.sleep(1.0)
        existing_services = _fanxiu_service_processes()
        if existing_services:
            return {"started": False, "reason": "service_process_already_running", "process": existing_services[0], "owner": read_fanxiu_behavior_tree_service_owner()}
    _clear_stale_fanxiu_behavior_tree_shutdown_request()
    env = os.environ.copy()
    env[FANXIU_EMBEDDED_SERVICE_ENV] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    log_dir = codeyun_temp_root("fanxiu-runtime")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stdout_path = log_dir / f"behavior_tree_service_{stamp}.stdout.log"
    stderr_path = log_dir / f"behavior_tree_service_{stamp}.stderr.log"
    stdout_file = stdout_path.open("ab")
    stderr_file = stderr_path.open("ab")
    try:
        process = popen_python_script_service(
            script_path.resolve(strict=False),
            "--entry-id",
            str(entry_id or DEFAULT_FANXIU_ENTRY_ID),
            "service",
            "--tick-seconds",
            str(max(0.2, float(tick_seconds or 1.0))),
            preferred_root=ROOT_DIR,
            executable=sys.executable,
            cwd=os.fspath(ROOT_DIR),
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
        )
    except Exception:
        stdout_file.close()
        stderr_file.close()
        raise
    stdout_file.close()
    stderr_file.close()
    deadline = time.time() + max(0.1, float(wait_seconds or 5.0))
    owner: dict[str, Any] = {}
    while time.time() < deadline:
        owner = read_fanxiu_behavior_tree_service_owner()
        if bool(owner.get("active")) and not bool(owner.get("stale")):
            break
        time.sleep(0.2)
    return {
        "started": True,
        "pid": process.pid,
        "owner": owner,
        "stdout_path": os.fspath(stdout_path),
        "stderr_path": os.fspath(stderr_path),
    }



def fanxiu_job_group_isolated(path: Path | None = None) -> bool:
    isolation_path = path or fanxiu_job_group_isolation_path()
    status = read_fanxiu_job_group_isolation(isolation_path)
    if _fanxiu_job_group_isolation_owner_dead(status):
        try:
            isolation_path.unlink()
        except FileNotFoundError:
            pass
        return False
    return is_json_lease_active(isolation_path)


def clear_stale_fanxiu_job_group_isolation(path: Path | None = None) -> dict[str, Any]:
    isolation_path = path or fanxiu_job_group_isolation_path()
    status = read_fanxiu_job_group_isolation(isolation_path)
    if _fanxiu_job_group_isolation_owner_dead(status):
        try:
            isolation_path.unlink()
        except FileNotFoundError:
            pass
        return {"cleared": True, "reason": "owner_dead", "path": str(isolation_path)}
    return clear_stale_json_lease(isolation_path)


def acquire_fanxiu_job_group_isolation(
    *,
    reason: str,
    ttl_seconds: float = 300.0,
    token: str | None = None,
    path: Path | None = None,
) -> str:
    isolation_path = path or fanxiu_job_group_isolation_path()
    return acquire_json_lease(
        isolation_path,
        reason=str(reason or "local_run"),
        ttl_seconds=max(5.0, float(ttl_seconds or 300.0)),
        token=str(token or uuid.uuid4().hex),
        now=time.time(),
        extra={"pid": os.getpid()},
    )


def release_fanxiu_job_group_isolation(token: str, path: Path | None = None) -> None:
    isolation_path = path or fanxiu_job_group_isolation_path()
    release_json_lease(isolation_path, str(token or ""))


@contextmanager
def isolate_fanxiu_job_group(
    *,
    reason: str = "local_script",
    ttl_seconds: float = 300.0,
    path: Path | None = None,
) -> Iterator[str]:
    token = acquire_fanxiu_job_group_isolation(reason=reason, ttl_seconds=ttl_seconds, path=path)
    try:
        yield token
    finally:
        release_fanxiu_job_group_isolation(token, path=path)


def register_fanxiu_runtime_runner(runner: Any) -> Any:
    global _RUNTIME_RUNNER
    _RUNTIME_RUNNER = runner
    return runner


def create_and_register_fanxiu_runtime_runner(runner_cls: type[Any] | None = None) -> Any:
    if runner_cls is not None:
        register_fanxiu_runtime_runner_class(runner_cls)
    return register_fanxiu_runtime_runner(create_fanxiu_runtime_runner())


def get_fanxiu_runtime_runner() -> Any:
    global _RUNTIME_RUNNER
    if _RUNTIME_RUNNER is None:
        _RUNTIME_RUNNER = create_fanxiu_runtime_runner()
    return _RUNTIME_RUNNER


def ensure_fanxiu_runtime_jobs_registered() -> None:
    register_fanxiu_data_annotation_debug_eval_job()
    register_fanxiu_data_annotation_default_runtime_jobs()


def ensure_fanxiu_default_runtime_jobs_registered() -> None:
    ensure_fanxiu_runtime_jobs_registered()


def fanxiu_data_annotation_task_cell_catalog() -> list[dict[str, Any]]:
    ensure_fanxiu_runtime_jobs_registered()
    return [
        {
            "task_type": definition.task_type,
            "label": definition.label,
            "interruptible": bool(definition.interruptible),
            "scheduler_supported": bool(definition.scheduler_supported),
            "has_payload_normalizer": definition.normalize_payload is not None,
        }
        for definition in list_fanxiu_data_annotation_task_cell_definitions()
    ]


def fanxiu_runtime_runner_status() -> dict[str, Any]:
    return get_fanxiu_runtime_runner().status()


def fanxiu_runtime_runner_running() -> bool:
    return bool(fanxiu_runtime_runner_status().get("running"))


def fanxiu_runtime_guard_definitions() -> Any:
    return getattr(get_fanxiu_runtime_runner(), "guard_definitions", {}) or {}


def fanxiu_runtime_runner_wake() -> None:
    runner = get_fanxiu_runtime_runner()
    wake_event = getattr(runner, "_service_wake_event", None)
    if wake_event is not None:
        wake_event.set()
    try:
        request_fanxiu_behavior_tree_wake(reason="runtime_wake")
    except Exception:
        pass


def fanxiu_runtime_task_label(task_type: str, payload: dict[str, Any] | None = None) -> str:
    return get_fanxiu_runtime_runner()._runtime_task_label(task_type, payload)


def start_fanxiu_task_cell(
    *,
    entry: Any,
    entry_id: str,
    task: dict[str, Any],
    asset_tree_path: Path | None = None,
) -> dict[str, Any]:
    ensure_fanxiu_runtime_jobs_registered()
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or DEFAULT_FANXIU_ENTRY_ID)
    return get_fanxiu_runtime_runner().start_task_cell(
        entry=entry,
        entry_id=resolved_entry_id,
        task=task,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
    )


def set_fanxiu_runtime_guard(
    *,
    entry: Any,
    entry_id: str,
    asset_tree_path: Path | None = None,
    guard_id: str = "close_popups",
    enabled: bool,
    interval_seconds: float,
) -> dict[str, Any]:
    ensure_fanxiu_runtime_jobs_registered()
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or DEFAULT_FANXIU_ENTRY_ID)
    return get_fanxiu_runtime_runner().set_guard(
        entry=entry,
        entry_id=resolved_entry_id,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
        guard_id=guard_id,
        enabled=enabled,
        interval_seconds=interval_seconds,
    )


def set_fanxiu_runtime_guard_group_enabled(
    *,
    entry: Any,
    entry_id: str,
    asset_tree_path: Path | None = None,
    enabled: bool,
) -> dict[str, Any]:
    ensure_fanxiu_runtime_jobs_registered()
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or DEFAULT_FANXIU_ENTRY_ID)
    return get_fanxiu_runtime_runner().set_guard_group_enabled(
        entry=entry,
        entry_id=resolved_entry_id,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
        enabled=enabled,
    )


def replace_fanxiu_runtime_logs(logs: list[dict[str, Any]]) -> None:
    get_fanxiu_runtime_runner().replace_logs(logs)


def persist_fanxiu_runtime_status(
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


def read_fanxiu_runtime_status(path: Path | None = None) -> dict[str, Any]:
    return read_data_annotation_runtime_status(path or fanxiu_data_annotation_runtime_state_path())


def fanxiu_data_annotation_runtime_status(
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    include_cell_logs: bool = True,
) -> dict[str, Any]:
    runner = get_fanxiu_runtime_runner()
    persisted = read_fanxiu_runtime_status(runtime_state_path) if runtime_state_path is not None else read_fanxiu_runtime_status()
    canonical_runtime_state_path = fanxiu_data_annotation_runtime_state_path().resolve(strict=False)
    use_resident_owner = (
        runtime_state_path is None
        or Path(runtime_state_path).resolve(strict=False) == canonical_runtime_state_path
    )
    owner = read_fanxiu_behavior_tree_service_owner() if use_resident_owner else {}
    owner_active_elsewhere = (
        bool(owner.get("active"))
        and not bool(owner.get("stale"))
        and int(owner.get("pid") or 0) != os.getpid()
    )
    if owner_active_elsewhere and isinstance(persisted, dict) and persisted:
        status = dict(persisted)
        owner_step = str(owner.get("step") or "")
        if bool(status.get("running")) and owner_step in {
            "idle_guard",
            "idle_guard_done",
            "task_cell_poll",
            "scheduler_poll",
            "scheduler_isolated",
            "waiting_context",
        }:
            status["running"] = False
            status["status"] = "idle"
            status["phase"] = owner_step
            status["message"] = data_annotation_runtime_owner_message(owner.get("pid"), owner_step)
            status["current_task"] = ""
            status["current_task_id"] = ""
            status["task_type"] = ""
            status["updated_at"] = time.time()
    else:
        try:
            status = runner.status(include_cell_logs=include_cell_logs)
        except TypeError:
            status = runner.status()
    owner_error = str(owner.get("error") or "") if isinstance(owner, dict) else ""
    if persisted and not owner_active_elsewhere and is_data_annotation_runtime_live_empty(status):
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
            append_data_annotation_runtime_log_once(
                status,
                "stop",
                "后端已重载，运行状态已结束",
                time_text=datetime.now().strftime("%H:%M:%S"),
            )
        elif persisted.get("guard_enabled") or persisted.get("guard_running"):
            status["status"] = "idle"
            status["message"] = "后端已重载，行为树服务待恢复"
            append_data_annotation_runtime_log_once(
                status,
                "stop",
                "后端已重载，行为树服务待恢复",
                time_text=datetime.now().strftime("%H:%M:%S"),
            )
    if (
        runtime_state_path is None
        and isinstance(owner, dict)
        and bool(owner.get("exists"))
        and not owner_active_elsewhere
        and bool(owner.get("stale"))
        and not bool(status.get("running"))
        and str(status.get("phase") or "") == "service_owned_by_other"
    ):
        status["status"] = "idle"
        status["phase"] = "idle"
        status["service_running"] = False
        status["guard_running"] = False
        status["message"] = f"行为树常驻服务未运行，等待恢复：{owner_error or 'owner 已过期'}"
        status["updated_at"] = time.time()
    if runtime_state_path is None:
        if bool(owner.get("active")) and not bool(owner.get("stale")):
            status["service_running"] = True
            status["updated_at"] = time.time()
            if not bool(status.get("running")):
                status["status"] = "idle"
                status_blocking_overlays = [
                    item
                    for item in (status.get("blocking_overlays") or [])
                    if isinstance(item, dict) and bool(item.get("blocking"))
                ]
                persisted_blocking_overlays = [
                    item
                    for item in ((persisted or {}).get("blocking_overlays") or [])
                    if isinstance(item, dict) and bool(item.get("blocking"))
                ] if isinstance(persisted, dict) else []
                has_blocking_overlay = bool(status_blocking_overlays or persisted_blocking_overlays)
                if (
                    has_blocking_overlay
                    and (
                        str(status.get("phase") or "") == "scheduler_blocked"
                        or str((persisted or {}).get("phase") or "") == "scheduler_blocked"
                    )
                ):
                    status["phase"] = "scheduler_blocked"
                    status["blocking_overlays"] = status_blocking_overlays or persisted_blocking_overlays
                    status["message"] = str(
                        (persisted or {}).get("message")
                        or (persisted or {}).get("last_scheduler_block_message")
                        or status.get("message")
                        or "Scheduler 已阻断"
                    )
                    status["last_scheduler_block_message"] = str(
                        (persisted or {}).get("last_scheduler_block_message") or status.get("message") or ""
                    )
                else:
                    status["phase"] = str(owner.get("step") or "scheduler_poll")
                    status["message"] = data_annotation_runtime_owner_message(owner.get("pid"), owner.get("step") or "scheduler_poll")
    normalize_data_annotation_runtime_guard_items(status, runner.guard_definitions)
    normalize_data_annotation_runtime_display(status)
    status.pop("priority", None)
    if not include_cell_logs:
        status.pop("cell_logs", None)
    if owner_active_elsewhere:
        return status
    if runtime_state_path is None and world_facts_path is None:
        persist_fanxiu_runtime_status(status)
    else:
        persist_fanxiu_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def fanxiu_data_annotation_runtime_logs(
    *,
    limit: int = 500,
    scope: str = "",
    item_id: str = "",
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> list[dict[str, Any]]:
    status = fanxiu_data_annotation_runtime_status(
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    return normalize_data_annotation_runtime_logs_for_display(filter_status_logs(status, limit=limit, scope=scope, item_id=item_id))


def clear_fanxiu_data_annotation_runtime_logs(
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    status = fanxiu_runtime_runner_status()
    status["logs"] = []
    replace_fanxiu_runtime_logs([])
    if runtime_state_path is None and world_facts_path is None:
        persist_fanxiu_runtime_status(status)
    else:
        persist_fanxiu_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def fanxiu_data_annotation_task_cells() -> list[dict[str, Any]]:
    from backend.core.fanxiu.data_annotation import runtime_control as runtime_control

    return runtime_control.read_task_cells(fanxiu_data_annotation_task_cell_state_path())


def cancel_fanxiu_task_cell(job_id: str, *, force: bool = False) -> dict[str, Any]:
    from backend.core.fanxiu.data_annotation import runtime_control as runtime_control

    resolved_job_id = str(job_id or "").strip()
    if not resolved_job_id:
        raise ValueError("job_id is required")
    path = fanxiu_data_annotation_task_cell_state_path()
    jobs = runtime_control.read_task_cells(path)
    target = next((job for job in jobs if str(job.get("id") or "") == resolved_job_id), None)
    if target is None:
        return {"cancelled": False, "reason": "not_found", "job_id": resolved_job_id, "remaining": len(jobs)}
    if str(target.get("status") or "") == "running":
        if not force:
            return {"cancelled": False, "reason": "running", "job_id": resolved_job_id, "remaining": len(jobs)}
        request = request_fanxiu_behavior_tree_stop(
            entry_id=str(target.get("entry_id") or ""),
            reason=f"cancel_task_cell:{resolved_job_id}",
        )
        return {
            "cancelled": True,
            "cancel_requested": True,
            "reason": "running_stop_requested",
            "job_id": resolved_job_id,
            "remaining": len(jobs),
            "request": request,
        }
    runtime_control.remove_task_cell(resolved_job_id, path)
    return {"cancelled": True, "job_id": resolved_job_id, "remaining": len(jobs) - 1}


def clear_fanxiu_task_cells(*, force: bool = False) -> dict[str, Any]:
    from backend.core.fanxiu.data_annotation import runtime_control as runtime_control

    path = fanxiu_data_annotation_task_cell_state_path()
    jobs = runtime_control.read_task_cells(path)
    kept, removed = clear_job_queue(
        jobs,
        keep_job=lambda job: str(job.get("status") or "") == "running" and not force,
    )
    runtime_control.write_task_cells(kept, path)
    return {"removed": removed, "remaining": len(kept)}


def wait_fanxiu_task_cell(
    job_id: str,
    *,
    timeout_seconds: float = 300.0,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    resolved_job_id = str(job_id or "").strip()
    if not resolved_job_id:
        raise ValueError("job_id is required")
    deadline = time.time() + max(0.1, float(timeout_seconds or 300.0))
    interval = max(0.1, float(poll_seconds or 0.5))
    last_status: dict[str, Any] = {}
    while True:
        jobs = fanxiu_data_annotation_task_cells()
        matching_job = next((job for job in jobs if str(job.get("id") or "") == resolved_job_id), None)
        status = fanxiu_data_annotation_runtime_status()
        last_status = status
        current_task_id = str(status.get("current_task_id") or "")
        running = bool(status.get("running"))
        if matching_job is None and (current_task_id != resolved_job_id or not running):
            logs = [item for item in status.get("logs") or [] if isinstance(item, dict)]
            matching_logs = [
                item
                for item in logs
                if resolved_job_id and resolved_job_id in str(item.get("message") or "")
            ]
            terminal_logs = [
                item
                for item in matching_logs
                if str(item.get("kind") or "") in {"success", "skip", "stop", "error"}
            ]
            if terminal_logs:
                terminal_log = terminal_logs[-1]
                return {
                    "done": True,
                    "result": "completed",
                    "job_id": resolved_job_id,
                    "terminal_kind": str(terminal_log.get("kind") or "success"),
                    "terminal_message": str(terminal_log.get("message") or ""),
                    "runtime_status": status,
                }
            return {
                "done": False,
                "result": "missing_completion_evidence",
                "job_id": resolved_job_id,
                "runtime_status": status,
            }
        if time.time() >= deadline:
            logs = [item for item in last_status.get("logs") or [] if isinstance(item, dict)]
            has_matching_log = any(
                resolved_job_id and resolved_job_id in str(item.get("message") or "")
                for item in logs
            )
            return {
                "done": False,
                "result": "timeout" if matching_job is not None or has_matching_log else "missing_completion_evidence",
                "job_id": resolved_job_id,
                "job": matching_job or {},
                "runtime_status": last_status,
            }
        time.sleep(interval)


def wait_fanxiu_queued_status(
    status: dict[str, Any],
    *,
    timeout_seconds: float = 300.0,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    queued_cell = status.get("queued_cell") if isinstance(status.get("queued_cell"), dict) else {}
    if not queued_cell:
        queued_cell = status.get("queued_job") if isinstance(status.get("queued_job"), dict) else {}
    job_id = str(queued_cell.get("id") or "")
    if not job_id:
        return {
            "done": False,
            "result": "missing_queued_cell_id",
            "job_id": "",
            "submitted_status": status,
            "runtime_status": status,
        }
    result = wait_fanxiu_task_cell(job_id, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds)
    return {**result, "submitted_status": status}


def ensure_fanxiu_behavior_tree_service(
    entry: Any,
    entry_id: str | None = None,
    *,
    asset_tree_path: Path | None = None,
    tick_seconds: float = 1.0,
) -> dict[str, Any]:
    ensure_fanxiu_runtime_jobs_registered()
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or DEFAULT_FANXIU_ENTRY_ID)
    if not _current_process_is_fanxiu_service_host():
        owner = read_fanxiu_behavior_tree_service_owner()
        if bool(owner.get("active")) and not bool(owner.get("stale")):
            restart = _restart_stuck_external_service_for_pending_jobs(owner)
            if bool(restart.get("restarted")):
                _start_external_fanxiu_behavior_tree_service(resolved_entry_id, tick_seconds=tick_seconds)
                status = fanxiu_data_annotation_runtime_status()
                status["service_recovery"] = restart
                return status
            return fanxiu_data_annotation_runtime_status()
        _start_external_fanxiu_behavior_tree_service(resolved_entry_id, tick_seconds=tick_seconds)
        return fanxiu_data_annotation_runtime_status()
    runner = get_fanxiu_runtime_runner()
    status = runner.ensure_service(
        entry=entry,
        entry_id=resolved_entry_id,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
        tick_seconds=tick_seconds,
    )
    persist_fanxiu_runtime_status(status)
    return status


def stop_fanxiu_behavior_tree_current_task(entry_id: str) -> dict[str, Any]:
    runner = get_fanxiu_runtime_runner()
    status = runner.stop_current_task(str(entry_id or ""))
    persist_fanxiu_runtime_status(status)
    return status


def start_fanxiu_local_service(request: FanxiuLocalServiceRequest) -> dict[str, Any]:
    ensure_fanxiu_runtime_jobs_registered()
    entry = resolve_fanxiu_entry(request.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or request.entry_id or DEFAULT_FANXIU_ENTRY_ID)
    asset_tree_path = data_annotation_asset_tree_path(entry_id)
    if not asset_tree_path.is_file():
        raise FileNotFoundError(f"资产树不存在：{asset_tree_path}")
    return ensure_fanxiu_behavior_tree_service(
        entry,
        entry_id,
        asset_tree_path=asset_tree_path,
        tick_seconds=max(0.2, float(request.tick_seconds or 1.0)),
    )


def stop_fanxiu_local_service(*, timeout_seconds: float = 5.0) -> dict[str, Any]:
    runner = get_fanxiu_runtime_runner()
    stop_service = getattr(runner, "stop_service", None)
    if callable(stop_service):
        status = stop_service(timeout_seconds=timeout_seconds)
    else:
        status = fanxiu_runtime_runner_status()
    persist_fanxiu_runtime_status(status)
    return status


def run_fanxiu_local_service(request: FanxiuLocalServiceRequest) -> dict[str, Any]:
    status = start_fanxiu_local_service(request)
    deadline = time.time() + float(request.duration_seconds or 0.0) if request.duration_seconds else None
    try:
        while deadline is None or time.time() < deadline:
            time.sleep(max(0.2, float(request.tick_seconds or 1.0)))
    except KeyboardInterrupt:
        pass
    return stop_fanxiu_local_service()


def submit_fanxiu_task_cell(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    isolate_jobs: bool = True,
    isolation_ttl_seconds: float = 300.0,
    wait: bool = False,
    wait_timeout_seconds: float = 300.0,
    wait_poll_seconds: float = 0.5,
    dedupe_active: bool = True,
) -> dict[str, Any]:
    """Submit a registered task through the public kernel-cell mental model."""
    from backend.core.fanxiu.data_annotation import runtime_framework

    ensure_fanxiu_runtime_jobs_registered()
    entry = resolve_fanxiu_entry(entry_id)
    resolved_entry_id = str(getattr(entry, "entry_id", None) or entry_id or DEFAULT_FANXIU_ENTRY_ID)
    payload_dict = dict(payload or {})
    existing = _find_matching_active_task_cell(
        str(task_type or ""),
        payload_dict,
        entry_id=resolved_entry_id,
    ) if dedupe_active else None
    if existing is not None:
        status = _task_cell_existing_status(existing)
    else:
        isolation_token = ""
        if isolate_jobs:
            isolation_ttl = max(
                5.0,
                float(isolation_ttl_seconds or 300.0),
                _fanxiu_task_wait_timeout_seconds(payload_dict),
            )
            isolation_token = acquire_fanxiu_job_group_isolation(
                reason=f"task_cell:{task_type}",
                ttl_seconds=isolation_ttl,
            )
            payload_dict["__job_group_isolation_token"] = isolation_token
        try:
            status = runtime_framework.submit_task_cell(
                entry=entry,
                entry_id=resolved_entry_id,
                task_type=str(task_type or ""),
                payload=payload_dict,
                asset_tree_path=data_annotation_asset_tree_path(resolved_entry_id),
                task_cell_path=fanxiu_data_annotation_task_cell_state_path(),
                runtime_state_path=fanxiu_data_annotation_runtime_state_path(),
                world_facts_path=fanxiu_data_annotation_world_facts_path(),
            )
        except Exception:
            if isolation_token:
                release_fanxiu_job_group_isolation(isolation_token)
            raise
        status = _normalize_queued_cell_status(status)
    if not wait:
        return status
    wait_result = wait_fanxiu_queued_status(
        status,
        timeout_seconds=float(wait_timeout_seconds or _fanxiu_task_wait_timeout_seconds(payload_dict)),
        poll_seconds=float(wait_poll_seconds or 0.5),
    )
    return _fanxiu_completed_runtime_status(status, wait_result)


def submit_fanxiu_code_cell(
    code: str,
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    mode: str = "readonly",
    timeout_seconds: float = 120.0,
    max_output_chars: int = 4000,
    isolate_jobs: bool = True,
    isolation_ttl_seconds: float = 300.0,
    wait: bool = False,
    wait_timeout_seconds: float = 300.0,
    wait_poll_seconds: float = 0.5,
) -> dict[str, Any]:
    """Execute dynamic Python code in the resident IPython kernel."""
    from backend.core.fanxiu.data_annotation import runtime_framework

    entry = resolve_fanxiu_entry(entry_id)
    resolved_entry_id = str(getattr(entry, "entry_id", None) or entry_id or DEFAULT_FANXIU_ENTRY_ID)
    return runtime_framework.submit_code_cell(
        entry=entry,
        entry_id=resolved_entry_id,
        code=str(code or ""),
        mode=mode,
        timeout_seconds=float(wait_timeout_seconds if wait and wait_timeout_seconds else timeout_seconds),
        max_output_chars=max_output_chars,
        isolate_jobs=isolate_jobs,
        asset_tree_path=data_annotation_asset_tree_path(resolved_entry_id),
        task_cell_path=fanxiu_data_annotation_task_cell_state_path(),
        runtime_state_path=fanxiu_data_annotation_runtime_state_path(),
        world_facts_path=fanxiu_data_annotation_world_facts_path(),
    )


def fanxiu_resident_owner_active_for_other_process() -> bool:
    return owner_active_for_other_process(read_fanxiu_behavior_tree_service_owner(), current_pid=os.getpid())


def fanxiu_local_task_should_enqueue(run_mode: str = "auto") -> bool:
    mode = normalize_fanxiu_local_run_mode(run_mode)
    try:
        return should_enqueue_local_run(
            mode,
            owner_active_elsewhere=fanxiu_resident_owner_active_for_other_process(),
        )
    except ValueError as exc:
        raise ValueError("run_mode 只支持 auto/direct") from exc


def normalize_fanxiu_local_run_mode(run_mode: str = "auto") -> str:
    mode = str(run_mode or "auto").strip().lower()
    if mode not in {"auto", "direct"}:
        raise ValueError("run_mode 只支持 auto/direct")
    return mode


def _fanxiu_task_wait_timeout_seconds(payload: dict[str, Any], *, fallback: float = 300.0) -> float:
    budgets: list[float] = []
    for key in ("max_runtime_seconds", "timeout_seconds"):
        try:
            value = float((payload or {}).get(key) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            budgets.append(value)
    if budgets:
        return max(float(fallback), max(budgets) + 30.0)
    return float(fallback)


def _task_cell_comparable_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(payload or {}).items()
        if str(key) not in {"__job_group_isolation_token"}
    }


def _find_matching_active_task_cell(
    task_type: str,
    payload: dict[str, Any] | None,
    *,
    entry_id: str = "",
) -> dict[str, Any] | None:
    expected_payload = json.dumps(_task_cell_comparable_payload(payload), ensure_ascii=False, sort_keys=True, default=str)
    for cell in fanxiu_data_annotation_task_cells():
        if str(cell.get("status") or "") not in {"pending", "queued", "running"}:
            continue
        if str(cell.get("task_type") or "") != str(task_type or ""):
            continue
        cell_entry_id = str(cell.get("entry_id") or "")
        if cell_entry_id and entry_id and cell_entry_id != entry_id:
            continue
        cell_payload = cell.get("payload") if isinstance(cell.get("payload"), dict) else {}
        comparable = json.dumps(_task_cell_comparable_payload(cell_payload), ensure_ascii=False, sort_keys=True, default=str)
        if comparable == expected_payload:
            return cell
    return None


def _task_cell_existing_status(cell: dict[str, Any]) -> dict[str, Any]:
    status = fanxiu_data_annotation_runtime_status()
    cell_status = str(cell.get("status") or "pending")
    queued_cell = {
        "id": cell.get("id"),
        "task_type": cell.get("task_type"),
        "label": cell.get("label"),
        "group": cell.get("group"),
        "status": cell_status,
        "created_at": cell.get("created_at"),
        "deduplicated": True,
    }
    status.update({
        "status": "running" if cell_status == "running" else "queued",
        "phase": "task_cell_deduplicated",
        "message": f"复用运行中的 task cell：{cell.get('label') or cell.get('task_type')}",
        "queued_cell": queued_cell,
        "queued_job": queued_cell,
    })
    return status


def _normalize_queued_cell_status(status: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(status or {})
    queued_cell = normalized.get("queued_cell") if isinstance(normalized.get("queued_cell"), dict) else {}
    if not queued_cell:
        queued_cell = normalized.get("queued_job") if isinstance(normalized.get("queued_job"), dict) else {}
    if queued_cell:
        normalized["queued_cell"] = queued_cell
        normalized["queued_job"] = queued_cell
    return normalized


def _fanxiu_completed_runtime_status(
    submitted_status: dict[str, Any],
    wait_result: dict[str, Any],
) -> dict[str, Any]:
    runtime_status = wait_result.get("runtime_status") if isinstance(wait_result.get("runtime_status"), dict) else {}
    status = dict(runtime_status or submitted_status)
    submitted_status = _normalize_queued_cell_status(submitted_status)
    queued_cell = dict(submitted_status.get("queued_cell") or status.get("queued_cell") or {})
    if bool(wait_result.get("done")):
        terminal_kind = str(wait_result.get("terminal_kind") or "success")
        terminal_status = {
            "success": "success",
            "skip": "skipped",
            "stop": "stopped",
            "error": "error",
        }.get(terminal_kind, "success")
        queued_cell["status"] = terminal_status
        status["status"] = terminal_status
        status["phase"] = "done" if terminal_status in {"success", "skipped"} else terminal_status
        status["message"] = str(wait_result.get("terminal_message") or status.get("message") or "cell 已完成")
        status["error"] = status["message"] if terminal_status == "error" else ""
        status["completed_cell"] = dict(queued_cell)
    status["queued_cell"] = queued_cell
    status["queued_job"] = status["queued_cell"]
    status["wait_result"] = {
        key: value
        for key, value in wait_result.items()
        if key not in {"runtime_status", "submitted_status"}
    }
    if not bool(wait_result.get("done")):
        current_status = str((wait_result.get("job") or {}).get("status") or queued_cell.get("status") or "running")
        queued_cell["status"] = current_status
        status["status"] = "running" if current_status == "running" else "queued"
        status["phase"] = "task_cell_wait_timeout"
        status["error"] = ""
        status["message"] = f"等待超时，task cell 仍在后台{('运行' if current_status == 'running' else '排队')}：{queued_cell.get('id') or ''}"
    return status


def run_fanxiu_local_task(request: FanxiuLocalRunRequest) -> dict[str, Any]:
    """Submit a behavior-tree task to the single resident kernel and wait."""
    return submit_fanxiu_task_cell(
        str(request.task_type or ""),
        dict(request.payload or {}),
        entry_id=str(request.entry_id or DEFAULT_FANXIU_ENTRY_ID),
        isolate_jobs=bool(request.isolate_jobs),
        wait=True,
        wait_timeout_seconds=_fanxiu_task_wait_timeout_seconds(dict(request.payload or {})),
        wait_poll_seconds=max(0.1, float(request.tick_seconds or 0.5)),
    )


def run_fanxiu_task(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    isolate_jobs: bool = True,
) -> dict[str, Any]:
    return submit_fanxiu_task_cell(
        str(task_type or ""),
        dict(payload or {}),
        entry_id=str(entry_id or DEFAULT_FANXIU_ENTRY_ID),
        isolate_jobs=bool(isolate_jobs),
        wait=True,
    )


def go_fanxiu_scene(
    scene_id: Any,
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    run_mode: str = "auto",
    isolate_jobs: bool = True,
    timeout_seconds: float = 0.0,
    wait: bool = False,
    wait_timeout_seconds: float = 300.0,
    wait_poll_seconds: float = 0.5,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"target_scene_id": parse_data_annotation_scene_id(scene_id)}
    if timeout_seconds:
        payload["timeout_seconds"] = float(timeout_seconds)
    return submit_fanxiu_task_cell(
        "go_scene",
        payload,
        entry_id=entry_id,
        isolate_jobs=isolate_jobs,
        wait=wait or normalize_fanxiu_local_run_mode(run_mode) == "direct",
        wait_timeout_seconds=wait_timeout_seconds,
        wait_poll_seconds=wait_poll_seconds,
    )


def run_fanxiu_mail_cleanup(
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    observe_only: bool = False,
    scan_mode: str = "incremental",
    skip_capture: bool = False,
    max_actions: int = 0,
    run_mode: str = "auto",
    isolate_jobs: bool = True,
    timeout_seconds: float = 0.0,
    wait: bool = False,
    wait_timeout_seconds: float = 300.0,
    wait_poll_seconds: float = 0.5,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "observe_only": bool(observe_only),
        "scan_mode": str(scan_mode or "incremental"),
        "skip_capture": bool(skip_capture),
        "max_actions": int(max_actions or 0),
    }
    if timeout_seconds:
        payload["timeout_seconds"] = float(timeout_seconds)
    return submit_fanxiu_task_cell(
        "mail_cleanup",
        payload,
        entry_id=entry_id,
        isolate_jobs=isolate_jobs,
        wait=wait or normalize_fanxiu_local_run_mode(run_mode) == "direct",
        wait_timeout_seconds=wait_timeout_seconds,
        wait_poll_seconds=wait_poll_seconds,
    )


def run_fanxiu_mail_claim_check(**kwargs: Any) -> dict[str, Any]:
    return run_fanxiu_mail_cleanup(**kwargs)


def run_fanxiu_xianfu_visit_partner(
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    run_mode: str = "auto",
    isolate_jobs: bool = True,
    timeout_seconds: float = 0.0,
    wait: bool = False,
    wait_timeout_seconds: float = 300.0,
    wait_poll_seconds: float = 0.5,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if timeout_seconds:
        payload["timeout_seconds"] = float(timeout_seconds)
    return submit_fanxiu_task_cell(
        "xianfu_visit_partner",
        payload,
        entry_id=entry_id,
        isolate_jobs=isolate_jobs,
        wait=wait or normalize_fanxiu_local_run_mode(run_mode) == "direct",
        wait_timeout_seconds=wait_timeout_seconds,
        wait_poll_seconds=wait_poll_seconds,
    )


def run_fanxiu_xianfu_learn_skill(
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    run_mode: str = "auto",
    isolate_jobs: bool = True,
    timeout_seconds: float = 0.0,
    wait: bool = False,
    wait_timeout_seconds: float = 300.0,
    wait_poll_seconds: float = 0.5,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if timeout_seconds:
        payload["timeout_seconds"] = float(timeout_seconds)
    return submit_fanxiu_task_cell(
        "xianfu_learn_skill",
        payload,
        entry_id=entry_id,
        isolate_jobs=isolate_jobs,
        wait=wait or normalize_fanxiu_local_run_mode(run_mode) == "direct",
        wait_timeout_seconds=wait_timeout_seconds,
        wait_poll_seconds=wait_poll_seconds,
    )

