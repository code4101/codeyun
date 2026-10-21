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


from datetime import datetime

from backend.core.fanxiu.data_annotation.tasks.moyu_challenge import moyu_reward_decision


def snap(rounds, rewards, claimed=False):
    return dict(complete=True, rounds_present=rounds, rewards=rewards,
                already_claimed=claimed)


def at(hour, minute):
    return datetime(2026, 10, 21, hour, minute)


def test_reward_deferred_before_evening_challenge_deadline():
    both = snap([1, 2], [reward(1, 26), reward(2, 3)])
    assert moyu_reward_decision(both, at(18, 19))['action'] == 'defer'
    assert moyu_reward_decision(snap([1], [reward(1, 26)]), at(18, 5))['action'] == 'defer'


def test_both_rounds_present_choose_best():
    decision = moyu_reward_decision(snap([1, 2], [reward(1, 26), reward(2, 3)]), at(18, 20))
    assert decision['action'] == 'claim'
    assert decision['selected']['round'] == 2 and decision['degraded_reason'] is None


def test_missing_round_retries_until_final_slot_then_degrades():
    only_first = snap([1], [reward(1, 26)])
    assert moyu_reward_decision(only_first, at(18, 20))['action'] == 'retry'
    assert moyu_reward_decision(only_first, at(21, 49))['action'] == 'retry'
    final = moyu_reward_decision(only_first, at(21, 50))
    assert final['action'] == 'claim' and final['selected']['round'] == 1
    assert '第2轮' in final['degraded_reason']
    assert moyu_reward_decision(only_first, at(21, 59))['action'] == 'claim'


def test_incomplete_evidence_never_claims_even_in_final_slot():
    assert moyu_reward_decision(dict(complete=False), at(21, 59))['action'] == 'retry'


def test_already_claimed_and_after_deadline_never_claim():
    claimed = snap([1], [{**reward(1, 26), 'claimed': True}], claimed=True)
    for now in (at(18, 30), at(21, 59)):
        assert moyu_reward_decision(claimed, now)['action'] == 'already_claimed'
    both = snap([1, 2], [reward(1, 26), reward(2, 3)])
    assert moyu_reward_decision(both, at(22, 0))['action'] == 'closed'
    assert moyu_reward_decision(both, at(23, 0))['action'] == 'closed'


def test_decision_is_deterministic():
    both = snap([1, 2], [reward(1, 5), reward(2, 5)])
    first = moyu_reward_decision(both, at(19, 0))
    assert first == moyu_reward_decision(both, at(19, 0))
    assert first['selected']['round'] == 2
