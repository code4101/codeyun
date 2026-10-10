"""社团炼体的任务奖励目标；查询不会打开页面或授权消费。"""
from datetime import datetime
import json

from ..catalog.resources import resolve_fanxiu_export_root
from ..instrumentation.activity_runtime import read_activity_instances_runtime_snapshot

from .runtime_schedule import read_fanxiu_activity_runtime_schedule
from ..instrumentation.shequn_lianti_task_rewards import (
    SHEQUN_LIANTI_ACTIVITY_ID, read_shequn_lianti_task_reward_snapshot,
)


def shequn_lianti_goal_key_for_occurrence(occurrence):
    """Bind a lifecycle checkpoint to the one actually taught 8-cross ladder.

    Reporting another similarly named activity does not authorize its page,
    materials or reward target. Preserve native millisecond period identity;
    neither the mutable Runtime ID nor the checkpoint business day replaces it.
    """
    if (occurrence.activity_type != 'shequn-lianti'
            or occurrence.family != 'resource_rank'
            or occurrence.activity_id != SHEQUN_LIANTI_ACTIVITY_ID
            or occurrence.cross_count != 8 or occurrence.base_id != 43400
            or any(value.utcoffset() is None or value.microsecond % 1000
                   for value in (occurrence.start_at, occurrence.end_at))
            or occurrence.end_at <= occurrence.start_at):
        raise ValueError('社团炼体 checkpoint 缺少已验证8跨实例及精确周期')
    return (f'{occurrence.activity_id}|{round(occurrence.start_at.timestamp() * 1000)}|'
            f'{round(occurrence.end_at.timestamp() * 1000)}|{occurrence.cross_count}')


def plan_shequn_lianti_goal_checkpoint(occurrence):
    """Prepare one opening-period goal; this does not register production use.

    Later business days reuse the same period checkpoint and completion proof.
    A 05:00:05 native opening is not lost by comparing it to 05:00:00. The
    exact taught ladder must still pass whole-goal live acceptance before the
    lifecycle planner and executor can admit this prepared checkpoint.
    """
    from datetime import time
    from .ranking_lifecycle import RankingCheckpoint

    shequn_lianti_goal_key_for_occurrence(occurrence)
    start = occurrence.start_at.astimezone()
    due = max(start, datetime.combine(start.date(), time(5), tzinfo=start.tzinfo))
    if due >= occurrence.end_at:
        raise ValueError('社团炼体周期未覆盖05点任务入口')
    return RankingCheckpoint(
        instance_key=occurrence.instance_key, activity_type=occurrence.activity_type,
        family=occurrence.family, runtime_id=occurrence.runtime_id,
        activity_id=occurrence.activity_id, checkpoint_kind='shequn_lianti_goal_0500',
        business_date=start.date().isoformat(), due_at=due)


def resolve_shequn_lianti_training_binding(*, parent_id, period, config_rows, instances):
    """Join the rank's explicit follow link to one same-period native child.

    The training dialog receives the activityType=4 child configuration,
    whereas the reward ladder belongs to the activityType=12 parent. Neither
    matching names nor an arbitrary loaded rank instance establishes this link.
    """
    rows = {int(row['id']): row for row in config_rows}
    parent = rows.get(parent_id, {})
    children = []
    for identity in parent.get('follow') or []:
        child = rows.get(int(identity), {})
        if (child.get('activityId') == 4 and child.get('subType') == 10341
                and child.get('sameActGroup') == parent.get('sameActGroup')
                and child.get('crossGroup') == parent.get('crossGroup')):
            children.append(int(identity))
    if parent_id != SHEQUN_LIANTI_ACTIVITY_ID or len(children) != 1:
        raise ValueError('社团炼体配置未提供唯一炼体子活动绑定')
    matches = [row for row in instances
               if row.get('activityId') == children[0] and row.get('activityType') == 4
               and row.get('startTime') == period['start_time_ms']
               and row.get('endTime') == period['end_time_ms']]
    if len(matches) != 1:
        raise ValueError('社团炼体当前周期子活动实例不唯一')
    return {'training_activity_id': children[0],
            'training_runtime_id': matches[0]['id'], 'training_binding_verified': True}


