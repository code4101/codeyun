import os
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.core import git_tools


def _run_git(repo_root: Path, *args: str, env: dict[str, str] | None = None) -> None:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged_env,
    )


def _init_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _run_git(repo_root, "init")
    _run_git(repo_root, "config", "user.name", "Codeyun Test")
    _run_git(repo_root, "config", "user.email", "test@example.com")


def _commit_file(repo_root: Path, relative_path: str, content: str, commit_day: date, message: str) -> None:
    file_path = repo_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    _run_git(repo_root, "add", relative_path)
    commit_timestamp = f"{commit_day.isoformat()}T12:00:00+08:00"
    _run_git(
        repo_root,
        "commit",
        "-m",
        message,
        env={
            "GIT_AUTHOR_DATE": commit_timestamp,
            "GIT_COMMITTER_DATE": commit_timestamp,
        },
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_collect_git_history_stats_aggregates_daily_numstat(tmp_path: Path) -> None:
    repo_root = tmp_path / "history-repo"
    _init_repo(repo_root)

    today = date.today()
    first_day = today - timedelta(days=2)
    second_day = today - timedelta(days=1)

    _commit_file(repo_root, "demo.txt", "one\n", first_day, "init demo")
    _commit_file(repo_root, "demo.txt", "two\nthree\n", second_day, "rewrite demo")
    _commit_file(repo_root, "extra.txt", "alpha\nbeta\ngamma\n", second_day, "add extra")

    payload = git_tools.collect_git_history_stats(str(repo_root), days=7)

    assert payload["days"] == 7
    assert payload["total_commit_count"] == 3
    assert payload["total_added_line_count"] == 6
    assert payload["total_deleted_line_count"] == 1
    assert len(payload["points"]) == 7

    point_map = {item["date"]: item for item in payload["points"]}
    assert point_map[first_day.isoformat()] == {
        "date": first_day.isoformat(),
        "added_line_count": 1,
        "deleted_line_count": 0,
        "commit_count": 1,
    }
    assert point_map[second_day.isoformat()] == {
        "date": second_day.isoformat(),
        "added_line_count": 5,
        "deleted_line_count": 1,
        "commit_count": 2,
    }

    zero_days = [
        item
        for item in payload["points"]
        if item["date"] not in {first_day.isoformat(), second_day.isoformat()}
    ]
    assert zero_days
    assert all(item["added_line_count"] == 0 for item in zero_days)
    assert all(item["deleted_line_count"] == 0 for item in zero_days)
    assert all(item["commit_count"] == 0 for item in zero_days)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_collect_git_history_stats_normalizes_window_days_for_empty_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "empty-history-repo"
    _init_repo(repo_root)

    payload = git_tools.collect_git_history_stats(str(repo_root), days=1)

    assert payload["days"] == 7
    assert len(payload["points"]) == 7
    assert payload["total_commit_count"] == 0
    assert payload["total_added_line_count"] == 0
    assert payload["total_deleted_line_count"] == 0
    assert all(item["commit_count"] == 0 for item in payload["points"])


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_collect_git_history_stats_supports_all_history_window(tmp_path: Path) -> None:
    repo_root = tmp_path / "all-history-repo"
    _init_repo(repo_root)

    today = date.today()
    first_day = today - timedelta(days=420)
    second_day = today - timedelta(days=35)

    _commit_file(repo_root, "demo.txt", "one\n", first_day, "init demo")
    _commit_file(repo_root, "demo.txt", "two\nthree\n", second_day, "rewrite demo")

    payload = git_tools.collect_git_history_stats(str(repo_root), days=0)

    expected_days = (today - first_day).days + 1
    assert payload["days"] == expected_days
    assert payload["start_date"] == first_day.isoformat()
    assert payload["end_date"] == today.isoformat()
    assert len(payload["points"]) == expected_days
    assert payload["total_commit_count"] == 2

    point_map = {item["date"]: item for item in payload["points"]}
    assert point_map[first_day.isoformat()]["commit_count"] == 1
    assert point_map[second_day.isoformat()]["commit_count"] == 1
