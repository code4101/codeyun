from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from backend.core.device import process_candidates_by_name
from backend.core.runtime.process_launcher import popen_service, run_quiet
from backend.core.settings import ROOT_DIR


ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY = "attendance-behavior-tree"
ATTENDANCE_BEHAVIOR_TREE_TITLE = "考勤行为树"
ATTENDANCE_MODULE_NAME = "xlsln.kq5034.kqmain"
ATTENDANCE_MODULE_NAMES = {ATTENDANCE_MODULE_NAME, "kq5034.kqmain"}
DEFAULT_ATTENDANCE_SERVICE_HOSTS = {"codepc_mi15", "mi15"}
PYTHON_PROCESS_NAMES = {"py.exe", "py", "python.exe", "python", "pythonw.exe", "pythonw"}


@dataclass(frozen=True)
class AttendanceBehaviorTreeProcess:
    pid: int
    parent_pid: int | None
    name: str
    command_line: str
    created_at: str | None
    matched_reason: str


def _home_root() -> Path:
    return ROOT_DIR.parent.parent if ROOT_DIR.parent.name.lower() == "slns" else ROOT_DIR.parent


def _configured_path(env_key: str) -> Path | None:
    value = os.getenv(env_key)
    if not value or not value.strip():
        return None
    return Path(os.path.expandvars(value.strip().strip('"'))).expanduser().resolve(strict=False)


def get_xlproject_root() -> Path:
    return _configured_path("KQ_XLPROJECT_ROOT") or (ROOT_DIR.parent / "xlproject").resolve(strict=False)


def get_attendance_package_root() -> Path:
    return _configured_path("KQ_PACKAGE_ROOT") or (get_xlproject_root() / "src" / "xlsln").resolve(strict=False)


def get_attendance_project_root() -> Path:
    return _configured_path("KQ_PROJECT_ROOT") or (get_attendance_package_root() / "kq5034").resolve(strict=False)


def get_attendance_python() -> Path:
    configured = _configured_path("KQ_PYTHON") or _configured_path("XLPROJECT_PYTHON")
    if configured is not None:
        return configured
    venv_dir = get_xlproject_root() / ".venv"
    if os.name == "nt":
        return (venv_dir / "Scripts" / "python.exe").resolve(strict=False)
    return (venv_dir / "bin" / "python").resolve(strict=False)


def get_attendance_work_root() -> Path:
    configured = _configured_path("KQ_WORK_ROOT")
    if configured is not None:
        return configured
    try:
        from pyxllib.prog.xlenv import get_xl_homedir

        return (Path(get_xl_homedir()) / "data" / "m2112kq5034").resolve(strict=False)
    except Exception:
        return (_home_root() / "data" / "m2112kq5034").resolve(strict=False)


def get_attendance_hostname() -> str:
    configured = os.getenv("KQ_HOSTNAME") or os.getenv("XL_HOSTNAME")
    if configured and configured.strip():
        return configured.strip()
    try:
        from pyxllib.prog.xlenv import get_xl_hostname

        return str(get_xl_hostname() or "").strip() or socket.gethostname().replace("-", "_")
    except Exception:
        return socket.gethostname().replace("-", "_").split(".", 1)[0]


def is_attendance_behavior_tree_service_enabled() -> bool:
    configured = os.getenv("KQ_BEHAVIOR_TREE_SERVICE_ENABLED")
    if configured is not None:
        return configured.strip().lower() not in {"0", "false", "no", "off", "disabled"}

    hosts_text = os.getenv("KQ_BEHAVIOR_TREE_SERVICE_HOSTS")
    hosts = (
        {item.strip().lower() for item in hosts_text.split(",") if item.strip()}
        if hosts_text
        else DEFAULT_ATTENDANCE_SERVICE_HOSTS
    )
    return get_attendance_hostname().strip().lower() in hosts


def _scheduler_dir() -> Path:
    return get_attendance_work_root() / "scheduler"


def _status_paths() -> dict[str, str]:
    hostname = get_attendance_hostname()
    scheduler = _scheduler_dir()
    return {
        "root": os.fspath(get_attendance_work_root()),
        "scheduler_path": os.fspath(scheduler),
        "state_path": os.fspath(scheduler / f"kqmain.{hostname}.state.json"),
        "lock_path": os.fspath(scheduler / f"kqmain.{hostname}.lock"),
        "behavior_tree_log_path": os.fspath(scheduler / f"kqmain.{hostname}.log"),
        "service_log_path": os.fspath(scheduler / f"kqmain.{hostname}.service.log"),
        "script_path": os.fspath(get_attendance_project_root() / "kqmain.py"),
        "python_path": os.fspath(get_attendance_python()),
        "cwd": os.fspath(get_attendance_package_root()),
    }


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


