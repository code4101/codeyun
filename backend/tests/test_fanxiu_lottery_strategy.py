from __future__ import annotations

import pytest

from backend.core.fanxiu.activity.lottery_strategy import (
    LotteryGoal,
    LotteryMilestone,
    LotteryPolicy,
    LotteryStrategyError,
    decide_lottery_action,
)


def _state(**overrides):
    value = {
        "complete": True,
        "available_draws": 23,
        "progress": 0,
        "hit_count": 0,
        "claimable": [],
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("goal", "hit_count", "expected_stop"),
    [
        (LotteryGoal("first_hit"), 1, "first_hit_reached"),
        (LotteryGoal("target_count", target_count=3), 3, "target_count_reached"),
    ],
)
def test_hit_goals_stop_at_the_configured_cumulative_count(
    goal, hit_count, expected_stop
):
    decision = decide_lottery_action(
        _state(hit_count=hit_count, progress=20),
        policy=LotteryPolicy(goal=goal),
    )

    assert decision.action == "stop"
    assert decision.stop_reason == expected_stop


def test_exhaust_all_uses_ten_draws_then_single_terminal_remainder():
    policy = LotteryPolicy(goal=LotteryGoal("exhaust_all"), remainder_mode="single")

    normal = decide_lottery_action(_state(available_draws=13), policy=policy)
    remainder = decide_lottery_action(_state(available_draws=3), policy=policy)
    done = decide_lottery_action(_state(available_draws=0), policy=policy)

    assert (normal.draw_mode, normal.requested_batch_size, normal.expected_batch_size) == (
        "ten_draw",
        10,
        10,
    )
    assert (
        remainder.draw_mode,
        remainder.requested_batch_size,
        remainder.expected_batch_size,
    ) == ("single_draw", 1, 1)
    assert done.stop_reason == "draws_exhausted"


def test_capped_ten_preserves_request_semantics_for_terminal_remainder():
    decision = decide_lottery_action(
        _state(available_draws=3),
        policy=LotteryPolicy(
            goal=LotteryGoal("exhaust_all"), remainder_mode="capped_ten"
        ),
    )

    assert decision.draw_mode == "ten_draw"
    assert decision.requested_batch_size == 10
    assert decision.expected_batch_size == 3


def test_deferred_remainder_is_a_stable_stage_boundary():
    decision = decide_lottery_action(
        _state(available_draws=3),
        policy=LotteryPolicy(
            goal=LotteryGoal("first_hit"), remainder_mode="defer"
        ),
    )

    assert decision.action == "stop"
    assert decision.stop_reason == "terminal_remainder_deferred"


def test_reached_target_may_top_up_only_a_strictly_positive_refund():
    policy = LotteryPolicy(
        goal=LotteryGoal("first_hit"),
        top_up_positive_refund_after_goal=True,
    )
    positive = decide_lottery_action(
        _state(available_draws=3, progress=18, hit_count=1),
        policy=policy,
        milestones=[LotteryMilestone(threshold=20, reward_draws=4)],
    )
    break_even = decide_lottery_action(
        _state(available_draws=4, progress=16, hit_count=1),
        policy=policy,
        milestones=[LotteryMilestone(threshold=20, reward_draws=4)],
    )

    assert positive.action == "draw"
    assert positive.draw_mode == "single_draw"
    assert positive.target_threshold == 20
    assert break_even.action == "stop"
    assert break_even.stop_reason == "first_hit_reached"


def test_claimable_milestone_preempts_draw_and_top_up():
    decision = decide_lottery_action(
        _state(progress=20, hit_count=1, claimable=[{"threshold": 20}]),
        policy=LotteryPolicy(
            goal=LotteryGoal("first_hit"),
            top_up_positive_refund_after_goal=True,
        ),
    )

    assert decision.action == "claim_rewards"


@pytest.mark.parametrize(
    "overrides",
    [
        {"available_draws": -1},
        {"progress": "10"},
        {"hit_count": True},
        {"progress": 1, "hit_count": 2},
    ],
)
def test_incomplete_or_incoherent_business_counts_fail_closed(overrides):
    with pytest.raises(LotteryStrategyError):
        decide_lottery_action(
            _state(**overrides),
            policy=LotteryPolicy(goal=LotteryGoal("exhaust_all")),
        )


def test_target_count_requires_a_positive_target():
    with pytest.raises(LotteryStrategyError, match="target_count"):
        decide_lottery_action(
            _state(),
            policy=LotteryPolicy(goal=LotteryGoal("target_count", target_count=0)),
        )
