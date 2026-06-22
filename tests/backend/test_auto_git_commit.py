import subprocess
from datetime import datetime

from sqlmodel import select

from backend.core.ai.git_commit import AiGitCommitError
from backend.core.ai.git_repos import save_user_ai_git_repos
from backend.core.ai.auto_git_commit import (
    AUTO_GIT_COMMIT_CRON,
    AUTO_GIT_COMMIT_MIN_CHANGED_LINES,
    AUTO_GIT_COMMIT_ORPHANED_QUEUE_GRACE_SECONDS,
    AUTO_GIT_COMMIT_SCHEDULE_SETTING_KEY,
    AUTO_GIT_COMMIT_STALE_HEARTBEAT_SECONDS,
    AutoGitCommitCandidate,
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
        stage_label="检查/提交 codeyun",
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


def test_auto_git_commit_marks_orphaned_queued_run_failed(session):
    now_ts = 2_000_000.0
    orphaned_ts = now_ts - AUTO_GIT_COMMIT_ORPHANED_QUEUE_GRACE_SECONDS - 1
    orphaned_run = AutoGitCommitRun(
        status="pending",
        trigger_reason="idle_maintenance",
        run_date="2026-05-06",
        stage="queued",
        stage_label="已进入队列",
        queue_task_id="missing-queue-task",
        heartbeat_at=orphaned_ts,
        created_at=orphaned_ts,
        updated_at=orphaned_ts,
    )
    session.add(orphaned_run)
    session.commit()

    changed_count = mark_stale_auto_git_commit_runs(
        session,
        now_ts=now_ts,
        queue_snapshot={"running": None, "pending": []},
    )

    assert changed_count == 1
    session.expire_all()
    updated = session.get(AutoGitCommitRun, orphaned_run.id)
    assert updated.status == "failed"
    assert updated.stage == "orphaned_queue"
    assert updated.stage_label == "队列任务丢失"
    assert "队列中没有对应 auto_git_commit 任务" in updated.error_message


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
            stage_label="检查/提交 codeyun",
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
        "backend.core.ai.auto_git_commit.resolve_ai_runtime_config",
        lambda **_: ("ollama", None, None, ()),
    )
    monkeypatch.setattr("backend.core.ai.auto_git_commit.AUTO_GIT_COMMIT_MIN_CHANGED_LINES", 1)

    draft_calls = []

    def fake_draft_generator(**kwargs):
        draft_calls.append(kwargs)
        return {
            "subject": "自动提交凌晨检查改动",
            "body": ["提交自动检查发现的仓库变更"],
            "model": "fake-model",
            "needs_split": False,
            "reason": "",
        }

    run_auto_git_commit_worker(
        session.get_bind(),
        run.id,
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
    assert "codeyun 自动提交只生成提交信息，不执行提交前自动优化" in repo_result["pre_commit_review"]["summary"]
    assert repo_result["commit_strategy"] == "lightweight_ai"
    assert len(draft_calls) == 1
    reduction_input = draft_calls[0]["reduction_input"]
    assert reduction_input["lightweight"] is True
    lightweight_content = reduction_input["source_units"][0]["content"]
    assert "feature.txt" in lightweight_content
    assert "new feature" not in lightweight_content
    assert _run_git(dirty_repo, "log", "-1", "--pretty=%s") == "自动提交凌晨检查改动"
    assert _run_git(dirty_repo, "status", "--short") == ""


def test_auto_git_commit_worker_skips_dirty_repo_below_line_threshold(session, auth_user, tmp_path, monkeypatch):
    dirty_repo = tmp_path / "small-dirty-repo"
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

    draft_calls = []

    def fake_draft_generator(**kwargs):
        draft_calls.append(kwargs)
        return {
            "subject": "不应生成",
            "body": [],
            "model": "fake-model",
            "needs_split": False,
            "reason": "",
        }

    run_auto_git_commit_worker(
        session.get_bind(),
        run.id,
        draft_generator=fake_draft_generator,
    )

    session.expire_all()
    updated = session.get(AutoGitCommitRun, run.id)
    assert updated.status == "skipped"
    assert updated.stage == "below_threshold"
    assert updated.stage_label == "未达到自动提交阈值"
    assert updated.changed_repo_count == 1
    assert updated.committed_repo_count == 0
    assert updated.skipped_repo_count == 1
    assert draft_calls == []
    repo_result = updated.result_json["repos"][0]
    assert repo_result["status"] == "skipped"
    assert repo_result["skip_reason"] == "below_line_threshold"
    assert repo_result["auto_commit_line_threshold"] == AUTO_GIT_COMMIT_MIN_CHANGED_LINES
    assert repo_result["estimated_changed_line_count"] < AUTO_GIT_COMMIT_MIN_CHANGED_LINES
    assert _run_git(dirty_repo, "log", "-1", "--pretty=%s") == "init"
    assert "feature.txt" in _run_git(dirty_repo, "status", "--short")


def test_auto_git_commit_worker_uses_lightweight_ai_for_large_codeyun(session, auth_user, tmp_path, monkeypatch):
    dirty_repo = tmp_path / "large-codeyun-repo"
    _init_git_repo(dirty_repo)
    for index in range(51):
        (dirty_repo / f"feature_{index:02d}.txt").write_text(f"new feature {index}\n", encoding="utf-8")
    _save_auto_commit_repos(
        session,
        auth_user.id,
        [
            {"id": "cy", "name": "codeyun", "cwd": dirty_repo},
        ],
    )
    run = create_auto_git_commit_run(session, trigger_reason="test", enqueue=False)

    monkeypatch.setattr(
        "backend.core.ai.auto_git_commit.resolve_ai_runtime_config",
        lambda **_: ("ollama", None, None, ()),
    )
    monkeypatch.setattr("backend.core.ai.auto_git_commit.AUTO_GIT_COMMIT_MIN_CHANGED_LINES", 1)

    draft_calls = []

    def fake_draft_generator(**kwargs):
        draft_calls.append(kwargs)
        return {
            "subject": "汇总 codeyun 大规模变更",
            "body": ["根据轻量摘要提交 codeyun 的大规模变更。"],
            "model": "fake-model",
            "needs_split": True,
            "reason": "变更规模较大，建议后续拆分",
        }

    run_auto_git_commit_worker(
        session.get_bind(),
        run.id,
        draft_generator=fake_draft_generator,
    )

    session.expire_all()
    updated = session.get(AutoGitCommitRun, run.id)
    assert updated.status == "completed"
    assert updated.changed_repo_count == 1
    assert updated.committed_repo_count == 1
    assert updated.failed_repo_count == 0
    repo_result = updated.result_json["repos"][0]
    assert repo_result["status"] == "committed"
    assert repo_result["commit_strategy"] == "lightweight_ai"
    assert repo_result["provider"] == "ollama"
    assert repo_result["model"] == "fake-model"
    assert repo_result["needs_split"] is True
    assert repo_result["split_reason"] == "变更规模较大，建议后续拆分"
    assert len(draft_calls) == 1
    reduction_input = draft_calls[0]["reduction_input"]
    assert reduction_input["lightweight"] is True
    lightweight_content = reduction_input["source_units"][0]["content"]
    assert "feature_00.txt" in lightweight_content
    assert "new feature 0" not in lightweight_content
    assert "不要使用固定 checkpoint 占位标题" in lightweight_content
    assert _run_git(dirty_repo, "log", "-1", "--pretty=%s") == "汇总 codeyun 大规模变更"
    assert _run_git(dirty_repo, "status", "--short") == ""


def test_auto_git_commit_worker_records_summary_only_before_draft(session, auth_user, tmp_path, monkeypatch):
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
        "backend.core.ai.auto_git_commit.resolve_ai_runtime_config",
        lambda **_: ("ollama", None, None, ()),
    )
    monkeypatch.setattr("backend.core.ai.auto_git_commit.AUTO_GIT_COMMIT_MIN_CHANGED_LINES", 1)

    events = []

    def fake_draft_generator(**kwargs):
        events.append(("draft", _run_git(dirty_repo, "status", "--short")))
        return {
            "subject": "自动提交凌晨检查改动",
            "body": ["提交自动检查发现的仓库变更"],
            "model": "fake-model",
            "needs_split": False,
            "reason": "",
        }

    run_auto_git_commit_worker(
        session.get_bind(),
        run.id,
        draft_generator=fake_draft_generator,
    )

    session.expire_all()
    updated = session.get(AutoGitCommitRun, run.id)
    repo_result = updated.result_json["repos"][0]
    assert updated.status == "completed"
    assert updated.committed_repo_count == 1
    assert repo_result["status"] == "committed"
    assert repo_result["pre_commit_review"]["status"] == "skipped"
    assert "pyxllib 自动提交只生成提交信息，不执行提交前自动优化" in repo_result["pre_commit_review"]["summary"]
    assert events[0][0] == "draft"
    assert "feature.txt" in events[0][1]
    assert _run_git(dirty_repo, "log", "-1", "--pretty=%s") == "自动提交凌晨检查改动"
    assert "review_fix.txt" not in _run_git(dirty_repo, "show", "--name-only", "--pretty=", "HEAD")
    assert _run_git(dirty_repo, "status", "--short") == ""


