"""Occurrence-scoped training receipts; unresolved actions never authorize replay.

The provider owns paths, locking and atomic publication. A receipt proves a
settled node/material effect, separately from aggregate clan score calibration.
Only real Runtime observations supplied by the sole operator belong here.
"""
import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from filelock import FileLock
from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_data_annotation_dir


def _path(occurrence_key, receipt_dir):
    parts = str(occurrence_key).split('|')
    if len(parts) != 4 or any(not part.isdecimal() for part in parts):
        raise ValueError('炼体凭证要求完整活动实例键')
    activity, start, end, cross = map(int, parts)
    if activity != 8043401 or end <= start or start <= 0 or cross != 8:
        raise ValueError('炼体凭证活动身份不符')
    folder = Path(receipt_dir) if receipt_dir is not None else fanxiu_data_annotation_dir() / 'shequn-lianti-receipts'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (hashlib.sha256(occurrence_key.encode()).hexdigest() + '.json')


def _read(path):
    rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if not isinstance(rows, list) or any(
            not isinstance(row, dict) or row.get('status') not in ('pending', 'verified')
            or not isinstance(row.get('dialog_before'), dict)
            or not isinstance(row.get('action_id'), str) or not row['action_id']
            for row in rows):
        raise ValueError('炼体消费凭证损坏')
    if len({row['action_id'] for row in rows}) != len(rows):
        raise ValueError('炼体消费凭证action_id重复')
    return rows


def _save(path, rows):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def read_shequn_lianti_training_receipts(occurrence_key, *, receipt_dir=None):
    """Read this exact period only; unreadable data fails closed."""
    path = _path(occurrence_key, receipt_dir)
    with FileLock(str(path) + '.lock', timeout=5):
        return _read(path)


def project_shequn_lianti_material_continuity(rows):
    """Compare adjacent action boundaries; net gaps never establish a cause.

    Only independent, force-refreshed inventory observations from the same
    process can establish a two-material gap. Older dialog-only baselines stay
    unverified. A negative delta may include rewards, a positive delta may
    include other actions; neither is assigned to a recorded training action.
    This pure projection never modifies old effects or grants replay.
    """
    boundaries = []
    for previous, current in zip(rows, rows[1:]):
        observation = previous.get('last_observation') or {}
        baseline = current.get('inventory_before') or {}
        old_evidence = observation.get('inventory_evidence') or {}
        new_evidence = baseline.get('evidence') or {}
        old_dialog = previous['dialog_before']
        new_dialog = current['dialog_before']
        identities = (new_dialog.get('normal_item_id'), new_dialog.get('breakthrough_item_id'))
        same_materials = identities == (old_dialog.get('normal_item_id'), old_dialog.get('breakthrough_item_id'))
        counts = baseline.get('counts') or {}
        before = {str(identities[0]): observation.get('inventory_count'),
                  str(identities[1]): observation.get('breakthrough_inventory_count')}
        after = {str(i): counts.get(str(i), counts.get(i)) for i in identities}
        valid_counts = all(type(v) is int and v >= 0 for v in (*before.values(), *after.values()))
        chronological = (type(old_evidence.get('completed_at')) in (int, float) and
                         type(new_evidence.get('observed_at')) in (int, float) and
                         math.isfinite(old_evidence['completed_at']) and math.isfinite(new_evidence['observed_at']) and
                         0 < old_evidence['completed_at'] <= new_evidence['observed_at'])
        bound = (same_materials and len(set(identities)) == 2 and
                 all(type(i) is int and i > 0 for i in identities) and valid_counts and chronological and
                 previous['status'] == 'verified' and
                 old_evidence.get('force_refresh') is True and new_evidence.get('force_refresh') is True and
                 old_evidence.get('source') == new_evidence.get('source') == 'BackpackMgr.Model.BackpackData.ItemVoDic' and
                 all(type(old_evidence.get(k)) is int and old_evidence[k] > 0 and
                     type(new_evidence.get(k)) is int and
                     old_evidence[k] == new_evidence.get(k) for k in ('pid', 'process_start_ticks')))
        boundaries.append({'previous_action_id': previous['action_id'],
                           'next_action_id': current['action_id'],
                           'from_observed_at': old_evidence.get('completed_at'),
                           'to_observed_at': new_evidence.get('observed_at'),
                           'inventory_identity_verified': bound,
                           'before': before, 'after': after,
                           'net_decrease': {k: before[k] - after[k] for k in before} if bound else None,
                           'cause_verified': False, 'replay_allowed': False})
    return {'read_only': True, 'boundaries': boundaries,
            'verified_nonzero_gaps': [row for row in boundaries
                if row['inventory_identity_verified'] and any(row['net_decrease'].values())],
            'unverified_boundary_count': sum(not row['inventory_identity_verified'] for row in boundaries)}


