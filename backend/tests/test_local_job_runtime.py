from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from backend.core.jobs.local_runtime import (
    create_local_job_run,
    find_active_local_job_run,
    get_local_job_run,
    reconcile_local_job_run,
    list_local_job_specs,
    request_local_job_cancel,
    run_local_job,
    submit_local_job_once,
)
from backend.core.jobs.scheduler import _find_queue_task_by_id
from backend.core.jobs import scheduler as scheduler_jobs
from backend.core.ai.auto_git_commit import create_auto_git_commit_run


@pytest.fixture
def engine():
    isolated = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(isolated)
    try:
        yield isolated
    finally:
        isolated.dispose()


def test_local_job_registry_is_explicit_whitelist(engine) -> None:
    assert {item["job_type"] for item in list_local_job_specs()} >= {
        "system.health-check",
        "maintenance.auto-git-commit",
        "notes.codex-diary-import",
        "notes.codex-diary-auto-import",
        "git.reduction",
        "stock.hk-pool-one-lot-backtest",
        "media.sync",
        "attendance.course-completion",
        "maintenance.idle",
        "maintenance.storage-analysis",
        "music.process",
        "attendance.summary-templates",
        "media.scheduled-discovery",
        "stock.market-quote-refresh",
        "stock.market-intraday-persist",
        "stock.hk-connect-momentum-review",
        "notes.sheet-page-snapshot-backfill",
        "rime.context-refresh",
        "rime.context-lint",
        "browser.dp-tab-cleanup",
        "frontend.public-deploy-check",
        "notes.ruanyf-weekly-note",
        "library.ruanyf-weekly-book",
        "library.ruanyf-weekly-excerpt-book",
        "library.tibo-x-archive",
        "archive.xiaoe-incremental-update",
        "archive.wechat-sync",
        "notes.metadata-feedback-optimization",
        "library.skill-book-translation",
        "library.wechat-chat-book",
        "attendance.registration-match",
        "attendance.clockin-link-detection",
        "stock.hk-pool-strategy-search",
        "stock.hk-pool-rotation-strategy-search",
        "stock.hk-connect-momentum-review-on-demand",
        "pdf.display-title-generation",
        "filesystem.media-list",
        "device.media-list",
    }
    assert {item["job_type"] for item in list_local_job_specs(user_submittable_only=True)} == {
        "system.health-check"
    }
    with pytest.raises(ValueError, match="未注册"):
        create_local_job_run(job_type="arbitrary.shell-command", db_engine=engine)


def test_local_job_rejects_oversized_persistent_payload(engine) -> None:
    with pytest.raises(ValueError, match="JSON 超过"):
        create_local_job_run(
            job_type="system.health-check",
            payload={"value": "x" * (2 * 1024 * 1024)},
            db_engine=engine,
        )


def test_submit_local_job_once_reuses_active_run(monkeypatch, tmp_path) -> None:
    active = SimpleNamespace(id="active-1")
    monkeypatch.setattr(
        "backend.core.jobs.local_runtime._resource_lock_path",
        lambda _key: tmp_path / "submit-once.lock",
    )
    monkeypatch.setattr(
        "backend.core.jobs.local_runtime.find_active_local_job_run",
        lambda *_job_types, **_kwargs: active,
    )
    monkeypatch.setattr(
        "backend.core.jobs.local_runtime.submit_local_job",
        lambda **_kwargs: pytest.fail("active run must be reused"),
    )

    run, created = submit_local_job_once(job_type="system.health-check", payload={})

    assert run is active
    assert created is False


def test_find_active_local_job_run_reconciles_terminal_workers(engine, monkeypatch) -> None:
    older = create_local_job_run(job_type="notes.codex-diary-import", db_engine=engine)
    newer = create_local_job_run(job_type="notes.codex-diary-auto-import", db_engine=engine)
    monkeypatch.setattr(
        "backend.core.jobs.local_runtime._worker_identity_matches",
        lambda _pid, _started_at: True,
    )

    active = find_active_local_job_run(
        "notes.codex-diary-import",
        "notes.codex-diary-auto-import",
        db_engine=engine,
    )

    assert active is not None
    assert active.id == newer.id
    assert active.id != older.id


def test_local_job_run_is_persistent_and_completes(engine, tmp_path) -> None:
    run = create_local_job_run(
        job_type="system.health-check",
        payload={"echo": "independent"},
        user_id=7,
        db_engine=engine,
    )

    assert run_local_job(run.id, db_engine=engine, lock_root=tmp_path / "locks") == 0

    saved = get_local_job_run(run.id, db_engine=engine)
    assert saved is not None
    assert saved.status == "succeeded"
    assert saved.stage == "completed"
    assert saved.result_json == {
        "ok": True,
        "worker_pid": os.getpid(),
        "echo": "independent",
    }
    assert saved.started_at is not None
    assert saved.finished_at is not None
    assert saved.heartbeat_at is not None
    assert saved.attempt_count == 1


