from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


INTERESTING_PROCESS_NAMES = {
    "adb.exe",
    "dumpcap.exe",
    "mumu.exe",
    "mumumanager.exe",
    "mumuvmmheadless.exe",
    "python.exe",
    "pythonw.exe",
    "svchost.exe",
    "tcpdump",
    "tshark.exe",
    "wireshark.exe",
}
OCR_PREEMPTIVE_COMMIT_PRESSURE_PERCENT = 85.0
OCR_PREEMPTIVE_COMMIT_PRESSURE_AVAILABLE_MB = 16 * 1024
CRITICAL_COMMIT_PRESSURE_PERCENT = 90.0
CRITICAL_COMMIT_PRESSURE_AVAILABLE_MB = 8192
WMI_ACTIVITY_CAPTURE_SECONDS = 5.0
WMI_ACTIVITY_CAPTURE_INTERVAL_SECONDS = 0.05
DEFAULT_LOOP_INTERVAL_SECONDS = 30 * 60


def _data_dir() -> Path:
    configured = os.getenv("CODEYUN_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    machine = (os.getenv("COMPUTERNAME") or socket.gethostname() or "local").strip().lower()
    if machine.startswith("codepc_"):
        suffix = machine.removeprefix("codepc_")
    else:
        suffix = machine
    home = ROOT_DIR.parent.parent
    return home / "data" / "m2603codeyun" / f"codepc_{suffix}"


def _monitor_log_path(now: float | None = None) -> Path:
    stamp = datetime.fromtimestamp(now or time.time()).strftime("%Y%m%d")
    path = _data_dir() / "fanxiu" / "host-memory-monitor"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"host-memory-{stamp}.jsonl"


def _monitor_dir() -> Path:
    path = _data_dir() / "fanxiu" / "host-memory-monitor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _monitor_loop_pid_path() -> Path:
    return _monitor_dir() / "host-memory-monitor-loop.pid.json"


def _monitor_loop_error_path(now: float | None = None) -> Path:
    stamp = datetime.fromtimestamp(now or time.time()).strftime("%Y%m%d")
    return _monitor_dir() / f"host-memory-monitor-errors-{stamp}.jsonl"


def _windows_commit_snapshot() -> dict[str, Any]:
    if os.name != "nt":
        return {}

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {}
    commit_limit = int(status.ullTotalPageFile)
    commit_available = int(status.ullAvailPageFile)
    committed = max(0, commit_limit - commit_available)
    return {
        "committed_mb": int(committed / 1024 / 1024),
        "commit_limit_mb": int(commit_limit / 1024 / 1024),
        "commit_available_mb": int(commit_available / 1024 / 1024),
        "commit_percent": round(committed * 100.0 / commit_limit, 2) if commit_limit else 0.0,
        "physical_total_mb": int(status.ullTotalPhys / 1024 / 1024),
        "physical_available_mb": int(status.ullAvailPhys / 1024 / 1024),
        "memory_load_percent": int(status.dwMemoryLoad),
    }


def _windows_services_by_pid() -> dict[int, list[str]]:
    if os.name != "nt":
        return {}
    try:
        result = subprocess.run(
            ["sc.exe", "queryex", "type=", "service", "state=", "all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except Exception:
        return {}
    if result.returncode != 0:
        return {}
    services_by_pid: dict[int, list[str]] = {}
    service_name = ""
    for line in result.stdout.splitlines():
        service_match = re.match(r"^\s*SERVICE_NAME:\s*(.+?)\s*$", line)
        if service_match:
            service_name = service_match.group(1).strip()
            continue
        pid_match = re.match(r"^\s*PID\s*:\s*(\d+)\s*$", line)
        if pid_match and service_name:
            pid = int(pid_match.group(1))
            services_by_pid.setdefault(pid, []).append(service_name)
    return {pid: sorted(set(services)) for pid, services in services_by_pid.items()}


def _process_row(proc: psutil.Process, *, include_cmdline: bool) -> dict[str, Any] | None:
    try:
        info = proc.as_dict(attrs=["pid", "ppid", "name", "cmdline", "create_time"])
        memory = proc.memory_info()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None
    cmdline = " ".join(str(part) for part in (info.get("cmdline") or [])) if include_cmdline else ""
    return {
        "pid": int(info.get("pid") or proc.pid),
        "ppid": int(info.get("ppid") or 0),
        "name": str(info.get("name") or ""),
        "private_mb": round(float(getattr(memory, "private", 0)) / 1024 / 1024, 1),
        "rss_mb": round(float(memory.rss) / 1024 / 1024, 1),
        "started_at": datetime.fromtimestamp(float(info.get("create_time") or 0)).isoformat(timespec="seconds"),
        "cmdline": cmdline[:500],
    }


def _top_private_processes(services_by_pid: dict[int, list[str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter():
        row = _process_row(proc, include_cmdline=False)
        if not row:
            continue
        services = services_by_pid.get(int(row["pid"])) or []
        if services:
            row["services"] = services
        rows.append(row)
    return sorted(rows, key=lambda item: float(item.get("private_mb") or 0), reverse=True)[:15]


def _pressure_hints(commit: dict[str, Any], top_processes: list[dict[str, Any]]) -> list[str]:
    hints: list[str] = []
    commit_percent = float(commit.get("commit_percent") or 0.0)
    commit_available_mb = int(commit.get("commit_available_mb") or 0)
    if commit_percent >= 95 or commit_available_mb < 4096:
        hints.append("windows_commit_nearly_exhausted")
    elif commit_percent >= 90 or commit_available_mb < 8192:
        hints.append("windows_commit_pressure")
    for item in top_processes:
        private_mb = float(item.get("private_mb") or 0)
        name = str(item.get("name") or "").lower()
        services = {str(service).lower() for service in item.get("services") or []}
        if name == "svchost.exe" and private_mb >= 8192:
            hints.append("large_svchost_commit")
        if name == "svchost.exe" and "winmgmt" in services and private_mb >= 4096:
            hints.append("winmgmt_wmi_commit_growth")
        if name.startswith("mumu") and private_mb >= 8192:
            hints.append("mumu_commit_high")
        if name.startswith("python") and private_mb >= 4096:
            hints.append("python_commit_high")
    return sorted(set(hints))


def _packet_decode_pressure(commit: dict[str, Any]) -> dict[str, Any]:
    commit_percent = float(commit.get("commit_percent") or 0.0)
    commit_available_mb = int(commit.get("commit_available_mb") or 0)
    skip = commit_percent >= OCR_PREEMPTIVE_COMMIT_PRESSURE_PERCENT or commit_available_mb < OCR_PREEMPTIVE_COMMIT_PRESSURE_AVAILABLE_MB
    return {
        "skip": skip,
        "reason": "host_commit_pressure" if skip else None,
        "commit": {
            key: commit.get(key)
            for key in ("committed_mb", "commit_limit_mb", "commit_available_mb", "commit_percent")
        },
    }


def _host_commit_pressure(commit: dict[str, Any]) -> bool:
    commit_percent = float(commit.get("commit_percent") or 0.0)
    commit_available_mb = int(commit.get("commit_available_mb") or 0)
    return commit_percent >= CRITICAL_COMMIT_PRESSURE_PERCENT or commit_available_mb < CRITICAL_COMMIT_PRESSURE_AVAILABLE_MB


def _ocr_preemptive_pressure(commit: dict[str, Any]) -> bool:
    commit_percent = float(commit.get("commit_percent") or 0.0)
    commit_available_mb = int(commit.get("commit_available_mb") or 0)
    return commit_percent >= OCR_PREEMPTIVE_COMMIT_PRESSURE_PERCENT or commit_available_mb < OCR_PREEMPTIVE_COMMIT_PRESSURE_AVAILABLE_MB


def _is_ocr_service_process(cmdline: str) -> bool:
    return "backend.services.ocr_daemon" in cmdline.replace("\\", "/").lower()


def _is_pressure_reclaimable_process(cmdline: str, process_name: str = "") -> bool:
    name = process_name.lower()
    if name not in {"python.exe", "pythonw.exe", "python", "pythonw", "uv.exe", "uv"}:
        return False
    normalized = cmdline.replace("\\", "/").lower()
    if _is_ocr_service_process(cmdline):
        return True
    if ".venv/scripts/pytest.exe" in normalized and (
        "backend/tests/test_fanxiu_mumu_control.py" in normalized
        or "backend/tests/test_fanxiu_data_annotation_runtime_guard.py" in normalized
        or "tests/test_fanxiu_data_annotation_scheduler.py" in normalized
    ):
        return True
    if "scripts/fanxiu_bt.py" in normalized:
        return True
    return False


def _append_reclaim_target(
    targets: list[psutil.Process],
    rows: list[dict[str, Any]],
    proc: psutil.Process,
    *,
    excluded_pids: set[int],
    reclaim_parent_pid: int | None = None,
    late_reclaim: bool = False,
) -> None:
    try:
        pid = int(proc.pid)
        if pid in excluded_pids or any(int(target.pid) == pid for target in targets):
            return
        row = _process_row(proc, include_cmdline=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return
    if not row:
        return
    if reclaim_parent_pid is not None and reclaim_parent_pid != pid:
        row["reclaim_parent_pid"] = reclaim_parent_pid
    if late_reclaim:
        row["late_reclaim"] = True
    rows.append(row)
    targets.append(proc)


def _collect_reclaimable_process_tree_targets(
    *,
    excluded_pids: set[int],
    late_reclaim: bool = False,
    ocr_only: bool = False,
) -> tuple[list[psutil.Process], list[dict[str, Any]]]:
    targets: list[psutil.Process] = []
    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
        try:
            pid = int(proc.info.get("pid") or 0)
            if pid in excluded_pids:
                continue
            cmdline = " ".join(str(part) for part in (proc.info.get("cmdline") or []))
            if ocr_only:
                if not _is_ocr_service_process(cmdline):
                    continue
            elif not _is_pressure_reclaimable_process(cmdline, str(proc.info.get("name") or "")):
                continue
            _append_reclaim_target(
                targets,
                rows,
                proc,
                excluded_pids=excluded_pids,
                late_reclaim=late_reclaim,
            )
            for child in proc.children(recursive=True):
                _append_reclaim_target(
                    targets,
                    rows,
                    child,
                    excluded_pids=excluded_pids,
                    reclaim_parent_pid=pid,
                    late_reclaim=late_reclaim,
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return targets, rows


def _terminate_reclaimable_processes_under_pressure(commit: dict[str, Any]) -> dict[str, Any]:
    critical_pressure = _host_commit_pressure(commit)
    ocr_preemptive = _ocr_preemptive_pressure(commit)
    if not critical_pressure and not ocr_preemptive:
        return {"attempted": False, "reason": "no_host_commit_pressure", "terminated": []}

    current_pid = os.getpid()
    excluded_pids = {current_pid}
    try:
        current_proc = psutil.Process(current_pid)
        excluded_pids.update(int(parent.pid) for parent in current_proc.parents())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        pass
    targets, terminated = _collect_reclaimable_process_tree_targets(
        excluded_pids=excluded_pids,
        ocr_only=not critical_pressure,
    )
    for proc in targets:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    _, alive = psutil.wait_procs(targets, timeout=3)
    killed: list[int] = []
    for proc in alive:
        try:
            killed.append(int(proc.pid))
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    time.sleep(1.0)
    late_targets, late_rows = _collect_reclaimable_process_tree_targets(
        excluded_pids=excluded_pids,
        late_reclaim=True,
        ocr_only=not critical_pressure,
    )
    terminated.extend(late_rows)
    for proc in late_targets:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    _, late_alive = psutil.wait_procs(late_targets, timeout=3)
    for proc in late_alive:
        try:
            killed.append(int(proc.pid))
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return {
        "attempted": True,
        "reason": "host_commit_pressure" if critical_pressure else "ocr_preemptive_commit_pressure",
        "terminated": terminated,
        "killed_after_timeout": killed,
    }


def _latest_mumu_device_health_event() -> dict[str, Any]:
    path = _data_dir() / "fanxiu" / "mumu-device-health" / f"device-health-{datetime.now().strftime('%Y%m%d')}.jsonl"
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return {}
    if not lines:
        return {}
    try:
        item = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(item, dict):
        return {}
    native = item.get("mumu_native") if isinstance(item.get("mumu_native"), dict) else {}
    state = item.get("state") if isinstance(item.get("state"), dict) else {}
    return {
        "time": item.get("time"),
        "event": item.get("event"),
        "reason": item.get("reason") or item.get("last_recovery_reason"),
        "status": item.get("status") or state.get("status"),
        "recovered": item.get("recovered") if "recovered" in item else state.get("recovered"),
        "recovery_skipped": item.get("recovery_skipped") or state.get("recovery_skipped"),
        "suspected_causes": native.get("suspected_causes") if isinstance(native, dict) else None,
    }


def _capture_recent_process_births(
    *,
    duration_seconds: float = WMI_ACTIVITY_CAPTURE_SECONDS,
    interval_seconds: float = WMI_ACTIVITY_CAPTURE_INTERVAL_SECONDS,
) -> dict[int, dict[str, Any]]:
    deadline = time.time() + max(0.0, duration_seconds)
    captured: dict[int, dict[str, Any]] = {}
    while time.time() < deadline:
        for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
            try:
                pid = int(proc.info.get("pid") or 0)
                if pid in captured:
                    continue
                cmdline = " ".join(str(part) for part in (proc.info.get("cmdline") or []))
                captured[pid] = {
                    "pid": pid,
                    "ppid": int(proc.info.get("ppid") or 0),
                    "name": str(proc.info.get("name") or ""),
                    "started_at": datetime.fromtimestamp(float(proc.info.get("create_time") or 0)).isoformat(timespec="seconds"),
                    "cmdline": cmdline[:500],
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        time.sleep(max(0.01, interval_seconds))
    return captured


def _parent_row(pid: int, captured_processes: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    captured = captured_processes.get(pid)
    if captured:
        return {
            "pid": captured.get("pid"),
            "name": captured.get("name"),
            "cmdline": captured.get("cmdline"),
        }
    try:
        parent = psutil.Process(pid)
        row = _process_row(parent, include_cmdline=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None
    if not row:
        return None
    return {
        "pid": row.get("pid"),
        "name": row.get("name"),
        "private_mb": row.get("private_mb"),
        "cmdline": row.get("cmdline"),
    }


def _recent_wmi_activity_clients(
    limit: int = 80,
    *,
    captured_processes: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if os.name != "nt":
        return {}
    captured_processes = captured_processes or {}
    try:
        result = subprocess.run(
            [
                "wevtutil",
                "qe",
                "Microsoft-Windows-WMI-Activity/Operational",
                "/q:*[System[(EventID=5857 or EventID=5858 or EventID=5859 or EventID=5860 or EventID=5861)]]",
                f"/c:{limit}",
                "/rd:true",
                "/f:text",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except Exception as exc:
        return {"error": str(exc)}
    if result.returncode != 0:
        return {"error": (result.stderr or result.stdout).strip()[:500]}

    clients: dict[int, dict[str, Any]] = {}
    for block in re.split(r"\r?\n\r?\n(?=Event\[\d+\])", result.stdout):
        pid_match = re.search(r"ClientProcessId\s*=\s*(\d+)", block)
        if not pid_match:
            continue
        pid = int(pid_match.group(1))
        operation_match = re.search(r"Operation\s*=\s*(.+?)(?:;\s*ResultCode|\x00|\r?\n)", block, flags=re.S)
        operation = " ".join((operation_match.group(1) if operation_match else "").split())
        time_match = re.search(r"Date:\s*([^\r\n]+)", block)
        row = clients.setdefault(
            pid,
            {
                "pid": pid,
                "count": 0,
                "operations": {},
                "latest_at": "",
            },
        )
        row["count"] += 1
        if operation:
            operations = row.setdefault("operations", {})
            operations[operation] = int(operations.get(operation) or 0) + 1
        if time_match and not row.get("latest_at"):
            row["latest_at"] = time_match.group(1).strip()

    for pid, row in clients.items():
        captured = captured_processes.get(pid)
        if captured:
            row["process"] = {
                "name": captured.get("name"),
                "ppid": captured.get("ppid"),
                "started_at": captured.get("started_at"),
                "cmdline": captured.get("cmdline"),
            }
            parent_pid = int(captured.get("ppid") or 0)
            parent = _parent_row(parent_pid, captured_processes) if parent_pid else None
            if parent:
                row["parent_process"] = parent
            continue
        try:
            proc = psutil.Process(pid)
            proc_row = _process_row(proc, include_cmdline=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            proc_row = None
        if proc_row:
            row["process"] = {
                "name": proc_row.get("name"),
                "ppid": proc_row.get("ppid"),
                "private_mb": proc_row.get("private_mb"),
                "cmdline": proc_row.get("cmdline"),
            }
            parent_pid = int(proc_row.get("ppid") or 0)
            parent = _parent_row(parent_pid, captured_processes) if parent_pid else None
            if parent:
                row["parent_process"] = parent
        else:
            row["process"] = {"exited": True}

    rows = sorted(clients.values(), key=lambda item: int(item.get("count") or 0), reverse=True)
    return {
        "event_count": sum(int(item.get("count") or 0) for item in rows),
        "client_count": len(rows),
        "top_clients": rows[:12],
    }


def _load_recent_monitor_rows(limit: int = 12) -> list[dict[str, Any]]:
    path = _monitor_log_path()
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _winmgmt_row(sample: dict[str, Any]) -> dict[str, Any] | None:
    for collection_name in ("top_private_processes", "interesting_processes"):
        for item in sample.get(collection_name) or []:
            if not isinstance(item, dict):
                continue
            if int(item.get("pid") or 0) == 4312:
                return item
            services = {str(service).lower() for service in item.get("services") or []}
            if str(item.get("name") or "").lower() == "svchost.exe" and "winmgmt" in services:
                return item
    return None


def _monitor_trend(current_sample: dict[str, Any]) -> dict[str, Any]:
    recent = _load_recent_monitor_rows()
    if not recent:
        return {}
    previous = recent[-1]
    try:
        current_time = datetime.fromisoformat(str(current_sample.get("sampled_at")))
        previous_time = datetime.fromisoformat(str(previous.get("sampled_at")))
    except ValueError:
        return {}
    elapsed_minutes = max(0.001, (current_time - previous_time).total_seconds() / 60.0)
    current_commit = current_sample.get("commit") or {}
    previous_commit = previous.get("commit") or {}
    current_winmgmt = _winmgmt_row(current_sample) or {}
    previous_winmgmt = _winmgmt_row(previous) or {}
    commit_delta = float(current_commit.get("committed_mb") or 0) - float(previous_commit.get("committed_mb") or 0)
    winmgmt_delta = float(current_winmgmt.get("private_mb") or 0) - float(previous_winmgmt.get("private_mb") or 0)
    return {
        "previous_sampled_at": previous.get("sampled_at"),
        "elapsed_minutes": round(elapsed_minutes, 2),
        "commit_delta_mb": round(commit_delta, 1),
        "commit_delta_mb_per_min": round(commit_delta / elapsed_minutes, 1),
        "winmgmt_private_delta_mb": round(winmgmt_delta, 1),
        "winmgmt_private_delta_mb_per_min": round(winmgmt_delta / elapsed_minutes, 1),
    }


def _interesting_processes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter():
        row = _process_row(proc, include_cmdline=True)
        if not row:
            continue
        name_lower = str(row.get("name") or "").lower()
        cmdline = str(row.get("cmdline") or "")
        if name_lower not in INTERESTING_PROCESS_NAMES and "tcpdump" not in cmdline.lower():
            continue
        rows.append(row)
    return sorted(rows, key=lambda item: float(item.get("private_mb") or 0), reverse=True)


def collect_sample() -> dict[str, Any]:
    services_by_pid = _windows_services_by_pid()
    commit = _windows_commit_snapshot()
    top_processes = _top_private_processes(services_by_pid)
    interesting = _interesting_processes()
    mitigation = _terminate_reclaimable_processes_under_pressure(commit)
    sample = {
        "sampled_at": datetime.now().isoformat(timespec="seconds"),
        "commit": commit,
        "pressure_hints": _pressure_hints(commit, top_processes),
        "top_private_processes": top_processes,
        "interesting_processes": interesting[:30],
        "packet_decode_pressure": _packet_decode_pressure(commit),
        "mitigation": mitigation,
    }
    trend = _monitor_trend(sample)
    if trend:
        sample["trend"] = trend
    mumu_health = _latest_mumu_device_health_event()
    if mumu_health:
        sample["latest_mumu_device_health"] = mumu_health
    captured_processes = _capture_recent_process_births()
    wmi_activity = _recent_wmi_activity_clients(captured_processes=captured_processes)
    if wmi_activity:
        sample["recent_wmi_activity"] = wmi_activity
    return sample


def append_sample(sample: dict[str, Any], path: Path | None = None) -> Path:
    target = path or _monitor_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as file:
        file.write(json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n")
    return target


def _process_is_monitor_loop(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(str(part) for part in proc.cmdline()).replace("\\", "/").lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False
    return "scripts/fanxiu_host_memory_monitor.py" in cmdline and "--loop" in cmdline


def _acquire_loop_lock(pid_path: Path | None = None) -> tuple[bool, dict[str, Any]]:
    path = pid_path or _monitor_loop_pid_path()
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}
    existing_pid = int(existing.get("pid") or 0) if isinstance(existing, dict) else 0
    if _process_is_monitor_loop(existing_pid):
        return False, {"already_running": True, "pid": existing_pid, "pid_path": str(path)}

    payload = {
        "pid": os.getpid(),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "script": str(Path(__file__).resolve()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, {"already_running": False, "pid": os.getpid(), "pid_path": str(path)}


def _release_loop_lock(pid_path: Path | None = None) -> None:
    path = pid_path or _monitor_loop_pid_path()
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if int(existing.get("pid") or 0) == os.getpid():
        try:
            path.unlink()
        except OSError:
            pass


def _append_loop_error(error: dict[str, Any]) -> Path:
    path = _monitor_loop_error_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(error, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path


def run_loop(interval_seconds: float) -> int:
    acquired, lock = _acquire_loop_lock()
    if not acquired:
        print(json.dumps(lock, ensure_ascii=False))
        return 0
    try:
        while True:
            started = time.time()
            try:
                sample = collect_sample()
                append_sample(sample)
            except Exception as exc:
                _append_loop_error(
                    {
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "error": repr(exc),
                        "traceback": traceback.format_exc()[-4000:],
                    }
                )
            elapsed = time.time() - started
            time.sleep(max(1.0, interval_seconds - elapsed))
    finally:
        _release_loop_lock()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample Fanxiu host commit pressure without WMI.")
    parser.add_argument("--no-write", action="store_true", help="Print only; do not append JSONL.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON instead of a short summary.")
    parser.add_argument("--loop", action="store_true", help="Run forever and append one sample per interval.")
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_LOOP_INTERVAL_SECONDS, help="Loop interval in seconds.")
    args = parser.parse_args()

    if args.loop:
        return run_loop(max(30.0, float(args.interval_seconds or DEFAULT_LOOP_INTERVAL_SECONDS)))

    sample = collect_sample()
    path = None if args.no_write else append_sample(sample)
    if args.json:
        print(json.dumps({"log_path": str(path) if path else "", **sample}, ensure_ascii=False, indent=2))
        return 0

    commit = sample.get("commit") or {}
    top = sample.get("top_private_processes") or []
    print(
        "commit "
        f"{commit.get('committed_mb')} / {commit.get('commit_limit_mb')} MB "
        f"({commit.get('commit_percent')}%), available={commit.get('commit_available_mb')} MB"
    )
    if sample.get("pressure_hints"):
        print("pressure_hints:", ", ".join(str(item) for item in sample["pressure_hints"]))
    if top:
        leader = top[0]
        services = ",".join(str(item) for item in (leader.get("services") or []))
        print(
            "top_private:",
            f"pid={leader.get('pid')} name={leader.get('name')} private={leader.get('private_mb')}MB services={services}",
        )
    if path:
        print(f"log_path: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
