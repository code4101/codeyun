from __future__ import annotations

import importlib


def test_fanxiu_behavior_tree_legacy_module_aliases_canonical_module():
    legacy = importlib.import_module("backend.core.fanxiu_behavior_tree")
    canonical = importlib.import_module("backend.core.fanxiu.behavior_tree.runtime")

    assert legacy is canonical
    assert hasattr(legacy, "ensure_fanxiu_behavior_tree_service")


def test_fanxiu_data_annotation_legacy_modules_alias_canonical_modules():
    pairs = [
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
    assert not hasattr(legacy, "FanxiuBehaviorTreeServiceStatus")


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
    ]

    for legacy_name, canonical_name in pairs:
        legacy = importlib.import_module(legacy_name)
        canonical = importlib.import_module(canonical_name)
        assert legacy is canonical, legacy_name


def test_fanxiu_legacy_external_service_routes_are_removed():
    from backend.api.fanxiu import status_router

    removed_paths = {
        "/behavior-tree-service",
        "/behavior-tree-service/start",
        "/behavior-tree-service/stop",
    }

    paths = {getattr(route, "path", None) for route in status_router.routes}

    assert not (paths & removed_paths)
    assert "/processes/terminate" in paths


def test_fanxiu_legacy_external_service_module_is_removed():
    import pytest

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backend.core.fanxiu_behavior_tree_service")

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backend.core.fanxiu.behavior_tree.service")
