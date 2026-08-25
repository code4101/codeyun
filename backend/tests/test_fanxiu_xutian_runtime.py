from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)
from backend.core.fanxiu.instrumentation.xutian_runtime import (
    _decode_auto_settings,
    _decode_special_options,
)


class _Reader:
    def __init__(self, tables: dict[int, dict]) -> None:
        self.tables = tables

    def fields(self, value):
        if isinstance(value, LuaRef):
            return dict(self.tables[value.address].get("fields") or {})
        return dict(value or {})

    def table(self, address: int):
        return self.tables[address]

    def list_items(self, value):
        rows = list(self.tables[value.address].get("array") or [])
        return rows, len(rows)


def _settings_table(*, skip_animation: bool = True) -> tuple[_Reader, dict]:
    values = {
        3: False,
        4: False,
        5: True,
        6: True,
        7: True,
        8: True,
        9: True,
        10: 2025,
        14: True,
        15: False,
        16: skip_animation,
        99: False,
    }
    tables: dict[int, dict] = {}
    array = [None] * 17
    for key, value in values.items():
        address = 1000 + key
        tables[address] = {
            "fields": {
                "autoFight": value,
                "useItem": key == 6,
                "useItem3": key == 6,
                "useItem4": key == 6,
            },
            "array": [],
        }
        if key == 99:
            continue
        array[key] = LuaRef("table", address)
    tables[1] = {
        "fields": {99.0: LuaRef("table", 1099)},
        "array": array,
    }
    return _Reader(tables), {"_AutoFightData": LuaRef("table", 1)}


def test_decode_auto_settings_reads_numeric_array_and_hash_keys() -> None:
    reader, data = _settings_table()

    snapshot = _decode_auto_settings(reader, data)

    assert snapshot["values"] == {
        "quality_player": False,
        "quality_3": False,
        "quality_4": False,
        "quality_5": True,
        "quality_6": True,
        "quality_7": True,
        "refill_challenge": True,
        "refill_explore": True,
        "challenge_count": 2025,
        "quick_auto": True,
        "quality_8": False,
        "skip_animation": True,
    }
    assert snapshot["raw"]["6"] == {
        "auto_fight": True,
        "use_item": True,
        "use_item_2": None,
        "use_item_3": True,
        "use_item_4": True,
    }


def test_decode_auto_settings_fails_closed_on_incomplete_runtime() -> None:
    reader, data = _settings_table()
    reader.tables[1]["array"][16] = None

    with pytest.raises(FanxiuRuntimeMemoryError, match="skip_animation"):
        _decode_auto_settings(reader, data)


def test_special_item_selection_comes_from_heaven_info_checks() -> None:
    reader = _Reader({2: {"array": [30030010]}})
    snapshot = _decode_special_options(
        reader,
        {
            "_HeavenInfo": {"checks": LuaRef("table", 2)},
            "_HeightDetectItem": 30030010,
            "_HeightDetectItem2": 30030015,
            "_CanShowUseSpecialItem": True,
            "_CanShowUseSpecialItem2": False,
        },
    )

    assert snapshot["find_demon_selected"] is True
    assert snapshot["native_soul_lock_selected"] is False
    assert snapshot["selected_item_ids"] == [30030010]
