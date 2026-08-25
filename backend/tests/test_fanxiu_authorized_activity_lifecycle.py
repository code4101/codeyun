from __future__ import annotations

from datetime import datetime

import pytest

from backend.core.fanxiu.activity.authorized_activity_lifecycle import (
    build_activity_lifecycle_scheduler_migration,
    daily_plan_activity_end_authorities,
    plan_authorized_activity_lifecycles,
    trusted_activity_lifecycle_authority,
)


NOW = datetime.fromisoformat("2026-08-19T00:20:00+08:00")


def _plan(*observations: dict) -> dict:
    return {
        "status": "ready",
        "activity_observations": list(observations),
        "source_evidence": {
            "supplemental_activity_observation": {
                "complete": True,
                "source_kind": "revenue_activity_observation_runtime_memory",
            }
        },
    }


def _observation(name: str, activity_id: int) -> dict:
    return {
        "observation_id": f"revenue:{activity_id}",
        "activity_id": activity_id,
        "template_id": activity_id + 1000,
        "name": name,
        "is_schedule_occurrence": False,
        "observed_at": "2026-08-19T00:20:00+08:00",
    }


def _authority(activity_id: int) -> dict:
    return trusted_activity_lifecycle_authority(
        activity_id=activity_id,
        instance_key=f"activity:{activity_id}:2026-08-19",
        end_at="2026-08-21T23:59:59+08:00",
        source_kind="revenue_activity_period_runtime_memory",
    )


def test_explicit_activities_project_to_one_canonical_job_each() -> None:
    result = plan_authorized_activity_lifecycles(
        _plan(
            _observation("蓬莱仙藏", 701),
            _observation("昆仑秘藏", 702),
            _observation("灵霄仙会", 703),
            _observation("万宝臻宝", 704),
        ),
        end_authorities=[_authority(value) for value in (701, 702, 703, 704)],
        now=NOW,
    )

    assert set(result["desired_next_times"]) == {
        "penglai-xianzang",
        "kunlun-secret",
        "lingxiao-xianhui",
        "wanbao-zhenbao",
    }
    assert "penglai-xianzang-config" not in result["desired_next_times"]
    assert "penglai-xianzang-lottery" not in result["desired_next_times"]
    assert result["desired_next_times"]["penglai-xianzang"] == (
        "2026-08-19 00:20:00"
    )
    assert result["desired_next_times"]["wanbao-zhenbao"] == (
        "2026-08-19 00:30:00"
    )


def test_missing_period_authority_blocks_without_guessing_biweekly_time() -> None:
    result = plan_authorized_activity_lifecycles(
        _plan(_observation("蓬莱仙藏", 701)),
        end_authorities=[],
        now=NOW,
    )

    assert result["desired_next_times"]["penglai-xianzang"] is None
    assert result["decisions"][0]["status"] == "blocked"
    assert "权威活动结束时间" in result["decisions"][0]["reason"]
    assert result["migration"]["ready"] is False


def test_completed_activation_uses_same_job_for_ten_crossing_then_end_tail() -> None:
    plan = _plan(_observation("昆仑秘藏", 702))
    previous = {
        "kunlun-secret": {
            "instance_key": "activity:702:2026-08-19",
            "completed_triggers": ["instance_activation"],
            "resource_count": 9,
        }
    }
    crossing = plan_authorized_activity_lifecycles(
        plan,
        end_authorities=[_authority(702)],
        previous_completions=previous,
        resource_counts={"kunlun-secret": 10},
        now=NOW,
    )
    tail = plan_authorized_activity_lifecycles(
        plan,
        end_authorities=[_authority(702)],
        previous_completions={
            "kunlun-secret": {
                **previous["kunlun-secret"],
                "resource_count": 10,
            }
        },
        resource_counts={"kunlun-secret": 19},
        now=NOW,
    )

    assert crossing["decisions"][0]["trigger"] == "resource_ten_draw_crossing"
    assert crossing["desired_next_times"]["kunlun-secret"] == (
        "2026-08-19 00:20:00"
    )
    assert tail["decisions"][0]["trigger"] == "authoritative_end_tail"
    assert tail["desired_next_times"]["kunlun-secret"] == (
        "2026-08-21 21:10:00"
    )


def test_duplicate_observations_for_one_job_fail_closed() -> None:
    result = plan_authorized_activity_lifecycles(
        _plan(
            _observation("凌霄仙会", 703),
            _observation("灵霄仙会", 704),
        ),
        end_authorities=[_authority(703), _authority(704)],
        now=NOW,
    )

    decision = result["decisions"][0]
    assert decision["status"] == "blocked"
    assert result["desired_next_times"]["lingxiao-xianhui"] is None


