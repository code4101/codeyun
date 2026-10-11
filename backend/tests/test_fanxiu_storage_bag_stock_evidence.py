"""Pure count/provenance arithmetic, not simulated game acceptance."""
from copy import deepcopy
import pytest

from backend.core.fanxiu.data_annotation.tasks.storage_bag_stock_evidence import (
    project_storage_bag_new_stock_evidence,
)


def snapshot(stamp, *quantities):
    return {'captured_at_epoch': stamp, 'complete': True,
            'source': 'active_backpack_panel_item_info_list',
            'tab': {'param': 0, 'number': 1},
            'evidence': {'pid': 2688, 'process_start_ticks': 5832},
            'items': [{'base_id': base_id, 'num': quantity} for base_id, quantity in quantities]}


def remaining(base_id):
    return {'base_id': base_id, 'quantity': 1, 'disposition': 'action'}


def test_net_gain_requires_current_actionable_plan_and_preserves_inputs():
    before = snapshot(100, (1, 20), (1, 30))
    after = snapshot(110, (1, 55), (2, 8))
    original = deepcopy((before, after))
    result = project_storage_bag_new_stock_evidence(before, after, [remaining(1)])
    assert result['observation_verified'] and result['new_actionable_stock_verified']
    assert result['net_gains'] == [
        {'base_id': 1, 'before_quantity': 50, 'after_quantity': 55, 'net_gain': 5},
        {'base_id': 2, 'before_quantity': 0, 'after_quantity': 8, 'net_gain': 8}]
    assert result['new_actionable_stock'] == result['net_gains'][:1]
    assert not result['consumption_authorized'] and (before, after) == original


def test_stack_reorganization_and_old_remaining_stock_do_not_prove_new_drop():
    result = project_storage_bag_new_stock_evidence(
        snapshot(100, (1, 50)), snapshot(110, (1, 20), (1, 30)), [remaining(1)])
    assert result['observation_verified'] and not result['new_actionable_stock_verified']
    assert result['net_gains'] == []


@pytest.mark.parametrize('change,reason', [
    ({'tab': {'param': 1, 'number': 2}}, 'full_inventory_scope_unverified'),
    ({'tab': {'param': 0}}, 'full_inventory_scope_unverified'),
    ({'tab': None}, 'full_inventory_scope_unverified'),
    ({'tab': {'param': False, 'number': 1}}, 'full_inventory_scope_unverified'),
    ({'evidence': {'pid': 2689, 'process_start_ticks': 5832}}, 'inventory_process_changed'),
    ({'evidence': {'pid': 2688, 'process_start_ticks': 5833}}, 'inventory_process_changed'),
    ({'evidence': {'pid': 2688}}, 'unbound_inventory_observation'),
    ({'pid': 2689}, 'invalid_inventory_observation'),
    ({'complete': False}, 'unbound_inventory_observation'),
    ({'source': 'catalog'}, 'unbound_inventory_observation'),
    ({'captured_at_epoch': 100}, 'inventory_observation_not_newer'),
    ({'captured_at_epoch': 99}, 'inventory_observation_not_newer'),
    ({'captured_at_epoch': float('inf')}, 'unbound_inventory_observation'),
    ({'captured_at_epoch': float('nan')}, 'unbound_inventory_observation'),
    ({'items': [{'base_id': 1, 'num': 1.5}]}, 'invalid_inventory_observation'),
])
def test_uncertain_provenance_never_proves_new_stock(change, reason):
    after = snapshot(110, (1, 55)); after.update(change)
    result = project_storage_bag_new_stock_evidence(snapshot(100, (1, 50)), after, [remaining(1)])
    assert result['reason'] == reason
    assert not result['observation_verified'] and not result['new_actionable_stock_verified']
    assert result['net_gains'] == []
