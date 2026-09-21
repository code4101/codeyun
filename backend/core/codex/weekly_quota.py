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
DEFAULT_GENERAL_QUOTA_WINDOW_DAYS = 7
# Show the current reset period plus the one before it, so the chart carries the
# previous cycle's burn rhythm for comparison instead of a single period.
GENERAL_QUOTA_WINDOW_PERIODS = 2
QUOTA_HISTORY_DEDUP_MINUTES = 10


def _history_bucket(observed_at: dt.datetime) -> str:
    step = max(1, QUOTA_HISTORY_DEDUP_MINUTES)
    minute = (observed_at.minute // step) * step
    return observed_at.replace(minute=minute, second=0, microsecond=0).isoformat()


class CodexWeeklyQuotaError(RuntimeError):
    pass


class CodexWeeklyQuotaLoginRequired(CodexWeeklyQuotaError):
    pass


def describe_codex_quota_error(error: BaseException) -> str:
    """Turn a raw app-server failure into an actionable Chinese message.

    ``account/rateLimits/read`` can only answer with the ChatGPT account, so a
    missing (or config-disabled) ChatGPT login is the single most common failure.
    Say what to do instead of surfacing the English protocol error verbatim.
    """

    text = str(error or "").strip()
    if "authentication required" in text.lower() or "chatgpt login is disabled" in text.lower():
        return (
            '本机 Codex 未登录 ChatGPT 账号（config.toml 被官方脚本写成 API key 强制登录时也如此）：'
            '请执行 codex login -c forced_login_method="chatgpt" 重新登录后刷新'
            f"（原始错误：{text}）"
        )
    return text


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


# Reading the ChatGPT account quota must not depend on which provider ``config.toml``
# currently points at.  The official DeepSeek setup script writes both
# ``preferred_auth_method = "apikey"`` and ``forced_login_method = "api"``, and the
# latter alone makes Codex ignore a perfectly valid ChatGPT ``auth.json``: it then
# answers ``account/rateLimits/read`` with "codex account authentication required".
# Override both keys for the child process so the read always uses the ChatGPT login.
CODEX_CHATGPT_AUTH_OVERRIDE: tuple[tuple[str, str], ...] = (
    ("preferred_auth_method", '"chatgpt"'),
    ("forced_login_method", '"chatgpt"'),
)


def _window_label(minutes: Any) -> str:
    try:
        value = int(minutes or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value % 10080 == 0:
        return "每周" if value == 10080 else f"{value // 10080} 周"
    if value % 1440 == 0:
        return "每天" if value == 1440 else f"{value // 1440} 天"
    if value % 60 == 0:
        return f"{value // 60} 小时"
    return f"{value} 分钟"


def _reset_at_iso(value: Any) -> str:
    if value is None:
        return ""
    try:
        return (
            dt.datetime.fromtimestamp(int(value), tz=dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    except (OSError, OverflowError, TypeError, ValueError):
        return ""


def parse_codex_rate_limit_groups(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every rate-limit bucket (general + per-model) with its windows."""

    by_id = payload.get("rateLimitsByLimitId") if isinstance(payload, dict) else None
    if not isinstance(by_id, dict) or not by_id:
        single = payload.get("rateLimits") if isinstance(payload, dict) else None
        by_id = {"codex": single} if isinstance(single, dict) else {}

    groups: list[dict[str, Any]] = []
    for limit_id, snapshot in by_id.items():
        if not isinstance(snapshot, dict):
            continue
        name = str(snapshot.get("limitName") or "").strip()
        if not name:
            name = "通用使用限额" if str(limit_id) == "codex" else str(limit_id)
        windows: list[dict[str, Any]] = []
        for key in ("primary", "secondary"):
            window = snapshot.get(key)
            if not isinstance(window, dict):
                continue
            try:
                used_percent = int(window.get("usedPercent"))
            except (TypeError, ValueError):
                continue
            minutes = int(window.get("windowDurationMins") or 0)
            windows.append(
                {
                    "label": _window_label(minutes),
                    "remaining_percent": max(0, min(100, 100 - used_percent)),
                    "reset_at": _reset_at_iso(window.get("resetsAt")),
                    "window_minutes": minutes,
                }
            )
        windows.sort(key=lambda item: item["window_minutes"])
        groups.append({"id": str(limit_id), "name": name, "windows": windows})

    groups.sort(key=lambda item: 0 if item["id"] == "codex" else 1)
    return groups


def read_codex_quota_groups(*, timeout_seconds: float = 25.0) -> list[dict[str, Any]]:
    """Read the ChatGPT/Codex account quota buckets, regardless of active provider."""

    payload = read_codex_rate_limits(
        timeout_seconds=timeout_seconds,
        config_overrides=CODEX_CHATGPT_AUTH_OVERRIDE,
    )
    return parse_codex_rate_limit_groups(payload)


def _to_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(dt.timezone.utc)


def _floor_to_local_day(value: dt.datetime, local_tz: dt.tzinfo) -> dt.datetime:
    return value.astimezone(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)


def _ceil_to_local_day(value: dt.datetime, local_tz: dt.tzinfo) -> dt.datetime:
    floored = _floor_to_local_day(value, local_tz)
    if value.astimezone(local_tz) == floored:
        return floored
    return floored + dt.timedelta(days=1)


def _parse_history_timestamp(item: dict[str, Any]) -> dt.datetime | None:
    for key in ("observed_at", "date"):
        raw = str(item.get(key) or "").strip()
        if not raw:
            continue
        try:
            return _to_utc(dt.datetime.fromisoformat(raw))
        except ValueError:
            continue
    return None


def _parse_iso_timestamp(raw: Any) -> dt.datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return _to_utc(dt.datetime.fromisoformat(text))
    except ValueError:
        return None


def _general_quota_window(groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    general = next((item for item in groups if isinstance(item, dict) and item.get("id") == "codex"), None)
    if general is None and groups:
        general = groups[0]
    if not isinstance(general, dict):
        return None
    windows = [item for item in general.get("windows") or [] if isinstance(item, dict)]
    return max(windows, key=lambda item: int(item.get("window_minutes") or 0), default=None)


def _expand_reset_breaks(points: list[dict[str, Any]], period: dt.timedelta) -> list[dict[str, Any]]:
    """Split the polyline where a quota reset falls between two samples.

    History is sampled sparsely, so a raw series slopes from the last pre-reset
    reading up to the post-reset one and lands the jump in the middle of the gap.
    Each snapshot carries the reset time of its own period, so when that value
    changes, close the old segment at the earlier of its scheduled reset and the
    new cycle's start: hold the old reading until then and open from 100%.

    The new period does not start at the old reset: the next timer only begins on
    first use after the reset, so its start is the new ``reset_at`` minus one
    ``period`` (both come from the API).  That start is usually a little later
    than the old reset, but can be earlier after an unscheduled reset. The null
    break keeps separate cycles from being connected.
    """

    expanded: list[dict[str, Any]] = []
    previous_reset = ""
    previous_value: int | None = None
    for item in points:
        reset_at = str(item.get("reset_at") or "")
        item_at = str(item.get("at") or "")
        last_at = str(expanded[-1].get("at") or "") if expanded else ""
        previous_moment = _parse_iso_timestamp(previous_reset)
        new_reset = _parse_iso_timestamp(reset_at)
        new_start = new_reset - period if new_reset is not None else None
        last_moment = _parse_iso_timestamp(last_at)
        item_moment = _parse_iso_timestamp(item_at)
        # An early reset opens a new cycle before the old scheduled end. Use the
        # new cycle's own start, not the now-obsolete old deadline, as the break.
        close_at = previous_reset
        if (
            new_start is not None and previous_moment is not None
            and last_moment is not None and item_moment is not None
            and last_moment < new_start <= item_moment
            and new_start < previous_moment
        ):
            close_at = new_start.isoformat()
        # A real reset moved the marker to a new period and happened between the
        # previous sample and this one; a bare drift of the timestamp (a second or
        # two) or a marker outside that span is not a reset.
        if (
            previous_reset
            and reset_at
            and reset_at != previous_reset
            and previous_value is not None
            and last_at < close_at <= item_at
        ):
            # Close the old period at the reset instant, break the line, then open
            # the new period at its own start (100%). The break (null) keeps the
            # two periods as separate polylines instead of drawing a vertical
            # connector across the idle gap.
            expanded.append({"at": close_at, "remaining_percent": previous_value})
            expanded.append({"at": close_at, "remaining_percent": None})
            close_moment = _parse_iso_timestamp(close_at)
            open_at = (
                new_start.isoformat()
                if new_start is not None and close_moment is not None and new_start > close_moment
                else close_at
            )
            expanded.append({"at": open_at, "remaining_percent": 100})
        expanded.append(item)
        try:
            previous_value = int(item.get("remaining_percent"))
        except (TypeError, ValueError):
            previous_value = None
        if reset_at:
            previous_reset = reset_at
    return expanded


def _build_reset_periods(
    points: list[dict[str, Any]],
    end: dt.datetime | None,
    period: dt.timedelta,
    span: dt.timedelta,
) -> list[dict[str, str]]:
    """Distinct reset cycles in the display range as ``{start_at, reset_at}``.

    A cycle's ``start_at`` is its own ``reset_at`` minus one ``period`` (the
    first-use trigger), so the previous cycle's end and the current cycle's start
    stay separate instead of being collapsed into one timestamp.
    """

    if end is None:
        return []
    moments: list[dt.datetime] = []
    seen: set[str] = set()
    for item in points:
        moment = _parse_iso_timestamp(item.get("reset_at"))
        if moment is None or moment > end or moment <= end - span:
            continue
        key = moment.isoformat()
        if key in seen:
            continue
        seen.add(key)
        moments.append(moment)
    if end.isoformat() not in seen:
        moments.append(end)
    moments.sort()
    moments = moments[-GENERAL_QUOTA_WINDOW_PERIODS:]
    return [
        {"start_at": (moment - period).isoformat(), "reset_at": moment.isoformat()}
        for moment in moments
    ]


def build_codex_general_quota_window(
    groups: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Plot the general quota for the current reset window and the previous one.

    All history stays persisted; the chart only shows ``[reset - span, reset]`` where
    ``span`` is ``GENERAL_QUOTA_WINDOW_PERIODS`` reset periods, so the previous
    cycle's burn rhythm stays visible next to the current one.  The time axis is
    rounded outward to local midnight so the daily 00:00 snapshots land on day
    boundaries, while ``reset_at`` stays precise to the minute.

    ``period_minutes`` is the reset period itself (7 days for the weekly limit).
    ``periods`` lists each reset cycle individually as ``{start_at, reset_at}``:
    the timer only starts on first use after a reset, so a period's ``start_at`` is
    its own ``reset_at`` minus one period and is *not* the previous period's reset.
    The client draws one even-burn reference line per period from that pair.
    """

    general = next((item for item in groups if isinstance(item, dict) and item.get("id") == "codex"), None)
    if general is None and groups:
        general = groups[0]
    weekly = _general_quota_window(groups)

    history_points: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        moment = _parse_history_timestamp(snapshot)
        if moment is None:
            continue
        try:
            remaining = int(snapshot.get("remaining_percent"))
        except (TypeError, ValueError):
            continue
        history_points.append({
            "at": moment.isoformat(),
            "remaining_percent": max(0, min(100, remaining)),
            "reset_at": str(snapshot.get("reset_at") or "").strip(),
        })
    history_points.sort(key=lambda item: item["at"])

    weekly_minutes = int(weekly.get("window_minutes") or 0) if weekly else 0
    period = dt.timedelta(
        minutes=weekly_minutes or DEFAULT_GENERAL_QUOTA_WINDOW_DAYS * 24 * 60
    )
    span = period * GENERAL_QUOTA_WINDOW_PERIODS

    end = _parse_iso_timestamp(weekly.get("reset_at")) if weekly else None
    if end is None:
        # Without a live snapshot, fall back to the newest persisted reset marker.
        for snapshot in reversed(snapshots):
            if not isinstance(snapshot, dict):
                continue
            candidate = _parse_iso_timestamp(snapshot.get("reset_at"))
            if candidate is not None:
                end = candidate
                break
    if end is None and history_points:
        end = dt.datetime.fromisoformat(history_points[-1]["at"])

    periods = _build_reset_periods(history_points, end, period, span)

    raw_start = end - span if end else None
    local_tz = dt.datetime.now().astimezone().tzinfo
    start = _floor_to_local_day(raw_start, local_tz) if raw_start else None
    axis_end = _ceil_to_local_day(end, local_tz) if end else None

    points = [
        item
        for item in history_points
        if raw_start is None or end is None or raw_start <= dt.datetime.fromisoformat(item["at"]) <= end
    ]
    points = _expand_reset_breaks(points, period)

    name = str(general.get("name") or "") if isinstance(general, dict) else ""
    if not name:
        name = "通用使用限额"
    if weekly:
        remaining = weekly.get("remaining_percent")
    elif history_points:
        remaining = history_points[-1]["remaining_percent"]
    else:
        remaining = None

    return {
        "name": name,
        "window_start": start.isoformat() if start else "",
        "window_end": axis_end.isoformat() if axis_end else "",
        "reset_at": end.isoformat() if end else "",
        "period_minutes": int(period.total_seconds() // 60),
        "periods": periods,
        "remaining_percent": remaining,
        "points": points,
    }


def get_codex_quota_snapshot_path() -> Path:
    return get_settings().data_dir / "codex" / "quota_snapshot.json"


def save_codex_quota_snapshot(
    groups: list[dict[str, Any]],
    observed_at: dt.datetime,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    resolved_path = path or get_codex_quota_snapshot_path()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "observed_at": observed_at.replace(microsecond=0).isoformat(),
        "groups": groups,
    }
    temp_path = resolved_path.with_suffix(f"{resolved_path.suffix}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, resolved_path)
    return payload


def load_codex_quota_snapshot(*, path: Path | None = None) -> dict[str, Any]:
    resolved_path = path or get_codex_quota_snapshot_path()
    if not resolved_path.exists():
        return {"observed_at": "", "groups": []}
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"observed_at": "", "groups": []}
    groups = payload.get("groups") if isinstance(payload, dict) else []
    return {
        "observed_at": str(payload.get("observed_at") or "") if isinstance(payload, dict) else "",
        "groups": [item for item in groups if isinstance(item, dict)] if isinstance(groups, list) else [],
    }


def record_codex_general_quota(groups: list[dict[str, Any]], *, now: dt.datetime | None = None) -> bool:
    weekly = _general_quota_window(groups)
    if not weekly:
        return False
    try:
        remaining = int(weekly.get("remaining_percent"))
    except (TypeError, ValueError):
        return False
    record_codex_weekly_quota_snapshot(
        remaining_percent=remaining,
        observed_at=now or dt.datetime.now(),
        reset_at=str(weekly.get("reset_at") or ""),
    )
    return True


def collect_codex_quota_snapshot(*, now: dt.datetime | None = None) -> dict[str, Any]:
    """Read the live quota buckets once and persist them as the latest snapshot."""

    groups = read_codex_quota_groups()
    observed_at = (now or dt.datetime.now()).replace(microsecond=0)
    save_codex_quota_snapshot(groups, observed_at)
    record_codex_general_quota(groups, now=observed_at)
    return {"observed_at": observed_at.isoformat(), "groups": groups}


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
    observed_text = observed_at.replace(microsecond=0).isoformat()
    bucket = _history_bucket(observed_at)
    record = {
        "date": observed_at.date().isoformat(),
        "remaining_percent": int(remaining_percent),
        "observed_at": observed_text,
        "bucket": bucket,
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
            if str(item.get("bucket") or "") != bucket
        ]
        snapshots.append(record)
        snapshots.sort(key=lambda item: str(item.get("observed_at") or item.get("date") or ""))
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
    snapshot_path: Path | None = None,
    rate_limits_reader: Callable[..., dict[str, Any]] = read_codex_rate_limits,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    observed_at = (now or dt.datetime.now()).replace(microsecond=0)
    payload = rate_limits_reader(
        timeout_seconds=timeout_seconds,
        config_overrides=CODEX_CHATGPT_AUTH_OVERRIDE,
    )
    parsed = parse_codex_rate_limits_snapshot(payload)
    record = record_codex_weekly_quota_snapshot(
        remaining_percent=int(parsed["remaining_percent"]),
        observed_at=observed_at,
        reset_at=str(parsed.get("reset_at") or ""),
        path=history_path,
    )
    save_codex_quota_snapshot(
        parse_codex_rate_limit_groups(payload),
        observed_at,
        path=snapshot_path,
    )
    print(
        "Codex weekly quota recorded: "
        f"date={record['date']} remaining={record['remaining_percent']}% observed_at={record['observed_at']}"
    )
    return record
