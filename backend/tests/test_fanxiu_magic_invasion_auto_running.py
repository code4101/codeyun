from __future__ import annotations

from backend.core.fanxiu.instrumentation import magic_invasion_auto_running as runtime
from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef


class _Reader:
    def __init__(self) -> None:
        self.tables = {
            0x100: {"inst": LuaRef("table", 0x101)},
            0x101: {
                "hadAutoTimes": 34,
                "autoSetTimes": 100,
                "Model": LuaRef("table", 0x102),
            },
            0x102: {"MagicinvadeData": LuaRef("table", 0x103)},
            0x103: {
                "isInAuto": True,
                "MagicEventsList": LuaRef("table", 0x20),
                "V_MagicInvadeInfo": LuaRef("table", 0x104),
            },
            0x104: {
                "events": LuaRef("table", 0x30),
                "counts": LuaRef("table", 0x31),
            },
            0x201: {"configData": LuaRef("table", 0x211)},
            0x202: {"configData": LuaRef("table", 0x212)},
            0x211: {"autoType": 4},
            0x212: {"autoType": 1},
            0x220: {"current": 2},
        }

    def fields(self, value):
        if isinstance(value, LuaRef):
            return dict(self.tables.get(value.address, {}))
        return {}

    def list_items(self, value):
        rows = {
            0x10: [7, 4, 5, 6, 8, 9, 11],
            0x11: [4101, 4102],
            0x20: [LuaRef("table", 0x201), LuaRef("table", 0x202)],
        }[value.address]
        return list(rows), len(rows)

    def dictionary_fields(self, value):
        if isinstance(value, LuaRef) and value.address == 0x30:
            return {701: LuaRef("table", 0x401), 702: LuaRef("table", 0x402)}
        if isinstance(value, LuaRef) and value.address == 0x31:
            return {2: LuaRef("table", 0x220)}
        return {}


def _field(_address, name):
    return {
        "timeCount": 0,
        "timeCountMax": 10,
        "NeedJoinTeam": True,
        "CanJoinTeam": True,
        "isSendTeamList": True,
        "isSendUseItemMsg": False,
        "needDelayCloseReward": 0,
        "needAutoUseItem": True,
        "needAutoTenKill": False,
        "needAutoHundredKill": False,
        "TargetTeamList": LuaRef("table", 0x11),
        "selectList": LuaRef("table", 0x10),
    }.get(name)


def test_decode_running_snapshot_exposes_stuck_latches_and_event_supply(monkeypatch):
    reader = _Reader()
    monkeypatch.setattr(
        runtime,
        "manager_index_fields",
        lambda _reader, root, _methods: reader.tables[root],
    )

    result = runtime._decode_running_state(reader, 0x699, 0x100, field=_field)

    assert result["panel"] == {
        "time_count": 0,
        "time_count_max": 10,
        "NeedJoinTeam": True,
        "CanJoinTeam": True,
        "isSendTeamList": True,
        "isSendUseItemMsg": False,
        "needAutoUseItem": True,
        "needAutoTenKill": False,
        "needAutoHundredKill": False,
        "needDelayCloseReward": 0,
        "target_team_count": 2,
        "selected_types": [4, 5, 6, 7, 8, 9, 11],
    }
    assert result["manager"] == {
        "had_auto_times": 34,
        "set_auto_times": 100,
        "is_in_auto": True,
        "challenge_server_count": 2,
        "raw_event_count": 2,
        "cached_available_event_count": 2,
        "cached_matching_event_count": 1,
        "cached_event_auto_types": [4, 1],
    }
    assert result["blocker_candidates"] == [
        "join_required_but_team_request_latched"
    ]
