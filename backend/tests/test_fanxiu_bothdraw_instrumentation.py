from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.bothdraw import (
    build_bothdraw_revenue_task_snapshot,
    read_bothdraw_cumulative_rewards_runtime,
    _validated_kunlun_targets,
    build_bothdraw_runtime_reward_items,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


def test_runtime_optional_rows_resolve_kunlun_reward_kinds() -> None:
    result = build_bothdraw_runtime_reward_items(
        [700105, 700106, 700107, 700110],
        runtime_rows={
            700105: {
                "item_id": 18015027,
                "reward_limit": "IsGetFashionMax|5042_18015027_1",
            },
            700106: {
                "item_id": 4130017,
                "reward_limit": "IsGetTalismanGradeMax|2016_4130017_1",
            },
            700107: {
                "item_id": 3110059,
                "reward_limit": "IsGetGongFaMax|395801_3110059_1",
            },
            700110: {
                "item_id": 3110152,
                "reward_limit": "IsGetGongFaMax|470701_3110152_1",
            },
        },
        item_cards=[
            {"id": 18015027, "name": "御器·广府龙舟", "linked_fashion_id": 5042},
            {"id": 4130017, "name": "古·山河无疆屏", "linked_talisman_id": 2016},
            {"id": 3110059, "name": "镇妖宝箓", "linked_gongfa_id": 395801},
            {"id": 3110152, "name": "兽王丹箓", "linked_gongfa_id": 470701},
        ],
    )

    assert [(item["item_id"], item["kind"]) for item in result] == [
        (18015027, "fashion"),
        (4130017, "talisman"),
        (3110059, "gongfa"),
        (3110152, "gongfa"),
    ]
    assert result[0]["reward_limit"] == "IsGetFashionMax|5042_18015027_1"


def test_runtime_optional_rows_fail_closed_when_catalog_identity_is_missing() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match=r"missing=\[700110\]"):
        build_bothdraw_runtime_reward_items(
            [700105, 700110],
            runtime_rows={
                700105: {"item_id": 18015027},
                700110: {"item_id": 3110152},
            },
            item_cards=[
                {"id": 18015027, "name": "御器·广府龙舟", "linked_fashion_id": 5042},
            ],
        )


def test_kunlun_reward_limit_must_match_catalog_target() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="目录身份不一致"):
        _validated_kunlun_targets(
            [
                {
                    "library_id": 700105,
                    "item_id": 18015027,
                    "kind": "fashion",
                    "target_id": 9999,
                    "reward_limit": "IsGetFashionMax|5042_18015027_1",
                }
            ]
        )


def test_cumulative_reader_forces_current_bothdraw_root(monkeypatch) -> None:
    """A structurally valid cached root may belong to the previous activity."""

    from backend.core.fanxiu.instrumentation import bothdraw

    class Memory:
        pid = 1
        process_start_ticks = 2

    calls: list[dict] = []
    monkeypatch.setattr(bothdraw.MumuProcessMemory, "discover_cached", classmethod(lambda _cls: Memory()))
    monkeypatch.setattr(bothdraw, "_lua_addresses", lambda _memory: {"state": "0x1"})

    def resolve(_memory, **kwargs):
        calls.append(kwargs)
        raise FanxiuRuntimeMemoryError("stop after root selection")

    monkeypatch.setattr(bothdraw, "resolve_lua_global_manager_root", resolve)
    snapshot = read_bothdraw_cumulative_rewards_runtime(include_selected_big_reward=False)

    assert snapshot["complete"] is False
    assert calls[0]["manager_key"] == "bothdraw-optional-reward"
    assert calls[0]["force_refresh"] is True


def test_cumulative_reader_cold_rebinds_all_roots_after_stale_lua_node(monkeypatch) -> None:
    from backend.core.fanxiu.instrumentation import bothdraw

    calls = []

    def read_once(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "complete": False,
                "reason": "Lua table node 地址无效：0x7fdd47906de0",
            }
        return {"complete": True, "activity_id": 3001003}

    monkeypatch.setattr(
        bothdraw,
        "_read_bothdraw_cumulative_rewards_runtime_once",
        read_once,
    )

    snapshot = bothdraw.read_bothdraw_cumulative_rewards_runtime()

    assert snapshot == {"complete": True, "activity_id": 3001003}
    assert calls == [
        {
            "include_selected_big_reward": True,
            "visible_slot_count": 4,
        },
        {
            "include_selected_big_reward": True,
            "visible_slot_count": 4,
            "force_refresh_roots": True,
        },
    ]


def test_activity_owned_revenue_tasks_preserve_live_group_membership() -> None:
    snapshot = build_bothdraw_revenue_task_snapshot(
        activity_id=3001003,
        task_groups={
            4: [
                {"id": 300100301, "isFinished": True},
                {
                    "id": 300100302,
                    "isFinished": False,
                    "serverData": {
                        "taskId": 300100302,
                        "status": 4,
                        "turn": 1,
                        "targetTurn": 1,
                    },
                },
            ],
            5: [
                {
                    "id": 300100305,
                    "isFinished": False,
                    "serverData": {"taskId": 300100305, "status": 3},
                }
            ],
        },
    )

    assert snapshot["task_count"] == 3
    assert snapshot["claimed_count"] == 1
    assert snapshot["claimable"] == [
        {
            "task_id": 300100302,
            "group_id": 4,
            "position": 2,
            "status": 4,
            "state": "claimable",
            "turn": 1,
            "target_turn": 1,
            "reward_time": 0,
        }
    ]
    assert snapshot["task_groups"][0]["group_id"] == 4
    assert snapshot["all_current_claimable_rewards_claimed"] is False


def test_activity_owned_revenue_finished_row_rejects_stale_server_state() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="仍携带 serverData"):
        build_bothdraw_revenue_task_snapshot(
            activity_id=3001003,
            task_groups={
                4: [
                    {
                        "id": 300100301,
                        "isFinished": True,
                        "serverData": {"taskId": 300100301, "status": 5},
                    }
                ]
            },
        )
