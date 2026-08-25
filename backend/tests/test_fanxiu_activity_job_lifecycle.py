from __future__ import annotations

from datetime import datetime

import pytest

from backend.core.fanxiu.activity.activity_job_lifecycle import (
    complete_activity_job_lifecycle,
    plan_activity_job_lifecycle,
)


NOW = datetime.fromisoformat("2026-08-19T00:20:00+08:00")
OBSERVATION = {
    "observation_id": "revenue:712",
    "activity_id": 712,
    "is_schedule_occurrence": False,
}
AUTHORITY = {
    "complete": True,
    "source_kind": "revenue_activity_period_runtime_memory",
    "activity_id": 712,
    "instance_key": "revenue:712:2026-08-19",
    "end_at": "2026-08-21T23:59:59+08:00",
}


def _plan(**kwargs):
    return plan_activity_job_lifecycle(
        task_id="wanbao-zhenbao",
        observation=OBSERVATION,
        end_authority=AUTHORITY,
        now=NOW,
        **kwargs,
    )


def test_missing_authoritative_end_fails_closed_even_for_new_instance() -> None:
    decision = plan_activity_job_lifecycle(
        task_id="wanbao-zhenbao",
        observation=OBSERVATION,
        end_authority=None,
        now=NOW,
    )

    assert decision["status"] == "blocked"
    assert decision["next_time"] is None
    assert "权威活动结束时间" in decision["reason"]


def test_untrusted_static_end_source_fails_closed() -> None:
    decision = plan_activity_job_lifecycle(
        task_id="wanbao-zhenbao",
        observation=OBSERVATION,
        end_authority={**AUTHORITY, "source_kind": "static_biweekly_guess"},
        now=NOW,
    )

    assert decision["status"] == "blocked"
    assert decision["next_time"] is None


def test_new_instance_activation_is_the_single_immediate_trigger() -> None:
    decision = _plan(
        previous_completion={
            "instance_key": "revenue:712:old",
            "completed_triggers": ["instance_activation"],
            "resource_count": 4,
        },
        resource_count=20,
    )

    assert decision["trigger"] == "instance_activation"
    assert decision["next_time"] == "2026-08-19 00:20:00"
    assert decision["completion_token"]["instance_key"] == AUTHORITY["instance_key"]
    assert "desired_next_times" not in decision


def test_new_instance_activation_honors_configurable_earliest_at() -> None:
    decision = _plan(
        resource_count=0,
        activation_earliest_at="2026-08-19T00:30:00+08:00",
    )

    assert decision["trigger"] == "instance_activation"
    assert decision["next_time"] == "2026-08-19 00:30:00"
    assert "最早激活时间" in decision["reason"]


def test_new_instance_activation_can_delay_ten_minutes_from_observation() -> None:
    decision = plan_activity_job_lifecycle(
        task_id="wanbao-zhenbao",
        observation={
            **OBSERVATION,
            "observed_at": "2026-08-19T00:20:00+08:00",
        },
        end_authority=AUTHORITY,
        resource_count=0,
        activation_delay_minutes=10,
        now=NOW,
    )

    assert decision["trigger"] == "instance_activation"
    assert decision["next_time"] == "2026-08-19 00:30:00"


def test_completed_activation_schedules_authoritative_end_day_2110() -> None:
    decision = _plan(
        previous_completion={
            "instance_key": AUTHORITY["instance_key"],
            "completed_triggers": ["instance_activation"],
            "resource_count": 4,
        },
        resource_count=7,
    )

    assert decision["trigger"] == "authoritative_end_tail"
    assert decision["next_time"] == "2026-08-21 21:10:00"
    assert decision["end_tail_at"] == "2026-08-21T21:10:00+08:00"


def test_resource_crossing_ten_draw_threshold_preempts_end_tail() -> None:
    decision = _plan(
        previous_completion={
            "instance_key": AUTHORITY["instance_key"],
            "completed_triggers": ["instance_activation"],
            "resource_count": 9,
        },
        resource_count=10,
    )

    assert decision["trigger"] == "resource_ten_draw_crossing"
    assert decision["next_time"] == "2026-08-19 00:20:00"
    assert "9" in decision["reason"] and "10" in decision["reason"]


