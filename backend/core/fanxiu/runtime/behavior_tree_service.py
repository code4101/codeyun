from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from pyxllib.prog import (
    local_service_enabled,
    process_candidates_by_name,
    read_json_state_dict,
    root_process_records,
    seconds_since,
    tail_text,
    terminate_process_tree,
    write_json_state,
)

from backend.core.runtime.ocr_service import get_ocr_service_base_url, get_ocr_service_status, start_ocr_service
from backend.core.runtime.process_launcher import popen_python_script_service
from backend.core.access.service_tokens import discover_legacy_service_tokens
from backend.core.settings import ROOT_DIR, get_settings
from backend.core.fanxiu.runtime.mumu_control import _collect_windows_commit_snapshot


FANXIU_BEHAVIOR_TREE_SERVICE_KEY = "fanxiu-behavior-tree"
REGISTRY_FILENAME = "behavior_tree_service.json"
DEFAULT_LEGACY_OCR_TOKEN = "log@a#zJy4&"
DEFAULT_OCR_CLIENT_TIMEOUT_SECONDS = "60"
DEFAULT_OCR_CLIENT_RETRIES = "2"
DEFAULT_OCR_CLIENT_RETRY_INTERVAL_SECONDS = "1"
DEFAULT_FANXIU_SERVICE_HOSTS: set[str] = set()
PYTHON_RUNNER_NAMES = {"py.exe", "python.exe", "pythonw.exe", "uv.exe"}
_RFC1918_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
OCR_PREWARM_COMMIT_PRESSURE_PERCENT = 85.0
OCR_PREWARM_COMMIT_PRESSURE_AVAILABLE_MB = 16 * 1024


def _host_commit_pressure_should_skip_ocr_prewarm() -> bool:
    commit = _collect_windows_commit_snapshot()
    if not commit:
        return False
    commit_percent = float(commit.get("commit_percent") or 0.0)
    commit_available_mb = int(commit.get("commit_available_mb") or 0)
    return commit_percent >= OCR_PREWARM_COMMIT_PRESSURE_PERCENT or commit_available_mb < OCR_PREWARM_COMMIT_PRESSURE_AVAILABLE_MB


def _home_root() -> Path:
    return ROOT_DIR.parent.parent if ROOT_DIR.parent.name.lower() == "slns" else ROOT_DIR.parent


def _current_hostname() -> str:
    configured = os.getenv("FX_HOSTNAME") or os.getenv("XL_HOSTNAME")
    if configured and configured.strip():
        return configured.strip()
    try:
        from pyxllib.prog.xlenv import get_xl_hostname

        return str(get_xl_hostname() or "").strip() or socket.gethostname().replace("-", "_")
    except Exception:
        return socket.gethostname().replace("-", "_").split(".", 1)[0]


def _host_with_port(host: str, port: int) -> str:
    value = str(host or "").strip()
    if not value:
        return f"127.0.0.1:{port}"
    if value.startswith(("http://", "https://")):
        return value.rstrip("/")
    if ":" in value and not value.startswith("["):
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return value
        return f"[{value}]:{port}"
    return f"{value}:{port}"


