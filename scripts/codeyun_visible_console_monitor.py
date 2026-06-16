from __future__ import annotations

import argparse
import ctypes
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ctypes import wintypes

try:
    import psutil
except ImportError:  # pragma: no cover - CodeYun runtime includes psutil.
    psutil = None


MONITOR_DIR = Path(tempfile.gettempdir()) / "codeyun" / "visible-console-monitor"
EVENTS_PATH = MONITOR_DIR / "events_24h.jsonl"
STATUS_PATH = MONITOR_DIR / "codeyun_visible_console_monitor_status.json"
DEFAULT_DURATION_SECONDS = 24 * 60 * 60.0
DEFAULT_INTERVAL_SECONDS = 0.08
DEFAULT_HEARTBEAT_SECONDS = 5.0
WINDOW_KEYWORDS = ("cmd", "powershell", "terminal", "console", "cascadia")


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_status() -> dict[str, Any]:
    status = _read_json(STATUS_PATH)
    pid = int(status.get("pid") or 0)
    status["alive"] = _pid_alive(pid)
    status["status_path"] = os.fspath(STATUS_PATH)
    status["events_path"] = os.fspath(EVENTS_PATH)
    return status


def _write_status(data: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _chain_for(pid: int) -> list[dict[str, Any]]:
    if psutil is None:
        return []
    chain: list[dict[str, Any]] = []
    try:
        current = psutil.Process(pid)
    except Exception:
        return chain
    for _ in range(12):
        try:
            chain.append(
                {
                    "pid": current.pid,
                    "name": current.name(),
                    "cmdline": current.cmdline(),
                    "cwd": current.cwd(),
                }
            )
            parent = current.parent()
            if parent is None:
                break
            current = parent
        except Exception:
            break
    return chain


def _children_for(pid: int) -> list[dict[str, Any]]:
    if psutil is None:
        return []
    try:
        return [
            {"pid": child.pid, "name": child.name(), "cmdline": child.cmdline()}
            for child in psutil.Process(pid).children(recursive=True)
        ]
    except Exception:
        return []


def _enum_visible_console_windows() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []

    user32 = ctypes.windll.user32
    enum_windows_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    items: list[dict[str, Any]] = []

    def window_text(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def window_class(hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value

    @enum_windows_proc
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = window_text(hwnd)
        class_name = window_class(hwnd)
        haystack = f"{title} {class_name}".lower()
        if not any(keyword in haystack for keyword in WINDOW_KEYWORDS):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_id = int(pid.value)
        items.append(
            {
                "hwnd": int(hwnd),
                "pid": process_id,
                "title": title,
                "class": class_name,
                "chain": _chain_for(process_id),
                "children": _children_for(process_id),
            }
        )
        return True

    user32.EnumWindows(callback, 0)
    return items


def run_loop(*, duration_seconds: float, interval_seconds: float, heartbeat_seconds: float) -> int:
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    start = time.time()
    deadline = start + max(1.0, float(duration_seconds))
    seen: set[tuple[int, int, str]] = set()
    next_heartbeat = 0.0
    base_status = {
        "pid": os.getpid(),
        "started_at": _now_text(),
        "duration_seconds": float(duration_seconds),
        "interval_seconds": float(interval_seconds),
        "events_path": os.fspath(EVENTS_PATH),
    }
    _write_status({**base_status, "heartbeat_at": _now_text(), "alive": True})

    while time.time() < deadline:
        for item in _enum_visible_console_windows():
            key = (int(item.get("hwnd") or 0), int(item.get("pid") or 0), str(item.get("title") or ""))
            if key in seen:
                continue
            seen.add(key)
            item["time"] = _now_text()
            with EVENTS_PATH.open("a", encoding="utf-8") as file:
                file.write(json.dumps(item, ensure_ascii=False) + "\n")

        now = time.time()
        if now >= next_heartbeat:
            _write_status(
                {
                    **base_status,
                    "heartbeat_at": _now_text(),
                    "expires_at": datetime.fromtimestamp(deadline).strftime("%Y-%m-%d %H:%M:%S"),
                    "alive": True,
                    "seen_windows": len(seen),
                }
            )
            next_heartbeat = now + max(1.0, float(heartbeat_seconds))
        time.sleep(max(0.01, float(interval_seconds)))

    _write_status(
        {
            **base_status,
            "finished_at": _now_text(),
            "alive": False,
            "seen_windows": len(seen),
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitor visible Windows console windows and record process chains.")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--duration-seconds", type=float, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(read_status(), ensure_ascii=False, indent=2))
        return 0
    if args.loop:
        return run_loop(
            duration_seconds=args.duration_seconds,
            interval_seconds=args.interval_seconds,
            heartbeat_seconds=args.heartbeat_seconds,
        )
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

