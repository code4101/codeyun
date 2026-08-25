from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.fanxiu.instrumentation import beast_spirit_quick_synthesis as subject
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)
from backend.core.fanxiu.instrumentation.service import FanxiuInstrumentationService


_IDENTITY = {
    "dropDownItem_material": LuaRef("table", 81),
    "dropDownItem_cost": LuaRef("table", 82),
    "confirmBtn": LuaRef("table", 83),
    "DropDownBox1": LuaRef("table", 84),
    "DropDownBox2": LuaRef("table", 85),
}


def _panel_field(overrides=None):
    values = {
        **_IDENTITY,
        "_selectLevelId": 4,
        "_selectCountId": 3,
        # Lua writes probabilityDic[count] * 0.1: this field is percent.
        "V_CurrentProbability": 100.0,
        **(overrides or {}),
    }
    return lambda _address, name: values.get(name)


def test_batch_panel_decodes_authoritative_source_level_count_and_percent():
    assert subject._decode_batch_panel(80, field=_panel_field()) == {
        "source_level": 4,
        "batch_size": 3,
        "success_probability_percent": 100.0,
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"confirmBtn": None}, "身份字段"),
        ({"_selectLevelId": True}, "正整数"),
        ({"_selectLevelId": 4.5}, "正整数"),
        ({"_selectCountId": None}, "正整数"),
        ({"V_CurrentProbability": None}, "明确数值"),
        ({"V_CurrentProbability": True}, "明确数值"),
        ({"V_CurrentProbability": 100.1}, "百分比范围"),
    ],
)
def test_batch_panel_rejects_incomplete_or_wrongly_typed_fields(overrides, message):
    with pytest.raises(FanxiuRuntimeMemoryError, match=message):
        subject._decode_batch_panel(80, field=_panel_field(overrides))


def test_batch_panel_candidate_must_be_unique():
    with pytest.raises(FanxiuRuntimeMemoryError, match="NotLoaded"):
        subject._select_unique_panel({})
    with pytest.raises(FanxiuRuntimeMemoryError, match="同时发现 2 个"):
        subject._select_unique_panel({1: {}, 2: {}})


class _Reader:
    def __init__(self, *, slot_in_hash=False, loaded=True):
        self.tables = {
            100: {
                "array": [],
                "fields": ({1: LuaRef("table", 10)} if loaded else {}),
            },
            60: {
                "array": [] if slot_in_hash else [None, None, LuaRef("table", 70)],
                "fields": {2: LuaRef("table", 70)} if slot_in_hash else {},
            },
        }

    def table(self, address):
        return self.tables[address]

    def list_items(self, ref):
        assert ref.address == 10
        return [LuaRef("table", 20)], 1


def _context(*, broken_panel=False, slot_in_hash=False, loaded=True, tab_index=1):
    outer_identity = {
        "panelRoot": LuaRef("table", 31),
        "tabBtnRoot": LuaRef("table", 32),
        "tabBtn": LuaRef("table", 33),
        "tabPanelGroup": LuaRef("table", 40),
        "open_index": 1,
    }
    fields = {
        (20, "m_panel"): LuaRef("table", 30),
        **{(30, name): value for name, value in outer_identity.items()},
        (40, "curTabIndex"): tab_index,
        (40, "panelShowComps"): LuaRef("table", 50),
        (50, "_dt_"): LuaRef("table", 60),
        (70, "m_panel"): LuaRef("table", 80),
        **{(80, name): value for name, value in _IDENTITY.items()},
        (80, "_selectLevelId"): 4,
        (80, "_selectCountId"): None if broken_panel else 3,
        (80, "V_CurrentProbability"): 100.0,
    }
    return SimpleNamespace(
        reader=_Reader(slot_in_hash=slot_in_hash, loaded=loaded),
        field=lambda address, name: fields.get((address, name)),
        binding=SimpleNamespace(component_storage_address=100),
        memory=SimpleNamespace(pid=123, process_start_ticks=456),
        timings={"discover": 0.2},
        cache_mode="cold",
    )


def test_snapshot_follows_active_second_tab_and_reports_stage_timings():
    result = subject._snapshot(_context())
    assert result["source_level"] == 4
    assert result["batch_size"] == 3
    assert result["active_tab_index"] == 1
    assert result["evidence"]["active_membership"] == "UIShowMgr.V_M_compDic"
    assert result["performance"]["cache_mode"] == "cold"
    assert result["performance"]["stages_seconds"]["window_scan"] >= 0


def test_snapshot_accepts_authoritative_numeric_hash_slot():
    result = subject._snapshot(_context(slot_in_hash=True))
    assert result["active_panel_address"] == "0x50"


@pytest.mark.parametrize(
    "context",
    [
        pytest.param(_context(loaded=False), id="closed-window-removed"),
        pytest.param(_context(tab_index=0), id="wrong-tab"),
    ],
)
def test_snapshot_rejects_closed_window_or_wrong_active_tab(context):
    with pytest.raises(FanxiuRuntimeMemoryError, match="NotLoaded"):
        subject._snapshot(context)


def test_open_batch_candidate_decode_failure_is_incomplete_not_skipped():
    with pytest.raises(FanxiuRuntimeMemoryError, match="_selectCountId"):
        subject._snapshot(_context(broken_panel=True))


def test_service_exposes_read_only_batch_panel_snapshot(monkeypatch):
    expected = {"ok": True, "source_level": 4}
    monkeypatch.setattr(subject, "read_beast_spirit_quick_synthesis_snapshot", lambda: expected)
    assert FanxiuInstrumentationService().beast_spirit_quick_synthesis_snapshot() == expected
