"""攻略下载状态的持久化契约，供目录查询与下载工作器共同使用。

读取只访问本地状态文件；写入以临时文件原子替换，并统一重算完成/失败数。
本模块不导入目录采集或媒体下载执行器。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from backend.core.settings import get_settings


GUIDE_VIDEO_DOWNLOAD_SCHEMA_VERSION = 1


def guide_video_download_snapshot_path() -> Path:
    return get_settings().data_dir / "fanxiu" / "guide-videos" / "downloads.json"


def _empty_snapshot() -> dict[str, Any]:
    return {
        "schema_version": GUIDE_VIDEO_DOWNLOAD_SCHEMA_VERSION,
        "status": "idle",
        "target_count": 0,
        "done_count": 0,
        "failed_count": 0,
        "current_item_id": "",
        "started_at": 0.0,
        "updated_at": 0.0,
        "worker_pid": 0,
        "error": "",
        "items": [],
    }


def load_guide_video_download_snapshot(path: str | Path | None = None) -> dict[str, Any]:
    snapshot_path = Path(path) if path is not None else guide_video_download_snapshot_path()
    if not snapshot_path.is_file():
        return _empty_snapshot()
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_snapshot()
    if not isinstance(payload, dict):
        return _empty_snapshot()
    snapshot = _empty_snapshot()
    snapshot.update(payload)
    snapshot["schema_version"] = GUIDE_VIDEO_DOWNLOAD_SCHEMA_VERSION
    snapshot["items"] = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    return snapshot


def save_guide_video_download_snapshot(
    snapshot: dict[str, Any], path: str | Path | None = None
) -> Path:
    snapshot_path = Path(path) if path is not None else guide_video_download_snapshot_path()
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _empty_snapshot()
    normalized.update(snapshot)
    normalized["schema_version"] = GUIDE_VIDEO_DOWNLOAD_SCHEMA_VERSION
    normalized["items"] = [item for item in snapshot.get("items") or [] if isinstance(item, dict)]
    normalized["done_count"] = sum(item.get("status") == "done" for item in normalized["items"])
    normalized["failed_count"] = sum(item.get("status") == "error" for item in normalized["items"])
    normalized["updated_at"] = time.time()
    temporary = snapshot_path.with_name(
        f".{snapshot_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, snapshot_path)
    return snapshot_path


def download_record_by_item_id(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("item_id") or ""): item
        for item in snapshot.get("items") or []
        if str(item.get("item_id") or "")
    }