def test_local_job_context_persists_progress_without_exposing_internal_result(engine) -> None:
    from backend.core.jobs.local_runtime import LocalJobContext, serialize_local_job_run

    run = create_local_job_run(job_type="system.health-check", db_engine=engine)
    context = LocalJobContext(run.id, engine)
    context.heartbeat(
        stage="processing",
        message="2/5",
        progress_current=2,
        progress_total=5,
        metadata={"part": "vocals"},
    )

    saved = get_local_job_run(run.id, db_engine=engine)
    payload = serialize_local_job_run(saved)
    assert context.task_id == run.id
    assert payload["progress_current"] == 2
    assert payload["progress_total"] == 5
    assert payload["result"] == {}


def test_local_job_cancel_command_is_separate_from_terminal_state(engine, tmp_path) -> None:
    run = create_local_job_run(
        job_type="system.health-check",
        db_engine=engine,
    )
    requested = request_local_job_cancel(run.id, db_engine=engine)

    assert requested.status == "queued"
    assert requested.cancel_requested_at is not None
    assert run_local_job(run.id, db_engine=engine, lock_root=tmp_path / "locks") == 0

    saved = get_local_job_run(run.id, db_engine=engine)
    assert saved is not None
    assert saved.status == "cancelled"
    assert saved.stage == "cancelled"


def test_missing_worker_is_interrupted_not_business_failed(engine, monkeypatch) -> None:
    run = create_local_job_run(
        job_type="system.health-check",
        db_engine=engine,
    )
    monkeypatch.setattr(
        "backend.core.jobs.local_runtime._worker_identity_matches",
        lambda _pid, _started_at: False,
    )

    assert reconcile_local_job_run(
        run.id,
        db_engine=engine,
        launch_grace_seconds=0,
    ) is True
    saved = get_local_job_run(run.id, db_engine=engine)
    assert saved is not None
    assert saved.status == "interrupted"
    assert saved.stage == "worker-interrupted"
    assert "业务失败" in str(saved.error_message)


def test_launcher_pid_handoff_has_grace_before_worker_claim(engine, monkeypatch) -> None:
    run = create_local_job_run(job_type="system.health-check", db_engine=engine)
    with Session(engine) as session:
        stored = session.get(type(run), run.id)
        stored.worker_pid = 999_999
        session.add(stored)
        session.commit()
    monkeypatch.setattr(
        "backend.core.jobs.local_runtime._worker_identity_matches",
        lambda _pid, _started_at: False,
    )

    assert reconcile_local_job_run(run.id, db_engine=engine) is False
    saved = get_local_job_run(run.id, db_engine=engine)
    assert saved is not None
    assert saved.status == "queued"


def test_scheduler_wait_projection_accepts_local_job(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.jobs.local_runtime.get_local_job_run",
        lambda _run_id: SimpleNamespace(
            id="local-1",
            job_type="maintenance.auto-git-commit",
            status="succeeded",
            queued_at=1.0,
            started_at=2.0,
            finished_at=3.0,
            error_message=None,
            resource_key="resource:repo",
            result_json={"run_id": "business-1"},
        ),
    )

    projected = _find_queue_task_by_id(
        {"running": None, "pending": [], "recent": []},
        "local-1",
    )
    assert projected is not None
    assert projected["status"] == "completed"
    assert projected["result"] == {"run_id": "business-1"}


def test_auto_git_commit_submits_whitelisted_local_job(engine, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "backend.core.jobs.local_runtime.submit_local_job",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(id="local-auto-git-1"),
    )
    with Session(engine) as session:
        run = create_auto_git_commit_run(session, trigger_reason="test", enqueue=True)

    assert run.queue_task_id == "local-auto-git-1"
    assert calls == [
        {
            "job_type": "maintenance.auto-git-commit",
            "payload": {"run_id": run.id, "trigger_reason": "test"},
        }
    ]


@pytest.mark.parametrize(
    ("enqueue_name", "job_type"),
    [
        ("_enqueue_attendance_summary", "attendance.summary-templates"),
        ("_enqueue_media_sync_home_discovery", "media.scheduled-discovery"),
        ("_enqueue_market_quote_refresh", "stock.market-quote-refresh"),
        ("_enqueue_market_intraday_persist", "stock.market-intraday-persist"),
        ("_enqueue_hk_connect_momentum_review", "stock.hk-connect-momentum-review"),
        ("_enqueue_note_sheet_page_snapshot_backfill", "notes.sheet-page-snapshot-backfill"),
        ("_enqueue_rime_context_refresh", "rime.context-refresh"),
        ("_enqueue_rime_context_lint", "rime.context-lint"),
        ("_enqueue_dp_browser_tab_cleanup", "browser.dp-tab-cleanup"),
        ("_enqueue_public_frontend_deploy", "frontend.public-deploy-check"),
    ],
)
def test_scheduler_parameterless_jobs_submit_local_job(monkeypatch, enqueue_name, job_type) -> None:
    submitted = []
    monkeypatch.setattr(scheduler_jobs, "find_active_local_job_run", lambda *_args: None)
    monkeypatch.setattr(
        scheduler_jobs,
        "submit_local_job",
        lambda **kwargs: submitted.append(kwargs) or SimpleNamespace(id=f"local-{job_type}"),
    )

    task_id = getattr(scheduler_jobs, enqueue_name)()

    assert task_id == f"local-{job_type}"
    assert submitted == [{"job_type": job_type, "payload": {}}]
