from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation import lingmai
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
)


def test_lingmai_snapshot_normalizes_runtime_model(monkeypatch):
    monkeypatch.setattr(lingmai.time, "time", lambda: 1_600.0)
    room_list = LuaRef("table", 0x1000)
    room = LuaRef("table", 0x1100)
    remaining = LuaRef("table", 0x1200)
    union_group = LuaRef("table", 0x1300)
    role_info = LuaRef("table", 0x1400)
    role_remaining = LuaRef("table", 0x1500)
    sit_down_time = LuaRef("table", 0x1600)
    data = {
        "myRoomId": 17.0,
        "seatId": 3.0,
        "leftListenTime": remaining,
        "strength": 812.0,
        "fightConsume": 150.0,
        "sitConsume": 150.0,
        "initialStrength": 720.0,
        "maxVenisTime": 10800.0,
        "UnionVeinsGroup": union_group,
        "roomList": room_list,
        "battleId": 1.0,
        "localServer": 22077.0,
        "roleInfo": role_info,
    }
    monkeypatch.setattr(lingmai, "_data_fields", lambda _reader, _root: data)
    monkeypatch.setattr(
        LuaJitReader,
        "list_items",
        lambda _reader, value: ([room], 1) if value == room_list else ([], 0),
    )

    def fake_fields(_reader, value):
        if value == room:
            return {
                "id": 17.0,
                "left": 5.0,
                "themeId": 2.0,
                "npcId": 9.0,
            }
        if value == role_info:
            return {
                "battleId": 1.0,
                "roomId": 17.0,
                "seatId": 3.0,
                "localServer": 22077.0,
                "leftListenTime": role_remaining,
                "sitDownTime": sit_down_time,
                "skillLv": 5.0,
            }
        return {}

    monkeypatch.setattr(LuaJitReader, "fields", fake_fields)
    monkeypatch.setattr(
        LuaJitReader,
        "long",
        lambda _reader, value: {
            remaining: 10_800_000,
            union_group: 24_077_380_502_945_993,
            role_remaining: 10_800_000,
            sit_down_time: 123456789,
        }.get(value),
    )
    memory = MumuProcessMemory(
        pid=2712,
        process_start_ticks=5278,
        adb_serial="test",
        regions=[],
    )

    result = lingmai._snapshot(
        memory,
        0x2000,
        root_cache_hit=False,
    )

    assert result["ok"] is True
    assert result["remaining_milliseconds"] == 10_800_000
    assert result["maximum_milliseconds"] == 10_800_000
    assert result["strength"] == 812
    assert result["fight_consume"] == 150
    assert result["sit_consume"] == 150
    assert result["initial_strength"] == 720
    assert result["union_group"] == 24_077_380_502_945_993
    assert result["own_room_id"] == 17
    assert result["own_seat_id"] == 3
    assert result["rooms"] == [
        {
            "id": 17,
            "available_count": 5,
            "theme_id": 2,
            "npc_id": 9,
        }
    ]
    assert result["role_info"]["sit_down_time"] == 123456789
    assert result["shengmai_roster"]["room_id"] == 18
    assert result["room_rosters"]["17"] == result["shenmai_roster"]
    assert result["room_rosters"]["18"] == result["shengmai_roster"]
    assert result["shenmai_roster"]["evidence"]["order_key"] == [1_600.0]


def test_lingmai_snapshot_reads_role_profile_when_player_is_not_seated(monkeypatch):
    monkeypatch.setattr(lingmai.time, "time", lambda: 1_600.0)
    room_list = LuaRef("table", 0x1000)
    room = LuaRef("table", 0x1100)
    remaining = LuaRef("table", 0x1200)
    union_group = LuaRef("table", 0x1300)
    data = {
        "myRoomId": 0.0,
        "seatId": 0.0,
        "leftListenTime": remaining,
        "strength": 812.0,
        "UnionVeinsGroup": union_group,
        "roomList": room_list,
        "roleInfo": None,
    }
    monkeypatch.setattr(lingmai, "_data_fields", lambda _reader, _root: data)
    monkeypatch.setattr(
        LuaJitReader,
        "list_items",
        lambda _reader, value: ([room], 1) if value == room_list else ([], 0),
    )
    monkeypatch.setattr(
        LuaJitReader,
        "fields",
        lambda _reader, value: (
            {"id": 17.0, "left": 1.0, "themeId": 2.0, "npcId": 9.0}
            if value == room
            else {}
        ),
    )
    monkeypatch.setattr(
        LuaJitReader,
        "long",
        lambda _reader, value: {
            remaining: 10_800_000,
            union_group: 24_077_380_502_945_993,
        }.get(value),
    )
    expected_profile = {
        "ok": True,
        "available": True,
        "role_id": 42,
        "battle_score": 1234.5,
        "source": "runtime_memory",
    }
    monkeypatch.setattr(
        lingmai,
        "read_role_profile_from_memory",
        lambda _memory: expected_profile,
    )
    memory = MumuProcessMemory(
        pid=2712,
        process_start_ticks=5278,
        adb_serial="test",
        regions=[],
    )

    result = lingmai._snapshot(memory, 0x2000, root_cache_hit=False)

    assert result["self_seat_facts"]["seated"] is False
    assert result["self_profile"] == expected_profile


