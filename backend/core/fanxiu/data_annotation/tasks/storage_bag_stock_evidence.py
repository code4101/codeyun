"""Net new stock observed within one real bag operation, never an action gate.

Counts are summed by base ID so stack split/merge cannot invent a drop. A
positive net change proves acquisition between observations, not its cause or
gross reward quantity when other actions also consumed that item. Only items
in the post-state's validated actionable plan qualify as follow-up candidates.
Missing provenance stays unverified; existing scheduling remains independent.
"""
from collections.abc import Mapping, Sequence
import math

from backend.core.fanxiu.storage_bag_unknown_research import (
    project_storage_bag_inventory_observation,
)


def _counts(snapshot):
    counts = {}
    for item in snapshot['items']:
        if item.get('is_padding'):
            continue
        base_id, quantity = item.get('base_id'), item.get('num')
        if type(base_id) is not int or base_id <= 0 or type(quantity) is not int or quantity < 0:
            raise ValueError('Invalid inventory count')
        counts[base_id] = counts.get(base_id, 0) + quantity
    return counts


def project_storage_bag_new_stock_evidence(
    before: Mapping, after: Mapping, remaining_items: Sequence[Mapping],
) -> dict:
    """Compare original full UI snapshots; never reconstruct or consume stock.

    The enclosing formal Job receipt owns task/attempt identity. This projection
    retains both original process/time/fingerprint observations. It cannot
    prove a minute continuation ran; that needs the subsequent formal attempt.
    """
    result = {'read_only': True, 'observation_verified': False,
              'new_actionable_stock_verified': False, 'net_gains': [],
              'new_actionable_stock': [], 'consumption_authorized': False}
    # complete means the active category is complete, not necessarily the bag.
    # Both observations must be the native all-items category (param=0/tabNum=1).
    scopes = [snapshot.get('tab') for snapshot in (before, after)]
    result.update(before_scope=scopes[0], after_scope=scopes[1])
    if any(not isinstance(scope, Mapping)
           or type(scope.get('param')) is not int or scope['param'] != 0
           or type(scope.get('number')) is not int or scope['number'] != 1
           for scope in scopes):
        return {**result, 'reason': 'full_inventory_scope_unverified'}
    try:
        opening, closing = _counts(before), _counts(after)
        first = project_storage_bag_inventory_observation(before)
        last = project_storage_bag_inventory_observation(after)
        result.update(before_observation=first, after_observation=last)
    except (KeyError, TypeError, ValueError, AttributeError):
        return {**result, 'reason': 'invalid_inventory_observation'}
    for observation in (first, last):
        stamp = observation['captured_at_epoch']
        if (observation['complete'] is not True
                or observation['source'] != 'active_backpack_panel_item_info_list'
                or any(type(observation[k]) is not int or observation[k] <= 0
                       for k in ('pid', 'process_start_ticks'))
                or type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= 0):
            return {**result, 'reason': 'unbound_inventory_observation'}
    if any(first[k] != last[k] for k in ('pid', 'process_start_ticks')):
        return {**result, 'reason': 'inventory_process_changed'}
    if first['captured_at_epoch'] >= last['captured_at_epoch']:
        return {**result, 'reason': 'inventory_observation_not_newer'}
    gains = [{'base_id': base_id, 'before_quantity': opening.get(base_id, 0),
              'after_quantity': closing[base_id],
              'net_gain': closing[base_id] - opening.get(base_id, 0)}
             for base_id in sorted(closing) if closing[base_id] > opening.get(base_id, 0)]
    actionable = {item['base_id'] for item in remaining_items
                  if type(item.get('base_id')) is int and item.get('quantity', 0) > 0
                  and item.get('disposition') == 'action'}
    candidates = [gain for gain in gains if gain['base_id'] in actionable]
    return {**result, 'observation_verified': True, 'reason': 'net_inventory_comparison',
            'net_gains': gains, 'new_actionable_stock': candidates,
            'new_actionable_stock_verified': bool(candidates)}
