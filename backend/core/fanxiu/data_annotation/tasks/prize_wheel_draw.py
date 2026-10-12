"""Single-draw execution with durable intent and fresh Runtime receipt.

Uses the currently verified 天河 assets and currency whitelist. Reentry first
reconciles an unresolved submission in its original live page, never clicks it
again. Closing result overlays is a separate verified navigation operation.
"""
import time

from backend.core.fanxiu.activity.prize_wheel_receipts import (
    begin_prize_wheel_single_draw, read_prize_wheel_actions,
    settle_prize_wheel_single_draw, validate_prize_wheel_observation, prize_wheel_instance,
)
from backend.core.fanxiu.instrumentation.prize_wheel import (
    read_prize_wheel_snapshot, read_prize_wheel_view_snapshot,
    read_prize_wheel_reward_window,
)
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_research_batch_admission
from .tianhe_navigation import enter_tianhe_activity_home


def read_prize_wheel_draw_observation(activity_id):
    """Coherent read-only wheel/page/wallet projection; does not load a page."""
    wheel = read_prize_wheel_snapshot(activity_id)
    wallet = read_wallet_currency_snapshot(40017, missing_as_zero=True)
    view = read_prize_wheel_view_snapshot(activity_id)
    snapshot = {'wheel': wheel, 'wallet': wallet, 'view': view}
    validate_prize_wheel_observation(snapshot)
    return snapshot


def execute_tianhe_single_draw(context, *, activity_id: int, retainability: str,
                               receipt_timeout_seconds: float = 60):
    """Submit at most one draw, or only reconcile the original pending action.

    Retainability must come from the actual ticket description, not absence of
    expiry. A retainable ticket's verified first grand prize blocks new intents.
    Receipt timeout preserves the original action ID and current page.
    """
    pending = [r for r in read_prize_wheel_actions() if r.get('status') == 'pending']
    if pending:
        if (len(pending) != 1 or pending[0].get('action_kind', 'draw') != 'draw'
                or pending[0]['before']['view']['activity_id'] != activity_id):
            raise RuntimeError('另一宝盘原intent未结，禁止导航或新抽奖')
        action = pending[0]
    else:
        admission = read_scheduler_research_batch_admission(batch_seconds=120, release_seconds=120)
        if admission.get('admitted') is not True:
            return {'status': 'deferred', 'admission': admission, 'submitted': False}
        try:
            before = read_prize_wheel_draw_observation(activity_id)
        except ValueError:
            yield from enter_tianhe_activity_home(context, activity_id=activity_id)
            before = read_prize_wheel_draw_observation(activity_id)
        if before['view']['ten_draw']:
            yield from context.wait_click(498, '转动十次')
            yield from context.wait_action_settle(0.5)
            before = read_prize_wheel_draw_observation(activity_id)
        # Recheck after navigation/mode selection, before the durable intent.
        admission = read_scheduler_research_batch_admission(batch_seconds=90, release_seconds=120)
        if admission.get('admitted') is not True:
            return {'status': 'deferred', 'admission': admission, 'submitted': False}
        if before['wallet']['currency_amount'] == 0:
            return {'status': 'stopped', 'stop_reason': 'existing_tickets_exhausted'}
        if not before['view']['start_time_ms'] <= int(time.time() * 1000) < before['view']['end_time_ms']:
            raise RuntimeError('宝盘已不在当期抽奖窗口')
        action = begin_prize_wheel_single_draw(before, retainability=retainability)
        yield from context.wait_click(498, '转动（高风险）')
    deadline = time.monotonic() + max(1, float(receipt_timeout_seconds))
    last_error = ''
    while time.monotonic() < deadline:
        try:
            after = read_prize_wheel_draw_observation(activity_id)
            receipt = settle_prize_wheel_single_draw(action['action_id'], after)
            return {'status': 'verified', 'action_id': action['action_id'],
                    'effect': receipt['effect'], 'after': after}
        except (ValueError, OSError) as exc:
            last_error = str(exc)
        yield from context.wait_action_settle(1)
    raise RuntimeError(f"宝盘原intent {action['action_id']} 未结，保留页面与凭证，禁止重抽：{last_error}")


