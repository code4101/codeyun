from __future__ import annotations

from backend.core.fanxiu.data_annotation.tasks.draw_claim_cycle import (
    run_draw_claim_cycle,
)


def test_draw_claim_cycle_records_each_observed_batch_and_rechecks_rewards():
    states = iter(
        [
            {"complete": True, "available_draws": 10, "claimable": [], "x": 0, "y": 0},
            {"complete": True, "available_draws": 6, "claimable": [], "x": 10, "y": 1},
            {"complete": True, "available_draws": 0, "claimable": [], "x": 16, "y": 1},
            {"complete": True, "available_draws": 0, "claimable": [], "x": 16, "y": 1, "claimed_count": 2, "activity_id": 102},
        ]
    )
    draws = iter(
        [
            {"after": {"x": 10, "y": 1}, "dx": 10, "dy": 1},
            {"after": {"x": 16, "y": 1}, "dx": 6, "dy": 0},
        ]
    )
    actions: list[str] = []

    result = run_draw_claim_cycle(
        read_snapshot=lambda: next(states),
        draw_once=lambda: actions.append("draw") or next(draws),
        close_draw_result=lambda: actions.append("close") or {"result": "success"},
        claim_rewards=lambda: actions.append("claim") or {"result": "success"},
        reward_settle_seconds=0,
    )

    assert result["stop_reason"] == "draws_exhausted_and_rewards_claimed"
    assert [(row["draw"]["dx"], row["draw"]["dy"]) for row in result["rounds"]] == [
        (10, 1),
        (6, 0),
    ]
    assert actions == [
        "claim",
        "draw",
        "close",
        "claim",
        "draw",
        "close",
        "claim",
        "claim",
    ]


def test_claimed_reward_can_refill_empty_wallet_before_first_draw():
    states = iter(
        [
            {"complete": True, "available_draws": 3, "claimable": [], "x": 10, "y": 1},
            {"complete": True, "available_draws": 0, "claimable": [], "x": 13, "y": 1},
            {"complete": True, "available_draws": 0, "claimable": [], "x": 13, "y": 1, "claimed_count": 2, "activity_id": 102},
        ]
    )
    draw_calls: list[int] = []

    result = run_draw_claim_cycle(
        read_snapshot=lambda: next(states),
        draw_once=lambda: draw_calls.append(1) or {"after": {"x": 13, "y": 1}, "dx": 3, "dy": 0},
        close_draw_result=lambda: {"result": "success"},
        claim_rewards=lambda: {"result": "success"},
        reward_settle_seconds=0,
    )

    assert len(draw_calls) == 1
    assert result["rounds"][0]["draw"]["dx"] == 3
