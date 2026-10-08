"""积分轮次使用独立样本；资源回执负责消费，积分观察负责预算。"""
from math import ceil
from datetime import datetime

from .pet_resource_planning import order_pet_resources_low_to_high, plan_pet_probe_bulk_tail
from .pet_resource_receipts import read_pet_resource_receipts_range, record_pet_resource_receipt
from .pet_rank_estimation import estimate_pet_rank_inventory


def assess_round_resource_reachability(facts, resources, pet, history, *, instance_key, pet_id):
    """Estimate affordable rounds with shared gift capacity and explicit unknowns.

    This reuses the estimator without opponent ranks. Unknown items inherit only
    a labelled pooled estimate; execution still probes each item independently.
    Shared remaining aptitude is deducted in low-to-high order so two resources
    cannot both budget the same pet capacity. The estimate never proves a round
    or its reward complete; those require the Quest and receiveBoxList receipts.
    """
    verified = {r['action_id']: r for r in history if r.get('status') == 'verified'}
    samples = []
    for row in history:
        source = verified.get(row.get('resource_action_id'))
        if (row.get('status') == 'round_observation_verified' and row.get('instance_key') == instance_key
                and row.get('pet_id') == pet_id and not row.get('score_is_lower_bound') and source):
            samples.append({'item_id': row['item_id'], 'quantity': row['quantity'],
                'rank_delta': row['score_after'] - row['score_before'], 'base_total': source.get('base_total'),
                'aptitude_delta': source['aptitude_delta']})
    aptitude_rate = max([1.0, *(s['aptitude_delta'] / s['base_total'] for s in samples if s.get('base_total'))])
    capacity = {int(k): int(v) for k, v in pet['gift_remaining'].items()}
    rows = []
    for item in order_pet_resources_low_to_high(resources):
        if item.item_id == 8022009 or not item.count:
            continue
        gains = {int(k): int(v) for k, v in item.aptitude_gain_by_gift_id.items()
                 if int(v) > 0 and capacity.get(int(k), 0) > 0}
        if not gains:
            continue
        # Budget only units whose still-live components all have capacity.
        count = min(item.count, *(int(capacity[k] / (v * aptitude_rate)) for k, v in gains.items()))
        if count <= 0:
            continue
        rows.append({'item_id': item.item_id, 'item_name': item.name, 'count': count,
                     'base_gain': sum(gains.values())})
        for k, v in gains.items():
            capacity[k] = max(0, capacity[k] - ceil(count * v * aptitude_rate))
    estimate = estimate_pet_rank_inventory(rows, samples)
    potential = facts['score'] + estimate['estimated_gain']
    feasible = [r['round'] for r in facts['rounds'] if r['cumulative_target'] <= potential]
    return {'score': facts['score'], 'estimated_final_score': potential,
            'estimated_reachable_rounds': max(feasible, default=0),
            'claimed_rounds': sum(r['tasks_claimed'] and r['box_claimed'] for r in facts['rounds']),
            'is_guarantee': False, 'aptitude_rate_used': aptitude_rate,
            'unmeasured_item_ids': estimate['unmeasured_item_ids'], 'resources': estimate['resources']}


def read_round_resource_history(occurrence):
    return read_pet_resource_receipts_range(occurrence.activity_id,
        start_date=occurrence.start_at.date().isoformat(), end_date=occurrence.end_at.date().isoformat())


def round_resource_samples(history, *, instance_key, pet_id):
    """Only unclamped own-score observations from this exact period are samples."""
    samples = {}
    for row in history:
        if (row.get('status') != 'round_observation_verified'
                or row.get('instance_key') != instance_key or row.get('pet_id') != pet_id
                or row.get('score_is_lower_bound')):
            continue
        quantity = int(row.get('quantity') or 0)
        gain = int(row.get('score_after') or 0) - int(row.get('score_before') or 0)
        if quantity > 0 and gain > 0:
            samples[int(row['item_id'])] = {'score_gain': gain / quantity,
                'aptitude_gain': int(row['aptitude_delta']) / quantity}
    return samples


