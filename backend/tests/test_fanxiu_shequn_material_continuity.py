"""Pure receipt arithmetic and provenance contracts; no simulated game."""
from copy import deepcopy

from backend.core.fanxiu.activity.shequn_lianti_receipts import project_shequn_lianti_material_continuity


def boundaries():
    provenance = {'pid': 12, 'process_start_ticks': 34, 'force_refresh': True,
                  'source': 'BackpackMgr.Model.BackpackData.ItemVoDic'}
    dialog = {'normal_item_id': 1, 'breakthrough_item_id': 2}
    return [
        {'action_id': 'first', 'status': 'verified', 'dialog_before': dialog,
         'last_observation': {'inventory_count': 500, 'breakthrough_inventory_count': 38,
                              'inventory_evidence': {**provenance, 'completed_at': 100}}},
        {'action_id': 'second', 'status': 'verified', 'dialog_before': dialog,
         'inventory_before': {'counts': {'1': 420, '2': 36},
                              'evidence': {**provenance, 'observed_at': 200}}}]


def test_verified_gap_retains_both_identities_without_attribution_or_mutation():
    rows = boundaries()
    original = deepcopy(rows)
    gap = project_shequn_lianti_material_continuity(rows)['verified_nonzero_gaps'][0]
    assert gap['net_decrease'] == {'1': 80, '2': 2}
    assert (gap['previous_action_id'], gap['next_action_id']) == ('first', 'second')
    assert gap['cause_verified'] is False and gap['replay_allowed'] is False
    assert rows == original


def test_legacy_process_or_time_mismatch_never_establishes_a_gap():
    for change in ('legacy', 'pid', 'time', 'missing_time', 'infinite_time', 'identity_float'):
        rows = boundaries()
        evidence = rows[1]['inventory_before']['evidence']
        if change == 'legacy':
            rows[0]['last_observation'].pop('breakthrough_inventory_count')
        elif change == 'pid':
            evidence['pid'] = 99
        elif change == 'time':
            evidence['observed_at'] = 90
        elif change == 'missing_time':
            evidence.pop('observed_at')
        elif change == 'infinite_time':
            evidence['observed_at'] = float('inf')
        else:
            evidence['pid'] = 12.0
        result = project_shequn_lianti_material_continuity(rows)
        assert result['verified_nonzero_gaps'] == []
        assert result['unverified_boundary_count'] == 1
        assert result['boundaries'][0]['net_decrease'] is None


def test_stock_gain_is_a_net_gap_without_inferred_refund():
    rows = boundaries()
    rows[1]['inventory_before']['counts'] = {'1': 560, '2': 38}
    gap = project_shequn_lianti_material_continuity(rows)['verified_nonzero_gaps'][0]
    assert gap['net_decrease'] == {'1': -60, '2': 0}
    assert gap['cause_verified'] is False