def test_remaining_above_threshold_does_not_create_repeated_resource_trigger() -> None:
    decision = _plan(
        previous_completion={
            "instance_key": AUTHORITY["instance_key"],
            "completed_triggers": [
                "instance_activation",
                "resource_ten_draw_crossing",
            ],
            "resource_count": 10,
        },
        resource_count=19,
    )

    assert decision["trigger"] == "authoritative_end_tail"
    assert decision["next_time"] == "2026-08-21 21:10:00"


def test_successful_drain_allows_same_instance_to_cross_ten_again() -> None:
    activation = _plan(resource_count=12)
    after_activation = complete_activity_job_lifecycle(
        activation,
        resource_count_after=2,
        completed_at=datetime.fromisoformat("2026-08-19T00:25:00+08:00"),
    )
    crossing = _plan(previous_completion=after_activation, resource_count=10)
    after_crossing = complete_activity_job_lifecycle(
        crossing,
        previous_completion=after_activation,
        resource_count_after=2,
        completed_at=datetime.fromisoformat("2026-08-19T01:00:00+08:00"),
    )
    crossing_again = _plan(
        previous_completion=after_crossing,
        resource_count=10,
    )

    assert after_activation["completed_triggers"] == ["instance_activation"]
    assert crossing["trigger"] == "resource_ten_draw_crossing"
    assert after_crossing["completed_triggers"] == ["instance_activation"]
    assert after_crossing["resource_count"] == 2
    assert crossing_again["trigger"] == "resource_ten_draw_crossing"


def test_success_while_resource_stays_above_ten_does_not_repeat_edge() -> None:
    previous = {
        "instance_key": AUTHORITY["instance_key"],
        "completed_triggers": ["instance_activation"],
        "resource_count": 9,
    }
    crossing = _plan(previous_completion=previous, resource_count=12)
    after_crossing = complete_activity_job_lifecycle(
        crossing,
        previous_completion=previous,
        resource_count_after=12,
        completed_at=datetime.fromisoformat("2026-08-19T01:00:00+08:00"),
    )
    next_decision = _plan(
        previous_completion=after_crossing,
        resource_count=19,
    )

    assert after_crossing["completed_triggers"] == ["instance_activation"]
    assert next_decision["trigger"] == "authoritative_end_tail"
    assert next_decision["next_time"] == "2026-08-21 21:10:00"


def test_midnight_exclusive_end_uses_previous_calendar_day() -> None:
    decision = plan_activity_job_lifecycle(
        task_id="one-activity-job",
        observation=OBSERVATION,
        end_authority={**AUTHORITY, "end_at": "2026-08-22T00:00:00+08:00"},
        previous_completion={
            "instance_key": AUTHORITY["instance_key"],
            "completed_triggers": ["instance_activation"],
            "resource_count": 0,
        },
        resource_count=0,
        now=NOW,
    )

    assert decision["next_time"] == "2026-08-21 21:10:00"


def test_end_before_2110_fails_closed() -> None:
    decision = plan_activity_job_lifecycle(
        task_id="one-activity-job",
        observation=OBSERVATION,
        end_authority={**AUTHORITY, "end_at": "2026-08-21T20:00:00+08:00"},
        now=NOW,
    )

    assert decision["status"] == "blocked"
    assert decision["next_time"] is None


def test_expired_stale_observation_never_reactivates_job() -> None:
    decision = plan_activity_job_lifecycle(
        task_id="one-activity-job",
        observation=OBSERVATION,
        end_authority=AUTHORITY,
        now=datetime.fromisoformat("2026-08-22T00:00:00+08:00"),
    )

    assert decision["status"] == "ready"
    assert decision["trigger"] == "none"
    assert decision["next_time"] is None
    assert "已经结束" in decision["reason"]


def test_negative_resource_is_invalid() -> None:
    with pytest.raises(ValueError, match="不能为负数"):
        _plan(resource_count=-1)
