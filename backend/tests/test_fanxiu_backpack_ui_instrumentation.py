from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.backpack_ui import (
    _decode_panel,
    _select_unique_panel,
    backpack_ui_snapshot_fingerprint,
    locate_backpack_ui_items,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
)


def _reader(*, include_materialized=True, missing_item_key=False):
    reader = object.__new__(LuaJitReader)
    fields_by_address = {
        2: {"id": 101, "param": 3, "name": "材料"},
        4: {"_dt_": LuaRef("table", 40), "count": 4},
        5: {"id": 9001, "baseId": 1001, "num": 2, "endTime": 0, "ext": "a"},
        6: {"id": 9002, "baseId": 1001, "num": 7, "endTime": 123, "ext": "b"},
        12: {"id": 9002},
    }
    reader.fields = lambda value: fields_by_address.get(
        getattr(value, "address", -1), {}
    )

    def table(address):
        if address == 40:
            return {
                "array": [
                    None,
                    LuaRef("table", 5),
                    None if missing_item_key else False,
                    LuaRef("table", 6),
                    False,
                ],
                "fields": {},
            }
        if address == 10:
            return {"array": [], "fields": {77: LuaRef("table", 11)}}
        raise AssertionError(address)

    def string_fields(address, names):
        assert address == 11
        return {"itemvo": LuaRef("table", 12), "root": LuaRef("table", 13)}

    reader.table = table
    reader.string_fields = string_fields
    reader.long = lambda value: None
    reader._table_cache = {}
    reader._test_include_materialized = include_materialized
    return reader


def _field(address, name):
    return {
        (1, "tablo"): LuaRef("table", 2),
        (1, "isShow"): True,
        (1, "tabNum"): 3,
        (1, "ItemListScroll"): LuaRef("table", 3),
        (3, "ItemInfoList"): LuaRef("table", 4),
        (3, "ItemClassDic"): LuaRef("table", 9),
        (9, "_dt_"): LuaRef("table", 10),
    }.get((address, name))


def test_snapshot_fingerprint_tracks_ordered_inventory_but_ignores_timings() -> None:
    snapshot = {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "tab": {"id": 1},
        "panel_address": "0x1",
        "evidence": {"pid": 3, "process_start_ticks": 4},
        "items": [
            {"ui_index": 0, "instance_id": "a", "base_id": 10, "num": 2},
            {"ui_index": 1, "instance_id": "b", "base_id": 20, "num": 3},
        ],
        "performance": {"runtime": 1.0},
    }
    first = backpack_ui_snapshot_fingerprint(snapshot)
    snapshot["performance"] = {"runtime": 99.0}
    assert backpack_ui_snapshot_fingerprint(snapshot) == first
    snapshot["items"][0]["num"] = 1
    assert backpack_ui_snapshot_fingerprint(snapshot) != first


def test_panel_snapshot_preserves_ui_order_padding_and_materialized_binding():
    result = _decode_panel(_reader(), LuaRef("table", 1), field=_field)

    assert result["tab"] == {"id": 101, "param": 3, "number": 3, "label": "材料"}
    assert [item["ui_index"] for item in result["items"]] == [0, 1, 2, 3]
    assert [item["is_padding"] for item in result["items"]] == [False, True, False, True]
    assert result["items"][0] == {
        "ui_index": 0,
        "is_padding": False,
        "instance_id": "9001",
        "base_id": 1001,
        "num": 2,
        "end_time": 0,
        "ext": "a",
    }
    assert result["materialized_bindings"] == [{
        "instance_id": "9002",
        "root_address": "0xd",
        "item_instance_address": "0xb",
    }]
    assert result["materialized_complete"] is True
    assert result["materialized_available"] is True
    assert result["materialized_state"] == "complete"


