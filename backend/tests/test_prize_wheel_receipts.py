"""Numeric identity/atomic receipt invariants, independent of game automation."""
from copy import deepcopy

import pytest
from backend.core.fanxiu.activity import prize_wheel_receipts as receipts


def observation(amount=158, *, target=None):
    evidence = {'pid': 5598, 'process_start_ticks': 39960}
    rewards = [{'id': 71101, 'is_big_reward': True}, {'id': 71102, 'is_big_reward': False}]
    return {'view': {'complete': True, 'activity_id': 711, 'runtime_id': 711,
                    'start_time_ms': 1000, 'end_time_ms': 2000, 'panel_address': '0x1234',
                    'evidence': evidence, 'ten_draw': False, 'cost_item_id': 40017,
                    'displayed_currency_amount': amount, 'wheel_rewards': rewards,
                    'waiting_response': False, 'animation_running': False,
                    'last_response_target': target},
            'wheel': {'complete': True, 'activity_id': 711, 'evidence': evidence,
                      'configuration': {'available': True, 'currency_type': 40017,
                                        'cost_per_draw': 1, 'gift_item_id': 40003},
                      'progress': 0, 'gift_balance': 0,
                      'wheel_rewards': [{**r, 'reward': 'Item|123_1'} for r in rewards]},
            'wallet': {'currency_type': 40017, 'currency_amount': amount,
                       'currency_borrow': 0, 'evidence': evidence}}


def test_exact_single_receipt_does_not_need_lagging_progress():
    before = observation(); after = observation(157, target={'id': 71101, 'is_big_reward': True})
    effect = receipts.verify_prize_wheel_single_draw(before, after)
    assert effect['draw_count'] == effect['grand_prize_count'] == effect['cost'] == 1


@pytest.mark.parametrize('change', ['no_debit', 'extra_debit', 'ten', 'reopened', 'new_process', 'waiting', 'animation'])
def test_ambiguous_or_incomplete_result_never_settles(change):
    before = observation(); after = observation(157, target={'id': 71102, 'is_big_reward': False})
    if change in ('no_debit', 'extra_debit'):
        amount = 158 if change == 'no_debit' else 156
        after['wallet']['currency_amount'] = after['view']['displayed_currency_amount'] = amount
    elif change == 'ten': after['view']['ten_draw'] = True
    elif change == 'reopened': after['view']['panel_address'] = '0x5678'
    elif change == 'new_process':
        after = deepcopy(after)
        for k in ('view', 'wheel', 'wallet'): after[k]['evidence']['process_start_ticks'] = 777
    elif change == 'waiting': after['view']['waiting_response'] = True
    elif change == 'animation': after['view']['animation_running'] = True
    with pytest.raises(ValueError): receipts.verify_prize_wheel_single_draw(before, after)


def test_pending_blocks_replay_and_verified_hit_blocks_next_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, '_path', lambda: tmp_path / 'receipts.json')
    before = observation()
    action = receipts.begin_prize_wheel_single_draw(before, retainability='retainable')
    with pytest.raises(RuntimeError, match='尚未结算'):
        receipts.begin_prize_wheel_single_draw(before, retainability='retainable')
    after = observation(157, target={'id': 71101, 'is_big_reward': True})
    first = receipts.settle_prize_wheel_single_draw(action['action_id'], after)
    assert receipts.settle_prize_wheel_single_draw(action['action_id'], before) == first
    with pytest.raises(RuntimeError, match='首个大奖'):
        receipts.begin_prize_wheel_single_draw(after, retainability='retainable')
    assert len(receipts.read_prize_wheel_actions()) == 1


def test_failed_settlement_preserves_original_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, '_path', lambda: tmp_path / 'receipts.json')
    action = receipts.begin_prize_wheel_single_draw(observation(), retainability='retainable')
    with pytest.raises(ValueError):
        receipts.settle_prize_wheel_single_draw(action['action_id'], observation())
    rows = receipts.read_prize_wheel_actions()
    assert len(rows) == 1 and rows[0]['action_id'] == action['action_id'] and rows[0]['status'] == 'pending'


