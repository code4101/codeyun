from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if os.fspath(ROOT_DIR) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT_DIR))

from backend.core.runtime.process_launcher import popen_python_script_service

MONITOR_DIR = Path(tempfile.gettempdir()) / "codeyun" / "visible-console-monitor"
EVENTS_PATH = MONITOR_DIR / "events_24h.jsonl"
BASELINE_PATH = MONITOR_DIR / "codeyun_popup_24h_baseline.json"
STATUS_PATH = MONITOR_DIR / "codeyun_popup_24h_status.json"
MONITOR_SCRIPT = ROOT_DIR / "scripts" / "codeyun_visible_console_monitor.py"
MONITOR_STATUS_PATH = MONITOR_DIR / "codeyun_visible_console_monitor_status.json"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _event_time(event: dict[str, Any]) -> str:
    return str(event.get("time") or "")


def _load_events(path: Path = EVENTS_PATH) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def _load_baseline(path: Path = BASELINE_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, TIME_FORMAT)
    except ValueError:
        return None


def _load_monitor_status() -> dict[str, Any]:
    try:
        data = json.loads(MONITOR_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    status = data if isinstance(data, dict) else {}
    pid = int(status.get("pid") or 0)
    status["alive"] = _pid_alive(pid)
    status["status_path"] = os.fspath(MONITOR_STATUS_PATH)
    status["script"] = os.fspath(MONITOR_SCRIPT)
    return status


def _stop_monitor(pid: int) -> None:
    if pid <= 0:
        return
    try:
        import psutil

        proc = psutil.Process(pid)
        children = proc.children(recursive=True)
        for child in children:
            child.terminate()
        proc.terminate()
        psutil.wait_procs([*children, proc], timeout=3.0)
        for item in [*children, proc]:
            try:
                if item.is_running():
                    item.kill()
            except psutil.Error:
                pass
        return
    except Exception:
        pass
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def ensure_monitor_running(*, min_covered_until: datetime | None = None) -> dict[str, Any]:
    status = _load_monitor_status()
    if status.get("alive"):
        expires_at = _parse_time(status.get("expires_at"))
        if min_covered_until is None or (expires_at is not None and expires_at >= min_covered_until):
            status["started_now"] = False
            return status
        _stop_monitor(int(status.get("pid") or 0))

    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    duration_seconds = 24 * 60 * 60
    if min_covered_until is not None:
        duration_seconds = max(duration_seconds, int((min_covered_until - datetime.now()).total_seconds()) + 60)
    proc = popen_python_script_service(
        MONITOR_SCRIPT,
        "--loop",
        "--duration-seconds",
        str(duration_seconds),
        preferred_root=ROOT_DIR,
        executable=sys.executable,
        cwd=os.fspath(ROOT_DIR),
    )
    status = {
        "pid": int(proc.pid),
        "alive": True,
        "started_now": True,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status_path": os.fspath(MONITOR_STATUS_PATH),
        "script": os.fspath(MONITOR_SCRIPT),
    }
    return status


def _chain_text(event: dict[str, Any]) -> str:
    parts: list[str] = []
    for proc in event.get("chain") or []:
        if not isinstance(proc, dict):
            continue
        parts.append(str(proc.get("name") or ""))
        parts.extend(str(x) for x in (proc.get("cmdline") or []))
        parts.append(str(proc.get("cwd") or ""))
    for proc in event.get("children") or []:
        if not isinstance(proc, dict):
            continue
        parts.append(str(proc.get("name") or ""))
        parts.extend(str(x) for x in (proc.get("cmdline") or []))
    parts.append(str(event.get("title") or ""))
    return "\n".join(parts).lower().replace("\\", "/")


def is_codeyun_event(event: dict[str, Any], root_dir: Path = ROOT_DIR) -> bool:
    text = _chain_text(event)
    root = os.fspath(root_dir).lower().replace("\\", "/")
    if root not in text:
        return False
    markers = (
        "dev.py",
        "backend.app:app",
        "backend.core.runtime.uvicorn_hidden",
        "frontend/node_modules/vite/bin/vite.js",
        "scripts/codeyun_watchdog.py",
        "/.venv/scripts/python",
        " npm ",
        " npm.cmd",
        "net use",
    )
    return any(marker in text for marker in markers)


def _summarize_event(event: dict[str, Any]) -> dict[str, Any]:
    chain = []
    for proc in event.get("chain") or []:
        if not isinstance(proc, dict):
            continue
        chain.append(
            {
                "pid": proc.get("pid"),
                "name": proc.get("name"),
                "cmdline": proc.get("cmdline"),
                "cwd": proc.get("cwd"),
            }
        )
    return {
        "time": event.get("time"),
        "title": event.get("title"),
        "class": event.get("class"),
        "pid": event.get("pid"),
        "chain": chain,
    }


def audit_since(started_at: str, *, monitor_status: dict[str, Any] | None = None) -> dict[str, Any]:
    events = [event for event in _load_events() if _event_time(event) >= started_at]
    codeyun_events = [event for event in events if is_codeyun_event(event)]
    external_events = [event for event in events if not is_codeyun_event(event)]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = {
        "baseline_started_at": started_at,
        "checked_at": now,
        "coverage_valid": bool((monitor_status or _load_monitor_status()).get("alive")),
        "monitor": monitor_status or _load_monitor_status(),
        "total_events": len(events),
        "codeyun_events": len(codeyun_events),
        "external_events": len(external_events),
        "last_codeyun_events": [_summarize_event(event) for event in codeyun_events[-5:]],
        "last_external_events": [_summarize_event(event) for event in external_events[-5:]],
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def reset_baseline() -> dict[str, Any]:
    baseline_at = datetime.now()
    monitor_status = ensure_monitor_running(min_covered_until=baseline_at + timedelta(hours=24))
    started_at = baseline_at.strftime(TIME_FORMAT)
    baseline = {
        "started_at": started_at,
        "events_path": os.fspath(EVENTS_PATH),
        "status_path": os.fspath(STATUS_PATH),
        "monitor_status_path": os.fspath(MONITOR_STATUS_PATH),
        "root_dir": os.fspath(ROOT_DIR),
    }
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit_since(started_at, monitor_status=monitor_status)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit visible console popup events for CodeYun process chains.")
    parser.add_argument("--reset-baseline", action="store_true")
    parser.add_argument("--ensure-monitor", action="store_true")
    args = parser.parse_args()

    if args.reset_baseline:
        status = reset_baseline()
    else:
        monitor_status = ensure_monitor_running() if args.ensure_monitor else _load_monitor_status()
        baseline = _load_baseline()
        started_at = str(baseline.get("started_at") or "")
        if not started_at:
            status = reset_baseline()
        else:
            status = audit_since(started_at, monitor_status=monitor_status)
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
