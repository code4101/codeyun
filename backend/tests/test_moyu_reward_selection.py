from backend.core.fanxiu.data_annotation.tasks.moyu_challenge import (
    MoyuChallengeTaskMixin, select_moyu_reward,
)


def reward(round_number, rank):
    return dict(round=round_number, activity_id=250301, rank=rank, claimed=False)


def test_single_ranked_round_remains_claimable():
    first = reward(1, 7)
    assert select_moyu_reward([first]) == first
    assert select_moyu_reward([first, reward(2, 0)]) == first


def test_choose_best_actual_rank_and_do_not_repeat_claim():
    first, second = reward(1, 7), reward(2, 1)
    assert select_moyu_reward([first, second]) == second
    assert select_moyu_reward([{**first, 'claimed': True}]) is None


def test_empty_button_is_unknown_and_claimed_has_precedence():
    classify = MoyuChallengeTaskMixin._moyu_reward_claim_action_state
    assert classify('') is None
    assert classify('领取') == 'claimable'
    assert classify('已领取') == 'claimed'
