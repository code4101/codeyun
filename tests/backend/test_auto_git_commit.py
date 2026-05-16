import subprocess
from datetime import datetime

from sqlmodel import select

from backend.core.ai_git_commit import AiGitCommitError
from backend.core.ai_git_repos import save_user_ai_git_repos
from backend.core.auto_git_commit import (
    AUTO_GIT_COMMIT_CRON,
    AUTO_GIT_COMMIT_SCHEDULE_SETTING_KEY,
    AUTO_GIT_COMMIT_STALE_HEARTBEAT_SECONDS,
    create_auto_git_commit_run,
    mark_stale_auto_git_commit_runs,
    maybe_create_due_auto_git_commit_run,
    run_auto_git_commit_worker,
    select_auto_git_commit_candidates,
)
from backend.models import AppSetting, AutoGitCommitRun


def _run_git(repo_path, *args):
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _init_git_repo(repo_path):
    repo_path.mkdir(parents=True, exist_ok=True)
    _run_git(repo_path, "init")
    _run_git(repo_path, "config", "user.name", "CodeYun Test")
    _run_git(repo_path, "config", "user.email", "codeyun-test@example.com")
    (repo_path / "README.md").write_text("# demo\n", encoding="utf-8")
    _run_git(repo_path, "add", "README.md")
    _run_git(repo_path, "commit", "-m", "init")


def _save_auto_commit_repos(session, user_id, items):
    save_user_ai_git_repos(
        session,
        user_id,
        items=[
            {
                "id": item["id"],
                "name": item["name"],
                "entry_id": "local-entry",
                "cwd": str(item["cwd"]),
                "pinned": False,
                "order_index": index,
            }
            for index, item in enumerate(items)
        ],
    )


def _dt(text):
    return datetime.fromisoformat(text)


def _save_auto_git_schedule(session, next_run_at: str):
    row = AppSetting(
        key=AUTO_GIT_COMMIT_SCHEDULE_SETTING_KEY,
        value={
            "cron": AUTO_GIT_COMMIT_CRON,
            "next_run_at": next_run_at,
        },
    )
    session.add(row)
    session.commit()


def _load_auto_git_schedule(session):
    row = session.get(AppSetting, AUTO_GIT_COMMIT_SCHEDULE_SETTING_KEY)
    assert row is not None
    return row.value


def _noop_pre_commit_optimizer(candidate, inspect_payload):
    return {"status": "completed", "summary": f"已检查 {candidate.name}"}


def test_auto_git_commit_candidates_use_first_three_allowed_saved_repos(session, auth_user, tmp_path):
    _save_auto_commit_repos(
        session,
        auth_user.id,
        [
            {"id": "py", "name": "pyxllib", "cwd": tmp_path / "pyxllib"},
            {"id": "xl", "name": "xlproject", "cwd": tmp_path / "xlproject"},
            {"id": "cy", "name": "codeyun", "cwd": tmp_path / "codeyun"},
            {"id": "dsp", "name": "dsp-calc", "cwd": tmp_path / "dsp-calc"},
        ],
    )

    candidates = select_auto_git_commit_candidates(session)

    assert [item.name for item in candidates] == ["pyxllib", "xlproject", "codeyun"]
    assert all("dsp-calc" not in item.cwd for item in candidates)


def test_auto_git_commit_due_scheduler_waits_until_persisted_next_run(session):
    _save_auto_git_schedule(session, "2026-05-06T00:15:00+08:00")

    run = maybe_create_due_auto_git_commit_run(
        session,
        trigger_reason="scheduled",
        now=_dt("2026-05-06T00:14:00+08:00"),
        enqueue=False,
    )

    assert run is None
    assert _load_auto_git_schedule(session)["next_run_at"] == "2026-05-06T00:15:00+08:00"


def test_auto_git_commit_due_scheduler_backfills_expired_persisted_next_run(session):
    _save_auto_git_schedule(session, "2026-05-06T00:15:00+08:00")

    run = maybe_create_due_auto_git_commit_run(
        session,
        trigger_reason="scheduled_catchup",
        now=_dt("2026-05-06T10:00:00+08:00"),
        enqueue=False,
    )

    assert run is not None
    assert run.trigger_reason == "scheduled_catchup"
    assert _load_auto_git_schedule(session)["next_run_at"] == "2026-05-07T00:15:00+08:00"