def read_shequn_lianti_material_continuity(occurrence_key, *, receipt_dir=None):
    """Read the original period's inter-action inventory gaps, without game IO."""
    return {'occurrence_key': occurrence_key, **project_shequn_lianti_material_continuity(
        read_shequn_lianti_training_receipts(occurrence_key, receipt_dir=receipt_dir))}


def record_shequn_lianti_goal_result(result, *, receipt_dir=None):
    """Persist business evidence before the Cell wrapper reduces it to status.

    This is an observation, not a resumable generator or permission to spend.
    The next invocation still reads live goal facts and original action IDs.
    Fourteen actual claimed task identities are required for a complete result.
    """
    key = result['occurrence_key']
    path = _path(key, receipt_dir).with_suffix('.goal.json')
    goal = result.get('goal_evidence') or {}
    # Unfinished task rows expose the common score; finished rows clamp it to
    # their thresholds. All fourteen claimed/claimable identities prove the
    # highest target, not an exact score. This reached-target partial state
    # permits claims only; it is never a new training/consumption baseline.
    task_ids = set(goal.get('task_ids') or [])
    claimed_ids = set(goal.get('claimed_task_ids') or [])
    claimable_ids = set(goal.get('claimable_task_ids') or [])
    target_lower_bound = (
        goal.get('progress_is_exact') is False
        and goal.get('activity_progress') == 600000
        and len(task_ids) == 14 and len(goal.get('task_ids') or []) == 14
        and goal.get('pending_task_ids') == []
        and not claimed_ids & claimable_ids
        and claimed_ids | claimable_ids == task_ids
    )
    if (not goal.get('available') or not goal.get('complete')
            or goal.get('occurrence_key') != key or goal.get('target_score') != 600000
            or result.get('target_score') != goal['target_score']
            or result.get('result') not in ('partial', 'success')
            or type(result.get('goal_completed')) is not bool
            or type(goal.get('goal_completed')) is not bool
            or not (goal.get('progress_is_exact') is True or target_lower_bound)
            or type(goal.get('activity_progress')) is not int
            or goal['activity_progress'] < 0):
        raise ValueError('社团炼体整单结果缺少当前实例完整任务事实')
    completed = result['goal_completed']
    if completed != (result['result'] == 'success') or completed != bool(goal.get('goal_completed')):
        raise ValueError('社团炼体整单完成状态与真实任务事实不符')
    if completed and goal['activity_progress'] < goal['target_score']:
        raise ValueError('社团炼体整单尚未达到最高任务目标600000')
    if completed and (len(set(goal.get('claimed_task_ids') or [])) != 14
                      or set(goal['claimed_task_ids']) != set(goal.get('task_ids') or [])
                      or goal.get('claimable_task_ids') or goal.get('pending_task_ids')):
        raise ValueError('社团炼体整单缺少14档实际领取凭证')
    with FileLock(str(_path(key, receipt_dir)) + '.lock', timeout=5):
        rows = _read(_path(key, receipt_dir))
        if completed and any(row['status'] == 'pending' for row in rows):
            raise ValueError('社团炼体整单存在待核验原action_id，不能记录完成')
        verified = {row['action_id'] for row in rows
                    if row['status'] == 'verified'}
        if any(action not in verified for action in result.get('action_ids') or []):
            raise ValueError('社团炼体整单引用未经效果核验的action_id')
        value = {**result, 'recorded_at': datetime.now().astimezone().isoformat()}
        _save(path, value)
    return value


