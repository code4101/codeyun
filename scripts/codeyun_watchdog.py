from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
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

from backend.core.runtime.process_launcher import popen_service, resolve_pythonw

try:
    import psutil
except ImportError:  # pragma: no cover - CodeYun runtime includes psutil.
    psutil = None


DEFAULT_BACKEND_URL = "http://127.0.0.1:8000/api/health"
DEFAULT_FRONTEND_URL = "http://127.0.0.1:5173/"
DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_STARTUP_GRACE_SECONDS = 180.0
DEFAULT_CONSOLE_HOST_STALE_SECONDS = 20.0
WATCHDOG_BACKEND_RELOAD_MODE_ENV = "CODEYUN_WATCHDOG_BACKEND_RELOAD_MODE"
PYTHON_PROCESS_NAMES = {"py.exe", "py", "python.exe", "python", "pythonw.exe", "pythonw", "uv.exe", "uv"}


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    cmdline: str
    started_at: float | None = None


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


def _console_host_status_path() -> Path:
    return Path(tempfile.gettempdir()) / "codeyun" / "codeyun-console-host.json"


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


def list_dev_component_processes() -> list[dict[str, Any]]:
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


def summarize_codeyun_dev_instance(dev_component_processes: list[dict[str, Any]]) -> dict[str, Any]:
    role_pids: dict[str, list[int]] = {}
    for item in dev_component_processes:
        role = _dev_process_role(item)
        role_pids.setdefault(role, []).append(int(item["pid"]))

    return {
        "running": bool(dev_component_processes),
        "instance_count": 1 if dev_component_processes else 0,
        "component_pids": role_pids,
    }


def terminate_dev_processes(timeout: float, log_path: Path) -> list[int]:
    if psutil is None:
        _log(log_path, "psutil is unavailable; cannot clean stale dev processes.")
        return []
    targets = []
    for item in list_dev_component_processes():
        try:
            targets.append(psutil.Process(item["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
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


def _dev_process_startup_age(dev_component_processes: list[dict[str, Any]]) -> float | None:
    started_at_values = [
        float(item["started_at"])
        for item in dev_component_processes
        if item.get("started_at") is not None
    ]
    if not started_at_values:
        return None
    return max(0.0, time.time() - max(started_at_values))


def _resolve_dev_start_command() -> list[str]:
    python_executable = resolve_pythonw(PROJECT_ROOT, sys.executable)
    if os.name == "nt" and Path(python_executable).name.lower() == "pythonw.exe":
        return [python_executable, "dev.py"]
    uv_path = shutil.which("uv") or "uv"
    return [uv_path, "run", "dev.py"]


def start_detached_dev(log_path: Path, stdout_path: Path, stderr_path: Path) -> int:
    command = _resolve_dev_start_command()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["CODEYUN_DEV_BACKEND_RELOAD_MODE"] = os.getenv(WATCHDOG_BACKEND_RELOAD_MODE_ENV, "outer").strip() or "outer"
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


def read_console_host_status(*, max_age_seconds: float = DEFAULT_CONSOLE_HOST_STALE_SECONDS) -> dict[str, Any]:
    path = _console_host_status_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"running": False, "status_path": os.fspath(path)}
    status = data if isinstance(data, dict) else {}
    pid = int(status.get("pid") or 0)
    heartbeat_at = float(status.get("heartbeat_at") or 0.0)
    age_seconds = max(0.0, time.time() - heartbeat_at) if heartbeat_at else None
    root_matches = _path_in_project(str(status.get("root_dir") or ""))
    alive = _pid_alive(pid)
    fresh = age_seconds is not None and age_seconds <= max(1.0, float(max_age_seconds))
    status.update(
        {
            "running": bool(alive and fresh and root_matches),
            "alive": alive,
            "fresh": fresh,
            "root_matches": root_matches,
            "heartbeat_age_seconds": age_seconds,
            "status_path": os.fspath(path),
        }
    )
    return status


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


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    log_path = Path(args.log_path).resolve(strict=False)
    console_host = read_console_host_status(max_age_seconds=max(DEFAULT_CONSOLE_HOST_STALE_SECONDS, float(args.interval) * 2.5))
    health = check_health(args.backend_url, args.frontend_url, args.timeout)
    if console_host.get("running"):
        _log(
            log_path,
            "Console host is alive; watchdog will not restart or hot-reload dev runner. "
            f"pid={console_host.get('pid')} health={'ok' if health['healthy'] else 'failed'}",
        )
        return {
            "status": "console_host_observed",
            "health": health,
            "console_host": console_host,
            "started_pid": None,
        }
    if health["healthy"]:
        _log(log_path, "Health check ok; no restart needed.")
        return {
            "status": "healthy",
            "health": health,
            "console_host": console_host,
            "started_pid": None,
        }

    dev_component_processes = list_dev_component_processes()
    startup_age = _dev_process_startup_age(dev_component_processes)
    if dev_component_processes and startup_age is not None and startup_age < args.startup_grace:
        _log(
            log_path,
            "Health check failed, but CodeYun dev runner is still starting; "
            f"waiting. age={startup_age:.1f}s grace={args.startup_grace:.1f}s "
            f"backend={health['backend']['message']} frontend={health['frontend']['message']}",
        )
        return {
            "status": "starting",
            "health": health,
            "console_host": console_host,
            "dev_component_processes": dev_component_processes,
            "startup_age": startup_age,
            "started_pid": None,
        }

    restart = _restart_dev_runner(
        args,
        log_path,
        "Health check failed "
        f"(backend={health['backend']['message']} frontend={health['frontend']['message']})",
    )
    return {
        "status": "restarted",
        "health": health,
        "console_host": console_host,
        **restart,
    }


def run_loop(args: argparse.Namespace) -> int:
    lock_path = Path(args.lock_path).resolve(strict=False)
    log_path = Path(args.log_path).resolve(strict=False)
    if not acquire_lock(lock_path, log_path):
        return 0
    _log(log_path, f"CodeYun watchdog loop started. interval={args.interval}s")
    terminate_stale_watchdog_processes(lock_path, log_path)
    try:
        while True:
            run_once(args)
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
        "--startup-grace",
        type=float,
        default=float(os.getenv("CODEYUN_WATCHDOG_STARTUP_GRACE_SECONDS", DEFAULT_STARTUP_GRACE_SECONDS)),
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
        dev_component_processes = list_dev_component_processes()
        codeyun_instance = summarize_codeyun_dev_instance(dev_component_processes)
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
            "console_host": read_console_host_status(),
            "codeyun_instance": codeyun_instance,
            "dev_component_processes": dev_component_processes,
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
