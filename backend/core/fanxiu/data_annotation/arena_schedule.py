from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta


DAOFA_TASK_ID = "daily-daofa"
XIANYUAN_DUEL_TASK_ID = "daily-xianyuan-duel"

# 游戏开放窗口与作业策略触发点是两套正交事实：10:00 开放不代表
# Scheduler 要在 10:00 出手。道法保留晚打的后手优势策略。
DAOFA_WEEKDAY_TRIGGER = dt_time(23, 0)
DAOFA_SUNDAY_TRIGGER = dt_time(18, 30)
DAOFA_WINDOW_START = dt_time(10, 0)
DAOFA_SUNDAY_WINDOW_END = dt_time(22, 0)
XIANYUAN_DUEL_WEEKDAY_TRIGGER = dt_time(23, 0)
XIANYUAN_DUEL_SUNDAY_TRIGGER = dt_time(19, 0)
ARENA_WINDOW_END = dt_time(23, 59, 59, 999999)


def _trigger_for_day(
    value: datetime,
    *,
    weekday_trigger: dt_time,
    sunday_trigger: dt_time,
) -> dt_time:
    return sunday_trigger if value.weekday() == 6 else weekday_trigger


def _next_trigger_at(
    now: datetime,
    *,
    weekday_trigger: dt_time,
    sunday_trigger: dt_time,
) -> datetime:
    for day_offset in range(8):
        day = now + timedelta(days=day_offset)
        candidate = datetime.combine(
            day.date(),
            _trigger_for_day(
                day,
                weekday_trigger=weekday_trigger,
                sunday_trigger=sunday_trigger,
            ),
        )
        if candidate > now:
            return candidate
    raise RuntimeError("无法计算竞技作业下次触发时间")


def _next_cycle_trigger_at(
    now: datetime,
    *,
    weekday_trigger: dt_time,
    sunday_trigger: dt_time,
) -> datetime:
    """Return the trigger for the next calendar-day business cycle."""

    next_day = now + timedelta(days=1)
    return datetime.combine(
        next_day.date(),
        _trigger_for_day(
            next_day,
            weekday_trigger=weekday_trigger,
            sunday_trigger=sunday_trigger,
        ),
    )


def daofa_window_text(now: datetime) -> str:
    return "周日 10:00-22:00" if now.weekday() == 6 else "周一至周六 10:00-24:00"


def next_daofa_trigger_at(now: datetime) -> datetime:
    return _next_trigger_at(
        now,
        weekday_trigger=DAOFA_WEEKDAY_TRIGGER,
        sunday_trigger=DAOFA_SUNDAY_TRIGGER,
    )


def next_daofa_cycle_trigger_at(now: datetime) -> datetime:
    return _next_cycle_trigger_at(
        now,
        weekday_trigger=DAOFA_WEEKDAY_TRIGGER,
        sunday_trigger=DAOFA_SUNDAY_TRIGGER,
    )


def daofa_scheduler_in_window(now: datetime) -> bool:
    if now.weekday() == 6:
        # 周日 22:00 是周结算边界；边界本身已属于关闭状态，不能补跑
        # 上一周的次数。周一 10:00 再进入新的周周期。
        return DAOFA_WINDOW_START <= now.time() < DAOFA_SUNDAY_WINDOW_END
    return DAOFA_WINDOW_START <= now.time() <= ARENA_WINDOW_END


def xianyuan_duel_window_text(now: datetime) -> str:
    return "周日 10:00-22:00" if now.weekday() == 6 else "周一至周六 10:00-24:00"


def next_xianyuan_duel_trigger_at(now: datetime) -> datetime:
    return _next_trigger_at(
        now,
        weekday_trigger=XIANYUAN_DUEL_WEEKDAY_TRIGGER,
        sunday_trigger=XIANYUAN_DUEL_SUNDAY_TRIGGER,
    )


def next_xianyuan_duel_cycle_trigger_at(now: datetime) -> datetime:
    return _next_cycle_trigger_at(
        now,
        weekday_trigger=XIANYUAN_DUEL_WEEKDAY_TRIGGER,
        sunday_trigger=XIANYUAN_DUEL_SUNDAY_TRIGGER,
    )


def xianyuan_duel_scheduler_in_window(now: datetime) -> bool:
    if now.weekday() == 6:
        return DAOFA_WINDOW_START <= now.time() < DAOFA_SUNDAY_WINDOW_END
    return DAOFA_WINDOW_START <= now.time() <= ARENA_WINDOW_END