def read_shequn_lianti_goal_result(occurrence_key, *, receipt_dir=None):
    """Read the latest business observation; it never replaces live facts."""
    source = _path(occurrence_key, receipt_dir)
    path = source.with_suffix('.goal.json')
    with FileLock(str(source) + '.lock', timeout=5):
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def prepare_shequn_lianti_claim_follow_up(goal, *, receipt_dir=None):
    """Preserve the pre-claim observation before any reward click.

    For an already-claimed recovery with no journal, the last verified training
    receipt may supply its same-period pre-claim native observation. It never
    authorizes another reward click or fabricates a backpack increment.
    """
    key = goal['occurrence_key']
    source = _path(key, receipt_dir)
    path = source.with_suffix('.claims.json')
    with FileLock(str(source) + '.lock', timeout=5):
        if path.exists():
            existing = json.loads(path.read_text(encoding='utf-8'))
            if existing.get('status') != 'completed':
                return existing
            previous = existing['after']
            if (not goal.get('claimable_task_ids') and
                    set(goal.get('claimed_task_ids') or []) <= set(previous.get('claimed_task_ids') or [])):
                return existing
            # A later partial-reward round is a new delta. Keep the last
            # actual post-claim observation instead of replaying old drops.
            value = {'status': 'pending',
                     'before': goal if goal.get('claimable_task_ids') else previous,
                     'occurrence_key': key}
            _save(path, value)
            return value
        before = goal
        if goal.get('goal_completed'):
            rows = _read(source)
            for row in reversed(rows):
                candidate = (row.get('last_observation') or {}).get('goal') or {}
                if (row.get('status') == 'verified' and candidate.get('complete')
                        and candidate.get('occurrence_key') == key
                        and set(candidate.get('claimed_task_ids') or []) <= set(goal['claimed_task_ids'])):
                    before = candidate
                    break
        value = {'status': 'pending', 'before': before, 'occurrence_key': key}
        _save(path, value)
        return value


def record_shequn_lianti_claim_follow_up(occurrence_key, result, *, after, receipt_dir=None):
    """Deduplicate a completed wake/no-wake decision; failures remain pending."""
    source = _path(occurrence_key, receipt_dir)
    path = source.with_suffix('.claims.json')
    with FileLock(str(source) + '.lock', timeout=5):
        if not path.exists():
            raise ValueError('缺少领奖前事实')
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('status') == 'completed':
            return value
        from ..instrumentation.shequn_lianti_task_rewards import build_shequn_lianti_claim_reward_items
        expected = build_shequn_lianti_claim_reward_items(value['before'], after)
        if expected['claimed_now_task_ids'] != result.get('claimed_now_task_ids'):
            raise ValueError('领奖后续结果与原领取差量不符')
        value.update(status='completed', result=result, after=after,
                     recorded_at=datetime.now().astimezone().isoformat())
        _save(path, value)
        return value


