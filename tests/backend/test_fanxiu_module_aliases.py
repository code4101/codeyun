from __future__ import annotations

import importlib


def test_fanxiu_behavior_tree_legacy_module_aliases_canonical_module():
    legacy = importlib.import_module("backend.core.fanxiu_behavior_tree")
    canonical = importlib.import_module("backend.core.fanxiu.runtime.behavior_tree")

    assert legacy is canonical
    assert hasattr(legacy, "fanxiu_local_task_should_enqueue")


def test_fanxiu_data_annotation_legacy_modules_alias_canonical_modules():
    pairs = [
        ("backend.core.fanxiu_data_annotation_runtime_control", "backend.core.fanxiu.data_annotation.runtime_control"),
        ("backend.core.fanxiu_data_annotation_runner", "backend.core.fanxiu.data_annotation.runner"),
        ("backend.core.fanxiu_data_annotation_state", "backend.core.fanxiu.data_annotation.state"),
        ("backend.core.fanxiu_data_annotation_jobs", "backend.core.fanxiu.data_annotation.jobs"),
        ("backend.core.fanxiu_data_annotation_default_jobs", "backend.core.fanxiu.data_annotation.default_jobs"),
        ("backend.core.fanxiu_data_annotation_debug_eval", "backend.core.fanxiu.data_annotation.debug_eval"),
        ("backend.core.fanxiu_data_annotation_scheduler", "backend.core.fanxiu.data_annotation.scheduler"),
        (
            "backend.core.fanxiu_data_annotation_scheduler_defaults",
            "backend.core.fanxiu.data_annotation.scheduler_defaults",
        ),
    ]

    for legacy_name, canonical_name in pairs:
        legacy = importlib.import_module(legacy_name)
        canonical = importlib.import_module(canonical_name)
        assert legacy is canonical, legacy_name


def test_fanxiu_behavior_tree_service_log_lines_distinguish_root_and_descendants(monkeypatch):
    from backend.core import fanxiu_behavior_tree_service as fanxiu_service

    status = {
        "title": "凡修行为树",
        "state_label": "运行中",
        "pid": 123,
        "process_count": 1,
        "child_process_count": 2,
        "total_process_count": 3,
        "started_at": "2026-07-01 13:30:00",
        "heartbeat_at": "2026-07-01 13:31:00",
        "registry_path": "registry.json",
        "status_path": "status.json",
        "behavior_tree_log_path": "behavior_tree.log",
        "service_log_path": "service.log",
        "last_error": "",
        "registry": {},
        "processes": [
            {"pid": 123, "parent_pid": 1, "created_at": "2026-07-01 13:30:00", "command_line": "python fanxiu_bt.py service"},
            {"pid": 456, "parent_pid": 123, "created_at": "2026-07-01 13:30:01", "command_line": "python fanxiu_bt.py service"},
            {"pid": 789, "parent_pid": 123, "created_at": "2026-07-01 13:30:01", "command_line": "conhost.exe"},
        ],
        "root_processes": [
            {"pid": 123, "parent_pid": 1, "created_at": "2026-07-01 13:30:00", "command_line": "python fanxiu_bt.py service"},
        ],
    }

    monkeypatch.setattr(fanxiu_service, "get_behavior_tree_status", lambda: status)
    monkeypatch.setattr(fanxiu_service, "tail_text", lambda *_args, **_kwargs: ["tail"])

    lines = fanxiu_service.build_behavior_tree_log_lines(limit=80)

    assert "行为树根进程数：1" in lines
    assert "子孙进程数：2" in lines
    assert "总进程数：3" in lines
    assert any("子孙进程不代表第二个行为树" in line for line in lines)
    assert any("root · PID 123" in line for line in lines)
    assert any("descendant-of:123 · PID 456" in line for line in lines)
