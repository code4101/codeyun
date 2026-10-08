from types import SimpleNamespace

from backend.core.fanxiu.activity.lingchong_round_resource_planning import (
    plan_round_resource_batch, round_resource_samples, assess_round_resource_reachability,
)


def test_round_two_uses_cumulative_target_and_ceil_with_own_sample():
    facts = {'all_claimed': False, 'current_round': 2, 'score': 11500001,
             'rounds': [{}, {'cumulative_target': 23370500, 'target': 11870500}]}
    item = SimpleNamespace(item_id=8022005, name='凡品灵力绒', quality=2,
                           sort_order=3, count=30000, aptitude_gain_by_gift_id={3: 1})
    result = plan_round_resource_batch(facts, [item], {'gift_remaining': {3: 30000}},
        {8022005: {'score_gain': 500, 'aptitude_gain': 1}})
    assert result['quantity'] == 23741
    assert result['target'] == 23370500


def test_unknown_resource_probes_one_and_never_replays_seed_allowance():
    facts = {'all_claimed': False, 'current_round': 1, 'score': 0,
             'rounds': [{'cumulative_target': 11500000}]}
    seed = SimpleNamespace(item_id=8022009, name='兽神饲灵丸', quality=7,
                            sort_order=9, count=316, aptitude_gain_by_gift_id={4: 100})
    item = SimpleNamespace(item_id=8022005, name='凡品灵力绒', quality=2,
                            sort_order=3, count=9602, aptitude_gain_by_gift_id={3: 1})
    result = plan_round_resource_batch(facts, [seed, item], {'gift_remaining': {3: 3, 4: 99999}}, {})
    assert result['item_id'] == 8022005
    assert result['quantity'] == 1


def test_old_period_and_clamped_score_cannot_replace_real_score_sample():
    base = {'status': 'round_observation_verified', 'instance_key': 'today', 'pet_id': 7101,
            'item_id': 8022005, 'quantity': 1, 'score_before': 0, 'score_after': 500, 'aptitude_delta': 1}
    rows = [base, {**base, 'instance_key': 'yesterday', 'score_after': 9999},
            {**base, 'score_is_lower_bound': True, 'score_after': 10},
            {**base, 'pet_id': 7102, 'score_after': 8888}]
    assert round_resource_samples(rows, instance_key='today', pet_id=7101) == {
        8022005: {'score_gain': 500, 'aptitude_gain': 1}}


def test_reachability_does_not_double_spend_shared_pet_capacity():
    resources = [SimpleNamespace(item_id=i, name=str(i), quality=2, sort_order=i,
        count=10, aptitude_gain_by_gift_id={3: 1}) for i in [8022005, 8022004]]
    facts = {'score': 0, 'rounds': [
        {'round': 1, 'cumulative_target': 5000, 'tasks_claimed': False, 'box_claimed': False},
        {'round': 2, 'cumulative_target': 6000, 'tasks_claimed': False, 'box_claimed': False}]}
    history = [{'action_id': 'use', 'status': 'verified', 'base_total': 10, 'aptitude_delta': 10},
        {'action_id': 'score', 'status': 'round_observation_verified', 'instance_key': 'today',
         'pet_id': 7101, 'resource_action_id': 'use', 'item_id': 8022005, 'quantity': 10,
         'score_before': 0, 'score_after': 5000}]
    result = assess_round_resource_reachability(facts, resources,
        {'gift_remaining': {3: 10}}, history, instance_key='today', pet_id=7101)
    assert result['estimated_final_score'] == 5000
    assert result['estimated_reachable_rounds'] == 1
    assert result['claimed_rounds'] == 0
    assert result['is_guarantee'] is False
