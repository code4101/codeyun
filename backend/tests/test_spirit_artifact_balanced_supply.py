import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_balanced_supply import (
    BalancedSupplyChoice as Choice, plan_balanced_spirit_artifact_supply as plan,
)


def test_miluo_five_boxes_are_two_batches():
    levels = {(3, 4): 1, (3, 5): 3, (3, 6): 1}
    result = plan(levels, [Choice(t, i) for i, t in enumerate(levels, 1)], 5)
    assert [(b.target, b.quantity) for b in result.batches] == [((3, 4), 3), ((3, 6), 2)]
    assert result.projected_levels == {(3, 4): 4, (3, 5): 3, (3, 6): 3}
    assert levels[(3, 4)] == 1


def test_next_source_counts_previous_pending_rewards_and_uses_numeric_tie_order():
    levels = {(1, 6): 1, (1, 5): 1}
    choices = [Choice((1, 6), 6), Choice((1, 5), 5)]
    first = plan(levels, choices, 1)
    second = plan(first.projected_levels, choices, 1)
    assert first.batches[0].target == (1, 5)
    assert second.batches[0].target == (1, 6)


def test_limits_and_non_unit_reward_gain():
    result = plan({(1, 1): 0, (1, 2): 1},
                  [Choice((1, 1), 1, 2, 1), Choice((1, 2), 2, 1, 1)], 5)
    assert result.projected_levels == {(1, 1): 2, (1, 2): 2}
    assert result.unallocated == 3


def test_unknown_level_and_duplicate_target_are_rejected():
    with pytest.raises(ValueError):
        plan({(1, 1): None}, [Choice((1, 1), 1)], 1)
    with pytest.raises(ValueError):
        plan({(1, 1): 0}, [Choice((1, 1), 1), Choice((1, 1), 2)], 1)