def review_shequn_lianti_legacy_inventory_baseline(*, occurrence_key, action_id,
        book, inventory, inventory_evidence, execution, cost_range, goal, receipt_dir=None):
    """Retain an unprovable historical spend and establish a fresh boundary.

    A legacy canUse baseline is an opening-time usable budget, not total stock.
    This review never upgrades its cost/count claim or authorizes replay. It
    verifies the original book effect is stable and inactive, with no pending
    action, before the business entry may create a DIFFERENT action from fresh
    state. New actions still require an independent pre-click stock baseline.
    A discrepancy with an independent original baseline cannot use this path.
    """
    import time
    path = _path(occurrence_key, receipt_dir)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        row = next((r for r in rows if r['action_id'] == action_id), None)
        if (row is None or row is not rows[-1] or any(r['status'] == 'pending' for r in rows)
                or row['status'] != 'verified' or row.get('inventory_before') is not None
                or row.get('effect', {}).get('kind') != 'partial_net_material_effect'):
            raise ValueError('仅允许复核无独立库存基线且已结束的最新历史动作')
        before, observed = row['dialog_before'], row['last_observation']
        nodes = cost_range.get('cost_nodes', [])
        identity = before.get('evidence', {})
        if (not goal.get('complete') or goal.get('occurrence_key') != occurrence_key
                or goal.get('target_score') != row['goal_before']['target_score']
                or goal.get('activity_progress', -1) < observed['goal']['activity_progress']
                or any(book.get(k) != observed['book'].get(k) for k in ('base_id', 'stage', 'current_node'))
                or execution.get('complete') is not True or execution.get('active') is not False
                or cost_range.get('complete') is not True or cost_range.get('base_id') != before['base_id']
                or [n.get('node_id') for n in nodes] != list(range(before['current_node'] + 1, book['current_node'] + 1))
                or any(n.get('item_id') != before['normal_item_id'] or type(n.get('quantity')) is not int
                       or n['quantity'] <= 0 or n.get('required_stage', -1) > before['stage'] for n in nodes)
                or any(type(inventory.get(i)) is not int or inventory[i] < 0
                       for i in (before['normal_item_id'], before['breakthrough_item_id']))
                or inventory_evidence.get('force_refresh') is not True
                or inventory_evidence.get('source') != 'BackpackMgr.Model.BackpackData.ItemVoDic'
                or not 0 <= time.time() - inventory_evidence.get('completed_at', 0) <= 120
                or any(e.get(k) != identity.get(k)
                       for e in (inventory_evidence, execution.get('evidence', {}), cost_range.get('evidence', {}))
                       for k in ('pid', 'process_start_ticks'))):
            raise ValueError('历史库存基线复核缺少同实例稳定效果与当前公共事实')
        review = dict(kind='legacy_cached_usable_budget', original_consumption_proven=False,
                      original_successful_attempts=None, replay_allowed=False,
                      reviewed_at=datetime.now().astimezone().isoformat(),
                      current_inventory=inventory, inventory_evidence=inventory_evidence,
                      book=book, execution=execution, cost_range=cost_range, goal_evidence=goal)
        row['baseline_review'] = review
        _save(path, rows)
        return review


def read_shequn_lianti_training_score_rule(*, occurrence_key, item_id, native_rate, receipt_dir=None):
    """Return a native rate corroborated by an exact settled same-period batch.

    Aggregate clan changes are never divided by our material cost to invent a
    rate. Only an exact match to the independently loaded native rule qualifies.
    The latest relevant receipt decides: a newer mismatch must never fall back
    to an older matching sample. A mismatching score remains independent of
    the verified node/material effect, and is returned for diagnosis.
    """
    if type(native_rate) is not int or native_rate <= 0:
        raise ValueError('炼体计分需要明确原生正整数规则')
    for row in reversed(read_shequn_lianti_training_receipts(occurrence_key, receipt_dir=receipt_dir)):
        dialog = row['dialog_before']
        if dialog.get('normal_item_id') != item_id:
            continue
        if row['status'] != 'verified':
            return {'verified': False, 'reason': 'latest_score_sample_pending',
                    'action_id': row['action_id'], 'occurrence_key': occurrence_key}
        if row.get('effect', {}).get('kind') == 'partial_net_material_effect':
            return {'verified': False, 'reason': 'latest_material_effect_partial',
                    'action_id': row['action_id'], 'occurrence_key': occurrence_key}
        if (not isinstance(row.get('inventory_before'), dict)
                or row.get('effect', {}).get('inventory_baseline_verified') is not True):
            # A matching configured cost/score arithmetic cannot upgrade an
            # old opening-time usable budget to independently observed spend.
            return {'verified': False, 'reason': 'latest_inventory_baseline_unverified',
                    'action_id': row['action_id'], 'occurrence_key': occurrence_key}
        before, after = row['goal_before'], row['last_observation']['goal']
        if (before.get('progress_is_exact') and after.get('progress_is_exact')
                and before.get('occurrence_key') == occurrence_key
                and after.get('occurrence_key') == occurrence_key
                and type(before.get('activity_progress')) is int
                and type(after.get('activity_progress')) is int):
            delta = after['activity_progress'] - before['activity_progress']
            expected = dialog['normal_material_cost'] * native_rate
            evidence = {'action_id': row['action_id'], 'occurrence_key': occurrence_key,
                        'item_id': item_id, 'material_spent': dialog['normal_material_cost'],
                        'task_score_delta': delta, 'native_expected_delta': expected}
            if delta != expected:
                return {**evidence, 'verified': False,
                        'reason': 'latest_task_delta_differs_from_native_rate'}
            return {**evidence, 'verified': True, 'score_per_item': native_rate}
        return {'verified': False, 'reason': 'latest_score_sample_incomplete',
                'action_id': row['action_id'], 'occurrence_key': occurrence_key}
    return {'verified': False, 'reason': 'no_matching_settled_score_sample'}


