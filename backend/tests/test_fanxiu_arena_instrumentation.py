from __future__ import annotations

from types import SimpleNamespace

from backend.core.fanxiu.instrumentation import arena
from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef


class _Reader:
    field_map: dict[int, dict] = {}
    list_map: dict[int, tuple[list, int]] = {}
    list_calls: list[int] = []

    def __init__(self, _memory) -> None:
        pass

    def fields(self, value):
        return self.field_map.get(value.address, {}) if isinstance(value, LuaRef) else {}

    def list_items(self, value):
        if isinstance(value, LuaRef):
            self.list_calls.append(value.address)
        return self.list_map.get(value.address, ([], 0))

    def long(self, value):
        return value if isinstance(value, int) else None


def _ref(address: int) -> LuaRef:
    return LuaRef("table", address)


def test_daofa_snapshot_excludes_the_client_inserted_self_row(monkeypatch) -> None:
    _Reader.field_map = {
        1: {"joinerVO": _ref(2)},
        2: {"rank": 20, "remainTimes": 3},
        10: {"isMySelf": False, "data": _ref(11)},
        11: {"id": 100, "rank": 19, "name": "target", "power": 800, "player": True},
        12: {"isMySelf": True, "data": _ref(13)},
        13: {"id": 200, "rank": 20, "name": "self", "power": 1000, "player": True},
    }
    _Reader.list_map = {3: ([_ref(10), _ref(12)], 2)}
    monkeypatch.setattr(arena, "LuaJitReader", _Reader)
    monkeypatch.setattr(
        arena,
        "_daofa_data_fields",
        lambda *_args: {"immortalRaceInfo": _ref(1), "ravalList": _ref(3)},
    )

    result = arena._daofa_snapshot(
        SimpleNamespace(pid=7, process_start_ticks=8),
        99,
        root_cache_hit=True,
    )

    assert result["complete"] is True
    assert result["self_power"] == 1000
    assert [target["name"] for target in result["targets"]] == ["target"]


def test_daofa_snapshot_falls_back_to_role_profile_when_self_row_is_absent(monkeypatch) -> None:
    _Reader.field_map = {
        1: {"joinerVO": _ref(2)},
        2: {"rank": 20, "remainTimes": 3},
        10: {"isMySelf": False, "data": _ref(11)},
        11: {"id": 100, "rank": 19, "name": "npc", "power": 0, "player": False},
    }
    _Reader.list_map = {3: ([_ref(10)], 1)}
    monkeypatch.setattr(arena, "LuaJitReader", _Reader)
    monkeypatch.setattr(
        arena,
        "_daofa_data_fields",
        lambda *_args: {"immortalRaceInfo": _ref(1), "ravalList": _ref(3)},
    )
    monkeypatch.setattr(
        arena,
        "read_role_profile_from_memory",
        lambda _memory: {"available": True, "battle_score": 1234},
    )

    result = arena._daofa_snapshot(
        SimpleNamespace(pid=7, process_start_ticks=8),
        99,
        root_cache_hit=True,
    )

    assert result["base_complete"] is True
    assert result["complete"] is True
    assert result["self_power"] == 1234
    assert result["self_power_source"] == "role_profile"
    assert result["targets"][0]["power"] == 0


def test_daofa_snapshot_reuses_job_self_power_without_reading_role_profile(monkeypatch) -> None:
    _Reader.field_map = {
        1: {"joinerVO": _ref(2)},
        2: {"rank": 20, "remainTimes": 3},
        10: {"isMySelf": False, "data": _ref(11)},
        11: {"id": 100, "rank": 19, "name": "npc", "power": 0, "player": False},
    }
    _Reader.list_map = {3: ([_ref(10)], 1)}
    monkeypatch.setattr(arena, "LuaJitReader", _Reader)
    monkeypatch.setattr(
        arena,
        "_daofa_data_fields",
        lambda *_args: {"immortalRaceInfo": _ref(1), "ravalList": _ref(3)},
    )
    monkeypatch.setattr(
        arena,
        "read_role_profile_from_memory",
        lambda _memory: (_ for _ in ()).throw(AssertionError("role profile must be reused")),
    )

    result = arena._daofa_snapshot(
        SimpleNamespace(pid=7, process_start_ticks=8),
        99,
        root_cache_hit=True,
        self_power_hint=1234,
    )

    assert result["complete"] is True
    assert result["self_power"] == 1234
    assert result["self_power_source"] == "job_cache"


