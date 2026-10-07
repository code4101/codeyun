"""虚天尾日兑换：复用优先级规划、商品对齐及精确数量控制。"""
from datetime import datetime

from sqlmodel import Session
from backend.db import engine
from backend.core.fanxiu.activity.ranking_reconcile import seed_ranking_occurrence
from backend.core.fanxiu.activity.exchange_activity_registry import collect_registered_exchange_activity
from .xutian_open_collection import enter_xutian_exchange_shop, XUTIAN_SHOP_SCENE


def execute_xutian_exchange_tail_job(runner, ctx, payload, stop_event):
    """Explicit whole-attempt tail for an exact, ended occurrence.

    The lifecycle Job retains its ordinary schedule. An authorized early tail
    uses this same exchange/ranking provider after the real gameplay end;
    neither a synthetic business time nor a restored scene cursor is accepted.
    """
    from backend.core.fanxiu.activity.ranking_lifecycle import discover_ranking_occurrences
    from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
    instance_key = payload.get('instance_key')
    if not isinstance(instance_key, str) or not instance_key.strip():
        raise ValueError('虚天兑换收尾需要完整实例身份')
    now = datetime.now().astimezone()
    schedule = read_fanxiu_activity_runtime_schedule(allow_discovery=True, force_refresh=True)
    if not (schedule.get('available') and schedule.get('complete')):
        raise RuntimeError('虚天兑换收尾 Runtime 日程不完整')
    occurrences = [o for o in discover_ranking_occurrences(schedule)
                   if o.activity_type == 'xutian-palace' and o.instance_key == instance_key
                   and o.end_at <= now < o.close_at]
    if len(occurrences) != 1:
        raise RuntimeError('虚天兑换收尾实例不唯一或不在真实结束后窗口')
    result = yield from execute_xutian_exchange_tail_checkpoint(
        runner, ctx, payload, stop_event, occurrence=occurrences[0])
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    control.record_world_discovery('xutian-exchange-tail:' + instance_key,
                                  {**result, 'instance_key': instance_key,
                                   'captured_at': datetime.now().astimezone().isoformat()})
    return {**result, 'final_scene': 34}


def refresh_xutian_exchange_detail(activity_id, occurrence):
    """Refresh wallet and bought counts in the currently loaded shop window."""
    with Session(engine) as session:
        detail = collect_registered_exchange_activity(
            session, activity_type='xutian-palace', activity_id=activity_id,
        )
        session.commit()
    if detail is None or detail.id != activity_id or int(detail.game_activity_id) != occurrence.activity_id:
        raise RuntimeError('虚天兑换事实切换到其他实例')
    return detail


def store_xutian_final_rankings(activity_id):
    from sqlmodel import select
    from backend.models import FanxiuExchangeRanking
    from backend.core.fanxiu.activity.xutian_palace_instrumentation import collect_and_store_xutian_palace_rankings
    with Session(engine) as session:
        collect_and_store_xutian_palace_rankings(session, activity_id=activity_id, allow_discovery=True)
        rows = session.exec(select(FanxiuExchangeRanking).where(
            FanxiuExchangeRanking.activity_id == activity_id)).all()
        counts = {scope: sum(r.ranking_scope == scope for r in rows) for scope in ('personal','plane')}
        if not all(counts.values()):
            raise RuntimeError('虚天最终榜单不完整')
        session.commit()
    return counts