def begin_shequn_lianti_training_action(*, goal, dialog, inventory_before=None, receipt_dir=None):
    """Reserve one verified configuration immediately before its sole click.

    A pending reservation is deliberately conservative even if the click later
    fails: only effect reconciliation can clear it, never a retry timeout.
    """
    if (not goal.get('complete') or not goal.get('progress_is_exact')
            or not goal.get('training_binding_verified') or not dialog.get('complete')
            or not dialog.get('selection_cost_verified')
            or dialog.get('activity_id') != goal.get('training_activity_id')
            or dialog.get('auto_buy_enabled') is not False
            or dialog.get('multiplicity_enabled') is not False
            or dialog.get('breakthrough_material_cost') != 0):
        raise ValueError('炼体消费缺少当前实例及精确配置门禁')
    fields = ('base_id', 'current_node', 'stage', 'selected_nodes', 'normal_item_id',
              'normal_material_cost', 'available_normal_material')
    if any(type(dialog.get(k)) is not int or dialog[k] < 0 for k in fields):
        raise ValueError('炼体消费数值不完整')
    if (type(goal.get('activity_progress')) is not int or goal['activity_progress'] < 0
            or type(goal.get('target_score')) is not int or goal['target_score'] <= 0):
        raise ValueError('炼体消费任务目标数值无效')
    if (dialog['base_id'] <= 0 or dialog['normal_item_id'] <= 0
            or not 1 <= dialog['selected_nodes'] <= 512
            or not 0 < dialog['normal_material_cost'] <= dialog['available_normal_material']
            or goal['activity_progress'] >= goal['target_score']):
        raise ValueError('炼体消费超过库存、单批范围或任务目标')
    key = goal['occurrence_key']
    if inventory_before is not None:
        import time
        counts, evidence = inventory_before.get('counts', {}), inventory_before.get('evidence', {})
        identities = (dialog['normal_item_id'], dialog.get('breakthrough_item_id'))
        if (any(type(identity) is not int or identity <= 0 or type(counts.get(identity)) is not int
                or counts[identity] < 0 for identity in identities)
                or counts[dialog['normal_item_id']] < dialog['normal_material_cost']
                or evidence.get('source') != 'BackpackMgr.Model.BackpackData.ItemVoDic'
                or evidence.get('force_refresh') is not True or evidence.get('read_only') is not True
                or any(evidence.get(k) != dialog.get('evidence', {}).get(k)
                       for k in ('pid', 'process_start_ticks'))
                or type(evidence.get('completed_at')) not in (int, float)
                or not 0 <= time.time() - evidence['completed_at'] <= 15):
            raise ValueError('炼体消费需要同进程新鲜普通/突破材料背包基线')
    path = _path(key, receipt_dir)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        if any(row.get('status') == 'pending' for row in rows):
            raise RuntimeError('炼体已有待核验效果，必须按原action_id回读')
        if any(row.get('status') == 'verified' and all(
                row['dialog_before'].get(k) == dialog[k]
                for k in ('base_id', 'stage', 'current_node')) for row in rows):
            raise RuntimeError('炼体已核验节点不得以旧配置再次提交')
        action_id = uuid4().hex
        rows.append(dict(action_id=action_id, status='pending', occurrence_key=key,
                         goal_before=goal, dialog_before=dialog,
                         inventory_before=inventory_before,
                         submitted_at=datetime.now().astimezone().isoformat()))
        _save(path, rows)
        return action_id


