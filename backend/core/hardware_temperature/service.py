from __future__ import annotations

from typing import Any

from .local_collector import LocalCollectorUnavailable, read_local_temperature_devices


def get_temperature_snapshot() -> dict[str, Any]:
    try:
        payload = read_local_temperature_devices()
    except LocalCollectorUnavailable as error:
        return {
            "status": "unavailable",
            "observed_at": None,
            "source": "CodeYun",
            "devices": [],
            "message": str(error),
        }
    except (OSError, ValueError):
        return {
            "status": "error",
            "observed_at": None,
            "source": "CodeYun",
            "devices": [],
            "message": "本机温度数据暂时无法解析",
        }

    devices = payload["devices"]
    status = str(payload.get("status") or ("ok" if devices else "partial"))
    elevated = bool(payload.get("elevated"))
    return {
        "status": status,
        "observed_at": payload.get("observed_at"),
        "source": "CodeYun",
        "elevated": elevated,
        "devices": devices,
        "message": "温度数据正常" if status == "ok" else (
            "部分传感器需要管理员权限" if devices and not elevated else "部分设备暂未提供温度"
        ),
    }
