from __future__ import annotations

from typing import Any

from backend.core.fanxiu.instrumentation import xianfu_building
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
    MumuProcessMemory,
)


class FakeReader:
    def __init__(
        self,
        *,
        fields: dict[LuaRef, dict[Any, Any]],
        dictionaries: dict[LuaRef, dict[Any, Any]],
        lists: dict[LuaRef, tuple[list[Any], int | None]],
        longs: dict[LuaRef, int] | None = None,
    ) -> None:
        self._fields = fields
        self._dictionaries = dictionaries
        self._lists = lists
        self._longs = longs or {}

    def fields(self, value: Any) -> dict[Any, Any]:
        return self._fields.get(value, {})

    def dictionary_fields(self, value: Any) -> dict[Any, Any]:
        return self._dictionaries.get(value, {})

    def list_items(self, value: Any) -> tuple[list[Any], int | None]:
        return self._lists.get(value, ([], None))

    def long(self, value: Any) -> int | None:
        return self._longs.get(value)


def _memory() -> MumuProcessMemory:
    return MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )


def test_xianfu_building_snapshot_treats_empty_constructed_data_as_not_loaded(
    monkeypatch,
):
    building_dic = LuaRef("table", 0x1000)
    suits = LuaRef("table", 0x1100)
    reader = FakeReader(
        fields={},
        dictionaries={building_dic: {}},
        lists={suits: ([], 0)},
    )
    monkeypatch.setattr(xianfu_building, "LuaJitReader", lambda _memory: reader)
    monkeypatch.setattr(
        xianfu_building,
        "_building_data_fields",
        lambda _reader, _root: {
            "buildingInfoDic": building_dic,
            "suits": suits,
        },
    )

    try:
        xianfu_building._snapshot(
            _memory(),
            0x2000,
            root_cache_hit=False,
        )
    except FanxiuRuntimeMemoryError as exc:
        assert exc.code == "data_not_loaded"
        assert "尚未同步" in str(exc)
    else:
        raise AssertionError("空 buildingInfoDic/suits 不得伪装成 loaded_empty")


def test_xianfu_building_snapshot_normalizes_full_model(monkeypatch):
    building_dic = LuaRef("table", 0x1000)
    suits = LuaRef("table", 0x1100)
    building = LuaRef("table", 0x1200)
    partners = LuaRef("table", 0x1300)
    partner = LuaRef("table", 0x1400)
    items = LuaRef("table", 0x1500)
    science = LuaRef("table", 0x1600)
    golden = LuaRef("table", 0x1700)
    golden_attr = LuaRef("table", 0x1800)
    golden_calculated = LuaRef("table", 0x1900)
    building_end = LuaRef("table", 0x2000)
    partner_end = LuaRef("table", 0x2100)
    can_rec_exp = LuaRef("table", 0x2200)
    reader = FakeReader(
        fields={
            building: {
                "type": 3.0,
                "level": 8.0,
                "level2": 2.0,
                "jie": 1.0,
                "partners": partners,
                "endTime": building_end,
                "items": items,
                "scienceMap": science,
            },
            partner: {"grid": 2.0, "partner": 998.0, "endTime": partner_end},
            golden: {
                "attr": golden_attr,
                "canRecExp": can_rec_exp,
                "calExpAttrMap": golden_calculated,
            },
        },
        dictionaries={
            building_dic: {3.0: building},
            items: {1001.0: 9.0},
            science: {7001.0: 4.0},
            golden_attr: {1.0: 33.0},
            golden_calculated: {2.0: 44.0},
        },
        lists={
            suits: ([12.0, 7.0], 2),
            partners: ([partner], 1),
        },
        longs={
            building_end: 1_700_000_000_000,
            partner_end: 1_700_000_100_000,
            can_rec_exp: 123_456_789,
        },
    )
    monkeypatch.setattr(xianfu_building, "LuaJitReader", lambda _memory: reader)
    monkeypatch.setattr(
        xianfu_building,
        "_building_data_fields",
        lambda _reader, _root: {
            "buildingInfoDic": building_dic,
            "suits": suits,
            "_goldenInfo": golden,
        },
    )

    result = xianfu_building._snapshot(
        _memory(),
        0x3000,
        root_cache_hit=True,
    )

    assert result["load_state"] == "loaded"
    assert result["buildings"] == [
        {
            "type": 3,
            "level": 8,
            "level2": 2,
            "jie": 1,
            "partners": [
                {
                    "grid": 2,
                    "partner": 998,
                    "end_time": 1_700_000_100_000,
                }
            ],
            "end_time": 1_700_000_000_000,
            "items": {"1001": 9},
            "science_map": {"7001": 4},
        }
    ]
    assert result["suits"] == [7, 12]
    assert result["golden_info"] == {
        "attr": {"1": 33},
        "can_rec_exp": 123_456_789,
        "cal_exp_attr_map": {"2": 44},
    }
    assert result["evidence"]["read_only"] is True


def test_xianfu_building_reader_reports_manager_unavailable(monkeypatch):
    monkeypatch.setattr(
        MumuProcessMemory,
        "discover_cached",
        classmethod(lambda _cls: _memory()),
    )
    monkeypatch.setattr(
        xianfu_building,
        "_resolve_building_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("BuildingMgr 尚未加载", code="manager_not_found")
        ),
    )

    result = xianfu_building.read_xianfu_building_snapshot()

    assert result["ok"] is False
    assert result["load_state"] == "manager_unavailable"
    assert result["manager_available"] is False
    assert result["data_available"] is False
    assert result["reason_code"] == "manager_not_found"


