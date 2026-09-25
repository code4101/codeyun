from __future__ import annotations

from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation import kernel_scheduler_control
from backend.core.fanxiu.data_annotation.effective_time import job_effective_time
from backend.core.fanxiu.data_annotation.job_times import clip_daily_retry_to_window
from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_executor
from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import (
    default_kernel_scheduler_tasks,
)


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("2026-09-08 12:59:59", "2026-09-08 13:00:00"),
        ("2026-09-08 13:00:00", "2026-09-09 00:00:00"),
        ("2026-09-08 21:30:00", "2026-09-09 00:00:00"),
        ("2026-09-09 00:00:00", "2026-09-09 13:00:00"),
        ("2026-09-13 13:00:00", "2026-09-14 13:00:00"),
        ("2026-09-13 21:30:00", "2026-09-14 13:00:00"),
        ("2026-09-14 00:00:00", "2026-09-14 13:00:00"),
    ],
)
def test_mojie_followup_uses_next_midnight_after_noon(current, expected):
    from backend.core.fanxiu.data_annotation.tasks.daily_foundation import DailyFoundationTaskMixin

    runner = DailyFoundationTaskMixin()
    assert runner._next_mojie_raid_followup_time_text(datetime.fromisoformat(current)) == expected


@pytest.mark.parametrize("day", [9, 10, 11, 12, 13])
def test_mojie_week_completion_starts_thursday_and_resumes_monday_noon(day):
    from backend.core.fanxiu.data_annotation.tasks.daily_foundation import DailyFoundationTaskMixin

    runner = DailyFoundationTaskMixin()
    current = datetime(2026, 9, day)
    assert runner._mojie_raid_completion_window_open(current) is (day >= 10)
    assert runner._next_mojie_raid_week_start_time_text(current) == "2026-09-14 13:00:00"


@pytest.mark.parametrize(
    ("task_id", "expected"),
    [
        ("daily-lundao-seat", "2026-08-14 15:30:00"),
        ("daily-lingmai-seat", "2026-08-14 17:30:00"),
        ("legacy-daily-dongtian-clear", "2026-08-14 21:00:00"),
    ],
)
def test_windowed_job_technical_retry_rolls_past_22_to_tomorrow(
    task_id: str,
    expected: str,
) -> None:
    tasks = default_kernel_scheduler_tasks(datetime(2026, 8, 13, 21, 0))
    task = next(item for item in tasks if item["id"] == task_id)

    assert kernel_scheduler_control.scheduler_task_retry_time(
        task,
        datetime(2026, 8, 13, 21, 59, 30),
    ) == expected


def test_windowed_job_technical_retry_before_22_stays_today() -> None:
    tasks = default_kernel_scheduler_tasks(datetime(2026, 8, 13, 21, 0))
    task = next(item for item in tasks if item["id"] == "daily-lingmai-seat")

    assert kernel_scheduler_control.scheduler_task_retry_time(
        task,
        datetime(2026, 8, 13, 21, 48, 0),
    ) == "2026-08-13 21:58:00"


def test_xianmeng_has_no_retired_child_scheduler_job_after_consolidation() -> None:
    tasks = default_kernel_scheduler_tasks(datetime(2026, 8, 13, 21, 0))
    assert not any(item["id"] == "legacy-daily-xianmeng" for item in tasks)
    assert sum(item["id"] == "ranking-lifecycle" for item in tasks) == 1


def test_activity_end_boundary_itself_is_already_closed() -> None:
    now = datetime(2026, 8, 13, 21, 59, 0)

    assert clip_daily_retry_to_window(
        datetime(2026, 8, 13, 22, 0, 0),
        now=now,
        start="21:30",
        end="22:00",
    ) == datetime(2026, 8, 14, 21, 30, 0)


def test_business_rechecks_use_the_same_22_clock_boundary(monkeypatch) -> None:
    now = datetime(2026, 8, 13, 21, 55, 0)
    runner = create_behavior_tree_executor()
    scheduled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append((task_id, next_time)),
    )
    monkeypatch.setattr(runner, "_log", lambda *_args, **_kwargs: None)

    with job_effective_time({"effective_now": now}):
        lundao = runner._schedule_daily_lundao_next_check({}, message="test", seconds=600)
        lingmai = runner._schedule_daily_lingmai_next_check({}, message="test", seconds=600)
        dongtian = runner._record_daily_entry_not_found_retry(
            {},
            task_id="legacy-daily-dongtian-clear",
            task_type="daily_dongtian_clear",
            label="洞天_行动力",
            seconds=600,
            daily_start_time="21:30",
            daily_end_time="22:00",
        )

    assert lundao == "2026-08-14 15:30:00"
    assert lingmai == "2026-08-14 17:30:00"
    assert dongtian == "2026-08-14 21:30:00"
    assert [next_time for _task_id, next_time in scheduled] == [
        lundao,
        lingmai,
        dongtian,
    ]
