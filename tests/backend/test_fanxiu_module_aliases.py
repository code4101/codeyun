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


def test_fanxiu_status_models_legacy_module_aliases_canonical_module():
    legacy = importlib.import_module("backend.core.fanxiu_status_models")
    canonical = importlib.import_module("backend.core.fanxiu.catalog.status_models")

    assert legacy is canonical
    assert hasattr(legacy, "FanxiuBehaviorTreeServiceStatus")


def test_fanxiu_catalog_legacy_modules_alias_canonical_modules():
    pairs = [
        ("backend.core.fanxiu_item_catalog", "backend.core.fanxiu.catalog.item"),
        ("backend.core.fanxiu_hot_update", "backend.core.fanxiu.catalog.hot_update"),
        ("backend.core.fanxiu_protocol_semantics", "backend.core.fanxiu.catalog.protocol_semantics"),
        ("backend.core.fanxiu_apk_static", "backend.core.fanxiu.catalog.apk_static"),
    ]

    for legacy_name, canonical_name in pairs:
        legacy = importlib.import_module(legacy_name)
        canonical = importlib.import_module(canonical_name)
        assert legacy is canonical, legacy_name


def test_fanxiu_catalog_supporting_legacy_modules_alias_canonical_modules():
    pairs = [
        ("backend.core.fanxiu_resources", "backend.core.fanxiu.catalog.resources"),
        ("backend.core.fanxiu_lua_config", "backend.core.fanxiu.catalog.lua_config"),
        ("backend.core.fanxiu_lua_packet_index", "backend.core.fanxiu.catalog.lua_packet_index"),
        ("backend.core.fanxiu_wiki", "backend.core.fanxiu.catalog.wiki"),
        ("backend.core.fanxiu_timeline", "backend.core.fanxiu.catalog.timeline"),
        ("backend.core.fanxiu_gongfa_catalog", "backend.core.fanxiu.catalog.gongfa"),
        ("backend.core.fanxiu_packet_insights", "backend.core.fanxiu.packet.insights"),
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


def test_fanxiu_legacy_external_service_routes_are_marked_deprecated():
    from backend.api.fanxiu import status_router

    deprecated_paths = {
        "/processes/terminate",
        "/behavior-tree-service",
        "/behavior-tree-service/start",
        "/behavior-tree-service/stop",
    }

    matched = {
        route.path: bool(getattr(route, "deprecated", False))
        for route in status_router.routes
        if getattr(route, "path", None) in deprecated_paths
    }

    assert matched == {path: True for path in deprecated_paths}


def test_fanxiu_behavior_tree_service_status_model_accepts_root_child_counts():
    from backend.core.fanxiu.catalog.status_models import FanxiuBehaviorTreeServiceStatus

    payload = FanxiuBehaviorTreeServiceStatus.model_validate(
        {
            "key": "fanxiu-behavior-tree",
            "title": "凡修行为树",
            "running": True,
            "state": "running",
            "state_label": "运行中",
            "pid": 123,
            "process_count": 1,
            "child_process_count": 2,
            "total_process_count": 3,
            "processes": [],
            "registry": {},
            "registry_pid_alive": True,
            "heartbeat_age_seconds": 5,
            "started_at": "2026-07-01 13:30:00",
            "heartbeat_at": "2026-07-01 13:31:00",
            "last_error": "",
            "root": "C:/fanxiu",
            "registry_path": "registry.json",
            "status_path": "status.json",
            "behavior_tree_log_path": "behavior_tree.log",
            "service_log_path": "service.log",
            "script_path": "fanxiu_bt.py",
            "python_path": "python.exe",
        }
    )

    assert payload.process_count == 1
    assert payload.child_process_count == 2
    assert payload.total_process_count == 3