def reconcile_shequn_lianti_training_action(*, occurrence_key, action_id,
                                           book, inventory_count, goal, execution=None,
                                           breakthrough_inventory_count=None, inventory_evidence=None,
                                           allow_partial_material_effect=False, receipt_dir=None):
    """Verify settled effects, keeping incomplete batch quantities explicit.

    Unchanged state is ambiguous (server latency or an unsubmitted click), so
    it never grants replay. Clan progress is recorded but cannot establish an
    individual scoring rate by itself. Repeated reads of a verified ID return
    its original receipt without editing it.
    Explicit partial recovery may acknowledge a positive net material
    deduction below the configured budget once the native panel has closed.
    It proves neither gross server cost nor successful-attempt count. The
    business caller must stop further training to investigate that variance.
    """
    path = _path(occurrence_key, receipt_dir)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        row = next((r for r in rows if r.get('action_id') == action_id), None)
        if row is None:
            raise ValueError('未知炼体action_id')
        if row['status'] == 'verified':
            return row
        before = row['dialog_before']
        baseline = row.get('inventory_before')
        stock_before = before['available_normal_material']
        inventory_identity = True
        if baseline:
            counts = baseline['counts']
            stock_before = counts.get(str(before['normal_item_id']), counts.get(before['normal_item_id']))
            break_before = counts.get(str(before['breakthrough_item_id']), counts.get(before['breakthrough_item_id']))
            inventory_identity = (breakthrough_inventory_count == break_before
                                  and inventory_evidence is not None
                                  and inventory_evidence.get('force_refresh') is True
                                  and all(inventory_evidence.get(k) == baseline['evidence'].get(k)
                                          for k in ('pid', 'process_start_ticks')))
        # useNum counts attempts, not node gain. Server activation responses
        # may advance multiple nodes. Verify the exact material effect and
        # stable book/stage after auto-training has closed; never infer a
        # successful-attempt count from the observed node delta. Legacy callers
        # retain only the old exact-increment path without execution evidence.
        node = book.get('current_node')
        node_effect = (type(node) is int and node > before['current_node']
                       and execution is not None and execution.get('complete') is True
                       and execution.get('active') is False)
        if execution is None:
            node_effect = node == before['current_node'] + before['selected_nodes']
        envelope = before.get('node_gain_envelope')
        if envelope is not None:
            maximum_gain = envelope.get('max_node_gain')
            node_effect = (node_effect and type(maximum_gain) is int and maximum_gain > 0
                           and node - before['current_node'] <= maximum_gain)
        spent = (stock_before - inventory_count
                 if type(inventory_count) is int else None)
        exact_cost = spent == before['normal_material_cost']
        partial_cost = (allow_partial_material_effect is True and execution is not None
                        and execution.get('complete') is True and execution.get('active') is False
                        and type(spent) is int and 0 < spent < before['normal_material_cost'])
        matched = (goal.get('complete') and goal.get('occurrence_key') == occurrence_key
                   and book.get('base_id') == before['base_id']
                   and book.get('stage') == before['stage']
                   and node_effect
                   and inventory_identity and (exact_cost or partial_cost))
        row['last_observation'] = dict(book=book, inventory_count=inventory_count, goal=goal,
                                       execution=execution)
        if baseline:
            row['last_observation'].update(breakthrough_inventory_count=breakthrough_inventory_count,
                                           inventory_evidence=inventory_evidence)
        if matched:
            row.update(status='verified', verified_at=datetime.now().astimezone().isoformat(),
                       effect=dict(material_spent=spent,
                                   selected_attempts=before['selected_nodes'],
                                   actual_node_gain=node - before['current_node'],
                                   successful_attempts=None))
            if not exact_cost:
                row['effect'].update(kind='partial_net_material_effect',
                                     configured_material_budget=before['normal_material_cost'],
                                     gross_server_cost_verified=False)
            if baseline:
                row['effect']['inventory_baseline_verified'] = True
        _save(path, rows)
        return row
