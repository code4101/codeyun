from __future__ import annotations

import json
import hashlib
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import base64
import tempfile
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from pyxllib.autogui.anlib import ImageTools
from pyxllib.cv.rgbfmt import (
    compare_bgr_pixel_tolerance,
    normalize_for_saved_jpeg_match,
    to_bgr_frame,
)

from backend.core.settings import ROOT_DIR, get_settings
from backend.core.runtime.process_launcher import popen_service, run_quiet
from backend.core.fanxiu.data_annotation.storage import resolve_data_annotation_image_asset
from backend.core.fanxiu.runtime.android_proxy import fanxiu_android_proxy_service
from backend.core.ocr.preview import OcrPreviewError, run_paddle_ocr_preview
from backend.core.devices.window_capture_preview import (
    WindowCapture,
    WindowCandidate,
    activate_window,
    click_window_title_bar,
    click_window_raw_point,
    drag_window_raw_points,
    ensure_windows_runtime,
    find_window,
    iter_mjpeg_frames,
    map_processed_point_to_raw_point,
    normalize_rotate,
    parse_crop,
    process_frame,
    set_dpi_awareness,
)


PROCESS_ENV_MARKER = "CODEYUN_FANXIU_MUMU_CONTROL"
PROCESS_ENV_VALUE = "1"
PREVIEW_MODULE = "backend.core.devices.window_capture_preview"
DEFAULT_TARGET_TITLE = "MuMu"
DEFAULT_PREVIEW_TITLE = "codeyun-mumu-window"
DEFAULT_FPS = "15"
DEFAULT_CAPTURE_MODE = "auto"
DEFAULT_CROP = "0,60,4,4"
DEFAULT_TRIM_BORDER = "0,0,0,0"
DEFAULT_ROTATE = "0"
DEFAULT_FIXED_WIDTH = "900"
DEFAULT_FIXED_HEIGHT = "1600"
DEFAULT_FIXED_DPI = "320"
DEFAULT_MUMU_MAIN_WIDTH_AT_150_DPI = 607
DEFAULT_MUMU_MAIN_HEIGHT_AT_150_DPI = 1111
# MuMu main window outer rect on codepc_mf, calibrated by hand for comfortable
# desktop viewing. Keep this as a fixed xywh; do not derive it from DPI.
DEFAULT_MUMU_MAIN_WINDOW_RECT = (2927, 0, 910, 1666)
MUMU_MAIN_WINDOW_RECT_ENV = "CODEYUN_FANXIU_MUMU_WINDOW_RECT"
SCREENSHOT_FRAME_DIRNAME = "截图"
MATCH_FRAME_DIRNAME = "匹配"
BURST_FRAME_DIRNAME = "连拍缓存"
DEFAULT_MATCH_FRAME_MAX_FILES = 1000
MATCH_FRAME_MAX_FILES_ENV = "FX_MATCH_FRAME_MAX_FILES"
_SCREENSHOT_FRAME_LOCK = threading.Lock()
_MATCH_FRAME_LOCK = threading.Lock()
_BURST_FRAME_LOCK = threading.Lock()
_LATEST_FRAME_LOCK = threading.Lock()
_MUMU_ADB_SESSION_LOCK = threading.Lock()
_LATEST_FRAME_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}
_MUMU_ADB_SESSION: dict[str, Any] = {}
_LATEST_FRAME_MAX_AGE_SECONDS = 3.0
_SCREENSHOT_FRAME_NAME_PATTERN = re.compile(r"^(\d+)\.(?:jpe?g|png)$", re.IGNORECASE)
_SCREENSHOT_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
MUMU_ADB_PORTS = (7555, 16416, 5555)
MUMU_ADB_SERIAL_ENV_KEYS = ("FANXIU_MUMU_ADB_SERIAL",)
MUMU_ADB_ALLOW_PROXY_DEVICES_ENV = "FANXIU_MUMU_ADB_ALLOW_PROXY_DEVICES"
MUMU_ADB_PORT_PROBE_TIMEOUT_ENV = "FANXIU_MUMU_ADB_PORT_PROBE_TIMEOUT"
MUMU_DEVICE_HEALTH_CHECK_INTERVAL_ENV = "FANXIU_MUMU_DEVICE_HEALTH_CHECK_INTERVAL"
MUMU_DEVICE_AUTO_RECOVERY_ENV = "FANXIU_MUMU_DEVICE_AUTO_RECOVERY"
FANXIU_ANDROID_PACKAGE = "com.frxxcrjpwssc3.ggws"
_MUMU_ADB_FAILURE_CACHE_TTL = 3.0
_MUMU_ADB_FAILURE_CACHE_LOCK = threading.Lock()
_MUMU_ADB_RECOVERY_LOCK = threading.Lock()
_MUMU_DEVICE_HEALTH_LOCK = threading.Lock()
_mumu_adb_failure_cache: tuple[float, str] | None = None
_mumu_device_health_state: dict[str, Any] = {
    "status": "unknown",
    "checked_at": 0.0,
    "checked_monotonic": 0.0,
    "last_error": "",
    "failure_count": 0,
    "recovery_count": 0,
    "last_recovery_at": 0.0,
    "last_recovery_reason": "",
    "vmindex": "1",
}


def _mumu_device_recovery_state_path() -> Path:
    return Path(tempfile.gettempdir()) / "codeyun" / "fanxiu_mumu_device_health" / "recovery_state.json"


def _mumu_device_health_log_dir() -> Path:
    path = get_settings().data_dir / "fanxiu" / "mumu-device-health"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _mumu_device_health_log_path(now: float | None = None) -> Path:
    stamp = datetime.fromtimestamp(now or time.time()).strftime("%Y%m%d")
    return _mumu_device_health_log_dir() / f"device-health-{stamp}.jsonl"


def _compact_for_log(value: Any, *, max_chars: int = 1000) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "...[truncated]"
    if isinstance(value, dict):
        return {str(key): _compact_for_log(item, max_chars=max_chars) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact_for_log(item, max_chars=max_chars) for item in value[:50]]
    return value


def _collect_windows_commit_snapshot() -> dict[str, Any]:
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


