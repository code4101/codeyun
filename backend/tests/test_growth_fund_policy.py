"""纯策略契约；不模拟游戏流程，真实 GUI 验收另由 Kernel 执行。"""
import pytest
from backend.core.fanxiu.instrumentation.growth_fund import parse_fund_reward
from backend.core.fanxiu.data_annotation.tasks.growth_fund_policy import (
    plan_growth_funds, reward_prayer_categories,
)


def test_reward_payload_must_be_completely_understood():
    assert parse_fund_reward('Item|1_2,item|3_4') == [
        {'item_id': 1, 'amount': 2}, {'item_id': 3, 'amount': 4}]
    for text in ('Item|1_0', 'Item|1_2,Pay|3_4', '', 'Item|1_2junk'):
        with pytest.raises(ValueError):
            parse_fund_reward(text)


def test_boxes_keep_the_contained_prayer_resource():
    assert reward_prayer_categories({'item_name': '珍品淬体宝匣',
        'description': '装有淬体精魄的宝匣'}) == {'淬体'}
    assert reward_prayer_categories({'item_name': '灵石'}) == set()
    with pytest.raises(ValueError):
        reward_prayer_categories({'item_name': ''})


def test_paid_track_requires_existing_purchase_and_claimed_ids_are_idempotent():
    state = {'categories': [{'id': 1}], 'funds': [
        {'fundId': 7, 'type': 1, 'sort': 1, 'claimable': True}],
        'bought': [], 'claimed_free': [], 'claimed_paid': [],
        'rewards': [{'id': 9, 'fundId': 7, 'free': [{'item_id': 1}], 'paid': [{'item_id': 2}]}],
        'items': {1: {'item_name': '灵石'}, 2: {'item_name': '洗灵奇石'}}}
    assert len(plan_growth_funds(state, '炼丹')['claim']) == 1
    state['bought'] = [7]
    assert len(plan_growth_funds(state, '炼丹')['mixed']) == 1
    assert len(plan_growth_funds(state, '洗灵')['claim']) == 1
    state['claimed_free'] = [9]
    assert len(plan_growth_funds(state, '炼丹')['deferred']) == 1
    state['claimed_paid'] = [9]
    state['funds'][0]['claimable'] = False
    assert plan_growth_funds(state, '洗灵') == {'claim': [], 'deferred': [], 'mixed': []}
