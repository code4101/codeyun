from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from backend.core.device import build_background_popen_kwargs, process_candidates_by_name
from backend.core.service_tokens import discover_legacy_service_tokens
from backend.core.settings import ROOT_DIR, get_settings


FANXIU_BEHAVIOR_TREE_SERVICE_KEY = "fanxiu-behavior-tree"
REGISTRY_FILENAME = "behavior_tree_service.json"
DEFAULT_LEGACY_OCR_TOKEN = "log@a#zJy4&"
DEFAULT_FANXIU_SERVICE_HOSTS = {"codepc_mi15", "mi15"}
PYTHON_RUNNER_NAMES = {"py.exe", "python.exe", "pythonw.exe", "uv.exe"}


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


def is_fanxiu_behavior_tree_service_enabled() -> bool:
    configured = os.getenv("FX_BEHAVIOR_TREE_SERVICE_ENABLED")
    if configured is not None:
        return configured.strip().lower() not in {"0", "false", "no", "off", "disabled"}

    hosts_text = os.getenv("FX_BEHAVIOR_TREE_SERVICE_HOSTS")
    hosts = (
        {item.strip().lower() for item in hosts_text.split(",") if item.strip()}
        if hosts_text
        else DEFAULT_FANXIU_SERVICE_HOSTS
    )
    return _current_hostname().strip().lower() in hosts


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
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    last_error: PermissionError | None = None
    for attempt in range(6):
        temp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(text, encoding="utf-8")
            temp_path.replace(path)
            return
        except PermissionError as exc:
            last_error = exc
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error


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


def _root_behavior_tree_processes(processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    process_pids = {int(item["pid"]) for item in processes if item.get("pid") is not None}
    return [item for item in processes if int(item.get("parent_pid") or -1) not in process_pids]


def _terminate_process_tree(pid: int, timeout: float) -> None:
    try:
        root = psutil.Process(pid)
        targets = [*root.children(recursive=True), root]
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return

    for target in reversed(targets):
        try:
            target.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
    _, alive = psutil.wait_procs(targets, timeout=timeout)
    for target in alive:
        try:
            target.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
    if alive:
        psutil.wait_procs(alive, timeout=timeout)


def terminate_behavior_tree_processes(timeout: float = 5.0) -> dict[str, Any]:
    matched = list_behavior_tree_processes()
    root_items = _root_behavior_tree_processes(matched)
    errors: list[dict[str, Any]] = []
    terminated: list[dict[str, Any]] = []
    for item in root_items:
        try:
            _terminate_process_tree(int(item["pid"]), timeout)
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


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            pass
    return None


def _seconds_since(value: Any) -> int | None:
    parsed = _parse_time(value)
    if not parsed:
        return None
    return max(0, int((datetime.now() - parsed).total_seconds()))


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
    root_processes = _root_behavior_tree_processes(processes)
    process_pids = {int(item["pid"]) for item in processes if item.get("pid") is not None}
    registry_pid = registry.get("pid")
    registry_pid_alive = isinstance(registry_pid, int) and registry_pid in process_pids
    heartbeat_age = _seconds_since(registry.get("heartbeat_at"))

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
    env.update(
        {
            "FX_BEHAVIOR_TREE_REGISTRY": os.fspath(get_registry_path()),
            "FX_BEHAVIOR_TREE_SERVICE_SOURCE": "codeyun",
            "FX_FORCE_CODEYUN_OCR": "1",
            "FX_CODEYUN_OCR_HOST": f"127.0.0.1:{settings.backend_port}",
            "FX_MAINWIN_ROOT": os.fspath(mainwin),
            "MAIN_WEBSITE": f"127.0.0.1:{settings.backend_port}",
            "XL_API_PRIU_TOKEN": _resolve_ocr_token(),
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )

    with service_log_path.open("ab") as log_file:
        log_file.write(f"\n[{_now_text()}] CodeYun start behavior tree\n".encode("utf-8"))
        proc = subprocess.Popen(
            [os.fspath(python_path), os.fspath(script_path)],
            cwd=os.fspath(fx_root),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            **build_background_popen_kwargs(independent=True),
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


def _tail_text(path: Path, *, lines: int = 80, max_bytes: int = 512 * 1024) -> list[str]:
    if not path.exists():
        return [f"{path} 不存在"]
    try:
        size = path.stat().st_size
        with path.open("rb") as file:
            file.seek(max(0, size - max_bytes))
            data = file.read()
    except OSError as exc:
        return [f"读取 {path} 失败：{exc}"]
    text = data.decode("utf-8", errors="replace")
    rows = [row.rstrip() for row in text.splitlines() if row.strip()]
    return rows[-lines:] if lines > 0 else rows


def build_behavior_tree_log_lines(limit: int = 500) -> list[str]:
    status = get_behavior_tree_status()
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
    lines.extend(_tail_text(Path(status["service_log_path"]), lines=tail_limit))
    lines.extend(["", "behavior_tree.log 尾部："])
    lines.extend(_tail_text(Path(status["behavior_tree_log_path"]), lines=tail_limit))
    return lines[: max(1, limit)]