def test_auto_git_commit_due_scheduler_initializes_from_existing_run_history(session):
    session.add(
        AutoGitCommitRun(
            status="completed",
            trigger_reason="scheduled",
            run_date="2026-05-04",
            created_at=_dt("2026-05-04T00:16:00+08:00").timestamp(),
            updated_at=_dt("2026-05-04T00:17:00+08:00").timestamp(),
            finished_at=_dt("2026-05-04T00:17:00+08:00").timestamp(),
        )
    )
    session.commit()

    run = maybe_create_due_auto_git_commit_run(
        session,
        trigger_reason="scheduled_catchup",
        now=_dt("2026-05-06T10:00:00+08:00"),
        enqueue=False,
    )

    assert run is not None
    assert run.trigger_reason == "scheduled_catchup"
    assert _load_auto_git_schedule(session)["next_run_at"] == "2026-05-07T00:15:00+08:00"


def test_auto_git_commit_due_scheduler_does_not_duplicate_existing_due_day_run(session):
    session.add(
        AutoGitCommitRun(
            status="completed",
            trigger_reason="manual_backfill",
            run_date="2026-05-06",
            created_at=_dt("2026-05-06T10:00:00+08:00").timestamp(),
            updated_at=_dt("2026-05-06T10:01:00+08:00").timestamp(),
            finished_at=_dt("2026-05-06T10:01:00+08:00").timestamp(),
        )
    )
    session.commit()

    run = maybe_create_due_auto_git_commit_run(
        session,
        trigger_reason="scheduled_catchup",
        now=_dt("2026-05-06T10:05:00+08:00"),
        enqueue=False,
    )

    assert run is None
    assert _load_auto_git_schedule(session)["next_run_at"] == "2026-05-07T00:15:00+08:00"


def test_auto_git_commit_marks_stale_running_run_failed(session):
    now_ts = _dt("2026-05-09T10:56:57+08:00").timestamp()
    stale_ts = now_ts - AUTO_GIT_COMMIT_STALE_HEARTBEAT_SECONDS - 1
    stale_run = AutoGitCommitRun(
        status="running",
        trigger_reason="scheduled",
        run_date="2026-05-09",
        stage="processing_repo",
        stage_label="检查/优化 codeyun",
        heartbeat_at=stale_ts,
        started_at=stale_ts,
        created_at=stale_ts,
        updated_at=stale_ts,
    )
    session.add(stale_run)
    session.commit()

    changed_count = mark_stale_auto_git_commit_runs(
        session,
        now_ts=now_ts,
        queue_snapshot={"running": None, "pending": []},
    )

    assert changed_count == 1
    session.expire_all()
    updated = session.get(AutoGitCommitRun, stale_run.id)
    assert updated.status == "failed"
    assert updated.stage == "stale"
    assert updated.stage_label == "任务心跳超时"
    assert "当前执行队列中没有对应任务" in updated.error_message
    assert updated.finished_at == now_ts


def test_auto_git_commit_due_scheduler_ignores_stale_active_run(session):
    now = _dt("2026-05-06T10:00:00+08:00")
    stale_ts = now.timestamp() - AUTO_GIT_COMMIT_STALE_HEARTBEAT_SECONDS - 1
    _save_auto_git_schedule(session, "2026-05-06T00:15:00+08:00")
    session.add(
        AutoGitCommitRun(
            status="running",
            trigger_reason="scheduled",
            run_date="2026-05-06",
            stage="processing_repo",
            stage_label="检查/优化 codeyun",
            heartbeat_at=stale_ts,
            started_at=stale_ts,
            created_at=stale_ts,
            updated_at=stale_ts,
        )
    )
    session.commit()

    run = maybe_create_due_auto_git_commit_run(
        session,
        trigger_reason="scheduled_catchup",
        now=now,
        enqueue=False,
    )

    assert run is not None
    assert run.trigger_reason == "scheduled_catchup"
    runs = session.exec(select(AutoGitCommitRun).order_by(AutoGitCommitRun.created_at.asc())).all()
    assert [item.status for item in runs] == ["failed", "pending"]
    assert _load_auto_git_schedule(session)["next_run_at"] == "2026-05-07T00:15:00+08:00"


