"""Query the DeepSeek official account balance.

DeepSeek exposes ``GET /user/balance``; the API key comes from CodeYun's system
DeepSeek resource (the same key used when switching Codex to the DeepSeek
provider).
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import requests

from backend.core.settings import get_settings


DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
_DEEPSEEK_BALANCE_TIMEOUT_SECONDS = 15.0
_DEEPSEEK_BALANCE_WINDOW_DAYS = 30
_DEEPSEEK_BALANCE_DEDUP_MINUTES = 10


def _to_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(dt.timezone.utc)


def _parse_timestamp(raw: Any) -> dt.datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return _to_utc(dt.datetime.fromisoformat(text))
    except ValueError:
        return None


def _floor_to_local_day(value: dt.datetime, local_tz: dt.tzinfo) -> dt.datetime:
    return value.astimezone(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)


def _ceil_to_local_day(value: dt.datetime, local_tz: dt.tzinfo) -> dt.datetime:
    floored = _floor_to_local_day(value, local_tz)
    if value.astimezone(local_tz) == floored:
        return floored
    return floored + dt.timedelta(days=1)


def _history_bucket(observed_at: dt.datetime) -> str:
    step = max(1, _DEEPSEEK_BALANCE_DEDUP_MINUTES)
    minute = (observed_at.minute // step) * step
    return observed_at.replace(minute=minute, second=0, microsecond=0).isoformat()


def get_deepseek_balance_snapshot_path() -> Path:
    return get_settings().data_dir / "codex" / "deepseek_balance.json"


def read_deepseek_balance(api_key: str, *, timeout_seconds: float = _DEEPSEEK_BALANCE_TIMEOUT_SECONDS) -> dict[str, Any]:
    key = str(api_key or "").strip()
    if not key:
        return {"available": False, "is_available": False, "balances": [], "error": "未找到可用的 DeepSeek API Key"}

    try:
        response = requests.get(
            DEEPSEEK_BALANCE_URL,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"available": False, "is_available": False, "balances": [], "error": f"查询 DeepSeek 余额失败：{exc}"}

    balances: list[dict[str, str]] = []
    for item in (payload.get("balance_infos") if isinstance(payload, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        balances.append({
            "currency": str(item.get("currency") or ""),
            "total_balance": str(item.get("total_balance") or ""),
            "granted_balance": str(item.get("granted_balance") or ""),
            "topped_up_balance": str(item.get("topped_up_balance") or ""),
        })
    return {
        "available": True,
        "is_available": bool(payload.get("is_available")) if isinstance(payload, dict) else False,
        "balances": balances,
        "error": "",
    }


def save_deepseek_balance_snapshot(
    payload: dict[str, Any],
    observed_at: dt.datetime,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    resolved_path = path or get_deepseek_balance_snapshot_path()
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


def load_deepseek_balance_snapshot(*, path: Path | None = None) -> dict[str, Any]:
    resolved_path = path or get_deepseek_balance_snapshot_path()
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


def get_deepseek_balance_history_path() -> Path:
    return get_settings().data_dir / "codex" / "deepseek_balance_history.json"


def read_deepseek_balance_history(*, path: Path | None = None) -> list[dict[str, Any]]:
    resolved_path = path or get_deepseek_balance_history_path()
    if not resolved_path.exists():
        return []
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    snapshots = payload.get("snapshots") if isinstance(payload, dict) else None
    return [item for item in (snapshots or []) if isinstance(item, dict)]


def total_balance_value(payload: dict[str, Any]) -> float | None:
    return _total_balance_of(payload)


def _total_balance_of(payload: dict[str, Any]) -> float | None:
    balances = payload.get("balances") if isinstance(payload, dict) else None
    for item in balances or []:
        if not isinstance(item, dict):
            continue
        try:
            return float(item.get("total_balance"))
        except (TypeError, ValueError):
            continue
    return None


def record_deepseek_balance_snapshot(
    payload: dict[str, Any],
    observed_at: dt.datetime,
    *,
    path: Path | None = None,
) -> dict[str, Any] | None:
    total = _total_balance_of(payload)
    if total is None:
        return None

    resolved_path = path or get_deepseek_balance_history_path()
    observed_text = observed_at.replace(microsecond=0).isoformat()
    bucket = _history_bucket(observed_at)
    record = {
        "date": observed_at.date().isoformat(),
        "observed_at": observed_text,
        "bucket": bucket,
        "total_balance": total,
    }
    snapshots = [
        item
        for item in read_deepseek_balance_history(path=resolved_path)
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


def build_deepseek_balance_window(
    snapshots: list[dict[str, Any]],
    *,
    now: dt.datetime | None = None,
    days: int = _DEEPSEEK_BALANCE_WINDOW_DAYS,
    latest_value: float | None = None,
    latest_at: str | None = None,
) -> dict[str, Any]:
    """Rolling window: the last ``days`` days ending today (no reset concept).

    ``latest_value``/``latest_at`` let the chart show the current balance even
    before any history has been recorded.
    """

    current = now or dt.datetime.now()
    if current.tzinfo is None:
        current = current.astimezone()
    local_tz = current.astimezone().tzinfo
    raw_start = current - dt.timedelta(days=days)

    points: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        try:
            value = float(snapshot.get("total_balance"))
        except (TypeError, ValueError):
            continue
        moment = _parse_timestamp(snapshot.get("observed_at")) or _parse_timestamp(snapshot.get("date"))
        if moment is None or not (raw_start <= moment <= current):
            continue
        points.append({"at": moment.isoformat(), "value": value})

    if latest_value is not None and latest_at:
        moment = _parse_timestamp(latest_at)
        if moment is not None and raw_start <= moment <= current:
            at_iso = moment.isoformat()
            if not any(item["at"] == at_iso for item in points):
                points.append({"at": at_iso, "value": float(latest_value)})
    points.sort(key=lambda item: item["at"])

    start = _floor_to_local_day(_to_utc(raw_start), local_tz)
    axis_end = _ceil_to_local_day(_to_utc(current), local_tz)
    return {
        "window_start": start.isoformat(),
        "window_end": axis_end.isoformat(),
        "points": points,
    }


def collect_deepseek_balance_snapshot(api_key: str, *, now: dt.datetime | None = None) -> dict[str, Any]:
    payload = read_deepseek_balance(api_key)
    observed_at = (now or dt.datetime.now()).replace(microsecond=0)
    # Keep the last successful reading: a failed probe must not overwrite a good
    # snapshot, so the page can still fall back to it.
    if payload.get("available"):
        save_deepseek_balance_snapshot(payload, observed_at)
        record_deepseek_balance_snapshot(payload, observed_at)
    return {"observed_at": observed_at.isoformat(), "payload": payload}
