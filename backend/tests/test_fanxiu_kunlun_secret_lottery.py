from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.kunlun_secret_lottery import (
    KUNLUN_LOTTERY_NAMESPACE,
    list_kunlun_lottery_points,
    kunlun_week_instance_id,
    record_kunlun_lottery_point,
)
from backend.core.fanxiu.data_annotation.tasks import kunlun_secret_lottery as task


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _snapshot(x: int, y: int) -> dict:
    return {
        "complete": True,
        "captured_at": "2026-08-13T21:10:00+08:00",
        "activity_id": 103,
        "x": x,
        "y": y,
        "hit_big": y,
        "hit_big_total": y,
        "selected_big_count": y,
        "selected_big_reward": {
            "big_id": 103,
            "library_id": 900201,
            "item_id": 2016,
            "name": "古·山河无疆屏",
        },
        "evidence": {"pid": 1},
    }


def test_kunlun_points_have_independent_namespace_and_instance() -> None:
    assert kunlun_week_instance_id("2026-08-13T21:10:00+08:00") == (
        "kunlun-secret-2026-08-13"
    )
    assert KUNLUN_LOTTERY_NAMESPACE == "kunlun-secret-draw-grand-prize"

    engine = _engine()
    instance_id = "kunlun-secret-2026-08-13"
    with Session(engine) as session:
        record_kunlun_lottery_point(
            session, snapshot=_snapshot(0, 0), instance_id=instance_id
        )
        dataset = record_kunlun_lottery_point(
            session, snapshot=_snapshot(10, 1), instance_id=instance_id
        )
        stored = list_kunlun_lottery_points(session, instance_id=instance_id)

    assert dataset.namespace == KUNLUN_LOTTERY_NAMESPACE
    assert [(point.x, point.y, point.dx, point.dy) for point in stored.samples] == [
        (0, 0, 0, 0),
        (10, 1, 10, 1),
    ]


def test_kunlun_point_preserves_strategy_observation_fields() -> None:
    engine = _engine()
    snapshot = {
        **_snapshot(10, 1),
        "observation_kind": "after_draw",
        "action_id": "action-1",
        "draw_mode": "ten_draw",
        "batch_size": 10,
        "available_currency": 13,
        "available_draws": 13,
        "cost_type": 4001,
        "cost_per_draw": 1,
        "progress": 10,
        "claimed_count": 0,
    }
    with Session(engine) as session:
        record_kunlun_lottery_point(session, snapshot=snapshot)
        point = list_kunlun_lottery_points(
            session, instance_id="kunlun-secret-2026-08-13"
        ).samples[0]

    assert point.observation_kind == "after_draw"
    assert point.action_id == "action-1"
    assert point.draw_mode == "ten_draw"
    assert point.batch_size == 10
    assert point.available_currency == 13
    assert point.progress == 10




def test_kunlun_lottery_uses_independently_verified_result_asset() -> None:
    assert task.KUNLUN_DRAW_RESULT_SCENE_ID == 544




@pytest.mark.parametrize(
    ("available", "remaining", "progress", "expected_action", "batch"),
    [
        (23, 20, 0, "ten_draw", 10),
        (10, 20, 0, "ten_draw", 10),
        (9, 20, 0, "single_draw", 1),
        (0, 20, 0, "stop_exhausted", 0),
        (12, 19, 10, "stop_first_grand_prize", 0),
    ],
)
def test_kunlun_strategy_targets_only_first_grand_prize(
    available, remaining, progress, expected_action, batch
) -> None:
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": available,
            "selected_big_capacity": 20,
            "selected_big_remaining": remaining,
            "progress": progress,
            "cost_type": 9001,
            "rewards": [],
            "claimable": [],
        }
    )
    assert decision.action == expected_action
    assert decision.expected_batch_size == batch


@pytest.mark.parametrize("progress", [17, 18, 19, 37, 38, 39])
def test_kunlun_strategy_saves_keys_after_hit_even_near_refund(progress: int) -> None:
    threshold = 20 if progress < 20 else 40
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 10,
            "selected_big_capacity": 20,
            "selected_big_remaining": 19,
            "progress": progress,
            "cost_type": 9001,
            "rewards": [
                {
                    "threshold": threshold,
                    "reward": "Item|9001_4",
                    "state": "locked",
                }
            ],
            "claimable": [],
        }
    )
    assert decision.action == "stop_first_grand_prize"
    assert decision.expected_batch_size == 0
    assert decision.target_threshold is None


