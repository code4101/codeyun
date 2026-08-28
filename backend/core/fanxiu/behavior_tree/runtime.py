from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from filelock import FileLock, Timeout as FileLockTimeout
from pyxllib.prog import filter_status_logs

from backend.core.fanxiu.data_annotation.state import (
    append_behavior_tree_runtime_log_once,
    normalize_behavior_tree_runtime_display,
    normalize_behavior_tree_runtime_logs_for_display,
    normalize_behavior_tree_runtime_guard_items,
    select_behavior_tree_runtime_status,
    persist_behavior_tree_runtime_status as _persist_behavior_tree_runtime_status,
    read_behavior_tree_runtime_status as _read_behavior_tree_runtime_status,
)
from backend.core.fanxiu.data_annotation.jobs import (
    list_fanxiu_data_annotation_task_cell_definitions,
    parse_data_annotation_scene_id,
)
from backend.core.fanxiu.data_annotation.debug_eval import register_fanxiu_data_annotation_debug_eval_job
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.runner import (
    create_behavior_tree_runtime_runner,
    get_behavior_tree_runtime_runner_class,
    register_behavior_tree_runtime_runner_class,
)
from backend.core.services.launcher import popen_service, resolve_python
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.data_annotation.storage import (
    DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID,
    data_annotation_asset_tree_path,
)
from backend.core.settings import ROOT_DIR, get_settings


DEFAULT_FANXIU_ENTRY_ID = DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID
_BEHAVIOR_TREE_RUNTIME_RUNNER: Any | None = None


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


def fanxiu_behavior_tree_runtime_dir() -> Path:
    """Return the Behavior Tree Runtime state directory.

    The physical ``data-annotation/runtime`` directory is a persisted-data
    compatibility contract.  New Python APIs deliberately use
    ``behavior_tree_runtime`` so an unqualified Runtime remains available for
    the game client's authoritative runtime state.
    """
    path = fanxiu_data_annotation_dir() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fanxiu_data_annotation_dir() -> Path:
    return get_settings().data_dir / "fanxiu" / "data-annotation"


def fanxiu_behavior_tree_runtime_state_path() -> Path:
    return fanxiu_behavior_tree_runtime_dir() / "runtime_state.json"


def fanxiu_data_annotation_world_facts_path() -> Path:
    return fanxiu_behavior_tree_runtime_dir() / "world_facts.json"


def fanxiu_data_annotation_scheduler_state_path() -> Path:
    return fanxiu_behavior_tree_runtime_dir() / "scheduler_tasks.json"


def fanxiu_data_annotation_scheduler_settings_path() -> Path:
    return fanxiu_behavior_tree_runtime_dir() / "scheduler_settings.json"


def fanxiu_kernel_service_start_lock_path() -> Path:
    """Serialize KernelManager recovery across backend and Scheduler processes."""

    return fanxiu_behavior_tree_runtime_dir() / "kernel-service.start.lock"


def fanxiu_data_annotation_mail_scan_state_path() -> Path:
    return fanxiu_behavior_tree_runtime_dir() / "mail_scan_state.json"


def restart_fanxiu_behavior_tree_service(
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    timeout_seconds: float = 15.0,
    tick_seconds: float = 1.0,
    force: bool = False,
) -> dict[str, Any]:
    del tick_seconds, force
    from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel

    result = FanxiuKernel(entry_id=str(entry_id or DEFAULT_FANXIU_ENTRY_ID)).restart(
        timeout_seconds=max(1.0, float(timeout_seconds or 15.0)),
    )
    return {**result, "restarted": bool(result.get("ok"))}


