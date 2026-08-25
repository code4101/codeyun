from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.activity_lifecycle_daily_sync_adapter import (
    persist_successful_activity_lifecycle_completion,
    project_daily_sync_activity_lifecycles,
    session_activity_lifecycle_completion_reader,
)
from backend.core.fanxiu.activity.activity_lifecycle_store import (
    read_activity_lifecycle_completion,
)


NOW = datetime.fromisoformat("2026-08-19T00:20:00+08:00")


def _observation(name: str, activity_id: int) -> dict:
    return {
        "observation_id": f"revenue:{activity_id}",
        "activity_id": activity_id,
        "name": name,
        "is_schedule_occurrence": False,
        "observed_at": NOW.isoformat(),
    }


def _occurrence(activity_id: int) -> dict:
    return {
        "activity_id": activity_id,
        "schedule_id": 6400000 + activity_id,
        "identity_complete": True,
        "catalog_status": "known",
        "start_at": "2026-08-19T00:00:00+08:00",
        "end_at": "2026-08-21T23:59:59+08:00",
    }


def _plan(*, observations: list[dict], occurrences: list[dict]) -> dict:
    return {
        "status": "ready",
        "source_kind": "worldline_activity_runtime_memory",
        "activity_observations": observations,
        "occurrences": occurrences,
        "source_evidence": {
            "supplemental_activity_observation": {
                "complete": True,
                "source_kind": "revenue_activity_observation_runtime_memory",
            }
        },
    }


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_exact_activity_id_pair_projects_only_ready_canonical_executor() -> None:
    plan = _plan(
        observations=[
            _observation("蓬莱仙藏", 701),
            _observation("万宝臻宝", 704),
        ],
        occurrences=[_occurrence(701), _occurrence(704)],
    )
    result = project_daily_sync_activity_lifecycles(
        plan,
        canonical_executor_task_ids=("wanbao-zhenbao",),
        completion_reader=lambda _task_id: None,
        completion_store_ready=True,
        now=NOW,
    )

    assert result["status"] == "partially_blocked"
    assert result["scheduler_updates"] == {
        "wanbao-zhenbao": "2026-08-19 00:30:00"
    }
    by_task = {item["task_id"]: item for item in result["decisions"]}
    assert by_task["penglai-xianzang"]["status"] == "blocked"
    assert "执行器尚未就绪" in by_task["penglai-xianzang"]["reason"]
    assert result["migration"] == {"ready": False, "removed_task_ids": []}


def test_different_occurrence_activity_id_cannot_lend_period() -> None:
    result = project_daily_sync_activity_lifecycles(
        _plan(
            observations=[_observation("万宝臻宝", 704)],
            occurrences=[_occurrence(705)],
        ),
        canonical_executor_task_ids=("wanbao-zhenbao",),
        completion_reader=lambda _task_id: None,
        completion_store_ready=True,
        now=NOW,
    )

    assert result["scheduler_updates"] == {}
    assert result["decisions"][0]["status"] == "blocked"
    assert "权威活动结束时间" in result["decisions"][0]["reason"]


def test_untrusted_revenue_or_occurrence_source_blocks_all_updates() -> None:
    plan = _plan(
        observations=[_observation("万宝臻宝", 704)],
        occurrences=[_occurrence(704)],
    )
    plan["source_evidence"]["supplemental_activity_observation"][
        "source_kind"
    ] = "static_guess"
    bad_revenue = project_daily_sync_activity_lifecycles(
        plan,
        canonical_executor_task_ids=("wanbao-zhenbao",),
        completion_reader=lambda _task_id: None,
        completion_store_ready=True,
        now=NOW,
    )
    plan["source_evidence"]["supplemental_activity_observation"][
        "source_kind"
    ] = "revenue_activity_observation_runtime_memory"
    plan["source_kind"] = "static_biweekly_guess"
    bad_occurrence = project_daily_sync_activity_lifecycles(
        plan,
        canonical_executor_task_ids=("wanbao-zhenbao",),
        completion_reader=lambda _task_id: None,
        completion_store_ready=True,
        now=NOW,
    )

    assert bad_revenue["status"] == "blocked"
    assert bad_revenue["scheduler_updates"] == {}
    assert bad_occurrence["status"] == "blocked"
    assert bad_occurrence["scheduler_updates"] == {}


def test_completion_store_unavailable_or_read_failure_fails_closed() -> None:
    plan = _plan(
        observations=[_observation("万宝臻宝", 704)],
        occurrences=[_occurrence(704)],
    )
    unavailable = project_daily_sync_activity_lifecycles(
        plan,
        canonical_executor_task_ids=("wanbao-zhenbao",),
        completion_reader=None,
        completion_store_ready=False,
        now=NOW,
    )
    failed = project_daily_sync_activity_lifecycles(
        plan,
        canonical_executor_task_ids=("wanbao-zhenbao",),
        completion_reader=lambda _task_id: (_ for _ in ()).throw(
            RuntimeError("db unavailable")
        ),
        completion_store_ready=True,
        now=NOW,
    )

    assert unavailable["scheduler_updates"] == {}
    assert "store 尚未就绪" in unavailable["reason"]
    assert failed["scheduler_updates"] == {}
    assert "读取失败" in failed["reason"]


def test_completion_reader_suppresses_already_completed_activation() -> None:
    with _session() as session:
        decision = project_daily_sync_activity_lifecycles(
            _plan(
                observations=[_observation("万宝臻宝", 704)],
                occurrences=[_occurrence(704)],
            ),
            canonical_executor_task_ids=("wanbao-zhenbao",),
            completion_reader=lambda _task_id: None,
            completion_store_ready=True,
            now=NOW,
        )["decisions"][0]
        persisted = persist_successful_activity_lifecycle_completion(
            session,
            decision=decision,
            resource_count_after=0,
            completed_at=datetime.fromisoformat("2026-08-19T00:31:00+08:00"),
            job_succeeded=True,
        )
        result = project_daily_sync_activity_lifecycles(
            _plan(
                observations=[_observation("万宝臻宝", 704)],
                occurrences=[_occurrence(704)],
            ),
            canonical_executor_task_ids=("wanbao-zhenbao",),
            completion_reader=session_activity_lifecycle_completion_reader(session),
            completion_store_ready=True,
            resource_counts={"wanbao-zhenbao": 0},
            now=datetime.fromisoformat("2026-08-19T00:32:00+08:00"),
        )

        assert persisted["completed_triggers"] == ["instance_activation"]
        assert result["scheduler_updates"] == {
            "wanbao-zhenbao": "2026-08-21 21:10:00"
        }


def test_unsuccessful_job_cannot_write_completion_store() -> None:
    with _session() as session:
        decision = project_daily_sync_activity_lifecycles(
            _plan(
                observations=[_observation("万宝臻宝", 704)],
                occurrences=[_occurrence(704)],
            ),
            canonical_executor_task_ids=("wanbao-zhenbao",),
            completion_reader=lambda _task_id: None,
            completion_store_ready=True,
            now=NOW,
        )["decisions"][0]
        try:
            persist_successful_activity_lifecycle_completion(
                session,
                decision=decision,
                resource_count_after=0,
                completed_at=NOW,
                job_succeeded=False,
            )
        except ValueError as exc:
            assert "未成功" in str(exc)
        else:
            raise AssertionError("未成功 Job 不得写 completion store")

        assert read_activity_lifecycle_completion(
            session, task_id="wanbao-zhenbao"
        ) is None
