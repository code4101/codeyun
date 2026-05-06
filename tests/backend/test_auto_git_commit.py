import subprocess

from backend.core.ai_git_commit import AiGitCommitError
from backend.core.ai_git_repos import save_user_ai_git_repos
from backend.core.auto_git_commit import (
    create_auto_git_commit_run,
    run_auto_git_commit_worker,
    select_auto_git_commit_candidates,
)
from backend.models import AutoGitCommitRun


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
    assert _run_git(dirty_repo, "log", "-1", "--pretty=%s") == "自动提交凌晨检查改动"
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
