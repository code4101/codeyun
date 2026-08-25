from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation import daozu_road
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    MumuProcessMemory,
)


class _FakeReader:
    def __init__(self, _memory: MumuProcessMemory) -> None:
        pass


def _memory() -> MumuProcessMemory:
    return MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )


def _loaded_fields(*, daily_limit=20, pass_count=7, challenge_num=3):
    return (
        {},
        {"challengeNum": challenge_num},
        {
            "current": 328,
            "challengeMax": 327,
            "passCount": pass_count,
            "level": 6,
            "yesterdayId": 320,
            "maxDayCount": daily_limit,
        },
    )


def test_daozu_road_snapshot_reads_complete_loaded_model(monkeypatch) -> None:
    monkeypatch.setattr(daozu_road, "LuaJitReader", _FakeReader)
    monkeypatch.setattr(
        daozu_road,
        "_daozu_road_loaded_fields",
        lambda _reader, _root: _loaded_fields(),
    )

    result = daozu_road._snapshot(
        _memory(),
        0x2000,
        root_cache_hit=True,
    )

    assert result["ok"] is True
    assert result["available"] is True
    assert result["complete"] is True
    assert result["protocol"] == "DaozuroadMgr.Model.DaozuroadData"
    assert result["current_level_id"] == 328
    assert result["challenge_max_level_id"] == 327
    assert result["daily_pass_count"] == 7
    assert result["daily_limit"] == 20
    assert result["daily_remaining"] == 13
    assert result["dao_level"] == 6
    assert result["yesterday_level_id"] == 320
    assert result["chain_pass_count"] == 3
    assert result["evidence"]["root_cache_hit"] is True


def test_daozu_road_snapshot_keeps_sync_fact_when_limit_cache_is_absent(
    monkeypatch,
) -> None:
    monkeypatch.setattr(daozu_road, "LuaJitReader", _FakeReader)
    monkeypatch.setattr(
        daozu_road,
        "_daozu_road_loaded_fields",
        lambda _reader, _root: _loaded_fields(daily_limit=None),
    )

    result = daozu_road._snapshot(
        _memory(),
        0x2000,
        root_cache_hit=False,
    )

    assert result["ok"] is False
    assert result["available"] is True
    assert result["complete"] is False
    assert result["daily_pass_count"] == 7
    assert result["daily_limit"] is None
    assert result["daily_remaining"] is None


@pytest.mark.parametrize(
    ("overrides", "reason"),
    (
        ({"current": 327}, "current/challengeMax"),
        ({"passCount": -1}, "同步字段"),
        ({"level": 0}, "同步字段"),
        ({"maxDayCount": 6}, "超过每日上限"),
    ),
)
def test_daozu_road_snapshot_rejects_invalid_state(
    monkeypatch,
    overrides,
    reason,
) -> None:
    monkeypatch.setattr(daozu_road, "LuaJitReader", _FakeReader)
    instance, model, data = _loaded_fields()
    data.update(overrides)
    monkeypatch.setattr(
        daozu_road,
        "_daozu_road_loaded_fields",
        lambda _reader, _root: (instance, model, data),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match=reason):
        daozu_road._snapshot(
            _memory(),
            0x2000,
            root_cache_hit=False,
        )


def test_daozu_road_reader_fails_closed_when_memory_is_unavailable(
    monkeypatch,
) -> None:
    def unavailable():
        raise FanxiuRuntimeMemoryError("测试：游戏进程不可读")

    monkeypatch.setattr(
        daozu_road.MumuProcessMemory,
        "discover_cached",
        unavailable,
    )

    result = daozu_road.read_daozu_road_snapshot()

    assert result["ok"] is False
    assert result["available"] is False
    assert result["complete"] is False
    assert result["reason"] == "测试：游戏进程不可读"
    assert result["evidence"]["pid"] is None


def test_daozu_road_resolver_prefers_exact_lua_global(monkeypatch) -> None:
    memory = _memory()
    captured = {}
    monkeypatch.setattr(
        daozu_road,
        "_lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )

    def resolve_global(_memory, **kwargs):
        captured.update(kwargs)
        return 0x2000, True, 0x3000

    monkeypatch.setattr(
        daozu_road,
        "resolve_lua_global_manager_root",
        resolve_global,
    )
    monkeypatch.setattr(
        daozu_road,
        "resolve_manager_root",
        lambda *_args, **_kwargs: pytest.fail("global success must not scan marker"),
    )

    root, cache_hit, resolver, timings = daozu_road._resolve_daozu_road_root(memory)

    assert root == 0x2000
    assert cache_hit is True
    assert resolver == "lua_global"
    assert timings["lua_state_seconds"] >= 0
    assert timings["manager_resolution_seconds"] >= 0
    assert captured["manager_key"] == "daozu-road"
    assert captured["state_address"] == 0x1234
    assert captured["global_name"] == "DaozuroadMgr"
    assert captured["required_methods"] == frozenset(
        {"LuaDaozuroadMgr", "Inst_get", "GetDayRemainCount"}
    )
    assert callable(captured["validate"])


def test_daozu_road_resolver_falls_back_to_constructor_marker(monkeypatch) -> None:
    memory = _memory()
    captured = {}
    monkeypatch.setattr(
        daozu_road,
        "_lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        daozu_road,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("global unavailable", code="manager_not_found")
        ),
    )

    def resolve_marker(_memory, **kwargs):
        captured.update(kwargs)
        return 0x4000, False

    monkeypatch.setattr(daozu_road, "resolve_manager_root", resolve_marker)

    root, cache_hit, resolver, _timings = daozu_road._resolve_daozu_road_root(memory)

    assert root == 0x4000
    assert cache_hit is False
    assert resolver == "constructor_marker"
    assert captured["marker"] == b"LuaDaozuroadMgr"


def test_daozu_road_resolver_preserves_data_not_loaded(monkeypatch) -> None:
    memory = _memory()
    monkeypatch.setattr(
        daozu_road,
        "_lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        daozu_road,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("model not loaded", code="data_not_loaded")
        ),
    )
    monkeypatch.setattr(
        daozu_road,
        "resolve_manager_root",
        lambda *_args, **_kwargs: pytest.fail("data-not-loaded must not scan marker"),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="model not loaded"):
        daozu_road._resolve_daozu_road_root(memory)
