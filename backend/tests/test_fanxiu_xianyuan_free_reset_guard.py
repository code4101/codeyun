import time

import pytest

from backend.core.fanxiu.instrumentation.xianyuan_gift_page import require_free_single_step_npc_reset


def facts():
    process = {'pid': 21822, 'process_start_ticks': 12589765}
    bag = {'complete': True, 'npc_id': 4005, 'reset_steps': [1],
           'remaining_reset_rewards': 0, 'reset_cost_quantity': 0,
           'is_enough': True, 'captured_at': time.time(), 'evidence': dict(process)}
    person = {'npc_id': 4005, 'rewards': [{'state': 2}]}
    return bag, person, process


def test_verified_free_fully_claimed_single_stage_reset_is_allowed():
    bag, person, process = facts()
    assert require_free_single_step_npc_reset(bag, person, expected_process=process) == bag


@pytest.mark.parametrize('change', ['paid', 'unclaimed', 'recipient', 'process', 'stale', 'multiple_steps'])
def test_changed_reset_scope_or_unclaimed_rewards_are_rejected(change):
    bag, person, process = facts()
    if change == 'paid':
        bag['reset_cost_quantity'] = 1
    elif change == 'unclaimed':
        person['rewards'][0]['state'] = 1
    elif change == 'recipient':
        bag['npc_id'] = 4013
    elif change == 'process':
        bag['evidence']['process_start_ticks'] += 1
    elif change == 'stale':
        bag['captured_at'] -= 10
    else:
        bag['reset_steps'] = [1, 2]
    with pytest.raises(RuntimeError):
        require_free_single_step_npc_reset(bag, person, expected_process=process)
