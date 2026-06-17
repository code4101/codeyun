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
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.runtime.process_launcher import run_quiet


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
CRITICAL_COMMIT_PRESSURE_PERCENT = 90.0
CRITICAL_COMMIT_PRESSURE_AVAILABLE_MB = 8192
WMI_ACTIVITY_SKIP_COMMIT_PRESSURE_PERCENT = 70.0
WMI_ACTIVITY_SKIP_COMMIT_AVAILABLE_MB = 32 * 1024
WMI_ACTIVITY_CAPTURE_SECONDS = 5.0
WMI_ACTIVITY_CAPTURE_INTERVAL_SECONDS = 0.05
WMI_ACTIVITY_AUTO_WINMGMT_PRIVATE_MB = 4 * 1024
POWERSHELL_WMI_EVENT_LIMIT = 260
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
        result = run_quiet(
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


def _process_snapshot(services_by_pid: dict[int, list[str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter():
        row = _process_row(proc, include_cmdline=True)
        if not row:
            continue
        services = services_by_pid.get(int(row["pid"])) or []
        if services:
            row["services"] = services
        rows.append(row)
    return rows


def _top_private_processes(process_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in process_rows:
        item = dict(row)
        item["cmdline"] = ""
        rows.append(item)
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


def _host_commit_pressure(commit: dict[str, Any]) -> bool:
    commit_percent = float(commit.get("commit_percent") or 0.0)
    commit_available_mb = int(commit.get("commit_available_mb") or 0)
    return commit_percent >= CRITICAL_COMMIT_PRESSURE_PERCENT or commit_available_mb < CRITICAL_COMMIT_PRESSURE_AVAILABLE_MB


def _skip_wmi_activity_probe(commit: dict[str, Any]) -> bool:
    commit_percent = float(commit.get("commit_percent") or 0.0)
    commit_available_mb = int(commit.get("commit_available_mb") or 0)
    return commit_percent >= WMI_ACTIVITY_SKIP_COMMIT_PRESSURE_PERCENT or commit_available_mb < WMI_ACTIVITY_SKIP_COMMIT_AVAILABLE_MB


def _should_auto_capture_wmi_activity(commit: dict[str, Any], top_processes: list[dict[str, Any]]) -> bool:
    if os.name != "nt":
        return False
    commit_percent = float(commit.get("commit_percent") or 0.0)
    if commit_percent >= WMI_ACTIVITY_SKIP_COMMIT_PRESSURE_PERCENT:
        return True
    for item in top_processes:
        services = {str(service).lower() for service in item.get("services") or []}
        if str(item.get("name") or "").lower() == "svchost.exe" and "winmgmt" in services:
            return float(item.get("private_mb") or 0.0) >= WMI_ACTIVITY_AUTO_WINMGMT_PRIVATE_MB
    return False


def _is_pressure_reclaimable_process(cmdline: str, process_name: str = "") -> bool:
    name = process_name.lower()
    if name not in {"python.exe", "pythonw.exe", "python", "pythonw", "uv.exe", "uv"}:
        return False
    normalized = cmdline.replace("\\", "/").lower()
    if "backend.services.ocr_daemon" in normalized:
        return False
    if "backend.services.game_window_daemon" in normalized:
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


def _auto_process_termination_enabled() -> bool:
    value = os.getenv("CODEYUN_HOST_MEMORY_MONITOR_AUTO_TERMINATE")
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


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
) -> tuple[list[psutil.Process], list[dict[str, Any]]]:
    targets: list[psutil.Process] = []
    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
        try:
            pid = int(proc.info.get("pid") or 0)
            if pid in excluded_pids:
                continue
            cmdline = " ".join(str(part) for part in (proc.info.get("cmdline") or []))
            if not _is_pressure_reclaimable_process(cmdline, str(proc.info.get("name") or "")):
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
    if not critical_pressure:
        return {"attempted": False, "reason": "no_host_commit_pressure", "terminated": []}
    reason = "host_commit_pressure"
    if not _auto_process_termination_enabled():
        return {
            "attempted": False,
            "reason": f"{reason}_auto_termination_disabled",
            "terminated": [],
            "killed_after_timeout": [],
        }

    current_pid = os.getpid()
    excluded_pids = {current_pid}
    try:
        current_proc = psutil.Process(current_pid)
        excluded_pids.update(int(parent.pid) for parent in current_proc.parents())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        pass
    targets, terminated = _collect_reclaimable_process_tree_targets(
        excluded_pids=excluded_pids,
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
        "reason": reason,
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


def _should_capture_wmi_process_births() -> bool:
    value = os.getenv("CODEYUN_MONITOR_CAPTURE_WMI_PROCESS_BIRTHS")
    return bool(value and value.strip().lower() not in {"0", "false", "no", "off", "disabled"})


def _should_capture_wmi_activity() -> bool:
    value = os.getenv("CODEYUN_MONITOR_CAPTURE_WMI_ACTIVITY")
    return bool(value and value.strip().lower() not in {"0", "false", "no", "off", "disabled"})


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


def _is_suspicious_wmi_query_client(row: dict[str, Any]) -> bool:
    process = row.get("process") if isinstance(row.get("process"), dict) else {}
    parent = row.get("parent_process") if isinstance(row.get("parent_process"), dict) else {}
    operations = row.get("operations") if isinstance(row.get("operations"), dict) else {}
    recent_events = row.get("recent_events") if isinstance(row.get("recent_events"), list) else []
    operation_text = " ".join(
        [
            " ".join(str(key) for key in operations.keys()),
            " ".join(str(item.get("operation") or "") for item in recent_events if isinstance(item, dict)),
        ]
    ).lower()
    notification_text = " ".join(
        str(item.get("notification_query") or "") for item in recent_events if isinstance(item, dict)
    ).lower()
    client_text = " ".join(
        [
            str(process.get("name") or ""),
            str(process.get("cmdline") or ""),
            str(parent.get("name") or ""),
            str(parent.get("cmdline") or ""),
        ]
    ).lower()

    expensive_query = (
        "win32_perfformatteddata_perfproc_process" in operation_text
        or "iwbemservices::createinstanceenum" in operation_text
        or bool(re.search(r"\bfrom\s+win32_process\b", operation_text))
        or bool(re.search(r"\bwin32_process\s+where\b", operation_text))
    )
    if expensive_query:
        return True

    client_uses_wmi_tooling = any(token in client_text for token in ("get-ciminstance", "get-wmiobject"))
    if client_uses_wmi_tooling:
        return True

    # Process notification subscriptions are noisy but usually cheap; only promote them
    # when WMI is already reporting quota/throttling pressure for that client.
    if "win32_processstarttrace" in notification_text or "win32_processstoptrace" in notification_text:
        possible_causes = " ".join(
            str(item.get("possible_cause") or "") for item in recent_events if isinstance(item, dict)
        ).lower()
        return "quota" in possible_causes or "thrott" in possible_causes

    return False


def _is_codex_tool_wmi_client(row: dict[str, Any]) -> bool:
    process = row.get("process") if isinstance(row.get("process"), dict) else {}
    parent = row.get("parent_process") if isinstance(row.get("parent_process"), dict) else {}
    cmdline = str(process.get("cmdline") or "").lower()
    name = str(process.get("name") or "").lower()
    parent_name = str(parent.get("name") or "").lower()
    parent_cmdline = str(parent.get("cmdline") or "").lower()
    if "codex.exe" not in f"{parent_name} {parent_cmdline}":
        return False
    if not name.endswith("powershell.exe"):
        return False
    return "get-ciminstance" in cmdline or "get-wmiobject" in cmdline


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _wmi_xml_text(fields: dict[str, str], *names: str) -> str:
    lowered = {key.lower(): value for key, value in fields.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value:
            return " ".join(value.split())[:500]
    return ""


def _recent_wmi_activity_clients(
    limit: int = 80,
    *,
    captured_processes: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if os.name != "nt":
        return {}
    captured_processes = captured_processes or {}
    try:
        result = run_quiet(
            [
                "wevtutil",
                "qe",
                "Microsoft-Windows-WMI-Activity/Operational",
                "/q:*[System[(EventID=5857 or EventID=5858 or EventID=5859 or EventID=5860 or EventID=5861)]]",
                f"/c:{limit}",
                "/rd:true",
                "/f:xml",
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
    try:
        root = ET.fromstring(f"<Events>{result.stdout}</Events>")
    except ET.ParseError as exc:
        return {"error": f"parse_wmi_activity_xml_failed: {exc}"}
    for event in list(root):
        fields: dict[str, str] = {}
        event_id = ""
        event_time = ""
        for elem in event.iter():
            name = _xml_local_name(elem.tag)
            text = (elem.text or "").strip()
            if name == "EventID" and text:
                event_id = text
            elif name == "TimeCreated":
                event_time = elem.attrib.get("SystemTime", "")
            elif text:
                fields[name] = text
        pid_text = _wmi_xml_text(fields, "ClientProcessId", "Processid")
        if not pid_text or not pid_text.isdigit():
            continue
        pid = int(pid_text)
        operation = _wmi_xml_text(fields, "Operation")
        notification_query = _wmi_xml_text(fields, "NotificationQuery", "Query")
        namespace = _wmi_xml_text(fields, "Namespace", "NamespaceName")
        result_code = _wmi_xml_text(fields, "ResultCode")
        possible_cause = _wmi_xml_text(fields, "PossibleCause")
        component = _wmi_xml_text(fields, "Component")
        row = clients.setdefault(
            pid,
            {
                "pid": pid,
                "count": 0,
                "operations": {},
                "latest_at": "",
                "recent_events": [],
            },
        )
        row["count"] += 1
        if operation:
            operations = row.setdefault("operations", {})
            operations[operation] = int(operations.get(operation) or 0) + 1
        if notification_query:
            queries = row.setdefault("notification_queries", {})
            queries[notification_query] = int(queries.get(notification_query) or 0) + 1
        if event_time and not row.get("latest_at"):
            row["latest_at"] = event_time
        recent_events = row.setdefault("recent_events", [])
        if len(recent_events) < 5:
            recent_events.append(
                {
                    "event_id": event_id,
                    "time": event_time,
                    "namespace": namespace,
                    "operation": operation,
                    "notification_query": notification_query,
                    "result_code": result_code,
                    "possible_cause": possible_cause,
                    "component": component,
                }
            )

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
    suspicious_rows = [row for row in rows if _is_suspicious_wmi_query_client(row)]
    codex_tool_rows = [row for row in rows if _is_codex_tool_wmi_client(row)]
    exited_client_count = sum(
        1
        for row in rows
        if isinstance(row.get("process"), dict) and bool(row["process"].get("exited"))
    )
    return {
        "event_count": sum(int(item.get("count") or 0) for item in rows),
        "client_count": len(rows),
        "exited_client_count": exited_client_count,
        "suspicious_query_client_count": len(suspicious_rows),
        "codex_tool_client_count": len(codex_tool_rows),
        "top_clients": rows[:12],
        "suspicious_query_clients": suspicious_rows[:12],
        "codex_tool_clients": codex_tool_rows[:12],
    }


def _normalize_powershell_wmi_command(command: str) -> str:
    text = " ".join(command.split())[:2000]
    text = re.sub(
        r"ProcessId\s*=\s*(?:\d+\s+OR\s+)*\d+",
        "ProcessId = <pid-list>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"ProcessId,ParentProcessId,CommandLine,WorkingSetSize,@\{Name='CpuPercent'.*?ConvertTo-Json -Depth 2",
        "ProcessId,ParentProcessId,CommandLine,WorkingSetSize,<cpu+age> | ConvertTo-Json -Depth 2",
        text,
        flags=re.IGNORECASE,
    )
    return text[:1200]


def _powershell_wmi_command_from_text(text: str) -> str:
    compact = " ".join(text.split())
    match = re.search(
        r"(powershell\.exe\s+-NoProfile\s+-NonInteractive\s+-Command\s+.*?)(?:\s+EngineVersion=|\s+CommandName=|$)",
        compact,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)[:1800]
    match = re.search(
        r"(powershell\.exe\s+-NoProfile\s+-NonInteractive\s+-Command\s+.*)",
        compact,
        flags=re.IGNORECASE,
    )
    return match.group(1)[:1800] if match else ""


def _recent_powershell_wmi_commands(limit: int = POWERSHELL_WMI_EVENT_LIMIT) -> dict[str, Any]:
    if os.name != "nt":
        return {}
    channels = ["Microsoft-Windows-PowerShell/Operational", "Windows PowerShell"]
    patterns = (
        "get-ciminstance",
        "get-wmiobject",
        "win32_process",
        "win32_perfformatteddata_perfproc_process",
    )
    commands: dict[str, dict[str, Any]] = {}
    examples: list[dict[str, str]] = []
    event_count = 0
    errors: list[str] = []
    for channel in channels:
        try:
            result = run_quiet(
                ["wevtutil", "qe", channel, "/f:xml", "/rd:true", f"/c:{limit}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12,
                check=False,
            )
        except Exception as exc:
            errors.append(f"{channel}: {exc}")
            continue
        if result.returncode != 0:
            errors.append(f"{channel}: {(result.stderr or result.stdout).strip()[:300]}")
            continue
        try:
            root = ET.fromstring(f"<Events>{result.stdout}</Events>")
        except ET.ParseError as exc:
            errors.append(f"{channel}: parse_powershell_xml_failed: {exc}")
            continue
        for event in list(root):
            event_text = "".join(event.itertext())
            lowered = event_text.lower()
            if not any(pattern in lowered for pattern in patterns):
                continue
            command = _powershell_wmi_command_from_text(event_text)
            if not command:
                continue
            normalized = _normalize_powershell_wmi_command(command)
            event_time = ""
            event_id = ""
            for elem in event.iter():
                name = _xml_local_name(elem.tag)
                if name == "EventID" and elem.text:
                    event_id = elem.text.strip()
                elif name == "TimeCreated":
                    event_time = elem.attrib.get("SystemTime", "")
            row = commands.setdefault(
                normalized,
                {
                    "count": 0,
                    "first_at": event_time,
                    "last_at": event_time,
                    "channels": set(),
                    "event_ids": {},
                    "command": normalized,
                },
            )
            row["count"] = int(row.get("count") or 0) + 1
            row["channels"].add(channel)
            if event_time:
                if not row.get("first_at") or event_time < str(row.get("first_at") or ""):
                    row["first_at"] = event_time
                if event_time > str(row.get("last_at") or ""):
                    row["last_at"] = event_time
            event_ids = row.setdefault("event_ids", {})
            if event_id:
                event_ids[event_id] = int(event_ids.get(event_id) or 0) + 1
            event_count += 1
            if len(examples) < 8:
                examples.append(
                    {
                        "time": event_time,
                        "channel": channel,
                        "command": command[:1200],
                    }
                )

    top_commands: list[dict[str, Any]] = []
    for row in sorted(commands.values(), key=lambda item: int(item.get("count") or 0), reverse=True):
        top_commands.append(
            {
                "count": row.get("count"),
                "first_at": row.get("first_at"),
                "last_at": row.get("last_at"),
                "channels": sorted(row.get("channels") or []),
                "event_ids": row.get("event_ids") or {},
                "command": row.get("command"),
            }
        )
    result: dict[str, Any] = {
        "event_count": event_count,
        "unique_command_count": len(top_commands),
        "top_commands": top_commands[:8],
        "examples": examples,
    }
    if errors:
        result["errors"] = errors[:4]
    return result


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


def _interesting_processes(process_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in process_rows:
        name_lower = str(row.get("name") or "").lower()
        cmdline = str(row.get("cmdline") or "")
        service_names = {str(service).lower() for service in row.get("services") or []}
        if (
            name_lower not in INTERESTING_PROCESS_NAMES
            and "tcpdump" not in cmdline.lower()
            and "winmgmt" not in service_names
        ):
            continue
        rows.append(dict(row))
    return sorted(rows, key=lambda item: float(item.get("private_mb") or 0), reverse=True)


def _limit_interesting_processes(rows: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    limited = list(rows[:limit])
    included_pids = {int(item.get("pid") or 0) for item in limited}
    for row in rows[limit:]:
        services = {str(service).lower() for service in row.get("services") or []}
        if "winmgmt" in services and int(row.get("pid") or 0) not in included_pids:
            limited.append(row)
            included_pids.add(int(row.get("pid") or 0))
    return limited


def _critical_findings(sample: dict[str, Any]) -> list[dict[str, Any]]:
    commit = sample.get("commit") or {}
    commit_percent = float(commit.get("commit_percent") or 0.0)
    commit_available_mb = int(commit.get("commit_available_mb") or 0)
    winmgmt = _winmgmt_row(sample) or {}
    winmgmt_private_mb = float(winmgmt.get("private_mb") or 0.0)
    powershell_wmi = sample.get("recent_powershell_wmi_commands") or {}
    powershell_wmi_event_count = int(powershell_wmi.get("event_count") or 0)
    top_powershell_wmi_commands = [
        {
            "count": int(item.get("count") or 0),
            "first_at": item.get("first_at"),
            "last_at": item.get("last_at"),
            "command": str(item.get("command") or "")[:500],
        }
        for item in (powershell_wmi.get("top_commands") or [])[:3]
        if isinstance(item, dict)
    ]
    wmi_activity = sample.get("recent_wmi_activity") or {}
    suspicious_query_client_count = int(wmi_activity.get("suspicious_query_client_count") or 0)

    findings: list[dict[str, Any]] = []
    if commit_percent >= 90.0 and winmgmt_private_mb >= 16 * 1024:
        findings.append(
            {
                "severity": "critical",
                "code": "winmgmt_commit_exhaustion",
                "summary": "Windows commit pressure is dominated by Winmgmt memory growth.",
                "evidence": {
                    "commit_percent": commit_percent,
                    "commit_available_mb": commit_available_mb,
                    "winmgmt_private_mb": round(winmgmt_private_mb, 1),
                    "powershell_wmi_event_count": powershell_wmi_event_count,
                    "suspicious_wmi_query_client_count": suspicious_query_client_count,
                    "top_powershell_wmi_commands": top_powershell_wmi_commands,
                },
                "recommended_actions": [
                    "Stop or reduce the external high-frequency WMI process queries.",
                    "Close likely WMI-heavy external applications before touching MuMu or CodeYun services.",
                    "If commit remains critical after the query source stops, manually restart Winmgmt or reboot after saving work.",
                ],
                "not_primary_suspects": ["adb", "packet_capture", "ocr_daemon"],
                "auto_action_taken": False,
            }
        )
    elif winmgmt_private_mb >= 8 * 1024:
        findings.append(
            {
                "severity": "warning",
                "code": "winmgmt_commit_growth",
                "summary": "Winmgmt memory is abnormally high and should be watched as the primary pressure source.",
                "evidence": {
                    "commit_percent": commit_percent,
                    "commit_available_mb": commit_available_mb,
                    "winmgmt_private_mb": round(winmgmt_private_mb, 1),
                    "powershell_wmi_event_count": powershell_wmi_event_count,
                    "suspicious_wmi_query_client_count": suspicious_query_client_count,
                    "top_powershell_wmi_commands": top_powershell_wmi_commands,
                },
                "recommended_actions": [
                    "Keep WMI-based diagnostics disabled unless explicitly needed.",
                    "Investigate external tools repeatedly launching Get-CimInstance or hardware inventory queries.",
                ],
                "auto_action_taken": False,
            }
        )
    return findings


def collect_sample() -> dict[str, Any]:
    services_by_pid = _windows_services_by_pid()
    commit = _windows_commit_snapshot()
    process_rows = _process_snapshot(services_by_pid)
    top_processes = _top_private_processes(process_rows)
    interesting = _interesting_processes(process_rows)
    mitigation = _terminate_reclaimable_processes_under_pressure(commit)
    sample = {
        "sampled_at": datetime.now().isoformat(timespec="seconds"),
        "commit": commit,
        "pressure_hints": _pressure_hints(commit, top_processes),
        "top_private_processes": top_processes,
        "interesting_processes": _limit_interesting_processes(interesting),
        "mitigation": mitigation,
    }
    trend = _monitor_trend(sample)
    if trend:
        sample["trend"] = trend
    mumu_health = _latest_mumu_device_health_event()
    if mumu_health:
        sample["latest_mumu_device_health"] = mumu_health
    capture_wmi_activity = _should_capture_wmi_activity()
    auto_capture_wmi_activity = _should_auto_capture_wmi_activity(commit, top_processes)
    if not capture_wmi_activity and not auto_capture_wmi_activity:
        sample["recent_wmi_activity"] = {
            "skipped": True,
            "reason": "disabled_by_default",
        }
    elif capture_wmi_activity and not auto_capture_wmi_activity and _skip_wmi_activity_probe(commit):
        sample["recent_wmi_activity"] = {
            "skipped": True,
            "reason": "host_commit_pressure",
        }
    else:
        # The WMI Activity log often records very short-lived clients after they exit.
        # Keep process-birth polling opt-in so high-pressure samples stay cheap.
        capture_births = _should_capture_wmi_process_births()
        captured_processes = _capture_recent_process_births() if capture_births else {}
        wmi_activity = _recent_wmi_activity_clients(captured_processes=captured_processes)
        if wmi_activity:
            wmi_activity["capture_mode"] = "auto_winmgmt_pressure" if auto_capture_wmi_activity else "manual"
            if captured_processes:
                wmi_activity["captured_process_count"] = len(captured_processes)
            if int(wmi_activity.get("suspicious_query_client_count") or 0) >= 20:
                sample["pressure_hints"] = sorted(set([*sample["pressure_hints"], "external_wmi_query_storm"]))
            if int(wmi_activity.get("codex_tool_client_count") or 0) >= 3:
                sample["pressure_hints"] = sorted(set([*sample["pressure_hints"], "codex_tool_wmi_queries"]))
            if int(wmi_activity.get("exited_client_count") or 0) >= 20:
                sample["pressure_hints"] = sorted(set([*sample["pressure_hints"], "transient_wmi_clients"]))
            sample["recent_wmi_activity"] = wmi_activity
            powershell_wmi = _recent_powershell_wmi_commands()
            if powershell_wmi:
                sample["recent_powershell_wmi_commands"] = powershell_wmi
    critical_findings = _critical_findings(sample)
    if critical_findings:
        sample["critical_findings"] = critical_findings
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
        print(json.dumps({"log_path": str(path) if path else "", **sample}, ensure_ascii=True, indent=2))
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
