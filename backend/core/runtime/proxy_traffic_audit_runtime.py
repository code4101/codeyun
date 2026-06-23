from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

from pyxllib.prog import process_runtime

from backend.core.runtime.proxy_traffic_audit import get_proxy_traffic_audit_db_path, summarize_proxy_traffic
from backend.core.runtime.process_launcher import popen_python_module_service
from backend.core.settings import ROOT_DIR, get_settings


PROXY_TRAFFIC_AUDIT_SERVICE_KEY = "proxy-traffic-audit"
PROXY_TRAFFIC_AUDIT_TITLE = "代理流量审计"
PROXY_TRAFFIC_AUDIT_MODULE = "backend.services.proxy_traffic_audit_daemon"
PYTHON_PROCESS_NAMES = {"py.exe", "py", "python.exe", "python", "pythonw.exe", "pythonw"}


class ProxyTrafficAuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProxyTrafficAuditProcess:
    pid: int
    parent_pid: int | None
    name: str
    cmdline: str
    started_at: float | None = None


def get_proxy_traffic_audit_log_path() -> Path:
    configured = (os.getenv("CODEYUN_PROXY_TRAFFIC_AUDIT_LOG") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (get_settings().data_dir / "logs" / "proxy-traffic-audit.log").resolve(strict=False)


def get_proxy_traffic_audit_interval_seconds() -> float:
    try:
        return float(os.getenv("CODEYUN_PROXY_TRAFFIC_AUDIT_INTERVAL") or 2.0)
    except ValueError:
        return 2.0


def _safe_cmdline(proc: Any) -> list[str]:
    try:
        return [str(part) for part in proc.cmdline()]
    except Exception:
        return []


def _safe_name(proc: Any) -> str:
    try:
        return str(proc.name() or "")
    except Exception:
        return ""


def _safe_ppid(proc: Any) -> int | None:
    try:
        return int(proc.ppid())
    except Exception:
        return None


def _safe_started_at(proc: Any) -> float | None:
    try:
        return float(proc.create_time())
    except Exception:
        return None


def _matches_proxy_traffic_audit_process(proc: Any) -> bool:
    cmdline = _safe_cmdline(proc)
    if not cmdline:
        return False
    for index, part in enumerate(cmdline[:-1]):
        if part == "-m" and cmdline[index + 1] == PROXY_TRAFFIC_AUDIT_MODULE:
            return True
    return PROXY_TRAFFIC_AUDIT_MODULE in " ".join(cmdline)


def list_proxy_traffic_audit_processes() -> list[dict[str, Any]]:
    current_pid = os.getpid()
    items: list[ProxyTrafficAuditProcess] = []
    for proc in process_runtime.process_candidates_by_name(PYTHON_PROCESS_NAMES):
        if int(proc.pid) == current_pid:
            continue
        if not _matches_proxy_traffic_audit_process(proc):
            continue
        items.append(
            ProxyTrafficAuditProcess(
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


def _read_proxy_traffic_audit_collector_state(db_path: Path) -> dict[str, str]:
    if not db_path.is_file():
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            return {
                str(row[0]): str(row[1])
                for row in conn.execute(
                    "SELECT key, value FROM collector_state WHERE key IN ('last_sample_at', 'last_sample_summary')"
                ).fetchall()
            }
    except sqlite3.Error:
        return {}


def get_proxy_traffic_audit_status(*, include_summary: bool = True) -> dict[str, Any]:
    processes = list_proxy_traffic_audit_processes()
    running = bool(processes)
    db_path = get_proxy_traffic_audit_db_path()
    state: dict[str, Any]
    top_hosts: list[dict[str, Any]]
    if include_summary:
        summary = summarize_proxy_traffic(db_path=db_path, hours=24, limit=5, group_by="host")
        state = dict(summary.get("state", {}))
        top_hosts = list(summary.get("items", []))
    else:
        state = _read_proxy_traffic_audit_collector_state(db_path)
        top_hosts = []
    return {
        "key": PROXY_TRAFFIC_AUDIT_SERVICE_KEY,
        "title": PROXY_TRAFFIC_AUDIT_TITLE,
        "running": running,
        "state": "running" if running else "stopped",
        "state_label": "运行中" if running else "已停止",
        "interval_seconds": get_proxy_traffic_audit_interval_seconds(),
        "module": PROXY_TRAFFIC_AUDIT_MODULE,
        "cwd": os.fspath(ROOT_DIR),
        "db_path": os.fspath(db_path),
        "log_path": os.fspath(get_proxy_traffic_audit_log_path()),
        "process_count": len(processes),
        "processes": processes,
        "pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "last_sample_at": state.get("last_sample_at", ""),
        "last_sample_summary": state.get("last_sample_summary", ""),
        "top_hosts": top_hosts,
        "external": True,
        "controllable": True,
    }


def start_proxy_traffic_audit(wait_seconds: float = 2.0) -> dict[str, Any]:
    status = get_proxy_traffic_audit_status()
    if status.get("running"):
        return {"status": "started", "service": status}

    log_path = get_proxy_traffic_audit_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
    )
    command_args = [
        "--interval",
        str(get_proxy_traffic_audit_interval_seconds()),
        "--db",
        os.fspath(get_proxy_traffic_audit_db_path()),
    ]
    try:
        with log_path.open("ab") as log_file:
            log_file.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] CodeYun start proxy traffic audit\n".encode("utf-8")
            )
            proc = popen_python_module_service(
                PROXY_TRAFFIC_AUDIT_MODULE,
                *command_args,
                preferred_root=ROOT_DIR,
                cwd=os.fspath(ROOT_DIR),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
    except OSError as exc:
        raise ProxyTrafficAuditError(f"启动代理流量审计失败：{exc}") from exc

    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while time.monotonic() <= deadline:
        status = get_proxy_traffic_audit_status()
        if status.get("running"):
            status["started_pid"] = proc.pid
            return {"status": "started", "service": status}
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    status = get_proxy_traffic_audit_status()
    status["started_pid"] = proc.pid
    if status.get("process_count"):
        return {"status": "starting", "service": status}
    raise ProxyTrafficAuditError(f"已启动代理流量审计 PID {proc.pid}，但进程未保持运行。")


def stop_proxy_traffic_audit(timeout: float = 5.0) -> dict[str, Any]:
    processes = list_proxy_traffic_audit_processes()
    for item in processes:
        pid = item.get("pid")
        if pid is None:
            continue
        process_runtime.terminate_process_tree(int(pid), timeout=timeout)
    time.sleep(0.2)
    return {
        "status": "stopped",
        "stopped_pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "service": get_proxy_traffic_audit_status(),
    }


def build_proxy_traffic_audit_log_lines(limit: int = 200) -> list[str]:
    status = get_proxy_traffic_audit_status()
    path = get_proxy_traffic_audit_log_path()
    lines = [
        f"名称：{PROXY_TRAFFIC_AUDIT_TITLE}",
        f"状态：{status.get('state_label') or '-'}",
        f"间隔：{status.get('interval_seconds')} 秒",
        f"数据库：{status.get('db_path')}",
        f"日志：{path}",
    ]
    pids = status.get("pids") or []
    if pids:
        lines.append(f"PID：{', '.join(str(pid) for pid in pids)}")
    last_sample_at = status.get("last_sample_at")
    if last_sample_at:
        lines.append(f"最近采样：{last_sample_at}")
    top_hosts = status.get("top_hosts") or []
    if top_hosts:
        lines.append("")
        lines.append("24小时代理流量 Top 域名：")
        for item in top_hosts:
            total = int(item.get("total") or 0)
            lines.append(f"- {item.get('key') or '-'} · {total / 1024 / 1024:.2f} MB")
    if path.is_file():
        try:
            tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, int(limit)) :]
        except OSError:
            tail = []
        if tail:
            lines.extend(["", "最近日志：", *tail])
    return lines