def test_auto_git_commit_worker_auto_ignores_obvious_dot_tmp_directory(session, auth_user, tmp_path, monkeypatch):
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

    monkeypatch.setattr(
        "backend.core.ai.auto_git_commit.resolve_ai_runtime_config",
        lambda **_: ("ollama", None, None, ()),
    )
    monkeypatch.setattr("backend.core.ai.auto_git_commit.AUTO_GIT_COMMIT_MIN_CHANGED_LINES", 1)

    def fake_draft_generator(**kwargs):
        return {
            "subject": "忽略自动提交临时产物",
            "body": ["提交 .gitignore 规则，避免临时产物阻断自动提交。"],
            "model": "fake-model",
            "needs_split": False,
            "reason": "",
        }

    run_auto_git_commit_worker(
        session.get_bind(),
        run.id,
        draft_generator=fake_draft_generator,
    )

    session.expire_all()
    updated = session.get(AutoGitCommitRun, run.id)
    assert updated.status == "completed"
    assert updated.stage == "completed"
    assert updated.stage_label == "已提交 1 个仓库"
    assert updated.changed_repo_count == 1
    assert updated.committed_repo_count == 1
    assert updated.failed_repo_count == 0
    repo_result = updated.result_json["repos"][0]
    assert repo_result["status"] == "committed"
    assert repo_result["auto_gitignore"]["status"] == "updated"
    assert repo_result["auto_gitignore"]["patterns"] == [".tmp_pdf_check/"]
    assert ".tmp_pdf_check/" in (dirty_repo / ".gitignore").read_text(encoding="utf-8")
    assert _run_git(dirty_repo, "log", "-1", "--pretty=%s") == "忽略自动提交临时产物"
    committed_paths = _run_git(dirty_repo, "show", "--name-only", "--pretty=", "HEAD")
    assert ".gitignore" in committed_paths
    assert ".tmp_pdf_check/page_1.png" not in committed_paths
    assert _run_git(dirty_repo, "status", "--short") == ""


