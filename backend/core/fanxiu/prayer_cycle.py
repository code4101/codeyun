from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


PRAYER_CYCLE_TIMEZONE = ZoneInfo("Asia/Shanghai")
PRAYER_CYCLE_NAMES: tuple[str, ...] = ("炼丹", "淬体", "灵兽", "洗灵", "仙花")
PRAYER_CYCLE_ANCHOR = datetime(2026, 6, 29, 0, 0, 0, tzinfo=PRAYER_CYCLE_TIMEZONE)
PRAYER_CYCLE_ANCHOR_NAME = "淬体"


@dataclass(frozen=True)
class PrayerCycleWeek:
    name: str
    week_start: datetime
    week_end: datetime
    index: int


def prayer_cycle_week_start(now: datetime | None = None) -> datetime:
    current = now or datetime.now(PRAYER_CYCLE_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=PRAYER_CYCLE_TIMEZONE)
    else:
        current = current.astimezone(PRAYER_CYCLE_TIMEZONE)
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start - timedelta(days=day_start.weekday())


def prayer_cycle_week(now: datetime | None = None, *, offset_weeks: int = 0) -> PrayerCycleWeek:
    week_start = prayer_cycle_week_start(now) + timedelta(weeks=int(offset_weeks))
    anchor_index = PRAYER_CYCLE_NAMES.index(PRAYER_CYCLE_ANCHOR_NAME)
    weeks_delta = (week_start.date() - PRAYER_CYCLE_ANCHOR.date()).days // 7
    index = (anchor_index + weeks_delta) % len(PRAYER_CYCLE_NAMES)
    return PrayerCycleWeek(
        name=PRAYER_CYCLE_NAMES[index],
        week_start=week_start,
        week_end=week_start + timedelta(weeks=1),
        index=index,
    )


def current_prayer_cycle(now: datetime | None = None) -> str:
    return prayer_cycle_week(now).name


def prayer_entry_category(entry_text: str) -> str | None:
    """将已定位的真实入口名映射为业务类别；未知名称不按日历猜测。

    2026-10-05 真机炼丹周入口为“灵草祈愿”。其他类别保留原有名称，
    只接收完整入口标签，合行文字须先由入口定位组件拆分。
    """
    text = "".join(str(entry_text or "").split()).replace("祈原", "祈愿")
    names = {name + "祈愿": name for name in PRAYER_CYCLE_NAMES}
    names["灵草祈愿"] = "炼丹"
    names["锻体祈愿"] = "淬体"
    return names.get(text)


def next_prayer_cycle(now: datetime | None = None) -> str:
    return prayer_cycle_week(now, offset_weeks=1).name
