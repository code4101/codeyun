"""Business-owned seat checks; displacement events may wake the same Job early."""

from datetime import datetime, timedelta

from backend.core.fanxiu.data_annotation.effective_time import job_now


def next_dongtian_seating_at(
    now: datetime | None = None, *, include_current: bool = False,
) -> datetime:
    """Daily 10:00, plus Monday's six competition-window rechecks.

    Completion selects a strictly future check. Initial creation may include
    the current exact trigger. An event-triggered check preserves the next
    fixed check rather than disabling this Job or inventing a retry interval.
    """
    current = now or job_now()
    for offset in (0, 1):
        day = current + timedelta(days=offset)
        clocks = [(10, 0)]
        if day.weekday() == 0:
            clocks += [(10, 30), (11, 0), (12, 0), (13, 0), (16, 0), (20, 0)]
        for hour, minute in clocks:
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > current or (include_current and candidate == current):
                return candidate
    raise AssertionError("Daily 10:00 always supplies a next check")