def test_auto_git_commit_worker_commits_dirty_repo_and_skips_clean_repo(session, auth_user, tmp_path, monkeypatch):
    dirty_repo = tmp_path / "dirty-repo"
    clean_repo = tmp_path / "clean-repo"
    _init_git_repo(dirty_repo)
    _init_git_repo(clean_repo)
    (dirty_repo / "feature.txt").write_text("new feature\n", encoding="utf-8")
    _save_auto_commit_repos(
        session,
        auth_user.id,
        [
            {"id": "cy", "name": "codeyun", "cwd": dirty_repo},
            {"id": "py", "name": "pyxllib", "cwd": clean_repo},
        ],
    )
    run = create_auto_git_commit_run(session, trigger_reason="test", enqueue=False)

    monkeypatch.setattr(
        "backend.core.auto_git_commit.resolve_ai_runtime_config",
        lambda **_: ("ollama", None, None, ()),
    )

    def fake_draft_generator(**kwargs):
        return {
            "subject": "自动提交凌晨检查改动",
            "body": ["提交自动检查发现的仓库变更"],
            "model": "fake-model",
            "needs_split": False,
            "reason": "",
        }

    def unexpected_pre_commit_optimizer(candidate, inspect_payload):
        raise AssertionError("codeyun should skip pre-commit optimization")

    run_auto_git_commit_worker(
        session.get_bind(),
        run.id,
        pre_commit_optimizer=unexpected_pre_commit_optimizer,
        draft_generator=fake_draft_generator,
    )

    session.expire_all()
    updated = session.get(AutoGitCommitRun, run.id)
    assert updated.status == "completed"
    assert updated.changed_repo_count == 1
    assert updated.committed_repo_count == 1
    assert updated.skipped_repo_count == 1
    assert updated.failed_repo_count == 0
    result_statuses = {item["name"]: item["status"] for item in updated.result_json["repos"]}
    assert result_statuses == {"codeyun": "committed", "pyxllib": "clean"}
    repo_result = next(item for item in updated.result_json["repos"] if item["name"] == "codeyun")
    assert repo_result["pre_commit_review"]["status"] == "skipped"
    assert "codeyun 自动提交只生成提交信息" in repo_result["pre_commit_review"]["summary"]
    assert _run_git(dirty_repo, "log", "-1", "--pretty=%s") == "自动提交凌晨检查改动"
    assert _run_git(dirty_repo, "status", "--short") == ""


def test_auto_git_commit_worker_runs_pre_commit_optimizer_before_draft(session, auth_user, tmp_path, monkeypatch):
    dirty_repo = tmp_path / "dirty-review-repo"
    _init_git_repo(dirty_repo)
    (dirty_repo / "feature.txt").write_text("new feature\n", encoding="utf-8")
    _save_auto_commit_repos(
        session,
        auth_user.id,
        [
            {"id": "py", "name": "pyxllib", "cwd": dirty_repo},
        ],
    )
    run = create_auto_git_commit_run(session, trigger_reason="test", enqueue=False)

    monkeypatch.setattr(
        "backend.core.auto_git_commit.resolve_ai_runtime_config",
        lambda **_: ("ollama", None, None, ()),
    )

    events = []

    def fake_pre_commit_optimizer(candidate, inspect_payload):
        events.append(("optimizer", [item["path"] for item in inspect_payload["changed_files"]]))
        (dirty_repo / "review_fix.txt").write_text("codex review fix\n", encoding="utf-8")
        return {"status": "completed", "summary": "补充 review 优化"}

    def fake_draft_generator(**kwargs):
        events.append(("draft", _run_git(dirty_repo, "status", "--short")))
        return {
            "subject": "自动提交前先执行工程优化",
            "body": ["提交原始变更和 Codex review 优化"],
            "model": "fake-model",
            "needs_split": False,
            "reason": "",
        }

    run_auto_git_commit_worker(
        session.get_bind(),
        run.id,
        pre_commit_optimizer=fake_pre_commit_optimizer,
        draft_generator=fake_draft_generator,
    )

    session.expire_all()
    updated = session.get(AutoGitCommitRun, run.id)
    repo_result = updated.result_json["repos"][0]
    assert updated.status == "completed"
    assert updated.committed_repo_count == 1
    assert repo_result["status"] == "committed"
    assert repo_result["pre_commit_review"]["summary"] == "补充 review 优化"
    assert events[0] == ("optimizer", ["feature.txt"])
    assert events[1][0] == "draft"
    assert "review_fix.txt" in events[1][1]
    assert _run_git(dirty_repo, "log", "-1", "--pretty=%s") == "自动提交前先执行工程优化"
    assert "review_fix.txt" in _run_git(dirty_repo, "show", "--name-only", "--pretty=", "HEAD")
    assert _run_git(dirty_repo, "status", "--short") == ""


