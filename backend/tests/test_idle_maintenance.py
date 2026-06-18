from __future__ import annotations

import json
from pathlib import Path

from backend.core.maintenance import idle_maintenance


def test_select_idle_maintenance_prefers_auto_commit_when_worktree_dirty():
    decision = idle_maintenance.select_idle_maintenance_task(
        git_inspect={"clean": False, "changed_files": [{"path": "README.md"}]},
    )

    assert decision.selected_task_key == "auto_commit_dirty_worktree"
    assert any(
        item["key"] == "auto_commit_dirty_worktree"
        and item["title"] == "GitHub 项目脏工作区自动提交"
        for item in decision.candidates
    )
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


def test_docs_sync_scan_ignores_packaging_sources_noise(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    egg_info = repo / "backend" / "codeyun_backend.egg-info"
    docs = repo / "docs"
    egg_info.mkdir(parents=True)
    docs.mkdir()
    (egg_info / "SOURCES.txt").write_text("tests/missing_from_build_metadata.py\n", encoding="utf-8")
    (docs / "guide.md").write_text("See `backend/missing.py` for details.", encoding="utf-8")
    (repo / "backend").mkdir(exist_ok=True)

    monkeypatch.setattr(idle_maintenance, "ROOT_DIR", repo)

    result = idle_maintenance._run_docs_sync_scan_task()

    assert result["missing_path_ref_count"] == 1
    assert result["issues"][0]["ref"] == "backend/missing.py"


def test_docs_sync_scan_ignores_glob_and_code_style_refs(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    (repo / "backend").mkdir()
    (repo / "frontend" / "src" / "standard" / "notes").mkdir(parents=True)
    (docs / "guide.md").write_text(
        "\n".join(
            [
                "Use `frontend/src/standard/**/index.ts` as a route pattern.",
                "Call `backend.core.temp_paths.codeyun_temp_root(...)` in Python.",
                "See `backend/missing.py` for details.",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(idle_maintenance, "ROOT_DIR", repo)

    result = idle_maintenance._run_docs_sync_scan_task()

    assert result["missing_path_ref_count"] == 1
    assert result["issues"][0]["ref"] == "backend/missing.py"


def test_docs_sync_scan_ignores_windows_absolute_paths(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    (repo / "backend").mkdir()
    (docs / "guide.md").write_text(
        "\n".join(
            [
                r"Repo example: `D:\home\chenkunze\slns\codeyun\backend`.",
                r"Old context: `c:\home\chenkunze\slns\codeyun\frontend`.",
                r"Report path: `%TEMP%\codeyun\idle-maintenance\20260618-docs_sync_scan.json`.",
                "See `backend/missing.py` for details.",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(idle_maintenance, "ROOT_DIR", repo)

    result = idle_maintenance._run_docs_sync_scan_task()

    assert result["missing_path_ref_count"] == 1
    assert result["issues"][0]["ref"] == "backend/missing.py"


def test_docs_sync_scan_does_not_truncate_markdown_links_or_unicode_paths(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    (repo / "backend").mkdir()
    (docs / "My File.md").write_text("# guide\n", encoding="utf-8")
    (docs / "CodeYun代码健康长期优化上下文.md").write_text("# context\n", encoding="utf-8")
    (docs / "自动部署恢复档案.md").write_text("# deploy archive\n", encoding="utf-8")
    (docs / "guide.md").write_text(
        "\n".join(
            [
                "See [docs/自动部署恢复档案.md](docs/自动部署恢复档案.md).",
                "Read [file with space](docs/My File.md).",
                "Long-term context: docs/CodeYun代码健康长期优化上下文.md",
                "See `backend/missing.py` for details.",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(idle_maintenance, "ROOT_DIR", repo)

    result = idle_maintenance._run_docs_sync_scan_task()

    assert result["missing_path_ref_count"] == 1
    assert result["issues"][0]["ref"] == "backend/missing.py"


def test_docs_sync_scan_ignores_template_placeholder_paths(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    (repo / "backend").mkdir()
    (repo / "frontend").mkdir()
    (docs / "guide.md").write_text(
        "\n".join(
            [
                "Plugin template: `frontend/src/plugins/modules/<插件名>/index.ts`.",
                "Domain template: `backend/standard/<域>/...`.",
                "See `backend/missing.py` for details.",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(idle_maintenance, "ROOT_DIR", repo)

    result = idle_maintenance._run_docs_sync_scan_task()

    assert result["missing_path_ref_count"] == 1
    assert result["issues"][0]["ref"] == "backend/missing.py"