def test_xianfu_building_reader_reports_data_unavailable(monkeypatch):
    monkeypatch.setattr(
        MumuProcessMemory,
        "discover_cached",
        classmethod(lambda _cls: _memory()),
    )
    monkeypatch.setattr(
        xianfu_building,
        "_resolve_building_root",
        lambda *_args, **_kwargs: (0x2000, False, "lua_global"),
    )
    monkeypatch.setattr(
        xianfu_building,
        "_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            xianfu_building._BuildingDataUnavailable(
                "BuildingData 尚未初始化"
            )
        ),
    )

    result = xianfu_building.read_xianfu_building_snapshot()

    assert result["ok"] is False
    assert result["load_state"] == "data_not_loaded"
    assert result["manager_available"] is True
    assert result["data_available"] is False
    assert result["reason_code"] == "data_not_loaded"


def test_xianfu_building_reader_reports_loaded_data_as_invalid(monkeypatch):
    monkeypatch.setattr(
        MumuProcessMemory,
        "discover_cached",
        classmethod(lambda _cls: _memory()),
    )
    monkeypatch.setattr(
        xianfu_building,
        "_resolve_building_root",
        lambda *_args, **_kwargs: (0x2000, False, "lua_global"),
    )
    monkeypatch.setattr(
        xianfu_building,
        "_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            xianfu_building._BuildingDataInvalid("BuildingVO 字段不完整")
        ),
    )

    result = xianfu_building.read_xianfu_building_snapshot()

    assert result["ok"] is False
    assert result["load_state"] == "data_invalid"
    assert result["manager_available"] is True
    assert result["data_available"] is False
    assert result["reason_code"] == "snapshot_incomplete"


def test_xianfu_building_root_uses_cached_marker_without_heap_scan(monkeypatch):
    calls: list[tuple[str, bool | None]] = []
    monkeypatch.setattr(xianfu_building, "_main_lua_state_address", lambda _memory: 11)

    def fail_global(*_args, **_kwargs):
        calls.append(("global", None))
        raise FanxiuRuntimeMemoryError("global missing", code="manager_not_found")

    def fail_package(*_args, **_kwargs):
        calls.append(("package", None))
        raise FanxiuRuntimeMemoryError("package missing", code="manager_not_found")

    def cached_marker(*_args, **kwargs):
        calls.append(("marker", kwargs.get("allow_discovery")))
        return 0x2000, True

    monkeypatch.setattr(xianfu_building, "resolve_lua_global_manager_root", fail_global)
    monkeypatch.setattr(xianfu_building, "_package_loaded_building_root", fail_package)
    monkeypatch.setattr(xianfu_building, "resolve_manager_root", cached_marker)

    result = xianfu_building._resolve_building_root(
        _memory(),
        allow_diagnostic_discovery=False,
    )

    assert result == (0x2000, True, "constructor_marker_cache")
    assert calls == [("global", None), ("package", None), ("marker", False)]


def test_xianfu_building_root_heap_scan_requires_explicit_diagnostics(monkeypatch):
    monkeypatch.setattr(xianfu_building, "_main_lua_state_address", lambda _memory: 11)
    monkeypatch.setattr(
        xianfu_building,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("global missing", code="manager_not_found")
        ),
    )
    monkeypatch.setattr(
        xianfu_building,
        "_package_loaded_building_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("package missing", code="manager_not_found")
        ),
    )
    discovery_flags: list[bool] = []

    def marker(*_args, **kwargs):
        allow_discovery = bool(kwargs.get("allow_discovery"))
        discovery_flags.append(allow_discovery)
        if not allow_discovery:
            raise FanxiuRuntimeMemoryError("cache miss", code="root_cache_miss")
        return 0x3000, False

    monkeypatch.setattr(xianfu_building, "resolve_manager_root", marker)

    try:
        xianfu_building._resolve_building_root(
            _memory(),
            allow_diagnostic_discovery=False,
        )
    except FanxiuRuntimeMemoryError as exc:
        assert exc.code == "manager_not_found"
    else:
        raise AssertionError("普通读取不得进入 marker 全堆发现")
    assert discovery_flags == [False]

    result = xianfu_building._resolve_building_root(
        _memory(),
        allow_diagnostic_discovery=True,
    )
    assert result == (0x3000, False, "constructor_marker_diagnostic")
    assert discovery_flags == [False, False, True]


def test_xianfu_building_snapshot_rejects_incomplete_partner_list(monkeypatch):
    building_dic = LuaRef("table", 0x1000)
    suits = LuaRef("table", 0x1100)
    building = LuaRef("table", 0x1200)
    partners = LuaRef("table", 0x1300)
    items = LuaRef("table", 0x1400)
    science = LuaRef("table", 0x1500)
    reader = FakeReader(
        fields={
            building: {
                "type": 3.0,
                "level": 1.0,
                "level2": 0.0,
                "jie": 0.0,
                "partners": partners,
                "endTime": 0.0,
                "items": items,
                "scienceMap": science,
            }
        },
        dictionaries={building_dic: {3.0: building}, items: {}, science: {}},
        lists={suits: ([], 0), partners: ([], None)},
    )
    monkeypatch.setattr(xianfu_building, "LuaJitReader", lambda _memory: reader)
    monkeypatch.setattr(
        xianfu_building,
        "_building_data_fields",
        lambda _reader, _root: {
            "buildingInfoDic": building_dic,
            "suits": suits,
        },
    )

    try:
        xianfu_building._snapshot(
            _memory(),
            0x2000,
            root_cache_hit=False,
        )
    except FanxiuRuntimeMemoryError as exc:
        assert "partners" in str(exc)
    else:
        raise AssertionError("不完整的 partners CList 必须失败关闭")