def test_read_daofa_snapshot_uses_exact_lua_global(monkeypatch) -> None:
    memory = SimpleNamespace(pid=7, process_start_ticks=8)
    monkeypatch.setattr(
        arena.MumuProcessMemory,
        "discover",
        classmethod(lambda _cls: memory),
    )
    monkeypatch.setattr(arena, "_main_lua_state_address", lambda _memory: 123)
    monkeypatch.setattr(
        arena,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (99, True, 456),
    )
    monkeypatch.setattr(
        arena,
        "_daofa_snapshot",
        lambda *_args, **_kwargs: {
            "ok": True,
            "available": True,
            "complete": True,
            "targets": [{}],
            "evidence": {},
        },
    )

    result = arena.read_daofa_snapshot()

    assert result["complete"] is True
    assert result["evidence"]["manager_resolver"] == "lua_global"


def test_xianyuan_snapshot_reads_joiner_team_and_three_targets(monkeypatch) -> None:
    _Reader.field_map = {
        1: {
            "rank": 5,
            "current": 900,
            "remainChallengeTimes": 4,
            "remainRefreshTimes": 1,
            "teams": _ref(2),
        },
        3: {"type": 0, "power": 1200, "partnerIds": _ref(5), "teamDetail": _ref(6)},
    }
    _Reader.list_map = {
        5: ([16, 23, 9, 2, 1], 5),
        6: ([_ref(40 + index) for index in range(5)], 5),
    }
    for index, partner_id in enumerate([16, 23, 9, 2, 1]):
        _Reader.field_map[40 + index] = {"partnerId": partner_id, "fightPower": 100 + index}
    target_refs = []
    for index in range(3):
        target_address = 10 + index
        rank_address = 20 + index
        team_address = 30 + index
        target_refs.append(_ref(target_address))
        _Reader.field_map[target_address] = {
            "id": 100 + index,
            "player": True,
            "willScore": 10,
            "rankVO": _ref(rank_address),
            "teamVO": _ref(team_address),
        }
        _Reader.field_map[rank_address] = {
            "name": f"target-{index}",
            "server": 1,
            "score": 800 + index,
            "rank": 10 + index,
        }
        partner_ids_ref = 50 + index
        detail_ref = 60 + index
        _Reader.field_map[team_address] = {
            "power": 700 + index,
            "partnerIds": _ref(partner_ids_ref),
            "teamDetail": _ref(detail_ref),
        }
        ids = [1, 2, 3, 4, 5]
        detail_addresses = [100 + index * 10 + slot for slot in range(5)]
        _Reader.list_map[partner_ids_ref] = (ids, 5)
        _Reader.list_map[detail_ref] = ([_ref(address) for address in detail_addresses], 5)
        for address, partner_id in zip(detail_addresses, ids):
            _Reader.field_map[address] = {"partnerId": partner_id, "fightPower": 50}
    _Reader.list_map.update({2: ([_ref(3)], 1), 4: (target_refs, 3)})
    monkeypatch.setattr(arena, "LuaJitReader", _Reader)
    monkeypatch.setattr(
        arena,
        "_xianyuan_data_fields",
        lambda *_args: {"joinerVO": _ref(1), "targets": _ref(4)},
    )

    result = arena._xianyuan_snapshot(
        SimpleNamespace(pid=7, process_start_ticks=8),
        99,
        root_cache_hit=False,
    )

    assert result["complete"] is True
    assert result["self_power"] == 1200
    assert result["remaining_challenges"] == 4
    assert result["remaining_refreshes"] == 1
    assert len(result["targets"]) == 3
    assert result["self_team"]["partner_ids"] == [16, 23, 9, 2, 1]
    assert result["self_team"]["formation_complete"] is True
    assert result["targets"][0]["team"]["formation_complete"] is True


def test_xianyuan_summary_skips_partner_rows_but_keeps_power_facts(monkeypatch) -> None:
    _Reader.list_calls = []
    _Reader.field_map = {
        1: {
            "rank": 5,
            "current": 900,
            "remainChallengeTimes": 4,
            "remainRefreshTimes": 1,
            "teams": _ref(2),
        },
        3: {"type": 0, "power": 1200, "partnerIds": _ref(5), "teamDetail": _ref(6)},
    }
    target_refs = []
    for index in range(3):
        target_address = 10 + index
        rank_address = 20 + index
        team_address = 30 + index
        target_refs.append(_ref(target_address))
        _Reader.field_map[target_address] = {
            "id": 100 + index,
            "player": True,
            "willScore": 10,
            "rankVO": _ref(rank_address),
            "teamVO": _ref(team_address),
        }
        _Reader.field_map[rank_address] = {
            "name": f"target-{index}",
            "server": 1,
            "score": 800 + index,
            "rank": 10 + index,
        }
        _Reader.field_map[team_address] = {
            "power": 700 + index,
            "partnerIds": _ref(50 + index),
            "teamDetail": _ref(60 + index),
        }
    _Reader.list_map = {2: ([_ref(3)], 1), 4: (target_refs, 3)}
    monkeypatch.setattr(arena, "LuaJitReader", _Reader)
    monkeypatch.setattr(
        arena,
        "_xianyuan_data_fields",
        lambda *_args: {"joinerVO": _ref(1), "targets": _ref(4)},
    )

    result = arena._xianyuan_snapshot(
        SimpleNamespace(pid=7, process_start_ticks=8),
        99,
        root_cache_hit=True,
        include_formations=False,
    )

    assert result["complete"] is True
    assert result["self_power"] == 1200
    assert result["targets"][0]["team_power"] == 700
    assert result["self_team"]["formation_complete"] is False
    assert result["targets"][0]["team"]["formation_complete"] is False
    assert result["evidence"]["formations_included"] is False
    assert _Reader.list_calls == [4, 2]


