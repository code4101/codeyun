from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock

from backend.core.codex.app_server import CODEX_RATE_LIMITS_METHOD, read_codex_rate_limits
from backend.core.settings import get_settings


CODEX_WEEKLY_QUOTA_TASK_KEY = "codex_weekly_quota_snapshot"
CODEX_WEEKLY_QUOTA_RUN_TIME = "00:00"
CODEX_USAGE_URL = "https://chatgpt.com/codex/cloud/settings/analytics#usage"
CODEX_WEEKLY_QUOTA_SOURCE = f"codex_app_server:{CODEX_RATE_LIMITS_METHOD}"
CODEX_WEEKLY_QUOTA_HISTORY_VERSION = 2


class CodexWeeklyQuotaError(RuntimeError):
    pass


class CodexWeeklyQuotaLoginRequired(CodexWeeklyQuotaError):
    pass


def parse_codex_rate_limits_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the longest (weekly) rate-limit window from app-server output."""

    snapshots = payload.get("rateLimitsByLimitId")
    snapshot = snapshots.get("codex") if isinstance(snapshots, dict) else None
    if not isinstance(snapshot, dict):
        snapshot = payload.get("rateLimits")
    if not isinstance(snapshot, dict):
        raise CodexWeeklyQuotaError("Codex app-server 未返回 codex rateLimits")

    windows = [
        item
        for name in ("primary", "secondary")
        if isinstance((item := snapshot.get(name)), dict)
    ]
    if not windows:
        raise CodexWeeklyQuotaError("Codex app-server 未返回任何限额窗口")
    weekly = max(
        enumerate(windows),
        key=lambda pair: (int(pair[1].get("windowDurationMins") or 0), pair[0]),
    )[1]
    try:
        used_percent = int(weekly["usedPercent"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CodexWeeklyQuotaError("Codex app-server 限额窗口缺少 usedPercent") from exc
    if not 0 <= used_percent <= 100:
        raise CodexWeeklyQuotaError(f"Codex app-server usedPercent 超出范围：{used_percent}")

    reset_at = ""
    raw_reset_at = weekly.get("resetsAt")
    if raw_reset_at is not None:
        try:
            reset_at = dt.datetime.fromtimestamp(
                int(raw_reset_at),
                tz=dt.timezone.utc,
            ).replace(microsecond=0).isoformat()
        except (OSError, OverflowError, TypeError, ValueError) as exc:
            raise CodexWeeklyQuotaError(
                f"Codex app-server resetsAt 无效：{raw_reset_at}"
            ) from exc
    return {
        "remaining_percent": 100 - used_percent,
        "reset_at": reset_at,
        "window_duration_minutes": int(weekly.get("windowDurationMins") or 0),
    }


def get_codex_weekly_quota_history_path() -> Path:
    return get_settings().data_dir / "codex" / "weekly_quota_history.json"


def parse_codex_weekly_quota_text(text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", str(text or "").replace("\u00a0", " ")).strip()
    patterns = (
        r"每周使用限额\s*(\d{1,3})\s*%\s*剩余",
        r"Weekly usage limit\s*(\d{1,3})\s*%\s*(?:left|remaining)",
    )
    remaining_percent: int | None = None
    for pattern in patterns:
        matched = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if matched:
            remaining_percent = int(matched.group(1))
            break
    if remaining_percent is None or not 0 <= remaining_percent <= 100:
        raise CodexWeeklyQuotaError("未从 Codex 分析页读到每周使用限额的剩余百分比")

    reset_at = ""
    reset_patterns = (
        r"每周使用限额.*?重置时间[：:]\s*(\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2})",
        r"Weekly usage limit.*?(?:resets?|reset time)[：:]?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4}[^%]{0,20}\d{1,2}:\d{2})",
    )
    for pattern in reset_patterns:
        matched = re.search(pattern, normalized, flags=re.IGNORECASE)
        if matched:
            reset_at = matched.group(1).strip()
            break
    return {
        "remaining_percent": remaining_percent,
        "reset_at": reset_at,
    }


def read_codex_weekly_quota_history(path: Path | None = None) -> dict[str, Any]:
    resolved_path = path or get_codex_weekly_quota_history_path()
    if not resolved_path.exists():
        return {"version": CODEX_WEEKLY_QUOTA_HISTORY_VERSION, "snapshots": []}
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": CODEX_WEEKLY_QUOTA_HISTORY_VERSION, "snapshots": []}
    source_version = int(payload.get("version") or 1) if isinstance(payload, dict) else 1
    snapshots = payload.get("snapshots") if isinstance(payload, dict) else []
    normalized_snapshots = [dict(item) for item in snapshots if isinstance(item, dict)]
    if source_version < 2:
        for item in normalized_snapshots:
            observed_at = str(item.get("observed_at") or "").strip()
            try:
                item["date"] = dt.datetime.fromisoformat(observed_at).date().isoformat()
            except ValueError:
                try:
                    item["date"] = (
                        dt.date.fromisoformat(str(item.get("date") or "")) + dt.timedelta(days=1)
                    ).isoformat()
                except ValueError:
                    pass
    return {
        "version": CODEX_WEEKLY_QUOTA_HISTORY_VERSION,
        "snapshots": normalized_snapshots,
    }


def list_codex_weekly_quota_snapshots(path: Path | None = None) -> list[dict[str, Any]]:
    snapshots = read_codex_weekly_quota_history(path).get("snapshots") or []
    return sorted(
        (dict(item) for item in snapshots if str(item.get("date") or "")),
        key=lambda item: str(item.get("date") or ""),
    )


def record_codex_weekly_quota_snapshot(
    *,
    remaining_percent: int,
    observed_at: dt.datetime,
    reset_at: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    if not 0 <= int(remaining_percent) <= 100:
        raise ValueError("Codex 每周余额必须在 0 到 100 之间")
    resolved_path = path or get_codex_weekly_quota_history_path()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    effective_date = observed_at.date().isoformat()
    record = {
        "date": effective_date,
        "remaining_percent": int(remaining_percent),
        "observed_at": observed_at.replace(microsecond=0).isoformat(),
        "reset_at": str(reset_at or "").strip(),
        "source_url": CODEX_USAGE_URL,
        "source": CODEX_WEEKLY_QUOTA_SOURCE,
    }

    lock_path = resolved_path.with_suffix(f"{resolved_path.suffix}.lock")
    with FileLock(str(lock_path), timeout=10):
        history = read_codex_weekly_quota_history(resolved_path)
        snapshots = [
            dict(item)
            for item in history.get("snapshots") or []
            if str(item.get("date") or "") != effective_date
        ]
        snapshots.append(record)
        snapshots.sort(key=lambda item: str(item.get("date") or ""))
        payload = {
            "version": CODEX_WEEKLY_QUOTA_HISTORY_VERSION,
            "snapshots": snapshots,
        }
        temp_path = resolved_path.with_suffix(f"{resolved_path.suffix}.{os.getpid()}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, resolved_path)
    return record


def collect_codex_weekly_quota_snapshot(
    *,
    now: dt.datetime | None = None,
    history_path: Path | None = None,
    rate_limits_reader: Callable[..., dict[str, Any]] = read_codex_rate_limits,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    observed_at = (now or dt.datetime.now()).replace(microsecond=0)
    payload = rate_limits_reader(timeout_seconds=timeout_seconds)
    parsed = parse_codex_rate_limits_snapshot(payload)
    record = record_codex_weekly_quota_snapshot(
        remaining_percent=int(parsed["remaining_percent"]),
        observed_at=observed_at,
        reset_at=str(parsed.get("reset_at") or ""),
        path=history_path,
    )
    print(
        "Codex weekly quota recorded: "
        f"date={record['date']} remaining={record['remaining_percent']}% observed_at={record['observed_at']}"
    )
    return record