def test_auto_git_commit_worker_marks_run_failed_when_any_repo_fails(session, auth_user, tmp_path, monkeypatch):
    committed_repo = tmp_path / "commit-repo"
    failed_repo = tmp_path / "failed-repo"
    _init_git_repo(committed_repo)
    _init_git_repo(failed_repo)
    (committed_repo / "feature.txt").write_text("new feature\n", encoding="utf-8")
    (failed_repo / "feature.txt").write_text("new feature\n", encoding="utf-8")
    run = create_auto_git_commit_run(session, trigger_reason="test", enqueue=False)

    monkeypatch.setattr(
        "backend.core.ai.auto_git_commit.resolve_ai_runtime_config",
        lambda **_: ("ollama", None, None, ()),
    )
    monkeypatch.setattr("backend.core.ai.auto_git_commit.AUTO_GIT_COMMIT_MIN_CHANGED_LINES", 1)

    def fake_draft_generator(**kwargs):
        if str(kwargs.get("cwd") or "") == str(failed_repo):
            raise AiGitCommitError("模型额度不足")
        return {
            "subject": "提交部分仓库变更",
            "body": ["提交正常仓库，保留失败仓库现场。"],
            "model": "fake-model",
            "needs_split": False,
            "reason": "",
        }

    candidates = [
        AutoGitCommitCandidate(
            user_id=auth_user.id,
            username=auth_user.username,
            name="codeyun",
            cwd=str(committed_repo),
            entry_id="cy",
        ),
        AutoGitCommitCandidate(
            user_id=auth_user.id,
            username=auth_user.username,
            name="xlproject",
            cwd=str(failed_repo),
            entry_id="xl",
        ),
    ]

    run_auto_git_commit_worker(
        session.get_bind(),
        run.id,
        candidate_selector=lambda _session: candidates,
        draft_generator=fake_draft_generator,
    )

    session.expire_all()
    updated = session.get(AutoGitCommitRun, run.id)
    assert updated.status == "failed"
    assert updated.stage == "failed"
    assert updated.stage_label == "已提交 1 个仓库，1 个仓库失败"
    assert updated.committed_repo_count == 1
    assert updated.failed_repo_count == 1
    assert _run_git(committed_repo, "log", "-1", "--pretty=%s") == "提交部分仓库变更"
    assert "?? feature.txt" in _run_git(failed_repo, "status", "--short")


