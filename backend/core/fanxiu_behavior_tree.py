from __future__ import annotations

import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

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

from backend.core.fanxiu_data_annotation_state import (
    append_data_annotation_runtime_log_once,
    is_data_annotation_runtime_live_empty,
    normalize_data_annotation_runtime_guard_items,
    persist_data_annotation_runtime_status,
    read_data_annotation_runtime_status,
)
from backend.core.fanxiu_data_annotation_jobs import (
    list_fanxiu_data_annotation_manual_job_definitions,
    parse_data_annotation_scene_id,
)
from backend.core.fanxiu_data_annotation_debug_eval import register_fanxiu_data_annotation_debug_eval_job
from backend.core.fanxiu_data_annotation_default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu_data_annotation_runner import (
    create_fanxiu_runtime_runner,
    get_fanxiu_runtime_runner_class,
    register_fanxiu_runtime_runner_class,
)
from backend.core.settings import get_settings


DEFAULT_FANXIU_ENTRY_ID = "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"
_RUNTIME_RUNNER: Any | None = None


@dataclass(frozen=True)
class FanxiuLocalRunRequest:
    task_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID
    isolate_jobs: bool = True


@dataclass(frozen=True)
class FanxiuLocalServiceRequest:
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID
    tick_seconds: float = 1.0
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class FanxiuLocalEnqueueRequest:
    task_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID
    label: str = ""
    interruptible: bool | None = None
    isolate_jobs: bool = True
    isolation_ttl_seconds: float = 300.0


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


def fanxiu_data_annotation_dir() -> Path:
    return get_settings().data_dir / "fanxiu" / "data-annotation"


def fanxiu_data_annotation_runtime_dir() -> Path:
    path = fanxiu_data_annotation_dir() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fanxiu_data_annotation_runtime_state_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "runtime_state.json"


def fanxiu_data_annotation_world_facts_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "world_facts.json"


def fanxiu_data_annotation_scheduler_state_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "scheduler_tasks.json"


def fanxiu_data_annotation_scheduler_settings_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "scheduler_settings.json"


def fanxiu_data_annotation_manual_job_state_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "manual_jobs.json"


def fanxiu_data_annotation_mail_scan_state_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "mail_scan_state.json"


def data_annotation_asset_tree_path(entry_id: str = DEFAULT_FANXIU_ENTRY_ID) -> Path:
    safe_entry_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(entry_id or DEFAULT_FANXIU_ENTRY_ID)).strip("._") or "default"
    return fanxiu_data_annotation_dir() / "asset-trees" / f"{safe_entry_id}.json"


def fanxiu_job_group_isolation_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "job_group_isolation.json"


def read_fanxiu_job_group_isolation(path: Path | None = None) -> dict[str, Any]:
    isolation_path = path or fanxiu_job_group_isolation_path()
    return read_json_lease(isolation_path, invalid_message="isolation 文件不是 JSON object")


def fanxiu_behavior_tree_service_owner_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "behavior_tree_service_owner.json"


def fanxiu_behavior_tree_control_path() -> Path:
    return fanxiu_data_annotation_runtime_dir() / "behavior_tree_control.json"


def _fanxiu_process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
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
    return status



def fanxiu_job_group_isolated(path: Path | None = None) -> bool:
    isolation_path = path or fanxiu_job_group_isolation_path()
    return is_json_lease_active(isolation_path)


def clear_stale_fanxiu_job_group_isolation(path: Path | None = None) -> dict[str, Any]:
    isolation_path = path or fanxiu_job_group_isolation_path()
    return clear_stale_json_lease(isolation_path)


