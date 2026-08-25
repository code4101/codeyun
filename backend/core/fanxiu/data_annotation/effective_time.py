from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, tzinfo
from typing import Any, Iterator


EFFECTIVE_NOW_PAYLOAD_KEY = "effective_now"

_effective_now: ContextVar[datetime | None] = ContextVar(
    "fanxiu_job_effective_now",
    default=None,
)


def parse_effective_now(value: Any) -> datetime | None:
    """Parse one optional Job-Cell business clock override.

    Fanxiu business schedules historically use local naive datetimes.  An
    offset-aware input is therefore converted to the machine's local timezone
    and made naive before it enters existing Job arithmetic.
    """

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                "effective_now 必须是 ISO 日期时间，例如 2026-08-13 21:15:00"
            ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


@contextmanager
def job_effective_time(payload: dict[str, Any] | None) -> Iterator[datetime]:
    """Apply an optional effective clock for exactly one Job Cell."""

    configured = parse_effective_now((payload or {}).get(EFFECTIVE_NOW_PAYLOAD_KEY))
    token = _effective_now.set(configured)
    try:
        yield job_now()
    finally:
        _effective_now.reset(token)


def job_now(tz: tzinfo | None = None) -> datetime:
    """Return the current Job business time, defaulting to the real clock."""

    configured = _effective_now.get()
    if configured is None:
        return datetime.now(tz)
    if tz is None:
        return configured
    return configured.astimezone().astimezone(tz)


def job_today() -> date:
    """Return the current Job business date."""

    return job_now().date()


__all__ = [
    "EFFECTIVE_NOW_PAYLOAD_KEY",
    "job_effective_time",
    "job_now",
    "job_today",
    "parse_effective_now",
]
