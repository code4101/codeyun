from __future__ import annotations

import json
from pathlib import Path

from backend.core.ai import ui_learning


def test_ui_design_learning_extracts_cases_and_updates_checkpoint(monkeypatch, tmp_path: Path):
    overview = {
        "groups": [
            {
                "threads": [
                    {
                        "id": "thread-ui-1",
                        "title": "调整前端页面布局",
                        "preview": "页面 UI 优化",
                        "project_label": "codeyun",
                        "updated_at": 100,
                    }
                ]
            }
        ]
    }
    detail = {
        "messages": [
            {"seq": 1, "role": "user", "text": "帮我做一个设置页面 UI。"},
            {"seq": 2, "role": "assistant", "text": "我会做一个大卡片式布局。"},
            {"seq": 3, "role": "user", "text": "不要过度卡片化，布局应该更紧凑，按钮按真实操作分组。"},
        ]
    }

    monkeypatch.setattr(ui_learning, "build_codex_overview", lambda *args, **kwargs: overview)
    monkeypatch.setattr(ui_learning, "build_codex_thread_detail", lambda *args, **kwargs: detail)

    result = ui_learning.run_ui_design_learning_once(report_root=tmp_path, session=object())

    assert result["status"] == "completed"
    assert result["scanned_thread_count"] == 1
    assert result["case_count"] == 1
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert report["source_layer"] == "raw_codex_session"
    assert report["derived_layer"] == "ui_design_learning_report"
    assert report["cases"][0]["thread_id"] == "thread-ui-1"
    assert "不要过度卡片化" in report["cases"][0]["user_text"]

    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["last_thread_updated_at"] == 100
    assert checkpoint["processed_thread_ids_at_last_updated_at"] == ["thread-ui-1"]


def test_ui_design_learning_skips_already_processed_threads(monkeypatch, tmp_path: Path):
    (tmp_path / "checkpoint.json").write_text(
        json.dumps(
            {
                "last_thread_updated_at": 100,
                "processed_thread_ids_at_last_updated_at": ["thread-ui-1"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    overview = {
        "groups": [
            {
                "threads": [
                    {
                        "id": "thread-ui-1",
                        "title": "调整前端页面布局",
                        "project_label": "codeyun",
                        "updated_at": 100,
                    }
                ]
            }
        ]
    }

    monkeypatch.setattr(ui_learning, "build_codex_overview", lambda *args, **kwargs: overview)

    result = ui_learning.run_ui_design_learning_once(report_root=tmp_path, session=object())

    assert result["status"] == "skipped"
    assert result["scanned_thread_count"] == 0
    assert result["case_count"] == 0