def test_nonretainable_prize_does_not_stop_existing_tickets(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, '_path', lambda: tmp_path / 'receipts.json')
    before = observation()
    action = receipts.begin_prize_wheel_single_draw(before, retainability='non_retainable')
    after = observation(157, target={'id': 71101, 'is_big_reward': True})
    receipts.settle_prize_wheel_single_draw(action['action_id'], after)
    next_action = receipts.begin_prize_wheel_single_draw(after, retainability='non_retainable')
    assert next_action['action_id'] != action['action_id']


def test_unknown_history_or_currency_cannot_authorize_draw(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, '_path', lambda: tmp_path / 'receipts.json')
    before = observation(); before['wheel']['progress'] = 1
    with pytest.raises(RuntimeError, match='历史抽奖'):
        receipts.begin_prize_wheel_single_draw(before, retainability='retainable')
    before = observation(); before['wheel']['configuration']['currency_type'] = 1
    with pytest.raises(ValueError, match='白名单'):
        receipts.begin_prize_wheel_single_draw(before, retainability='retainable')
    assert receipts.read_prize_wheel_actions() == []


def test_milestone_prefix_and_last_page_math():
    from backend.core.fanxiu.instrumentation.prize_wheel import project_prize_wheel_milestones
    rows = [{'id': i, 'progress': i * 5, 'reward': 'Item|123_1'} for i in range(1, 7)]
    initial = project_prize_wheel_milestones(11, rows, [])
    assert initial['claimable_reward_ids'] == [1, 2]
    assert [r['id'] for r in initial['visible_milestones']] == [1, 2, 3, 4]
    last = project_prize_wheel_milestones(30, rows, [1, 2, 3, 4])
    assert [r['id'] for r in last['visible_milestones']] == [3, 4, 5, 6]
    assert [r['visible_slot'] for r in last['visible_milestones'] if r['claimable']] == [3, 4]


def test_nonprefix_claims_cannot_be_positioned_by_count():
    from backend.core.fanxiu.instrumentation.prize_wheel import project_prize_wheel_milestones
    rows = [{'id': i, 'progress': i * 5, 'reward': 'Item|123_1'} for i in range(1, 5)]
    with pytest.raises(ValueError, match='前缀'):
        project_prize_wheel_milestones(20, rows, [2])


def cumulative_observation(claimed=()):
    from backend.core.fanxiu.instrumentation.prize_wheel import project_prize_wheel_milestones
    view = observation()['view']
    rows = [{'id': i, 'progress': i * 5, 'reward': 'Item|123_1'} for i in range(1, 5)]
    rewards = {'complete': True, 'activity_id': 711, 'evidence': view['evidence'],
               **project_prize_wheel_milestones(11, rows, list(claimed))}
    return {'view': view, 'rewards': rewards}


def test_cumulative_claim_intent_blocks_draw_and_settles_exact_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, '_path', lambda: tmp_path / 'receipts.json')
    action = receipts.begin_prize_wheel_cumulative_claim(cumulative_observation())
    with pytest.raises(RuntimeError, match='尚未结算'):
        receipts.begin_prize_wheel_single_draw(observation(), retainability='retainable')
    with pytest.raises(ValueError, match='精确结算'):
        receipts.settle_prize_wheel_cumulative_claim(action['action_id'], cumulative_observation([1]))
    result = receipts.settle_prize_wheel_cumulative_claim(action['action_id'], cumulative_observation([1, 2]))
    assert result['effect'] == {'claimed_reward_ids': [1, 2], 'draw_count': 0, 'grand_prize_count': 0, 'cost': 0}
    assert receipts.settle_prize_wheel_cumulative_claim(action['action_id'], {}) == result


def test_cumulative_claim_rejects_wrong_receipt_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, '_path', lambda: tmp_path / 'receipts.json')
    action = receipts.begin_prize_wheel_cumulative_claim(cumulative_observation())
    with pytest.raises(ValueError, match='累计领奖'):
        receipts.settle_prize_wheel_single_draw(action['action_id'], observation())