def plan_round_resource_batch(facts, resources, pet, samples):
    """Plan toward the current cumulative target, bounded by stock and capacity."""
    if facts['all_claimed']:
        return {'quantity': 0, 'mode': 'complete'}
    target = facts['rounds'][facts['current_round'] - 1]['cumulative_target']
    gap = max(0, target - facts['score'])
    if not gap:
        return {'quantity': 0, 'mode': 'rewards_required'}
    remaining = pet['gift_remaining']
    for item in order_pet_resources_low_to_high(resources):
        # The initialization owns the thirty-seed allowance; rounds spend
        # ordinary resources rather than replaying that initialization.
        if item.item_id == 8022009 or item.count <= 0:
            continue
        capacities = {int(k): int(remaining.get(int(k), remaining.get(str(k), 0)))
            for k, v in item.aptitude_gain_by_gift_id.items() if int(v) > 0}
        capacities = {k: v for k, v in capacities.items() if v > 0}
        if not capacities:
            continue
        base = sum(int(v) for k, v in item.aptitude_gain_by_gift_id.items() if int(k) in capacities)
        sample = samples.get(item.item_id)
        cap = ceil(sum(capacities.values()) / sample['aptitude_gain']) if sample else 1
        plan = plan_pet_probe_bulk_tail(gap=gap, available=item.count,
            sample_task_gain=sample['score_gain'] if sample else None, remaining_cap=cap)
        if plan['quantity']:
            return {**plan, 'item_id': item.item_id, 'item_name': item.name,
                    'base_gain': base, 'target': target, 'gap': gap}
    return {'quantity': 0, 'mode': 'resource_exhausted', 'target': target, 'gap': gap}


def begin_round_resource_observation(occurrence, *, pet_id, facts, plan):
    """Persist the pre-use score before the resource provider can submit a click."""
    history = read_round_resource_history(occurrence)
    if any(r.get('status') in {'submitted', 'rank_observation_pending', 'round_observation_pending'} for r in history):
        raise RuntimeError('上一批资源或积分观察未结，禁止新增消费')
    today = datetime.now().astimezone().date().isoformat()
    data = {'status': 'round_observation_pending', 'instance_key': occurrence.instance_key,
        'pet_id': pet_id, 'round': facts['current_round'], 'score_before': facts['score'],
        'item_id': plan['item_id'], 'quantity': plan['quantity'],
        'prior_action_ids': [r['action_id'] for r in history], 'receipt_date': today}
    action_id = record_pet_resource_receipt(occurrence.activity_id, today, data)
    return {**data, 'action_id': action_id}


def settle_round_resource_observation(occurrence, *, observation, history, facts):
    """Verify exactly one new resource effect before attributing a score delta."""
    prior = set(observation['prior_action_ids']) | {observation['action_id']}
    effects = [r for r in history if r['action_id'] not in prior and r.get('status') in {'submitted', 'verified'}]
    if (len(effects) != 1 or effects[0].get('status') != 'verified'
            or any(effects[0].get(k) != observation[k] for k in ['pet_id', 'item_id', 'quantity'])
            or observation['instance_key'] != occurrence.instance_key
            or facts['current_round'] != observation['round'] or facts['score'] < observation['score_before']):
        raise RuntimeError('轮次消费与积分效果未能唯一对齐，保留观察，禁止重复使用')
    receipt = effects[0]
    # Verified submission quantity is the denominator. Inventory is a net
    # observation and may include refunds/rewards; it cannot disprove a settled
    # positive pet effect or authorize replaying the resource action.
    target = facts['rounds'][facts['current_round'] - 1]['cumulative_target']
    result = {**observation, 'status': 'round_observation_verified', 'resource_action_id': receipt['action_id'],
        'score_after': facts['score'], 'score_is_lower_bound': facts['score'] >= target,
        'aptitude_delta': receipt['aptitude_delta'], 'captured_at': facts['captured_at']}
    record_pet_resource_receipt(occurrence.activity_id, observation['receipt_date'], result,
                               action_id=observation['action_id'])
    return result
