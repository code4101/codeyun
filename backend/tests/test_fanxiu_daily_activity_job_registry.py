from __future__ import annotations

from backend.core.fanxiu.activity.daily_activity_job_registry import (
    AUTHORIZED_ACTIVITY_JOB_BINDINGS,
    build_authorized_daily_activity_job_schedule,
)


def _plan(*occurrences: dict) -> dict:
    return {
        "status": "ready",
        "target_date": "2026-08-16",
        "occurrences": list(occurrences),
        "operations": [],
    }


def test_daily_activity_registry_has_no_ranking_child_owner() -> None:
    assert AUTHORIZED_ACTIVITY_JOB_BINDINGS == ()
    result = build_authorized_daily_activity_job_schedule(
        _plan({
            "activity_id": 421601,
            "activity_type": 43,
            "base_id": 28100,
            "schedule_id": 6400002,
            "catalog_status": "known",
            "identity_complete": True,
            "day_relation": "starts_today",
            "start_at": "2026-08-16T10:00:00+08:00",
        })
    )
    assert result["desired_next_times"] == {}
    assert result["decisions"] == []


def test_empty_registry_is_idempotent_for_unknown_and_missing_activities() -> None:
    first = build_authorized_daily_activity_job_schedule(_plan())
    second = build_authorized_daily_activity_job_schedule(_plan({"base_id": 99999}))
    assert first["desired_next_times"] == second["desired_next_times"] == {}
    assert first["decisions"] == second["decisions"] == []