def acquire_fanxiu_job_group_isolation(
    *,
    reason: str,
    ttl_seconds: float = 300.0,
    path: Path | None = None,
) -> str:
    isolation_path = path or fanxiu_job_group_isolation_path()
    return acquire_json_lease(
        isolation_path,
        reason=str(reason or "local_run"),
        ttl_seconds=max(5.0, float(ttl_seconds or 300.0)),
        token=uuid.uuid4().hex,
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


def fanxiu_data_annotation_manual_job_catalog() -> list[dict[str, Any]]:
    ensure_fanxiu_runtime_jobs_registered()
    return [
        {
            "task_type": definition.task_type,
            "label": definition.label,
            "interruptible": bool(definition.interruptible),
            "scheduler_supported": bool(definition.scheduler_supported),
            "has_payload_normalizer": definition.normalize_payload is not None,
        }
        for definition in list_fanxiu_data_annotation_manual_job_definitions()
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


def start_fanxiu_manual_runtime_task(
    *,
    entry: Any,
    entry_id: str,
    task: dict[str, Any],
    asset_tree_path: Path | None = None,
) -> dict[str, Any]:
    ensure_fanxiu_runtime_jobs_registered()
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or DEFAULT_FANXIU_ENTRY_ID)
    return get_fanxiu_runtime_runner().start_manual_runtime_task(
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
) -> dict[str, Any]:
    runner = get_fanxiu_runtime_runner()
    persisted = read_fanxiu_runtime_status(runtime_state_path) if runtime_state_path is not None else read_fanxiu_runtime_status()
    owner = read_fanxiu_behavior_tree_service_owner() if runtime_state_path is None else {}
    owner_active_elsewhere = (
        bool(owner.get("active"))
        and not bool(owner.get("stale"))
        and int(owner.get("pid") or 0) != os.getpid()
    )
    if owner_active_elsewhere and isinstance(persisted, dict) and persisted:
        status = dict(persisted)
    else:
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
                status["phase"] = str(owner.get("step") or "scheduler_poll")
                status["message"] = (
                    f"行为树常驻服务运行中：进程 {owner.get('pid')} "
                    f"{owner.get('step') or 'scheduler_poll'}"
                )
    normalize_data_annotation_runtime_guard_items(status, runner.guard_definitions)
    status.pop("priority", None)
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
    return filter_status_logs(status, limit=limit, scope=scope, item_id=item_id)


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


def fanxiu_data_annotation_manual_jobs() -> list[dict[str, Any]]:
    from backend.core import fanxiu_data_annotation_runtime_control as runtime_control

    return runtime_control.read_manual_jobs(fanxiu_data_annotation_manual_job_state_path())


def cancel_fanxiu_local_manual_job(job_id: str, *, force: bool = False) -> dict[str, Any]:
    from backend.core import fanxiu_data_annotation_runtime_control as runtime_control

    resolved_job_id = str(job_id or "").strip()
    if not resolved_job_id:
        raise ValueError("job_id is required")
    path = fanxiu_data_annotation_manual_job_state_path()
    jobs = runtime_control.read_manual_jobs(path)
    target = next((job for job in jobs if str(job.get("id") or "") == resolved_job_id), None)
    if target is None:
        return {"cancelled": False, "reason": "not_found", "job_id": resolved_job_id, "remaining": len(jobs)}
    if str(target.get("status") or "") == "running" and not force:
        return {"cancelled": False, "reason": "running", "job_id": resolved_job_id, "remaining": len(jobs)}
    runtime_control.remove_manual_job(resolved_job_id, path)
    return {"cancelled": True, "job_id": resolved_job_id, "remaining": len(jobs) - 1}


def clear_fanxiu_local_manual_jobs(*, force: bool = False) -> dict[str, Any]:
    from backend.core import fanxiu_data_annotation_runtime_control as runtime_control

    path = fanxiu_data_annotation_manual_job_state_path()
    jobs = runtime_control.read_manual_jobs(path)
    kept, removed = clear_job_queue(
        jobs,
        keep_job=lambda job: str(job.get("status") or "") == "running" and not force,
    )
    runtime_control.write_manual_jobs(kept, path)
    return {"removed": removed, "remaining": len(kept)}


def wait_fanxiu_local_manual_job(
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
        jobs = fanxiu_data_annotation_manual_jobs()
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
                if str(item.get("kind") or "") in {"success", "stop", "error"}
            ]
            if terminal_logs:
                return {
                    "done": True,
                    "result": "completed",
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
    queued_job = status.get("queued_job") if isinstance(status.get("queued_job"), dict) else {}
    job_id = str(queued_job.get("id") or "")
    if not job_id:
        return {
            "done": False,
            "result": "missing_queued_job_id",
            "job_id": "",
            "submitted_status": status,
            "runtime_status": status,
        }
    result = wait_fanxiu_local_manual_job(job_id, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds)
    return {**result, "submitted_status": status}


def ensure_fanxiu_behavior_tree_service(
    entry: Any,
    entry_id: str | None = None,
    *,
    asset_tree_path: Path | None = None,
    tick_seconds: float = 1.0,
) -> dict[str, Any]:
    ensure_fanxiu_runtime_jobs_registered()
    runner = get_fanxiu_runtime_runner()
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or DEFAULT_FANXIU_ENTRY_ID)
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


def enqueue_fanxiu_local_manual_job(request: FanxiuLocalEnqueueRequest) -> dict[str, Any]:
    from backend.core import fanxiu_data_annotation_runtime_control as runtime_control

    ensure_fanxiu_runtime_jobs_registered()
    entry = resolve_fanxiu_entry(request.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or request.entry_id or DEFAULT_FANXIU_ENTRY_ID)
    payload = dict(request.payload or {})
    if request.isolate_jobs:
        token = acquire_fanxiu_job_group_isolation(
            reason=f"local_enqueue:{request.task_type}",
            ttl_seconds=max(5.0, float(request.isolation_ttl_seconds or 300.0)),
        )
        payload["__job_group_isolation_token"] = token
    return runtime_control.submit_manual_job(
        entry=entry,
        entry_id=entry_id,
        task_type=str(request.task_type or ""),
        payload=payload,
        label=str(request.label or ""),
        interruptible=request.interruptible,
        asset_tree_path=data_annotation_asset_tree_path(entry_id),
        manual_job_path=fanxiu_data_annotation_manual_job_state_path(),
        runtime_state_path=fanxiu_data_annotation_runtime_state_path(),
        world_facts_path=fanxiu_data_annotation_world_facts_path(),
    )


def fanxiu_resident_owner_active_for_other_process() -> bool:
    return owner_active_for_other_process(read_fanxiu_behavior_tree_service_owner(), current_pid=os.getpid())


def fanxiu_local_task_should_enqueue(run_mode: str = "auto") -> bool:
    try:
        return should_enqueue_local_run(
            run_mode,
            owner_active_elsewhere=fanxiu_resident_owner_active_for_other_process(),
        )
    except ValueError as exc:
        raise ValueError("run_mode 只支持 auto/direct/enqueue") from exc


def run_fanxiu_local_task(request: FanxiuLocalRunRequest) -> dict[str, Any]:
    """Run a Fanxiu behavior-tree task from local developer code.

    This is the stable local entrypoint. It deliberately keeps CLI/dev callers
    out of the FastAPI layer. A fresh Python process can import the core runner
    implementation directly without waiting for CodeYun/FastAPI startup.
    """
    ensure_fanxiu_runtime_jobs_registered()
    entry = resolve_fanxiu_entry(request.entry_id)
    entry_id = str(getattr(entry, "entry_id", None) or request.entry_id or DEFAULT_FANXIU_ENTRY_ID)
    asset_tree_path = data_annotation_asset_tree_path(entry_id)
    if not asset_tree_path.is_file():
        raise FileNotFoundError(f"资产树不存在：{asset_tree_path}")
    return get_fanxiu_runtime_runner().start_local_runtime_task(
        entry=entry,
        entry_id=entry_id,
        task_type=str(request.task_type or ""),
        payload=dict(request.payload or {}),
        asset_tree_path=asset_tree_path,
        isolate_jobs=bool(request.isolate_jobs),
    )


def run_fanxiu_task(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    isolate_jobs: bool = True,
) -> dict[str, Any]:
    return run_fanxiu_local_task(
        FanxiuLocalRunRequest(
            task_type=str(task_type or ""),
            payload=dict(payload or {}),
            entry_id=str(entry_id or DEFAULT_FANXIU_ENTRY_ID),
            isolate_jobs=bool(isolate_jobs),
        )
    )


def submit_fanxiu_task(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    run_mode: str = "auto",
    isolate_jobs: bool = True,
    label: str = "",
    interruptible: bool | None = None,
    isolation_ttl_seconds: float = 300.0,
    wait: bool = False,
    wait_timeout_seconds: float = 300.0,
    wait_poll_seconds: float = 0.5,
) -> dict[str, Any]:
    if fanxiu_local_task_should_enqueue(run_mode):
        status = enqueue_fanxiu_local_manual_job(
            FanxiuLocalEnqueueRequest(
                task_type=str(task_type or ""),
                payload=dict(payload or {}),
                entry_id=str(entry_id or DEFAULT_FANXIU_ENTRY_ID),
                label=str(label or ""),
                interruptible=interruptible,
                isolate_jobs=bool(isolate_jobs),
                isolation_ttl_seconds=float(isolation_ttl_seconds or 300.0),
            )
        )
        if wait:
            return wait_fanxiu_queued_status(
                status,
                timeout_seconds=float(wait_timeout_seconds or 300.0),
                poll_seconds=float(wait_poll_seconds or 0.5),
            )
        return status
    return run_fanxiu_task(
        str(task_type or ""),
        dict(payload or {}),
        entry_id=str(entry_id or DEFAULT_FANXIU_ENTRY_ID),
        isolate_jobs=bool(isolate_jobs),
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
    return submit_fanxiu_task(
        "go_scene",
        payload,
        entry_id=entry_id,
        run_mode=run_mode,
        isolate_jobs=isolate_jobs,
        wait=wait,
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
    return submit_fanxiu_task(
        "mail_cleanup",
        payload,
        entry_id=entry_id,
        run_mode=run_mode,
        isolate_jobs=isolate_jobs,
        wait=wait,
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
    return submit_fanxiu_task(
        "xianfu_visit_partner",
        payload,
        entry_id=entry_id,
        run_mode=run_mode,
        isolate_jobs=isolate_jobs,
        wait=wait,
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
    return submit_fanxiu_task(
        "xianfu_learn_skill",
        payload,
        entry_id=entry_id,
        run_mode=run_mode,
        isolate_jobs=isolate_jobs,
        wait=wait,
        wait_timeout_seconds=wait_timeout_seconds,
        wait_poll_seconds=wait_poll_seconds,
    )
