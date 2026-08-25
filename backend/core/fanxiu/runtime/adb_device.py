from __future__ import annotations

import os
import shutil
from pathlib import Path

from backend.core.services.launcher import run_quiet


DEFAULT_ADB_CANDIDATES = (
    r"D:\TapTap\Support\android_emulator\engine\nx_device\12.0\shell\adb.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"D:\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
)


class FanxiuAdbDeviceService:
    """ADB discovery only; this service cannot configure a capture proxy."""

    def adb_path(self) -> Path:
        candidates: list[Path] = []
        if env_path := os.environ.get("FANXIU_ADB_PATH"):
            candidates.append(Path(env_path))
        if path_adb := shutil.which("adb"):
            candidates.append(Path(path_adb))
        candidates.extend(Path(item) for item in DEFAULT_ADB_CANDIDATES)
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).lower()
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file():
                return candidate
        raise RuntimeError("找不到 adb.exe。可设置 FANXIU_ADB_PATH 指向 MuMu/TapTap 的 adb.exe。")

    def devices(self) -> list[str]:
        process = run_quiet(
            [str(self.adb_path()), "devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        if process.returncode != 0:
            raise RuntimeError((process.stderr or process.stdout or "adb devices 失败").strip())
        result: list[str] = []
        for line in (process.stdout or "").splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                result.append(parts[0])
        return result

    def choose_device(self, device_id: str = "") -> str:
        if requested := str(device_id or "").strip():
            return requested
        devices = self.devices()
        if not devices:
            raise RuntimeError("没有检测到已连接的安卓模拟器。")
        return next((item for item in devices if item.startswith("emulator-")), devices[0])


fanxiu_adb_device_service = FanxiuAdbDeviceService()


__all__ = ["DEFAULT_ADB_CANDIDATES", "FanxiuAdbDeviceService", "fanxiu_adb_device_service"]