@pytest.mark.parametrize("progress", [16, 36])
def test_kunlun_strategy_does_not_continue_for_break_even_refund(progress: int) -> None:
    threshold = 20 if progress < 20 else 40
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 10,
            "selected_big_capacity": 20,
            "selected_big_remaining": 19,
            "progress": progress,
            "cost_type": 9001,
            "rewards": [
                {
                    "threshold": threshold,
                    "reward": "Item|9001_4",
                    "state": "locked",
                }
            ],
            "claimable": [],
        }
    )

    assert decision.action == "stop_first_grand_prize"


def test_kunlun_strategy_does_not_top_up_when_wallet_cannot_reach_refund() -> None:
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 2,
            "selected_big_capacity": 20,
            "selected_big_remaining": 19,
            "progress": 16,
            "cost_type": 9001,
            "rewards": [
                {"threshold": 20, "reward": "Item|9001_4", "state": "locked"}
            ],
            "claimable": [],
        }
    )
    assert decision.action == "stop_first_grand_prize"


def test_kunlun_strategy_claims_reached_milestone_before_more_draws() -> None:
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 3,
            "selected_big_capacity": 20,
            "selected_big_remaining": 20,
            "progress": 20,
            "claimable": [{"threshold": 20}],
        }
    )
    assert decision.action == "claim_rewards"


def test_config_phase_defers_sub_ten_inventory_without_first_prize() -> None:
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 3,
            "selected_big_capacity": 20,
            "selected_big_remaining": 20,
            "progress": 20,
            "claimable": [],
        },
        allow_single_draws=False,
    )

    assert decision.action == "stop_single_draws_deferred"
    assert decision.expected_batch_size == 0


def test_config_phase_stops_after_hit_even_near_milestone() -> None:
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 3,
            "selected_big_capacity": 20,
            "selected_big_remaining": 19,
            "progress": 17,
            "cost_type": 9001,
            "rewards": [
                {"threshold": 20, "reward": "Item|9001_4", "state": "locked"}
            ],
            "claimable": [],
        },
        allow_single_draws=False,
    )

    assert decision.action == "stop_first_grand_prize"
    assert decision.target_threshold is None


@pytest.mark.parametrize("remaining", [None, -1, 21, "20", True])
def test_kunlun_strategy_fails_closed_without_exact_pool_remaining(remaining) -> None:
    with pytest.raises(RuntimeError, match="大奖剩余数量"):
        task.decide_kunlun_next_draw(
            {
                "complete": True,
                "available_draws": 23,
                "selected_big_capacity": 20,
                "selected_big_remaining": remaining,
                "progress": 0,
                "claimable": [],
            }
        )




@pytest.mark.parametrize("remaining", [19, 18, 17, 0])
def test_one_or_more_prizes_claims_reached_rewards_before_stopping(remaining):
    decision = task.decide_kunlun_next_draw({
        "complete": True, "selected_big_capacity": 20,
        "selected_big_remaining": remaining, "available_draws": 10,
        "progress": 20, "claimable": [{"id": 10102, "threshold": 20}],
    })
    assert decision.action == "claim_rewards"
    assert decision.expected_batch_size == 0


def closing_snapshot(progress=17, available=10):
    return {"complete": True, "selected_big_capacity": 20,
            "selected_big_remaining": 19, "progress": progress,
            "available_draws": available, "cost_type": 9001, "cost_per_draw": 1,
            "claimable": [], "rewards": [
                {"threshold": 20, "reward": "Item|9001_4", "state": "locked"},
                {"threshold": 40, "reward": "Item|9001_30", "state": "locked"}]}


@pytest.mark.parametrize("progress,available,target", [(16,4,20),(17,3,20),(18,2,20),(15,10,None),(17,2,None)])
def test_closing_next_refund_includes_break_even(progress, available, target):
    state = closing_snapshot(progress, available)
    assert task.kunlun_refund_target(state) == target
    decision = task.decide_kunlun_next_draw(state, post_hit_target=target)
    assert decision.action == ("single_draw" if target else "stop_first_grand_prize")


def test_closing_entry_already_hit_skips_rewards_and_top_up():
    state = closing_snapshot()
    state["claimable"] = [{"threshold": 10}]
    assert task.decide_kunlun_next_draw(state, closing_entry=True).action == "stop_first_grand_prize"


def test_closing_only_fills_one_committed_threshold():
    state = closing_snapshot(20)
    state["claimable"] = [{"threshold": 20}]
    assert task.decide_kunlun_next_draw(state, post_hit_target=20).action == "claim_rewards"
    state["claimable"] = []
    assert task.decide_kunlun_next_draw(state, post_hit_target=20).action == "stop_first_grand_prize"


def test_refund_uses_draw_cost_not_raw_currency():
    state = closing_snapshot(17)
    state["cost_per_draw"] = 2
    assert task.kunlun_refund_target(state) is None