def read_current_shequn_lianti_goal():
    """Bind the current world-line occurrence to its exact live reward ladder.

    World-line identities select the server/cross instance; Runtime must
    corroborate its period. An absent active occurrence is pending evidence,
    never completed. Only the currently taught 8043401 ladder is supported;
    another size/version needs its own verified task configuration.
    """
    now = datetime.now().astimezone()
    # A schedule saved before opening can retain a preparing instance ID after
    # the client replaces it with the open instance. Never use that projection
    # as the current GUI/QuestMgr identity.
    schedule = read_fanxiu_activity_runtime_schedule(allow_discovery=True)
    if not schedule.get('complete') or not schedule.get('available'):
        raise RuntimeError('社团炼体当前世界线事实不完整')
    candidates = []
    for row in schedule.get("items") or []:
        if int(row.get("baseId") or 0) != 43400:
            continue
        start, end = row.get('startTime'), row.get('endTime')
        if type(start) is not int or type(end) is not int or end <= start:
            raise RuntimeError("社团炼体世界线周期无效")
        if start <= now.timestamp() * 1000 < end:
            candidates.append(row)
    if not candidates:
        return {"available": False, "complete": False, "goal_completed": False,
                "reason": "no_active_worldline_occurrence", "performed_actions": False}
    if len(candidates) != 1:
        raise RuntimeError("社团炼体当前世界线实例不唯一")
    occurrence = candidates[0]
    if (not occurrence.get("identityComplete") or not occurrence.get('id')
            or occurrence.get('serverCount') != 8 or occurrence.get('activityType') != 12
            or int(occurrence["activityId"]) != SHEQUN_LIANTI_ACTIVITY_ID):
        raise RuntimeError("社团炼体世界线身份不完整或尚无已验证梯度")
    result = read_shequn_lianti_task_reward_snapshot(runtime_id=int(occurrence['id']))
    period = result["period"]
    for field, source in (("start_time_ms", "startTime"), ("end_time_ms", "endTime")):
        if int(period[field]) != occurrence[source]:
            raise RuntimeError("社团炼体原生周期与世界线实例不一致")
    config = json.loads((resolve_fanxiu_export_root() / 'parsed_configs/Activity/rows.json').read_text(encoding='utf-8'))
    binding = resolve_shequn_lianti_training_binding(
        parent_id=int(occurrence['activityId']), period=period, config_rows=config,
        instances=read_activity_instances_runtime_snapshot()['items'])
    key = f"{occurrence['activityId']}|{occurrence['startTime']}|{occurrence['endTime']}|{occurrence['serverCount']}"
    return {**result, **binding, "occurrence_key": key,
            "cross_count": occurrence["serverCount"], "performed_actions": False,
            "worldline_captured_at": schedule.get("captured_at")}


def review_current_legacy_training_baseline(*, occurrence_key, action_id):
    """Review a closed legacy effect using fresh queries; never operate UI."""
    from .shequn_lianti_receipts import (read_shequn_lianti_training_receipts,
                                       review_shequn_lianti_legacy_inventory_baseline)
    from ..instrumentation.physical_training import (read_physical_training_books_snapshot,
        read_physical_training_cost_range_snapshot, read_physical_training_execution_snapshot)
    from ..instrumentation.backpack import read_backpack_item_counts
    receipt = next(r for r in read_shequn_lianti_training_receipts(occurrence_key)
                   if r['action_id'] == action_id)
    before, after = receipt['dialog_before'], receipt['last_observation']['book']
    books = read_physical_training_books_snapshot()
    matches = [b for b in books['books'] if b['base_id'] == before['base_id']]
    if not books.get('complete') or len(matches) != 1:
        raise RuntimeError('历史炼体复核功法事实不完整')
    costs = read_physical_training_cost_range_snapshot(base_id=before['base_id'],
                first_node=before['current_node'] + 1, last_node=after['current_node'])
    counts, evidence = read_backpack_item_counts(
        [before['normal_item_id'], before['breakthrough_item_id']],
        manager_key='shequn-lianti-legacy-baseline-review', force_refresh=True)
    execution = read_physical_training_execution_snapshot()
    goal = read_current_shequn_lianti_goal()
    return review_shequn_lianti_legacy_inventory_baseline(occurrence_key=occurrence_key,
        action_id=action_id, book=matches[0], inventory=counts, inventory_evidence=evidence,
        execution=execution, cost_range=costs, goal=goal)


