from __future__ import annotations

from types import SimpleNamespace

import pytest

import backend.core.fanxiu.instrumentation.backpack_quick_settings as backpack_quick_settings

from backend.core.fanxiu.instrumentation.backpack_quick_settings import (
    _decode_quick_view,
    _select_unique_quick_view,
    _snapshot,
    clear_backpack_quick_view_cache,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)


def _field(values):
    identity = {
        "configBtn": LuaRef("table", 1),
        "ItemContent": LuaRef("table", 2),
        "BackPackQuickItem": LuaRef("table", 3),
    }
    return lambda _address, name: {**identity, **values}.get(name)


def test_active_quick_view_decodes_exact_quick_types_one_through_four():
    values = _decode_quick_view(
        100,
        field=_field({
            "isSelectedOpenBox": False,
            "isSelectedFenJie": True,
            "isSelectedMerge": True,
            "isSelectedUse": True,
        }),
    )
    assert values == {1: 0, 2: 1, 3: 1, 4: 1}


@pytest.mark.parametrize("bad_value", [None, 0, 1, "true"])
def test_quick_view_rejects_missing_or_non_boolean_setting(bad_value):
    with pytest.raises(FanxiuRuntimeMemoryError, match="明确 Lua boolean"):
        _decode_quick_view(
            100,
            field=_field({
                "isSelectedOpenBox": bad_value,
                "isSelectedFenJie": True,
                "isSelectedMerge": True,
                "isSelectedUse": True,
            }),
        )


def test_quick_view_rejects_missing_identity_and_ambiguous_instances():
    with pytest.raises(FanxiuRuntimeMemoryError, match="身份字段"):
        _decode_quick_view(
            100,
            field=lambda _address, name: True if name == "isShow" else None,
        )
    with pytest.raises(FanxiuRuntimeMemoryError, match="NotLoaded"):
        _select_unique_quick_view({})
    with pytest.raises(FanxiuRuntimeMemoryError, match="同时发现 2 个"):
        _select_unique_quick_view({1: {"values": {}}, 2: {"values": {}}})


def test_closed_window_removed_from_open_set_is_not_a_candidate():
    values = {
        "isSelectedOpenBox": False,
        "isSelectedFenJie": True,
        "isSelectedMerge": True,
        "isSelectedUse": True,
    }

    active = {"values": _decode_quick_view(100, field=_field(values))}
    # UIShowMgr.P_CloseOneWin removes the closed component before candidate
    # selection, so a closed view is represented by absence, not isShow=false.
    assert _select_unique_quick_view({100: active}) is active
    with pytest.raises(FanxiuRuntimeMemoryError, match="NotLoaded"):
        _select_unique_quick_view({})


def test_snapshot_skips_stale_closed_window_and_reads_fresh_active_view():
    clear_backpack_quick_view_cache()

    class Reader:
        def table(self, _address):
            return {
                "array": [LuaRef("table", 10), LuaRef("table", 20)],
                "fields": {},
            }

        def list_items(self, address):
            return {
                10: ([LuaRef("table", 11)], 1),
                20: ([LuaRef("table", 21)], 1),
            }[address.address]

    quick_values = {
        "isSelectedOpenBox": False,
        "isSelectedFenJie": True,
        "isSelectedMerge": True,
        "isSelectedUse": True,
    }

    def field(address, name):
        if name == "m_panel":
            return LuaRef("table", {11: 12, 21: 22}[address])
        if address == 12:
            raise FanxiuRuntimeMemoryError("stale closed quick view")
        if name in {"configBtn", "ItemContent", "BackPackQuickItem"}:
            return LuaRef("table", 100 + len(name))
        return quick_values.get(name)

    context = SimpleNamespace(
        reader=Reader(),
        field=field,
        memory=SimpleNamespace(pid=2629, process_start_ticks=5072),
        binding=SimpleNamespace(component_storage_address=1),
        timings={},
        cache_mode="test",
    )

    snapshot = _snapshot(context)

    assert snapshot["complete"] is True
    assert snapshot["panel_address"] == "0x16"
    assert snapshot["values"] == {"1": 0, "2": 1, "3": 1, "4": 1}


def test_public_snapshot_cold_recovers_once_after_stale_binding(monkeypatch):
    context = SimpleNamespace(
        memory=SimpleNamespace(pid=2629, process_start_ticks=5072),
        timings={},
    )
    attempts = []
    clears = []

    monkeypatch.setattr(
        backpack_quick_settings,
        "acquire_ui_runtime_context",
        lambda _keys: context,
    )

    def snapshot(_context):
        attempts.append(1)
        if len(attempts) == 1:
            raise FanxiuRuntimeMemoryError("stale binding")
        return {
            "ok": True,
            "complete": True,
            "values": {"1": 0, "2": 1, "3": 1, "4": 1},
            "performance": {"stages_seconds": {}},
        }

    monkeypatch.setattr(backpack_quick_settings, "_snapshot", snapshot)
    monkeypatch.setattr(
        backpack_quick_settings,
        "clear_backpack_quick_view_cache",
        lambda: clears.append("view"),
    )
    monkeypatch.setattr(
        backpack_quick_settings,
        "clear_ui_runtime_context_cache",
        lambda: clears.append("binding"),
    )

    result = backpack_quick_settings.read_backpack_quick_settings_snapshot()

    assert result["complete"] is True
    assert result["performance"]["recovery_mode"] == "cold_after_stale_view"
    assert len(attempts) == 2
    assert clears == ["view", "binding"]