def _start_external_fanxiu_behavior_tree_service(
    entry_id: str,
    *,
    tick_seconds: float = 1.0,
    wait_seconds: float = 60.0,
) -> dict[str, Any]:
    del tick_seconds
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

    lock_path = fanxiu_kernel_service_start_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(lock_path), timeout=max(10.0, float(wait_seconds) + 10.0)):
            return _start_external_fanxiu_behavior_tree_service_unlocked(
                entry_id,
                wait_seconds=wait_seconds,
            )
    except FileLockTimeout as exc:
        status = fanxiu_kernel_manager_status(timeout_seconds=3.0)
        if bool(status.get("alive")):
            return {
                "started": False,
                "reason": "kernel_recovered_while_waiting_for_start_lock",
                "kernel": status,
            }
        raise RuntimeError(f"等待 Fanxiu Kernel 自动恢复超时：{lock_path}") from exc


def _start_external_fanxiu_behavior_tree_service_unlocked(
    entry_id: str,
    *,
    wait_seconds: float,
) -> dict[str, Any]:
    """Start or restart the one KernelManager while holding the recovery lock."""

    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

    before = fanxiu_kernel_manager_status()
    if bool(before.get("alive")):
        return {"started": False, "reason": "kernel_already_alive", "kernel": before}
    if before.get("manager_pid") is not None:
        # ``alive`` is the Jupyter child state.  A responsive manager with a
        # dead child already owns the fixed control address, so launching a
        # second manager can only fail with WinError 10048.  Restart the child
        # through the existing owner instead.
        from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel

        restarted = FanxiuKernel(entry_id=str(entry_id or DEFAULT_FANXIU_ENTRY_ID)).restart(
            timeout_seconds=max(5.0, float(wait_seconds or 5.0)),
        )
        return {
            "started": bool(restarted.get("alive")),
            "reason": "kernel_restarted_in_existing_manager",
            "kernel": restarted,
        }
    script_path = ROOT_DIR / "scripts" / "fanxiu_bt.py"
    if not script_path.is_file():
        return {"started": False, "reason": f"service_script_missing:{script_path}"}
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    log_dir = codeyun_temp_root("fanxiu-runtime")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stdout_path = log_dir / f"behavior_tree_service_{stamp}.stdout.log"
    stderr_path = log_dir / f"behavior_tree_service_{stamp}.stderr.log"
    stdout_file = stdout_path.open("ab")
    stderr_file = stderr_path.open("ab")
    try:
        process = popen_service(
            [
                resolve_python(preferred_root=ROOT_DIR, executable=sys.executable),
                os.fspath(script_path.resolve(strict=False)),
                "--entry-id",
                str(entry_id or DEFAULT_FANXIU_ENTRY_ID),
                "service",
            ],
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
    deadline = time.time() + max(0.1, float(wait_seconds or 60.0))
    status: dict[str, Any] = {}
    while time.time() < deadline:
        status = fanxiu_kernel_manager_status(timeout_seconds=3.0)
        if bool(status.get("alive")):
            break
        if process.poll() is not None:
            break
        time.sleep(0.2)
    return {
        "started": bool(status.get("alive")),
        "pid": process.pid,
        "kernel": status,
        "stdout_path": os.fspath(stdout_path),
        "stderr_path": os.fspath(stderr_path),
    }



def register_behavior_tree_runtime_runner(runner: Any) -> Any:
    global _BEHAVIOR_TREE_RUNTIME_RUNNER
    _BEHAVIOR_TREE_RUNTIME_RUNNER = runner
    return runner


def create_and_register_behavior_tree_runtime_runner(runner_cls: type[Any] | None = None) -> Any:
    if runner_cls is not None:
        register_behavior_tree_runtime_runner_class(runner_cls)
    return register_behavior_tree_runtime_runner(create_behavior_tree_runtime_runner())


def get_behavior_tree_runtime_runner() -> Any:
    global _BEHAVIOR_TREE_RUNTIME_RUNNER
    if _BEHAVIOR_TREE_RUNTIME_RUNNER is None:
        _BEHAVIOR_TREE_RUNTIME_RUNNER = create_behavior_tree_runtime_runner()
    return _BEHAVIOR_TREE_RUNTIME_RUNNER


def ensure_behavior_tree_runtime_jobs_registered() -> None:
    register_fanxiu_data_annotation_debug_eval_job()
    register_fanxiu_data_annotation_default_runtime_jobs()


def ensure_behavior_tree_default_runtime_jobs_registered() -> None:
    ensure_behavior_tree_runtime_jobs_registered()


def fanxiu_data_annotation_task_cell_catalog() -> list[dict[str, Any]]:
    ensure_behavior_tree_runtime_jobs_registered()
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


def behavior_tree_runtime_runner_status() -> dict[str, Any]:
    return get_behavior_tree_runtime_runner().status()


def behavior_tree_runtime_runner_running() -> bool:
    return bool(behavior_tree_runtime_runner_status().get("running"))


def behavior_tree_runtime_guard_definitions() -> Any:
    return getattr(get_behavior_tree_runtime_runner(), "guard_definitions", {}) or {}


def behavior_tree_runtime_task_label(task_type: str, payload: dict[str, Any] | None = None) -> str:
    return get_behavior_tree_runtime_runner()._runtime_task_label(task_type, payload)


def set_behavior_tree_runtime_guard(
    *,
    entry: Any,
    entry_id: str,
    asset_tree_path: Path | None = None,
    guard_id: str = "device_health",
    enabled: bool,
    interval_seconds: float,
) -> dict[str, Any]:
    ensure_behavior_tree_runtime_jobs_registered()
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or DEFAULT_FANXIU_ENTRY_ID)
    return get_behavior_tree_runtime_runner().set_guard(
        entry=entry,
        entry_id=resolved_entry_id,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
        guard_id=guard_id,
        enabled=enabled,
        interval_seconds=interval_seconds,
    )