def verify_shequn_lianti_score_delta(*, before_goal, after_goal, item_id,
                                    material_before, material_after, native_rate):
    """Validate an observed material/score delta against the native rule.

    Only an exact, same-occurrence progress delta establishes this arithmetic
    contract. A first packet can synchronize previously unseen progress, so a
    mismatch is evidence to investigate, never a newly invented multiplier.
    The caller must also prove the settled batch and absence of other effects.
    """
    if (not before_goal.get('progress_is_exact') or not after_goal.get('progress_is_exact')
            or not before_goal.get('occurrence_key')
            or before_goal['occurrence_key'] != after_goal.get('occurrence_key')):
        raise ValueError('计分校准要求同一活动实例的精确进度')
    values = (material_before, material_after, native_rate,
              before_goal.get('activity_progress'), after_goal.get('activity_progress'))
    if any(type(value) is not int or value < 0 for value in values) or native_rate == 0:
        raise ValueError('计分校准数值无效')
    spent = material_before - material_after
    delta = after_goal['activity_progress'] - before_goal['activity_progress']
    if spent <= 0 or delta < 0 or delta != spent * native_rate:
        return {'verified': False, 'reason': 'task_delta_differs_from_native_rate',
                'material_spent': spent, 'task_score_delta': delta,
                'native_expected_delta': spent * native_rate}
    return {'verified': True, 'occurrence_key': before_goal['occurrence_key'],
            'item_id': item_id, 'score_per_item': native_rate,
            'material_spent': spent, 'task_score_delta': delta}


def build_shequn_lianti_node_gain_bound(membership, definitions):
    """Conservative envelope for the supported current periodic bonus effect.

    Type35's params are interval_extra: one paid activation raises one base
    node. When extra < interval, even counting bonus nodes toward the next
    interval cannot recursively trigger another bonus in that activation: the
    triggering threshold resets the counter and the bonus stays below it.
    Use 1+extra per paid attempt, not the observed ten-attempt gain seventeen.
    This is a prepared policy; the executor must bind fresh membership and the
    full reachable cost range again immediately before consumption.
    """
    import re
    if (not membership.get('complete') or not definitions.get('complete')
            or not definitions.get('text_complete')
            or set(membership.get('client_active_resource_ids') or []) !=
               {row.get('id') for row in definitions.get('resources') or []}
            or any(membership.get('evidence', {}).get(key) != definitions.get('evidence', {}).get(key)
                   for key in ('pid', 'process_start_ticks'))):
        raise ValueError('社团炼体增层边界缺少同进程完整生效法则及配置')
    if any(type(membership.get('evidence', {}).get(key)) is not int
           or membership['evidence'][key] <= 0 for key in ('pid', 'process_start_ticks')):
        raise ValueError('社团炼体增层边界缺少实际进程身份')
    effects = {row['id']: row for row in definitions['effects']}
    bonuses = []
    for resource in definitions['resources']:
        effect = effects.get(resource.get('effects'))
        if effect is None:
            raise ValueError('社团炼体当前法则效果定义缺失')
        text = re.sub(r'<[^>]*>', '', str(resource.get('tip_text') or ''))
        if effect.get('type') == 35:
            match = re.fullmatch(r'([1-9]\d*)_([1-9]\d*)', str(effect.get('params') or ''))
            if match is None or int(match[2]) >= int(match[1]):
                raise ValueError('社团炼体增层周期或递归边界尚未支持')
            bonuses.append({'resource_id': resource['id'], 'effect_id': effect['id'],
                            'interval': int(match[1]), 'extra_nodes': int(match[2])})
        elif (re.search(r'淬体|淬炼|炼体|体魄', text)
              and re.search(r'(?:额外|连续)(?:提升|增加).{0,12}\d+层|淬体层数', text)):
            raise ValueError('社团炼体存在尚未支持的其他增层效果')
    if len(bonuses) != 1:
        raise ValueError('社团炼体仅支持已核验的单一周期增层法则')
    return {'kind': 'single_periodic_extra_nodes',
            'max_nodes_per_paid_attempt': 1 + bonuses[0]['extra_nodes'],
            'bonus': bonuses[0], 'evidence': membership['evidence'],
            'membership_captured_at': membership.get('captured_at'),
            'definitions_captured_at': definitions.get('captured_at')}