def _is_lan_ipv4_address(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not isinstance(ip, ipaddress.IPv4Address):
        return False
    return any(ip in network for network in _RFC1918_LAN_NETWORKS)


def _get_primary_lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            address = str(sock.getsockname()[0])
            if _is_lan_ipv4_address(address):
                return address
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            address = str(item[4][0])
            if _is_lan_ipv4_address(address):
                return address
    except OSError:
        pass
    return None


def _resolve_codeyun_ocr_host(settings: Any) -> str:
    configured = (os.getenv("FX_CODEYUN_OCR_HOST") or "").strip()
    if configured:
        return configured

    return get_ocr_service_base_url()


def _resolve_fanxiu_ocr_device(settings: Any | None = None) -> str:
    value = (
        os.getenv("CODEYUN_OCR_DEVICE")
        or os.getenv("FX_CODEYUN_OCR_DEVICE")
    )
    if value is None:
        value = getattr(settings or get_settings(), "ocr_device", "gpu")
    return str(value or "gpu").strip().lower() or "gpu"


def is_fanxiu_behavior_tree_service_enabled() -> bool:
    return local_service_enabled(
        service_names={
            "*",
            "all",
            "fanxiu",
            FANXIU_BEHAVIOR_TREE_SERVICE_KEY,
            "fanxiu_behavior_tree",
            "behavior_tree",
            "凡修行为树",
        },
        runtime_services_text=os.getenv("FX_RUNTIME_SERVICES"),
        enabled_text=os.getenv("FX_BEHAVIOR_TREE_SERVICE_ENABLED"),
        hosts_text=os.getenv("FX_BEHAVIOR_TREE_SERVICE_HOSTS"),
        default_hosts=DEFAULT_FANXIU_SERVICE_HOSTS,
        current_hostname=_current_hostname(),
    )


def get_fanxiu_mainwin_root() -> Path:
    configured = os.getenv("FX_MAINWIN_ROOT")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser().resolve(strict=False)
    return (_home_root() / "data" / "m2508凡修" / "mainwin").resolve(strict=False)


def get_fanxiu_fx_root() -> Path:
    configured = os.getenv("FX_PROJECT_ROOT")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser().resolve(strict=False)
    return (ROOT_DIR.parent / "xlproject" / "src" / "xlsln" / "ckz2025" / "fx").resolve(strict=False)


def get_fanxiu_python() -> Path:
    configured = os.getenv("FX_PYTHON")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser().resolve(strict=False)
    if os.name == "nt":
        return (ROOT_DIR.parent / "xlproject" / ".venv" / "Scripts" / "python.exe").resolve(strict=False)
    return (ROOT_DIR.parent / "xlproject" / ".venv" / "bin" / "python").resolve(strict=False)


def get_registry_path() -> Path:
    configured = os.getenv("FX_BEHAVIOR_TREE_REGISTRY")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser().resolve(strict=False)
    return get_fanxiu_mainwin_root() / REGISTRY_FILENAME


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json_file(path: Path) -> dict[str, Any]:
    return read_json_state_dict(path)


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    write_json_state(path, payload, permission_retries=6)


def _is_behavior_tree_process(item: dict[str, Any]) -> bool:
    name = Path(str(item.get("name") or "")).name.lower()
    if name not in PYTHON_RUNNER_NAMES:
        return False
    command_line = str(item.get("command_line") or "").replace("\\", "/").lower()
    return "tools/凡修手游.py" in command_line


def list_behavior_tree_processes() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for proc in process_candidates_by_name(PYTHON_RUNNER_NAMES):
        try:
            command_line = " ".join(str(part) for part in proc.cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        if "tools/凡修手游.py" not in command_line.replace("\\", "/").lower():
            continue
        try:
            name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        if not _is_behavior_tree_process({"name": name, "command_line": command_line}):
            continue
        try:
            created_at = datetime.fromtimestamp(proc.create_time()).strftime("%Y-%m-%d %H:%M:%S")
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            created_at = None
        try:
            items.append(
                {
                    "pid": proc.pid,
                    "parent_pid": proc.ppid(),
                    "name": name,
                    "command_line": command_line,
                    "created_at": created_at,
                    "matched_reason": "cmd:fanxiu-behavior-tree",
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    items.sort(key=lambda item: (item.get("created_at") or "", int(item["pid"])))
    return items


def terminate_behavior_tree_processes(timeout: float = 5.0) -> dict[str, Any]:
    matched = list_behavior_tree_processes()
    root_items = root_process_records(matched)
    errors: list[dict[str, Any]] = []
    terminated: list[dict[str, Any]] = []
    for item in root_items:
        try:
            terminate_process_tree(int(item["pid"]), timeout=timeout)
            terminated.append(item)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
            errors.append({"pid": item.get("pid"), "error": str(exc)})
    time.sleep(0.2)
    return {
        "matched": matched,
        "terminated": terminated,
        "remaining": list_behavior_tree_processes(),
        "errors": errors,
    }


def _resolve_ocr_token() -> str:
    for name in ("CODEYUN_SERVICE_TOKEN", "CODEYUN_OCR_TOKEN", "XL_API_PRIU_TOKEN"):
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    discovered = discover_legacy_service_tokens()
    if discovered:
        return discovered[0][0]
    return DEFAULT_LEGACY_OCR_TOKEN


def _status_paths() -> dict[str, str]:
    mainwin = get_fanxiu_mainwin_root()
    log_dir = mainwin / "日志"
    return {
        "root": os.fspath(mainwin),
        "registry_path": os.fspath(get_registry_path()),
        "status_path": os.fspath(mainwin / "status.json"),
        "behavior_tree_log_path": os.fspath(log_dir / "behavior_tree.log"),
        "service_log_path": os.fspath(log_dir / "fanxiu_behavior_tree.service.log"),
        "script_path": os.fspath(get_fanxiu_fx_root() / "tools" / "凡修手游.py"),
        "python_path": os.fspath(get_fanxiu_python()),
    }


def get_behavior_tree_status() -> dict[str, Any]:
    registry = _read_json_file(get_registry_path())
    processes = list_behavior_tree_processes()
    root_processes = root_process_records(processes)
    process_pids = {int(item["pid"]) for item in processes if item.get("pid") is not None}
    registry_pid = registry.get("pid")
    registry_pid_alive = isinstance(registry_pid, int) and registry_pid in process_pids
    heartbeat_age = seconds_since(registry.get("heartbeat_at"))

    if processes:
        state = "running"
        state_label = "运行中"
        if len(root_processes) > 1:
            state_label = f"运行中，{len(root_processes)} 个行为树"
    elif registry.get("state") == "running":
        state = "stale"
        state_label = "登记残留"
    else:
        state = "stopped"
        state_label = "已停止"

    if processes and registry and not registry_pid_alive:
        state = "orphan"
        state_label = "进程未登记"

    paths = _status_paths()
    last_error = ""
    if len(root_processes) > 1:
        last_error = "检测到多个凡修行为树；下一次启动会先终止旧进程。"
    elif state == "stale":
        last_error = "登记文件仍显示运行，但没有找到对应行为树进程。"

    return {
        "key": FANXIU_BEHAVIOR_TREE_SERVICE_KEY,
        "title": "凡修行为树",
        "running": bool(processes),
        "state": state,
        "state_label": state_label,
        "pid": registry_pid if registry_pid_alive else (processes[0]["pid"] if processes else None),
        "process_count": len(root_processes),
        "processes": processes,
        "root_processes": root_processes,
        "registry": registry,
        "registry_pid_alive": registry_pid_alive,
        "heartbeat_age_seconds": heartbeat_age,
        "started_at": registry.get("started_at") or (processes[0].get("created_at") if processes else None),
        "heartbeat_at": registry.get("heartbeat_at"),
        "last_error": last_error,
        **paths,
    }


def _mark_registry_stopped(extra: dict[str, Any] | None = None) -> None:
    registry = _read_json_file(get_registry_path())
    payload = {
        **registry,
        "service": "fanxiu_behavior_tree",
        "state": "stopped",
        "pid": None,
        "last_pid": registry.get("last_pid") or registry.get("pid"),
        "updated_at": _now_text(),
        "stopped_at": _now_text(),
        **(extra or {}),
    }
    _write_json_file(get_registry_path(), payload)


def stop_behavior_tree_service(timeout: float = 5.0) -> dict[str, Any]:
    result = terminate_behavior_tree_processes(timeout=timeout)
    _mark_registry_stopped({"stop_result": result})
    return {
        "status": "stopped",
        "stop_result": result,
        "service": get_behavior_tree_status(),
    }


def start_behavior_tree_service(*, replace_existing: bool = True) -> dict[str, Any]:
    stop_result = terminate_behavior_tree_processes(timeout=5.0) if replace_existing else {"terminated": [], "remaining": []}
    paths = _status_paths()
    python_path = Path(paths["python_path"])
    script_path = Path(paths["script_path"])
    fx_root = get_fanxiu_fx_root()
    mainwin = get_fanxiu_mainwin_root()
    service_log_path = Path(paths["service_log_path"])

    if not python_path.exists():
        raise RuntimeError(f"凡修 Python 不存在：{python_path}")
    if not script_path.exists():
        raise RuntimeError(f"凡修入口脚本不存在：{script_path}")

    service_log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    settings = get_settings()
    fanxiu_ocr_device = _resolve_fanxiu_ocr_device(settings)
    if (os.getenv("FX_CODEYUN_OCR_HOST") or "").strip():
        codeyun_ocr_host = _resolve_codeyun_ocr_host(settings)
        ocr_start_status: dict[str, Any] | None = None
        force_codeyun_ocr = True
    elif _host_commit_pressure_should_skip_ocr_prewarm():
        codeyun_ocr_host = _resolve_codeyun_ocr_host(settings)
        ocr_start_status = {
            "running": False,
            "skipped": "host_commit_pressure",
            "url": codeyun_ocr_host,
        }
        force_codeyun_ocr = False
    else:
        current_ocr_status = get_ocr_service_status()
        current_ocr_device = str(current_ocr_status.get("device") or "").strip().lower()
        replace_ocr_service = bool(current_ocr_status.get("running") and current_ocr_device != fanxiu_ocr_device)
        ocr_start = start_ocr_service(
            replace_existing=replace_ocr_service,
            env_overrides={
                "CODEYUN_OCR_DEVICE": fanxiu_ocr_device,
                "CODEYUN_OCR_IDLE_TIMEOUT_SECONDS": os.getenv("CODEYUN_OCR_IDLE_TIMEOUT_SECONDS", "3600"),
            },
        )
        ocr_start_status = ocr_start.get("service") if isinstance(ocr_start, dict) else None
        codeyun_ocr_host = str((ocr_start_status or {}).get("url") or _resolve_codeyun_ocr_host(settings))
        force_codeyun_ocr = True
    env.update(
        {
            "FX_BEHAVIOR_TREE_REGISTRY": os.fspath(get_registry_path()),
            "FX_BEHAVIOR_TREE_SERVICE_SOURCE": "codeyun",
            "FX_FORCE_CODEYUN_OCR": "1" if force_codeyun_ocr else "0",
            "FX_CODEYUN_OCR_HOST": codeyun_ocr_host,
            "FX_CODEYUN_OCR_PROBE_MODE": os.getenv("FX_CODEYUN_OCR_PROBE_MODE", "predict"),
            "FX_CODEYUN_OCR_PROBE_TIMEOUT": os.getenv("FX_CODEYUN_OCR_PROBE_TIMEOUT", "300"),
            "FX_MAINWIN_ROOT": os.fspath(mainwin),
            "MAIN_WEBSITE": codeyun_ocr_host,
            "CODEYUN_OCR_DEVICE": fanxiu_ocr_device,
            "XL_API_PRIU_TIMEOUT": os.getenv("XL_API_PRIU_TIMEOUT", DEFAULT_OCR_CLIENT_TIMEOUT_SECONDS),
            "XL_API_PRIU_RETRIES": os.getenv("XL_API_PRIU_RETRIES", DEFAULT_OCR_CLIENT_RETRIES),
            "XL_API_PRIU_RETRY_INTERVAL": os.getenv(
                "XL_API_PRIU_RETRY_INTERVAL",
                DEFAULT_OCR_CLIENT_RETRY_INTERVAL_SECONDS,
            ),
            "NO_PROXY": "*",
            "no_proxy": "*",
            "XL_API_PRIU_TOKEN": _resolve_ocr_token(),
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )

    with service_log_path.open("ab") as log_file:
        log_file.write(f"\n[{_now_text()}] CodeYun start behavior tree\n".encode("utf-8"))
        proc = popen_python_script_service(
            script_path,
            executable=python_path,
            cwd=os.fspath(fx_root),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

    _write_json_file(
        get_registry_path(),
        {
            "service": "fanxiu_behavior_tree",
            "state": "starting",
            "pid": proc.pid,
            "last_pid": proc.pid,
            "source": "codeyun",
            "python": os.fspath(python_path),
            "script": os.fspath(script_path),
            "cwd": os.fspath(fx_root),
            "root": os.fspath(mainwin),
            "ocr_host": codeyun_ocr_host,
            "ocr_device": fanxiu_ocr_device,
            "ocr_probe_mode": env["FX_CODEYUN_OCR_PROBE_MODE"],
            "ocr_probe_timeout_seconds": env["FX_CODEYUN_OCR_PROBE_TIMEOUT"],
            "ocr_request_timeout_seconds": env["XL_API_PRIU_TIMEOUT"],
            "ocr_request_retries": env["XL_API_PRIU_RETRIES"],
            "ocr_service": ocr_start_status,
            "started_at": _now_text(),
            "updated_at": _now_text(),
            "stop_result": stop_result,
        },
    )
    time.sleep(0.5)
    return {
        "status": "started",
        "pid": proc.pid,
        "stop_result": stop_result,
        "service": get_behavior_tree_status(),
    }


def build_behavior_tree_log_lines(limit: int = 500) -> list[str]:
    status = get_behavior_tree_status()
    registry = status.get("registry") if isinstance(status.get("registry"), dict) else {}
    lines = [
        f"名称：{status['title']}",
        f"状态：{status['state_label']}",
        f"PID：{status.get('pid') or '-'}",
        f"进程数：{status.get('process_count') or 0}",
        f"启动时间：{status.get('started_at') or '-'}",
        f"心跳时间：{status.get('heartbeat_at') or '-'}",
        f"登记文件：{status.get('registry_path')}",
        f"状态文件：{status.get('status_path')}",
        f"行为树日志：{status.get('behavior_tree_log_path')}",
    ]
    if registry.get("ocr_host") or registry.get("ocr_device"):
        lines.append(
            "OCR："
            f"host={registry.get('ocr_host') or '-'}，"
            f"device={registry.get('ocr_device') or '-'}，"
            f"probe={registry.get('ocr_probe_mode') or '-'}，"
            f"timeout={registry.get('ocr_request_timeout_seconds') or '-'}s，"
            f"retries={registry.get('ocr_request_retries') or '-'}"
        )
    if status.get("last_error"):
        lines.extend(["", f"提示：{status['last_error']}"])
    if status.get("processes"):
        lines.append("")
        lines.append("进程：")
        for item in status["processes"][:20]:
            lines.append(
                f"- PID {item.get('pid')} · {item.get('created_at') or '-'} · "
                f"{item.get('command_line') or '-'}"
            )

    tail_limit = max(20, min(120, limit // 2 if limit else 80))
    lines.extend(["", "服务启动日志："])
    lines.extend(tail_text(Path(status["service_log_path"]), lines=tail_limit))
    lines.extend(["", "behavior_tree.log 尾部："])
    lines.extend(tail_text(Path(status["behavior_tree_log_path"]), lines=tail_limit))
    return lines[: max(1, limit)]
