"""积分预算的资源边界；纯契约测试不提交游戏操作。"""
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.activity import lingchong_round_resource_planning as planner


def resource(item_id=8022005, count=100):
    return SimpleNamespace(item_id=item_id, name='普通资源', count=count,
                           quality=2, sort_order=1, aptitude_gain_by_gift_id={3: 1})


def test_score_samples_require_exact_period_pet_and_unclamped_score():
    valid = dict(status='round_observation_verified', instance_key='today', pet_id=7101,
                 item_id=8022005, quantity=2, score_before=10, score_after=30,
                 aptitude_delta=4, score_is_lower_bound=False)
    history = [valid, dict(valid, instance_key='yesterday', score_after=999),
               dict(valid, pet_id=999, score_after=999),
               dict(valid, status='verified', score_after=999),
               dict(valid, score_is_lower_bound=True, score_after=999)]
    samples = planner.round_resource_samples(history, instance_key='today', pet_id=7101)
    assert samples == {8022005: {'score_gain': 10, 'aptitude_gain': 2}}


def test_unknown_resource_probes_one_without_repeating_seed_initialization():
    facts = dict(all_claimed=False, current_round=1, score=10,
                 rounds=[dict(cumulative_target=100)])
    result = planner.plan_round_resource_batch(
        facts, [resource(8022009), resource()], {'gift_remaining': {3: 100}}, {})
    assert result['item_id'] == 8022005
    assert result['quantity'] == 1
    assert result['mode'] == 'probe'


@pytest.mark.parametrize('stock,capacity,expected', [(2, 100, 2), (100, 6, 3)])
def test_measured_batch_respects_stock_and_remaining_pet_capacity(stock, capacity, expected):
    facts = dict(all_claimed=False, current_round=1, score=10,
                 rounds=[dict(cumulative_target=100)])
    result = planner.plan_round_resource_batch(
        facts, [resource(count=stock)], {'gift_remaining': {3: capacity}},
        {8022005: {'score_gain': 10, 'aptitude_gain': 2}})
    assert result['quantity'] == expected


def test_ambiguous_resource_effect_cannot_settle_score_observation(monkeypatch):
    occurrence = SimpleNamespace(instance_key='today', activity_id=16042901)
    observation = dict(action_id='observation', prior_action_ids=[], instance_key='today',
                       pet_id=7101, item_id=8022005, quantity=1, round=1, score_before=10)
    effect = dict(status='verified', pet_id=7101, item_id=8022005, quantity=1,
                  aptitude_delta=1)
    writes = []
    monkeypatch.setattr(planner, 'record_pet_resource_receipt', lambda *a, **k: writes.append(a))
    with pytest.raises(RuntimeError, match='唯一对齐'):
        planner.settle_round_resource_observation(
            occurrence, observation=observation,
            history=[dict(effect, action_id='a'), dict(effect, action_id='b')],
            facts=dict(current_round=1, score=20))
    assert not writes


@pytest.mark.parametrize('score_after', [10, 20])
def test_verified_consumption_can_close_observation_even_without_score_gain(monkeypatch, score_after):
    """Zero-score consumption must remain visible without leaving a retryable intent."""
    occurrence = SimpleNamespace(instance_key='today', activity_id=16042901)
    observation = dict(action_id='observation', prior_action_ids=[], instance_key='today',
                       pet_id=7101, item_id=8022005, quantity=1, round=1,
                       score_before=10, receipt_date='2026-10-08')
    effect = dict(action_id='resource', status='verified', pet_id=7101,
                  item_id=8022005, quantity=1, aptitude_delta=1,
                  inventory_before=100, inventory_after=99)
    writes = []
    monkeypatch.setattr(planner, 'record_pet_resource_receipt',
                        lambda *a, **k: writes.append((a, k)))
    result = planner.settle_round_resource_observation(
        occurrence, observation=observation, history=[effect],
        facts=dict(current_round=1, score=score_after,
                   rounds=[dict(cumulative_target=100)], captured_at='now'))
    assert result['status'] == 'round_observation_verified'
    assert result['resource_action_id'] == 'resource'
    assert result['score_after'] == score_after
    assert len(writes) == 1
    assert writes[0][1]['action_id'] == 'observation'


def test_reachable_round_estimate_does_not_double_spend_shared_capacity():
    facts = dict(score=0, rounds=[
        dict(round=1, cumulative_target=600, tasks_claimed=False, box_claimed=False),
        dict(round=2, cumulative_target=1600, tasks_claimed=False, box_claimed=False)])
    history = [dict(action_id='used', status='verified', base_total=2, aptitude_delta=2),
               dict(status='round_observation_verified', instance_key='today', pet_id=7101,
                    resource_action_id='used', item_id=8022005, quantity=2,
                    score_before=0, score_after=20, score_is_lower_bound=False)]
    result = planner.assess_round_resource_reachability(
        facts, [resource(8022005), resource(8022004)], {'gift_remaining': {3: 100}},
        history, instance_key='today', pet_id=7101)
    assert sum(r['count'] for r in result['resources']) == 100
    assert result['estimated_final_score'] == 1000
    assert result['estimated_reachable_rounds'] == 1
    assert result['claimed_rounds'] == 0
    assert result['is_guarantee'] is False


def test_unmeasured_resources_do_not_prove_a_reachable_round():
    facts = dict(score=0, rounds=[
        dict(round=1, cumulative_target=600, tasks_claimed=False, box_claimed=False)])
    result = planner.assess_round_resource_reachability(
        facts, [resource()], {'gift_remaining': {3: 100}}, [],
        instance_key='today', pet_id=7101)
    assert result['unmeasured_item_ids'] == [8022005]
    assert result['estimated_reachable_rounds'] == 0
    assert result['is_guarantee'] is False


def test_verified_provider_receipt_with_refund_is_not_an_unsettled_consumption(monkeypatch):
    """Existing pet yield samples use submitted quantity, including refunds."""
    occurrence = SimpleNamespace(instance_key='today', activity_id=16042901)
    observation = dict(action_id='observation', prior_action_ids=[], instance_key='today',
                       pet_id=7101, item_id=8022005, quantity=2, round=1,
                       score_before=10, receipt_date='2026-10-08')
    effect = dict(action_id='resource', status='verified', pet_id=7101,
                  item_id=8022005, quantity=2, aptitude_delta=2,
                  inventory_before=5, inventory_after=4)
    monkeypatch.setattr(planner, 'record_pet_resource_receipt', lambda *a, **k: None)
    result = planner.settle_round_resource_observation(
        occurrence, observation=observation, history=[effect],
        facts=dict(current_round=1, score=30,
                   rounds=[dict(cumulative_target=100)], captured_at='now'))
    assert result['status'] == 'round_observation_verified'
    assert result['quantity'] == 2
    assert result['resource_action_id'] == 'resource'
