from __future__ import annotations

from backend.core.fanxiu.instrumentation import demon_boss
from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef


class _Memory:
    pid = 123
    process_start_ticks = 456


class _Reader:
    def __init__(self, memory):
        self.memory = memory

    def fields(self, value):
        if isinstance(value, dict):
            return value
        return {}

    def long(self, value):
        return value.address


def test_demon_boss_snapshot_exposes_authoritative_left_times(monkeypatch):
    data_fields = {
        "V_DemonBossSync": {
            "leftTimes": 0,
            "buyInspireTimes": 2,
            "curCorpsByInspireTimes": 1,
            "nextDifferentCrossTime": LuaRef(
                kind="table",
                address=789,
            ),
        },
        "V_ActivityVO": {
            "state": 2,
            "startTime": LuaRef(kind="table", address=1000),
            "endTime": LuaRef(kind="table", address=2000),
        },
    }
    monkeypatch.setattr(demon_boss, "LuaJitReader", _Reader)
    monkeypatch.setattr(
        demon_boss,
        "_demon_boss_data_fields",
        lambda reader, root: data_fields,
    )

    result = demon_boss._snapshot(
        _Memory(),
        0xABC,
        root_cache_hit=True,
    )

    assert result["complete"] is True
    assert result["left_times"] == 0
    assert result["exhausted"] is True
    assert result["activity_state"] == 2
    assert result["activity_start_epoch_ms"] == 1000
    assert result["activity_end_epoch_ms"] == 2000
    assert result["evidence"]["root_cache_hit"] is True
