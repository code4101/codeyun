from __future__ import annotations

from types import SimpleNamespace


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
