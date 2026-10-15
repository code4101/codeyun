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
