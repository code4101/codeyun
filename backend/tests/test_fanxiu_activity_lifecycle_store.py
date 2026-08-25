from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.activity_lifecycle_store import (
    persist_activity_lifecycle_completion,
    read_activity_lifecycle_completion,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _state(**overrides) -> dict:
    return {
        "version": 1,
        "task_id": "penglai-xianzang",
        "activity_id": 701,
        "instance_key": "activity:701:2026-08-19",
        "completed_triggers": ["instance_activation"],
        "resource_count": 2,
        "completed_at": "2026-08-19T00:25:00+08:00",
        **overrides,
    }


def test_completion_round_trip_and_idempotent_upsert() -> None:
    with _session() as session:
        first = persist_activity_lifecycle_completion(session, _state())
        second = persist_activity_lifecycle_completion(session, _state())

        assert first == second
        assert read_activity_lifecycle_completion(
            session, task_id="penglai-xianzang"
        ) == first


def test_same_instance_can_add_tail_and_update_resource() -> None:
    with _session() as session:
        persist_activity_lifecycle_completion(session, _state())
        updated = persist_activity_lifecycle_completion(
            session,
            _state(
                completed_triggers=[
                    "instance_activation",
                    "authoritative_end_tail",
                ],
                resource_count=0,
                completed_at="2026-08-21T21:15:00+08:00",
            ),
        )

        assert updated["completed_triggers"] == [
            "authoritative_end_tail",
            "instance_activation",
        ]
        assert updated["resource_count"] == 0


def test_same_instance_cannot_erase_one_shot_completion() -> None:
    with _session() as session:
        persist_activity_lifecycle_completion(
            session,
            _state(
                completed_triggers=[
                    "instance_activation",
                    "authoritative_end_tail",
                ],
            ),
        )

        try:
            persist_activity_lifecycle_completion(session, _state())
        except ValueError as exc:
            assert "完成事实发生倒退" in str(exc)
        else:
            raise AssertionError("应拒绝擦除同一期 one-shot completion")


def test_newer_instance_replaces_previous_instance_without_merging_triggers() -> None:
    with _session() as session:
        persist_activity_lifecycle_completion(
            session,
            _state(
                completed_triggers=[
                    "instance_activation",
                    "authoritative_end_tail",
                ],
            ),
        )
        newer = persist_activity_lifecycle_completion(
            session,
            _state(
                activity_id=702,
                instance_key="activity:702:2026-08-26",
                completed_triggers=["instance_activation"],
                completed_at="2026-08-26T00:25:00+08:00",
            ),
        )

        assert newer["activity_id"] == 702
        assert newer["completed_triggers"] == ["instance_activation"]
