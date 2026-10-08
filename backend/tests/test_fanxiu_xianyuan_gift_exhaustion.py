import pytest

from backend.core.fanxiu.data_annotation.tasks.xianyuan_auto_gift import plan_xianyuan_gift_batch


@pytest.mark.parametrize(('reward_state', 'action'), [
    (0, 'inventory_exhausted'),
    (1, 'claim_rewards'),
    (2, 'inventory_exhausted'),
])
def test_empty_gift_inventory_claims_existing_rewards_but_never_resets(reward_state, action):
    item = {'base_id': 37092001, 'quantity': 0,
            'eligible_targets': [{'npc_id': 4005, 'runtime_index': 350}]}
    person = {'npc_id': 4005, 'runtime_index': 350, 'giftable': True,
              'gift_options': [{'item_id': 37092001, 'favorability': 100}],
              'rewards': [{'level': 12, 'state': reward_state}],
              'favor_thresholds': [{'level': 12, 'favor': 12000, 'reset_favor': 12000}]}
    result = plan_xianyuan_gift_batch(item, person)
    assert result['action'] == action
    assert result['quantity'] == 0
