"""Native default-plan trading with explicit occurrence and resource gates."""
import time
from datetime import datetime
from math import isfinite
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.instrumentation.celebration_trade import (
    read_celebration_trade_auto_settings, read_celebration_trade_auto_result,
    read_celebration_trade_auto_admission_cache,
)
from backend.core.fanxiu.activity.celebration_trade_receipts import (
    read_celebration_trade_receipts, arm_celebration_trade_batch,
    settle_celebration_trade_batch,
)
from backend.core.fanxiu.runtime_gui.integer_count_control import (
    IntegerSliderAssets, set_verified_integer_slider_count,
)
from .celebration_trade_research import inspect_celebration_trade_main_panel
from .revenue_task_claims import claim_open_revenue_task_rewards
from backend.core.fanxiu.data_annotation.effective_time import job_now


def next_celebration_trade_checkpoint(now):
    """Return the remaining same-day pre-close trigger in Scheduler format.

    No trigger crosses midnight with an old occurrence's Runtime ID.
    """
    future = [now.replace(hour=h, minute=m, second=0, microsecond=0)
              for h, m in [(20, 45), (21, 45)]]
    next_time = min((t for t in future if t > now), default=None)
    text = next_time.strftime('%Y-%m-%d %H:%M:%S') if next_time else None
    return text


def celebration_trade_batch_fits_window(*, now_epoch, end_time_ms, timeout_seconds,
                                       release_seconds=120):
    """An observing timeout plus safe departure must fit the native open window."""
    values = (now_epoch, end_time_ms, timeout_seconds, release_seconds)
    if any(type(v) not in (int, float) or not isfinite(v) or v < 0 for v in values):
        raise ValueError('商盟批次窗口需要有限非负时间')
    return now_epoch + timeout_seconds + release_seconds < end_time_ms / 1000


def _write_evening_checkpoint(context, *, manage_schedule=True):
    text = next_celebration_trade_checkpoint(job_now())
    if manage_schedule:
        context.set_next_time(text)
    return text


