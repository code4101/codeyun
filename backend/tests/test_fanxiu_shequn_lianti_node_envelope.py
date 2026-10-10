"""Pure safety envelope contracts; no game/UI substitute or consumption."""
import copy
import pytest

from backend.core.fanxiu.activity.shequn_lianti import (
    build_shequn_lianti_node_gain_bound, plan_shequn_lianti_normal_attempt_envelope,
)


def observations():
    evidence = {'pid': 2688, 'process_start_ticks': 5832}
    membership = {'complete': True, 'client_active_resource_ids': [220007], 'evidence': evidence}
    definitions = {'complete': True, 'text_complete': True, 'evidence': evidence,
                   'resources': [{'id': 220007, 'effects': 22007, 'tip_text': '神魄秘箓：额外提升7层淬体层数'}],
                   'effects': [{'id': 22007, 'type': 35, 'params': '10_7'}]}
    return membership, definitions


def cost_facts(start=219, count=100):
    dialog = dict(base_id=910515, current_node=start, stage=55, maximum_nodes=512,
                  normal_item_id=5030001, breakthrough_item_id=5030002,
                  evidence={'pid': 2688, 'process_start_ticks': 5832})
    costs = dict(complete=True, base_id=910515, evidence=dialog['evidence'],
                 cost_nodes=[dict(node_id=node, item_id=5030002 if node == 300 else 5030001,
                                  quantity=2 if node == 300 else 12, required_stage=55)
                             for node in range(start + 1, start + count + 1)])
    return dialog, costs


def test_conservative_gain_is_not_the_observed_seventeen():
    bound = build_shequn_lianti_node_gain_bound(*observations())
    assert bound['max_nodes_per_paid_attempt'] == 8
    dialog, costs = cost_facts()
    plan = plan_shequn_lianti_normal_attempt_envelope(dialog, costs, bound, maximum_attempts=10)
    assert (plan['selected_attempts'], plan['max_final_node'], plan['normal_material_cost']) == (10, 299, 120)


def test_reduces_attempts_using_the_full_bonus_envelope():
    bound = build_shequn_lianti_node_gain_bound(*observations())
    dialog, costs = cost_facts(start=236)
    plan = plan_shequn_lianti_normal_attempt_envelope(dialog, costs, bound, maximum_attempts=10)
    assert (plan['selected_attempts'], plan['max_final_node']) == (7, 292)
    dialog, costs = cost_facts(start=292)
    assert plan_shequn_lianti_normal_attempt_envelope(dialog, costs, bound, maximum_attempts=10)['kind'] == 'blocked'


def test_price_change_after_paid_prefix_still_blocks_crossing():
    bound = build_shequn_lianti_node_gain_bound(*observations())
    dialog, costs = cost_facts()
    costs['cost_nodes'][15]['quantity'] = 16
    # A ten-node paid prefix looks uniform but its reachable envelope is not.
    assert plan_shequn_lianti_normal_attempt_envelope(dialog, costs, bound, maximum_attempts=10)['selected_attempts'] == 1


@pytest.mark.parametrize('fault', ['recursive', 'mixed_effect', 'missing_text', 'wrong_process'])
def test_rejects_unknown_effect_or_identity(fault):
    membership, definitions = copy.deepcopy(observations())
    if fault == 'recursive':
        definitions['effects'][0]['params'] = '7_7'
    elif fault == 'mixed_effect':
        definitions['resources'].append({'id': 12, 'effects': 13, 'tip_text': '淬体时额外提升1层'})
        definitions['effects'].append({'id': 13, 'type': 999, 'params': '1'})
        membership['client_active_resource_ids'].append(12)
    elif fault == 'missing_text':
        definitions['text_complete'] = False
    else:
        definitions['evidence'] = {'pid': 99, 'process_start_ticks': 1}
    with pytest.raises(ValueError):
        build_shequn_lianti_node_gain_bound(membership, definitions)
