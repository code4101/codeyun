"""Read the local OpenCode Go plan usage through its official API.

opencode stores the ``opencode-go`` API key in its own auth file; the official
usage endpoint returns the rolling (5h), weekly and monthly quota windows.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import requests

from backend.core.settings import get_settings


OPENCODE_GO_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
OPENCODE_GO_PROVIDER_ID = "opencode-go"
_OPENCODE_GO_TIMEOUT_SECONDS = 15.0

_WINDOW_ORDER = ("rolling", "weekly", "monthly")
_WINDOW_LABELS = {"rolling": "5 小时", "weekly": "每周", "monthly": "每月"}


class OpenCodeUsageError(RuntimeError):
    """Raised when the OpenCode Go usage response cannot be parsed."""


def resolve_opencode_auth_path() -> Path:
    base = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if base:
        return Path(base) / "opencode" / "auth.json"
    return Path.home() / ".local" / "share" / "opencode" / "auth.json"


def read_opencode_go_key(*, path: Path | None = None) -> str:
    auth_path = path or resolve_opencode_auth_path()
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    entry = payload.get(OPENCODE_GO_PROVIDER_ID) if isinstance(payload, dict) else None
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("key") or "").strip()


def parse_opencode_go_usage(payload: dict[str, Any]) -> list[dict[str, Any]]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        raise OpenCodeUsageError("opencode-go 未返回 usage")

    windows: list[dict[str, Any]] = []
    for key in _WINDOW_ORDER:
        window = usage.get(key)
        if not isinstance(window, dict):
            continue
        try:
            used_percent = int(window.get("percent"))
        except (TypeError, ValueError):
            continue
        windows.append(
            {
                "label": _WINDOW_LABELS[key],
                "remaining_percent": max(0, min(100, 100 - used_percent)),
                "reset_at": str(window.get("resetsAt") or ""),
                "status": str(window.get("status") or ""),
            }
        )
    return windows


_OPENCODE_MONTHLY_WINDOW_DAYS = 30
_OPENCODE_HISTORY_DEDUP_MINUTES = 10


def _history_bucket(observed_at: dt.datetime) -> str:
    step = max(1, _OPENCODE_HISTORY_DEDUP_MINUTES)
    minute = (observed_at.minute // step) * step
    return observed_at.replace(minute=minute, second=0, microsecond=0).isoformat()


def _to_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(dt.timezone.utc)


def _parse_timestamp(raw: Any) -> dt.datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return _to_utc(value)


def _floor_to_local_day(value: dt.datetime, local_tz: dt.tzinfo) -> dt.datetime:
    return value.astimezone(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)


def _ceil_to_local_day(value: dt.datetime, local_tz: dt.tzinfo) -> dt.datetime:
    floored = _floor_to_local_day(value, local_tz)
    if value.astimezone(local_tz) == floored:
        return floored
    return floored + dt.timedelta(days=1)


def _window_by_label(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("label")): item
        for item in (payload.get("windows") or [])
        if isinstance(item, dict)
    }


def get_opencode_usage_snapshot_path() -> Path:
    return get_settings().data_dir / "codex" / "opencode_usage.json"


def get_opencode_usage_history_path() -> Path:
    return get_settings().data_dir / "codex" / "opencode_usage_history.json"


def read_opencode_usage_history(*, path: Path | None = None) -> list[dict[str, Any]]:
    resolved_path = path or get_opencode_usage_history_path()
    if not resolved_path.exists():
        return []
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    snapshots = payload.get("snapshots") if isinstance(payload, dict) else None
    return [item for item in (snapshots or []) if isinstance(item, dict)]


def record_opencode_usage_snapshot(
    payload: dict[str, Any],
    observed_at: dt.datetime,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    resolved_path = path or get_opencode_usage_history_path()
    windows = _window_by_label(payload)
    monthly = windows.get("每月") or {}
    observed_text = observed_at.replace(microsecond=0).isoformat()
    bucket = _history_bucket(observed_at)
    record = {
        "date": observed_at.date().isoformat(),
        "observed_at": observed_text,
        "bucket": bucket,
        "rolling": (windows.get("5 小时") or {}).get("remaining_percent"),
        "weekly": (windows.get("每周") or {}).get("remaining_percent"),
        "monthly": monthly.get("remaining_percent"),
        "monthly_reset_at": str(monthly.get("reset_at") or ""),
    }
    snapshots = [
        item
        for item in read_opencode_usage_history(path=resolved_path)
        if str(item.get("bucket") or "") != bucket
    ]
    snapshots.append(record)
    snapshots.sort(key=lambda item: str(item.get("observed_at") or item.get("date") or ""))
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = resolved_path.with_suffix(f"{resolved_path.suffix}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps({"version": 1, "snapshots": snapshots}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp_path, resolved_path)
    return record


def build_opencode_monthly_window(
    payload: dict[str, Any],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    monthly = _window_by_label(payload).get("每月") if isinstance(payload, dict) else None
    reset = _parse_timestamp(monthly.get("reset_at")) if isinstance(monthly, dict) else None
    raw_start = reset - dt.timedelta(days=_OPENCODE_MONTHLY_WINDOW_DAYS) if reset else None

    points: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        try:
            remaining = int(snapshot.get("monthly"))
        except (TypeError, ValueError):
            continue
        moment = _parse_timestamp(snapshot.get("observed_at")) or _parse_timestamp(snapshot.get("date"))
        if moment is None:
            continue
        if raw_start and reset and not (raw_start <= moment <= reset):
            continue
        points.append({"at": moment.isoformat(), "remaining_percent": max(0, min(100, remaining))})
    points.sort(key=lambda item: item["at"])

    if raw_start is None and points:
        raw_start = dt.datetime.fromisoformat(points[0]["at"])
    end_for_axis = reset or (dt.datetime.fromisoformat(points[-1]["at"]) if points else None)
    local_tz = dt.datetime.now().astimezone().tzinfo
    start = _floor_to_local_day(raw_start, local_tz) if raw_start else None
    axis_end = _ceil_to_local_day(end_for_axis, local_tz) if end_for_axis else None

    return {
        "name": "OpenCode Go 套餐余额",
        "window_start": start.isoformat() if start else "",
        "window_end": axis_end.isoformat() if axis_end else "",
        "reset_at": reset.isoformat() if reset else "",
        "remaining_percent": monthly.get("remaining_percent") if isinstance(monthly, dict) else None,
        "points": points,
    }


def save_opencode_usage_snapshot(
    payload: dict[str, Any],
    observed_at: dt.datetime,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    resolved_path = path or get_opencode_usage_snapshot_path()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "observed_at": observed_at.replace(microsecond=0).isoformat(),
        "payload": payload,
    }
    temp_path = resolved_path.with_suffix(f"{resolved_path.suffix}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, resolved_path)
    return data


def load_opencode_usage_snapshot(*, path: Path | None = None) -> dict[str, Any]:
    resolved_path = path or get_opencode_usage_snapshot_path()
    if not resolved_path.exists():
        return {"observed_at": "", "payload": {}}
    try:
        data = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"observed_at": "", "payload": {}}
    payload = data.get("payload") if isinstance(data, dict) else None
    return {
        "observed_at": str(data.get("observed_at") or "") if isinstance(data, dict) else "",
        "payload": payload if isinstance(payload, dict) else {},
    }


def collect_opencode_usage_snapshot(*, now: dt.datetime | None = None) -> dict[str, Any]:
    payload = read_opencode_go_usage()
    observed_at = (now or dt.datetime.now()).replace(microsecond=0)
    # Keep the last successful reading: an unavailable probe must not overwrite a
    # good snapshot, so the page can still fall back to it.
    if payload.get("available"):
        save_opencode_usage_snapshot(payload, observed_at)
        record_opencode_usage_snapshot(payload, observed_at)
    return {"observed_at": observed_at.isoformat(), "payload": payload}


def read_opencode_go_usage(*, timeout_seconds: float = _OPENCODE_GO_TIMEOUT_SECONDS) -> dict[str, Any]:
    key = read_opencode_go_key()
    if not key:
        return {"available": False, "windows": [], "error": "未找到 OpenCode Go 凭证"}

    try:
        response = requests.get(
            OPENCODE_GO_USAGE_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"available": False, "windows": [], "error": f"读取 OpenCode Go 用量失败：{exc}"}

    try:
        windows = parse_opencode_go_usage(payload)
    except OpenCodeUsageError as exc:
        return {"available": False, "windows": [], "error": str(exc)}
    return {"available": True, "windows": windows, "error": ""}