def _safe_mtime_text(path: Path) -> str | None:
    try:
        if not path.exists():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return None


def _normalize_path(value: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.path.realpath(os.fspath(value))))


def _is_python_process(proc: psutil.Process) -> bool:
    try:
        name = Path(proc.name()).name.lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return False
    return name in {"py.exe", "py", "python.exe", "python", "pythonw.exe", "pythonw"} or name.startswith("python")


def _safe_cmdline(proc: psutil.Process) -> list[str]:
    try:
        return [str(part) for part in proc.cmdline()]
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return []


def _safe_cwd(proc: psutil.Process) -> str:
    try:
        return proc.cwd()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return ""


def _safe_ppid(proc: psutil.Process) -> int | None:
    try:
        return proc.ppid()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _safe_name(proc: psutil.Process) -> str:
    try:
        return proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return ""


def _safe_created_at(proc: psutil.Process) -> str | None:
    try:
        return datetime.fromtimestamp(proc.create_time()).strftime("%Y-%m-%d %H:%M:%S")
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _matches_attendance_script(
    proc: psutil.Process,
    script_path: Path,
    *,
    assume_python_process: bool = False,
) -> str | None:
    if not assume_python_process and not _is_python_process(proc):
        return None

    args = _safe_cmdline(proc)
    if not args:
        return None

    expected_script = _normalize_path(script_path)

    def _has_extra_cli(after_index: int) -> bool:
        return any(str(part).strip() for part in args[after_index + 1 :])

    index = 1
    while index < len(args):
        text = str(args[index]).strip().strip('"')
        if text in {"-m", "/m"}:
            if index + 1 < len(args) and args[index + 1] in ATTENDANCE_MODULE_NAMES and not _has_extra_cli(index + 1):
                return "cmd:attendance-module"
            return None
        if text.startswith("-m") and len(text) > 2:
            return "cmd:attendance-module" if text[2:] in ATTENDANCE_MODULE_NAMES and not _has_extra_cli(index) else None
        if text in {"-c", "/c"} or text.startswith("-c"):
            return None
        if text in {"-W", "-X"}:
            index += 2
            continue
        if text.startswith("-"):
            index += 1
            continue
        if not text.lower().endswith(".py"):
            return None
        if not os.path.isabs(text):
            cwd = _safe_cwd(proc)
            if cwd:
                text = os.path.join(cwd, text)
        if _normalize_path(text) != expected_script:
            return None
        return "cmd:attendance-script" if not _has_extra_cli(index) else None
    return None