def execute_xutian_exchange_tail_checkpoint(runner, ctx, payload, stop_event, *, occurrence, start_at_shop=False, exchange_only=False):
    """Reuse a verified closing receipt, otherwise replan current bought facts.

    A receipt is a completed business result, never an intermediate click
    intent. Prayer preclose always reads current shop facts. Overflow execution
    may reuse only a receipt proving that the overflow allocation also settled.
    """
    label = '虚天_兑换收尾'
    from backend.core.fanxiu.activity.ranking_lifecycle import occurrence_prayer_exchange_window_contains
    now = datetime.now().astimezone()
    allowed = occurrence_prayer_exchange_window_contains(occurrence, now) if exchange_only else occurrence.end_at <= now < occurrence.close_at
    if not allowed:
        raise RuntimeError('虚天收尾不在活动结束后的兑换窗口内')
    with Session(engine) as session:
        activity = seed_ranking_occurrence(session, occurrence,
            captured_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        activity_id = str(activity.id)
        session.commit()
    context = runner._behavior_tree_context(ctx, stop_event=stop_event)
    # 独立正式收尾与玩法榜共用同一个业务完成凭证。有限兑换目标和
    # 结束后的双榜都已验收时，后续父作业只补其 checkpoint，不再访问
    # 过期商店或重复消费。周日前置兑换不复用；溢出兑换不能复用旧有限凭证。
    # Owner seq1417 confirms the existing overflow-pill policy. Unlimited
    # goods remain bounded by saved priorities and locked reserves; an explicit
    # False can still request finite-only execution.
    allow_overflow = payload.get('allow_xutian_unlimited_exchange', True) is True
    if not exchange_only:
        from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
        receipt = control.read_world_facts().get('discoveries', {}).get(
            'xutian-exchange-tail:' + occurrence.instance_key)
        completed = validated_xutian_tail_receipt(receipt, occurrence=occurrence,
                                                 activity_id=activity_id,
                                                 require_overflow=allow_overflow)
        if completed is not None:
            yield from context.go_scene(34)
            return {**completed, 'purchases': [],
                    'completion_source': 'formal_exchange_tail_receipt'}
    if start_at_shop:
        if int((yield from context.wait_scene([XUTIAN_SHOP_SCENE], wait=8))) != XUTIAN_SHOP_SCENE:
            raise RuntimeError('虚天兑换：局部验收必须位于兑换宝阁')
    else:
        yield from enter_xutian_exchange_shop(context, occurrence)
    from .gameplay_exchange_purchase import redeem_gameplay_exchange_shop
    result = yield from redeem_gameplay_exchange_shop(context,
        refresh_detail=lambda: refresh_xutian_exchange_detail(activity_id, occurrence),
        run_date=occurrence.end_at.date(), shop_scene=XUTIAN_SHOP_SCENE,
        # Wallet enum is 12; CommonShop costItemCfg uses Item.id 15 (纳元晶).
        shop_base_id=80000, cost_item_id=15, wallet_currency_type=12, label=label,
        finite_only=not allow_overflow)
    if exchange_only:
        yield from context.go_scene(34)
        return dict(status='completed', phase='prayer_preclose', activity_id=activity_id, **result)
    from .gameplay_final_rankings import refresh_gameplay_final_rankings
    rankings = yield from refresh_gameplay_final_rankings(context,
        # The ranking page restores a map-stage tab. Explicitly load the
        # personal-total tab; page identity alone does not select that scope.
        tabs=((739,'虚天榜',453),(453,'个人',453),(453,'位面',454)),
        collect=lambda: store_xutian_final_rankings(activity_id))
    yield from context.go_scene(34)
    return dict(status='completed', activity_id=activity_id, rankings=rankings,
                overflow_settled=allow_overflow, **result)


def validated_xutian_tail_receipt(receipt, *, occurrence, activity_id, require_overflow=False):
    """只复用同实例、真实结束后、有限目标及双榜均完整的正式凭证。"""
    if not isinstance(receipt, dict):
        return None
    if require_overflow and receipt.get('overflow_settled') is not True:
        return None
    planning, rankings = receipt.get('planning'), receipt.get('rankings')
    if (receipt.get('status') != 'completed'
            or receipt.get('instance_key') != occurrence.instance_key
            or receipt.get('activity_id') != activity_id
            or not isinstance(planning, dict)
            or planning.get('complete') is not True
            or planning.get('closing_goods_reached') is not True
            or not isinstance(rankings, dict)
            or any(type(rankings.get(scope)) is not int or rankings[scope] <= 0
                   for scope in ('personal', 'plane'))):
        return None
    try:
        captured_at = datetime.fromisoformat(receipt['captured_at'])
        if not occurrence.end_at <= captured_at < occurrence.close_at:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return dict(receipt)