def plan_shequn_lianti_normal_attempt_envelope(dialog, cost_range, node_bound, *, maximum_attempts):
    """Plan paid attempts only across a fully covered normal/flat-price envelope.

    Node bonuses are free. Summing all reachable node costs would overcharge
    the prediction; instead prove every possible paid node has the same price,
    then multiply that price by paid attempts. Rejection grants no consumption.
    """
    if type(maximum_attempts) is not int or not 1 <= maximum_attempts <= 10:
        raise ValueError('社团炼体反馈批次必须在原生10次边界内')
    gain = node_bound.get('max_nodes_per_paid_attempt')
    if (node_bound.get('kind') != 'single_periodic_extra_nodes'
            or type(gain) is not int or gain <= 0
            or not cost_range.get('complete') or cost_range.get('base_id') != dialog.get('base_id')
            or any(node_bound.get('evidence', {}).get(key) != cost_range.get('evidence', {}).get(key)
                   or cost_range.get('evidence', {}).get(key) != dialog.get('evidence', {}).get(key)
                   for key in ('pid', 'process_start_ticks'))):
        raise ValueError('社团炼体节点包络、成本范围或进程身份不完整')
    start, stage = dialog.get('current_node'), dialog.get('stage')
    if type(start) is not int or type(stage) is not int:
        raise ValueError('社团炼体当前节点或阶段缺失')
    rows = cost_range.get('cost_nodes') or []
    for offset, row in enumerate(rows, 1):
        if (row.get('node_id') != start + offset
                or type(row.get('quantity')) is not int or row['quantity'] <= 0
                or type(row.get('required_stage')) is not int):
            raise ValueError('社团炼体节点包络成本身份不完整')
    maximum = dialog.get('maximum_nodes')
    if type(maximum) is not int or maximum < 0:
        raise ValueError('社团炼体原生次数上限不完整')
    limit = min(maximum_attempts, maximum, len(rows) // gain)
    for attempts in range(limit, 0, -1):
        reachable = rows[:attempts * gain]
        price = reachable[0]['quantity']
        if all(row.get('item_id') == dialog.get('normal_item_id')
               and row['quantity'] == price and 0 <= row['required_stage'] <= stage
               for row in reachable):
            return {'kind': 'train', 'selected_attempts': attempts,
                    'normal_material_cost': attempts * price,
                    'max_node_gain': attempts * gain, 'max_final_node': start + attempts * gain,
                    'node_gain_bound': node_bound}
    return {'kind': 'blocked', 'selected_attempts': 0,
            'reason': 'no_verified_normal_flat_price_envelope'}


def read_shequn_lianti_normal_attempt_envelope(dialog, *, maximum_attempts):
    """Fresh high-level safety observation, without a GUI or start action."""
    from datetime import datetime
    from ..instrumentation.faze_state import read_faze_runtime_snapshot, read_faze_definition_snapshot
    from ..instrumentation.physical_training import read_physical_training_cost_range_snapshot
    membership = read_faze_runtime_snapshot(include_expiry_details=False)
    definitions = read_faze_definition_snapshot(membership['client_active_resource_ids'])
    bound = build_shequn_lianti_node_gain_bound(membership, definitions)
    costs = read_physical_training_cost_range_snapshot(
        base_id=dialog['base_id'], first_node=dialog['current_node'] + 1,
        last_node=dialog['current_node'] + maximum_attempts * bound['max_nodes_per_paid_attempt'])
    now = datetime.now().astimezone()
    for fact in (membership, definitions, costs):
        age = (now - datetime.fromisoformat(fact['captured_at'])).total_seconds()
        if not 0 <= age <= 120:
            raise ValueError('社团炼体节点包络观测已过期，保留当前配置')
    return plan_shequn_lianti_normal_attempt_envelope(
        dialog, costs, bound, maximum_attempts=maximum_attempts)


def normal_only_shequn_lianti_quantum(dialog, *, maximum_attempts):
    """Reject a mixed-material boundary instead of shrinking native attempts.

    This selects native attempts, not successful node gains or a predicted
    score. The configured count and fresh pre-click cost guard remain required.
    PhysicalAutoStartView counts successful activations independently of node
    gains. Real ten-attempt receipts advanced seventeen nodes. Therefore a
    nine-node normal prefix cannot prove nine attempts avoid the breakthrough:
    any breakthrough in the observed path blocks this prepared selection.
    This is a cost rejection gate, not a bound on stochastic node advancement.
    """
    if type(maximum_attempts) is not int or not 1 <= maximum_attempts <= 10:
        raise ValueError('社团炼体反馈批次必须在原生10次边界内')
    if (not dialog.get('cost_path_complete') or dialog.get('activity_id') != 43403
            or dialog.get('auto_buy_enabled') is not False
            or dialog.get('multiplicity_enabled') is not False):
        raise ValueError('社团炼体普通材料前缀缺少完整成本或消费门禁')
    start, maximum, stage = (dialog.get(k) for k in ('current_node', 'maximum_nodes', 'stage'))
    if any(type(x) is not int or x < 0 for x in (start, maximum, stage)):
        raise ValueError('社团炼体成本边界无效')
    rows = dialog.get('cost_nodes') or []
    needed = min(maximum_attempts, maximum)
    if len(rows) < needed:
        raise ValueError('社团炼体成本前缀未覆盖反馈批次')
    mixed = False
    for offset, row in enumerate(rows, 1):
        if (row.get('node_id') != start + offset
                or type(row.get('quantity')) is not int or row['quantity'] < 0
                or type(row.get('required_stage')) is not int
                or not 0 <= row['required_stage'] <= stage
                or row.get('item_id') not in (dialog.get('normal_item_id'), dialog.get('breakthrough_item_id'))):
            raise ValueError('社团炼体成本前缀身份或阶段不完整')
        mixed |= row['item_id'] != dialog['normal_item_id']
    return 0 if mixed else needed


def plan_shequn_lianti_training_batch(*, goal, dialog, inventory, score_per_item,
                                    max_batch_nodes=None):
    """Choose a verified prefix toward the unchanged highest task target.

    Rates must come from the verified native scoring rule and real task-delta
    acceptance, never the different lianti-faxiang activity's hardcoded rate.
    This pure plan grants no purchase, stage-up or GUI permission. The caller
    binds its rates to the current occurrence and verifies dialog identity
    again before submitting the selected node count. An explicit batch limit
    permits bounded progress, with ``continuation_required`` until fresh task
    facts prove the full target. Without a limit, require a whole target prefix.
    """
    if max_batch_nodes is not None and (
            type(max_batch_nodes) is not int or not 1 <= max_batch_nodes <= 512):
        raise ValueError("炼体单批节点上限必须为1到512")
    if (not goal.get("available") or not goal.get("complete")
            or not dialog.get("complete") or not dialog.get("cost_path_complete")
            or goal.get('training_binding_verified') is not True
            or dialog.get("activity_id") != goal.get('training_activity_id')
            or dialog.get("auto_buy_enabled") is not False
            or dialog.get("multiplicity_enabled") is not False):
        raise ValueError("社团炼体规划缺少完整事实或存在额外消费开关")
    score, target = goal.get("activity_progress"), goal.get("target_score")
    if type(score) is not int or type(target) is not int or score < 0 or target <= 0:
        raise ValueError("社团炼体积分目标无效")
    if score >= target:
        return {"kind": "no_training", "selected_nodes": 0, "material_costs": {},
                "expected_score": score, "reason": "task_target_reached"}
    if goal.get("progress_is_exact") is not True:
        raise ValueError("社团炼体当前积分只有下界，不能据此规划消费")
    nodes = dialog.get("cost_nodes") or []
    start, maximum = dialog.get("current_node"), dialog.get("maximum_nodes")
    stage = dialog.get("stage")
    coverage = dialog.get("cost_path_nodes", maximum)
    if any(type(value) is not int or value < 0 for value in (start, maximum, stage)):
        raise ValueError("炼体节点边界无效")
    if type(coverage) is not int or not 0 <= coverage <= maximum:
        raise ValueError("炼体成本观察覆盖无效")
    if [row.get("node_id") for row in nodes] != list(range(start + 1, start + coverage + 1)):
        raise ValueError("炼体可达节点成本路径不连续")
    costs, projected = {}, score
    batch_nodes = nodes if max_batch_nodes is None else nodes[:max_batch_nodes]
    for count, row in enumerate(batch_nodes, 1):
        item, quantity = row.get("item_id"), row.get("quantity")
        required_stage = row.get("required_stage")
        rate, available = score_per_item.get(item), inventory.get(item)
        if (item not in (dialog.get("normal_item_id"), dialog.get("breakthrough_item_id"))
                or type(quantity) is not int or quantity < 0
                or type(required_stage) is not int or not 0 <= required_stage <= stage
                or type(rate) is not int or rate < 0
                or type(available) is not int or available < 0):
            raise ValueError("炼体材料计分、库存或阶段证据不完整")
        costs[item] = costs.get(item, 0) + quantity
        if costs[item] > available:
            return {"kind": "insufficient_material", "selected_nodes": 0,
                    "material_costs": {}, "expected_score": score,
                    "short_item_id": item}
        projected += quantity * rate
        if projected >= target:
            return {"kind": "train", "selected_nodes": count,
                    "material_costs": costs, "expected_score": projected,
                    "target_score": target, "base_id": dialog.get("base_id"),
                    "continuation_required": False}
    if (max_batch_nodes is not None and len(batch_nodes) == max_batch_nodes
            and maximum > max_batch_nodes and projected > score):
        return {"kind": "train", "selected_nodes": len(batch_nodes),
                "material_costs": costs, "expected_score": projected,
                "target_score": target, "base_id": dialog.get("base_id"),
                "continuation_required": True, "reason": "bounded_target_progress"}
    return {"kind": "need_more_cost_evidence" if coverage < maximum else "reachable_path_below_target", "selected_nodes": 0,
            "material_costs": {}, "expected_score": score,
            "reachable_score": projected}


def schedule_storage_bag_after_shequn_lianti_claim(session, before, after, *, observed_at,
                                                 scheduler_state_path=None):
    """Inspect selected declared rewards after same-period claim verification.

    This advances the existing formal Job through its atomic provider; it does
    not consume items, infer a bag increment, or bypass a live attempt/backoff.
    No newly claimed tier means no wake, including recovery after prior claims.
    """
    from datetime import timedelta
    from ..instrumentation.shequn_lianti_task_rewards import build_shequn_lianti_claim_reward_items
    from ..storage_bag_settings import read_storage_bag_auto_claim_item_ids
    from ..data_annotation.tasks.storage_bag_operation import (
        STANDARD_JOB_ID, STORAGE_BAG_FOLLOW_UP_SECONDS,
    )
    from ..data_annotation.kernel_scheduler_control import advance_scheduler_task_from_fact_result

    rewards = build_shequn_lianti_claim_reward_items(before, after)
    matched = read_storage_bag_auto_claim_item_ids(
        session, base_ids=rewards['claimed_reward_item_ids'])
    if not matched:
        return {**rewards, 'woke': False, 'reason': 'no_authorized_reward',
                'matched_item_ids': []}
    atomic = advance_scheduler_task_from_fact_result(
        STANDARD_JOB_ID, observed_at + timedelta(seconds=STORAGE_BAG_FOLLOW_UP_SECONDS),
        scheduler_state_path=scheduler_state_path)
    return {**rewards, 'woke': bool(atomic.get('changed')), 'reason': atomic.get('reason'),
            'next_time': atomic.get('next_time'), 'matched_item_ids': matched}


__all__ = ["read_current_shequn_lianti_goal", "plan_shequn_lianti_training_batch",
           "verify_shequn_lianti_score_delta", "resolve_shequn_lianti_training_binding",
           "shequn_lianti_goal_key_for_occurrence", "plan_shequn_lianti_goal_checkpoint",
           "normal_only_shequn_lianti_quantum", "schedule_storage_bag_after_shequn_lianti_claim",
           "build_shequn_lianti_node_gain_bound", "plan_shequn_lianti_normal_attempt_envelope"]
