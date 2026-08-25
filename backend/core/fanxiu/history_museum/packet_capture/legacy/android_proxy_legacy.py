from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from backend.core.services.launcher import run_quiet


DEFAULT_ADB_CANDIDATES = (
    r"D:\TapTap\Support\android_emulator\engine\nx_device\12.0\shell\adb.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"D:\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
)
PROXY_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+:\d{1,5}$")


def _completed_text(process: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part.strip() for part in (process.stdout, process.stderr) if part and part.strip())


def _run_command(command: list[str], timeout: float = 8) -> str:
    process = run_quiet(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    output = _completed_text(process)
    if process.returncode != 0:
        raise RuntimeError(output or f"命令退出码 {process.returncode}")
    return output


def _normalize_http_proxy(value: str) -> str:
    value = str(value or "").strip()
    if value.lower() in {"", "null", "undefined", ":0"}:
        return ""
    return value


class FanxiuAndroidProxyService:
    def __init__(self) -> None:
        self._last_error = ""

    def _adb_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        env_path = os.environ.get("FANXIU_ADB_PATH")
        if env_path:
            candidates.append(Path(env_path))
        path_adb = shutil.which("adb")
        if path_adb:
            candidates.append(Path(path_adb))
        candidates.extend(Path(item) for item in DEFAULT_ADB_CANDIDATES)

        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def adb_path(self) -> Path:
        for candidate in self._adb_candidates():
            if candidate.exists() and candidate.is_file():
                return candidate
        raise RuntimeError("找不到 adb.exe。可设置 FANXIU_ADB_PATH 指向 MuMu/TapTap 的 adb.exe。")

    def _run_adb(self, args: list[str], timeout: float = 8) -> str:
        return _run_command([str(self.adb_path()), *args], timeout=timeout)

    def devices(self) -> list[str]:
        output = self._run_adb(["devices"])
        devices: list[str] = []
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped or stripped.lower().startswith("list of devices"):
                continue
            parts = stripped.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    def choose_device(self, device_id: str = "") -> str:
        requested = str(device_id or "").strip()
        if requested:
            return requested
        devices = self.devices()
        if not devices:
            raise RuntimeError("没有检测到已连接的安卓模拟器。")
        for item in devices:
            if item.startswith("emulator-"):
                return item
        return devices[0]

    def status(self, *, device_id: str = "", target_proxy: str = "") -> dict[str, Any]:
        target = str(target_proxy or "").strip()
        try:
            adb_path = self.adb_path()
            devices = self.devices()
            selected_device = self.choose_device(device_id) if devices or device_id else ""
            current_proxy = ""
            if selected_device:
                current_proxy = _normalize_http_proxy(
                    self._run_adb(["-s", selected_device, "shell", "settings", "get", "global", "http_proxy"])
                )
            self._last_error = ""
            return {
                "available": bool(selected_device),
                "adb_path": str(adb_path),
                "device_id": selected_device,
                "devices": devices,
                "http_proxy": current_proxy,
                "enabled": bool(current_proxy),
                "target_proxy": target,
                "matches_target": bool(target and current_proxy == target),
                "last_error": "",
            }
        except Exception as exc:
            self._last_error = str(exc)
            return {
                "available": False,
                "adb_path": "",
                "device_id": str(device_id or "").strip(),
                "devices": [],
                "http_proxy": "",
                "enabled": False,
                "target_proxy": target,
                "matches_target": False,
                "last_error": self._last_error,
            }

    def set_http_proxy(self, value: str, *, device_id: str = "") -> dict[str, Any]:
        proxy_value = str(value or "").strip()
        if not PROXY_VALUE_PATTERN.match(proxy_value):
            raise RuntimeError(f"安卓代理地址格式非法：{proxy_value}")
        selected_device = self.choose_device(device_id)
        self._run_adb(["-s", selected_device, "shell", "settings", "put", "global", "http_proxy", proxy_value])
        return self.status(device_id=selected_device, target_proxy=proxy_value)

    def clear_http_proxy(self, *, device_id: str = "") -> dict[str, Any]:
        selected_device = self.choose_device(device_id)
        self._run_adb(["-s", selected_device, "shell", "settings", "put", "global", "http_proxy", ":0"])
        return self.status(device_id=selected_device)


fanxiu_android_proxy_service = FanxiuAndroidProxyService()
