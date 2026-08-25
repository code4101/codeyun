from datetime import datetime

from backend.core.fanxiu.data_annotation.scheduler_time import (
    effective_scheduler_time,
    scheduler_task_time_view,
    scheduler_time_bias_minutes,
)
from backend.core.fanxiu.data_annotation.job_times import next_business_time
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.behavior_tree.runtime import (
    create_behavior_tree_runtime_runner,
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
    runner = create_behavior_tree_runtime_runner()

    assert runner._next_mojie_raid_week_start_time_text(
        datetime(2026, 7, 30, 14, 0)
    ) == "2026-08-03 10:00:00"


def test_mojie_raid_followups_use_thirteen_and_twenty_one_thirty():
    runner = create_behavior_tree_runtime_runner()

    assert runner._next_mojie_raid_followup_time_text(
        datetime(2026, 8, 3, 10, 5)
    ) == "2026-08-03 13:00:00"
    assert runner._next_mojie_raid_followup_time_text(
        datetime(2026, 8, 3, 13, 1)
    ) == "2026-08-03 21:30:00"
    assert runner._next_mojie_raid_followup_time_text(
        datetime(2026, 8, 3, 21, 31)
    ) == "2026-08-04 13:00:00"


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
    jobs = default_data_annotation_scheduler_tasks(datetime(2026, 7, 30, 9, 0))
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
    runner = create_behavior_tree_runtime_runner()

    assert runner._normalize_runtime_task_result("skipped") == ("success", "")
    assert runner._normalize_runtime_task_result({
        "result": "business_not_finished",
        "message": "已设置稍后复查",
    }) == ("success", "已设置稍后复查")
