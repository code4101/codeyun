from __future__ import annotations

import os
import subprocess
import sys
import time
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

from backend.core.runtime.subprocess_utils import popen_python_script_background, python_script_command
from backend.core.settings import ROOT_DIR, get_settings


CODEYUN_WATCHDOG_SERVICE_KEY = "codeyun-watchdog"
CODEYUN_WATCHDOG_TITLE = "CodeYun 本机守护"
WATCHDOG_SCRIPT = ROOT_DIR / "scripts" / "codeyun_watchdog.py"
PYTHON_PROCESS_NAMES = {"py.exe", "py", "python.exe", "python", "pythonw.exe", "pythonw"}
LEGACY_WATCHDOG_LOCK_PATH = ROOT_DIR / ".codex-run" / "codeyun-watchdog.pid"


class CodeYunWatchdogError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodeYunWatchdogProcess:
    pid: int
    parent_pid: int | None
    name: str
    cmdline: str
    started_at: float | None = None


def get_codeyun_watchdog_log_path() -> Path:
    configured = (os.getenv("CODEYUN_WATCHDOG_LOG") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (_temp_runtime_dir() / "codeyun-watchdog.log").resolve(strict=False)


def get_codeyun_watchdog_lock_path() -> Path:
    configured = (os.getenv("CODEYUN_WATCHDOG_LOCK") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (_temp_runtime_dir() / "codeyun-watchdog.pid").resolve(strict=False)


def _temp_runtime_dir() -> Path:
    return Path(tempfile.gettempdir()) / "codeyun" / "codeyun-watchdog"


def _read_lock_pid() -> int | None:
    paths = [get_codeyun_watchdog_lock_path()]
    if not (os.getenv("CODEYUN_WATCHDOG_LOCK") or "").strip():
        paths.append(LEGACY_WATCHDOG_LOCK_PATH.resolve(strict=False))

    for path in paths:
        try:
            value = int(path.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            continue
        if value > 0 and psutil.pid_exists(value):
            return value
    return None


def _safe_cmdline(proc: psutil.Process) -> list[str]:
    try:
        return [str(part) for part in proc.cmdline()]
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return []


def _safe_name(proc: psutil.Process) -> str:
    try:
        return str(proc.name() or "")
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return ""


def _safe_ppid(proc: psutil.Process) -> int | None:
    try:
        return int(proc.ppid())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _safe_started_at(proc: psutil.Process) -> float | None:
    try:
        return float(proc.create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _ancestor_pids(pid: int | None) -> set[int]:
    if not pid:
        return set()
    try:
        return {int(proc.pid) for proc in psutil.Process(pid).parents()}
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return set()


def _matches_watchdog_process(proc: psutil.Process) -> bool:
    cmdline = " ".join(_safe_cmdline(proc)).lower().replace("/", "\\")
    script = str(WATCHDOG_SCRIPT).lower().replace("/", "\\")
    return (
        "--loop" in cmdline
        and "codeyun_watchdog.py" in cmdline
        and (script in cmdline or "scripts\\codeyun_watchdog.py" in cmdline)
    )


def list_codeyun_watchdog_processes() -> list[dict[str, Any]]:
    current_pid = os.getpid()
    lock_pid = _read_lock_pid()
    items: list[CodeYunWatchdogProcess] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        if int(proc.pid) == current_pid:
            continue
        if not _matches_watchdog_process(proc):
            continue
        items.append(
            CodeYunWatchdogProcess(
                pid=int(proc.pid),
                parent_pid=_safe_ppid(proc),
                name=_safe_name(proc),
                cmdline=" ".join(_safe_cmdline(proc)),
                started_at=_safe_started_at(proc),
            )
        )
    items.sort(key=lambda item: (0 if lock_pid and item.pid == lock_pid else 1, item.started_at or 0, item.pid))
    return [asdict(item) for item in items]


def get_codeyun_watchdog_status() -> dict[str, Any]:
    processes = list_codeyun_watchdog_processes()
    running = bool(processes)
    interval_seconds = int(os.getenv("CODEYUN_WATCHDOG_INTERVAL_SECONDS") or "60")
    reload_enabled = str(os.getenv("CODEYUN_WATCHDOG_RELOAD", "1")).strip().lower() not in {"0", "false", "no", "off"}
    reload_quiet_seconds = float(os.getenv("CODEYUN_WATCHDOG_RELOAD_QUIET_SECONDS") or "120")
    settings = get_settings()
    active_pid = _read_lock_pid()
    launcher_pids = _ancestor_pids(active_pid)
    active_processes = [
        item
        for item in processes
        if item.get("pid") is not None
        and item.get("pid") == active_pid
        and item.get("pid") not in launcher_pids
    ]
    effective_pids = [item["pid"] for item in active_processes if item.get("pid") is not None]
    return {
        "key": CODEYUN_WATCHDOG_SERVICE_KEY,
        "title": CODEYUN_WATCHDOG_TITLE,
        "running": running,
        "state": "running" if running else "stopped",
        "state_label": "运行中" if running else "已停止",
        "interval_seconds": interval_seconds,
        "reload_enabled": reload_enabled,
        "reload_quiet_seconds": reload_quiet_seconds,
        "backend_url": os.getenv("CODEYUN_WATCHDOG_BACKEND_URL") or "http://127.0.0.1:8000/api/health",
        "frontend_url": os.getenv("CODEYUN_WATCHDOG_FRONTEND_URL") or "http://127.0.0.1:5173/",
        "script_path": os.fspath(WATCHDOG_SCRIPT),
        "cwd": os.fspath(ROOT_DIR),
        "log_path": os.fspath(get_codeyun_watchdog_log_path()),
        "data_dir": os.fspath(settings.data_dir),
        "process_count": len(effective_pids) if active_pid else len(processes),
        "raw_process_count": len(processes),
        "processes": processes,
        "pids": effective_pids or [item["pid"] for item in processes if item.get("pid") is not None],
        "active_pid": active_pid,
        "launcher_pids": [
            item["pid"]
            for item in processes
            if item.get("pid") is not None and item.get("pid") in launcher_pids
        ],
        "stale_pids": [
            item["pid"]
            for item in processes
            if item.get("pid") is not None
            and item.get("pid") != active_pid
            and item.get("pid") not in launcher_pids
        ],
        "last_error": "" if WATCHDOG_SCRIPT.is_file() else f"脚本不存在：{WATCHDOG_SCRIPT}",
        "external": True,
        "controllable": True,
    }


def _resolve_watchdog_python_executable() -> str:
    return python_script_command(WATCHDOG_SCRIPT, preferred_root=ROOT_DIR, executable=sys.executable)[0]


def start_codeyun_watchdog(wait_seconds: float = 1.0) -> dict[str, Any]:
    status = get_codeyun_watchdog_status()
    if status.get("running"):
        return {"status": "started", "service": status}
    if not WATCHDOG_SCRIPT.is_file():
        raise CodeYunWatchdogError(f"脚本不存在：{WATCHDOG_SCRIPT}")

    log_path = get_codeyun_watchdog_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command_args = [
        "--loop",
        "--interval",
        str(os.getenv("CODEYUN_WATCHDOG_INTERVAL_SECONDS") or "60"),
    ]
    if str(os.getenv("CODEYUN_WATCHDOG_RELOAD", "1")).strip().lower() in {"0", "false", "no", "off"}:
        command.append("--no-reload")
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        with log_path.open("ab") as log_file:
            log_file.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] CodeYun start watchdog service\n".encode("utf-8")
            )
            proc = popen_python_script_background(
                WATCHDOG_SCRIPT,
                *command_args,
                preferred_root=ROOT_DIR,
                executable=sys.executable,
                cwd=os.fspath(ROOT_DIR),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
    except OSError as exc:
        raise CodeYunWatchdogError(f"启动 CodeYun 本机守护失败：{exc}") from exc

    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while time.monotonic() <= deadline:
        status = get_codeyun_watchdog_status()
        if status.get("running"):
            status["started_pid"] = proc.pid
            return {"status": "started", "service": status}
        if proc.poll() is not None:
            break
        time.sleep(0.1)

    status = get_codeyun_watchdog_status()
    status["started_pid"] = proc.pid
    if status.get("process_count"):
        return {"status": "starting", "service": status}
    raise CodeYunWatchdogError(f"已启动守护进程 PID {proc.pid}，但进程未保持运行。")


def stop_codeyun_watchdog(timeout: float = 5.0) -> dict[str, Any]:
    targets = [psutil.Process(item["pid"]) for item in list_codeyun_watchdog_processes()]
    for proc in targets:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    _gone, alive = psutil.wait_procs(targets, timeout=max(0.1, float(timeout)))
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    if alive:
        psutil.wait_procs(alive, timeout=2)
    return {
        "status": "stopped",
        "stopped_pids": [int(proc.pid) for proc in targets],
        "service": get_codeyun_watchdog_status(),
    }


def build_codeyun_watchdog_log_lines(limit: int = 200) -> list[str]:
    path = get_codeyun_watchdog_log_path()
    status = get_codeyun_watchdog_status()
    lines = [
        f"名称：{CODEYUN_WATCHDOG_TITLE}",
        f"状态：{status.get('state_label') or '-'}",
        f"间隔：{status.get('interval_seconds')} 秒",
        f"热加载：{'启用' if status.get('reload_enabled') else '关闭'}",
        f"静默期：{status.get('reload_quiet_seconds')} 秒",
        f"后端：{status.get('backend_url')}",
        f"前端：{status.get('frontend_url')}",
        f"脚本：{status.get('script_path')}",
        f"日志：{path}",
    ]
    pids = status.get("pids") or []
    if pids:
        lines.append(f"PID：{', '.join(str(pid) for pid in pids)}")
    if path.is_file():
        try:
            tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, int(limit)) :]
        except OSError:
            tail = []
        if tail:
            lines.extend(["", "最近日志：", *tail])
    return lines
