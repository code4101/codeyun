from __future__ import annotations

from backend.core.fanxiu.instrumentation import wild_boss
from backend.core.fanxiu.instrumentation.runtime_memory import MumuProcessMemory


class _FakeReader:
    """Plain-dict stand-in for already-decoded Lua tables."""

    def __init__(self, _memory: MumuProcessMemory) -> None:
        pass

    def fields(self, value):
        return value.get("fields", {}) if isinstance(value, dict) else {}

    def dictionary_fields(self, value):
        return value.get("entries", {}) if isinstance(value, dict) else {}

    def list_items(self, value):
        return list(value), len(value)


def _memory() -> MumuProcessMemory:
    return MumuProcessMemory(pid=1, process_start_ticks=2, adb_serial="test", regions=[])


def _snapshot(monkeypatch, data):
    monkeypatch.setattr(wild_boss, "LuaJitReader", _FakeReader)
    monkeypatch.setattr(wild_boss, "_wild_boss_data_fields", lambda _reader, _root: data)
    return wild_boss._snapshot(_memory(), 0x1000, root_cache_hit=False)


def test_wild_boss_unloaded_fatigue_and_list_stay_unknown_not_zero(monkeypatch) -> None:
    result = _snapshot(monkeypatch, {"bossGroupInfoVoDic": {"entries": {}}})

    assert result["complete"] is False
    assert result["reward_remaining"] is None
    assert result["boss_list_loaded"] is False
    assert result["group_count"] is None
    assert result["unsealed_group_count"] is None


def test_wild_boss_counts_only_type6_group_leaders(monkeypatch) -> None:
    boss = {"fields": {"groupId": 7, "monsterId": 7, "isSeal": False, "isDead": False}}
    sealed = {"fields": {"groupId": 8, "monsterId": 8, "isSeal": True, "isDead": True}}
    data = {
        "fatigue": 0,
        "bossGroupInfoVoDic": {
            "entries": {
                6: {"entries": {7: [boss], 8: [sealed]}},
                7: {"entries": {9: [boss]}},
            }
        },
    }

    result = _snapshot(monkeypatch, data)

    assert result["complete"] is True
    assert result["reward_remaining"] == 0
    assert result["loaded_boss_types"] == [6, 7]
    assert result["group_count"] == 2
    assert result["unsealed_group_count"] == 1
    assert result["sealed_group_count"] == 1


def _groups(*leaders):
    return {"boss_list_loaded": True, "reward_remaining": 0,
            "groups": [{"group_id": i, "bosses": [leader]} for i, leader in enumerate(leaders, 1)]}


def test_wild_boss_target_skips_sealed_dead_and_unknown_leaders():
    snapshot = _groups(
        {"is_seal": True, "is_dead": False, "map_id": 1},
        {"is_seal": False, "is_dead": True, "map_id": 2},
        {"is_seal": None, "is_dead": False, "map_id": 3},
        {"is_seal": False, "is_dead": False, "map_id": 4, "monster_id": 9},
    )
    assert wild_boss.select_wild_boss_target(snapshot) == {"group_id": 4, "map_id": 4, "monster_id": 9}
    assert wild_boss.select_wild_boss_target({"boss_list_loaded": False}) is None


def test_quest1023_admission_uses_quest_progress_not_reward_fatigue():
    open_snapshot = _groups({"is_seal": False, "is_dead": False, "map_id": 4})
    # reward_remaining 0 does not mean today's quest is done.
    assert wild_boss.wild_boss_daily_admission({"task_id": 1023, "turn": 0, "target_turn": 1}, open_snapshot)["state"] == "eligible"
    assert wild_boss.wild_boss_daily_admission({"task_id": 1023, "turn": 1, "target_turn": 1}, open_snapshot)["state"] == "done"
    sealed = _groups({"is_seal": True, "is_dead": False, "map_id": 4})
    assert wild_boss.wild_boss_daily_admission({"task_id": 1023, "turn": 0, "target_turn": 1}, sealed)["state"] == "no_open_boss"
    assert wild_boss.wild_boss_daily_admission({"task_id": 1023, "turn": 0, "target_turn": 1}, {"boss_list_loaded": None})["state"] == "unknown"
    assert wild_boss.wild_boss_daily_admission(None, open_snapshot)["state"] == "unknown"


Q0 = {"task_id": 1023, "turn": 0, "target_turn": 1, "done": False}


def test_quest1023_unknown_leader_facts_are_not_proof_of_no_open_boss():
    unknown_seal = _groups({"is_seal": None, "is_dead": False, "map_id": 90001})
    assert wild_boss.wild_boss_daily_admission(Q0, unknown_seal)["state"] == "unknown"
    for leader in ({"is_seal": False, "is_dead": False},                       # map missing
                   {"is_seal": False, "is_dead": False, "map_id": True},       # bool map
                   {"is_seal": False, "is_dead": False, "map_id": -3},
                   {"is_seal": False, "map_id": 5}):                            # dead flag missing
        assert wild_boss.wild_boss_daily_admission(Q0, _groups(leader))["state"] == "unknown"
    # One proven open target still wins over unknown neighbours; reward count unknown is irrelevant.
    mixed = _groups({"is_seal": None, "is_dead": False, "map_id": 1},
                    {"is_seal": False, "is_dead": False, "map_id": 7})
    mixed["reward_remaining"] = None
    result = wild_boss.wild_boss_daily_admission(Q0, mixed)
    assert result["state"] == "eligible" and result["target"]["map_id"] == 7


def test_quest1023_structure_and_identity_fail_closed():
    assert wild_boss.wild_boss_daily_admission(Q0, {"boss_list_loaded": True})["state"] == "unknown"
    assert wild_boss.wild_boss_daily_admission(Q0, {"boss_list_loaded": True, "groups": [{"bosses": []}]})["state"] == "unknown"
    assert wild_boss.wild_boss_daily_admission(Q0, {"boss_list_loaded": True, "groups": []})["state"] == "no_open_boss"
    closed = _groups({"is_seal": True, "map_id": 1}, {"is_dead": True, "is_seal": False, "map_id": 2})
    assert wild_boss.wild_boss_daily_admission(Q0, closed)["state"] == "no_open_boss"
    open_snapshot = _groups({"is_seal": False, "is_dead": False, "map_id": 4})
    for row in ({"task_id": 1002, "turn": 2, "target_turn": 2, "done": True},
                {"task_id": True, "turn": 0, "target_turn": 1},
                {"task_id": 1023, "turn": -1, "target_turn": 1},
                {"task_id": 1023, "turn": False, "target_turn": 1},
                {"task_id": 1023, "turn": 0, "target_turn": 1, "done": True}):
        assert wild_boss.wild_boss_daily_admission(row, open_snapshot)["state"] == "unknown"
