"""Pure reward-protocol comparison; no game interaction or simulated Runtime."""
from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks.prayer_box_experiment import (
    validate_prayer_box_runtime_graph_alignment,
)


def source_cards():
    return {
        '19070135': dict(id=19070135, name='玄魄宝匣', type=2, sub_type=1,
                        effect_value='810210_810210', effect_description='打开后获得以下道具',
                        optional_gift_rewards=[dict(id=5030001, count=15, show_condition=None)]),
        '5030001': dict(id=5030001, name='淬体精魄', type=13, sub_type=1, effect_value='1'),
    }


def current_graph():
    cards = source_cards()
    cards['19070135']['optional_gift_rewards'][0]['show_condition'] = ''
    return dict(complete=True, requested_ids=[19070135], cards_by_id=cards)


def test_display_metadata_and_empty_condition_formats_do_not_change_protocol():
    cards = source_cards()
    cards['19070135']['icon'] = 'catalog_display_only'
    validate_prayer_box_runtime_graph_alignment(19070135, cards, current_graph())


def test_ordinary_leaf_omitted_zero_subtype_keeps_exact_nonzero_gate():
    cards = source_cards()
    cards['19070135']['optional_gift_rewards'].append(dict(id=8022001, count=6))
    cards['8022001'] = dict(id=8022001, name='珍品饲灵丸', type=28,
                            sub_type=None, effect_value='1:4,2:4,3:2')
    graph = dict(complete=True, requested_ids=[19070135], cards_by_id=deepcopy(cards))
    graph['cards_by_id']['8022001']['sub_type'] = 0
    validate_prayer_box_runtime_graph_alignment(19070135, cards, graph)
    graph['cards_by_id']['8022001']['sub_type'] = 1
    with pytest.raises(ValueError):
        validate_prayer_box_runtime_graph_alignment(19070135, cards, graph)


@pytest.mark.parametrize('change', ['count', 'condition', 'effect', 'leaf', 'incomplete', 'purpose'])
def test_changed_current_reward_protocol_rejects_consumption(change):
    graph = deepcopy(current_graph())
    root = graph['cards_by_id']['19070135']
    if change == 'count':
        root['optional_gift_rewards'][0]['count'] = 16
    elif change == 'condition':
        root['optional_gift_rewards'][0]['show_condition'] = 'WorldLevel|100'
    elif change == 'effect':
        root['effect_value'] = '810210_999999'
    elif change == 'leaf':
        del graph['cards_by_id']['5030001']
    elif change == 'incomplete':
        graph['complete'] = False
    else:
        graph['cards_by_id']['5030001']['name'] = '洗灵材料'
    with pytest.raises(ValueError):
        validate_prayer_box_runtime_graph_alignment(19070135, source_cards(), graph)


def test_declared_non_prayer_bonus_remains_in_fixed_reward_attribution():
    from backend.core.fanxiu.storage_bag_prayer_research import (
        classify_prayer_box, validate_fixed_box_reward_attribution,
    )
    cards = source_cards()
    cards['19070135']['optional_gift_rewards'].append(dict(id=5050001, count=1))
    cards['5050001'] = dict(id=5050001, name='惊蛰灵魄', type=13, sub_type=1)
    rule = classify_prayer_box(cards['19070135'], cards)
    assert rule['resource_categories'] == ['淬体']
    assert {x['item_id'] for x in rule['reward_leaves']} == {5030001}
    assert {x['item_id'] for x in rule['reward_attribution_leaves']} == {5030001, 5050001}
    experiment = dict(catalog_fingerprint=rule['catalog_fingerprint'], opened_count=1,
                      rewards=[dict(item_id=5030001, quantity=15), dict(item_id=5050001, quantity=1)])
    validate_fixed_box_reward_attribution(experiment, rule)
    experiment['rewards'][1]['quantity'] = 2
    with pytest.raises(ValueError):
        validate_fixed_box_reward_attribution(experiment, rule)
