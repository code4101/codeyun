from __future__ import annotations

import json
from pathlib import Path

from backend.core.maintenance import idle_maintenance


def test_select_idle_maintenance_prefers_auto_commit_when_worktree_dirty():
    decision = idle_maintenance.select_idle_maintenance_task(
        git_inspect={"clean": False, "changed_files": [{"path": "README.md"}]},
    )

    assert decision.selected_task_key == "auto_commit_dirty_worktree"
    assert any(item["key"] == "docs_sync_scan" for item in decision.candidates)


def test_select_idle_maintenance_prefers_auto_commit_when_any_managed_repo_dirty():
    decision = idle_maintenance.select_idle_maintenance_task(
        repo_inspects=[
            {"name": "pyxllib", "has_changes": False},
            {"name": "xlproject", "has_changes": True, "changed_file_count": 2},
            {"name": "codeyun", "has_changes": False},
        ],
    )

    assert decision.selected_task_key == "auto_commit_dirty_worktree"


def test_select_idle_maintenance_uses_read_only_scan_when_worktree_clean():
    decision = idle_maintenance.select_idle_maintenance_task(
        git_inspect={"clean": True, "changed_files": []},
    )

    assert decision.selected_task_key == "docs_sync_scan"


def test_select_idle_maintenance_rotates_read_only_scans_when_worktree_clean():
    decision = idle_maintenance.select_idle_maintenance_task(
        git_inspect={"clean": True, "changed_files": []},
        last_task_key="docs_sync_scan",
    )

    assert decision.selected_task_key == "code_slimming_scan"


def test_run_idle_maintenance_skips_when_other_queue_task_is_running(tmp_path):
    result = idle_maintenance.run_idle_maintenance_once(
        queue_snapshot={
            "running": {"name": "auto_git_commit"},
            "pending": [],
        },
        report_dir=tmp_path,
    )

    assert result["status"] == "skipped"
    assert "auto_git_commit" in result["reason"]
    report_path = tmp_path / Path(result["report_path"]).name
    assert report_path.exists()


def test_run_idle_maintenance_executes_selected_task_and_writes_report(monkeypatch, tmp_path):
    task = idle_maintenance.IdleMaintenanceTask(
        key="docs_sync_scan",
        title="文档事实对齐扫描",
        category="docs",
        risk="low",
        mode="read_only",
        success_metric="missing_doc_path_ref_count",
        action=lambda: {"status": "completed", "missing_path_ref_count": 0},
    )
    monkeypatch.setattr(
        idle_maintenance,
        "_inspect_auto_commit_repositories",
        lambda: [{"name": "codeyun", "has_changes": False}],
    )

    result = idle_maintenance.run_idle_maintenance_once(
        task_pool=[task],
        queue_snapshot={"running": {"name": idle_maintenance.IDLE_MAINTENANCE_QUEUE_NAME}, "pending": []},
        report_dir=tmp_path,
    )

    assert result["status"] == "completed"
    assert result["task_key"] == "docs_sync_scan"
    report = json.loads((tmp_path / Path(result["report_path"]).name).read_text(encoding="utf-8"))
    assert report["task"]["success_metric"] == "missing_doc_path_ref_count"
    assert report["repo_inspects"][0]["name"] == "codeyun"


def test_docs_sync_scan_reports_missing_repo_path(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    (docs / "guide.md").write_text("See `backend/missing.py` for details.", encoding="utf-8")
    (repo / "backend").mkdir()

    monkeypatch.setattr(idle_maintenance, "ROOT_DIR", repo)

    result = idle_maintenance._run_docs_sync_scan_task()

    assert result["missing_path_ref_count"] == 1
    assert result["issues"][0]["ref"] == "backend/missing.py"
