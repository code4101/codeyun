from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation import capacity_tower
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    MumuProcessMemory,
)


class _FakeReader:
    def __init__(self, _memory: MumuProcessMemory) -> None:
        pass

    def fields(self, value):
        if value == "current":
            return {"curTowerId": 1426, "gettedBoxRewardId": 0}
        if value == "tower-list":
            return {"count": 1426}
        return {}

    def list_items(self, value):
        assert value == "rewards"
        return ([{"id": 1}, {"id": 2}], 2)


def _memory() -> MumuProcessMemory:
    return MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )


def test_capacity_tower_snapshot_reads_complete_loaded_model(monkeypatch) -> None:
    monkeypatch.setattr(capacity_tower, "LuaJitReader", _FakeReader)
    monkeypatch.setattr(
        capacity_tower,
        "_capacity_tower_loaded_fields",
        lambda _reader, _root: (
            {"maxTowerCfgId": 1426, "towerCfgList": "tower-list"},
            {
                "curTowerMsg": "current",
                "chanllenge": 20,
                "rewardList": "rewards",
            },
        ),
    )

    result = capacity_tower._snapshot(
        _memory(),
        0x2000,
        root_cache_hit=True,
    )

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["current_tower_id"] == 1426
    assert result["max_configured_tower_id"] == 1426
    assert result["declared_tower_config_count"] == 1426
    assert result["config_bounds_complete"] is True
    assert result["has_current_tower_config"] is True
    assert result["has_next_tower_config"] is False
    assert result["chain_pass_count"] == 20
    assert result["reward_result_count"] == 2
    assert result["declared_reward_result_count"] == 2
    assert result["evidence"]["root_cache_hit"] is True


def test_capacity_tower_snapshot_does_not_infer_config_bounds_from_incomplete_metadata(
    monkeypatch,
) -> None:
    monkeypatch.setattr(capacity_tower, "LuaJitReader", _FakeReader)
    monkeypatch.setattr(
        capacity_tower,
        "_capacity_tower_loaded_fields",
        lambda _reader, _root: (
            {"maxTowerCfgId": 1426, "towerCfgList": "incomplete-list"},
            {
                "curTowerMsg": "current",
                "chanllenge": 0,
                "rewardList": "rewards",
            },
        ),
    )

    result = capacity_tower._snapshot(
        _memory(),
        0x2000,
        root_cache_hit=False,
    )

    assert result["config_bounds_complete"] is False
    assert result["has_current_tower_config"] is None
    assert result["has_next_tower_config"] is None


@pytest.mark.parametrize("chain_pass_count", [-1, 21, None])
def test_capacity_tower_snapshot_rejects_invalid_chain_count(
    monkeypatch,
    chain_pass_count,
) -> None:
    monkeypatch.setattr(capacity_tower, "LuaJitReader", _FakeReader)
    monkeypatch.setattr(
        capacity_tower,
        "_capacity_tower_loaded_fields",
        lambda _reader, _root: (
            {"maxTowerCfgId": 1426, "towerCfgList": "tower-list"},
            {
                "curTowerMsg": "current",
                "chanllenge": chain_pass_count,
                "rewardList": "rewards",
            },
        ),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="连续通关数字段无效"):
        capacity_tower._snapshot(
            _memory(),
            0x2000,
            root_cache_hit=False,
        )


def test_capacity_tower_reader_fails_closed_when_memory_is_unavailable(monkeypatch) -> None:
    def unavailable():
        raise FanxiuRuntimeMemoryError("测试：游戏进程不可读")

    monkeypatch.setattr(
        capacity_tower.MumuProcessMemory,
        "discover_cached",
        unavailable,
    )

    result = capacity_tower.read_capacity_tower_snapshot()

    assert result["ok"] is False
    assert result["available"] is False
    assert result["complete"] is False
    assert result["reason"] == "测试：游戏进程不可读"
    assert result["evidence"]["pid"] is None
