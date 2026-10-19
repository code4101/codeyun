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


DONGTIAN_ACTIVITY_HOURS = (10, 22)


def dongtian_activity_open(now: datetime | None = None) -> bool:
    """Whether the game accepts #279 place clicks right now.

    XianLvMinesMgr.CurTimeCanDo gates every place detail on server hour in
    [10, 22) and otherwise only toasts「非活动时间，请道友10-22点参加活动」, so a
    click stays on #279. This is wall-clock game time, not planned business
    time: an early run before 10:00 must still be refused.
    """
    hour = (now or datetime.now()).hour
    return DONGTIAN_ACTIVITY_HOURS[0] <= hour < DONGTIAN_ACTIVITY_HOURS[1]