def test_auto_git_commit_worker_blocks_obvious_dot_tmp_directory(session, auth_user, tmp_path):
    dirty_repo = tmp_path / "dirty-temp-repo"
    _init_git_repo(dirty_repo)
    tmp_dir = dirty_repo / ".tmp_pdf_check"
    tmp_dir.mkdir()
    (tmp_dir / "page_1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _save_auto_commit_repos(
        session,
        auth_user.id,
        [
            {"id": "cy", "name": "codeyun", "cwd": dirty_repo},
        ],
    )
    run = create_auto_git_commit_run(session, trigger_reason="test", enqueue=False)

    def unexpected_draft_generator(**kwargs):
        raise AssertionError("precheck should block before AI draft generation")

    run_auto_git_commit_worker(
        session.get_bind(),
        run.id,
        draft_generator=unexpected_draft_generator,
    )

    session.expire_all()
    updated = session.get(AutoGitCommitRun, run.id)
    assert updated.status == "completed"
    assert updated.changed_repo_count == 1
    assert updated.committed_repo_count == 0
    assert updated.failed_repo_count == 1
    repo_result = updated.result_json["repos"][0]
    assert repo_result["status"] == "failed"
    assert "提交前预检未通过" in repo_result["error_message"]
    assert ".tmp_pdf_check/page_1.png" in repo_result["changed_paths"]
    assert _run_git(dirty_repo, "log", "-1", "--pretty=%s") == "init"
    assert "?? .tmp_pdf_check/" in _run_git(dirty_repo, "status", "--short")


def test_auto_git_commit_worker_records_ai_failure_without_blocking_or_committing(session, auth_user, tmp_path, monkeypatch):
    dirty_repo = tmp_path / "dirty-failure-repo"
    _init_git_repo(dirty_repo)
    (dirty_repo / "feature.txt").write_text("new feature\n", encoding="utf-8")
    _save_auto_commit_repos(
        session,
        auth_user.id,
        [
            {"id": "cy", "name": "codeyun", "cwd": dirty_repo},
        ],
    )
    run = create_auto_git_commit_run(session, trigger_reason="test", enqueue=False)

    monkeypatch.setattr(
        "backend.core.auto_git_commit.resolve_ai_runtime_config",
        lambda **_: ("ollama", None, None, ()),
    )

    def failing_draft_generator(**kwargs):
        raise AiGitCommitError("模型额度不足")

    run_auto_git_commit_worker(
        session.get_bind(),
        run.id,
        pre_commit_optimizer=_noop_pre_commit_optimizer,
        draft_generator=failing_draft_generator,
    )

    session.expire_all()
    updated = session.get(AutoGitCommitRun, run.id)
    assert updated.status == "completed"
    assert updated.changed_repo_count == 1
    assert updated.committed_repo_count == 0
    assert updated.failed_repo_count == 1
    assert "自动提交失败" in updated.error_message
    repo_result = updated.result_json["repos"][0]
    assert repo_result["status"] == "failed"
    assert repo_result["error_message"] == "模型额度不足"
    assert _run_git(dirty_repo, "log", "-1", "--pretty=%s") == "init"
    assert "feature.txt" in _run_git(dirty_repo, "status", "--short")