def _process_info(proc: psutil.Process, reason: str) -> AttendanceBehaviorTreeProcess | None:
    try:
        return AttendanceBehaviorTreeProcess(
            pid=proc.pid,
            parent_pid=_safe_ppid(proc),
            name=_safe_name(proc),
            command_line=" ".join(_safe_cmdline(proc)),
            created_at=_safe_created_at(proc),
            matched_reason=reason,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _collect_attendance_process_targets() -> tuple[
    dict[int, tuple[psutil.Process, str]],
    dict[int, tuple[psutil.Process, str]],
]:
    current_pid = os.getpid()
    script_path = Path(_status_paths()["script_path"])
    matched: dict[int, tuple[psutil.Process, str]] = {}
    for proc in process_candidates_by_name(PYTHON_PROCESS_NAMES):
        if proc.pid == current_pid:
            continue
        try:
            reason = _matches_attendance_script(proc, script_path, assume_python_process=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
        if reason:
            matched[proc.pid] = (proc, reason)

    direct = {
        pid: (proc, reason)
        for pid, (proc, reason) in matched.items()
        if _safe_ppid(proc) not in matched
    }
    targets: dict[int, tuple[psutil.Process, str]] = dict(direct)
    for root_pid, (proc, _reason) in direct.items():
        try:
            children = proc.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            children = []
        for child in children:
            if child.pid != current_pid:
                targets.setdefault(child.pid, (child, f"descendant-of:{root_pid}"))
    return direct, targets


def _wait_processes_safely(
    processes: list[psutil.Process],
    *,
    timeout: float,
    errors: list[dict[str, Any]],
) -> list[psutil.Process]:
    if not processes:
        return []
    try:
        _gone, alive = psutil.wait_procs(processes, timeout=max(0.1, float(timeout)))
        return list(alive)
    except (psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
        errors.append({"pid": None, "error": f"等待进程退出时遇到系统限制：{exc}"})

    alive: list[psutil.Process] = []
    deadline = time.monotonic() + max(0.1, float(timeout))
    for proc in processes:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except psutil.TimeoutExpired:
            alive.append(proc)
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
            errors.append({"pid": proc.pid, "error": str(exc)})
    return alive


def list_attendance_behavior_tree_processes() -> list[dict[str, Any]]:
    _direct, targets = _collect_attendance_process_targets()
    return _process_records_from_targets(targets)


def _process_records_from_targets(
    targets: dict[int, tuple[psutil.Process, str]],
) -> list[dict[str, Any]]:
    items: list[AttendanceBehaviorTreeProcess] = []
    for proc, reason in targets.values():
        info = _process_info(proc, reason)
        if info is not None:
            items.append(info)
    items.sort(key=lambda item: (item.created_at or "", item.pid))
    return [asdict(item) for item in items]


def _target_depth(pid: int, parent_pids: dict[int, int | None]) -> int:
    depth = 0
    seen = {pid}
    parent_pid = parent_pids.get(pid)
    while parent_pid in parent_pids and parent_pid not in seen:
        seen.add(parent_pid)
        depth += 1
        parent_pid = parent_pids.get(parent_pid)
    return depth


def terminate_attendance_behavior_tree_processes(timeout: float = 5.0) -> dict[str, Any]:
    direct, targets = _collect_attendance_process_targets()

    before = []
    for proc, reason in direct.values():
        info = _process_info(proc, reason)
        if info is not None:
            before.append(asdict(info))

    errors: list[dict[str, Any]] = []
    terminated: dict[int, dict[str, Any]] = {}
    parent_pids = {pid: _safe_ppid(proc) for pid, (proc, _reason) in targets.items()}
    ordered_pids = sorted(targets, key=lambda pid: (_target_depth(pid, parent_pids), pid), reverse=True)
    for pid in ordered_pids:
        proc, reason = targets[pid]
        try:
            info = _process_info(proc, reason)
            proc.terminate()
            if info is not None:
                terminated[proc.pid] = asdict(info)
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
            errors.append({"pid": proc.pid, "error": str(exc)})

    alive = _wait_processes_safely(
        [proc for proc, _reason in targets.values()],
        timeout=timeout,
        errors=errors,
    )
    for proc in alive:
        try:
            reason = targets.get(proc.pid, (proc, "matched-or-descendant"))[1]
            info = _process_info(proc, reason)
            proc.kill()
            if info is not None:
                terminated[proc.pid] = asdict(info)
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
            errors.append({"pid": proc.pid, "error": str(exc)})

    if alive:
        _wait_processes_safely(alive, timeout=timeout, errors=errors)

    time.sleep(0.2)
    return {
        "matched": before,
        "terminated": sorted(terminated.values(), key=lambda item: item["pid"]),
        "remaining": list_attendance_behavior_tree_processes(),
        "errors": errors,
    }


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).replace(microsecond=0)
    except ValueError:
        return None


def _next_run_at_from_state(state: dict[str, Any]) -> str | None:
    nodes = state.get("nodes")
    if not isinstance(nodes, dict):
        return None
    values: list[datetime] = []
    for node_state in nodes.values():
        if not isinstance(node_state, dict):
            continue
        parsed = _parse_datetime(node_state.get("next_run_at"))
        if parsed is not None:
            values.append(parsed)
    if not values:
        return None
    return min(values).strftime("%Y-%m-%d %H:%M:%S")


def _attendance_subprocess_env(paths: dict[str, str] | None = None) -> dict[str, str]:
    paths = paths or _status_paths()
    env = os.environ.copy()
    python_paths = [os.fspath(get_xlproject_root() / "src"), os.fspath(get_attendance_package_root())]
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env.update(
        {
            "KQ_BEHAVIOR_TREE_SERVICE_SOURCE": "codeyun",
            "KQ_WORK_ROOT": paths["root"],
            "PYTHONPATH": os.pathsep.join(python_paths),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def _run_attendance_script_command(*args: str, timeout: float = 30.0) -> dict[str, Any]:
    paths = _status_paths()
    python_path = Path(paths["python_path"])
    script_path = Path(paths["script_path"])
    cwd = Path(paths["cwd"])
    if not python_path.exists():
        raise RuntimeError(f"考勤 Python 不存在：{python_path}")
    if not script_path.exists():
        raise RuntimeError(f"考勤入口脚本不存在：{script_path}")

    command = [os.fspath(python_path), os.fspath(script_path), *args]
    proc = run_quiet(
        command,
        cwd=os.fspath(cwd),
        env=_attendance_subprocess_env(paths),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1.0, float(timeout)),
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"考勤脚本命令失败：{' '.join(args)} exit_code={proc.returncode} {detail}".strip())
    return {
        "command": command,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
    }


def probe_attendance_subprocess_utf8(text: str = "考勤中文探针", timeout: float = 15.0) -> dict[str, Any]:
    paths = _status_paths()
    python_path = Path(paths["python_path"])
    cwd = Path(paths["cwd"])
    if not python_path.exists():
        raise RuntimeError(f"考勤 Python 不存在：{python_path}")

    command = [
        os.fspath(python_path),
        "-c",
        "import sys; print(sys.stdout.encoding); print(sys.stderr.encoding, file=sys.stderr); print(%r)" % str(text),
    ]
    proc = run_quiet(
        command,
        cwd=os.fspath(cwd),
        env=_attendance_subprocess_env(paths),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1.0, float(timeout)),
        check=False,
    )
    return {
        "command": command,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
    }


def get_attendance_behavior_tree_status() -> dict[str, Any]:
    paths = _status_paths()
    state_path = Path(paths["state_path"])
    lock_path = Path(paths["lock_path"])
    behavior_tree_log_path = Path(paths["behavior_tree_log_path"])
    service_log_path = Path(paths["service_log_path"])
    state = _read_json_file(state_path)
    direct_targets, all_targets = _collect_attendance_process_targets()
    root_processes = _process_records_from_targets(direct_targets)
    processes = _process_records_from_targets(all_targets)
    process_count = len(root_processes)
    total_process_count = len(processes)
    child_process_count = max(0, total_process_count - process_count)

    if process_count:
        status = "running"
        state_label = "运行中" if process_count == 1 else f"运行中，{process_count} 个行为树"
        next_run_at = _next_run_at_from_state(state)
    else:
        status = "stopped"
        state_label = "已停止"
        next_run_at = None

    state_last_error = str(state.get("last_error") or "").strip()
    last_error = ""
    if process_count > 1:
        last_error = "检测到多个考勤行为树根进程；下一次启动会先终止旧进程。"
    elif not Path(paths["python_path"]).exists():
        last_error = f"考勤 Python 不存在：{paths['python_path']}"
    elif not Path(paths["script_path"]).exists():
        last_error = f"考勤入口脚本不存在：{paths['script_path']}"
    elif state_last_error:
        last_error = state_last_error

    return {
        "key": ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY,
        "title": ATTENDANCE_BEHAVIOR_TREE_TITLE,
        "running": bool(processes),
        "state": status,
        "state_label": state_label,
        "pid": root_processes[0]["pid"] if root_processes else None,
        "process_count": process_count,
        "child_process_count": child_process_count,
        "total_process_count": total_process_count,
        "root_processes": root_processes,
        "processes": processes,
        "started_at": root_processes[0].get("created_at") if root_processes else None,
        "next_run_at": next_run_at,
        "state_data": state,
        "state_last_error": state_last_error,
        "last_error": last_error,
        "state_exists": state_path.exists(),
        "lock_exists": lock_path.exists(),
        "state_updated_at": _safe_mtime_text(state_path),
        "behavior_tree_log_updated_at": _safe_mtime_text(behavior_tree_log_path),
        "service_log_updated_at": _safe_mtime_text(service_log_path),
        **paths,
    }


def start_attendance_behavior_tree_service(*, replace_existing: bool = True) -> dict[str, Any]:
    stop_result = (
        terminate_attendance_behavior_tree_processes(timeout=5.0)
        if replace_existing
        else {"matched": [], "terminated": [], "remaining": [], "errors": []}
    )
    paths = _status_paths()
    python_path = Path(paths["python_path"])
    script_path = Path(paths["script_path"])
    cwd = Path(paths["cwd"])
    service_log_path = Path(paths["service_log_path"])

    if not python_path.exists():
        raise RuntimeError(f"考勤 Python 不存在：{python_path}")
    if not script_path.exists():
        raise RuntimeError(f"考勤入口脚本不存在：{script_path}")

    service_log_path.parent.mkdir(parents=True, exist_ok=True)
    env = _attendance_subprocess_env(paths)

    with service_log_path.open("ab") as log_file:
        log_file.write(f"\n[{_now_text()}] CodeYun start attendance behavior tree\n".encode("utf-8"))
        proc = popen_service(
            [os.fspath(python_path), os.fspath(script_path)],
            cwd=os.fspath(cwd),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

    time.sleep(0.5)
    return {
        "status": "started",
        "pid": proc.pid,
        "stop_result": stop_result,
        "service": get_attendance_behavior_tree_status(),
    }


def stop_attendance_behavior_tree_service(timeout: float = 5.0) -> dict[str, Any]:
    result = terminate_attendance_behavior_tree_processes(timeout=timeout)
    return {
        "status": "stopped",
        "stop_result": result,
        "service": get_attendance_behavior_tree_status(),
    }


def show_attendance_behavior_tree_schedule(limit: int = 20) -> dict[str, Any]:
    result = _run_attendance_script_command("show_schedule", f"--limit={max(1, int(limit))}", timeout=30.0)
    return {
        "status": "ok",
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "service": get_attendance_behavior_tree_status(),
    }


def reset_attendance_behavior_tree_state() -> dict[str, Any]:
    result = _run_attendance_script_command("reset_state", timeout=30.0)
    return {
        "status": "ok",
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "service": get_attendance_behavior_tree_status(),
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


def _attendance_process_role_label(reason: str) -> str:
    text = str(reason or "").strip()
    if not text:
        return "unknown"
    if text.startswith("descendant-of:"):
        return text
    if text.startswith("cmd:attendance-"):
        return "root"
    return text


def build_attendance_behavior_tree_log_lines(limit: int = 500) -> list[str]:
    status = get_attendance_behavior_tree_status()
    lines = [
        f"名称：{status['title']}",
        f"状态：{status['state_label']}",
        f"PID：{status.get('pid') or '-'}",
        "动作语义：trigger=确保唯一调度器；stop=停止调度器；inspect=查看调度摘要；restart=停旧后拉起唯一实例；reset=清空状态文件但不补跑错过任务",
        f"行为树根进程数：{status.get('process_count') or 0}",
        f"子孙进程数：{status.get('child_process_count') or 0}",
        f"总进程数：{status.get('total_process_count') or status.get('process_count') or 0}",
        f"启动时间：{status.get('started_at') or '-'}",
        f"下次触发：{status.get('next_run_at') or '-'}",
        f"状态更新时间：{status.get('state_updated_at') or '-'}",
        f"行为树日志更新时间：{status.get('behavior_tree_log_updated_at') or '-'}",
        f"服务日志更新时间：{status.get('service_log_updated_at') or '-'}",
        f"状态文件：{status.get('state_path')}",
        f"锁文件：{status.get('lock_path')}",
        f"行为树日志：{status.get('behavior_tree_log_path')}",
        f"入口脚本：{status.get('script_path')}",
    ]
    if status.get("last_error"):
        lines.extend(["", f"提示：{status['last_error']}"])
    if status.get("processes"):
        lines.append("")
        lines.append("说明：Windows 下常见为 1 个 root python + 若干 descendant python/conhost；不要把 descendant 误判成第二棵行为树。")
        lines.append("")
        lines.append("进程：")
        for item in status["processes"][:20]:
            lines.append(
                f"- { _attendance_process_role_label(item.get('matched_reason') or '') } · "
                f"PID {item.get('pid')} · {item.get('created_at') or '-'} · "
                f"{item.get('command_line') or '-'}"
            )

    tail_limit = max(20, min(120, limit // 2 if limit else 80))
    lines.extend(["", "服务启动日志："])
    lines.extend(_tail_text(Path(status["service_log_path"]), lines=tail_limit))
    lines.extend(["", "kqmain 行为树日志尾部："])
    lines.extend(_tail_text(Path(status["behavior_tree_log_path"]), lines=tail_limit))
    return lines[: max(1, limit)]
