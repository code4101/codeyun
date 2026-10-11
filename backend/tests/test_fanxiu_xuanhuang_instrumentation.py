from __future__ import annotations

import pytest
from backend.core.fanxiu.data_annotation.tasks.daily_xuanhuang import xuanhuang_daily_target_complete
from backend.core.fanxiu.data_annotation.tasks.xuanhuang_reward_supply import plan_xuanhuang_reward_supply

from backend.core.fanxiu.instrumentation import xuanhuang
from backend.core.fanxiu.instrumentation.runtime_memory import (
    MumuProcessMemory,
)


def test_xuanhuang_supply_requires_explicit_budget():
    assert plan_xuanhuang_reward_supply({}, budget_lingshi=0)["quantity"] == 0


@pytest.mark.parametrize("override, raises", [
    ({}, False), ({"goods_id": 170002}, True), ({"cost_item_id": 5}, True),
    ({"item_id": 39031002}, True), ({"Price": 301}, True),
    ({"showNum": 2}, True), ({"identity_complete": False}, True),
])
def test_xuanhuang_supply_whitelists_single_verified_purchase(override, raises):
    dialog = {"complete": True, "identity_complete": True, "goods_id": 170001,
              "item_id": 39031001, "cost_item_id": 1, "goodsNum": 1,
              "Price": 300, "maxNum": 1, "showNum": 1, "CanBuy": True, "isEnough": True,
              **override}
    if raises:
        with pytest.raises(RuntimeError):
            plan_xuanhuang_reward_supply(dialog, budget_lingshi=300)
    else:
        assert plan_xuanhuang_reward_supply(dialog, budget_lingshi=300)["cost"] == 300
        assert not plan_xuanhuang_reward_supply(dialog, budget_lingshi=299)["ready"]


def test_xuanhuang_snapshot_reads_loaded_remaining_count(
    monkeypatch,
):
    monkeypatch.setattr(
        xuanhuang,
        "_xuanhuang_data_fields",
        lambda _reader, _root: {"leftChangeTimes": 2.0},
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = xuanhuang._snapshot(
        memory,
        0x2000,
        root_cache_hit=True,
    )

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["counter_loaded"] is True
    assert result["remaining"] == 2
    assert result["reward_chances_remaining"] == 2
    assert result["counter_kind"] == "reward_chances"
    assert result["protocol"].endswith(".leftChangeTimes")


def test_xuanhuang_data_requires_page_counter(monkeypatch):
    monkeypatch.setattr(
        xuanhuang,
        "manager_index_fields",
        lambda *_args, **_kwargs: {"inst": object()},
    )
    class FakeReader:
        @staticmethod
        def fields(_value):
            return {}

    try:
        xuanhuang._xuanhuang_data_fields(
            FakeReader(),
            0x2000,
        )
    except Exception as exc:
        assert "计数尚未加载" in str(exc)
    else:
        raise AssertionError("缺少 leftChangeTimes 时必须不可判定")


def test_daily_target_requires_quest_progress_independent_of_reward_counter():
    progress = {"complete": True, "task_id": 1049, "present": True,
                "turn": 2, "target_turn": 3, "done": False}
    assert xuanhuang_daily_target_complete(progress) is False
    assert xuanhuang_daily_target_complete({**progress, "turn": 3, "done": True}) is True
    # The client can remove a completed entry but retain its finishTasks ID.
    assert xuanhuang_daily_target_complete({"complete": True, "task_id": 1049,
                                           "present": False, "done": True}) is True
    for invalid in ({**progress, "task_id": 1002}, {**progress, "present": False},
                    {**progress, "target_turn": None}, {**progress, "complete": False}):
        with pytest.raises(RuntimeError):
            xuanhuang_daily_target_complete(invalid)
