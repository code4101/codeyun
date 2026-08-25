from __future__ import annotations

import pytest
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

from backend.core.fanxiu.instrumentation.spirit_artifact_store import (
    load_spirit_artifact_runtime_snapshot,
    upsert_spirit_artifact_runtime_snapshot,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _snapshot(updated_at: float, rank: int) -> dict:
    return {
        "artifacts": [{"order": 1, "name": "测试灵器", "rows": [{"rank": rank}]}],
        "runtime_source": "runtime_memory_current_parts",
        "runtime_complete": True,
        "runtime_error": "",
        "runtime_updated_at": updated_at,
        "runtime_item_count": 6,
        "runtime_equipped_count": 6,
        "runtime_debug": {"pid": 123},
    }


def test_runtime_snapshot_replaces_current_database_fact():
    with _session() as session:
        upsert_spirit_artifact_runtime_snapshot(session, _snapshot(100.0, 7))
        upsert_spirit_artifact_runtime_snapshot(session, _snapshot(200.0, 8))

        stored = load_spirit_artifact_runtime_snapshot(session)

    assert stored is not None
    assert stored["runtime_updated_at"] == 200.0
    assert stored["artifacts"][0]["rows"][0]["rank"] == 8


def test_incomplete_runtime_snapshot_cannot_overwrite_database_fact():
    with _session() as session:
        upsert_spirit_artifact_runtime_snapshot(session, _snapshot(100.0, 7))
        invalid = _snapshot(200.0, 8)
        invalid["runtime_complete"] = False

        with pytest.raises(ValueError, match="不完整"):
            upsert_spirit_artifact_runtime_snapshot(session, invalid)

        stored = load_spirit_artifact_runtime_snapshot(session)

    assert stored is not None
    assert stored["runtime_updated_at"] == 100.0