def test_xianyuan_summary_reuses_job_self_power_without_reading_self_teams(monkeypatch) -> None:
    _Reader.list_calls = []
    _Reader.field_map = {
        1: {
            "rank": 5,
            "current": 900,
            "remainChallengeTimes": 4,
            "remainRefreshTimes": 1,
            "teams": _ref(2),
        },
    }
    target_refs = []
    for index in range(3):
        target_address = 10 + index
        rank_address = 20 + index
        team_address = 30 + index
        target_refs.append(_ref(target_address))
        _Reader.field_map[target_address] = {
            "id": 100 + index,
            "player": True,
            "willScore": 10,
            "rankVO": _ref(rank_address),
            "teamVO": _ref(team_address),
        }
        _Reader.field_map[rank_address] = {
            "name": f"target-{index}",
            "server": 1,
            "score": 800 + index,
            "rank": 10 + index,
        }
        _Reader.field_map[team_address] = {"power": 700 + index}
    _Reader.list_map = {4: (target_refs, 3)}
    monkeypatch.setattr(arena, "LuaJitReader", _Reader)
    monkeypatch.setattr(
        arena,
        "_xianyuan_data_fields",
        lambda *_args: {"joinerVO": _ref(1), "targets": _ref(4)},
    )

    result = arena._xianyuan_snapshot(
        SimpleNamespace(pid=7, process_start_ticks=8),
        99,
        root_cache_hit=True,
        include_formations=False,
        self_power_hint=1200,
    )

    assert result["complete"] is True
    assert result["self_power"] == 1200
    assert result["self_power_source"] == "job_cache"
    assert _Reader.list_calls == [4]


def test_read_xianyuan_snapshot_resolves_long_lived_manager(monkeypatch) -> None:
    memory = SimpleNamespace(pid=7, process_start_ticks=8)
    monkeypatch.setattr(
        arena.MumuProcessMemory,
        "discover",
        classmethod(lambda _cls: memory),
    )
    monkeypatch.setattr(
        arena,
        "_main_lua_state_address",
        lambda _memory: 123,
    )
    monkeypatch.setattr(
        arena,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (99, False, 456),
    )
    monkeypatch.setattr(
        arena,
        "_xianyuan_snapshot",
        lambda *_args, **_kwargs: {
            "ok": True,
            "available": True,
            "complete": True,
            "targets": [{}, {}, {}],
            "evidence": {"root_kind": "manager"},
        },
    )

    result = arena.read_xianyuan_duel_snapshot()

    assert result["complete"] is True
    assert result["evidence"]["root_kind"] == "manager"
    assert result["evidence"]["manager_resolver"] == "lua_global"
    assert result["elapsed_seconds"] >= 0


def test_xianyuan_loaded_global_does_not_heap_scan_when_page_data_is_missing(monkeypatch) -> None:
    memory = SimpleNamespace(pid=7, process_start_ticks=8)
    monkeypatch.setattr(arena, "_main_lua_state_address", lambda _memory: 123)

    def data_not_loaded(*_args, **_kwargs):
        raise arena.FanxiuRuntimeMemoryError(
            "page data missing",
            code="data_not_loaded",
        )

    monkeypatch.setattr(arena, "resolve_lua_global_manager_root", data_not_loaded)
    monkeypatch.setattr(
        arena,
        "resolve_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("loaded exact global must not fall back to heap scan")
        ),
    )

    try:
        arena._resolve_snapshot_root(
            memory,
            manager_key="xianyuan-duel-manager",
            marker=arena._XIANYUAN_MARKER,
            required_methods=arena._XIANYUAN_METHODS,
            validate=arena._xianyuan_data_fields,
            global_name="PartnerarenaMgr",
        )
    except arena.FanxiuRuntimeMemoryError as exc:
        assert exc.code == "data_not_loaded"
    else:
        raise AssertionError("data_not_loaded must remain fail-closed")
