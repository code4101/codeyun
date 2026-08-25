from __future__ import annotations

import sqlite3

from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.codex import sessions as codex_sessions
from backend.models import CodexTextCacheRoot, CodexTextCacheThread, CodexTextCacheTurn


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
        lambda root_dir=None, session=None, refresh_rollouts=True: {"root_key": "root", "root_dir": "/tmp/codex"},
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


def test_build_codex_workload_compact_uses_dirty_rollout_refresh(monkeypatch):
    captured: list[object] = []

    def fake_ensure(root_dir=None, session=None, refresh_rollouts=True):
        captured.append(refresh_rollouts)
        return {"root_key": "root", "root_dir": "/tmp/codex"}

    monkeypatch.setattr(codex_sessions, "_ensure_codex_text_cache", fake_ensure)
    monkeypatch.setattr(
        codex_sessions,
        "_build_compact_codex_workload",
        lambda context, session, **kwargs: {
            "root_dir": context["root_dir"],
            "total_threads": 0,
            "total_turns": 0,
            "returned_turns": 0,
            "summarized_turns": 0,
            "skipped_threads": 0,
            "max_concurrency": 0,
            "time_range_start": None,
            "time_range_end": None,
            "day_seconds": {},
            "turns": [],
            "segments": [],
        },
    )

    payload = codex_sessions.build_codex_workload(
        None,
        compact=True,
        include_segments=False,
    )

    assert payload["root_dir"] == "/tmp/codex"
    assert captured == ["dirty"]


def test_assign_thread_summary_can_leave_unchanged_cache_row_untouched():
    row = CodexTextCacheThread(
        root_key="root",
        thread_id="thread-a",
        title="A",
        preview="preview",
        cwd="C:/repo",
        original_cwd="C:/repo",
        rollout_path="C:/tmp/thread-a.jsonl",
        created_at_source=1.0,
        updated_at_source=2.0,
        archived=False,
        project_label="repo",
        workspace_root="C:/repo",
        refreshed_at=10.0,
        updated_at=10.0,
    )
    summary = {
        "id": "thread-a",
        "title": "A",
        "preview": "preview",
        "cwd": "C:/repo",
        "original_cwd": "C:/repo",
        "rollout_path": "C:/tmp/thread-a.jsonl",
        "created_at": 1.0,
        "updated_at": 2.0,
        "archived": False,
        "project_label": "repo",
        "project_secondary_label": None,
        "workspace_root": "C:/repo",
    }

    changed = codex_sessions._assign_thread_summary_to_cache_row(
        row,
        summary,
        now=20.0,
        touch_unchanged=False,
    )

    assert changed is False
    assert row.refreshed_at == 10.0
    assert row.updated_at == 10.0


def test_initial_codex_cache_refresh_persists_new_threads_and_commits_once(tmp_path):
    codex_root = tmp_path / "codex"
    codex_root.mkdir()
    with sqlite3.connect(codex_root / "state_5.sqlite") as connection:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT,
                created_at REAL,
                updated_at REAL,
                cwd TEXT,
                title TEXT,
                archived INTEGER,
                first_user_message TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (id, rollout_path, created_at, updated_at, cwd, title, archived, first_user_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "new-thread",
                str(codex_root / "new-thread.jsonl"),
                100.0,
                200.0,
                str(tmp_path / "repo"),
                "New thread",
                0,
                "new work",
            ),
        )

    engine = _create_engine()
    SQLModel.metadata.create_all(
        engine,
        tables=[
            CodexTextCacheRoot.__table__,
            CodexTextCacheThread.__table__,
            CodexTextCacheTurn.__table__,
        ],
    )
    commit_count = 0

    def count_commit(_session):
        nonlocal commit_count
        commit_count += 1

    event.listen(Session, "after_commit", count_commit)
    try:
        with Session(engine) as session:
            codex_sessions._ensure_codex_text_cache(
                str(codex_root),
                session=session,
                refresh_rollouts=False,
            )
            cached_thread = session.exec(
                select(CodexTextCacheThread).where(CodexTextCacheThread.thread_id == "new-thread")
            ).one()
    finally:
        event.remove(Session, "after_commit", count_commit)

    assert commit_count == 1
    assert cached_thread is not None
    assert cached_thread.title == "New thread"
