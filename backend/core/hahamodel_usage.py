"""Read the hahamodel.com (New API relay) account consumption.

hahamodel is pay-as-you-go, so there is no provider-side weekly quota. To line
the chart up with the Codex / OpenCode "remaining quota" view, every Monday
00:00 → next Monday 00:00 (local time) week gets an *estimated* weekly quota:

* a finished week's quota is what it actually consumed;
* the running week's quota is its consumption scaled up by the elapsed share of
  the week (3 days and $30 used → $70).

Each week is drawn as remaining = quota - consumed so far, so the slope reads as
burn speed. Credentials come from ``HAHAMODEL_USERNAME`` / ``HAHAMODEL_PASSWORD``
(the repo ``.env`` re-read on every call first, then the process env, so a freshly
written secret works without restarting the backend).
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import requests

from backend.core.settings import ROOT_DIR, get_settings


HAHAMODEL_BASE_URL = "https://hahamodel.com"
HAHAMODEL_USERNAME_ENV = "HAHAMODEL_USERNAME"
HAHAMODEL_PASSWORD_ENV = "HAHAMODEL_PASSWORD"
_HAHAMODEL_TIMEOUT_SECONDS = 20.0
# New API's default: 500000 quota units per USD; /api/status overrides it.
_DEFAULT_QUOTA_PER_UNIT = 500000.0
_WEEK = dt.timedelta(days=7)
# The chart shows the previous and the running week.
_CHART_WEEKS = 2
# Backfill whole weeks; New API rejects quota-data ranges over 30 days.
_FETCH_WEEKS = 3
# Avoid a near-zero divisor right after Monday 00:00.
_MIN_ELAPSED = dt.timedelta(hours=1)

NOT_CONFIGURED_ERROR = (
    "未配置 hahamodel 账号：运行 uv run python scripts/set_hahamodel_account.py"
)


class HahaModelError(RuntimeError):
    """Raised when the hahamodel API rejects a request."""


def read_hahamodel_credentials(*, env_file: Path | None = None) -> tuple[str, str]:
    values: dict[str, str] = {}
    path = env_file or ROOT_DIR / ".env"
    if path.exists():
        try:
            from dotenv import dotenv_values

            values = {key: str(value or "") for key, value in dotenv_values(path).items()}
        except ImportError:
            values = {}
    username = (values.get(HAHAMODEL_USERNAME_ENV) or os.environ.get(HAHAMODEL_USERNAME_ENV) or "").strip()
    # .env wins: the backend loaded it into os.environ at startup, which goes stale once rewritten.
    password = values.get(HAHAMODEL_PASSWORD_ENV) or os.environ.get(HAHAMODEL_PASSWORD_ENV) or ""
    return username, password


def _local_tz(now: dt.datetime) -> dt.tzinfo:
    return now.astimezone().tzinfo  # type: ignore[return-value]


def _aware(now: dt.datetime | None) -> dt.datetime:
    current = now or dt.datetime.now()
    if current.tzinfo is None:
        current = current.astimezone()
    return current


def week_start_of(moment: dt.datetime) -> dt.datetime:
    """Local Monday 00:00 of the week containing ``moment``."""

    local = moment.astimezone(_local_tz(moment))
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - dt.timedelta(days=day.weekday())


def _api_data(response: requests.Response, action: str) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:
        raise HahaModelError(f"{action}失败：HTTP {response.status_code}") from exc
    if not isinstance(payload, dict) or not payload.get("success"):
        message = payload.get("message") if isinstance(payload, dict) else ""
        raise HahaModelError(f"{action}失败：{message or f'HTTP {response.status_code}'}")
    return payload.get("data")


def login_hahamodel(
    http: requests.Session,
    username: str,
    password: str,
    *,
    timeout_seconds: float = _HAHAMODEL_TIMEOUT_SECONDS,
) -> dict[str, str]:
    """Log ``http`` in; returns the headers New API requires on user endpoints."""

    login = _api_data(
        http.post(
            f"{HAHAMODEL_BASE_URL}/api/user/login",
            json={"username": username, "password": password},
            timeout=timeout_seconds,
        ),
        "登录 hahamodel",
    )
    if not isinstance(login, dict) or login.get("require_2fa"):
        raise HahaModelError("hahamodel 账号开启了两步验证，无法自动登录")
    # Newer New API builds return a bearer token; older ones rely on the session cookie.
    user = login.get("user") if isinstance(login.get("user"), dict) else login
    headers = {"New-Api-User": str(user.get("id") or "")}
    token = str(login.get("access_token") or "")
    if token:
        headers["Authorization"] = f"{login.get('token_type') or 'Bearer'} {token}"
    return headers


def fetch_hahamodel_account(
    username: str,
    password: str,
    *,
    start: dt.datetime,
    end: dt.datetime,
    timeout_seconds: float = _HAHAMODEL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Log in and read the profile plus hourly consumption between ``start`` and ``end``."""

    with requests.Session() as http:
        headers = login_hahamodel(http, username, password, timeout_seconds=timeout_seconds)
        status = _api_data(http.get(f"{HAHAMODEL_BASE_URL}/api/status", timeout=timeout_seconds), "读取站点配置")
        profile = _api_data(
            http.get(f"{HAHAMODEL_BASE_URL}/api/user/self", headers=headers, timeout=timeout_seconds),
            "读取账户额度",
        )
        hourly = _api_data(
            http.get(
                f"{HAHAMODEL_BASE_URL}/api/data/self",
                params={"start_timestamp": int(start.timestamp()), "end_timestamp": int(end.timestamp())},
                headers=headers,
                timeout=timeout_seconds,
            ),
            "读取消耗明细",
        )
    return {"status": status or {}, "profile": profile or {}, "hourly": hourly or []}


