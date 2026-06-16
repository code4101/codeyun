from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
MONITOR_DIR = Path(tempfile.gettempdir()) / "codeyun" / "visible-console-monitor"
EVENTS_PATH = MONITOR_DIR / "events_24h.jsonl"
BASELINE_PATH = MONITOR_DIR / "codeyun_popup_24h_baseline.json"
STATUS_PATH = MONITOR_DIR / "codeyun_popup_24h_status.json"


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


def audit_since(started_at: str) -> dict[str, Any]:
    events = [event for event in _load_events() if _event_time(event) >= started_at]
    codeyun_events = [event for event in events if is_codeyun_event(event)]
    external_events = [event for event in events if not is_codeyun_event(event)]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = {
        "baseline_started_at": started_at,
        "checked_at": now,
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
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    baseline = {
        "started_at": started_at,
        "events_path": os.fspath(EVENTS_PATH),
        "status_path": os.fspath(STATUS_PATH),
        "root_dir": os.fspath(ROOT_DIR),
    }
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit_since(started_at)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit visible console popup events for CodeYun process chains.")
    parser.add_argument("--reset-baseline", action="store_true")
    args = parser.parse_args()

    if args.reset_baseline:
        status = reset_baseline()
    else:
        baseline = _load_baseline()
        started_at = str(baseline.get("started_at") or "")
        if not started_at:
            status = reset_baseline()
        else:
            status = audit_since(started_at)
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