def close_tianhe_draw_result(context, *, activity_id, fresh_receipt=None):
    """Close the shared reward overlay after verifying a settled wheel response.

    #604 has both 恭喜获得 and 点击屏幕继续 as required OCR identities;
    it describes the shared native reward window, independent of prize icons.
    The wheel's exact instance/response proves which business owns this popup.
    """
    # Immediately following a verified response in this exclusive Task, its
    # sealed observation is already authoritative. Reentry or a stale receipt
    # always takes a new Runtime observation before touching a popup.
    observed = None
    if fresh_receipt is not None:
        rows = [r for r in read_prize_wheel_actions()
                if r.get('action_id') == fresh_receipt.get('action_id') and r.get('status') == 'verified']
        if len(rows) == 1:
            row = rows[0]
            from datetime import datetime
            age = time.time() - datetime.fromisoformat(row['verified_at']).timestamp()
            if 0 <= age <= 30 and row['after']['view']['activity_id'] == activity_id:
                observed = row['after']
    if observed is None:
        observed = read_prize_wheel_draw_observation(activity_id)
    if observed['view'].get('waiting_response') is True or observed['view'].get('animation_running') is True:
        raise RuntimeError('宝盘响应/动画尚未完成，不能关闭结果')
    # Settlement precedes the queued popup. A root click while item animations
    # run only skips animation; it does not close the Window. Its explicit
    # timer and quickHitEnd are the action gate, not a generic reward scene ID.
    deadline = time.monotonic() + 45
    clicks = 0
    seen_window = False
    while time.monotonic() < deadline:
        window = read_prize_wheel_reward_window()
        if any(window['evidence'][k] != observed['view']['evidence'][k]
               for k in ('pid', 'process_start_ticks')):
            raise RuntimeError('领奖窗口与原抽奖进程不一致')
        if not window['is_open']:
            current = read_prize_wheel_view_snapshot(activity_id)
            if ((seen_window or fresh_receipt is None)
                    and current.get('complete') is True and current.get('queued_reward_count') == 0
                    and current.get('reward_wait_frame_count') == 0
                    and current.get('animation_running') is not True):
                try:
                    yield from context.wait_scene_exact([498], timeout=3,
                              observation_scenes=[647, 604], label='天河：原生领奖结束后确认首页')
                    return {'scene_id': 498, 'close_clicks': clicks}
                except TimeoutError:
                    # Pop removes the queue entry before F_ShowWin finishes.
                    # Re-observe native membership if its popup appeared after
                    # the earlier closed sample; never let that sample seal exit.
                    pass
        else:
            seen_window = True
        if (window['is_open'] and window.get('auto_close_seconds') == 0
              and not window.get('auto_close_timer_id') and window.get('quick_hit_end') is True):
            scene = yield from context.wait_scene_exact([647, 604], timeout=10,
                          observation_scenes=[498], label='天河：确认共享原生奖励画面')
            fresh = read_prize_wheel_reward_window()
            if (fresh.get('is_open') and fresh.get('panel_address') == window['panel_address']
                    and fresh.get('auto_close_seconds') == 0
                    and not fresh.get('auto_close_timer_id') and fresh.get('quick_hit_end') is True):
                if clicks >= 2:
                    raise RuntimeError('原生奖品窗口两次关闭仍未结束，保留现场')
                # Exact visual/native identity is already owned; the fast path
                # avoids a second delayed guard and uses the existing Shape.
                context.click_shape_center_fast(scene.scene_id,
                        '继续' if scene.scene_id == 647 else '点击屏幕继续')
                clicks += 1
        yield from context.wait_action_settle(.5)
    raise RuntimeError('原生领奖队列或奖励窗口尚未结束，保留现场')


def execute_tianhe_draw_goal(context, *, activity_id, retainability, max_draws=20):
    """Bounded first-hit/exhaust cycle; keep durable effects across duty yields.

    Every submission observes live engineering admission. Reaching a chunk
    boundary is pending work, not activity completion. Never gifts or purchases.
    """
    if type(max_draws) is not int or not 1 <= max_draws <= 256:
        raise ValueError('需要1至256的有界单抽数量')
    if retainability not in ('retainable', 'non_retainable'):
        raise ValueError('需要明确跨期留存性')
    pending = [r for r in read_prize_wheel_actions() if r.get('status') == 'pending']
    if pending:
        yield from execute_tianhe_single_draw(context, activity_id=activity_id,
                                             retainability=retainability)
    try:
        observation = read_prize_wheel_draw_observation(activity_id)
    except ValueError:
        yield from enter_tianhe_activity_home(context, activity_id=activity_id)
        observation = read_prize_wheel_draw_observation(activity_id)
    yield from close_tianhe_draw_result(context, activity_id=activity_id)
    key = prize_wheel_instance(observation)
    completed = []
    for _ in range(max_draws):
        rows = [r for r in read_prize_wheel_actions()
                if r.get('instance_key') == key and r.get('status') == 'verified']
        if retainability == 'retainable' and any(r['effect']['grand_prize_count'] for r in rows):
            yield from context.go_scene(34)
            return {'status': 'success', 'stop_reason': 'first_grand_prize',
                    'draws': completed, 'instance_key': key,
                    'grand_prize_count': sum(r['effect']['grand_prize_count'] for r in rows)}
        result = yield from execute_tianhe_single_draw(context, activity_id=activity_id,
                                                      retainability=retainability)
        if result['status'] == 'deferred':
            yield from context.go_scene(34)
            return {'status': 'pending', 'stop_reason': 'engineering_duty',
                    'draws': completed, 'admission': result['admission'], 'instance_key': key}
        if result['status'] == 'stopped':
            yield from context.go_scene(34)
            return {'status': 'success', 'stop_reason': result['stop_reason'],
                    'draws': completed, 'instance_key': key}
        completed.append({'action_id': result['action_id'], 'effect': result['effect']})
        yield from close_tianhe_draw_result(context, activity_id=activity_id, fresh_receipt=result)
        if retainability == 'retainable' and result['effect']['grand_prize_count']:
            yield from context.go_scene(34)
            return {'status': 'success', 'stop_reason': 'first_grand_prize',
                    'draws': completed, 'instance_key': key, 'grand_prize_count': 1}
    yield from context.go_scene(34)
    return {'status': 'pending', 'stop_reason': 'bounded_chunk', 'draws': completed,
            'instance_key': key}


def execute_tianhe_draw_task(runner, ctx, payload, stop_event):
    """Formal manual Task for the accepted 40017 ticket pool.

    Lingyun ticket 40017 explicitly survives and can be used at the next
    occurrence; the owner's standing rule therefore selects first-hit. Other
    currencies/pools must pass their own evidence gate before this task accepts
    them. This component does not mark gifts, task rewards or the theme complete.
    """
    from backend.core.fanxiu.data_annotation.game_context import BehaviorTreeContext
    activity_id = payload.get('activity_id')
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError('正式天河抽奖Task需要精确活动ID')
    context = BehaviorTreeContext(runner, ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
    return (yield from execute_tianhe_draw_goal(context, activity_id=activity_id,
                   retainability='retainable', max_draws=payload.get('max_draws', 20)))
