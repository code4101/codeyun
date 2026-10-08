import pytest

from backend.core.fanxiu.data_annotation.tasks.xianyuan_auto_gift import plan_xianyuan_gift_batch, verify_xianyuan_gift_effect as verify_effect


def inventory_evidence(observed=1):
    return {'pid': 1, 'process_start_ticks': 2, 'requested_item_ids': [37092001],
            'source': 'BackpackMgr.Model.BackpackData.ItemVoDic', 'read_only': True,
            'observed_at': observed, 'completed_at': observed + .1}


def verify_xianyuan_gift_effect(*args, **kwargs):
    kwargs.setdefault('inventory_before_evidence', inventory_evidence())
    kwargs.setdefault('inventory_after_evidence', inventory_evidence(2))
    return verify_effect(*args, **kwargs)


def facts():
    item = {'base_id': 37092001, 'quantity': 1111,
            'eligible_targets': [{'npc_id': 4005, 'runtime_index': 350}]}
    person = {'npc_id': 4005, 'runtime_index': 350, 'giftable': True, 'hostile': False,
              'favor': 12000, 'favor_level': 12, 'reset_favor': 4000, 'reset_favor_level': 4,
              'gift_options': [{'item_id': 37092001, 'favorability': 100,
                                'career_conditional': False, 'activity_gift': False}],
              'favor_thresholds': [{'level': n, 'favor': 1000*n, 'reset_favor': 1000*n}
                                   for n in range(1, 13)],
              'rewards': [{'level': n, 'state': 2 if n <= 4 else 0} for n in range(1, 13)]}
    return item, person


def test_permanent_cap_does_not_hide_remaining_reset_capacity():
    item, person = facts()
    result = plan_xianyuan_gift_batch(item, person)
    assert result['progress_track'] == 'reset_favor'
    assert result['quantity'] == 80
    assert result['expected_favor_after'] == 12000


def test_zero_reset_level_after_completed_cycle_still_uses_reset_track():
    item, person = facts()
    person.update(reset_favor=0, reset_favor_level=0)
    result = plan_xianyuan_gift_batch(item, person)
    assert result['progress_track'] == 'reset_favor'
    assert result['quantity'] == 120
    item['quantity'] = 3
    assert plan_xianyuan_gift_batch(item, person)['quantity'] == 3


def test_claim_and_reset_gates_prevent_more_gifts():
    item, person = facts()
    person['rewards'][4]['state'] = 1
    assert plan_xianyuan_gift_batch(item, person)['action'] == 'claim_rewards'
    for reward in person['rewards']:
        reward['state'] = 2
    assert plan_xianyuan_gift_batch(item, person)['action'] == 'reset_required'


def test_round_up_can_exceed_reward_target_below_authoritative_track_cap():
    item, person = facts()
    person['reset_favor'] = 4055
    person['rewards'] = [{'level': 5, 'state': 0}]
    result = plan_xianyuan_gift_batch(item, person)
    assert result['quantity'] == 10
    assert result['target_favor'] == 5000
    assert result['track_cap'] == 12000
    assert result['planned_favor_gain'] == 1000
    assert result['expected_favor_after'] == 5055


def test_identity_conditions_and_inconsistent_progress_fail_closed():
    item, person = facts()
    person['runtime_index'] = 351
    with pytest.raises(ValueError, match='身份'):
        plan_xianyuan_gift_batch(item, person)
    person['runtime_index'] = 350
    person['gift_options'][0]['career_conditional'] = True
    with pytest.raises(ValueError, match='资格'):
        plan_xianyuan_gift_batch(item, person)
    person['gift_options'][0]['career_conditional'] = False
    person['reset_favor'] = 12000
    with pytest.raises(ValueError, match='领取状态'):
        plan_xianyuan_gift_batch(item, person)


def test_effect_requires_exact_process_recipient_favor_and_inventory():
    from copy import deepcopy
    item, person = facts()
    batch = plan_xianyuan_gift_batch(item, person)
    before = {'runtime_complete': True, 'runtime_updated_at': 1, 'runtime_debug': {'pid': 1, 'process_start_ticks': 2}, 'people': [person]}
    after = deepcopy(before)
    after['runtime_updated_at'] = 2
    after['people'][0]['reset_favor'] = 12000
    result = verify_xianyuan_gift_effect(batch, before, after, inventory_before=1111, inventory_after=1031)
    assert result['favor_delta'] == 8000
    for alteration, reason in [
        (lambda x: x['runtime_debug'].update(process_start_ticks=3), '进程'),
        (lambda x: x['people'][0].update(runtime_index=351), '人物'),
        (lambda x: x['people'][0].update(reset_favor=4000), '好感'),
    ]:
        changed = deepcopy(after)
        alteration(changed)
        with pytest.raises(RuntimeError, match=reason):
            verify_xianyuan_gift_effect(batch, before, changed, inventory_before=1111, inventory_after=1031)
    with pytest.raises(RuntimeError, match='库存'):
        verify_xianyuan_gift_effect(batch, before, after, inventory_before=1111, inventory_after=1111)
    wrong_source = inventory_evidence(2)
    wrong_source['pid'] = 9
    with pytest.raises(RuntimeError, match='库存来源'):
        verify_xianyuan_gift_effect(batch, before, after, inventory_before=1111, inventory_after=1031,
                                   inventory_after_evidence=wrong_source)
