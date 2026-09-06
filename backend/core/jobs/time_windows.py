from __future__ import annotations

from datetime import datetime, time, timedelta


def clip_daily_retry_to_window(
    candidate: datetime, *, now: datetime, start: str | time, end: str | time
) -> datetime:
    """Keep a retry in a same-day [start, end) window; otherwise use its next opening.

    Shared by background jobs and Fanxiu. Closing time is exclusive, so a
    03:50 failure with a ten-minute delay in [00:00, 04:00) waits until tomorrow.
    This controls new attempts, not cancellation of an attempt already running.
    """
    start_clock = time.fromisoformat(start) if isinstance(start, str) else start
    end_clock = time.fromisoformat(end) if isinstance(end, str) else end
    open_at = datetime.combine(now.date(), start_clock)
    close_at = datetime.combine(now.date(), end_clock)
    if now >= close_at or candidate >= close_at:
        return datetime.combine(now.date() + timedelta(days=1), start_clock)
    return max(candidate, open_at)
