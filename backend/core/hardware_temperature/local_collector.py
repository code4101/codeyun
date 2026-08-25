from __future__ import annotations

import atexit
import ctypes
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


class LocalCollectorUnavailable(RuntimeError):
    pass


_COLLECTOR_DIR = Path(__file__).with_name("collector")
_COLLECTOR_EXE = _COLLECTOR_DIR / "bin" / "Release" / "net8.0-windows" / "codeyun-hardware-temperature-collector.exe"
_SNAPSHOT_PATH = Path(tempfile.gettempdir()) / f"codeyun-hardware-temperature-{os.getpid()}.json"
_ELEVATED_SNAPSHOT_PATH = Path(tempfile.gettempdir()) / f"codeyun-hardware-temperature-elevated-{os.getpid()}.json"
_PROCESS: subprocess.Popen[bytes] | None = None
_LOCK = threading.Lock()


def read_local_temperature_devices() -> dict[str, Any]:
    with _LOCK:
        elevated = _read_snapshot(_ELEVATED_SNAPSHOT_PATH, max_age_seconds=5)
        if elevated is not None:
            return elevated
        _ensure_running()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            payload = _read_snapshot(_SNAPSHOT_PATH)
            if payload is not None:
                return payload
            time.sleep(0.1)
        raise LocalCollectorUnavailable("本机温度采集器没有及时生成数据")


def request_elevated_collector() -> dict[str, Any]:
    if os.name != "nt" or not _COLLECTOR_EXE.exists():
        raise LocalCollectorUnavailable("本机温度采集器不可用")
    if _read_snapshot(_ELEVATED_SNAPSHOT_PATH, max_age_seconds=5) is not None:
        return {"status": "already_running", "message": "完整温度采集已经运行"}
    parameters = subprocess.list2cmdline(
        [
            "--output",
            str(_ELEVATED_SNAPSHOT_PATH),
            "--interval-ms",
            "1000",
            "--parent-pid",
            str(os.getpid()),
        ]
    )
    result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
        None,
        "runas",
        str(_COLLECTOR_EXE),
        parameters,
        str(_COLLECTOR_EXE.parent),
        0,
    )
    if result <= 32:
        raise LocalCollectorUnavailable("管理员采集器未启动")
    return {"status": "started", "message": "请在 Windows 提示中允许运行"}


def _read_snapshot(path: Path, *, max_age_seconds: float | None = None) -> dict[str, Any] | None:
    try:
        if max_age_seconds is not None and time.time() - path.stat().st_mtime > max_age_seconds:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) and isinstance(payload.get("devices"), list) else None


def _ensure_running() -> None:
    global _PROCESS
    if os.name != "nt":
        raise LocalCollectorUnavailable("本机温度采集器仅支持 Windows")
    if not _COLLECTOR_EXE.exists():
        raise LocalCollectorUnavailable("本机温度采集器尚未构建")
    if _PROCESS is not None and _PROCESS.poll() is None:
        return
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _PROCESS = subprocess.Popen(
        [
            str(_COLLECTOR_EXE),
            "--output",
            str(_SNAPSHOT_PATH),
            "--interval-ms",
            "1000",
            "--parent-pid",
            str(os.getpid()),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def _stop() -> None:
    global _PROCESS
    if _PROCESS is not None and _PROCESS.poll() is None:
        _PROCESS.terminate()
        try:
            _PROCESS.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _PROCESS.kill()
    _PROCESS = None


atexit.register(_stop)
