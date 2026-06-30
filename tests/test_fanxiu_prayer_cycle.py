from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.core.fanxiu.prayer_cycle import current_prayer_cycle, next_prayer_cycle, prayer_cycle_week


SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_prayer_cycle_uses_known_anchor_week() -> None:
    now = datetime(2026, 6, 29, 12, 0, tzinfo=SHANGHAI)

    assert current_prayer_cycle(now) == "淬体"
    assert next_prayer_cycle(now) == "灵兽"


def test_prayer_cycle_previous_week_is_liandan() -> None:
    now = datetime(2026, 6, 28, 23, 59, tzinfo=SHANGHAI)

    assert current_prayer_cycle(now) == "炼丹"
    assert next_prayer_cycle(now) == "淬体"


def test_prayer_cycle_switches_at_monday_zero() -> None:
    before = datetime(2026, 7, 5, 23, 59, tzinfo=SHANGHAI)
    after = datetime(2026, 7, 6, 0, 0, tzinfo=SHANGHAI)

    assert current_prayer_cycle(before) == "淬体"
    assert current_prayer_cycle(after) == "灵兽"


def test_prayer_cycle_wraps_every_five_weeks() -> None:
    week = prayer_cycle_week(datetime(2026, 8, 3, 8, 0, tzinfo=SHANGHAI))

    assert week.name == "淬体"
    assert week.week_start == datetime(2026, 8, 3, 0, 0, tzinfo=SHANGHAI)