def test_locate_filters_without_aggregation_or_reordering():
    snapshot = {
        "complete": True,
        "items": [
            {"ui_index": 0, "is_padding": False, "instance_id": "a", "base_id": 7},
            {"ui_index": 1, "is_padding": True, "instance_id": None, "base_id": None},
            {"ui_index": 2, "is_padding": False, "instance_id": "b", "base_id": 7},
        ],
    }
    assert [item["instance_id"] for item in locate_backpack_ui_items(snapshot, base_id=7)] == ["a", "b"]
    assert locate_backpack_ui_items(snapshot, instance_id="b", base_id=7)[0]["ui_index"] == 2


def test_locate_rejects_incomplete_snapshot_and_empty_query():
    with pytest.raises(FanxiuRuntimeMemoryError, match="未完整加载"):
        locate_backpack_ui_items({"complete": False}, base_id=1)
    with pytest.raises(ValueError, match="至少提供一个"):
        locate_backpack_ui_items({"complete": True, "items": []})


def test_panel_decoder_fails_closed_on_tab_mismatch_or_missing_item_fields():
    def mismatch(address, name):
        return 0 if (address, name) == (1, "tabNum") else _field(address, name)

    with pytest.raises(FanxiuRuntimeMemoryError, match="tab 参数"):
        _decode_panel(_reader(), LuaRef("table", 1), field=mismatch)

    missing = _reader()
    original_fields = missing.fields
    missing.fields = lambda value: (
        {"id": 9001, "baseId": 1001}
        if getattr(value, "address", -1) == 5
        else original_fields(value)
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="id/baseId/num"):
        _decode_panel(missing, LuaRef("table", 1), field=_field)


def test_panel_decoder_rejects_closed_panel_and_missing_declared_clist_key():
    def closed(address, name):
        return False if (address, name) == (1, "isShow") else _field(address, name)

    with pytest.raises(FanxiuRuntimeMemoryError, match="isShow=true"):
        _decode_panel(_reader(), LuaRef("table", 1), field=closed)
    with pytest.raises(FanxiuRuntimeMemoryError, match="非尾部缺失槽位：2"):
        _decode_panel(_reader(missing_item_key=True), LuaRef("table", 1), field=_field)


def test_panel_decoder_accepts_only_a_contiguous_unmaterialized_tail():
    reader = _reader()
    original_table = reader.table
    reader.table = lambda address: (
        {
            "array": [None, LuaRef("table", 5), LuaRef("table", 6), None, None],
            "fields": {},
        }
        if address == 40
        else original_table(address)
    )

    result = _decode_panel(reader, LuaRef("table", 1), field=_field)

    assert [item["ui_index"] for item in result["items"]] == [0, 1]
    assert result["declared_slot_count"] == 4
    assert result["trailing_missing_indices"] == [3, 4]


def test_panel_decoder_rejects_unknown_scalar_as_padding():
    reader = _reader()
    original_table = reader.table
    reader.table = lambda address: (
        {"array": [None, LuaRef("table", 5), 17, LuaRef("table", 6), False], "fields": {}}
        if address == 40
        else original_table(address)
    )
    with pytest.raises(FanxiuRuntimeMemoryError, match="未知标量"):
        _decode_panel(reader, LuaRef("table", 1), field=_field)


def test_materialized_dictionary_not_loaded_is_explicitly_unavailable():
    def no_dictionary(address, name):
        if (address, name) in {(3, "ItemClassDic"), (9, "_dt_")}:
            return None
        return _field(address, name)

    result = _decode_panel(_reader(), LuaRef("table", 1), field=no_dictionary)
    assert result["materialized_bindings"] == []
    assert result["materialized_available"] is False
    assert result["materialized_complete"] is False
    assert result["materialized_state"] == "not_loaded"


def test_panel_selection_rejects_not_loaded_and_multiple_cached_panels():
    with pytest.raises(FanxiuRuntimeMemoryError, match="NotLoaded"):
        _select_unique_panel({})
    with pytest.raises(FanxiuRuntimeMemoryError, match="同时发现 2 个"):
        _select_unique_panel({1: {"tab": 1}, 2: {"tab": 2}})
