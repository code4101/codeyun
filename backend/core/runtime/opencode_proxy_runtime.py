"""Runtime management for the standalone OpenCode Go Responses proxy.

The proxy is a decoupled process (see ``backend.services.opencode_proxy``); this
module lets CodeYun's runtime page start, stop and inspect it without owning its
lifetime.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests
from pyxllib.prog import process_runtime

from backend.core.services.launcher import popen_python_module_service
from backend.core.settings import ROOT_DIR, get_settings


OPENCODE_PROXY_SERVICE_KEY = "opencode-proxy"
OPENCODE_PROXY_TITLE = "OpenCode Go 代理"
OPENCODE_PROXY_MODULE = "backend.services.opencode_proxy"
OPENCODE_PROXY_DEFAULT_HOST = "127.0.0.1"
OPENCODE_PROXY_DEFAULT_PORT = 8787
PYTHON_PROCESS_NAMES = {"py.exe", "py", "python.exe", "python", "pythonw.exe", "pythonw"}


class OpenCodeProxyError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenCodeProxyProcess:
    pid: int
    parent_pid: int | None
    name: str
    cmdline: str
    started_at: float | None = None


def get_opencode_proxy_host() -> str:
    return (os.environ.get("CODEYUN_OPENCODE_PROXY_HOST") or OPENCODE_PROXY_DEFAULT_HOST).strip()


def get_opencode_proxy_port() -> int:
    try:
        return int(os.environ.get("CODEYUN_OPENCODE_PROXY_PORT") or OPENCODE_PROXY_DEFAULT_PORT)
    except ValueError:
        return OPENCODE_PROXY_DEFAULT_PORT


def get_opencode_proxy_base_url() -> str:
    return f"http://{get_opencode_proxy_host()}:{get_opencode_proxy_port()}"


def get_opencode_proxy_log_path() -> Path:
    configured = (os.environ.get("CODEYUN_OPENCODE_PROXY_LOG") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (get_settings().data_dir / "logs" / "opencode-proxy.log").resolve(strict=False)


def _safe_cmdline(proc: Any) -> list[str]:
    try:
        return [str(part) for part in proc.cmdline()]
    except Exception:  # noqa: BLE001
        return []


def _safe_name(proc: Any) -> str:
    try:
        return str(proc.name() or "")
    except Exception:  # noqa: BLE001
        return ""


def _safe_ppid(proc: Any) -> int | None:
    try:
        return int(proc.ppid())
    except Exception:  # noqa: BLE001
        return None


def _safe_started_at(proc: Any) -> float | None:
    try:
        return float(proc.create_time())
    except Exception:  # noqa: BLE001
        return None


def _matches_opencode_proxy_process(proc: Any) -> bool:
    cmdline = _safe_cmdline(proc)
    if not cmdline:
        return False
    for index, part in enumerate(cmdline[:-1]):
        if part == "-m" and cmdline[index + 1] == OPENCODE_PROXY_MODULE:
            return True
    return OPENCODE_PROXY_MODULE in " ".join(cmdline)


def list_opencode_proxy_processes() -> list[dict[str, Any]]:
    current_pid = os.getpid()
    items: list[OpenCodeProxyProcess] = []
    for proc in process_runtime.process_candidates_by_name(PYTHON_PROCESS_NAMES):
        if int(proc.pid) == current_pid:
            continue
        if not _matches_opencode_proxy_process(proc):
            continue
        items.append(
            OpenCodeProxyProcess(
                pid=int(proc.pid),
                parent_pid=_safe_ppid(proc),
                name=_safe_name(proc),
                cmdline=" ".join(_safe_cmdline(proc)),
                started_at=_safe_started_at(proc),
            )
        )
    matched_pids = {item.pid for item in items}
    items = [item for item in items if item.parent_pid not in matched_pids]
    items.sort(key=lambda item: (item.started_at or 0, item.pid))
    return [asdict(item) for item in items]


def _probe_health() -> bool:
    try:
        response = requests.get(f"{get_opencode_proxy_base_url()}/health", timeout=2)
        return response.status_code == 200
    except requests.RequestException:
        return False


def get_opencode_proxy_status(*, probe: bool = True) -> dict[str, Any]:
    processes = list_opencode_proxy_processes()
    running = bool(processes)
    healthy = _probe_health() if (probe and running) else False
    return {
        "key": OPENCODE_PROXY_SERVICE_KEY,
        "title": OPENCODE_PROXY_TITLE,
        "running": running,
        "state": "running" if running else "stopped",
        "state_label": "运行中" if running else "已停止",
        "healthy": healthy,
        "host": get_opencode_proxy_host(),
        "port": get_opencode_proxy_port(),
        "base_url": get_opencode_proxy_base_url(),
        "module": OPENCODE_PROXY_MODULE,
        "cwd": os.fspath(ROOT_DIR),
        "log_path": os.fspath(get_opencode_proxy_log_path()),
        "process_count": len(processes),
        "processes": processes,
        "pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "external": True,
        "controllable": True,
    }


def start_opencode_proxy(wait_seconds: float = 5.0) -> dict[str, Any]:
    status = get_opencode_proxy_status(probe=False)
    if status.get("running"):
        return {"status": "started", "service": status}

    log_path = get_opencode_proxy_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    command_args = ["--host", get_opencode_proxy_host(), "--port", str(get_opencode_proxy_port())]
    try:
        with log_path.open("ab") as log_file:
            log_file.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] CodeYun start opencode proxy\n".encode("utf-8")
            )
            proc = popen_python_module_service(
                OPENCODE_PROXY_MODULE,
                *command_args,
                preferred_root=ROOT_DIR,
                cwd=os.fspath(ROOT_DIR),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
    except OSError as exc:
        raise OpenCodeProxyError(f"启动 OpenCode 代理失败：{exc}") from exc

    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while time.monotonic() <= deadline:
        status = get_opencode_proxy_status(probe=False)
        if status.get("running"):
            status["started_pid"] = proc.pid
            return {"status": "started", "service": status}
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    status = get_opencode_proxy_status(probe=False)
    status["started_pid"] = proc.pid
    if status.get("process_count"):
        return {"status": "starting", "service": status}
    raise OpenCodeProxyError(f"已启动 OpenCode 代理 PID {proc.pid}，但进程未保持运行。")


def stop_opencode_proxy(timeout: float = 5.0) -> dict[str, Any]:
    processes = list_opencode_proxy_processes()
    for item in processes:
        pid = item.get("pid")
        if pid is None:
            continue
        process_runtime.terminate_process_tree(int(pid), timeout=timeout)
    time.sleep(0.2)
    return {
        "status": "stopped",
        "stopped_pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "service": get_opencode_proxy_status(probe=False),
    }


def build_opencode_proxy_log_lines(limit: int = 200) -> list[str]:
    status = get_opencode_proxy_status()
    path = get_opencode_proxy_log_path()
    lines = [
        f"名称：{OPENCODE_PROXY_TITLE}",
        f"状态：{status.get('state_label') or '-'}",
        f"地址：{status.get('base_url')}",
        f"健康检查：{'正常' if status.get('healthy') else '未通过'}",
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
