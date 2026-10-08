"""Pure identity/epoch association; real claiming requires live Task evidence."""
from copy import deepcopy

import pytest

from backend.core.fanxiu.activity.xianyuan_gift_receipts import recovered_npc_reward_receipt


def observation(pid, timestamp, state, favor=12000):
    return {'runtime_complete': True, 'runtime_updated_at': timestamp,
            'runtime_debug': {'pid': pid, 'process_start_ticks': pid * 10},
            'people': [{'npc_id': 4005, 'reset_favor': favor,
                        'rewards': [{'reward_key': 3890, 'state': state}]}]}


def data():
    return ([{'action_id': 'original', 'status': 'verified',
              'batch': {'npc_id': 4005, 'progress_track': 'reset_favor'},
              'after': observation(1, 10, 1)}],
            observation(2, 20, 1), observation(2, 21, 2))


def test_recovery_links_original_effect_across_process_restart():
    actions, before, after = data()
    original = deepcopy(actions)
    receipt = recovered_npc_reward_receipt(actions, before, after)
    assert receipt['action_id'] == 'original'
    assert receipt['eligible_reward_keys'] == receipt['claimed_reward_keys'] == [3890]
    assert receipt['claimable_after'] == []
    assert receipt['original_gift_process']['pid'] == 1
    assert receipt['evidence']['pid'] == 2
    assert actions == original


@pytest.mark.parametrize('problem', ['pending', 'new_cycle', 'not_claimed', 'different_process', 'stale_after'])
def test_recovery_rejects_unproved_association(problem):
    actions, before, after = data()
    if problem == 'pending':
        actions[0]['status'] = 'pending'
    elif problem == 'new_cycle':
        before['people'][0]['reset_favor'] = 0
    elif problem == 'not_claimed':
        after['people'][0]['rewards'][0]['state'] = 1
    elif problem == 'different_process':
        after['runtime_debug']['pid'] = 3
    else:
        after['runtime_updated_at'] = 19
    with pytest.raises(ValueError):
        recovered_npc_reward_receipt(actions, before, after)


def test_recovery_only_associates_latest_gift_for_this_npc():
    actions, before, after = data()
    latest = deepcopy(actions[0])
    latest['action_id'] = 'latest'
    actions.append(latest)
    assert recovered_npc_reward_receipt(actions, before, after)['action_id'] == 'latest'
