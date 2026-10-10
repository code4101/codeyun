"""Occurrence-bound resource-ranking adapter for the accepted 8-cross goal.

The common lifecycle owns identity, per-period planning and dispatch.
The same whole-goal flow owns navigation, consumption, reconciliation and
fourteen-tier claim proofs; this adapter only translates its business result.
"""
from datetime import datetime, timedelta

from backend.core.fanxiu.activity.shequn_lianti import (
    read_current_shequn_lianti_goal, shequn_lianti_goal_key_for_occurrence,
)
from backend.core.fanxiu.activity.shequn_lianti_receipts import record_shequn_lianti_goal_result
from .shequn_lianti import run_shequn_lianti_goal


def project_shequn_lianti_checkpoint_result(recorded, *, now):
    """Translate recorded business evidence without operating the game.

    A material boundary is blocked and inherits the parent's ordinary retry
    policy. Only bounded progress, score latency and due-job handoffs request
    a minute continuation. Neither status terminalizes the unfinished goal.
    """
    if recorded['goal_completed']:
        return {**recorded, 'status': 'completed',
                'message': '社团炼体最高任务目标及14档奖励已实际核验，返回世界'}
    reason = recorded.get('reason')
    if reason == 'investigate_original_material_cost_variance':
        raise RuntimeError('社团炼体原action_id独立库存成本存在差异，需保留现场调查')
    if reason == 'breakthrough_material_boundary':
        return {**recorded, 'status': 'blocked',
                'message': '社团炼体到达突破材料边界，安全消费规则未验证，整单未完成'}
    if reason not in ('due_jobs_take_priority', 'upcoming_due_jobs_take_priority',
                      'bounded_batch_boundary', 'await_original_score_effect',
                      'remaining_target_after_partial_rewards'):
        raise RuntimeError(f'社团炼体未分类续跑原因：{reason}')
    pending = {**recorded, 'status': 'pending',
               'retry_at': (now + timedelta(minutes=1)).isoformat()}
    if reason in ('due_jobs_take_priority', 'upcoming_due_jobs_take_priority'):
        pending['yield_reason'] = 'higher_priority_trigger'
    return pending


def run_shequn_lianti_goal_checkpoint(context, *, occurrence, stop_event, max_batches=64):
    """Use live facts and original action receipts for each fresh parent attempt.

    A partial batch is pending, never a completed checkpoint. Due-job yields
    stop sibling checkpoint execution; the next attempt reconstructs the same
    goal from current facts, without resuming a generator or replaying effects.
    Only the exact accepted ladder is eligible for this production checkpoint.
    """
    key = shequn_lianti_goal_key_for_occurrence(occurrence)
    goal = read_current_shequn_lianti_goal()
    if (not goal.get('available') or not goal.get('complete')
            or not goal.get('training_binding_verified')
            or goal.get('occurrence_key') != key or goal.get('target_score') != 600000):
        raise RuntimeError('社团炼体 checkpoint 与当前原生任务实例不符，停止操作')
    result = yield from run_shequn_lianti_goal(
        context, stop_event=stop_event, max_batches=max_batches)
    if result.get('occurrence_key') != key:
        raise RuntimeError('社团炼体 checkpoint 执行期间实例变化，拒绝完成写回')
    recorded = record_shequn_lianti_goal_result(result)
    return project_shequn_lianti_checkpoint_result(recorded, now=datetime.now().astimezone())
