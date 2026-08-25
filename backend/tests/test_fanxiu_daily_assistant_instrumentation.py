from __future__ import annotations

from backend.core.fanxiu.instrumentation import daily_assistant
from backend.core.fanxiu.instrumentation.runtime_memory import (
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
)


def test_daily_assistant_snapshot_reads_exact_result_modules(
    monkeypatch,
):
    submodule_groups = LuaRef("table", 0x1000)
    group = LuaRef("table", 0x1100)
    practice = LuaRef("table", 0x1200)
    travel = LuaRef("table", 0x1300)
    selected = LuaRef("table", 0x1400)
    shown = LuaRef("table", 0x1500)
    unlocked = LuaRef("table", 0x1600)
    results = LuaRef("table", 0x1700)
    execute_counts = LuaRef("table", 0x1800)
    monkeypatch.setattr(
        daily_assistant,
        "_daily_helper_fields",
        lambda _reader, _root: {
            "_serverNewDailyHelperId": "101",
            "subModuleIdDic": submodule_groups,
            "selectNewDailyHelperModuleIds": selected,
            "showNewDailyHelperModuleIds": shown,
            "unlockNewDailyHelperSubModuleIds": unlocked,
            "resSubModuleList": results,
            "moduleExecuteCountDic": execute_counts,
        },
    )

    def fake_fields(_reader, value):
        if value == submodule_groups:
            return {101200.0: group}
        if value == execute_counts:
            return {1012001.0: 3.0}
        return {}

    def fake_list_items(_reader, value):
        return {
            group: ([practice, travel], 2),
            selected: ([101200.0], 1),
            shown: ([101200.0], 1),
            unlocked: ([1012001.0, 1001002.0], 2),
            results: ([practice], 1),
        }.get(value, ([], None))

    def fake_table(_reader, address):
        arrays = {
            practice.address: [
                None,
                1012001,
                101200,
                "双人修炼",
                "PracticeTogether",
            ],
            travel.address: [
                None,
                1001002,
                100100,
                "修仙传游历",
                "Travel",
            ],
        }
        return {"array": arrays[address]}

    monkeypatch.setattr(LuaJitReader, "fields", fake_fields)
    monkeypatch.setattr(LuaJitReader, "list_items", fake_list_items)
    monkeypatch.setattr(LuaJitReader, "table", fake_table)
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = daily_assistant._snapshot(
        memory,
        0x2000,
        root_cache_hit=False,
    )

    assert result["ok"] is True
    assert result["server_helper_id"] == "101"
    assert result["selected_module_ids"] == [101200]
    assert result["unlocked_submodule_ids"] == [1012001, 1001002]
    assert result["result_submodules"] == [
        {
            "submodule_id": 1012001,
            "module_id": 101200,
            "name": "双人修炼",
            "function_type": "PracticeTogether",
            "execute_count": 3,
        }
    ]
    assert result["result_fingerprint"] == [[1012001, 3]]


def test_daily_assistant_snapshot_rejects_truncated_result_list(
    monkeypatch,
):
    submodule_groups = LuaRef("table", 0x1000)
    group = LuaRef("table", 0x1100)
    practice = LuaRef("table", 0x1200)
    selected = LuaRef("table", 0x1400)
    shown = LuaRef("table", 0x1500)
    results = LuaRef("table", 0x1700)
    execute_counts = LuaRef("table", 0x1800)
    monkeypatch.setattr(
        daily_assistant,
        "_daily_helper_fields",
        lambda _reader, _root: {
            "subModuleIdDic": submodule_groups,
            "selectNewDailyHelperModuleIds": selected,
            "showNewDailyHelperModuleIds": shown,
            "unlockNewDailyHelperSubModuleIds": None,
            "resSubModuleList": results,
            "moduleExecuteCountDic": execute_counts,
        },
    )
    monkeypatch.setattr(
        LuaJitReader,
        "fields",
        lambda _reader, value: (
            {101200.0: group}
            if value == submodule_groups
            else {}
        ),
    )
    monkeypatch.setattr(
        LuaJitReader,
        "list_items",
        lambda _reader, value: {
            group: ([practice], 1),
            selected: ([101200.0], 1),
            shown: ([101200.0], 1),
            results: ([practice], 2),
        }.get(value, ([], None)),
    )
    monkeypatch.setattr(
        LuaJitReader,
        "table",
        lambda _reader, _address: {
            "array": [
                None,
                1012001,
                101200,
                "双人修炼",
                "PracticeTogether",
            ]
        },
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = daily_assistant._snapshot(
        memory,
        0x2000,
        root_cache_hit=True,
    )

    assert result["ok"] is False
    assert result["result_submodule_count"] == 2
    assert len(result["result_submodules"]) == 1
