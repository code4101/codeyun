from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(PROJECT_ROOT))

from backend.core.runtime.process_launcher import popen_service, resolve_pythonw, run_quiet
from scripts.codeyun_popup_audit import ensure_monitor_running
from scripts.codeyun_visible_console_monitor import read_status as read_visible_console_monitor_status

try:
    import psutil
except ImportError:  # pragma: no cover - CodeYun runtime includes psutil.
    psutil = None


DEFAULT_BACKEND_URL = "http://127.0.0.1:8000/api/health"
DEFAULT_FRONTEND_URL = "http://127.0.0.1:5173/"
DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_STARTUP_GRACE_SECONDS = 180.0
DEFAULT_RELOAD_QUIET_SECONDS = 120.0
DEFAULT_VISIBLE_CONSOLE_MONITOR_ENABLED = True
PYTHON_PROCESS_NAMES = {"py.exe", "py", "python.exe", "python", "pythonw.exe", "pythonw", "uv.exe", "uv"}
RELOAD_WATCH_TARGETS = ("backend", "scripts", "dev.py", "pyproject.toml", "uv.lock", ".env")
RELOAD_WATCH_EXTENSIONS = {".env", ".ini", ".json", ".py", ".toml", ".yaml", ".yml"}
IGNORED_WATCH_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    cmdline: str
    started_at: float | None = None


@dataclass
class WatchdogState:
    reload_snapshot: dict[str, tuple[int, int]] | None = None
    pending_reload_reason: str | None = None
    pending_reload_since: float | None = None
    last_restart_at: float | None = None


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _default_log_path() -> Path:
    return _temp_runtime_dir() / "codeyun-watchdog.log"


def _default_dev_stdout_path() -> Path:
    return _temp_runtime_dir() / "codeyun-dev.out.log"


def _default_dev_stderr_path() -> Path:
    return _temp_runtime_dir() / "codeyun-dev.err.log"


def _default_lock_path() -> Path:
    return _temp_runtime_dir() / "codeyun-watchdog.pid"


def _temp_runtime_dir() -> Path:
    return Path(tempfile.gettempdir()) / "codeyun" / "codeyun-watchdog"


def _log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{_now()}] {message}\n"
    with path.open("a", encoding="utf-8") as file:
        file.write(line)
    print(line, end="", flush=True)


def _request_ok(url: str, timeout: float) -> tuple[bool, str]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "codeyun-watchdog"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 0)
            return 200 <= status < 500, f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        return 200 <= int(exc.code) < 500, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


def check_health(backend_url: str, frontend_url: str, timeout: float) -> dict[str, Any]:
    backend_ok, backend_message = _request_ok(backend_url, timeout)
    frontend_ok, frontend_message = _request_ok(frontend_url, timeout)
    return {
        "healthy": backend_ok and frontend_ok,
        "backend": {"url": backend_url, "ok": backend_ok, "message": backend_message},
        "frontend": {"url": frontend_url, "ok": frontend_ok, "message": frontend_message},
    }


def _safe_name(proc: Any) -> str:
    try:
        return str(proc.name() or "")
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return ""


def _safe_cmdline(proc: Any) -> list[str]:
    try:
        return [str(part) for part in proc.cmdline()]
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return []