def test_unapproved_similar_activity_is_only_observed() -> None:
    result = plan_authorized_activity_lifecycles(
        _plan(_observation("蓬莱秘藏", 999)),
        end_authorities=[_authority(999)],
        now=NOW,
    )

    assert result["decisions"] == []
    assert all(value is None for value in result["desired_next_times"].values())
    assert result["migration"]["ready"] is False


def test_incomplete_supplemental_source_cannot_authorize_jobs() -> None:
    plan = _plan(_observation("万宝臻宝", 704))
    plan["source_evidence"]["supplemental_activity_observation"]["complete"] = False

    with pytest.raises(ValueError, match="supplemental activity observation"):
        plan_authorized_activity_lifecycles(
            plan,
            end_authorities=[_authority(704)],
            now=NOW,
        )


def test_migration_requires_explicit_old_pair_ids_and_all_runtime_gates() -> None:
    projection = plan_authorized_activity_lifecycles(
        _plan(_observation("蓬莱仙藏", 701), _observation("昆仑秘藏", 702)),
        end_authorities=[_authority(701), _authority(702)],
        now=NOW,
    )
    blocked = build_activity_lifecycle_scheduler_migration(
        projection,
        registered_standard_task_ids=("penglai-xianzang", "kunlun-secret"),
        completion_store_ready=False,
        daily_sync_adapter_ready=True,
    )

    assert projection["migration"]["fact_ready"] is True
    assert projection["migration"]["ready"] is False
    assert projection["migration"]["removed_task_ids"] == []
    assert blocked["status"] == "blocked"
    assert blocked["removed_task_ids"] == []

    ready = build_activity_lifecycle_scheduler_migration(
        projection,
        registered_standard_task_ids=(
            "penglai-xianzang",
            "kunlun-secret",
            "lingxiao-xianhui",
            "wanbao-zhenbao",
        ),
        completion_store_ready=True,
        daily_sync_adapter_ready=True,
    )

    assert ready["status"] == "ready"
    assert ready["removed_task_ids"] == [
        "kunlun-secret-config",
        "kunlun-secret-lottery",
        "penglai-xianzang-config",
        "penglai-xianzang-lottery",
    ]


def test_authority_constructor_rejects_static_recurrence() -> None:
    with pytest.raises(ValueError, match="不受信任"):
        trusted_activity_lifecycle_authority(
            activity_id=701,
            instance_key="guess",
            end_at="2026-08-21T23:59:59+08:00",
            source_kind="static_biweekly_guess",
        )


def test_complete_worldline_occurrence_can_supply_exact_period_authority() -> None:
    plan = _plan(_observation("万宝臻宝", 704))
    plan["source_kind"] = "worldline_activity_runtime_memory"
    plan["occurrences"] = [
        {
            "activity_id": 704,
            "schedule_id": 6400704,
            "identity_complete": True,
            "catalog_status": "known",
            "start_at": "2026-08-19T00:00:00+08:00",
            "end_at": "2026-08-21T23:59:59+08:00",
        }
    ]

    authorities = daily_plan_activity_end_authorities(plan)
    projection = plan_authorized_activity_lifecycles(
        plan,
        end_authorities=authorities,
        now=NOW,
    )

    assert len(authorities) == 1
    assert authorities[0]["activity_id"] == 704
    assert authorities[0]["source_kind"] == "worldline_activity_runtime_memory"
    assert projection["desired_next_times"]["wanbao-zhenbao"] == (
        "2026-08-19 00:30:00"
    )


def test_occurrence_with_different_activity_id_cannot_lend_its_end_time() -> None:
    plan = _plan(_observation("万宝臻宝", 704))
    plan["source_kind"] = "worldline_activity_runtime_memory"
    plan["occurrences"] = [
        {
            "activity_id": 705,
            "schedule_id": 6400705,
            "identity_complete": True,
            "catalog_status": "known",
            "start_at": "2026-08-19T00:00:00+08:00",
            "end_at": "2026-08-21T23:59:59+08:00",
        }
    ]

    projection = plan_authorized_activity_lifecycles(
        plan,
        end_authorities=daily_plan_activity_end_authorities(plan),
        now=NOW,
    )

    assert projection["desired_next_times"]["wanbao-zhenbao"] is None
    assert projection["decisions"][0]["status"] == "blocked"
