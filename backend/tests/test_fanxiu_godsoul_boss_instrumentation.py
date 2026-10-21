from backend.core.fanxiu.instrumentation import godsoul_boss
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


class _Memory:
    pid = 123
    process_start_ticks = 456


class _Reader:
    def __init__(self, _memory):
        self.values = {
            "sync": {
                "bossHpOfMapId": "hp",
                "selfPointOfMapId": "points",
                "selfRankOfMapId": "ranks",
            },
            "enter": {"code": 0, "mapId": 999303},
            "settle": {
                "code": 0,
                "mapId": 999303,
                "rank": 1,
                "totalDamage": 1_507_680.0,
                "desc": -1,
            },
        }
        self.dictionaries = {
            "hp": {999303: 0.0, 999304: 4_665_600_000.0},
            "points": {999303: "point-value"},
            "ranks": {999303: 20.0},
        }

    def fields(self, value):
        return self.values.get(value, {})

    def dictionary_fields(self, value):
        return self.dictionaries.get(value, {})

    def long(self, value):
        return 1_507_680 if value == "point-value" else None


def test_godsoul_challenge_snapshot_proves_entry_and_settlement(monkeypatch):
    monkeypatch.setattr(godsoul_boss, "LuaJitReader", _Reader)
    monkeypatch.setattr(
        godsoul_boss,
        "_godsoul_boss_data_fields",
        lambda _reader, _root: {
            "_GodSoulBossData": "sync",
            "_GodSoulBossEnter": "enter",
            "_GodSoulBossSettle": "settle",
        },
    )

    result = godsoul_boss._challenge_snapshot(
        _Memory(),
        0xABC,
        root_cache_hit=True,
    )

    assert result["complete"] is True
    assert result["entered_map_id"] == 999303
    assert result["settled"] is True
    assert result["settled_current_round"] is True
    assert result["current_round_participated"] is True
    assert result["settlement"]["total_damage"] == 1_507_680.0
    assert result["self_point_by_map_id"] == {999303: 1_507_680}
    assert result["self_rank_by_map_id"] == {999303: 20.0}
    assert result["evidence"] == {
        "pid": 123,
        "process_start_ticks": 456,
        "root_address": "0xabc",
        "root_cache_hit": True,
    }


def _round_snapshot(points, *, complete=True, settled=True):
    return {
        "complete": complete,
        "settled": settled,
        "settlement": {"code": 0, "map_id": 999304, "rank": 23,
                       "total_damage": 3_891_948.303375},
        "self_point_by_map_id": points,
    }


def test_godsoul_stale_settlement_with_reset_round_points_is_not_current():
    # 2026-10-21 evening: morning settlement retained, round points reset.
    project = godsoul_boss.godsoul_boss_settled_current_round
    assert project(_round_snapshot({})) is False
    assert project(_round_snapshot({999303: 1_507_680})) is False


def test_godsoul_settlement_with_matching_round_point_is_current():
    project = godsoul_boss.godsoul_boss_settled_current_round
    assert project(_round_snapshot({999304: 3_891_948})) is True


def test_godsoul_missing_evidence_never_projects_current_settlement():
    project = godsoul_boss.godsoul_boss_settled_current_round
    assert project({"ok": False, "available": False, "complete": False}) is False
    assert project(_round_snapshot({999304: 3_891_948}, complete=False)) is False
    assert project(_round_snapshot({999304: 3_891_948}, settled=False)) is False
    assert project({**_round_snapshot({999304: 3_891_948}), "settlement": {}}) is False
    assert project(_round_snapshot(None)) is False
    assert project(_round_snapshot({999304: 0})) is False


def test_godsoul_point_must_equal_floor_of_settlement_damage():
    project = godsoul_boss.godsoul_boss_settled_current_round
    # Positive but unrelated point proves participation, not this settlement.
    assert project(_round_snapshot({999304: 1})) is False
    assert project(_round_snapshot({999304: 3_891_949})) is False
    assert project(_round_snapshot({999304: 3_891_948.0})) is False
    assert project(_round_snapshot({999304: True})) is False
    for damage in (float("nan"), float("inf"), True, None, 2.0**53):
        snapshot = _round_snapshot({999304: 3_891_948})
        snapshot["settlement"] = {**snapshot["settlement"], "total_damage": damage}
        assert project(snapshot) is False
    assert godsoul_boss.godsoul_boss_current_round_participated(
        _round_snapshot({999304: 1})) is True


def test_godsoul_participation_requires_complete_positive_round_point():
    participated = godsoul_boss.godsoul_boss_current_round_participated
    assert participated(_round_snapshot({})) is False
    assert participated(_round_snapshot({999304: 0})) is False
    assert participated(_round_snapshot(None)) is False
    assert participated(_round_snapshot({999303: 5}, complete=False)) is False
    assert participated({"ok": False, "complete": False}) is False
    assert participated(_round_snapshot({999303: 5})) is True


def test_godsoul_root_prefers_exact_loaded_lua_global(monkeypatch):
    calls = []
    monkeypatch.setattr(
        godsoul_boss,
        "_lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        godsoul_boss,
        "resolve_lua_global_manager_root",
        lambda memory, **options: calls.append((memory, options))
        or (0xABC, True, 0xDEF),
    )
    monkeypatch.setattr(
        godsoul_boss,
        "resolve_manager_root",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("exact global should avoid heap marker discovery")
        ),
    )

    validate = lambda _reader, _root: None
    root, cache_hit, resolver = godsoul_boss._resolve_godsoul_boss_root(
        _Memory(),
        validate=validate,
    )

    assert (root, cache_hit, resolver) == (0xABC, True, "lua_global")
    assert calls[0][1]["state_address"] == 0x1234
    assert calls[0][1]["global_name"] == "GodSoulBossMgr"
    assert calls[0][1]["validate"] is validate


def test_godsoul_loaded_global_with_unloaded_data_does_not_heap_scan(monkeypatch):
    monkeypatch.setattr(
        godsoul_boss,
        "_lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        godsoul_boss,
        "resolve_lua_global_manager_root",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("not loaded", code="data_not_loaded")
        ),
    )
    monkeypatch.setattr(
        godsoul_boss,
        "resolve_manager_root",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unloaded data is already an authoritative result")
        ),
    )

    try:
        godsoul_boss._resolve_godsoul_boss_root(
            _Memory(),
            validate=lambda _reader, _root: None,
        )
    except FanxiuRuntimeMemoryError as exc:
        assert exc.code == "data_not_loaded"
    else:
        raise AssertionError("expected data_not_loaded")