def test_lingmai_resolves_loaded_global_before_marker_discovery(monkeypatch):
    memory = MumuProcessMemory(
        pid=2712,
        process_start_ticks=5278,
        adb_serial="test",
        regions=[],
    )
    calls = []
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.redbag_runtime_loader._lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        lingmai,
        "resolve_lua_global_manager_root",
        lambda _memory, **options: (
            calls.append(("global", options)) or (0x2000, False, 0x3000)
        ),
    )
    monkeypatch.setattr(
        lingmai,
        "resolve_manager_root",
        lambda *_args, **_kwargs: pytest.fail(
            "loaded UnionVenisMgr must not use marker discovery"
        ),
    )

    result = lingmai._resolve_union_venis_root(memory)

    assert result == (0x2000, False, "lua_global")
    assert calls[0][1]["global_name"] == "UnionVenisMgr"
    assert calls[0][1]["state_address"] == 0x1234


def test_lingmai_loaded_manager_data_gap_does_not_scan_marker(monkeypatch):
    memory = MumuProcessMemory(
        pid=2712,
        process_start_ticks=5278,
        adb_serial="test",
        regions=[],
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.redbag_runtime_loader._lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )

    def data_not_loaded(*_args, **_kwargs):
        raise FanxiuRuntimeMemoryError(
            "联盟灵脉 Runtime 模型尚未初始化",
            code="data_not_loaded",
        )

    monkeypatch.setattr(lingmai, "resolve_lua_global_manager_root", data_not_loaded)
    monkeypatch.setattr(
        lingmai,
        "resolve_manager_root",
        lambda *_args, **_kwargs: pytest.fail(
            "data-not-loaded must not trigger marker discovery"
        ),
    )

    with pytest.raises(FanxiuRuntimeMemoryError) as exc_info:
        lingmai._resolve_union_venis_root(memory)

    assert exc_info.value.code == "data_not_loaded"


class _GenerationMemory:
    def __init__(self, pid: int = 101, process_start_ticks: int = 202) -> None:
        self.pid = pid
        self.process_start_ticks = process_start_ticks


def test_lingmai_generation_retry_recovers_one_transient_decode(monkeypatch):
    memories = [_GenerationMemory(), _GenerationMemory()]
    monkeypatch.setattr(
        lingmai.MumuProcessMemory,
        "discover_cached",
        lambda: memories.pop(0),
    )
    calls = []

    def read_once(memory):
        calls.append(memory)
        if len(calls) == 1:
            raise FanxiuRuntimeMemoryError("Runtime 内存地址越界：0x1234+64")
        return {"ok": True}

    result, attempt_count, retried_error = (
        lingmai._read_lingmai_with_generation_retry(read_once)
    )

    assert result == {"ok": True}
    assert attempt_count == 2
    assert retried_error == "Runtime 内存地址越界：0x1234+64"
    assert calls[0] is not calls[1]


def test_lingmai_generation_retry_rejects_cross_process_snapshot(monkeypatch):
    memories = [_GenerationMemory(), _GenerationMemory(pid=303)]
    monkeypatch.setattr(
        lingmai.MumuProcessMemory,
        "discover_cached",
        lambda: memories.pop(0),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="拒绝拼接跨进程快照"):
        lingmai._read_lingmai_with_generation_retry(
            lambda _memory: (_ for _ in ()).throw(
                FanxiuRuntimeMemoryError("Runtime 内存地址越界：0x1234+64")
            )
        )


def test_lingmai_generation_retry_does_not_retry_non_transient(monkeypatch):
    calls = []
    monkeypatch.setattr(
        lingmai.MumuProcessMemory,
        "discover_cached",
        lambda: calls.append(True) or _GenerationMemory(),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="模型尚未初始化"):
        lingmai._read_lingmai_with_generation_retry(
            lambda _memory: (_ for _ in ()).throw(
                FanxiuRuntimeMemoryError("联盟灵脉 Runtime 模型尚未初始化")
            )
        )

    assert len(calls) == 1
