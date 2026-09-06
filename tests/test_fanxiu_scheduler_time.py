from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.kernel_scheduler_time import (
    effective_scheduler_time,
    scheduler_task_time_view,
    scheduler_time_bias_minutes,
    scheduler_time_sequence_groups,
)
from backend.core.fanxiu.data_annotation.job_times import next_business_time
from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import (
    default_kernel_scheduler_tasks,
)
from backend.core.fanxiu.behavior_tree.kernel_scheduler import (
    create_behavior_tree_executor,
)


def test_time_sequence_only_biases_tasks_with_the_same_original_datetime():
    tasks = [
        {"id": "a", "next_time": "2026-07-30 21:30:00"},
        {"id": "b", "next_time": "2026-07-30 21:30:00"},
        {"id": "c", "next_time": "2026-07-31 21:30:00"},
    ]
    sequence = {"21:30": ["a", "b", "c"]}

    assert scheduler_time_bias_minutes(tasks[0], tasks, sequence) == 0
    assert scheduler_time_bias_minutes(tasks[1], tasks, sequence) == 1
    assert scheduler_time_bias_minutes(tasks[2], tasks, sequence) == 0


def test_time_sequence_compacts_missing_configured_jobs():
    tasks = [
        {"id": "a", "next_time": "2026-07-30 21:30:00"},
        {"id": "c", "next_time": "2026-07-30 21:30:00"},
    ]
    sequence = {"21:30": ["a", "b", "c"]}

    assert scheduler_time_bias_minutes(tasks[1], tasks, sequence) == 1


def test_time_sequence_discovers_all_actual_members_and_keeps_saved_order():
    tasks = [
        {"id": "new", "next_time": "2026-09-07 00:00:00"},
        {"id": "a", "next_time": "2026-09-07 00:00:00"},
        {"id": "b", "next_time": "2026-09-07 00:00:00"},
        {"id": "moved", "next_time": "2026-09-07 05:00:00"},
    ]
    sequence = {"00:00": ["b", "moved", "a"]}
    groups = scheduler_time_sequence_groups(tasks, sequence)
    assert groups[0]["task_ids"] == ["b", "a", "new"]
    assert [item["bias_minutes"] for item in groups[0]["items"]] == [0, 1, 2]
    assert scheduler_time_bias_minutes(tasks[0], tasks, sequence) == 2
    assert tasks[0]["next_time"] == "2026-09-07 00:00:00"


def test_effective_time_is_derived_without_mutating_original_next_time():
    task = {"id": "b", "next_time": "2026-07-30 21:30:00"}
    tasks = [
        {"id": "a", "next_time": "2026-07-30 21:30:00"},
        task,
    ]
    sequence = {"21:30": ["a", "b"]}

    assert effective_scheduler_time(task, tasks, sequence) == datetime(
        2026, 7, 30, 21, 31
    )
    assert scheduler_task_time_view(task, tasks, sequence) == {
        "id": "b",
        "original_next_time": "2026-07-30 21:30:00",
        "next_time": "2026-07-30 21:31:00",
        "schedule_bias_minutes": 1,
    }
    assert task["next_time"] == "2026-07-30 21:30:00"


def test_mojie_raid_completion_sleeps_until_next_monday_at_ten():
    runner = create_behavior_tree_executor()

    assert runner._next_mojie_raid_week_start_time_text(
        datetime(2026, 7, 30, 14, 0)
    ) == "2026-08-03 10:00:00"


def test_mojie_raid_completion_requires_thursday_and_confirmed_zero(monkeypatch):
    runner = create_behavior_tree_executor()
    persisted: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: persisted.append((task_id, next_time)),
    )

    assert runner._mojie_raid_completion_window_open(datetime(2026, 7, 29, 23, 59)) is False
    assert runner._mojie_raid_completion_window_open(datetime(2026, 7, 30, 0, 0)) is True
    with pytest.raises(ValueError, match="周四起"):
        runner._schedule_next_mojie_raid_week(
            {},
            reason="test",
            confirmed_remaining=0,
            confirmed_at=datetime(2026, 7, 29, 23, 59),
        )
    with pytest.raises(ValueError, match="剩余次数为 0"):
        runner._schedule_next_mojie_raid_week(
            {},
            reason="test",
            confirmed_remaining=1,
            confirmed_at=datetime(2026, 7, 30, 10, 0),
        )

    assert runner._schedule_next_mojie_raid_week(
        {},
        reason="test",
        confirmed_remaining=0,
        confirmed_at=datetime(2026, 7, 30, 10, 0),
    ) == "2026-08-03 10:00:00"
    assert persisted == [("legacy-daily-mojie-raid", "2026-08-03 10:00:00")]