def test_auto_git_commit_worker_raises_for_queue_retry_when_repo_fails(session, auth_user, tmp_path, monkeypatch):
    dirty_repo = tmp_path / "dirty-queue-retry-repo"
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
        "backend.core.ai.auto_git_commit.resolve_ai_runtime_config",
        lambda **_: ("ollama", None, None, ()),
    )
    monkeypatch.setattr("backend.core.ai.auto_git_commit.AUTO_GIT_COMMIT_MIN_CHANGED_LINES", 1)

    def failing_draft_generator(**kwargs):
        raise AiGitCommitError("模型额度不足")

    try:
        run_auto_git_commit_worker(
            session.get_bind(),
            run.id,
            draft_generator=failing_draft_generator,
            raise_on_failure=True,
        )
    except RuntimeError as exc:
        assert "1 个仓库自动提交失败" in str(exc)
    else:
        raise AssertionError("expected failed auto git worker to raise for queue retry")

    session.expire_all()
    updated = session.get(AutoGitCommitRun, run.id)
    assert updated.status == "failed"
    assert updated.stage == "failed"
    assert updated.failed_repo_count == 1


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
        "backend.core.ai.auto_git_commit.resolve_ai_runtime_config",
        lambda **_: ("ollama", None, None, ()),
    )
    monkeypatch.setattr("backend.core.ai.auto_git_commit.AUTO_GIT_COMMIT_MIN_CHANGED_LINES", 1)

    def failing_draft_generator(**kwargs):
        raise AiGitCommitError("模型额度不足")

    run_auto_git_commit_worker(
        session.get_bind(),
        run.id,
        draft_generator=failing_draft_generator,
    )

    session.expire_all()
    updated = session.get(AutoGitCommitRun, run.id)
    assert updated.status == "failed"
    assert updated.stage == "failed"
    assert updated.stage_label == "1 个仓库失败"
    assert updated.changed_repo_count == 1
    assert updated.committed_repo_count == 0
    assert updated.failed_repo_count == 1
    assert "自动提交失败" in updated.error_message
    repo_result = updated.result_json["repos"][0]
    assert repo_result["status"] == "failed"
    assert repo_result["error_message"] == "模型额度不足"
    assert _run_git(dirty_repo, "log", "-1", "--pretty=%s") == "init"
    assert "feature.txt" in _run_git(dirty_repo, "status", "--short")