def _safe_started_at(proc: Any) -> float | None:
    try:
        return float(proc.create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _cmdline_text(proc: Any) -> str:
    return " ".join(_safe_cmdline(proc))


def _safe_cwd(proc: Any) -> str:
    try:
        return str(proc.cwd() or "")
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return ""


def _path_in_project(text: str) -> bool:
    normalized_text = text.lower().replace("/", "\\")
    root_text = str(PROJECT_ROOT).lower().replace("/", "\\")
    return root_text in normalized_text


def _process_in_project(proc: Any) -> bool:
    return _path_in_project(_cmdline_text(proc)) or _path_in_project(_safe_cwd(proc))


def _matches_watchdog(proc: Any) -> bool:
    cmdline = _cmdline_text(proc).lower()
    return "scripts" in cmdline and "codeyun_watchdog.py" in cmdline and "--loop" in cmdline and _process_in_project(proc)


def _matches_codeyun_dev(proc: Any) -> bool:
    name = _safe_name(proc).lower()
    cmdline = _cmdline_text(proc).lower()
    if not _process_in_project(proc):
        return False
    if "codeyun_watchdog.py" in cmdline:
        return False
    if "compileall" in cmdline:
        return False
    if "dev.py" in cmdline:
        return True
    if name in {"uv.exe", "uv"} and "run" in cmdline and "dev.py" in cmdline:
        return True
    if "uvicorn" in cmdline and ("backend.app:app" in cmdline or "backend.core.runtime.uvicorn_hidden" in cmdline):
        return True
    if name in {"node.exe", "node"} and "vite" in cmdline and " build" not in cmdline:
        return True
    if name in {"cmd.exe", "cmd"} and (
        ("vite" in cmdline and " build" not in cmdline)
        or ("npm" in cmdline and " dev" in cmdline)
    ):
        return True
    return False


def _iter_processes():
    if psutil is None:
        return []
    return psutil.process_iter(["pid", "name", "cmdline", "create_time"])


def list_watchdog_processes() -> list[dict[str, Any]]:
    current_pid = os.getpid()
    items: list[ProcessInfo] = []
    for proc in _iter_processes():
        if int(proc.pid) == current_pid:
            continue
        if not _matches_watchdog(proc):
            continue
        items.append(
            ProcessInfo(
                pid=int(proc.pid),
                name=_safe_name(proc),
                cmdline=_cmdline_text(proc),
                started_at=_safe_started_at(proc),
            )
        )
    return [asdict(item) for item in sorted(items, key=lambda item: (item.started_at or 0, item.pid))]


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return None
    if pid <= 0 or not _pid_alive(pid):
        return None
    return pid


def _watchdog_ancestor_pids(pid: int | None) -> set[int]:
    if psutil is None or not pid:
        return set()
    try:
        return {int(proc.pid) for proc in psutil.Process(pid).parents()}
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return set()


def terminate_stale_watchdog_processes(lock_path: Path, log_path: Path, timeout: float = 2.0) -> list[int]:
    if psutil is None:
        return []

    current_pid = os.getpid()
    active_pid = _read_lock_pid(lock_path) or current_pid
    ancestor_pids = _watchdog_ancestor_pids(current_pid)
    targets: list[Any] = []
    for proc in _iter_processes():
        try:
            pid = int(proc.pid)
        except (TypeError, ValueError):
            continue
        if pid in {current_pid, active_pid} or pid in ancestor_pids:
            continue
        if not _matches_watchdog(proc):
            continue
        targets.append(proc)

    stopped: list[int] = []
    for proc in targets:
        try:
            stopped.append(int(proc.pid))
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
    if targets:
        _gone, alive = psutil.wait_procs(targets, timeout=max(0.1, float(timeout)))
        for proc in alive:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                pass
        if alive:
            psutil.wait_procs(alive, timeout=1)
    if stopped:
        _log(log_path, f"Stopped stale CodeYun watchdog processes: {', '.join(str(pid) for pid in stopped)}")
    return stopped


def list_dev_processes() -> list[dict[str, Any]]:
    current_pid = os.getpid()
    items: list[ProcessInfo] = []
    for proc in _iter_processes():
        if int(proc.pid) == current_pid:
            continue
        if not _matches_codeyun_dev(proc):
            continue
        items.append(
            ProcessInfo(
                pid=int(proc.pid),
                name=_safe_name(proc),
                cmdline=_cmdline_text(proc),
                started_at=_safe_started_at(proc),
            )
        )
    return [asdict(item) for item in sorted(items, key=lambda item: (item.started_at or 0, item.pid))]


def _dev_process_role(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").lower()
    cmdline = str(item.get("cmdline") or "").lower()
    if "dev.py" in cmdline:
        return "dev_runner"
    if "uvicorn" in cmdline or "backend.core.runtime.uvicorn_hidden" in cmdline or "backend.app:app" in cmdline:
        return "backend"
    if name in {"node.exe", "node"} and "vite" in cmdline and " build" not in cmdline:
        return "frontend"
    if name in {"cmd.exe", "cmd"}:
        return "shell_wrapper"
    return "other"


def summarize_codeyun_dev_instance(dev_processes: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize raw component processes as the single local CodeYun instance.

    Windows pythonw/uvicorn launchers can appear as parent/child pairs, so raw
    process count is not the same thing as instance count.
    """

    role_pids: dict[str, list[int]] = {}
    for item in dev_processes:
        role = _dev_process_role(item)
        role_pids.setdefault(role, []).append(int(item["pid"]))

    return {
        "running": bool(dev_processes),
        "instance_count": 1 if dev_processes else 0,
        "single_instance_ok": bool(dev_processes),
        "raw_component_process_count": len(dev_processes),
        "component_pids": role_pids,
        "note": (
            "raw_component_process_count includes dev.py wrappers, backend uvicorn workers, "
            "and the Vite frontend; use instance_count to decide whether CodeYun is single-instance. "
            "Do not count component PIDs as separate CodeYun instances."
        ),
    }


def terminate_dev_processes(timeout: float, log_path: Path) -> list[int]:
    if psutil is None:
        _log(log_path, "psutil is unavailable; cannot clean stale dev processes.")
        return []
    targets = [psutil.Process(item["pid"]) for item in list_dev_processes()]
    if not targets:
        return []

    stopped: list[int] = []
    for proc in targets:
        try:
            stopped.append(int(proc.pid))
            for child in proc.children(recursive=True):
                if _matches_watchdog(child):
                    continue
                try:
                    child.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    pass
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass

    _, alive = psutil.wait_procs(targets, timeout=max(0.1, float(timeout)))
    for proc in alive:
        try:
            for child in proc.children(recursive=True):
                if _matches_watchdog(child):
                    continue
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    pass
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
    if alive:
        psutil.wait_procs(alive, timeout=2)
    _log(log_path, f"Stopped stale CodeYun dev processes: {', '.join(str(pid) for pid in stopped)}")
    return stopped


def _dev_process_startup_age(dev_processes: list[dict[str, Any]]) -> float | None:
    started_at_values = [
        float(item["started_at"])
        for item in dev_processes
        if item.get("started_at") is not None
    ]
    if not started_at_values:
        return None
    return max(0.0, time.time() - max(started_at_values))


def _resolve_uv_command() -> list[str]:
    configured = os.getenv("CODEYUN_WATCHDOG_DEV_COMMAND")
    if configured and configured.strip():
        return shlex.split(configured, posix=os.name != "nt")
    python_executable = resolve_pythonw(PROJECT_ROOT, sys.executable)
    if os.name == "nt" and Path(python_executable).name.lower() == "pythonw.exe":
        return [python_executable, "dev.py"]
    uv_path = shutil.which("uv") or "uv"
    return [uv_path, "run", "dev.py"]


def _resolve_reload_check_command() -> list[str]:
    configured = os.getenv("CODEYUN_WATCHDOG_RELOAD_CHECK_COMMAND")
    if configured is not None:
        if not configured.strip():
            return []
        return shlex.split(configured, posix=os.name != "nt")
    return [resolve_pythonw(PROJECT_ROOT, sys.executable), "-m", "compileall", "-q", "backend", "scripts", "dev.py"]


def start_detached_dev(log_path: Path, stdout_path: Path, stderr_path: Path) -> int:
    command = _resolve_uv_command()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    with stdout_path.open("ab") as stdout_file, stderr_path.open("ab") as stderr_file:
        stdout_file.write(f"\n[{_now()}] CodeYun watchdog detached start: {' '.join(command)}\n".encode("utf-8"))
        proc = popen_service(
            command,
            cwd=os.fspath(PROJECT_ROOT),
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
        )
    _log(log_path, f"Started detached CodeYun dev runner PID {proc.pid}.")
    return int(proc.pid)


def _watch_file_candidate(path: Path) -> bool:
    name = path.name
    if name.startswith(".env"):
        return True
    return path.suffix.lower() in RELOAD_WATCH_EXTENSIONS


def _iter_reload_watch_files(root: Path = PROJECT_ROOT):
    for rel_target in RELOAD_WATCH_TARGETS:
        target = root / rel_target
        if target.is_dir():
            for current_root, dirnames, filenames in os.walk(target):
                dirnames[:] = [name for name in dirnames if name not in IGNORED_WATCH_DIRS]
                for filename in filenames:
                    path = Path(current_root) / filename
                    if _watch_file_candidate(path):
                        yield path.resolve(strict=False)
            continue
        if target.is_file() and _watch_file_candidate(target):
            yield target.resolve(strict=False)


def build_reload_snapshot(root: Path = PROJECT_ROOT) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for path in _iter_reload_watch_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[os.fspath(path)] = (int(stat.st_mtime_ns), int(stat.st_size))
    return snapshot


def describe_reload_snapshot_change(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> str:
    before_keys = set(before)
    after_keys = set(after)
    changed = sorted(path for path in before_keys & after_keys if before[path] != after[path])
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    if changed:
        return f"changed: {os.path.relpath(changed[0], PROJECT_ROOT)}"
    if added:
        return f"added: {os.path.relpath(added[0], PROJECT_ROOT)}"
    if removed:
        return f"removed: {os.path.relpath(removed[0], PROJECT_ROOT)}"
    return "watched files changed"


def run_reload_precheck(args: argparse.Namespace, log_path: Path) -> bool:
    command = _resolve_reload_check_command()
    if not command:
        _log(log_path, "Reload precheck skipped because CODEYUN_WATCHDOG_RELOAD_CHECK_COMMAND is empty.")
        return True
    _log(log_path, f"Reload precheck: {' '.join(command)}")
    try:
        result = run_quiet(
            command,
            cwd=os.fspath(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(args.reload_check_timeout)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(log_path, f"Reload precheck failed to run: {exc}")
        return False
    output = (result.stdout or "").strip()
    if output:
        for line in output.splitlines()[-40:]:
            _log(log_path, f"precheck> {line}")
    if result.returncode != 0:
        _log(log_path, f"Reload precheck failed with exit code {result.returncode}; keeping current service.")
        return False
    _log(log_path, "Reload precheck passed.")
    return True


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if psutil is not None:
        return psutil.pid_exists(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(lock_path: Path, log_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            old_pid = 0
        if old_pid and _pid_alive(old_pid):
            _log(log_path, f"Another watchdog is already running: PID {old_pid}.")
            return False
        try:
            lock_path.unlink()
        except OSError:
            pass
    try:
        fd = os.open(os.fspath(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(str(os.getpid()))
    return True


def release_lock(lock_path: Path) -> None:
    try:
        if lock_path.exists() and lock_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock_path.unlink()
    except OSError:
        pass


def _restart_dev_runner(args: argparse.Namespace, log_path: Path, reason: str) -> dict[str, Any]:
    _log(log_path, f"{reason}; restarting dev runner.")
    stopped_pids = terminate_dev_processes(args.stop_timeout, log_path)
    started_pid = start_detached_dev(log_path, Path(args.dev_stdout), Path(args.dev_stderr))
    return {"stopped_pids": stopped_pids, "started_pid": started_pid}


def _ensure_visible_console_monitor(args: argparse.Namespace, log_path: Path) -> dict[str, Any] | None:
    if not getattr(args, "visible_console_monitor", DEFAULT_VISIBLE_CONSOLE_MONITOR_ENABLED):
        return None
    try:
        status = ensure_monitor_running()
    except Exception as exc:
        _log(log_path, f"Visible console monitor check failed: {exc}")
        return {"ok": False, "error": str(exc)}
    if status.get("started_now"):
        _log(log_path, f"Visible console monitor started: PID {status.get('pid')}")
    return {"ok": True, **status}


def _ensure_critical_command_services(log_path: Path) -> dict[str, Any] | None:
    try:
        from backend.core.runtime.management import ensure_local_critical_command_services

        result = ensure_local_critical_command_services()
    except Exception as exc:
        _log(log_path, f"Critical command service guard failed: {exc}")
        return {"ok": False, "error": str(exc)}

    for item in result.get("started") or []:
        name = item.get("name") or item.get("id")
        pid = ((item.get("result") or {}) if isinstance(item, dict) else {}).get("pid")
        _log(log_path, f"Critical command service started: {name} PID {pid}")
    for item in result.get("errors") or []:
        _log(log_path, f"Critical command service error: {item.get('name') or item.get('id')} {item.get('error')}")
    return {"ok": not bool(result.get("errors")), **result}


def _maybe_handle_stable_reload(args: argparse.Namespace, state: WatchdogState, log_path: Path) -> dict[str, Any] | None:
    if not args.reload:
        return None

    latest_snapshot = build_reload_snapshot()
    if state.reload_snapshot is None:
        state.reload_snapshot = latest_snapshot
        return None

    now = time.time()
    if latest_snapshot != state.reload_snapshot:
        reason = describe_reload_snapshot_change(state.reload_snapshot, latest_snapshot)
        state.reload_snapshot = latest_snapshot
        state.pending_reload_reason = reason
        state.pending_reload_since = now
        _log(
            log_path,
            "Watched files changed "
            f"({reason}); waiting {float(args.reload_quiet):.1f}s of quiet time before restart.",
        )
        return {
            "status": "reload_pending",
            "reason": reason,
            "quiet_seconds": 0.0,
            "started_pid": None,
        }

    if state.pending_reload_since is None:
        return None

    quiet_seconds = now - state.pending_reload_since
    if quiet_seconds < float(args.reload_quiet):
        return {
            "status": "reload_pending",
            "reason": state.pending_reload_reason,
            "quiet_seconds": quiet_seconds,
            "started_pid": None,
        }

    reason = state.pending_reload_reason or "watched files changed"
    _log(log_path, f"Reload quiet period reached after {quiet_seconds:.1f}s for {reason}.")
    if not run_reload_precheck(args, log_path):
        state.pending_reload_since = time.time()
        return {
            "status": "reload_precheck_failed",
            "reason": reason,
            "started_pid": None,
        }

    restart = _restart_dev_runner(args, log_path, f"Reload precheck passed for {reason}")
    state.pending_reload_reason = None
    state.pending_reload_since = None
    state.reload_snapshot = build_reload_snapshot()
    state.last_restart_at = time.time()
    return {
        "status": "reloaded",
        "reason": reason,
        **restart,
    }


def run_once(args: argparse.Namespace, state: WatchdogState | None = None) -> dict[str, Any]:
    log_path = Path(args.log_path).resolve(strict=False)
    monitor_status = _ensure_visible_console_monitor(args, log_path)
    critical_services = _ensure_critical_command_services(log_path)
    health = check_health(args.backend_url, args.frontend_url, args.timeout)
    if health["healthy"]:
        reload_result = _maybe_handle_stable_reload(args, state, log_path) if state is not None else None
        if reload_result is not None:
            return {
                "health": health,
                "visible_console_monitor": monitor_status,
                "critical_command_services": critical_services,
                **reload_result,
            }
        _log(log_path, "Health check ok; no restart needed.")
        return {
            "status": "healthy",
            "health": health,
            "visible_console_monitor": monitor_status,
            "critical_command_services": critical_services,
            "started_pid": None,
        }

    dev_processes = list_dev_processes()
    startup_age = _dev_process_startup_age(dev_processes)
    if dev_processes and startup_age is not None and startup_age < args.startup_grace:
        _log(
            log_path,
            "Health check failed, but CodeYun dev runner is still starting; "
            f"waiting. age={startup_age:.1f}s grace={args.startup_grace:.1f}s "
            f"backend={health['backend']['message']} frontend={health['frontend']['message']}",
        )
        return {
            "status": "starting",
            "health": health,
            "visible_console_monitor": monitor_status,
            "critical_command_services": critical_services,
            "dev_processes": dev_processes,
            "startup_age": startup_age,
            "started_pid": None,
        }

    restart = _restart_dev_runner(
        args,
        log_path,
        "Health check failed "
        f"(backend={health['backend']['message']} frontend={health['frontend']['message']})",
    )
    if state is not None:
        state.reload_snapshot = build_reload_snapshot()
        state.pending_reload_reason = None
        state.pending_reload_since = None
        state.last_restart_at = time.time()
    return {
        "status": "restarted",
        "health": health,
        "visible_console_monitor": monitor_status,
        "critical_command_services": critical_services,
        **restart,
    }


def run_loop(args: argparse.Namespace) -> int:
    lock_path = Path(args.lock_path).resolve(strict=False)
    log_path = Path(args.log_path).resolve(strict=False)
    if not acquire_lock(lock_path, log_path):
        return 0
    state = WatchdogState(reload_snapshot=build_reload_snapshot() if args.reload else None)
    _log(
        log_path,
        f"CodeYun watchdog loop started. interval={args.interval}s "
        f"reload={'on' if args.reload else 'off'} quiet={float(args.reload_quiet):.1f}s",
    )
    terminate_stale_watchdog_processes(lock_path, log_path)
    try:
        while True:
            run_once(args, state)
            time.sleep(max(1, int(args.interval)))
    finally:
        release_lock(lock_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CodeYun detached local dev watchdog")
    parser.add_argument("--backend-url", default=os.getenv("CODEYUN_WATCHDOG_BACKEND_URL", DEFAULT_BACKEND_URL))
    parser.add_argument("--frontend-url", default=os.getenv("CODEYUN_WATCHDOG_FRONTEND_URL", DEFAULT_FRONTEND_URL))
    parser.add_argument("--interval", type=int, default=int(os.getenv("CODEYUN_WATCHDOG_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("CODEYUN_WATCHDOG_REQUEST_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)))
    parser.add_argument("--stop-timeout", type=float, default=float(os.getenv("CODEYUN_WATCHDOG_STOP_TIMEOUT", "8")))
    parser.add_argument(
        "--reload",
        action=argparse.BooleanOptionalAction,
        default=str(os.getenv("CODEYUN_WATCHDOG_RELOAD", "1")).strip().lower() not in {"0", "false", "no", "off"},
        help="Watch backend/dev files and restart after a quiet period when precheck passes.",
    )
    parser.add_argument(
        "--reload-quiet",
        type=float,
        default=float(os.getenv("CODEYUN_WATCHDOG_RELOAD_QUIET_SECONDS", DEFAULT_RELOAD_QUIET_SECONDS)),
    )
    parser.add_argument(
        "--reload-check-timeout",
        type=float,
        default=float(os.getenv("CODEYUN_WATCHDOG_RELOAD_CHECK_TIMEOUT_SECONDS", "120")),
    )
    parser.add_argument(
        "--startup-grace",
        type=float,
        default=float(os.getenv("CODEYUN_WATCHDOG_STARTUP_GRACE_SECONDS", DEFAULT_STARTUP_GRACE_SECONDS)),
    )
    parser.add_argument(
        "--visible-console-monitor",
        action=argparse.BooleanOptionalAction,
        default=str(os.getenv("CODEYUN_VISIBLE_CONSOLE_MONITOR", "1")).strip().lower()
        not in {"0", "false", "no", "off"},
        help="Keep the visible console popup monitor alive while the watchdog is running.",
    )
    parser.add_argument("--log-path", default=os.getenv("CODEYUN_WATCHDOG_LOG", os.fspath(_default_log_path())))
    parser.add_argument("--dev-stdout", default=os.getenv("CODEYUN_DEV_STDOUT_LOG", os.fspath(_default_dev_stdout_path())))
    parser.add_argument("--dev-stderr", default=os.getenv("CODEYUN_DEV_STDERR_LOG", os.fspath(_default_dev_stderr_path())))
    parser.add_argument("--lock-path", default=os.getenv("CODEYUN_WATCHDOG_LOCK", os.fspath(_default_lock_path())))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="Run one health check and exit.")
    group.add_argument("--loop", action="store_true", help="Run forever.")
    group.add_argument("--status", action="store_true", help="Print detected watchdog/dev processes.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.status:
        lock_path = Path(args.lock_path).resolve(strict=False)
        active_pid = _read_lock_pid(lock_path)
        launcher_pids = _watchdog_ancestor_pids(active_pid)
        watchdog_processes = list_watchdog_processes()
        dev_processes = list_dev_processes()
        codeyun_instance = summarize_codeyun_dev_instance(dev_processes)
        status = {
            "watchdog": {
                "active_pid": active_pid,
                "instance_count": 1 if active_pid else 0,
                "launcher_pids": [
                    item["pid"] for item in watchdog_processes if item.get("pid") in launcher_pids
                ],
                "stale_pids": [
                    item["pid"]
                    for item in watchdog_processes
                    if item.get("pid") != active_pid and item.get("pid") not in launcher_pids
                ],
                "processes": watchdog_processes,
                "note": "launcher_pids are wrapper parents of the active watchdog, not extra watchdog instances.",
            },
            "visible_console_monitor": read_visible_console_monitor_status(),
            "codeyun_instance": codeyun_instance,
            "dev_component_processes": dev_processes,
            "dev_processes": {
                "deprecated": True,
                "summary": codeyun_instance,
                "raw_list_field": "dev_component_processes",
                "note": "This is an instance summary, not a raw process list.",
            },
        }
        print(json.dumps(status, ensure_ascii=False))
        return 0
    if args.loop:
        return run_loop(args)
    result = run_once(args)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
