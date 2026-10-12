"""Tianhe cumulative rewards: exact native IDs, one batch click, no draws."""
import time

from backend.core.fanxiu.activity.prize_wheel_receipts import (
    read_prize_wheel_actions, begin_prize_wheel_cumulative_claim,
    settle_prize_wheel_cumulative_claim,
)
from backend.core.fanxiu.instrumentation.prize_wheel import (
    read_prize_wheel_cumulative_rewards, read_prize_wheel_view_snapshot,
)
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_research_batch_admission
from .prize_wheel_draw import close_tianhe_draw_result
from .tianhe_navigation import enter_tianhe_activity_home


def read_tianhe_claim_observation(activity_id):
    """Fresh canonical claimed IDs plus the exact active period/page."""
    rewards = read_prize_wheel_cumulative_rewards(activity_id)
    view = read_prize_wheel_view_snapshot(activity_id)
    if rewards.get('complete') is not True or view.get('complete') is not True:
        raise ValueError('天河累计领奖事实尚未完整加载')
    return {'rewards': rewards, 'view': view}


def execute_tianhe_cumulative_claims(context, *, activity_id):
    """Claim visible eligible milestones, preserving any unsettled original IDs.

    The native LotteryItem click raises GetLotteryReward: the bar sends all
    eligible IDs in its current four-slot list as one batch. Never guesses a
    hidden slot, never repeats an uncertain click, never starts a new draw.
    """
    completed = []
    pending = [r for r in read_prize_wheel_actions() if r['status'] == 'pending']
    if pending:
        if (len(pending) != 1 or pending[0].get('action_kind') != 'cumulative_claim'
                or pending[0]['before']['view']['activity_id'] != activity_id):
            raise RuntimeError('另一宝盘动作未结，不能导航或领取')
        action = pending[0]
    else:
        admission = read_scheduler_research_batch_admission(batch_seconds=180, release_seconds=120)
        if not admission.get('admitted'):
            return {'status': 'pending', 'reason': 'engineering_duty', 'admission': admission}
        yield from enter_tianhe_activity_home(context, activity_id=activity_id)
        yield from close_tianhe_draw_result(context, activity_id=activity_id)
        action = None
    for _ in range(10):
        if action is None:
            before = read_tianhe_claim_observation(activity_id)
            targets = [r for r in before['rewards']['visible_milestones'] if r['claimable']]
            if not targets:
                if before['rewards']['claimable_reward_ids']:
                    raise RuntimeError('累计奖励可领档位不在当前原生可见页，保留现场')
                yield from context.go_scene(34)
                return {'status': 'success', 'claimed_reward_ids': completed,
                        'final_rewards': before['rewards']}
            admission = read_scheduler_research_batch_admission(batch_seconds=90, release_seconds=120)
            if not admission.get('admitted'):
                yield from context.go_scene(34)
                return {'status': 'pending', 'reason': 'engineering_duty',
                        'claimed_reward_ids': completed, 'admission': admission}
            action = begin_prize_wheel_cumulative_claim(before)
            yield from context.wait_click(498, f"累计奖励槽{targets[0]['visible_slot']}")
        deadline = time.monotonic() + 60
        last_error = ''
        while time.monotonic() < deadline:
            try:
                after = read_tianhe_claim_observation(activity_id)
                settled = settle_prize_wheel_cumulative_claim(action['action_id'], after)
                completed.extend(settled['effect']['claimed_reward_ids'])
                break
            except (ValueError, OSError) as exc:
                last_error = str(exc)
            yield from context.wait_action_settle(1)
        else:
            raise RuntimeError(f"原累计领奖{action['action_id']}未确认，禁止重放：{last_error}")
        yield from close_tianhe_draw_result(context, activity_id=activity_id)
        action = None
    raise RuntimeError('累计领奖超出有界档位轮次')


def execute_tianhe_cumulative_claim_task(runner, ctx, payload, stop_event):
    """Formal free-claim Task; does not declare the whole theme complete."""
    from backend.core.fanxiu.data_annotation.game_context import BehaviorTreeContext
    activity_id = payload.get('activity_id')
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError('累计领奖Task需要精确活动ID')
    context = BehaviorTreeContext(runner, ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
    return (yield from execute_tianhe_cumulative_claims(context, activity_id=activity_id))