def test_mojie_raid_zero_before_thursday_is_rechecked_on_thursday():
    runner = create_behavior_tree_executor()

    assert runner._next_mojie_raid_thursday_verification_time_text(
        datetime(2026, 7, 27, 10, 0)
    ) == "2026-07-30 10:00:00"


def test_mojie_raid_followups_use_thirteen_and_twenty_one_thirty():
    runner = create_behavior_tree_executor()

    assert runner._next_mojie_raid_followup_time_text(
        datetime(2026, 8, 3, 10, 5)
    ) == "2026-08-03 13:00:00"
    assert runner._next_mojie_raid_followup_time_text(
        datetime(2026, 8, 3, 13, 1)
    ) == "2026-08-03 21:30:00"
    assert runner._next_mojie_raid_followup_time_text(
        datetime(2026, 8, 3, 21, 31)
    ) == "2026-08-04 13:00:00"


def test_mojie_raid_sunday_settlement_boundary():
    runner = create_behavior_tree_executor()
    assert not runner._mojie_raid_settlement_only(datetime(2026, 9, 6, 21, 29, 59))
    assert runner._mojie_raid_settlement_only(datetime(2026, 9, 6, 21, 30))
    assert not runner._mojie_raid_settlement_only(datetime(2026, 9, 5, 22))
    assert runner._next_mojie_raid_followup_time_text(
        datetime(2026, 9, 6, 13)
    ) == "2026-09-06 21:30:00"
    assert runner._next_mojie_raid_followup_time_text(
        datetime(2026, 9, 6, 21, 30)
    ) == "2026-09-07 10:00:00"


def test_mojie_raid_closed_admission_persists_next_week(monkeypatch):
    from backend.core.fanxiu.data_annotation import behavior_tree_executor

    runner = create_behavior_tree_executor()
    persisted = []
    monkeypatch.setattr(runner, "_persist_scheduler_task_next_time",
                        lambda task_id, next_time: persisted.append((task_id, next_time)))
    payload = {"__scheduler_task_id": "legacy-daily-mojie-raid"}
    monkeypatch.setattr(behavior_tree_executor, "_now", lambda: datetime(2026, 9, 6, 21, 59, 59))
    assert runner.daily_mojie_raid_admission(payload) is None
    assert persisted == []
    monkeypatch.setattr(behavior_tree_executor, "_now", lambda: datetime(2026, 9, 6, 22))
    decision = runner.daily_mojie_raid_admission(payload)
    assert decision["result"] == "success"
    assert "next_time" not in decision
    assert persisted == [("legacy-daily-mojie-raid", "2026-09-07 10:00:00")]


def test_business_time_primitive_supports_daily_and_weekday_rules():
    assert next_business_time(
        ("13:00", "21:30"),
        now=datetime(2026, 8, 3, 13, 1),
    ) == "2026-08-03 21:30:00"
    assert next_business_time(
        ("23:00",),
        now=datetime(2026, 8, 8, 23, 1),
        weekdays=(0, 1, 2, 3, 4, 5),
    ) == "2026-08-10 23:00:00"


def test_default_jobs_have_one_time_fact_and_no_executable_trigger_type():
    jobs = default_kernel_scheduler_tasks(datetime(2026, 7, 30, 9, 0))
    forbidden = {
        "schedule_kind",
        "trigger_kind",
        "schedule_times",
        "weekdays",
        "schedule_offsets_minutes",
    }

    assert jobs
    assert all(forbidden.isdisjoint(job) for job in jobs)
    assert all("trigger_description" in job for job in jobs)
    mojie = next(job for job in jobs if job["id"] == "legacy-daily-mojie-raid")
    assert mojie["next_time"] == "2026-08-03 10:00:00"


def test_normal_job_return_is_always_a_success_terminal():
    runner = create_behavior_tree_executor()

    assert runner._normalize_task_result("skipped") == ("success", "")
    assert runner._normalize_task_result({
        "result": "business_not_finished",
        "message": "已设置稍后复查",
    }) == ("success", "已设置稍后复查")