def set_behavior_tree_runtime_guard_group_enabled(
    *,
    entry: Any,
    entry_id: str,
    asset_tree_path: Path | None = None,
    enabled: bool,
) -> dict[str, Any]:
    ensure_behavior_tree_runtime_jobs_registered()
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or DEFAULT_FANXIU_ENTRY_ID)
    return get_behavior_tree_runtime_runner().set_guard_group_enabled(
        entry=entry,
        entry_id=resolved_entry_id,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
        enabled=enabled,
    )


def replace_behavior_tree_runtime_logs(logs: list[dict[str, Any]]) -> None:
    get_behavior_tree_runtime_runner().replace_logs(logs)


def persist_behavior_tree_runtime_status(
    status: dict[str, Any],
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> None:
    _persist_behavior_tree_runtime_status(
        runtime_state_path or fanxiu_behavior_tree_runtime_state_path(),
        world_facts_path or fanxiu_data_annotation_world_facts_path(),
        status,
    )


def read_behavior_tree_runtime_status(path: Path | None = None) -> dict[str, Any]:
    return _read_behavior_tree_runtime_status(path or fanxiu_behavior_tree_runtime_state_path())


def fanxiu_behavior_tree_runtime_status(
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    include_cell_logs: bool = True,
) -> dict[str, Any]:
    """Return business Runtime state with native Kernel state kept separate."""
    runner = get_behavior_tree_runtime_runner()
    persisted = read_behavior_tree_runtime_status(runtime_state_path)
    try:
        status = runner.status(include_cell_logs=include_cell_logs)
    except TypeError:
        status = runner.status()
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

    kernel_state = fanxiu_kernel_manager_status()
    status = select_behavior_tree_runtime_status(status, persisted)
    if status.get("running"):
        try:
            status_age_seconds = max(0.0, time.time() - float(status.get("updated_at") or 0.0))
        except (TypeError, ValueError):
            status_age_seconds = float("inf")
        kernel_still_running_cell = (
            bool(kernel_state.get("alive"))
            and str(kernel_state.get("execution_state") or "") == "busy"
        )
        terminal_writeback_pending = bool(kernel_state.get("alive")) and status_age_seconds < 30.0
        if not kernel_still_running_cell and not terminal_writeback_pending:
            status["running"] = False
            status["guard_running"] = False
            status["status"] = "stopped"
            status["phase"] = "stopped"
            status["message"] = "执行进程已重载，先前业务任务已结束"
            status["finished_at"] = status.get("finished_at") or time.time()
            append_behavior_tree_runtime_log_once(
                status,
                "stop",
                "执行进程已重载，先前业务任务已结束",
                time_text=datetime.now().strftime("%H:%M:%S"),
            )
    if (
        not status.get("running")
        and str(status.get("status") or "") == "running"
        and str(kernel_state.get("execution_state") or "") != "busy"
    ):
        status["guard_running"] = False
        status["status"] = "stopped"
        status["phase"] = "stopped"
        status["task_type"] = ""
        status["current_task"] = ""
        status["current_task_id"] = ""
        status["message"] = "当前 Cell 已结束，Kernel 保持空闲"
        status["finished_at"] = status.get("finished_at") or time.time()
        append_behavior_tree_runtime_log_once(
            status,
            "stop",
            "当前 Cell 已结束，Kernel 保持空闲",
            time_text=datetime.now().strftime("%H:%M:%S"),
        )
    status["kernel"] = kernel_state
    normalize_behavior_tree_runtime_guard_items(status, runner.guard_definitions)
    normalize_behavior_tree_runtime_display(status)
    status.pop("priority", None)
    if not include_cell_logs:
        status.pop("cell_logs", None)
    return status


def fanxiu_behavior_tree_runtime_logs(
    *,
    limit: int = 500,
    scope: str = "",
    item_id: str = "",
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> list[dict[str, Any]]:
    runner = get_behavior_tree_runtime_runner()
    persisted = read_behavior_tree_runtime_status(runtime_state_path)
    try:
        live = runner.status(include_cell_logs=False)
    except TypeError:
        live = runner.status()
    status = select_behavior_tree_runtime_status(live, persisted)
    return normalize_behavior_tree_runtime_logs_for_display(filter_status_logs(status, limit=limit, scope=scope, item_id=item_id))


def clear_fanxiu_behavior_tree_runtime_logs(
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    status = behavior_tree_runtime_runner_status()
    status["logs"] = []
    replace_behavior_tree_runtime_logs([])
    if runtime_state_path is None and world_facts_path is None:
        persist_behavior_tree_runtime_status(status)
    else:
        persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def ensure_fanxiu_behavior_tree_service(
    entry: Any,
    entry_id: str | None = None,
    *,
    asset_tree_path: Path | None = None,
    tick_seconds: float = 1.0,
) -> dict[str, Any]:
    del asset_tree_path, tick_seconds
    ensure_behavior_tree_runtime_jobs_registered()
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or DEFAULT_FANXIU_ENTRY_ID)
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

    status = fanxiu_kernel_manager_status()
    if not bool(status.get("alive")):
        _start_external_fanxiu_behavior_tree_service(resolved_entry_id)
        status = fanxiu_kernel_manager_status(timeout_seconds=3.0)
    return status


def stop_fanxiu_behavior_tree_current_task(
    entry_id: str,
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    del entry_id
    from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel

    return FanxiuKernel().interrupt(timeout_seconds=timeout_seconds)


def start_fanxiu_local_service(request: FanxiuLocalServiceRequest) -> dict[str, Any]:
    ensure_behavior_tree_runtime_jobs_registered()
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
    from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel

    return FanxiuKernel().shutdown(timeout_seconds=timeout_seconds)


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
    wait_timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    """Compile a registered task invocation into one ordinary cell."""
    from backend.core.fanxiu.data_annotation import behavior_tree_framework

    ensure_behavior_tree_runtime_jobs_registered()
    entry = resolve_fanxiu_entry(entry_id)
    resolved_entry_id = str(getattr(entry, "entry_id", None) or entry_id or DEFAULT_FANXIU_ENTRY_ID)
    payload_dict = dict(payload or {})
    requested_timeout = float(wait_timeout_seconds or _fanxiu_task_wait_timeout_seconds(payload_dict))
    if requested_timeout > 0:
        payload_dict.setdefault("max_runtime_seconds", requested_timeout)
    return behavior_tree_framework.submit_task_cell(
        entry=entry,
        entry_id=resolved_entry_id,
        task_type=str(task_type or ""),
        payload=payload_dict,
    )


def submit_fanxiu_code_cell(
    code: str,
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    timeout_seconds: float = 120.0,
    max_output_chars: int = 4000,
) -> dict[str, Any]:
    """Execute dynamic Python code in the resident IPython kernel."""
    from backend.core.fanxiu.data_annotation import behavior_tree_framework

    entry = resolve_fanxiu_entry(entry_id)
    resolved_entry_id = str(getattr(entry, "entry_id", None) or entry_id or DEFAULT_FANXIU_ENTRY_ID)
    return behavior_tree_framework.submit_code_cell(
        entry=entry,
        entry_id=resolved_entry_id,
        code=str(code or ""),
        timeout_seconds=float(timeout_seconds),
        max_output_chars=max_output_chars,
    )


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




def run_fanxiu_task(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
) -> dict[str, Any]:
    return submit_fanxiu_task_cell(
        str(task_type or ""),
        dict(payload or {}),
        entry_id=str(entry_id or DEFAULT_FANXIU_ENTRY_ID),
    )


def go_fanxiu_scene(
    scene_id: Any,
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    timeout_seconds: float = 0.0,
    wait_timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"target_scene_id": parse_data_annotation_scene_id(scene_id)}
    if timeout_seconds:
        payload["timeout_seconds"] = float(timeout_seconds)
    return submit_fanxiu_task_cell(
        "go_scene",
        payload,
        entry_id=entry_id,
        wait_timeout_seconds=wait_timeout_seconds,
    )


def run_fanxiu_mail_selective_claim(
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    observe_only: bool = False,
    scan_mode: str = "incremental",
    skip_capture: bool = False,
    max_actions: int = 0,
    timeout_seconds: float = 0.0,
    wait_timeout_seconds: float = 300.0,
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
        "mail_selective_claim",
        payload,
        entry_id=entry_id,
        wait_timeout_seconds=wait_timeout_seconds,
    )


def run_fanxiu_mail_cleanup(**kwargs: Any) -> dict[str, Any]:
    """Legacy API alias; use ``run_fanxiu_mail_selective_claim``."""
    return run_fanxiu_mail_selective_claim(**kwargs)


def run_fanxiu_mail_claim_check(**kwargs: Any) -> dict[str, Any]:
    """Legacy API alias; use ``run_fanxiu_mail_selective_claim``."""
    return run_fanxiu_mail_selective_claim(**kwargs)


def run_fanxiu_xianfu_visit_partner(
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    timeout_seconds: float = 0.0,
    wait_timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if timeout_seconds:
        payload["timeout_seconds"] = float(timeout_seconds)
    return submit_fanxiu_task_cell(
        "xianfu_visit_partner",
        payload,
        entry_id=entry_id,
        wait_timeout_seconds=wait_timeout_seconds,
    )


def run_fanxiu_xianfu_learn_skill(
    *,
    entry_id: str = DEFAULT_FANXIU_ENTRY_ID,
    timeout_seconds: float = 0.0,
    wait_timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if timeout_seconds:
        payload["timeout_seconds"] = float(timeout_seconds)
    return submit_fanxiu_task_cell(
        "xianfu_learn_skill",
        payload,
        entry_id=entry_id,
        wait_timeout_seconds=wait_timeout_seconds,
    )

