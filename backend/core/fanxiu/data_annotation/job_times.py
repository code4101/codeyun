from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

from backend.core.fanxiu.data_annotation.effective_time import job_now


def clip_daily_retry_to_window(
    candidate: datetime,
    *,
    now: datetime | None = None,
    start: str | time,
    end: str | time,
) -> datetime:
    """Keep a retry inside today's activity window or roll it to tomorrow.

    The end boundary is exclusive: an activity advertised as ending at 22:00
    cannot accept a retry at exactly 22:00.  Once the candidate reaches that
    boundary, the next meaningful attempt is tomorrow's first trigger.
    """

    current = now or job_now()
    start_clock = (
        datetime.strptime(start, "%H:%M").time()
        if isinstance(start, str)
        else start
    )
    end_clock = (
        datetime.strptime(end, "%H:%M").time()
        if isinstance(end, str)
        else end
    )
    close_at = datetime.combine(current.date(), end_clock)
    if current >= close_at or candidate >= close_at:
        return datetime.combine(current.date() + timedelta(days=1), start_clock)
    return candidate


def next_business_time(
    clocks: Iterable[str],
    *,
    now: datetime | None = None,
    weekdays: Iterable[int] = range(7),
) -> str:
    """Return the next absolute time selected by a job's business rule.

    This is a convenience for job code, not a Scheduler recurrence engine.
    Callers may use it for a simple daily/weekly branch, combine it with
    cooldown or reward facts, or ignore it and calculate another absolute
    timestamp.  The final decision must remain inside the job because only the
    job has enough business context to choose correctly.
    """

    current = now or job_now()
    allowed_days = {int(day) for day in weekdays}
    parsed_clocks = [datetime.strptime(str(clock), "%H:%M").time() for clock in clocks]
    candidates: list[datetime] = []
    for day_offset in range(8):
        day = current.date() + timedelta(days=day_offset)
        if day.weekday() not in allowed_days:
            continue
        for clock in parsed_clocks:
            candidate = datetime.combine(day, clock)
            if candidate > current:
                candidates.append(candidate)
    if not candidates:
        raise ValueError("业务触发规则没有可用的未来时间")
    return min(candidates).strftime("%Y-%m-%d %H:%M:%S")


def next_biweekly_time(
    clock: str,
    *,
    anchor: date,
    now: datetime | None = None,
) -> str:
    """Return a Job-owned two-week ``next_time`` from one confirmed anchor.

    This is deliberately only timestamp arithmetic.  It creates no trigger,
    enablement state, recurrence record, or Scheduler policy.
    """

    current = now or job_now()
    trigger_clock = datetime.strptime(str(clock), "%H:%M").time()
    for day_offset in range(15):
        candidate_day = current.date() + timedelta(days=day_offset)
        if (candidate_day - anchor).days % 14 != 0:
            continue
        candidate = datetime.combine(candidate_day, trigger_clock)
        if candidate > current:
            return candidate.strftime("%Y-%m-%d %H:%M:%S")
    raise ValueError("双周业务规则没有可用的未来时间")
