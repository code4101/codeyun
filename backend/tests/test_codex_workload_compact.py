from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.core.codex import sessions as codex_sessions
from backend.models import CodexTextCacheThread, CodexTextCacheTurn


TEST_DAY_START = 1_700_000_000.0


def _create_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_build_codex_workload_compact_summarizes_history_and_keeps_recent_turns(monkeypatch):
    engine = _create_engine()
    SQLModel.metadata.create_all(engine, tables=[CodexTextCacheThread.__table__, CodexTextCacheTurn.__table__])

    monkeypatch.setattr(
        codex_sessions,
        "_ensure_codex_text_cache",
        lambda root_dir=None, session=None: {"root_key": "root", "root_dir": "/tmp/codex"},
    )

    with Session(engine) as session:
        session.add(
            CodexTextCacheThread(
                root_key="root",
                thread_id="thread-a",
                title="A",
                rollout_path="C:/tmp/thread-a.jsonl",
                project_label="CodeYun",
            )
        )
        session.add(
            CodexTextCacheThread(
                root_key="root",
                thread_id="thread-b",
                title="B",
                rollout_path="C:/tmp/thread-b.jsonl",
                project_label="CodeYun",
            )
        )
        session.add(
            CodexTextCacheTurn(
                root_key="root",
                thread_id="thread-a",
                turn_index=1,
                start_at=TEST_DAY_START,
                end_at=TEST_DAY_START + 30.0,
                duration_seconds=30.0,
                completed=True,
            )
        )
        session.add(
            CodexTextCacheTurn(
                root_key="root",
                thread_id="thread-b",
                turn_index=2,
                start_at=TEST_DAY_START + 100.0,
                end_at=TEST_DAY_START + 160.0,
                duration_seconds=60.0,
                completed=False,
            )
        )
        session.commit()

        payload = codex_sessions.build_codex_workload(
            None,
            session=session,
            compact=True,
            include_segments=False,
            historical_day_summary_before=TEST_DAY_START + 50.0,
        )

    assert payload["root_dir"] == "/tmp/codex"
    assert payload["total_threads"] == 2
    assert payload["total_turns"] == 2
    assert payload["returned_turns"] == 1
    assert payload["summarized_turns"] == 1
    assert payload["skipped_threads"] == 0
    assert payload["segments"] == []
    assert payload["turns"] == [
        {
            "id": "thread-b:2",
            "start_at": TEST_DAY_START + 100.0,
            "end_at": TEST_DAY_START + 160.0,
            "duration_seconds": 60.0,
            "completed": False,
        }
    ]
    assert payload["day_seconds"] == {"2023-11-15": 30.0}
    assert payload["time_range_start"] == TEST_DAY_START + 100.0
    assert payload["time_range_end"] == TEST_DAY_START + 160.0
