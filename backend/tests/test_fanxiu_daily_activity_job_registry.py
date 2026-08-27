from __future__ import annotations

from backend.core.fanxiu.activity.daily_activity_job_registry import (
    AUTHORIZED_ACTIVITY_JOB_BINDINGS,
    build_authorized_daily_activity_job_schedule,
)


MANAGED_IDS = {
    "penglai-xianzang-config",
    "penglai-xianzang-lottery",
    "kunlun-secret-config",
    "kunlun-secret-lottery",
}


def _observation(name: str, activity_id: int) -> dict:
    return {
        "activity_id": activity_id,
        "name": name,
        "is_schedule_occurrence": False,
    }


def _plan(*observations: dict, complete: bool = True) -> dict:
    return {
        "status": "ready",
        "target_date": "2026-08-16",
        "activity_observations": list(observations),
        "source_evidence": {
            "supplemental_activity_observation": {"complete": complete}
        },
    }


def test_daily_activity_registry_has_no_ranking_child_owner() -> None:
    assert {
        binding.task_id for binding in AUTHORIZED_ACTIVITY_JOB_BINDINGS
    } == MANAGED_IDS
    assert all(
        "xianmeng" not in binding.task_id
        for binding in AUTHORIZED_ACTIVITY_JOB_BINDINGS
    )


def test_missing_activities_clear_all_managed_next_times() -> None:
    first = build_authorized_daily_activity_job_schedule(_plan())
    second = build_authorized_daily_activity_job_schedule(
        _plan(_observation("昆仑仙藏", 99999))
    )
    expected = {task_id: None for task_id in MANAGED_IDS}
    assert first["desired_next_times"] == second["desired_next_times"] == expected
    assert first["decisions"] == second["decisions"] == []


def test_observed_activity_schedules_only_its_two_jobs() -> None:
    result = build_authorized_daily_activity_job_schedule(
        _plan(_observation("昆仑秘藏", 702))
    )

    assert result["desired_next_times"] == {
        "penglai-xianzang-config": None,
        "penglai-xianzang-lottery": None,
        "kunlun-secret-config": "2026-08-16 00:05:00",
        "kunlun-secret-lottery": "2026-08-16 21:10:00",
    }
    assert {item["activity_name"] for item in result["decisions"]} == {"昆仑秘藏"}


def test_incomplete_activity_observation_preserves_existing_job_times() -> None:
    result = build_authorized_daily_activity_job_schedule(_plan(complete=False))

    assert result["status"] == "observation_unavailable"
    assert result["desired_next_times"] == {}