def _quota_per_unit(status: dict[str, Any]) -> float:
    try:
        value = float(status.get("quota_per_unit") or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else _DEFAULT_QUOTA_PER_UNIT


def sum_hourly_usd(rows: list[Any], quota_per_unit: float) -> dict[str, float]:
    """Hour bucket (unix seconds, as text) → USD, summed across models."""

    hours: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            hour = int(row.get("created_at"))
            quota = float(row.get("quota") or 0)
        except (TypeError, ValueError):
            continue
        key = str(hour - hour % 3600)
        hours[key] = hours.get(key, 0.0) + quota / quota_per_unit
    return hours


def get_hahamodel_snapshot_path() -> Path:
    return get_settings().data_dir / "codex" / "hahamodel_usage.json"


def get_hahamodel_history_path() -> Path:
    return get_settings().data_dir / "codex" / "hahamodel_usage_history.json"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_hahamodel_history(*, path: Path | None = None) -> dict[str, float]:
    hours = _read_json(path or get_hahamodel_history_path()).get("hours")
    result: dict[str, float] = {}
    for key, value in (hours or {}).items() if isinstance(hours, dict) else ():
        try:
            result[str(int(key))] = float(value)
        except (TypeError, ValueError):
            continue
    return result


def merge_hahamodel_history(
    fetched: dict[str, float],
    *,
    start: dt.datetime,
    end: dt.datetime,
    path: Path | None = None,
) -> dict[str, float]:
    """Replace the fetched range wholesale and keep older hours untouched."""

    resolved_path = path or get_hahamodel_history_path()
    low, high = int(start.timestamp()), int(end.timestamp())
    hours = {key: value for key, value in read_hahamodel_history(path=resolved_path).items() if not low <= int(key) <= high}
    hours.update({key: round(value, 6) for key, value in fetched.items()})
    ordered = dict(sorted(hours.items(), key=lambda item: int(item[0])))
    _write_json(resolved_path, {"version": 1, "hours": ordered})
    return ordered


def save_hahamodel_snapshot(payload: dict[str, Any], observed_at: dt.datetime, *, path: Path | None = None) -> None:
    _write_json(
        path or get_hahamodel_snapshot_path(),
        {"version": 1, "observed_at": observed_at.replace(microsecond=0).isoformat(), "payload": payload},
    )


def load_hahamodel_snapshot(*, path: Path | None = None) -> dict[str, Any]:
    data = _read_json(path or get_hahamodel_snapshot_path())
    payload = data.get("payload")
    return {
        "observed_at": str(data.get("observed_at") or ""),
        "payload": payload if isinstance(payload, dict) else {},
    }


def _week_quota(used: float, week_start: dt.datetime, now: dt.datetime) -> tuple[float, bool]:
    week_end = week_start + _WEEK
    if now >= week_end:
        return used, True
    elapsed = max(now - week_start, _MIN_ELAPSED)
    return used * (_WEEK / elapsed), False


def build_hahamodel_weekly_window(
    hours: dict[str, float],
    *,
    now: dt.datetime | None = None,
    weeks: int = _CHART_WEEKS,
) -> dict[str, Any]:
    """Monday-aligned weekly "remaining quota" view in USD.

    The y-axis max is the largest estimated weekly quota across every recorded
    week, so one heavy week rescales the older ones too.
    """

    current = _aware(now)
    local_tz = _local_tz(current)
    current = current.astimezone(local_tz)
    buckets = sorted(
        (dt.datetime.fromtimestamp(int(key), tz=local_tz), value)
        for key, value in hours.items()
        if value > 0 and int(key) <= current.timestamp()
    )

    used_by_week: dict[dt.datetime, float] = {}
    for moment, value in buckets:
        start = week_start_of(moment)
        used_by_week[start] = used_by_week.get(start, 0.0) + value

    running = week_start_of(current)
    chart_starts = [running - _WEEK * offset for offset in range(weeks - 1, -1, -1)]
    quotas = {start: _week_quota(used, start, current)[0] for start, used in used_by_week.items()}
    axis_max = max([0.0, *quotas.values()])

    summaries: list[dict[str, Any]] = []
    periods: list[dict[str, Any]] = []
    points: list[dict[str, Any]] = []
    for start in chart_starts:
        end = start + _WEEK
        used = used_by_week.get(start, 0.0)
        quota, complete = _week_quota(used, start, current)
        summaries.append({
            "week_start": start.isoformat(),
            "week_end": end.isoformat(),
            "used": round(used, 4),
            "quota": round(quota, 4),
            "complete": complete,
        })
        periods.append({"start_at": start.isoformat(), "reset_at": end.isoformat(), "start_value": round(quota, 4)})
        if points:
            # Break between weeks: each week restarts from its own quota.
            points.append({"at": start.isoformat(), "value": None})
        remaining = quota
        points.append({"at": start.isoformat(), "value": round(remaining, 4)})
        for moment, value in buckets:
            if not start <= moment < end:
                continue
            # Spread each hour's spend over that hour instead of across idle gaps.
            points.append({"at": moment.isoformat(), "value": round(remaining, 4)})
            remaining -= value
            points.append({"at": min(moment + dt.timedelta(hours=1), current).isoformat(), "value": round(remaining, 4)})
        tail = min(end, current)
        if tail > start:
            points.append({"at": tail.isoformat(), "value": round(remaining, 4)})

    return {
        "window_start": chart_starts[0].isoformat(),
        "window_end": (running + _WEEK).isoformat(),
        "max": round(axis_max, 4),
        "periods": periods,
        "weeks": summaries,
        "points": points,
    }


def read_hahamodel_usage(*, now: dt.datetime | None = None) -> dict[str, Any]:
    current = _aware(now)
    username, password = read_hahamodel_credentials()
    if not username or not password:
        return {"configured": False, "available": False, "error": NOT_CONFIGURED_ERROR}
    start = week_start_of(current) - _WEEK * _FETCH_WEEKS
    try:
        account = fetch_hahamodel_account(username, password, start=start, end=current)
    except (requests.RequestException, HahaModelError) as exc:
        return {"configured": True, "available": False, "error": f"查询 hahamodel 失败：{exc}"}
    per_unit = _quota_per_unit(account["status"])
    profile = account["profile"]

    def usd(field: str) -> float:
        try:
            return round(float(profile.get(field) or 0) / per_unit, 4)
        except (TypeError, ValueError):
            return 0.0

    return {
        "configured": True,
        "available": True,
        "balance": usd("quota"),
        "used_total": usd("used_quota"),
        "request_count": int(profile.get("request_count") or 0),
        "hourly": sum_hourly_usd(account["hourly"], per_unit),
        "fetch_start": start.isoformat(),
        "error": "",
    }


def collect_hahamodel_usage_snapshot(*, now: dt.datetime | None = None) -> dict[str, Any]:
    current = _aware(now).replace(microsecond=0)
    payload = read_hahamodel_usage(now=current)
    hourly = payload.pop("hourly", None)
    fetch_start = payload.pop("fetch_start", "")
    # A failed probe must not overwrite the last good reading.
    if payload.get("available"):
        merge_hahamodel_history(hourly or {}, start=dt.datetime.fromisoformat(fetch_start), end=current)
        save_hahamodel_snapshot(payload, current)
    return {"observed_at": current.isoformat(), "payload": payload}