def _windows_services_for_pid_from_sc(pid: int) -> list[str]:
    if os.name != "nt" or int(pid or 0) <= 0:
        return []
    try:
        result = run_quiet(
            ["sc.exe", "queryex", "type=", "service", "state=", "all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    services: list[str] = []
    service_name = ""
    for line in result.stdout.splitlines():
        service_match = re.match(r"^\s*SERVICE_NAME:\s*(.+?)\s*$", line)
        if service_match:
            service_name = service_match.group(1).strip()
            continue
        pid_match = re.match(r"^\s*PID\s*:\s*(\d+)\s*$", line)
        if pid_match and pid_match.group(1) == str(int(pid)) and service_name:
            services.append(service_name)
    return sorted(set(services))


def _windows_services_for_pid(pid: int) -> list[str]:
    return _windows_services_for_pid_from_sc(pid)


def _annotate_windows_service_hosts(items: list[dict[str, Any]]) -> None:
    if os.name != "nt":
        return
    for item in items:
        if str(item.get("name") or "").lower() != "svchost.exe":
            continue
        services = _windows_services_for_pid(int(item.get("pid") or 0))
        if services:
            item["services"] = services


def _host_resource_pressure_hints(snapshot: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    commit = snapshot.get("commit")
    if isinstance(commit, dict):
        commit_percent = float(commit.get("commit_percent") or 0.0)
        commit_available_mb = int(commit.get("commit_available_mb") or 0)
        if commit_percent >= 95 or commit_available_mb < 4096:
            hints.append("windows_commit_nearly_exhausted")
        elif commit_percent >= 90 or commit_available_mb < 8192:
            hints.append("windows_commit_pressure")
    for item in snapshot.get("top_private_processes") or []:
        if not isinstance(item, dict):
            continue
        private_mb = int(item.get("private_mb") or 0)
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


def _collect_mumu_host_resource_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    commit = _collect_windows_commit_snapshot()
    if commit:
        snapshot["commit"] = commit
    try:
        vm = psutil.virtual_memory()
        snapshot["memory"] = {
            "total_mb": int(vm.total / 1024 / 1024),
            "available_mb": int(vm.available / 1024 / 1024),
            "used_mb": int(vm.used / 1024 / 1024),
            "percent": float(vm.percent),
        }
    except Exception as exc:
        snapshot["memory_error"] = str(exc)
    try:
        swap = psutil.swap_memory()
        snapshot["swap"] = {
            "total_mb": int(swap.total / 1024 / 1024),
            "used_mb": int(swap.used / 1024 / 1024),
            "free_mb": int(swap.free / 1024 / 1024),
            "percent": float(swap.percent),
        }
    except Exception as exc:
        snapshot["swap_error"] = str(exc)

    top: list[dict[str, Any]] = []
    mumu: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            info = proc.info
            name = str(info.get("name") or "")
            try:
                mem = proc.memory_full_info()
            except (psutil.AccessDenied, OSError):
                mem = proc.memory_info()
            item = {
                "pid": int(info.get("pid") or 0),
                "name": name,
                "rss_mb": int(getattr(mem, "rss", 0) / 1024 / 1024),
                "vms_mb": int(getattr(mem, "vms", 0) / 1024 / 1024),
                "private_mb": int(getattr(mem, "private", getattr(mem, "uss", 0)) / 1024 / 1024),
                "started_at": datetime.fromtimestamp(float(info.get("create_time") or 0)).isoformat(timespec="seconds"),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
            continue
        top.append(item)
        if any(marker in name.lower() for marker in ("mumu", "nemu", "taptap")):
            mumu.append(item)
    top_private = sorted(top, key=lambda item: int(item.get("private_mb") or 0), reverse=True)[:10]
    _annotate_windows_service_hosts(top_private)
    snapshot["top_private_processes"] = top_private
    snapshot["mumu_processes"] = sorted(mumu, key=lambda item: int(item.get("private_mb") or 0), reverse=True)
    hints = _host_resource_pressure_hints(snapshot)
    if hints:
        snapshot["pressure_hints"] = hints
    return snapshot


def _mumu_vm_root() -> Path:
    return Path(r"D:\TapTap\Support\android_emulator\engine\vms\EGTapTap-12.0-1")


def _tail_text_lines(path: Path, *, max_bytes: int = 120_000, max_lines: int = 80) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if not data:
        return []
    text = data[-max_bytes:].decode("utf-8", errors="replace")
    return [line for line in text.splitlines() if line][-max_lines:]


def _collect_mumu_native_diagnostics() -> dict[str, Any]:
    root = _mumu_vm_root()
    candidates = [
        root / "logs" / "shell.log",
        root / "logs" / "vm_vboxmanager.log",
        root / "data" / "exportLogs" / "vm_shell.log",
        root / "data" / "exportLogs" / "vm_vboxmanager.log",
    ]
    markers = (
        "GRAPHIC_CRASH",
        "VERR_",
        "showRuntimeError",
        "onPlayerHeadlessCrash",
        "Android error",
        "handleStartupError",
        "RuntimeError",
        "crash",
        "fatal",
        "error",
    )
    marker_lines: list[dict[str, str]] = []
    tails: dict[str, list[str]] = {}
    for path in candidates:
        lines = _tail_text_lines(path)
        if not lines:
            continue
        key = os.fspath(path.relative_to(root)) if path.is_relative_to(root) else os.fspath(path)
        tails[key] = lines[-25:]
        for line in lines:
            if any(marker.lower() in line.lower() for marker in markers):
                marker_lines.append({"file": key, "line": line})
    marker_text = "\n".join(item["line"] for item in marker_lines[-40:]).lower()
    suspected: list[str] = []
    if "graphic_crash" in marker_text or "renderer" in marker_text:
        suspected.append("renderer_or_graphic_crash")
    if "verr_need_no_admin" in marker_text or "verr_ole" in marker_text or "vbox" in marker_text:
        suspected.append("virtualbox_or_hypervisor_error")
    if "resource-exhaustion" in marker_text or "memory" in marker_text:
        suspected.append("host_memory_pressure")
    if "timed out" in marker_text or "connection refused" in marker_text:
        suspected.append("adb_or_rpc_timeout")
    return {
        "root": os.fspath(root),
        "suspected_causes": suspected,
        "marker_lines": marker_lines[-40:],
        "tails": tails,
    }


def _append_mumu_device_health_event(
    event: str,
    payload: dict[str, Any] | None = None,
    *,
    include_resources: bool = False,
    include_native: bool = False,
) -> None:
    now = time.time()
    item: dict[str, Any] = {
        "time": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
        "event": str(event or "unknown"),
    }
    if payload:
        item.update(_compact_for_log(payload))
    if include_resources:
        item["host_resources"] = _collect_mumu_host_resource_snapshot()
    if include_native:
        item["mumu_native"] = _compact_for_log(_collect_mumu_native_diagnostics(), max_chars=2000)
    try:
        path = _mumu_device_health_log_path(now)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _read_mumu_device_recovery_state() -> dict[str, Any]:
    path = _mumu_device_recovery_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_mumu_device_recovery_state(state: dict[str, Any]) -> None:
    path = _mumu_device_recovery_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_recovery_at": float(state.get("last_recovery_at") or 0.0),
        "last_recovery_reason": str(state.get("last_recovery_reason") or ""),
        "vmindex": str(state.get("vmindex") or "1"),
        "updated_at": time.time(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_mumu_adb_unavailable_error(message: str) -> bool:
    normalized = message.lower()
    return any(
        marker in normalized
        for marker in (
            "not found",
            "cannot connect",
            "connection refused",
            "actively refused",
            "winerror 10061",
            "winerror 10054",
            "目标计算机积极拒绝",
            "远程主机强迫关闭",
            "no connection could be made",
            "failed to connect",
            "timed out",
            "adb 端口不可用",
            "securityexception",
            "injecting to another application requires inject_events permission",
        )
    )


def _is_mumu_frame_unusable_error(message: str) -> bool:
    normalized = message.lower()
    return "mumu adb截图疑似黑屏" in normalized or "mumu adb screencap looks black" in normalized


def _mumu_adb_png_black_frame_summary(data: bytes) -> dict[str, Any]:
    try:
        from PIL import Image, ImageStat
        import io

        image = Image.open(io.BytesIO(data)).convert("RGB")
        stat = ImageStat.Stat(image)
        sample = image.resize((max(1, image.width // 16), max(1, image.height // 16)))
        pixels = list(sample.getdata())
        if not pixels:
            return {"black": False, "reason": "empty_sample"}
        near_dark = sum(1 for r, g, b in pixels if r < 24 and g < 24 and b < 24)
        unique_colors = len(set(pixels))
        near_dark_ratio = near_dark / len(pixels)
        mean = [float(value) for value in stat.mean]
        black = near_dark_ratio >= 0.995 and max(mean) < 12.0 and unique_colors <= 32
        return {
            "black": bool(black),
            "width": image.width,
            "height": image.height,
            "mean": [round(value, 3) for value in mean],
            "near_dark_ratio": round(near_dark_ratio, 6),
            "unique_sample_colors": unique_colors,
        }
    except Exception as exc:
        return {"black": False, "error": str(exc)}


def _get_mumu_adb_failure_cache() -> str | None:
    with _MUMU_ADB_FAILURE_CACHE_LOCK:
        if _mumu_adb_failure_cache is None:
            return None
        expires_at, message = _mumu_adb_failure_cache
        if time.monotonic() >= expires_at:
            return None
        return message


def _set_mumu_adb_failure_cache(message: str) -> None:
    global _mumu_adb_failure_cache
    if not _is_mumu_adb_unavailable_error(message):
        return
    with _MUMU_ADB_FAILURE_CACHE_LOCK:
        _mumu_adb_failure_cache = (time.monotonic() + _MUMU_ADB_FAILURE_CACHE_TTL, message)


def _clear_mumu_adb_failure_cache() -> None:
    global _mumu_adb_failure_cache
    with _MUMU_ADB_FAILURE_CACHE_LOCK:
        _mumu_adb_failure_cache = None


def _parse_adb_serial_host_port(serial: str) -> tuple[str, int] | None:
    host, sep, port_text = str(serial or "").strip().rpartition(":")
    if not sep or not host or not port_text.isdigit():
        return None
    return host, int(port_text)


def _dedupe_mumu_adb_serials(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        serial = str(value or "").strip()
        if not serial or serial in seen or _parse_adb_serial_host_port(serial) is None:
            continue
        seen.add(serial)
        result.append(serial)
    return result


def _is_truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_local_mumu_adb_serial(serial: str) -> bool:
    parsed = _parse_adb_serial_host_port(serial)
    if parsed is None:
        return False
    host, port = parsed
    return host in {"127.0.0.1", "localhost", "::1"} and port in MUMU_ADB_PORTS


def _mumu_adb_proxy_devices_allowed() -> bool:
    return _is_truthy_env(os.environ.get(MUMU_ADB_ALLOW_PROXY_DEVICES_ENV))


def _mumu_adb_port_probe_timeout(default: float = 0.75) -> float:
    try:
        return max(0.15, min(3.0, float(os.environ.get(MUMU_ADB_PORT_PROBE_TIMEOUT_ENV) or default)))
    except (TypeError, ValueError):
        return default


def _mumu_device_health_check_interval(default: float = 60.0) -> float:
    try:
        return max(10.0, min(600.0, float(os.environ.get(MUMU_DEVICE_HEALTH_CHECK_INTERVAL_ENV) or default)))
    except (TypeError, ValueError):
        return default


def _mumu_device_auto_recovery_enabled() -> bool:
    value = os.environ.get(MUMU_DEVICE_AUTO_RECOVERY_ENV)
    if value is None:
        return True
    return _is_truthy_env(value)


def _mumu_manager_path() -> Path | None:
    try:
        adb_path = fanxiu_android_proxy_service.adb_path()
    except Exception:
        return None
    candidates = [
        adb_path.parents[3] / "nx_main" / "MuMuManager.exe" if len(adb_path.parents) >= 4 else None,
        adb_path.parents[2] / "nx_main" / "MuMuManager.exe" if len(adb_path.parents) >= 3 else None,
        Path(r"D:\TapTap\Support\android_emulator\engine\nx_main\MuMuManager.exe"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.exists() and candidate.is_file():
            return candidate
    return None


def _mumu_manager_adb_serial_candidates() -> list[str]:
    manager_path = _mumu_manager_path()
    if manager_path is None:
        return []
    try:
        process = run_quiet(
            [str(manager_path), "info", "--vmindex", "all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except Exception:
        return []
    if process.returncode != 0 or not process.stdout.strip():
        return []
    try:
        info = json.loads(process.stdout)
    except Exception:
        return []
    candidates: list[str] = []
    players = info.values() if isinstance(info, dict) else []
    for player in players:
        if not isinstance(player, dict):
            continue
        if not bool(player.get("is_process_started")):
            continue
        host = str(player.get("adb_host_ip") or "").strip()
        port = player.get("adb_port")
        try:
            port_int = int(port)
        except (TypeError, ValueError):
            continue
        if host and port_int > 0:
            candidates.append(f"{host}:{port_int}")
    return _dedupe_mumu_adb_serials(candidates)


def _run_mumu_manager_json(args: list[str], *, timeout: float = 8.0) -> Any:
    manager_path = _mumu_manager_path()
    if manager_path is None:
        raise RuntimeError("未找到 MuMuManager.exe")
    process = run_quiet(
        [str(manager_path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    output = str(process.stdout or "").strip()
    if output:
        try:
            return json.loads(output)
        except Exception as exc:
            if process.returncode != 0:
                detail = "\n".join(
                    part.strip() for part in (process.stdout or "", process.stderr or "") if part and part.strip()
                )
                raise RuntimeError(detail or f"MuMuManager 退出码 {process.returncode}") from exc
            raise RuntimeError(f"MuMuManager 输出不是 JSON：{output[:200]}") from exc
    if process.returncode != 0:
        detail = "\n".join(part.strip() for part in (process.stdout or "", process.stderr or "") if part and part.strip())
        raise RuntimeError(detail or f"MuMuManager 退出码 {process.returncode}")
    if not output:
        return {}


def _mumu_manager_player_info(vmindex: str = "1") -> dict[str, Any]:
    payload = _run_mumu_manager_json(["info", "--vmindex", str(vmindex or "1")], timeout=8)
    if isinstance(payload, dict):
        if str(payload.get("index") or "") == str(vmindex or "1") or "is_android_started" in payload:
            return payload
        item = payload.get(str(vmindex or "1"))
        if isinstance(item, dict):
            return item
    raise RuntimeError(f"MuMuManager 未返回实例 {vmindex} 状态")


def _mumu_manager_control(vmindex: str, command: str, *, timeout: float = 12.0) -> dict[str, Any]:
    payload = _run_mumu_manager_json(["control", "--vmindex", str(vmindex or "1"), command], timeout=timeout)
    return payload if isinstance(payload, dict) else {}


def _mumu_manager_launch_app(vmindex: str, package: str = FANXIU_ANDROID_PACKAGE) -> dict[str, Any]:
    payload = _run_mumu_manager_json(
        ["control", "--vmindex", str(vmindex or "1"), "app", "launch", "--package", str(package or FANXIU_ANDROID_PACKAGE)],
        timeout=12,
    )
    return payload if isinstance(payload, dict) else {}


def _mumu_window_extended_rect(hwnd: int, win32gui: Any) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd,
            9,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr == 0:
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return tuple(int(part) for part in win32gui.GetWindowRect(hwnd))


def _mumu_window_dpi(hwnd: int) -> int:
    try:
        return int(ctypes.windll.user32.GetDpiForWindow(hwnd))
    except Exception:
        return 96


def _mumu_window_info(hwnd: int, win32gui: Any) -> dict[str, Any]:
    window_rect = tuple(int(part) for part in win32gui.GetWindowRect(hwnd))
    extended_rect = _mumu_window_extended_rect(hwnd, win32gui)
    client_rect = win32gui.GetClientRect(hwnd)
    client_top_left = win32gui.ClientToScreen(hwnd, (client_rect[0], client_rect[1]))
    client_bottom_right = win32gui.ClientToScreen(hwnd, (client_rect[2], client_rect[3]))
    dpi = _mumu_window_dpi(hwnd)
    return {
        "hwnd": int(hwnd),
        "title": win32gui.GetWindowText(hwnd).strip(),
        "class": win32gui.GetClassName(hwnd),
        "window_rect": list(window_rect),
        "window_size_logical": [window_rect[2] - window_rect[0], window_rect[3] - window_rect[1]],
        "extended_rect_physical": list(extended_rect),
        "extended_size_physical": [extended_rect[2] - extended_rect[0], extended_rect[3] - extended_rect[1]],
        "client_screen_rect_logical": [
            int(client_top_left[0]),
            int(client_top_left[1]),
            int(client_bottom_right[0]),
            int(client_bottom_right[1]),
        ],
        "client_size_logical": [
            int(client_bottom_right[0]) - int(client_top_left[0]),
            int(client_bottom_right[1]) - int(client_top_left[1]),
        ],
        "dpi": dpi,
        "scale": round(dpi / 96, 4),
    }


def _iter_mumu_desktop_windows() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    import win32gui

    items: list[dict[str, Any]] = []

    def callback(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd).strip()
        if "凡人修仙传" not in title and "MuMu" not in title:
            return True
        items.append(_mumu_window_info(hwnd, win32gui))
        return True

    win32gui.EnumWindows(callback, None)
    items.sort(
        key=lambda item: (
            0 if "凡人修仙传" in item["title"] else 1,
            0 if str(item["class"]).endswith("WindowIcon") else 1,
            -int(item["window_size_logical"][0]) * int(item["window_size_logical"][1]),
            item["title"],
        )
    )
    return items


def _find_mumu_desktop_main_window() -> dict[str, Any]:
    windows = _iter_mumu_desktop_windows()
    for item in windows:
        if "凡人修仙传" in item["title"] and str(item["class"]).endswith("WindowIcon"):
            return item
    for item in windows:
        if "凡人修仙传" in item["title"]:
            return item
    for item in windows:
        if str(item["class"]).endswith("WindowIcon"):
            return item
    raise RuntimeError("未找到凡修 MuMu 主窗口")


def _find_mumu_window_candidate(normalized_title: str, title_match: str) -> tuple[WindowCandidate, str, str]:
    """Find the actual MuMu game window instead of any window mentioning MuMu."""

    if str(normalized_title or "").strip().lower() == DEFAULT_TARGET_TITLE.lower() and title_match != "exact":
        info = _find_mumu_desktop_main_window()
        rect = info.get("extended_rect_physical") or info.get("window_rect") or [0, 0, 0, 0]
        target = WindowCandidate(
            hwnd=int(info.get("hwnd") or 0),
            title=str(info.get("title") or DEFAULT_TARGET_TITLE),
            class_name=str(info.get("class") or ""),
            rect=tuple(int(part) for part in rect),
        )
        return target, target.title, "exact"
    target = find_window(normalized_title, title_match)
    return target, normalized_title, title_match


def _target_mumu_main_window_size(hwnd: int, current_info: dict[str, Any] | None = None) -> tuple[int, int, str]:
    dpi = _mumu_window_dpi(hwnd)
    scale = dpi / 96 if dpi > 0 else 1.0
    coordinate_scale = 1.0
    coordinate_mode = "logical"
    if current_info:
        window_size = current_info.get("window_size_logical") or [0, 0]
        extended_size = current_info.get("extended_size_physical") or [0, 0]
        try:
            window_width = int(window_size[0])
            extended_width = int(extended_size[0])
        except (TypeError, ValueError, IndexError):
            window_width = 0
            extended_width = 0
        if scale > 1.01 and extended_width > 0 and abs(window_width - extended_width) <= 20:
            coordinate_scale = scale
            coordinate_mode = "physical"
    if abs(scale - 1.5) < 0.05:
        return (
            int(round(DEFAULT_MUMU_MAIN_WIDTH_AT_150_DPI * coordinate_scale)),
            int(round(DEFAULT_MUMU_MAIN_HEIGHT_AT_150_DPI * coordinate_scale)),
            coordinate_mode,
        )
    render_width = int(round(int(DEFAULT_FIXED_WIDTH) / scale))
    render_height = int(round(int(DEFAULT_FIXED_HEIGHT) / scale))
    return (
        int(round((render_width + 7) * coordinate_scale)),
        int(round((render_height + 44) * coordinate_scale)),
        coordinate_mode,
    )


def _target_mumu_main_window_rect() -> tuple[int, int, int, int]:
    configured = str(os.environ.get(MUMU_MAIN_WINDOW_RECT_ENV) or "").strip()
    if configured:
        parts = [part for part in re.split(r"[\s,;]+", configured) if part]
        if len(parts) == 4:
            try:
                left, top, width, height = (int(part) for part in parts)
                if width > 0 and height > 0:
                    return left, top, width, height
            except ValueError:
                pass
    return DEFAULT_MUMU_MAIN_WINDOW_RECT


def normalize_mumu_desktop_window_size(*, apply: bool = False, timeout_s: float = 0.0) -> dict[str, Any]:
    deadline = time.monotonic() + max(float(timeout_s or 0.0), 0.0)
    while True:
        try:
            import win32con
            import win32gui

            before = _find_mumu_desktop_main_window()
            initial = before
            hwnd = int(before["hwnd"])
            target_left, target_top, target_width, target_height = _target_mumu_main_window_rect()

            def is_target(info: dict[str, Any]) -> bool:
                current_left, current_top, _current_right, _current_bottom = info["window_rect"]
                current_width, current_height = info["window_size_logical"]
                return (
                    abs(int(current_left) - target_left) <= 1
                    and abs(int(current_top) - target_top) <= 1
                    and abs(int(current_width) - target_width) <= 1
                    and abs(int(current_height) - target_height) <= 1
                )

            already_target = is_target(before)
            result: dict[str, Any] = {
                "ok": True,
                "target_window_rect": [target_left, target_top, target_left + target_width, target_top + target_height],
                "target_main_size": [target_width, target_height],
                "setpos_size": [target_width, target_height],
                "target_main_size_logical_at_150_dpi": [
                    DEFAULT_MUMU_MAIN_WIDTH_AT_150_DPI,
                    DEFAULT_MUMU_MAIN_HEIGHT_AT_150_DPI,
                ],
                "target_render_size_physical": [int(DEFAULT_FIXED_WIDTH), int(DEFAULT_FIXED_HEIGHT)],
                "coordinate_mode": "window_rect",
                "before": initial,
                "already_target": already_target,
                "applied": False,
            }
            if apply:
                attempts: list[dict[str, Any]] = []
                for _ in range(2):
                    if is_target(before):
                        break
                    win32gui.SetWindowPos(
                        hwnd,
                        None,
                        int(target_left),
                        int(target_top),
                        int(target_width),
                        int(target_height),
                        win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
                    )
                    time.sleep(0.2)
                    result["applied"] = True
                    before = _find_mumu_desktop_main_window()
                    hwnd = int(before["hwnd"])
                    attempts.append({
                        "after": before,
                        "next_setpos_size": [target_width, target_height],
                    })
                if attempts:
                    result["attempts"] = attempts
                result["after"] = before
                result["already_target"] = is_target(before)
            return result
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1.0)


def _mumu_device_health_status_from_info(info: dict[str, Any]) -> str:
    if not bool(info.get("is_process_started")):
        return "stopped"
    if not bool(info.get("is_android_started")):
        return "broken"
    if str(info.get("player_state") or "") == "start_finished":
        return "healthy"
    return "suspect"


def _clone_mumu_device_health_state() -> dict[str, Any]:
    with _MUMU_DEVICE_HEALTH_LOCK:
        return json.loads(json.dumps(_mumu_device_health_state, ensure_ascii=False, default=str))


def reset_mumu_device_health_state() -> None:
    with _MUMU_DEVICE_HEALTH_LOCK:
        _mumu_device_health_state.clear()
        _mumu_device_health_state.update({
            "status": "unknown",
            "checked_at": 0.0,
            "checked_monotonic": 0.0,
            "last_error": "",
            "failure_count": 0,
            "recovery_count": 0,
            "last_recovery_at": 0.0,
            "last_recovery_reason": "",
            "vmindex": "1",
        })


def mumu_device_health_check(*, vmindex: str = "1", force: bool = False) -> dict[str, Any]:
    now_mono = time.monotonic()
    with _MUMU_DEVICE_HEALTH_LOCK:
        cached = dict(_mumu_device_health_state)
        if (
            not force
            and cached.get("status") != "unknown"
            and now_mono - float(cached.get("checked_monotonic") or 0.0) < _mumu_device_health_check_interval()
        ):
            return json.loads(json.dumps(cached, ensure_ascii=False, default=str))

    try:
        info = _mumu_manager_player_info(str(vmindex or "1"))
        status = _mumu_device_health_status_from_info(info)
        error = ""
    except Exception as exc:
        info = {}
        status = "suspect"
        error = str(exc)

    with _MUMU_DEVICE_HEALTH_LOCK:
        if status == "healthy":
            _mumu_device_health_state["failure_count"] = 0
            _clear_mumu_adb_failure_cache()
        _mumu_device_health_state.update({
            "status": status,
            "checked_at": time.time(),
            "checked_monotonic": now_mono,
            "last_error": error,
            "vmindex": str(vmindex or "1"),
            "info": info,
        })
        state = json.loads(json.dumps(_mumu_device_health_state, ensure_ascii=False, default=str))
    previous_status = str(cached.get("status") or "unknown")
    if force or status != previous_status or status != "healthy":
        _append_mumu_device_health_event(
            "health_check",
            {
                "vmindex": str(vmindex or "1"),
                "status": status,
                "previous_status": previous_status,
                "force": bool(force),
                "error": error,
                "info": info,
            },
            include_resources=status != "healthy",
            include_native=status != "healthy",
        )
    return state


def _mumu_device_recovery_cooldown_seconds(status: str, *, default: float = 600.0) -> float:
    return 45.0 if status in {"stopped", "broken"} else default


def _mumu_device_recovery_cooling_down(now: float, *, status: str = "unknown", cooldown_seconds: float | None = None) -> bool:
    resolved_cooldown = (
        float(cooldown_seconds)
        if cooldown_seconds is not None
        else _mumu_device_recovery_cooldown_seconds(str(status or "unknown"))
    )
    with _MUMU_DEVICE_HEALTH_LOCK:
        last_recovery_at = float(_mumu_device_health_state.get("last_recovery_at") or 0.0)
    persisted = _read_mumu_device_recovery_state()
    try:
        last_recovery_at = max(last_recovery_at, float(persisted.get("last_recovery_at") or 0.0))
    except (TypeError, ValueError):
        pass
    return last_recovery_at > 0 and now - last_recovery_at < resolved_cooldown


def recover_mumu_device(*, vmindex: str = "1", reason: str = "device_health", force_restart: bool = False) -> dict[str, Any]:
    if not _mumu_device_auto_recovery_enabled():
        state = mumu_device_health_check(vmindex=vmindex, force=True)
        state["recovered"] = False
        state["recovery_skipped"] = "auto_recovery_disabled"
        _append_mumu_device_health_event(
            "recovery_skipped",
            {"reason": reason, "skip": "auto_recovery_disabled", "state": state},
            include_resources=True,
            include_native=True,
        )
        return state

    with _MUMU_ADB_RECOVERY_LOCK:
        now = time.time()
        _close_mumu_adb_session()
        try:
            before = mumu_device_health_check(vmindex=vmindex, force=True)
            if before.get("status") in {"healthy"} and not force_restart:
                before["recovered"] = False
                before["recovery_skipped"] = "already_healthy"
                _append_mumu_device_health_event(
                    "recovery_skipped",
                    {"reason": reason, "skip": "already_healthy", "state": before},
                    include_resources=False,
                    include_native=False,
                )
                return before
            before_status = str(before.get("status") or "unknown")
            if not force_restart and _mumu_device_recovery_cooling_down(now, status=before_status):
                state = _clone_mumu_device_health_state()
                state["recovered"] = False
                state["recovery_skipped"] = "cooldown"
                state["recovery_cooldown_seconds"] = _mumu_device_recovery_cooldown_seconds(before_status)
                _append_mumu_device_health_event(
                    "recovery_skipped",
                    {"reason": reason, "skip": "cooldown", "state": state},
                    include_resources=True,
                    include_native=True,
                )
                return state
            with _MUMU_DEVICE_HEALTH_LOCK:
                _mumu_device_health_state.update({
                    "status": "recovering",
                    "last_recovery_at": now,
                    "last_recovery_reason": str(reason or "device_health"),
                })
                _write_mumu_device_recovery_state(_mumu_device_health_state)
            _append_mumu_device_health_event(
                "recovery_start",
                {"reason": reason, "force_restart": bool(force_restart), "before": before},
                include_resources=True,
                include_native=True,
            )
            if bool((before.get("info") or {}).get("is_process_started")):
                _mumu_manager_control(str(vmindex or "1"), "shutdown", timeout=15)
                time.sleep(5)
            _mumu_manager_control(str(vmindex or "1"), "launch", timeout=15)
            deadline = time.monotonic() + 90.0
            state: dict[str, Any] = {}
            while time.monotonic() < deadline:
                time.sleep(5)
                state = mumu_device_health_check(vmindex=vmindex, force=True)
                if state.get("status") == "healthy":
                    break
            if state.get("status") != "healthy":
                raise RuntimeError(f"MuMu 恢复后仍未启动 Android：{state.get('status')}")
            adb_online = wait_mumu_adb_online(vmindex=str(vmindex or "1"), timeout_s=45)
            try:
                resolution_result = ensure_mumu_adb_resolution(vmindex=str(vmindex or "1"))
            except Exception as exc:
                resolution_result = {"ok": False, "error": str(exc)}
                raise RuntimeError(f"MuMu 恢复后分辨率未对齐 900x1600：{exc}") from exc
            try:
                app_result = _mumu_manager_launch_app(str(vmindex or "1"), FANXIU_ANDROID_PACKAGE)
            except Exception as exc:
                app_result = {"errcode": -1, "errmsg": str(exc)}
            try:
                window_size_result = normalize_mumu_desktop_window_size(apply=True, timeout_s=20.0)
            except Exception as exc:
                window_size_result = {"ok": False, "error": str(exc)}
            with _MUMU_DEVICE_HEALTH_LOCK:
                _mumu_device_health_state["failure_count"] = 0
                _mumu_device_health_state["recovery_count"] = int(_mumu_device_health_state.get("recovery_count") or 0) + 1
                _mumu_device_health_state["app_launch"] = app_result
                _mumu_device_health_state["adb_online"] = adb_online
                _mumu_device_health_state["resolution"] = resolution_result
                _mumu_device_health_state["window_size"] = window_size_result
            _clear_mumu_adb_failure_cache()
            final_state = mumu_device_health_check(vmindex=vmindex, force=True)
            final_state["adb_online"] = adb_online
            final_state["resolution"] = resolution_result
            final_state["window_size"] = window_size_result
            final_state["recovered"] = True
            try:
                from backend.core.fanxiu.runtime.capture_runtime import (
                    FANXIU_CAPTURE_RUNTIME_MUMU_RECOVERY_REASON,
                    ensure_fanxiu_capture_runtime_backstop,
                )

                final_state["capture_runtime"] = ensure_fanxiu_capture_runtime_backstop(
                    FANXIU_CAPTURE_RUNTIME_MUMU_RECOVERY_REASON,
                )
            except Exception as exc:
                final_state["capture_runtime"] = {"ok": False, "ensured": False, "error": str(exc)}
            _append_mumu_device_health_event(
                "recovery_success",
                {"reason": reason, "state": final_state},
                include_resources=True,
                include_native=True,
            )
            return final_state
        except Exception as exc:
            with _MUMU_DEVICE_HEALTH_LOCK:
                _mumu_device_health_state.update({
                    "status": "broken",
                    "last_error": str(exc),
                    "checked_at": time.time(),
                    "checked_monotonic": time.monotonic(),
                })
            state = _clone_mumu_device_health_state()
            state["recovered"] = False
            _append_mumu_device_health_event(
                "recovery_failed",
                {"reason": reason, "error": str(exc), "state": state},
                include_resources=True,
                include_native=True,
            )
            return state


def record_mumu_adb_failure(error: Any, *, vmindex: str = "1", recover: bool = True) -> dict[str, Any]:
    message = str(error or "")
    now_mono = time.monotonic()
    with _MUMU_DEVICE_HEALTH_LOCK:
        _mumu_device_health_state["failure_count"] = int(_mumu_device_health_state.get("failure_count") or 0) + 1
        _mumu_device_health_state["last_error"] = message
        _mumu_device_health_state["status"] = "suspect"
        _mumu_device_health_state["checked_at"] = time.time()
        failure_count = int(_mumu_device_health_state.get("failure_count") or 0)
        checked_mono = float(_mumu_device_health_state.get("checked_monotonic") or 0.0)
    _append_mumu_device_health_event(
        "adb_failure",
        {"vmindex": vmindex, "failure_count": failure_count, "recover": bool(recover), "error": message},
        include_resources=failure_count >= 3 or _is_mumu_adb_unavailable_error(message),
        include_native=failure_count >= 3 or _is_mumu_adb_unavailable_error(message),
    )
    if failure_count < 3 and now_mono - checked_mono < _mumu_device_health_check_interval():
        return _clone_mumu_device_health_state()
    state = mumu_device_health_check(vmindex=vmindex, force=True)
    if recover and (
        state.get("status") in {"broken", "stopped"}
        or _is_mumu_adb_unavailable_error(message)
        or _is_mumu_frame_unusable_error(message)
    ):
        return recover_mumu_device(vmindex=vmindex, reason=f"adb_failure:{message[:80]}", force_restart=True)
    return state


def ensure_mumu_device_healthy(*, vmindex: str = "1", recover: bool = True, force: bool = False, reason: str = "heartbeat") -> dict[str, Any]:
    state = mumu_device_health_check(vmindex=vmindex, force=force)
    if recover and state.get("status") in {"broken", "stopped"}:
        return recover_mumu_device(vmindex=vmindex, reason=reason)
    return state


def _mumu_adb_serial_candidates() -> list[str]:
    env_candidates: list[str] = []
    for key in MUMU_ADB_SERIAL_ENV_KEYS:
        value = os.environ.get(key)
        if value and value.strip():
            env_candidates.append(value.strip())
            break
    if env_candidates:
        return _dedupe_mumu_adb_serials(env_candidates)

    candidates: list[str] = []
    cached_serial = _MUMU_ADB_SESSION.get("serial")
    if cached_serial and (_is_local_mumu_adb_serial(str(cached_serial)) or _mumu_adb_proxy_devices_allowed()):
        candidates.append(str(cached_serial))
    candidates.extend(f"127.0.0.1:{port}" for port in MUMU_ADB_PORTS)
    candidates.extend(_mumu_manager_adb_serial_candidates())
    if _mumu_adb_proxy_devices_allowed():
        try:
            candidates.extend(fanxiu_android_proxy_service.devices())
        except Exception:
            pass
    return _dedupe_mumu_adb_serials(candidates)


def _mumu_adb_meta(serial: str, *, input_name: str, adb_size: str = "") -> dict[str, Any]:
    parsed = _parse_adb_serial_host_port(serial)
    host, port = parsed if parsed is not None else ("", 0)
    return {
        "input": input_name,
        "adb_serial": serial,
        "adb_host": host,
        "adb_port": port,
        "adb_size": adb_size,
    }


def _ensure_mumu_adb_port_available() -> None:
    cached_error = _get_mumu_adb_failure_cache()
    if cached_error:
        _recover_mumu_adb_ports()
    errors: list[str] = []
    probe_timeout = _mumu_adb_port_probe_timeout()
    for serial in _mumu_adb_serial_candidates():
        parsed = _parse_adb_serial_host_port(serial)
        if parsed is None:
            continue
        host, port = parsed
        try:
            with socket.create_connection((host, port), timeout=probe_timeout):
                return
        except OSError as exc:
            errors.append(f"{serial}: {exc}")
            continue
    if errors and _recover_mumu_adb_ports():
        for serial in _mumu_adb_serial_candidates():
            parsed = _parse_adb_serial_host_port(serial)
            if parsed is None:
                continue
            host, port = parsed
            try:
                with socket.create_connection((host, port), timeout=probe_timeout):
                    _clear_mumu_adb_failure_cache()
                    return
            except OSError:
                continue
    message = "ADB 端口不可用：" + ("; ".join(errors) if errors else "没有可用 ADB serial")
    _set_mumu_adb_failure_cache(message)
    raise RuntimeError(message)


def _recover_mumu_adb_ports() -> bool:
    """Try to make local MuMu ADB ports reachable again.

    This only recovers local MuMu ports. Remote/proxy devices are deliberately
    not treated as a Runtime target unless explicitly configured elsewhere.
    """

    with _MUMU_ADB_RECOVERY_LOCK:
        _close_mumu_adb_session()
        try:
            adb_path = fanxiu_android_proxy_service.adb_path()
        except Exception:
            return False
        for command in (["kill-server"], ["start-server"]):
            try:
                run_quiet(
                    [str(adb_path), *command],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                )
            except Exception:
                pass
        for serial in _mumu_adb_serial_candidates():
            parsed = _parse_adb_serial_host_port(serial)
            if parsed is None:
                continue
            if not _is_local_mumu_adb_serial(serial):
                continue
            try:
                run_quiet(
                    [str(adb_path), "connect", serial],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                )
            except Exception:
                continue
            host, port = parsed
            try:
                with socket.create_connection((host, port), timeout=_mumu_adb_port_probe_timeout()):
                    _clear_mumu_adb_failure_cache()
                    return True
            except OSError:
                continue
        return False


def _is_mumu_target(*values: str | None) -> bool:
    return any("mumu" in str(value or "").lower() for value in values)


def _parse_adb_wm_size(value: str) -> tuple[int, int] | None:
    match = re.search(r"Physical size:\s*(\d+)x(\d+)", value)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _completed_text(process: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(
        part.strip()
        for part in (process.stdout or "", process.stderr or "")
        if part and part.strip()
    )
    return output.strip()


def _map_frame_point_to_adb(
    x: int,
    y: int,
    *,
    frame_width: int | None = None,
    frame_height: int | None = None,
    adb_width: int | None = None,
    adb_height: int | None = None,
) -> tuple[int, int]:
    if frame_width and frame_height and adb_width and adb_height and frame_width > 0 and frame_height > 0:
        return (
            max(0, min(adb_width - 1, int(round(x * adb_width / frame_width)))),
            max(0, min(adb_height - 1, int(round(y * adb_height / frame_height)))),
        )
    return x, y


def _run_mumu_adb_input(command: str, *, timeout_s: int = 5) -> dict[str, Any]:
    _ensure_mumu_adb_port_available()
    adb_path = fanxiu_android_proxy_service.adb_path()
    errors: list[str] = []
    for serial in _mumu_adb_serial_candidates():
        parsed = _parse_adb_serial_host_port(serial)
        if parsed is None:
            continue
        try:
            run_quiet(
                [str(adb_path), "connect", serial],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
            size_process = run_quiet(
                [str(adb_path), "-s", serial, "shell", "wm size"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            if size_process.returncode != 0:
                raise RuntimeError(_completed_text(size_process) or f"wm size 退出码 {size_process.returncode}")
            input_process = run_quiet(
                [str(adb_path), "-s", serial, "shell", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
            if input_process.returncode != 0:
                raise RuntimeError(_completed_text(input_process) or f"input 退出码 {input_process.returncode}")
            return _mumu_adb_meta(serial, input_name="adb-cli", adb_size=size_process.stdout.strip())
        except Exception as exc:
            errors.append(f"{serial}: {exc}")
    raise RuntimeError("ADB 输入失败：" + "; ".join(errors))


def _run_mumu_adb_shell_text(
    command: str,
    *,
    timeout_s: int = 5,
    preferred_serials: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    adb_path = fanxiu_android_proxy_service.adb_path()
    errors: list[str] = []
    candidate_serials = _dedupe_mumu_adb_serials([*(preferred_serials or []), *_mumu_adb_serial_candidates()])
    for attempt in range(2):
        for serial in candidate_serials:
            parsed = _parse_adb_serial_host_port(serial)
            if parsed is None:
                continue
            try:
                run_quiet(
                    [str(adb_path), "connect", serial],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3,
                )
                process = run_quiet(
                    [str(adb_path), "-s", serial, "shell", command],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_s,
                )
                if process.returncode != 0:
                    raise RuntimeError(_completed_text(process) or f"adb shell 退出码 {process.returncode}")
                return str(process.stdout or "").strip(), _mumu_adb_meta(serial, input_name="adb-cli")
            except Exception as exc:
                errors.append(f"{serial}: {exc}")
                if "offline" in str(exc).lower():
                    try:
                        run_quiet(
                            [str(adb_path), "disconnect", serial],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=3,
                        )
                    except Exception:
                        pass
        if attempt == 0:
            time.sleep(0.5)
            candidate_serials = _dedupe_mumu_adb_serials(
                [*candidate_serials, *_mumu_manager_adb_serial_candidates(), *_mumu_adb_serial_candidates()]
            )
    raise RuntimeError("ADB shell 失败：" + "; ".join(errors))


def _parse_wm_size_text(text: str) -> tuple[int, int] | None:
    matches = re.findall(r"(\d+)\s*x\s*(\d+)", str(text or ""))
    if not matches:
        return None
    width, height = matches[-1]
    return int(width), int(height)


def _parse_wm_density_text(text: str) -> int | None:
    matches = re.findall(r"(\d+)", str(text or ""))
    if not matches:
        return None
    return int(matches[-1])


def ensure_mumu_adb_resolution(*, vmindex: str = "1") -> dict[str, Any]:
    expected_width = int(DEFAULT_FIXED_WIDTH)
    expected_height = int(DEFAULT_FIXED_HEIGHT)
    expected_dpi = int(DEFAULT_FIXED_DPI)
    result: dict[str, Any] = {
        "expected": {"width": expected_width, "height": expected_height, "dpi": expected_dpi},
        "changed": False,
    }
    preferred_serials = _mumu_adb_serial_candidates()

    size_text, meta = _run_mumu_adb_shell_text("wm size", timeout_s=5, preferred_serials=preferred_serials)
    density_text, _ = _run_mumu_adb_shell_text("wm density", timeout_s=5, preferred_serials=preferred_serials)
    result["adb"] = meta
    result["before"] = {"size": size_text, "density": density_text}

    size = _parse_wm_size_text(size_text)
    density = _parse_wm_density_text(density_text)
    if size != (expected_width, expected_height):
        _run_mumu_adb_shell_text(
            f"wm size {expected_width}x{expected_height}",
            timeout_s=5,
            preferred_serials=preferred_serials,
        )
        result["changed"] = True
    if density != expected_dpi:
        _run_mumu_adb_shell_text(f"wm density {expected_dpi}", timeout_s=5, preferred_serials=preferred_serials)
        result["changed"] = True

    final_size_text, _ = _run_mumu_adb_shell_text("wm size", timeout_s=5, preferred_serials=preferred_serials)
    final_density_text, _ = _run_mumu_adb_shell_text("wm density", timeout_s=5, preferred_serials=preferred_serials)
    result["after"] = {"size": final_size_text, "density": final_density_text}
    final_size = _parse_wm_size_text(final_size_text)
    final_density = _parse_wm_density_text(final_density_text)
    result["ok"] = final_size == (expected_width, expected_height) and final_density == expected_dpi
    if not result["ok"]:
        raise RuntimeError(
            "MuMu 分辨率校正失败："
            f"size={final_size_text!r}, density={final_density_text!r}, "
            f"expected={expected_width}x{expected_height}@{expected_dpi}"
        )
    return result


def wait_mumu_adb_online(*, vmindex: str = "1", timeout_s: float = 45.0) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, float(timeout_s))
    last_error = ""
    preferred_serials = _mumu_adb_serial_candidates()
    while time.monotonic() < deadline:
        try:
            text, meta = _run_mumu_adb_shell_text("getprop sys.boot_completed", timeout_s=5, preferred_serials=preferred_serials)
            if str(text).strip() == "1":
                return {"ok": True, "boot_completed": text, "adb": meta}
            last_error = f"boot_completed={text!r}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
        preferred_serials = _dedupe_mumu_adb_serials([*preferred_serials, *_mumu_manager_adb_serial_candidates()])
    raise RuntimeError(f"MuMu ADB 未在 {timeout_s:.0f}s 内 online：{last_error}")


def _run_mumu_adb_shell_bytes(command: str, *, timeout_s: int = 8) -> tuple[bytes, dict[str, Any]]:
    _ensure_mumu_adb_port_available()
    try:
        from adb_shell.adb_device import AdbDeviceTcp
    except Exception as exc:
        raise RuntimeError(f"ADB 依赖不可用：{exc}") from exc

    errors: list[str] = []
    for serial in _mumu_adb_serial_candidates():
        parsed = _parse_adb_serial_host_port(serial)
        if parsed is None:
            continue
        host, port = parsed
        device = AdbDeviceTcp(host, port, default_transport_timeout_s=5)
        try:
            device.connect(rsa_keys=[], auth_timeout_s=1, read_timeout_s=5)
            size_text = device.shell("wm size", transport_timeout_s=5, read_timeout_s=5)
            data = device.shell(command, transport_timeout_s=timeout_s, read_timeout_s=timeout_s, decode=False)
            device.close()
            return bytes(data), _mumu_adb_meta(serial, input_name="adb", adb_size=size_text.strip())
        except Exception as exc:
            errors.append(f"{serial}: {exc}")
            try:
                device.close()
            except Exception:
                pass
    raise RuntimeError("ADB 命令失败：" + "; ".join(errors))


def _close_mumu_adb_session() -> None:
    device = _MUMU_ADB_SESSION.pop("device", None)
    _MUMU_ADB_SESSION.pop("port", None)
    _MUMU_ADB_SESSION.pop("host", None)
    _MUMU_ADB_SESSION.pop("serial", None)
    try:
        if device is not None:
            device.close()
    except Exception:
        pass


def _mumu_adb_session_shell_bytes(command: str, *, timeout_s: int = 8) -> tuple[bytes, dict[str, Any]]:
    _ensure_mumu_adb_port_available()
    try:
        from adb_shell.adb_device import AdbDeviceTcp
    except Exception as exc:
        raise RuntimeError(f"ADB 依赖不可用：{exc}") from exc

    with _MUMU_ADB_SESSION_LOCK:
        errors: list[str] = []
        cached_device = _MUMU_ADB_SESSION.get("device")
        cached_port = _MUMU_ADB_SESSION.get("port")
        cached_serial = str(_MUMU_ADB_SESSION.get("serial") or "")
        candidate_serials = _mumu_adb_serial_candidates()
        candidate_serial_set = set(candidate_serials)
        if cached_serial and cached_serial not in candidate_serial_set:
            _close_mumu_adb_session()
            cached_device = None
            cached_port = None
            cached_serial = ""
        if cached_device is not None and cached_port is not None and cached_serial:
            try:
                data = cached_device.shell(command, transport_timeout_s=timeout_s, read_timeout_s=timeout_s, decode=False)
                return bytes(data), _mumu_adb_meta(cached_serial, input_name="adb-session")
            except Exception as exc:
                errors.append(f"{cached_serial}: {exc}")
                _close_mumu_adb_session()

        for serial in candidate_serials:
            parsed = _parse_adb_serial_host_port(serial)
            if parsed is None:
                continue
            host, port = parsed
            device = AdbDeviceTcp(host, port, default_transport_timeout_s=5)
            try:
                device.connect(rsa_keys=[], auth_timeout_s=1, read_timeout_s=5)
                data = device.shell(command, transport_timeout_s=timeout_s, read_timeout_s=timeout_s, decode=False)
                _MUMU_ADB_SESSION["device"] = device
                _MUMU_ADB_SESSION["host"] = host
                _MUMU_ADB_SESSION["port"] = port
                _MUMU_ADB_SESSION["serial"] = serial
                return bytes(data), _mumu_adb_meta(serial, input_name="adb-session")
            except Exception as exc:
                errors.append(f"{serial}: {exc}")
                try:
                    device.close()
                except Exception:
                    pass
        raise RuntimeError("ADB 会话命令失败：" + "; ".join(errors))


def _normalize_keyevent_arg(key: str | int) -> str:
    raw = str(key).strip()
    if not raw:
        raise RuntimeError("keyevent 不能为空")
    if raw.isdigit():
        return raw
    normalized = raw.upper()
    normalized = normalized if normalized.startswith("KEYCODE_") else f"KEYCODE_{normalized}"
    if not re.fullmatch(r"KEYCODE_[A-Z0-9_]+", normalized):
        raise RuntimeError(f"keyevent 格式非法：{raw}")
    return normalized


def keyevent_mumu_adb(key: str | int) -> dict[str, Any]:
    key_arg = _normalize_keyevent_arg(key)
    result = _run_mumu_adb_input(f"input keyevent {key_arg}")
    return {**result, "keyevent": key_arg}


def keyevents_mumu_adb(keys: list[str | int]) -> dict[str, Any]:
    key_args = [_normalize_keyevent_arg(key) for key in keys]
    if not key_args:
        raise RuntimeError("keyevents 不能为空")
    result = _run_mumu_adb_input(f"input keyevent {' '.join(key_args)}")
    return {**result, "keyevents": key_args}


def text_mumu_adb(text: str) -> dict[str, Any]:
    value = str(text or "")
    if not value:
        raise RuntimeError("text 不能为空")
    if any(ord(char) < 32 for char in value):
        raise RuntimeError("text 不能包含控制字符")
    # Android input treats %s as a space token. Quote the argument so Chinese
    # and punctuation survive the adb shell layer.
    escaped = value.replace("%", "%25").replace(" ", "%s")
    result = _run_mumu_adb_input(f"input text {shlex.quote(escaped)}")
    return {**result, "text_length": len(value)}


def screencap_mumu_adb_png() -> tuple[bytes, dict[str, Any]]:
    try:
        data, meta = _mumu_adb_session_shell_bytes("screencap -p", timeout_s=10)
    except Exception as session_exc:
        session_error = str(session_exc)
        if _is_mumu_adb_unavailable_error(session_error):
            _set_mumu_adb_failure_cache(session_error)
            raise RuntimeError(session_error) from session_exc
        adb_path = fanxiu_android_proxy_service.adb_path()
        serial = (_mumu_adb_serial_candidates() or [f"127.0.0.1:{MUMU_ADB_PORTS[0]}"])[0]
        process = run_quiet(
            [str(adb_path), "-s", serial, "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=6,
        )
        if process.returncode == 0 and process.stdout:
            data = process.stdout
            meta = _mumu_adb_meta(serial, input_name="adb")
        else:
            message = (process.stderr or b"").decode("utf-8", errors="replace") or f"adb 退出码 {process.returncode}"
            _set_mumu_adb_failure_cache(message)
            raise RuntimeError(message)
    # Some adb stacks emit CRLF around PNG chunks; normalize only the common corruption pattern.
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        data = data.replace(b"\r\n", b"\n")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("ADB screencap 返回的不是 PNG 数据")
    black_frame = _mumu_adb_png_black_frame_summary(data)
    if black_frame.get("black"):
        raise RuntimeError(f"MuMu ADB截图疑似黑屏，需重建模拟器画面链路：{black_frame}")
    _clear_mumu_adb_failure_cache()
    return data, meta


MUMU_ADB_STREAM_MAX_FPS = 1.5
_MUMU_ADB_STREAM_FRAME_LOCK = threading.Lock()
_mumu_adb_stream_frame_data: bytes | None = None
_mumu_adb_stream_frame_timestamp = 0.0


def get_mumu_adb_cached_stream_frame(*, max_age_seconds: float = 3.0) -> bytes | None:
    now = time.monotonic()
    with _MUMU_ADB_STREAM_FRAME_LOCK:
        if _mumu_adb_stream_frame_data is None:
            return None
        if now - _mumu_adb_stream_frame_timestamp > max_age_seconds:
            return None
        return _mumu_adb_stream_frame_data


def screencap_mumu_adb_cached_png(*, cached_only: bool = False, max_age_seconds: float = 3.0) -> tuple[bytes, dict[str, Any]]:
    cached = get_mumu_adb_cached_stream_frame(max_age_seconds=max_age_seconds)
    if cached is not None:
        serial = str(_MUMU_ADB_SESSION.get("serial") or "")
        if not serial:
            serial = (_mumu_adb_serial_candidates() or [f"127.0.0.1:{MUMU_ADB_PORTS[0]}"])[0]
        return cached, _mumu_adb_meta(serial, input_name="adb_cached_stream")
    if cached_only:
        raise RuntimeError("当前没有可用的画面流缓存帧")
    return screencap_mumu_adb_png()


def _get_mumu_adb_stream_frame(*, min_interval: float) -> bytes:
    global _mumu_adb_stream_frame_data, _mumu_adb_stream_frame_timestamp
    now = time.monotonic()
    with _MUMU_ADB_STREAM_FRAME_LOCK:
        if _mumu_adb_stream_frame_data is not None and now - _mumu_adb_stream_frame_timestamp < min_interval:
            return _mumu_adb_stream_frame_data
        try:
            data, _meta = screencap_mumu_adb_png()
        except Exception:
            if (
                _mumu_adb_stream_frame_data is not None
                and time.monotonic() - _mumu_adb_stream_frame_timestamp < max(2.0, min_interval * 3)
            ):
                return _mumu_adb_stream_frame_data
            raise
        _mumu_adb_stream_frame_data = data
        _mumu_adb_stream_frame_timestamp = time.monotonic()
        return data


def stream_mumu_adb_screencap_mjpeg(*, fps: float = 2.0) -> Any:
    # ADB screencap is a blocking full-frame capture. High requested FPS can starve match/OCR requests.
    interval = 1.0 / max(0.2, min(MUMU_ADB_STREAM_MAX_FPS, float(fps or 2.0)))
    while True:
        started = time.monotonic()
        try:
            data = _get_mumu_adb_stream_frame(min_interval=interval)
            yield (
                b"--frame\r\n"
                b"Content-Type: image/png\r\n"
                b"Cache-Control: no-store\r\n\r\n"
                + data
                + b"\r\n"
            )
        except Exception as exc:
            message = str(exc).encode("utf-8", errors="replace")
            yield (
                b"--frame\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"Cache-Control: no-store\r\n\r\n"
                + message
                + b"\r\n"
            )
        elapsed = time.monotonic() - started
        if elapsed < interval:
            time.sleep(interval - elapsed)


def _tap_mumu_adb(
    x: int,
    y: int,
    *,
    frame_width: int | None = None,
    frame_height: int | None = None,
) -> dict[str, Any]:
    probe = _run_mumu_adb_input("echo ok")
    adb_size = _parse_adb_wm_size(str(probe.get("adb_size") or ""))
    adb_x, adb_y = _map_frame_point_to_adb(
        x,
        y,
        frame_width=frame_width,
        frame_height=frame_height,
        adb_width=adb_size[0] if adb_size else None,
        adb_height=adb_size[1] if adb_size else None,
    )
    result = _run_mumu_adb_input(f"input tap {adb_x} {adb_y}")
    return {**result, "adb_x": adb_x, "adb_y": adb_y}


def _swipe_mumu_adb(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    *,
    duration_ms: int = 300,
    frame_width: int | None = None,
    frame_height: int | None = None,
) -> dict[str, Any]:
    probe = _run_mumu_adb_input("echo ok")
    adb_size = _parse_adb_wm_size(str(probe.get("adb_size") or ""))
    adb_start_x, adb_start_y = _map_frame_point_to_adb(
        start_x,
        start_y,
        frame_width=frame_width,
        frame_height=frame_height,
        adb_width=adb_size[0] if adb_size else None,
        adb_height=adb_size[1] if adb_size else None,
    )
    adb_end_x, adb_end_y = _map_frame_point_to_adb(
        end_x,
        end_y,
        frame_width=frame_width,
        frame_height=frame_height,
        adb_width=adb_size[0] if adb_size else None,
        adb_height=adb_size[1] if adb_size else None,
    )
    result = _run_mumu_adb_input(
        f"input swipe {adb_start_x} {adb_start_y} {adb_end_x} {adb_end_y} {max(50, min(3000, int(duration_ms)))}",
        timeout_s=8,
    )
    return {
        **result,
        "adb_start_x": adb_start_x,
        "adb_start_y": adb_start_y,
        "adb_end_x": adb_end_x,
        "adb_end_y": adb_end_y,
    }


def _processed_window_target(
    *,
    normalized_title: str,
    title_match: str,
    area: str | None,
    mode: str | None,
    crop: str | None,
    trim_border: str | None,
    rotate: str | None,
    resolved_fixed_width: int,
    resolved_fixed_height: int,
) -> tuple[Any, Any, Any, Any, str, str, Any, Any, str]:
    ensure_windows_runtime()
    set_dpi_awareness()
    resolved_area = area or os.getenv("CODEYUN_FANXIU_MUMU_AREA", "outer")
    resolved_mode = mode or os.getenv("CODEYUN_FANXIU_MUMU_MODE", DEFAULT_CAPTURE_MODE)
    resolved_crop = parse_crop(crop or os.getenv("CODEYUN_FANXIU_MUMU_CROP", DEFAULT_CROP))
    resolved_trim_border = parse_crop(
        trim_border or os.getenv("CODEYUN_FANXIU_MUMU_TRIM_BORDER", DEFAULT_TRIM_BORDER)
    )
    resolved_rotate = normalize_rotate(rotate or os.getenv("CODEYUN_FANXIU_MUMU_ROTATE", DEFAULT_ROTATE))

    target, refind_title, refind_match = _find_mumu_window_candidate(normalized_title, title_match)
    capturer = WindowCapture(target.hwnd, resolved_area, resolved_mode, refind_title, refind_match, refind_interval=1.0)
    raw_frame = capturer.capture()
    if raw_frame is None:
        raise RuntimeError("输入前截图失败，无法确认窗口坐标")

    frame = process_frame(
        raw_frame,
        resolved_crop,
        resolved_trim_border,
        resolved_rotate,
        max_width=0,
        max_height=0,
        scale=1.0,
        fixed_width=resolved_fixed_width,
        fixed_height=resolved_fixed_height,
    )
    return target, capturer, raw_frame, frame, resolved_area, resolved_mode, resolved_crop, resolved_trim_border, resolved_rotate


@dataclass(frozen=True)
class MumuWindowProcessInfo:
    pid: int
    parent_pid: int | None
    name: str
    command_line: str
    started_at: str | None
    runtime_seconds: int | None


@dataclass(frozen=True)
class MumuWindowStatus:
    running: bool
    pids: list[int]
    primary_pid: int | None
    started_at: str | None
    runtime_seconds: int | None
    command_line: str
    target_title: str
    preview_title: str
    stdout_log: str
    stderr_log: str
    last_error: str


def get_target_title() -> str:
    return (os.getenv("CODEYUN_FANXIU_MUMU_TITLE") or DEFAULT_TARGET_TITLE).strip() or DEFAULT_TARGET_TITLE


def get_preview_title() -> str:
    return (os.getenv("CODEYUN_FANXIU_MUMU_PREVIEW_TITLE") or DEFAULT_PREVIEW_TITLE).strip() or DEFAULT_PREVIEW_TITLE


def _home_root() -> Path:
    return ROOT_DIR.parent.parent if ROOT_DIR.parent.name.lower() == "slns" else ROOT_DIR.parent


def get_fanxiu_mainwin_root() -> Path:
    configured = os.getenv("FX_MAINWIN_ROOT")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser().resolve(strict=False)
    return (_home_root() / "data" / "m2508凡修" / "mainwin").resolve(strict=False)


def get_fanxiu_screenshot_frame_dir() -> Path:
    configured = os.getenv("FX_SCREENSHOT_FRAME_DIR")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser().resolve(strict=False)
    return (get_fanxiu_mainwin_root() / SCREENSHOT_FRAME_DIRNAME).resolve(strict=False)


def get_fanxiu_match_frame_dir() -> Path:
    configured = os.getenv("FX_MATCH_FRAME_DIR")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser().resolve(strict=False)
    return (get_fanxiu_mainwin_root() / MATCH_FRAME_DIRNAME).resolve(strict=False)


def get_fanxiu_burst_frame_dir() -> Path:
    configured = os.getenv("FX_BURST_FRAME_DIR")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser().resolve(strict=False)
    return (get_fanxiu_mainwin_root() / BURST_FRAME_DIRNAME).resolve(strict=False)


def _next_numbered_frame_path(output_dir: Path, suffix: str = ".png") -> tuple[int, Path]:
    max_index = 0
    if output_dir.exists():
        for path in output_dir.iterdir():
            if not path.is_file():
                continue
            match = _SCREENSHOT_FRAME_NAME_PATTERN.match(path.name)
            if match:
                max_index = max(max_index, int(match.group(1)))
    index = max_index + 1
    return index, output_dir / f"{index:04d}{suffix}"


def _next_screenshot_frame_path(output_dir: Path) -> tuple[int, Path]:
    return _next_numbered_frame_path(output_dir, ".png")


def _match_frame_max_files() -> int:
    raw_value = os.getenv(MATCH_FRAME_MAX_FILES_ENV, "").strip()
    if not raw_value:
        return DEFAULT_MATCH_FRAME_MAX_FILES
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_MATCH_FRAME_MAX_FILES
    return max(1, value)


def _prune_match_frame_dir(output_dir: Path, *, max_files: int | None = None) -> None:
    limit = _match_frame_max_files() if max_files is None else max(1, int(max_files))
    try:
        files = [
            path
            for path in output_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _SCREENSHOT_IMAGE_SUFFIXES
        ]
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return

    overflow = len(files) - limit
    if overflow <= 0:
        return

    for path in sorted(files, key=_screenshot_sort_key)[:overflow]:
        try:
            path.unlink()
        except OSError:
            continue


def _save_limited_match_frame(data: bytes, suffix: str) -> tuple[int, Path]:
    output_dir = get_fanxiu_match_frame_dir()
    with _MATCH_FRAME_LOCK:
        output_dir.mkdir(parents=True, exist_ok=True)
        index, output = _next_numbered_frame_path(output_dir, suffix)
        while output.exists():
            index += 1
            output = output_dir / f"{index:04d}{suffix}"
        output.write_bytes(data)
        _prune_match_frame_dir(output_dir)
    return index, output


def _normalize_screenshot_filename(filename: str) -> str:
    name = Path(str(filename or "")).name
    if not name or name != str(filename) or "\x00" in name:
        raise ValueError("截图文件名不合法")
    if Path(name).suffix.lower() not in _SCREENSHOT_IMAGE_SUFFIXES:
        raise ValueError("截图只支持 jpg/jpeg/png")
    return name


def _screenshot_sort_key(path: Path) -> tuple[int, int, str]:
    match = _SCREENSHOT_FRAME_NAME_PATTERN.match(path.name)
    if match:
        return (0, int(match.group(1)), path.name.lower())
    return (1, 0, path.name.lower())


def _read_image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def _read_image_bgr(path: Path):
    import cv2
    import numpy as np

    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _frame_phash(frame: Any) -> str:
    import cv2
    import numpy as np

    bgr = _ensure_bgr_frame(frame)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype("float32")
    dct = cv2.dct(small)
    block = dct[:8, :8].copy()
    values = block.flatten()
    median = float(np.median(values[1:])) if values.size > 1 else float(np.median(values))
    bits = 0
    for value in values:
        bits = (bits << 1) | int(float(value) > median)
    return f"{bits:016x}"


def _decode_image_data_url_bgr(data_url: str):
    import cv2
    import numpy as np

    text = str(data_url or "").strip()
    if not text:
        return None
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    data = np.frombuffer(base64.b64decode(text, validate=False), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _decode_image_data_url_gray(data_url: str):
    import cv2
    import numpy as np

    text = str(data_url or "").strip()
    if not text:
        return None
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    data = np.frombuffer(base64.b64decode(text, validate=False), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)


def _screenshot_path(filename: str) -> Path:
    output_dir = get_fanxiu_screenshot_frame_dir()
    image_path = (output_dir / _normalize_screenshot_filename(filename)).resolve(strict=False)
    if image_path.parent != output_dir.resolve(strict=False):
        raise ValueError("截图路径越界")
    return image_path


def _match_frame_path(filename: str) -> Path:
    output_dir = get_fanxiu_match_frame_dir()
    image_path = (output_dir / _normalize_screenshot_filename(filename)).resolve(strict=False)
    if image_path.parent != output_dir.resolve(strict=False):
        raise ValueError("匹配帧路径越界")
    return image_path


def _burst_frame_path(filename: str) -> Path:
    output_dir = get_fanxiu_burst_frame_dir()
    image_path = (output_dir / _normalize_screenshot_filename(filename)).resolve(strict=False)
    if image_path.parent != output_dir.resolve(strict=False):
        raise ValueError("连拍帧路径越界")
    return image_path


def _screenshot_pre_label_path(image_path: Path) -> Path:
    return image_path.with_name(f"{image_path.stem}_pre.json")


def _screenshot_final_label_path(image_path: Path) -> Path:
    return image_path.with_suffix(".json")


def list_fanxiu_screenshots() -> dict[str, Any]:
    output_dir = get_fanxiu_screenshot_frame_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for path in sorted(
        (item for item in output_dir.iterdir() if item.is_file() and item.suffix.lower() in _SCREENSHOT_IMAGE_SUFFIXES),
        key=_screenshot_sort_key,
    ):
        stat = path.stat()
        width, height = _read_image_size(path)
        pre_label_path = _screenshot_pre_label_path(path)
        label_path = _screenshot_final_label_path(path)
        items.append(
            {
                "filename": path.name,
                "stem": path.stem,
                "pre_label_filename": pre_label_path.name,
                "pre_label_exists": pre_label_path.is_file(),
                "label_filename": label_path.name,
                "label_exists": label_path.is_file(),
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "width": width,
                "height": height,
            }
        )
    return {
        "directory": os.fspath(output_dir),
        "items": items,
    }


def get_fanxiu_screenshot_path(filename: str) -> Path:
    image_path = _screenshot_path(filename)
    if not image_path.is_file():
        raise FileNotFoundError(f"截图不存在：{image_path.name}")
    return image_path


def get_fanxiu_match_frame_path(filename: str) -> Path:
    image_path = _match_frame_path(filename)
    if not image_path.is_file():
        raise FileNotFoundError(f"匹配帧不存在：{image_path.name}")
    return image_path


def delete_fanxiu_screenshot(filename: str) -> dict[str, Any]:
    image_path = get_fanxiu_screenshot_path(filename)
    deleted: list[str] = []
    for path in (image_path, _screenshot_pre_label_path(image_path), _screenshot_final_label_path(image_path)):
        if path.is_file():
            path.unlink()
            deleted.append(path.name)
    return {
        "filename": image_path.name,
        "deleted": deleted,
    }


def _default_screenshot_pre_label_payload(image_path: Path) -> dict[str, Any]:
    width, height = _read_image_size(image_path)
    return {
        "version": 1,
        "image": image_path.name,
        "size": {
            "width": width,
            "height": height,
        },
        "boxes": [],
    }


def _normalize_screenshot_pre_label_payload(image_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    width, height = _read_image_size(image_path)
    normalized_boxes: list[dict[str, Any]] = []
    raw_boxes = payload.get("boxes") if isinstance(payload, dict) else None
    if isinstance(raw_boxes, list):
        for index, raw_box in enumerate(raw_boxes, start=1):
            if not isinstance(raw_box, dict):
                continue
            try:
                x = round(float(raw_box.get("x", 0)))
                y = round(float(raw_box.get("y", 0)))
                w = round(float(raw_box.get("w", 0)))
                h = round(float(raw_box.get("h", 0)))
            except (TypeError, ValueError):
                continue
            if w <= 0 or h <= 0:
                continue
            name = str(raw_box.get("name") or "").strip()[:100]
            max_x = width if width > 0 else max(1, x + w)
            max_y = height if height > 0 else max(1, y + h)
            x = min(max(0, x), max(0, max_x - 1))
            y = min(max(0, y), max(0, max_y - 1))
            w = min(max(1, w), max(1, max_x - x))
            h = min(max(1, h), max(1, max_y - y))
            normalized_boxes.append(
                {
                    "name": name,
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                }
            )

    return {
        "version": 1,
        "image": image_path.name,
        "size": {
            "width": width,
            "height": height,
        },
        "boxes": normalized_boxes,
    }


def read_fanxiu_screenshot_pre_label(filename: str) -> dict[str, Any]:
    image_path = get_fanxiu_screenshot_path(filename)
    pre_label_path = _screenshot_pre_label_path(image_path)
    if not pre_label_path.is_file():
        return {
            "exists": False,
            "filename": pre_label_path.name,
            "payload": _default_screenshot_pre_label_payload(image_path),
        }
    try:
        payload = json.loads(pre_label_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "exists": True,
        "filename": pre_label_path.name,
        "payload": _normalize_screenshot_pre_label_payload(image_path, payload),
    }


def write_fanxiu_screenshot_pre_label(filename: str, payload: dict[str, Any]) -> dict[str, Any]:
    image_path = get_fanxiu_screenshot_path(filename)
    pre_label_path = _screenshot_pre_label_path(image_path)
    normalized = _normalize_screenshot_pre_label_payload(image_path, payload)
    pre_label_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "exists": True,
        "filename": pre_label_path.name,
        "payload": normalized,
    }


def _runtime_dir() -> Path:
    path = get_settings().data_dir / "fanxiu" / "mumu-window"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stdout_log_path() -> Path:
    return _runtime_dir() / "preview.stdout.log"


def _stderr_log_path() -> Path:
    return _runtime_dir() / "preview.stderr.log"


def _normalize_command_line(cmdline: Any) -> str:
    if isinstance(cmdline, (list, tuple)):
        return " ".join(str(part) for part in cmdline if part is not None)
    return str(cmdline or "")


def _safe_command_line(proc: psutil.Process) -> str:
    try:
        return _normalize_command_line(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return ""


def _safe_environ(proc: psutil.Process) -> dict[str, str] | None:
    try:
        return proc.environ()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _safe_name(proc: psutil.Process) -> str:
    try:
        return proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return ""


def _safe_ppid(proc: psutil.Process) -> int | None:
    try:
        return proc.ppid()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _safe_create_time(proc: psutil.Process) -> float | None:
    try:
        return proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _is_preview_process(proc: psutil.Process) -> bool:
    if proc.pid == os.getpid():
        return False

    environ = _safe_environ(proc)
    if environ and environ.get(PROCESS_ENV_MARKER) == PROCESS_ENV_VALUE:
        return True

    command_line = _safe_command_line(proc)
    normalized = command_line.replace("\\", "/")
    return PREVIEW_MODULE in normalized and get_preview_title() in command_line


def _process_info(proc: psutil.Process) -> MumuWindowProcessInfo | None:
    try:
        created_ts = _safe_create_time(proc)
        return MumuWindowProcessInfo(
            pid=proc.pid,
            parent_pid=_safe_ppid(proc),
            name=_safe_name(proc),
            command_line=_safe_command_line(proc),
            started_at=datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S") if created_ts else None,
            runtime_seconds=max(0, int(time.time() - created_ts)) if created_ts else None,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _list_preview_processes() -> list[MumuWindowProcessInfo]:
    items: list[MumuWindowProcessInfo] = []
    for proc in psutil.process_iter(["pid"]):
        try:
            if not _is_preview_process(proc):
                continue
            info = _process_info(proc)
            if info is not None:
                items.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    items.sort(key=lambda item: (item.started_at or "", item.pid))
    return items


def _tail_text(path: Path, max_chars: int = 2000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if not data:
        return ""
    return data[-max_chars:].decode("utf-8", errors="replace").strip()


def get_mumu_window_status() -> dict[str, Any]:
    items = _list_preview_processes()
    primary = items[0] if items else None
    status = MumuWindowStatus(
        running=bool(items),
        pids=[item.pid for item in items],
        primary_pid=primary.pid if primary else None,
        started_at=primary.started_at if primary else None,
        runtime_seconds=primary.runtime_seconds if primary else None,
        command_line=primary.command_line if primary else "",
        target_title=get_target_title(),
        preview_title=get_preview_title(),
        stdout_log=os.fspath(_stdout_log_path()),
        stderr_log=os.fspath(_stderr_log_path()),
        last_error=_tail_text(_stderr_log_path()),
    )
    return asdict(status)


def _build_preview_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        PREVIEW_MODULE,
        "--title",
        get_target_title(),
        "--fps",
        os.getenv("CODEYUN_FANXIU_MUMU_FPS", DEFAULT_FPS),
        "--mode",
        os.getenv("CODEYUN_FANXIU_MUMU_MODE", DEFAULT_CAPTURE_MODE),
        "--crop",
        os.getenv("CODEYUN_FANXIU_MUMU_CROP", DEFAULT_CROP),
        "--trim-border",
        os.getenv("CODEYUN_FANXIU_MUMU_TRIM_BORDER", DEFAULT_TRIM_BORDER),
        "--rotate",
        os.getenv("CODEYUN_FANXIU_MUMU_ROTATE", DEFAULT_ROTATE),
        "--fixed-width",
        os.getenv("CODEYUN_FANXIU_MUMU_FIXED_WIDTH", DEFAULT_FIXED_WIDTH),
        "--fixed-height",
        os.getenv("CODEYUN_FANXIU_MUMU_FIXED_HEIGHT", DEFAULT_FIXED_HEIGHT),
        "--preview-title",
        get_preview_title(),
    ]


def _build_child_env() -> dict[str, str]:
    env = os.environ.copy()
    env[PROCESS_ENV_MARKER] = PROCESS_ENV_VALUE
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = os.fspath(ROOT_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def start_window_capture_preview() -> dict[str, Any]:
    if sys.platform != "win32":
        raise RuntimeError("MuMu 窗口预览仅支持 Windows 桌面环境")

    current_status = get_mumu_window_status()
    if current_status["running"]:
        return current_status

    stdout_path = _stdout_log_path()
    stderr_path = _stderr_log_path()
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    command = _build_preview_command()
    with stdout_path.open("a", encoding="utf-8", errors="replace") as stdout_file, stderr_path.open(
        "a",
        encoding="utf-8",
        errors="replace",
    ) as stderr_file:
        process = popen_service(
            command,
            cwd=os.fspath(ROOT_DIR),
            env=_build_child_env(),
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
        )

    time.sleep(0.8)
    return_code = process.poll()
    if return_code is not None:
        detail = _tail_text(stderr_path) or _tail_text(stdout_path) or "无日志"
        raise RuntimeError(f"投屏旋转预览启动后退出（退出码 {return_code}）：{detail}")

    return get_mumu_window_status()


def stop_window_capture_preview(timeout: float = 3.0) -> dict[str, Any]:
    targets: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid"]):
        try:
            if _is_preview_process(proc):
                targets.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    errors: list[dict[str, Any]] = []
    for proc in targets:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            errors.append({"pid": proc.pid, "error": str(exc)})

    _, alive = psutil.wait_procs(targets, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            errors.append({"pid": proc.pid, "error": str(exc)})

    if alive:
        psutil.wait_procs(alive, timeout=timeout)

    status = get_mumu_window_status()
    if errors:
        status["errors"] = errors
    return status


def stream_mumu_window_mjpeg(
    *,
    title: str | None = None,
    title_match: str = "contains",
    fps: float | None = None,
    quality: int = 80,
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
    auto_dismiss_popup: bool = False,
    popup_check_interval: float = 3.0,
):
    resolved_title = (title or get_target_title()).strip() or get_target_title()
    resolved_crop = parse_crop(crop or os.getenv("CODEYUN_FANXIU_MUMU_CROP", DEFAULT_CROP))
    resolved_trim_border = parse_crop(trim_border or os.getenv("CODEYUN_FANXIU_MUMU_TRIM_BORDER", DEFAULT_TRIM_BORDER))
    resolved_rotate = normalize_rotate(rotate or os.getenv("CODEYUN_FANXIU_MUMU_ROTATE", DEFAULT_ROTATE))
    resolved_area = area or os.getenv("CODEYUN_FANXIU_MUMU_AREA", "outer")
    resolved_mode = mode or os.getenv("CODEYUN_FANXIU_MUMU_MODE", DEFAULT_CAPTURE_MODE)
    resolved_fixed_width = int(
        fixed_width if fixed_width is not None else os.getenv("CODEYUN_FANXIU_MUMU_FIXED_WIDTH", DEFAULT_FIXED_WIDTH)
    )
    resolved_fixed_height = int(
        fixed_height if fixed_height is not None else os.getenv("CODEYUN_FANXIU_MUMU_FIXED_HEIGHT", DEFAULT_FIXED_HEIGHT)
    )
    cache_key = _latest_frame_cache_key(
        resolved_title,
        title_match,
        resolved_mode,
        resolved_area,
        resolved_crop,
        resolved_trim_border,
        resolved_rotate,
        resolved_fixed_width,
        resolved_fixed_height,
    )

    return iter_mjpeg_frames(
        title=resolved_title,
        fps=float(fps or os.getenv("CODEYUN_FANXIU_MUMU_FPS", DEFAULT_FPS)),
        crop=resolved_crop,
        trim_border=resolved_trim_border,
        rotate=resolved_rotate,
        area=resolved_area,
        mode=resolved_mode,
        max_width=0,
        max_height=0,
        scale=1.0,
        fixed_width=resolved_fixed_width,
        fixed_height=resolved_fixed_height,
        refind_interval=1.0,
        quality=quality,
        title_match=title_match,
        auto_dismiss_popup=auto_dismiss_popup,
        popup_check_interval=popup_check_interval,
        on_frame=lambda frame: _store_latest_frame(cache_key, frame),
    )


def _latest_frame_cache_key(
    title: str,
    title_match: str,
    mode: str,
    area: str,
    crop: tuple[int, int, int, int],
    trim_border: tuple[int, int, int, int],
    rotate: str,
    fixed_width: int,
    fixed_height: int,
) -> tuple[Any, ...]:
    return (
        title,
        title_match,
        mode,
        area,
        crop,
        trim_border,
        rotate,
        int(fixed_width),
        int(fixed_height),
    )


def _store_latest_frame(cache_key: tuple[Any, ...], frame: Any) -> None:
    with _LATEST_FRAME_LOCK:
        _LATEST_FRAME_CACHE[cache_key] = (time.monotonic(), frame.copy())


def _load_latest_frame(cache_key: tuple[Any, ...]) -> Any | None:
    with _LATEST_FRAME_LOCK:
        item = _LATEST_FRAME_CACHE.get(cache_key)
    if item is None:
        return None
    captured_at, frame = item
    if time.monotonic() - captured_at > _LATEST_FRAME_MAX_AGE_SECONDS:
        return None
    return frame.copy()


def capture_mumu_window_frame(
    *,
    title: str | None = None,
    title_match: str = "contains",
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
    prefer_cached: bool = False,
):
    ensure_windows_runtime()
    set_dpi_awareness()

    normalized_title = (title or get_target_title()).strip() or get_target_title()
    resolved_area = area or os.getenv("CODEYUN_FANXIU_MUMU_AREA", "outer")
    resolved_mode = mode or os.getenv("CODEYUN_FANXIU_MUMU_MODE", DEFAULT_CAPTURE_MODE)
    resolved_crop = parse_crop(crop or os.getenv("CODEYUN_FANXIU_MUMU_CROP", DEFAULT_CROP))
    resolved_trim_border = parse_crop(trim_border or os.getenv("CODEYUN_FANXIU_MUMU_TRIM_BORDER", DEFAULT_TRIM_BORDER))
    resolved_rotate = normalize_rotate(rotate or os.getenv("CODEYUN_FANXIU_MUMU_ROTATE", DEFAULT_ROTATE))
    resolved_fixed_width = int(
        fixed_width if fixed_width is not None else os.getenv("CODEYUN_FANXIU_MUMU_FIXED_WIDTH", DEFAULT_FIXED_WIDTH)
    )
    resolved_fixed_height = int(
        fixed_height if fixed_height is not None else os.getenv("CODEYUN_FANXIU_MUMU_FIXED_HEIGHT", DEFAULT_FIXED_HEIGHT)
    )
    if prefer_cached:
        cached = _load_latest_frame(
            _latest_frame_cache_key(
                normalized_title,
                title_match,
                resolved_mode,
                resolved_area,
                resolved_crop,
                resolved_trim_border,
                resolved_rotate,
                resolved_fixed_width,
                resolved_fixed_height,
            )
        )
        if cached is not None:
            return cached

    target, refind_title, refind_match = _find_mumu_window_candidate(normalized_title, title_match)
    capturer = WindowCapture(
        target.hwnd,
        resolved_area,
        resolved_mode,
        refind_title,
        refind_match,
        refind_interval=1.0,
    )
    frame = capturer.capture()
    if frame is None:
        raise RuntimeError("截图失败")

    return process_frame(
        frame,
        resolved_crop,
        resolved_trim_border,
        resolved_rotate,
        max_width=0,
        max_height=0,
        scale=1.0,
        fixed_width=resolved_fixed_width,
        fixed_height=resolved_fixed_height,
    )


def save_fanxiu_screenshot_frame(
    *,
    title: str | None = None,
    title_match: str = "contains",
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
    quality: int = 82,
    current_frame_data_url: str | None = None,
    overwrite_filename: str | None = None,
) -> dict[str, Any]:
    frame = _decode_image_data_url_bgr(current_frame_data_url or "") if current_frame_data_url else None
    if frame is None:
        frame = capture_mumu_window_frame(
            title=title,
            title_match=title_match,
            mode=mode,
            area=area,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            fixed_width=fixed_width,
            fixed_height=fixed_height,
            prefer_cached=True,
        )
    height, width = frame.shape[:2]
    data = _encode_png_frame(frame)

    output_dir = get_fanxiu_screenshot_frame_dir()
    with _SCREENSHOT_FRAME_LOCK:
        output_dir.mkdir(parents=True, exist_ok=True)
        if overwrite_filename:
            original_output = get_fanxiu_screenshot_path(overwrite_filename)
            output = original_output.with_suffix(".png")
            index = int(output.stem) if output.stem.isdigit() else 0
            if original_output != output and original_output.exists():
                original_output.unlink()
        else:
            index, output = _next_screenshot_frame_path(output_dir)
            while output.exists():
                index += 1
                output = output_dir / f"{index:04d}.png"
        output.write_bytes(data)

    return {
        "ok": True,
        "index": index,
        "filename": output.name,
        "path": os.fspath(output),
        "directory": os.fspath(output_dir),
        "width": width,
        "height": height,
    }


def save_fanxiu_burst_frame(
    *,
    title: str | None = None,
    title_match: str = "contains",
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
    current_frame_data_url: str | None = None,
) -> dict[str, Any]:
    frame = _decode_image_data_url_bgr(current_frame_data_url or "") if current_frame_data_url else None
    if frame is None:
        frame = capture_mumu_window_frame(
            title=title,
            title_match=title_match,
            mode=mode,
            area=area,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            fixed_width=fixed_width,
            fixed_height=fixed_height,
            prefer_cached=True,
        )
    height, width = frame.shape[:2]
    phash = _frame_phash(frame)
    data = _encode_png_frame(frame)

    output_dir = get_fanxiu_burst_frame_dir()
    with _BURST_FRAME_LOCK:
        output_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(
            (item for item in output_dir.iterdir() if item.is_file() and item.suffix.lower() in _SCREENSHOT_IMAGE_SUFFIXES),
            key=_screenshot_sort_key,
        )
        if existing:
            last = existing[-1]
            last_frame = _read_image_bgr(last)
            if last_frame is not None and _frame_phash(last_frame) == phash:
                return {
                    "ok": True,
                    "saved": False,
                    "skipped": True,
                    "reason": "same_phash",
                    "phash": phash,
                    "index": int(last.stem) if last.stem.isdigit() else 0,
                    "filename": last.name,
                    "directory": os.fspath(output_dir),
                    "width": width,
                    "height": height,
                }
        index, output = _next_numbered_frame_path(output_dir, ".png")
        while output.exists():
            index += 1
            output = output_dir / f"{index:04d}.png"
        output.write_bytes(data)

    return {
        "ok": True,
        "saved": True,
        "skipped": False,
        "reason": "",
        "phash": phash,
        "index": index,
        "filename": output.name,
        "path": os.fspath(output),
        "directory": os.fspath(output_dir),
        "width": width,
        "height": height,
    }


def list_fanxiu_burst_frames(page: int = 1, page_size: int = 24) -> dict[str, Any]:
    output_dir = get_fanxiu_burst_frame_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 24)))
    paths = sorted(
        (item for item in output_dir.iterdir() if item.is_file() and item.suffix.lower() in _SCREENSHOT_IMAGE_SUFFIXES),
        key=_screenshot_sort_key,
    )
    total = len(paths)
    start = (page - 1) * page_size
    items: list[dict[str, Any]] = []
    for path in paths[start : start + page_size]:
        stat = path.stat()
        width, height = _read_image_size(path)
        items.append(
            {
                "filename": path.name,
                "stem": path.stem,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(timespec="seconds"),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "width": width,
                "height": height,
            }
        )
    return {
        "ok": True,
        "directory": os.fspath(output_dir),
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
    }


def get_fanxiu_burst_frame_path(filename: str) -> Path:
    image_path = _burst_frame_path(filename)
    if not image_path.is_file():
        raise FileNotFoundError(f"连拍帧不存在：{filename}")
    return image_path


def clear_fanxiu_burst_frames() -> dict[str, Any]:
    output_dir = get_fanxiu_burst_frame_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    with _BURST_FRAME_LOCK:
        for path in output_dir.iterdir():
            if path.is_file() and path.suffix.lower() in _SCREENSHOT_IMAGE_SUFFIXES:
                path.unlink()
                count += 1
    return {"ok": True, "cleared": count, "directory": os.fspath(output_dir)}


def import_fanxiu_burst_frames(filenames: list[str]) -> dict[str, Any]:
    output_dir = get_fanxiu_screenshot_frame_dir()
    burst_dir = get_fanxiu_burst_frame_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    imported: list[dict[str, Any]] = []
    with _SCREENSHOT_FRAME_LOCK:
        for filename in filenames:
            source = _burst_frame_path(filename)
            if not source.is_file():
                continue
            index, output = _next_screenshot_frame_path(output_dir)
            while output.exists():
                index += 1
                output = output_dir / f"{index:04d}.png"
            output.write_bytes(source.read_bytes())
            width, height = _read_image_size(output)
            imported.append(
                {
                    "index": index,
                    "filename": output.name,
                    "source_filename": source.name,
                    "path": os.fspath(output),
                    "directory": os.fspath(output_dir),
                    "width": width,
                    "height": height,
                }
            )
    return {
        "ok": True,
        "directory": os.fspath(output_dir),
        "source_directory": os.fspath(burst_dir),
        "imported": imported,
        "imported_count": len(imported),
    }


def _normalize_match_box(box: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    try:
        x = round(float(box.get("x", 0)))
        y = round(float(box.get("y", 0)))
        w = round(float(box.get("w", 0)))
        h = round(float(box.get("h", 0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("匹配框坐标不合法") from exc
    if w <= 0 or h <= 0:
        raise ValueError("匹配框宽高必须大于 0")
    max_x = width if width > 0 else max(1, x + w)
    max_y = height if height > 0 else max(1, y + h)
    x = min(max(0, x), max(0, max_x - 1))
    y = min(max(0, y), max(0, max_y - 1))
    w = min(max(1, w), max(1, max_x - x))
    h = min(max(1, h), max(1, max_y - y))
    return {
        "name": str(box.get("name") or "").strip()[:100],
        "x": x,
        "y": y,
        "w": w,
        "h": h,
    }


def _ensure_bgr_frame(frame: Any):
    return to_bgr_frame(frame, source_format="auto")


def _crop_frame_box(frame: Any, box: dict[str, Any]):
    return frame[box["y"]:box["y"] + box["h"], box["x"]:box["x"] + box["w"]]


def _scale_box(box: dict[str, Any], source_width: int, source_height: int, target_width: int, target_height: int) -> dict[str, Any]:
    scale_x = target_width / source_width if source_width > 0 else 1.0
    scale_y = target_height / source_height if source_height > 0 else 1.0
    return _normalize_match_box(
        {
            "name": box.get("name", ""),
            "x": round(box["x"] * scale_x),
            "y": round(box["y"] * scale_y),
            "w": round(box["w"] * scale_x),
            "h": round(box["h"] * scale_y),
        },
        target_width,
        target_height,
    )


def _normalize_alpha_mask(alpha_mask: Any, width: int, height: int):
    if alpha_mask is None:
        return None
    import cv2
    import numpy as np

    mask = np.asarray(alpha_mask)
    if mask.size == 0:
        return None
    if len(mask.shape) == 3:
        mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
    if mask.shape[:2] != (height, width):
        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_LINEAR)
    return mask.astype("float32") / 255.0


def _normalize_tolerance_frames(tolerance_min: Any, tolerance_max: Any, width: int, height: int):
    if tolerance_min is None or tolerance_max is None:
        return None, None
    import cv2

    min_frame = _ensure_bgr_frame(tolerance_min)
    max_frame = _ensure_bgr_frame(tolerance_max)
    if min_frame.size == 0 or max_frame.size == 0:
        return None, None
    if min_frame.shape[:2] != (height, width):
        min_frame = cv2.resize(min_frame, (width, height), interpolation=cv2.INTER_AREA)
    if max_frame.shape[:2] != (height, width):
        max_frame = cv2.resize(max_frame, (width, height), interpolation=cv2.INTER_AREA)
    return min_frame, max_frame


def _compare_frame_crops(
    reference_crop: Any,
    current_crop: Any,
    pixel_tolerance: int = 5,
    alpha_mask: Any = None,
    tolerance_min: Any = None,
    tolerance_max: Any = None,
) -> tuple[int, float]:
    import cv2
    import numpy as np

    reference = _ensure_bgr_frame(reference_crop)
    current = _ensure_bgr_frame(current_crop)
    if reference.size == 0 or current.size == 0:
        raise ValueError("匹配图片为空")
    if reference.shape[:2] != current.shape[:2]:
        current = cv2.resize(current, (reference.shape[1], reference.shape[0]), interpolation=cv2.INTER_AREA)
    height, width = reference.shape[:2]
    mask = _normalize_alpha_mask(alpha_mask, width, height)
    min_frame, max_frame = _normalize_tolerance_frames(tolerance_min, tolerance_max, width, height)
    if (mask is None or float(mask.sum()) <= 1e-6) and (min_frame is None or max_frame is None):
        return compare_bgr_pixel_tolerance(reference, current, pixel_tolerance)
    if min_frame is not None and max_frame is not None:
        current_i = current.astype("int16")
        min_i = np.minimum(min_frame, max_frame).astype("int16")
        max_i = np.maximum(min_frame, max_frame).astype("int16")
        diff = np.maximum(min_i - current_i, current_i - max_i)
        diff = np.max(np.maximum(diff, 0), axis=2).astype("float32")
    else:
        diff = np.max(np.abs(reference.astype("int16") - current.astype("int16")), axis=2).astype("float32")
    matched = (diff <= max(0, min(255, int(pixel_tolerance)))).astype("float32")
    if mask is None:
        mask = np.ones((height, width), dtype="float32")
    score = float((matched * mask).sum() / mask.sum())
    return int(round(score * 100)), score


def _encode_bgra_data_url(image: Any) -> str:
    import base64
    import cv2

    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        return ""
    return "data:image/png;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")


def _build_frame_crop_match_debug(
    reference_crop: Any,
    current_crop: Any,
    pixel_tolerance: int = 5,
    alpha_mask: Any = None,
    tolerance_min: Any = None,
    tolerance_max: Any = None,
) -> dict[str, Any]:
    import cv2
    import numpy as np

    reference = _ensure_bgr_frame(reference_crop)
    current = _ensure_bgr_frame(current_crop)
    if reference.size == 0 or current.size == 0:
        return {}
    if reference.shape[:2] != current.shape[:2]:
        current = cv2.resize(current, (reference.shape[1], reference.shape[0]), interpolation=cv2.INTER_AREA)
    height, width = reference.shape[:2]
    mask = _normalize_alpha_mask(alpha_mask, width, height)
    min_frame, max_frame = _normalize_tolerance_frames(tolerance_min, tolerance_max, width, height)
    if mask is None:
        mask = np.ones((height, width), dtype="float32")
    effective = mask > (1.0 / 255.0)
    effective_count = int(effective.sum())
    if effective_count <= 0:
        return {
            "width": width,
            "height": height,
            "pixel_tolerance": max(0, min(255, int(pixel_tolerance))),
            "effective_pixel_count": 0,
            "matched_pixel_count": 0,
            "unmatched_pixel_count": 0,
            "score": 0.0,
            "similarity": 0,
            "mask_coverage": 0.0,
        }

    if min_frame is not None and max_frame is not None:
        current_i = current.astype("int16")
        min_i = np.minimum(min_frame, max_frame).astype("int16")
        max_i = np.maximum(min_frame, max_frame).astype("int16")
        diff = np.maximum(min_i - current_i, current_i - max_i)
        diff = np.max(np.maximum(diff, 0), axis=2).astype("float32")
    else:
        diff = np.max(np.abs(reference.astype("int16") - current.astype("int16")), axis=2).astype("float32")
    tolerance = max(0, min(255, int(pixel_tolerance)))
    matched = (diff <= tolerance) & effective
    matched_count = int(matched.sum())
    unmatched_count = int(effective_count - matched_count)
    score = float((matched.astype("float32") * mask).sum() / max(float(mask[effective].sum()), 1e-6))

    alpha = np.clip(mask * 255.0, 0, 255).astype("uint8")
    reference_masked = cv2.cvtColor(reference, cv2.COLOR_BGR2BGRA)
    current_masked = cv2.cvtColor(current, cv2.COLOR_BGR2BGRA)
    reference_masked[:, :, 3] = alpha
    current_masked[:, :, 3] = alpha

    heatmap = np.zeros((height, width, 4), dtype="uint8")
    heatmap[matched] = [64, 180, 64, 96]
    mismatch = effective & ~matched
    mismatch_strength = np.clip(diff, 0, 255).astype("uint8")
    heatmap[mismatch, 0] = 0
    heatmap[mismatch, 1] = np.maximum(32, 255 - mismatch_strength[mismatch] // 2)
    heatmap[mismatch, 2] = 255
    heatmap[mismatch, 3] = 220

    return {
        "width": width,
        "height": height,
        "pixel_tolerance": tolerance,
        "effective_pixel_count": effective_count,
        "matched_pixel_count": matched_count,
        "unmatched_pixel_count": unmatched_count,
        "score": score,
        "similarity": int(round(score * 100)),
        "mask_coverage": round(effective_count / float(width * height), 6),
        "reference_masked_data_url": _encode_bgra_data_url(reference_masked),
        "current_masked_data_url": _encode_bgra_data_url(current_masked),
        "mismatch_heatmap_data_url": _encode_bgra_data_url(heatmap),
    }


def _jpeg_normalize_frame(frame: Any, quality: int) -> tuple[Any, bytes]:
    return normalize_for_saved_jpeg_match(frame, quality=quality, source_format="auto", return_bytes=True)


def _prepare_current_frame_for_reference(
    frame: Any,
    reference_path: Path,
    quality: int,
    *,
    encode_frame: bool = True,
) -> tuple[Any, bytes, str]:
    if reference_path.suffix.lower() in {".jpg", ".jpeg"}:
        normalized_frame, data = _jpeg_normalize_frame(frame, quality)
        return normalized_frame, data if encode_frame else b"", ".jpg"
    normalized_frame = _ensure_bgr_frame(frame)
    return normalized_frame, _encode_png_frame(normalized_frame) if encode_frame else b"", ".png"


def _encode_png_frame(frame: Any) -> bytes:
    import cv2

    ok, data = cv2.imencode(".png", _ensure_bgr_frame(frame), [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise RuntimeError("编码 PNG 失败")
    return data.tobytes()


def _normalize_ocr_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _ocr_label_payload(label: Any) -> dict[str, Any]:
    if isinstance(label, dict):
        return label
    if isinstance(label, str):
        try:
            payload = json.loads(label)
        except json.JSONDecodeError:
            return {"text": label}
        return payload if isinstance(payload, dict) else {"text": label}
    return {"text": str(label or "")}


def _ocr_shape_box(shape: dict[str, Any], offset_x: int, offset_y: int, frame_width: int, frame_height: int) -> dict[str, Any] | None:
    points = shape.get("points")
    if not isinstance(points, list) or not points:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        if not isinstance(point, list) or len(point) < 2:
            continue
        try:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
        except (TypeError, ValueError):
            continue
    if not xs or not ys:
        return None
    return _normalize_match_box(
        {
            "name": "ocr",
            "x": offset_x + min(xs),
            "y": offset_y + min(ys),
            "w": max(1.0, max(xs) - min(xs)),
            "h": max(1.0, max(ys) - min(ys)),
        },
        frame_width,
        frame_height,
    )


def _ocr_text_matches(text: str, target: str, mode: str) -> bool:
    if not target:
        return False
    if mode == "regex":
        try:
            return re.search(target, text) is not None
        except re.error:
            return False
    normalized_text = _normalize_ocr_text(text)
    normalized_target = _normalize_ocr_text(target)
    if mode == "wildcard":
        pattern = "".join(
            "." if char == "?" else ".*" if char == "*" else re.escape(char)
            for char in normalized_target
        )
        return re.fullmatch(pattern, normalized_text) is not None
    if mode == "exact":
        return normalized_text == normalized_target
    return normalized_target in normalized_text


def _apply_alpha_mask_for_ocr(crop: Any, alpha_mask: Any):
    if alpha_mask is None:
        return crop
    import numpy as np

    frame = _ensure_bgr_frame(crop).copy()
    height, width = frame.shape[:2]
    mask = _normalize_alpha_mask(alpha_mask, width, height)
    if mask is None or float(mask.sum()) <= 1e-6:
        return frame
    frame[mask <= 0.05] = np.array([255, 255, 255], dtype=frame.dtype)
    return frame


_OCR_FRAME_CACHE_TTL = 10.0
_OCR_FRAME_CACHE_MAX_SIZE = 128
_OCR_FRAME_CACHE_LOCK = threading.Lock()
_OCR_FRAME_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_OCR_FRAME_INFLIGHT: dict[str, threading.Event] = {}


def _ocr_frame_cache_key(frame: Any) -> str:
    import cv2
    import numpy as np

    bgr = _ensure_bgr_frame(frame)
    height, width = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    scale = min(1.0, 48.0 / max(1, max(width, height)))
    small_width = max(1, int(round(width * scale)))
    small_height = max(1, int(round(height * scale)))
    small = cv2.resize(gray, (small_width, small_height), interpolation=cv2.INTER_AREA)
    quantized = (small // 16).astype(np.uint8)
    raw = f"{width}x{height}:{small_width}x{small_height}:".encode("ascii") + quantized.tobytes()
    return hashlib.sha256(raw).hexdigest()


def _clone_json_payload(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def _get_ocr_frame_cache(cache_key: str) -> dict[str, Any] | None:
    now = time.monotonic()
    with _OCR_FRAME_CACHE_LOCK:
        cached = _OCR_FRAME_CACHE.get(cache_key)
        if cached is None:
            return None
        cached_at, result = cached
        if now - cached_at > _OCR_FRAME_CACHE_TTL:
            _OCR_FRAME_CACHE.pop(cache_key, None)
            return None
        return _clone_json_payload(result)


def _set_ocr_frame_cache(cache_key: str, result: dict[str, Any]) -> None:
    now = time.monotonic()
    cloned = _clone_json_payload(result)
    with _OCR_FRAME_CACHE_LOCK:
        expired_keys = [
            key for key, (cached_at, _) in _OCR_FRAME_CACHE.items()
            if now - cached_at > _OCR_FRAME_CACHE_TTL
        ]
        for key in expired_keys:
            _OCR_FRAME_CACHE.pop(key, None)
        while len(_OCR_FRAME_CACHE) >= _OCR_FRAME_CACHE_MAX_SIZE:
            oldest_key = min(_OCR_FRAME_CACHE, key=lambda key: _OCR_FRAME_CACHE[key][0])
            _OCR_FRAME_CACHE.pop(oldest_key, None)
        _OCR_FRAME_CACHE[cache_key] = (now, cloned)


def _run_ocr_on_bgr_frame(frame: Any) -> dict[str, Any]:
    encoded_frame = _encode_png_frame(frame)
    cache_key = _ocr_frame_cache_key(frame)
    cached = _get_ocr_frame_cache(cache_key)
    if cached is not None:
        return cached

    owner = False
    with _OCR_FRAME_CACHE_LOCK:
        inflight = _OCR_FRAME_INFLIGHT.get(cache_key)
        if inflight is None:
            inflight = threading.Event()
            _OCR_FRAME_INFLIGHT[cache_key] = inflight
            owner = True
    if not owner:
        if inflight.wait(timeout=2.0):
            cached = _get_ocr_frame_cache(cache_key)
            if cached is not None:
                return cached

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_file.write(encoded_frame)
            temp_path = temp_file.name
        result = run_paddle_ocr_preview(Path(temp_path), shape_type="rectangle")
        _set_ocr_frame_cache(cache_key, result)
        return result
    except OcrPreviewError as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        if owner:
            with _OCR_FRAME_CACHE_LOCK:
                _OCR_FRAME_INFLIGHT.pop(cache_key, None)
            inflight.set()
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _match_ocr_frame(
    current_frame: Any,
    current_box: dict[str, Any],
    current_search_box: dict[str, Any],
    *,
    scan: bool,
    text: str,
    match_mode: str = "contains",
    min_confidence: float = 0.0,
    alpha_mask: Any = None,
) -> dict[str, Any]:
    expected_text = str(text or "")
    frame = _ensure_bgr_frame(current_frame)
    frame_height, frame_width = frame.shape[:2]
    target_box = _normalize_match_box(current_search_box if scan else current_box, frame_width, frame_height)
    x, y, w, h = int(target_box["x"]), int(target_box["y"]), int(target_box["w"]), int(target_box["h"])
    crop = frame[y:y + h, x:x + w]
    if crop.size == 0:
        return {
            "box": current_box,
            "similarity": 0,
            "score": 0.0,
            "crop_similarity": 0,
            "crop_score": 0.0,
            "search_radius": -1 if scan else 0,
            "matches": [],
            "ocr_text": "",
        }
    ocr_crop = _apply_alpha_mask_for_ocr(crop, alpha_mask) if not scan else crop
    document = _run_ocr_on_bgr_frame(ocr_crop).get("document") or {}
    raw_shapes = document.get("shapes") if isinstance(document, dict) else []
    matches: list[dict[str, Any]] = []
    all_texts: list[str] = []
    min_score = max(0.0, min(1.0, float(min_confidence or 0.0)))
    for shape in raw_shapes if isinstance(raw_shapes, list) else []:
        if not isinstance(shape, dict):
            continue
        label = _ocr_label_payload(shape.get("label"))
        recognized = str(label.get("text") or "")
        all_texts.append(recognized)
        confidence = float(label.get("score") or 0.0)
        if confidence < min_score or not _ocr_text_matches(recognized, expected_text, match_mode):
            continue
        absolute_box = _ocr_shape_box(shape, x, y, frame_width, frame_height) or current_box
        similarity = int(round(confidence * 100))
        matches.append(
            {
                "box": absolute_box,
                "similarity": similarity,
                "score": confidence,
                "crop_similarity": similarity,
                "crop_score": confidence,
                "ocr_text": recognized,
                "ocr_confidence": confidence,
            }
        )
    if not matches and _ocr_text_matches("".join(all_texts), expected_text, match_mode):
        matches.append(
            {
                "box": current_box,
                "similarity": 100,
                "score": 1.0,
                "crop_similarity": 100,
                "crop_score": 1.0,
                "ocr_text": "".join(all_texts),
                "ocr_confidence": 1.0,
            }
        )
    matches.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    best = matches[0] if matches else None
    return {
        "box": best["box"] if best else current_box,
        "similarity": int(best["similarity"]) if best else 0,
        "score": float(best["score"]) if best else 0.0,
        "crop_similarity": int(best["crop_similarity"]) if best else 0,
        "crop_score": float(best["crop_score"]) if best else 0.0,
        "search_radius": -1 if scan else 0,
        "matches": matches[:50],
        "ocr_text": "".join(all_texts),
    }


def _match_local_pixel_frame(
    reference_crop: Any,
    current_frame: Any,
    current_box: dict[str, Any],
    pixel_tolerance: int = 5,
    alpha_mask: Any = None,
    tolerance_min: Any = None,
    tolerance_max: Any = None,
    search_radius: int | None = None,
) -> dict[str, Any]:
    frame = _ensure_bgr_frame(current_frame)
    frame_height, frame_width = frame.shape[:2]
    template = _ensure_bgr_frame(reference_crop)
    template_height, template_width = template.shape[:2]
    radius = max(0, min(64, int(search_radius or 0)))
    if radius <= 0:
        similarity, score = _compare_frame_crops(
            template,
            _crop_frame_box(frame, current_box),
            pixel_tolerance,
            alpha_mask,
            tolerance_min,
            tolerance_max,
        )
        return {
            "box": current_box,
            "similarity": similarity,
            "score": score,
            "crop_similarity": similarity,
            "crop_score": score,
            "search_radius": 0,
        }
    x1 = max(0, int(current_box["x"]) - radius)
    y1 = max(0, int(current_box["y"]) - radius)
    x2 = min(frame_width - template_width, int(current_box["x"]) + radius)
    y2 = min(frame_height - template_height, int(current_box["y"]) + radius)
    if x2 < x1 or y2 < y1:
        similarity, score = _compare_frame_crops(
            template,
            _crop_frame_box(frame, current_box),
            pixel_tolerance,
            alpha_mask,
            tolerance_min,
            tolerance_max,
        )
        return {
            "box": current_box,
            "similarity": similarity,
            "score": score,
            "crop_similarity": similarity,
            "crop_score": score,
            "search_radius": radius,
        }

    best_box = current_box
    best_similarity = -1
    best_score = -1.0
    for y in range(y1, y2 + 1):
        for x in range(x1, x2 + 1):
            candidate_box = _normalize_match_box(
                {
                    "name": current_box.get("name", ""),
                    "x": x,
                    "y": y,
                    "w": template_width,
                    "h": template_height,
                },
                frame_width,
                frame_height,
            )
            similarity, score = _compare_frame_crops(
                template,
                _crop_frame_box(frame, candidate_box),
                pixel_tolerance,
                alpha_mask,
                tolerance_min,
                tolerance_max,
            )
            if score > best_score:
                best_box = candidate_box
                best_similarity = similarity
                best_score = score
    return {
        "box": best_box,
        "similarity": best_similarity,
        "score": best_score,
        "crop_similarity": best_similarity,
        "crop_score": best_score,
        "search_radius": radius,
    }


def _match_scan_frame(
    reference_crop: Any,
    current_frame: Any,
    current_search_box: dict[str, Any],
    current_box: dict[str, Any],
    pixel_tolerance: int = 5,
    alpha_mask: Any = None,
    tolerance_min: Any = None,
    tolerance_max: Any = None,
) -> dict[str, Any]:
    frame = _ensure_bgr_frame(current_frame)
    frame_height, frame_width = frame.shape[:2]
    search_box = _normalize_match_box(current_search_box, frame_width, frame_height)
    template = _ensure_bgr_frame(reference_crop)
    template_height, template_width = template.shape[:2]
    sx, sy, sw, sh = int(search_box["x"]), int(search_box["y"]), int(search_box["w"]), int(search_box["h"])
    search_crop = frame[sy : sy + sh, sx : sx + sw]
    if search_crop.size == 0:
        return {
            "box": current_box,
            "similarity": 0,
            "score": 0.0,
            "crop_similarity": 0,
            "crop_score": 0.0,
            "search_radius": -1,
        }

    scan_template = template
    template_offset_x = 0
    template_offset_y = 0
    mask = _normalize_alpha_mask(alpha_mask, template_width, template_height)
    if mask is not None and float(mask.sum()) > 1e-6:
        import numpy as np

        ys, xs = np.where(mask > 0.05)
        if xs.size and ys.size:
            left = max(0, int(xs.min()))
            top = max(0, int(ys.min()))
            right = min(template_width, int(xs.max()) + 1)
            bottom = min(template_height, int(ys.max()) + 1)
            if right > left and bottom > top:
                scan_template = template[top:bottom, left:right]
                template_offset_x = left
                template_offset_y = top

    import cv2
    import numpy as np

    def scan_template_candidates(template: Any, haystack: Any, confidence: float = 0.5) -> list[tuple[int, int, int, int, float]]:
        template_frame = _ensure_bgr_frame(template)
        haystack_frame = _ensure_bgr_frame(haystack)
        if template_frame.size == 0 or haystack_frame.size == 0:
            return []
        if template_frame.shape[0] > haystack_frame.shape[0] or template_frame.shape[1] > haystack_frame.shape[1]:
            return []
        template_gray = cv2.cvtColor(template_frame, cv2.COLOR_BGR2GRAY)
        haystack_gray = cv2.cvtColor(haystack_frame, cv2.COLOR_BGR2GRAY)
        if float(np.std(template_gray)) < 1e-6:
            return []
        result = cv2.matchTemplate(haystack_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= float(confidence))
        candidates = [
            (int(x), int(y), int(template_frame.shape[1]), int(template_frame.shape[0]), float(result[int(y), int(x)]))
            for y, x in zip(ys, xs)
        ]
        candidates.sort(key=lambda item: item[4], reverse=True)
        selected: list[tuple[int, int, int, int, float]] = []
        for candidate in candidates:
            cx, cy, cw, ch, _score = candidate
            keep = True
            for sx0, sy0, sw0, sh0, _ in selected:
                ix1 = max(cx, sx0)
                iy1 = max(cy, sy0)
                ix2 = min(cx + cw, sx0 + sw0)
                iy2 = min(cy + ch, sy0 + sh0)
                inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
                union = cw * ch + sw0 * sh0 - inter
                if union > 0 and inter / union >= 0.5:
                    keep = False
                    break
            if keep:
                selected.append(candidate)
                if len(selected) >= 50:
                    break
        return selected

    rects = scan_template_candidates(scan_template, search_crop, confidence=0.75)
    best_box = current_box
    best_similarity = 0
    best_score = 0.0
    matches: list[dict[str, Any]] = []
    for rect in rects:
        x, y, _w, _h = [int(round(v)) for v in rect[:4]]
        template_score = max(0.0, min(1.0, float(rect[4]) if len(rect) > 4 else 0.0))
        full_x = sx + x - template_offset_x
        full_y = sy + y - template_offset_y
        if full_x < 0 or full_y < 0 or full_x + template_width > frame_width or full_y + template_height > frame_height:
            continue
        candidate_box = _normalize_match_box(
            {
                "name": current_box.get("name", ""),
                "x": full_x,
                "y": full_y,
                "w": template_width,
                "h": template_height,
            },
            frame_width,
            frame_height,
        )
        similarity, score = _compare_frame_crops(
            template,
            _crop_frame_box(frame, candidate_box),
            pixel_tolerance,
            alpha_mask,
            tolerance_min,
            tolerance_max,
        )
        if template_score > score:
            score = template_score
            similarity = int(round(score * 100))
        matches.append(
            {
                "box": candidate_box,
                "similarity": similarity,
                "score": score,
                "crop_similarity": similarity,
                "crop_score": score,
            }
        )
        if score > best_score:
            best_box = candidate_box
            best_similarity = similarity
            best_score = score
    matches.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return {
        "box": best_box,
        "similarity": best_similarity,
        "score": best_score,
        "crop_similarity": best_similarity,
        "crop_score": best_score,
        "search_radius": -1,
        "matches": matches[:50],
    }


def match_fanxiu_screenshot_box_frame(
    *,
    filename: str,
    entry_id: str | None = None,
    box: dict[str, Any],
    scan: bool = False,
    scan_box: dict[str, Any] | None = None,
    title: str | None = None,
    title_match: str = "contains",
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
    quality: int = 82,
    pixel_tolerance: int = 5,
    alpha_mask_data_url: str | None = None,
    tolerance_min_data_url: str | None = None,
    tolerance_max_data_url: str | None = None,
    current_frame_data_url: str | None = None,
    prefer_cached: bool = True,
    match_strategy: str = "auto",
    match_search_radius: int | None = None,
    ocr_enabled: bool = False,
    ocr_text: str | None = None,
    ocr_match_mode: str = "contains",
    ocr_min_confidence: float = 0.0,
    debug_match: bool = False,
    save_match_frame: bool = True,
) -> dict[str, Any]:
    source_asset = resolve_data_annotation_image_asset(filename, entry_id=entry_id)
    if not source_asset.exists:
        raise FileNotFoundError(f"data-annotation 图片不存在：{source_asset.path}")
    source_path = source_asset.path
    reference_frame = _read_image_bgr(source_path)
    if reference_frame is None:
        raise RuntimeError(f"读取截图失败：{source_path.name}")
    source_height, source_width = reference_frame.shape[:2]
    source_box = _normalize_match_box(box, source_width, source_height)
    reference_crop = _crop_frame_box(reference_frame, source_box)
    alpha_mask = _decode_image_data_url_gray(alpha_mask_data_url or "") if alpha_mask_data_url else None
    tolerance_min = _decode_image_data_url_bgr(tolerance_min_data_url or "") if tolerance_min_data_url else None
    tolerance_max = _decode_image_data_url_bgr(tolerance_max_data_url or "") if tolerance_max_data_url else None

    current_frame = _decode_image_data_url_bgr(current_frame_data_url or "") if current_frame_data_url else None
    if current_frame is None:
        current_frame = capture_mumu_window_frame(
            title=title,
            title_match=title_match,
            mode=mode,
            area=area,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            fixed_width=fixed_width,
            fixed_height=fixed_height,
            prefer_cached=prefer_cached,
        )
    current_frame, data, match_frame_suffix = _prepare_current_frame_for_reference(
        current_frame,
        source_path,
        quality,
        encode_frame=bool(save_match_frame),
    )
    current_height, current_width = current_frame.shape[:2]
    current_box = _scale_box(source_box, source_width, source_height, current_width, current_height)
    current_crop = _crop_frame_box(current_frame, current_box)
    source_scan_box = _normalize_match_box(scan_box, source_width, source_height) if scan_box else {
        "name": "scan",
        "x": 0,
        "y": 0,
        "w": source_width,
        "h": source_height,
    }
    current_scan_box = _scale_box(source_scan_box, source_width, source_height, current_width, current_height)
    use_ocr = bool(ocr_enabled and str(ocr_text or "").strip())
    if use_ocr:
        fixed_match = _match_ocr_frame(
            current_frame,
            current_box,
            current_scan_box,
            scan=bool(scan),
            text=str(ocr_text or ""),
            match_mode=ocr_match_mode if ocr_match_mode in {"contains", "exact", "wildcard", "regex"} else "contains",
            min_confidence=ocr_min_confidence,
            alpha_mask=alpha_mask,
        )
        index = 0
        output_dir = get_fanxiu_match_frame_dir()
        output: Path | None = None
        if save_match_frame:
            index, output = _save_limited_match_frame(data, match_frame_suffix)

        return {
            "ok": True,
            "index": index,
            "source_filename": source_path.name,
            "match_filename": output.name if output is not None else "",
            "path": os.fspath(output) if output is not None else "",
            "directory": os.fspath(output_dir),
            "similarity": fixed_match["similarity"],
            "score": fixed_match["score"],
            "fixed_similarity": fixed_match["similarity"],
            "fixed_score": fixed_match["score"],
            "fixed_pixel_similarity": fixed_match["similarity"],
            "fixed_pixel_score": fixed_match["score"],
            "fixed_exact_similarity": fixed_match["similarity"],
            "fixed_exact_score": fixed_match["score"],
            "fixed_exact_pixel_similarity": fixed_match["similarity"],
            "fixed_exact_pixel_score": fixed_match["score"],
            "fixed_search_radius": fixed_match["search_radius"],
            "box": source_box,
            "scan": bool(scan),
            "scan_box": source_scan_box if scan else None,
            "current_box": current_box,
            "fixed_box": fixed_match["box"],
            "matches": fixed_match.get("matches") or [],
            "source_width": source_width,
            "source_height": source_height,
            "width": current_width,
            "height": current_height,
            "pixel_tolerance": max(0, min(255, int(pixel_tolerance if pixel_tolerance is not None else 5))),
            "match_strategy": "ocr",
            "ocr_text": fixed_match.get("ocr_text") or "",
            "ocr_target": str(ocr_text or ""),
            "ocr_match_mode": ocr_match_mode,
            "ocr_min_confidence": max(0.0, min(1.0, float(ocr_min_confidence or 0.0))),
        }
    if match_strategy == "anchor_pixel":
        similarity, score = _compare_frame_crops(
            reference_crop,
            current_crop,
            pixel_tolerance,
            alpha_mask,
            tolerance_min,
            tolerance_max,
        )
        result = {
            "ok": True,
            "index": 0,
            "source_filename": source_path.name,
            "match_filename": "",
            "path": "",
            "directory": "",
            "similarity": similarity,
            "score": score,
            "fixed_similarity": similarity,
            "fixed_score": score,
            "fixed_pixel_similarity": similarity,
            "fixed_pixel_score": score,
            "fixed_exact_similarity": similarity,
            "fixed_exact_score": score,
            "fixed_exact_pixel_similarity": similarity,
            "fixed_exact_pixel_score": score,
            "fixed_search_radius": 0,
            "box": source_box,
            "current_box": current_box,
            "fixed_box": current_box,
            "source_width": source_width,
            "source_height": source_height,
            "width": current_width,
            "height": current_height,
            "pixel_tolerance": max(0, min(255, int(pixel_tolerance if pixel_tolerance is not None else 5))),
            "match_strategy": "anchor_pixel",
        }
        if debug_match:
            result["match_debug"] = _build_frame_crop_match_debug(
                reference_crop,
                current_crop,
                pixel_tolerance,
                alpha_mask,
                tolerance_min,
                tolerance_max,
            )
        return result
    fixed_exact_pixel_similarity, fixed_exact_pixel_score = _compare_frame_crops(
        reference_crop,
        current_crop,
        pixel_tolerance,
        alpha_mask,
        tolerance_min,
        tolerance_max,
    )
    if scan:
        fixed_match = _match_scan_frame(
            reference_crop,
            current_frame,
            current_scan_box,
            current_box,
            pixel_tolerance,
            alpha_mask,
            tolerance_min,
            tolerance_max,
        )
    else:
        fixed_match = _match_local_pixel_frame(
            reference_crop,
            current_frame,
            current_box,
            pixel_tolerance,
            alpha_mask,
            tolerance_min,
            tolerance_max,
            match_search_radius,
        )

    index = 0
    output_dir = get_fanxiu_match_frame_dir()
    output: Path | None = None
    if save_match_frame:
        index, output = _save_limited_match_frame(data, match_frame_suffix)

    result = {
        "ok": True,
        "index": index,
        "source_filename": source_path.name,
        "match_filename": output.name if output is not None else "",
        "path": os.fspath(output) if output is not None else "",
        "directory": os.fspath(output_dir),
        "similarity": fixed_match["similarity"],
        "score": fixed_match["score"],
        "fixed_similarity": fixed_match["similarity"],
        "fixed_score": fixed_match["score"],
        "fixed_pixel_similarity": fixed_match["crop_similarity"],
        "fixed_pixel_score": fixed_match["crop_score"],
        "fixed_exact_similarity": fixed_exact_pixel_similarity,
        "fixed_exact_score": fixed_exact_pixel_score,
        "fixed_exact_pixel_similarity": fixed_exact_pixel_similarity,
        "fixed_exact_pixel_score": fixed_exact_pixel_score,
        "fixed_search_radius": fixed_match["search_radius"],
        "box": source_box,
        "scan": bool(scan),
        "scan_box": source_scan_box if scan else None,
        "current_box": current_box,
        "fixed_box": fixed_match["box"],
        "matches": fixed_match.get("matches") or [
            {
                "box": fixed_match["box"],
                "similarity": fixed_match["crop_similarity"],
                "score": fixed_match["crop_score"],
                "crop_similarity": fixed_match["crop_similarity"],
                "crop_score": fixed_match["crop_score"],
            }
        ],
        "source_width": source_width,
        "source_height": source_height,
        "width": current_width,
        "height": current_height,
        "pixel_tolerance": max(0, min(255, int(pixel_tolerance if pixel_tolerance is not None else 5))),
    }
    if debug_match:
        debug_box = fixed_match.get("box") or current_box
        debug_crop = _crop_frame_box(current_frame, debug_box)
        result["match_debug"] = _build_frame_crop_match_debug(
            reference_crop,
            debug_crop,
            pixel_tolerance,
            alpha_mask,
            tolerance_min,
            tolerance_max,
        )
    return result


def click_mumu_window_processed_point(
    *,
    x: float,
    y: float,
    title: str | None = None,
    title_match: str = "contains",
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
    frame_width: int | None = None,
    frame_height: int | None = None,
    input_backend: str = "adb",
) -> dict[str, Any]:
    resolved_fixed_width = int(
        fixed_width if fixed_width is not None else os.getenv("CODEYUN_FANXIU_MUMU_FIXED_WIDTH", DEFAULT_FIXED_WIDTH)
    )
    resolved_fixed_height = int(
        fixed_height if fixed_height is not None else os.getenv("CODEYUN_FANXIU_MUMU_FIXED_HEIGHT", DEFAULT_FIXED_HEIGHT)
    )
    normalized_title = (title or get_target_title()).strip() or get_target_title()
    frame_x = int(round(x))
    frame_y = int(round(y))
    normalized_input_backend = str(input_backend or "adb").strip().lower()
    if normalized_input_backend not in {"adb", "desktop"}:
        raise RuntimeError(f"不支持的输入后端：{input_backend!r}")
    adb_input_error = ""
    if normalized_input_backend == "adb":
        input_result = _tap_mumu_adb(
            frame_x,
            frame_y,
            frame_width=frame_width or resolved_fixed_width or None,
            frame_height=frame_height or resolved_fixed_height or None,
        )
        return {
            "ok": True,
            "title": normalized_title,
            **input_result,
            "frame_x": frame_x,
            "frame_y": frame_y,
            "frame_width": frame_width,
            "frame_height": frame_height,
        }

    target, capturer, raw_frame, frame, resolved_area, resolved_mode, resolved_crop, resolved_trim_border, resolved_rotate = (
        _processed_window_target(
            normalized_title=normalized_title,
            title_match=title_match,
            area=area,
            mode=mode,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            resolved_fixed_width=resolved_fixed_width,
            resolved_fixed_height=resolved_fixed_height,
        )
    )
    frame_height, frame_width = frame.shape[:2]
    if not (0 <= frame_x < frame_width and 0 <= frame_y < frame_height):
        raise RuntimeError(f"点击坐标超出画面范围：({frame_x}, {frame_y}) / {frame_width}x{frame_height}")

    raw_point = map_processed_point_to_raw_point(
        (frame_x, frame_y),
        raw_shape=raw_frame.shape,
        crop=resolved_crop,
        trim_border=resolved_trim_border,
        rotate=resolved_rotate,
        fixed_width=resolved_fixed_width,
        fixed_height=resolved_fixed_height,
    )
    if raw_point is None:
        raise RuntimeError("点击坐标无法映射到原始窗口坐标")

    input_result: dict[str, Any] = {"input": "window"}
    if adb_input_error:
        input_result["adb_input_error"] = adb_input_error
    try:
        click_window_raw_point(capturer.hwnd, resolved_area, raw_point)
    except Exception as exc:
        if adb_input_error or normalized_input_backend == "desktop" or (
            "mumu" not in normalized_title.lower() and "mumu" not in target.title.lower()
        ):
            raise
        input_result = _tap_mumu_adb(frame_x, frame_y, frame_width=frame_width, frame_height=frame_height)
        input_result["window_input_error"] = str(exc)
    return {
        "ok": True,
        "title": normalized_title,
        "hwnd": capturer.hwnd,
        **input_result,
        "frame_x": frame_x,
        "frame_y": frame_y,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "raw_x": raw_point[0],
        "raw_y": raw_point[1],
        "area": resolved_area,
        "mode": resolved_mode,
        "rotate": resolved_rotate,
    }


def activate_mumu_window(
    *,
    title: str | None = None,
    title_match: str = "contains",
    click_title: bool = True,
) -> dict[str, Any]:
    ensure_windows_runtime()
    set_dpi_awareness()

    normalized_title = (title or get_target_title()).strip() or get_target_title()
    target, _refind_title, _refind_match = _find_mumu_window_candidate(normalized_title, title_match)
    point: tuple[int, int] | None = None
    if click_title:
        point = click_window_title_bar(target.hwnd)
    else:
        activate_window(target.hwnd)
        time.sleep(0.03)
    return {
        "ok": True,
        "title": normalized_title,
        "hwnd": target.hwnd,
        "window_title": target.title,
        "clicked_title": bool(click_title),
        "screen_x": point[0] if point is not None else None,
        "screen_y": point[1] if point is not None else None,
    }


def drag_mumu_window_processed_points(
    *,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    duration_ms: int = 300,
    title: str | None = None,
    title_match: str = "contains",
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
    frame_width: int | None = None,
    frame_height: int | None = None,
    input_backend: str = "adb",
) -> dict[str, Any]:
    resolved_fixed_width = int(
        fixed_width if fixed_width is not None else os.getenv("CODEYUN_FANXIU_MUMU_FIXED_WIDTH", DEFAULT_FIXED_WIDTH)
    )
    resolved_fixed_height = int(
        fixed_height if fixed_height is not None else os.getenv("CODEYUN_FANXIU_MUMU_FIXED_HEIGHT", DEFAULT_FIXED_HEIGHT)
    )
    normalized_title = (title or get_target_title()).strip() or get_target_title()
    frame_start_x = int(round(start_x))
    frame_start_y = int(round(start_y))
    frame_end_x = int(round(end_x))
    frame_end_y = int(round(end_y))
    normalized_input_backend = str(input_backend or "adb").strip().lower()
    if normalized_input_backend not in {"adb", "desktop"}:
        raise RuntimeError(f"不支持的输入后端：{input_backend!r}")
    adb_input_error = ""
    if normalized_input_backend == "adb":
        input_result = _swipe_mumu_adb(
            frame_start_x,
            frame_start_y,
            frame_end_x,
            frame_end_y,
            duration_ms=duration_ms,
            frame_width=frame_width or resolved_fixed_width or None,
            frame_height=frame_height or resolved_fixed_height or None,
        )
        return {
            "ok": True,
            "title": normalized_title,
            **input_result,
            "frame_start_x": frame_start_x,
            "frame_start_y": frame_start_y,
            "frame_end_x": frame_end_x,
            "frame_end_y": frame_end_y,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "duration_ms": duration_ms,
        }

    target, capturer, raw_frame, frame, resolved_area, resolved_mode, resolved_crop, resolved_trim_border, resolved_rotate = (
        _processed_window_target(
            normalized_title=normalized_title,
            title_match=title_match,
            area=area,
            mode=mode,
            crop=crop,
            trim_border=trim_border,
            rotate=rotate,
            resolved_fixed_width=resolved_fixed_width,
            resolved_fixed_height=resolved_fixed_height,
        )
    )
    frame_height, frame_width = frame.shape[:2]
    for label, point_x, point_y in (
        ("起点", frame_start_x, frame_start_y),
        ("终点", frame_end_x, frame_end_y),
    ):
        if not (0 <= point_x < frame_width and 0 <= point_y < frame_height):
            raise RuntimeError(f"拖拽{label}坐标超出画面范围：({point_x}, {point_y}) / {frame_width}x{frame_height}")

    start_raw_point = map_processed_point_to_raw_point(
        (frame_start_x, frame_start_y),
        raw_shape=raw_frame.shape,
        crop=resolved_crop,
        trim_border=resolved_trim_border,
        rotate=resolved_rotate,
        fixed_width=resolved_fixed_width,
        fixed_height=resolved_fixed_height,
    )
    end_raw_point = map_processed_point_to_raw_point(
        (frame_end_x, frame_end_y),
        raw_shape=raw_frame.shape,
        crop=resolved_crop,
        trim_border=resolved_trim_border,
        rotate=resolved_rotate,
        fixed_width=resolved_fixed_width,
        fixed_height=resolved_fixed_height,
    )
    if start_raw_point is None or end_raw_point is None:
        raise RuntimeError("拖拽坐标无法映射到原始窗口坐标")

    input_result: dict[str, Any] = {"input": "window"}
    if adb_input_error:
        input_result["adb_input_error"] = adb_input_error
    try:
        drag_window_raw_points(capturer.hwnd, resolved_area, start_raw_point, end_raw_point, duration_ms=duration_ms)
    except Exception as exc:
        if adb_input_error or normalized_input_backend == "desktop" or not _is_mumu_target(normalized_title, target.title):
            raise
        input_result = _swipe_mumu_adb(
            frame_start_x,
            frame_start_y,
            frame_end_x,
            frame_end_y,
            duration_ms=duration_ms,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        input_result["window_input_error"] = str(exc)
    return {
        "ok": True,
        "title": normalized_title,
        "hwnd": capturer.hwnd,
        **input_result,
        "frame_start_x": frame_start_x,
        "frame_start_y": frame_start_y,
        "frame_end_x": frame_end_x,
        "frame_end_y": frame_end_y,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "raw_start_x": start_raw_point[0],
        "raw_start_y": start_raw_point[1],
        "raw_end_x": end_raw_point[0],
        "raw_end_y": end_raw_point[1],
        "duration_ms": duration_ms,
        "area": resolved_area,
        "mode": resolved_mode,
        "rotate": resolved_rotate,
    }


