from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_lundao_snapshot_normalizes_runtime_model(monkeypatch) -> None:
    from backend.core.fanxiu.instrumentation import lundao

    class Reader:
        def __init__(self, _memory):
            pass

        def fields(self, value):
            return value if isinstance(value, dict) else {}

        def list_items(self, value):
            return list(value), len(value)

        def long(self, value):
            return value if isinstance(value, int) else None

    monkeypatch.setattr(lundao, "LuaJitReader", Reader)
    monkeypatch.setattr(lundao.time, "time", lambda: 1_600.0)
    monkeypatch.setattr(
        lundao,
        "read_role_profile_from_memory",
        lambda _memory: {
            "ok": True,
            "available": True,
            "role_id": 42,
            "name": "自己",
            "battle_score": 1234.5,
            "faze": 0,
            "source": "runtime_memory",
        },
    )
    monkeypatch.setattr(
        lundao,
        "_data_fields",
        lambda _reader, _root: {
            "leftListenTime": 21_600_000,
            "maxLunDaoTime": 21_600,
            "strength": 2,
            "myRoomId": 15,
            "seatId": 7,
            "roomList": [
                {"id": 15, "left": 3, "themeId": 12, "npcId": 10034},
                {"id": 14, "left": 0, "themeId": 14, "npcId": 10111},
            ],
            "roleInfo": {
                "roomId": 15,
                "seatId": 7,
                "leftListenTime": 3_600_000,
                "sitDownTime": 1_000_000,
            },
        },
    )

    result = lundao._snapshot(
        SimpleNamespace(pid=42, process_start_ticks=9),
        0x1234,
        root_cache_hit=True,
    )

    assert result["complete"] is True
    assert result["remaining_milliseconds"] == 3_600_000
    assert result["current_left_listen_time"] == 3_000_000
    assert result["completed"] is False
    assert result["room_id"] == 15
    assert result["seat_id"] == 7
    assert result["seated"] is True
    assert result["room_available_counts"] == {"15": 3, "14": 0}
    assert result["maximum_milliseconds"] == 21_600_000
    assert result["self_profile"]["role_id"] == 42
    assert result["self_profile"]["battle_score"] == 1234.5
    assert result["captured_at_epoch"] == 1_600.0
    assert result["daluo_roster"]["evidence"] == {
        "pid": 42,
        "process_start_ticks": 9,
        "captured_at_epoch": 1_600.0,
        "order_key": [1_600.0],
    }

    monkeypatch.setattr(lundao.time, "time", lambda: 5_000.0)
    completed = lundao._snapshot(
        SimpleNamespace(pid=42, process_start_ticks=9),
        0x1234,
        root_cache_hit=True,
    )
    assert completed["current_left_listen_time"] == 0
    assert completed["completed"] is True
    assert completed["daluo_roster"]["evidence"]["order_key"] > result["daluo_roster"]["evidence"]["order_key"]


def _install_lundao_snapshot_stubs(monkeypatch) -> None:
    from backend.core.fanxiu.instrumentation import lundao

    class Reader:
        def __init__(self, _memory):
            pass

        def fields(self, value):
            return value if isinstance(value, dict) else {}

        def list_items(self, value):
            return list(value), len(value)

        def long(self, value):
            return value if isinstance(value, int) else None

    monkeypatch.setattr(lundao, "LuaJitReader", Reader)
    monkeypatch.setattr(lundao.time, "time", lambda: 1_600.0)
    monkeypatch.setattr(
        lundao,
        "_data_fields",
        lambda _reader, _root: {
            "leftListenTime": 21_600_000,
            "maxLunDaoTime": 21_600,
            "strength": 2,
            "myRoomId": 15,
            "seatId": 7,
            "roomList": [{"id": 15, "left": 3, "themeId": 12, "npcId": 10034}],
            "roleInfo": {
                "roomId": 15,
                "seatId": 7,
                "leftListenTime": 3_600_000,
                "sitDownTime": 1_000_000,
            },
        },
    )
    monkeypatch.setattr(
        lundao,
        "self_seat_facts",
        lambda _reader, _data: {"available": True, "seat": {"seat_id": 7}},
    )


def test_lundao_snapshot_prefers_current_role_faze_and_keeps_seat_metadata(monkeypatch) -> None:
    from backend.core.fanxiu.instrumentation import lundao

    _install_lundao_snapshot_stubs(monkeypatch)
    monkeypatch.setattr(
        lundao,
        "self_profile_from_seat",
        lambda _self_seat: {
            "ok": True,
            "available": True,
            "role_id": 42,
            "name": "座位快照",
            "faze": 0,
            "level": 240,
            "seat_id": 7,
            "battle_score": 100,
            "source": "runtime_memory",
        },
    )
    monkeypatch.setattr(
        lundao,
        "read_role_profile_from_memory",
        lambda _memory: {
            "ok": True,
            "available": True,
            "role_id": 42,
            "name": "自己",
            "battle_score": 1234.5,
            "faze": 10050,
            "source": "runtime_memory",
        },
    )

    result = lundao._snapshot(
        SimpleNamespace(pid=42, process_start_ticks=9),
        0x1234,
        root_cache_hit=True,
    )

    assert result["self_profile"]["faze"] == 10050
    assert result["self_profile"]["role_id"] == 42
    assert result["self_profile"]["battle_score"] == 1234.5
    assert result["self_profile"]["level"] == 240
    assert result["self_profile"]["seat_id"] == 7


def test_lundao_snapshot_fails_closed_when_seat_and_role_identity_conflict(monkeypatch) -> None:
    from backend.core.fanxiu.instrumentation import lundao

    _install_lundao_snapshot_stubs(monkeypatch)
    monkeypatch.setattr(
        lundao,
        "self_profile_from_seat",
        lambda _self_seat: {
            "ok": True,
            "available": True,
            "role_id": 42,
            "faze": 0,
        },
    )
    monkeypatch.setattr(
        lundao,
        "read_role_profile_from_memory",
        lambda _memory: {
            "ok": True,
            "available": True,
            "role_id": 99,
            "battle_score": 1.0,
            "faze": 10050,
        },
    )

    with pytest.raises(
        lundao.FanxiuRuntimeMemoryError,
        match="角色与当前 RoleMgr 身份不一致",
    ):
        lundao._snapshot(
            SimpleNamespace(pid=42, process_start_ticks=9),
            0x1234,
            root_cache_hit=True,
        )
