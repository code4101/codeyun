"""社团炼体的任务奖励目标；查询不会打开页面或授权消费。"""
from datetime import datetime
import json

from ..catalog.resources import resolve_fanxiu_export_root
from ..instrumentation.activity_runtime import read_activity_instances_runtime_snapshot

from .runtime_schedule import read_fanxiu_activity_runtime_schedule
from ..instrumentation.shequn_lianti_task_rewards import (
    SHEQUN_LIANTI_ACTIVITY_ID, read_shequn_lianti_task_reward_snapshot,
)


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


__all__ = ["read_current_shequn_lianti_goal", "plan_shequn_lianti_training_batch",
           "verify_shequn_lianti_score_delta", "resolve_shequn_lianti_training_binding"]
