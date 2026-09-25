"""虚天尾日兑换：复用优先级规划、商品对齐及精确数量控制。"""
from datetime import datetime

from sqlmodel import Session
from backend.db import engine
from backend.core.fanxiu.activity.ranking_reconcile import seed_ranking_occurrence
from backend.core.fanxiu.activity.exchange_activity_registry import collect_registered_exchange_activity
from .xutian_open_collection import enter_xutian_exchange_shop, XUTIAN_SHOP_SCENE


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


def execute_xutian_exchange_tail_checkpoint(runner, ctx, payload, stop_event, *, occurrence, start_at_shop=False):
    """Replan from real bought counts on every run; never repeat a saved intent."""
    label = '虚天_兑换收尾'
    if not occurrence.end_at <= datetime.now().astimezone() < occurrence.close_at:
        raise RuntimeError('虚天收尾不在活动结束后的兑换窗口内')
    with Session(engine) as session:
        activity = seed_ranking_occurrence(session, occurrence,
            captured_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        activity_id = str(activity.id)
        session.commit()
    context = runner._behavior_tree_context(ctx, stop_event=stop_event)
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
        shop_base_id=80000, currency=15, label=label)
    from .gameplay_final_rankings import refresh_gameplay_final_rankings
    rankings = yield from refresh_gameplay_final_rankings(context,
        # The ranking page restores a map-stage tab. Explicitly load the
        # personal-total tab; page identity alone does not select that scope.
        tabs=((739,'虚天榜',453),(453,'个人',453),(453,'位面',454)),
        collect=lambda: store_xutian_final_rankings(activity_id))
    yield from context.go_scene(34)
    return dict(status='completed', activity_id=activity_id, rankings=rankings, **result)