def _wait_native_auto_finish(context, *, timeout_seconds):
    """Observe an already started game batch, without another Start click.

    The native game can keep trading after its observing Cell fails. A fresh
    attempt waits for that same batch's finish window before settling intent.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        yield from context.wait_action_settle(3)
        after = None
        try:
            after = read_celebration_trade_auto_result()
        except FanxiuRuntimeMemoryError as exc:
            if exc.code != 'window_panel_not_ready':
                raise
        if after is not None and (after['finish_window_open'] or (after.get('auto_running') is False
                and after['completed_count'] == after['requested_count'] > 0)):
            return after
        if time.monotonic() >= deadline:
            raise RuntimeError('商盟自动仍未形成完成窗口；保留已开始批次，禁止重放')


def execute_celebration_trade_auto(context, *, activity_id: int, runtime_id: int,
                                  max_trades: int = 30, timeout_seconds: float = 420,
                                  reconcile_only: bool = False,
                                  claim_task_rewards: bool = False,
                                  scheduler_task_id: str | None = None,
                                  manage_schedule: bool = True):
    """Clear currently held licenses using only the native default trade plan.

    No resource-refill, shop, plan editing or manual guild action is allowed.
    An unresolved start intent must be reconciled before any navigation/new
    batch. After exact settlement, finish confirmation and departure are safe
    to retry without replaying the completed batch.
    """
    if type(max_trades) is not int or not 1 <= max_trades <= 30:
        raise ValueError('max_trades must be 1..30')
    if (type(timeout_seconds) not in (int, float) or not isfinite(timeout_seconds)
            or not 30 <= timeout_seconds <= 900):
        raise ValueError('timeout_seconds must be 30..900')
    if type(reconcile_only) is not bool:
        raise ValueError('reconcile_only must be bool')
    if type(claim_task_rewards) is not bool:
        raise ValueError('claim_task_rewards must be bool')
    if type(manage_schedule) is not bool:
        raise ValueError('manage_schedule must be bool')
    reward_claims = []
    newly_started_trades = 0
    rows = read_celebration_trade_receipts(runtime_id)
    pending = [r for r in rows if r['status'] == 'armed']
    if reconcile_only:
        if pending or not rows or rows[-1]['status'] != 'verified':
            raise RuntimeError('商盟只补验模式缺少准确已结批次')
        receipt = rows[-1]
        observed = read_celebration_trade_auto_result()
        after = receipt['after']
        if (observed['evidence'] != after['evidence']
                or observed['total_sell_count'] != after['total_sell_count']
                or observed['selected_plan_uid'] != after['selected_plan_uid']
                or observed['completed_count'] != after['completed_count']
                or observed['requested_count'] != after['requested_count']):
            raise RuntimeError('商盟已结批次与当前原生/世界现场不一致，拒绝补验')
        yield from context.wait_scene_exact([34], timeout=25,
                                           label='商盟零新增补验：确认已回世界')
        next_time_text = _write_evening_checkpoint(context, manage_schedule=manage_schedule)
        return dict(result='success', activity_id=activity_id, runtime_id=runtime_id,
            completed_trades=after['completed_count'], newly_started_trades=0,
            remaining_licenses=observed['license_count'], native_result=observed,
            receipt=receipt, current_scene=34, next_time=next_time_text,
            message=f"商盟已完成{after['completed_count']}次，零新增交易补验调度写回")
    if pending:
        if len(pending) != 1:
            raise RuntimeError('商盟未结批次不唯一')
        after = yield from _wait_native_auto_finish(context, timeout_seconds=timeout_seconds)
        receipt = settle_celebration_trade_batch(runtime_id, pending[0]['action_id'], after)
    else:
        # A previous settled batch can have left only its result dialog open.
        yield from context.wait_scene_exact([34, 959, 962, 963, 964, 965], timeout=25)
        if context.match_view(964, update=True)[0]:
            yield from context.wait_click(964, '确认')
        entry = yield from inspect_celebration_trade_main_panel(context,
            activity_id=activity_id, runtime_id=runtime_id, return_to_world=False)
        if claim_task_rewards:
            context.click_shape_center(959, '任务页签')
            yield from context.wait_scene_exact([965], timeout=30)
            for tab, subtype in [('日常页签', 23), ('家族页签', 21)]:
                context.click_shape_center(965, tab)
                yield from context.wait_action_settle(1.5)
                claimed = yield from claim_open_revenue_task_rewards(context,
                    activity_id=activity_id, expected_subtype=subtype,
                    scene_id=965, first_row_shape='首条任务领取区')
                reward_claims.extend(claimed['claimed_task_ids'])
            context.click_shape_center(965, '商盟主页面')
            yield from context.wait_scene_exact([959], timeout=30)
            from backend.core.fanxiu.instrumentation.celebration_trade import read_celebration_trade_main_panel
            # Reward quantities never become a spending budget. Re-read the
            # actual world-model license balance after native reward delivery.
            entry['native_snapshot'] = read_celebration_trade_main_panel(
                expected_activity_id=activity_id, expected_runtime_id=runtime_id)
        resources = entry['native_snapshot']['resources']
        if (not resources['complete'] or resources['license_item_id'] != 51
                or resources['money_item_id'] != 50):
            raise RuntimeError('商盟消费资源未符合已验收的古银币50/文书51白名单')
        if resources['license_count'] == 0:
            receipt = None
            after = dict(completed_count=0, license_count=0)
        else:
            context.click_shape_center(959, '前往贸易')
            yield from context.wait_scene_exact([962], timeout=30)
            context.click_shape_center(962, '自动贸易设置')
            yield from context.wait_scene_exact([963], timeout=30)
            gate = read_celebration_trade_auto_admission_cache()
            settings = read_celebration_trade_auto_settings()
            if not gate['sell_count_gate_passed'] or not settings['default_plan_selected']:
                raise RuntimeError('商盟原生自动准入或默认方案未对齐')
            desired = min(settings['maximum_trade_count'], max_trades)
            assets = IntegerSliderAssets(settings_scene_id=963, count_region='贸易次数',
                count_decrease='次数减少', count_increase='次数增加',
                count_slider_thumb=None, count_slider_track='次数滑轨')
            yield from set_verified_integer_slider_count(context, assets, desired,
                # The supported budget is at most 30 trades. The common
                # control's bounded +/- phase can therefore cover the entire
                # residual without inferring a commanded drag's actual thumb
                # position. It still verifies native feedback after each batch.
                maximum=settings['maximum_trade_count'], max_adjustments=30,
                runtime_count_reader=lambda: read_celebration_trade_auto_settings()['selected_trade_count'],
                initial_count=settings['selected_trade_count'], count_label='商盟自动贸易次数')
            settings = read_celebration_trade_auto_settings()
            before = read_celebration_trade_auto_result()
            if (not settings['default_plan_selected'] or settings['selected_trade_count'] != desired
                    or desired > before['license_count'] or before['money_count'] <= 0
                    or before['finish_window_open']):
                raise RuntimeError('商盟开始前资源、数量或窗口冲突')
            # A deferred attempt may arrive too close to this native child's
            # close time. Scheduler priority admission alone cannot protect a
            # game's own closing boundary. Never arm a batch across that edge.
            if not celebration_trade_batch_fits_window(now_epoch=time.time(),
                    end_time_ms=entry['period']['end_time_ms'], timeout_seconds=timeout_seconds):
                yield from context.go_scene(34)
                if manage_schedule:
                    context.set_next_time(None)
                return dict(result='success', window_exhausted=True,
                    activity_id=activity_id, runtime_id=runtime_id,
                    completed_trades=0, newly_started_trades=0,
                    remaining_licenses=before['license_count'], claimed_task_ids=reward_claims,
                    current_scene=34, next_time=None,
                    message='商盟剩余窗口不足以完成新批次及离场，保留文书')
            # Native auto play survives an observing Cell failure. Protect the
            # next higher-priority window before arming this irreversible batch;
            # an already armed batch above is reconciled without another Start.
            from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
                read_scheduler_batch_admission, read_scheduler_research_batch_admission,
            )
            admission = (read_scheduler_batch_admission(task_id=scheduler_task_id,
                batch_seconds=timeout_seconds, release_seconds=120)
                if scheduler_task_id else read_scheduler_research_batch_admission(
                    batch_seconds=timeout_seconds, release_seconds=120))
            if not admission['admitted']:
                yield from context.go_scene(34)
                next_time_text = datetime.fromisoformat(admission['retry_at']).strftime('%Y-%m-%d %H:%M:%S')
                if manage_schedule:
                    context.set_next_time(next_time_text)
                return dict(result='success', deferred=True, activity_id=activity_id,
                    runtime_id=runtime_id, completed_trades=0, newly_started_trades=0,
                    remaining_licenses=before['license_count'], claimed_task_ids=reward_claims,
                    current_scene=34, next_time=next_time_text, admission=admission,
                    message=f"商盟自动批次暂缓，保护作业 {admission['protected_task_id']}")
            action_id = arm_celebration_trade_batch(runtime_id, before, settings)
            context.click_shape_center(963, '开启自动')
            newly_started_trades = desired
            after = yield from _wait_native_auto_finish(context, timeout_seconds=timeout_seconds)
            receipt = settle_celebration_trade_batch(runtime_id, action_id, after)
    if receipt is not None and after['finish_window_open']:
        yield from context.wait_scene_exact([964], timeout=25)
        yield from context.wait_click(964, '确认')
        landed = yield from context.wait_scene_exact([962, 959, 34], timeout=25)
        if landed.scene_id == 962:
            yield from context.wait_click(962, '返回')
            yield from context.wait_scene_exact([959, 34], timeout=25)
    yield from context.go_scene(34)
    # Same occurrence evening checkpoints. Do not carry a stale Runtime ID
    # into tomorrow; activity discovery must bind its next occurrence.
    next_time_text = _write_evening_checkpoint(context, manage_schedule=manage_schedule)
    return dict(result='success', activity_id=activity_id, runtime_id=runtime_id,
        completed_trades=after['completed_count'], remaining_licenses=after['license_count'],
        newly_started_trades=newly_started_trades, recovered_existing_batch=bool(pending),
        native_result=after, receipt=receipt, current_scene=34,
        claimed_task_ids=reward_claims,
        next_time=next_time_text,
        message=f"商盟自动贸易完成{after['completed_count']}次，剩余文书{after['license_count']}")
