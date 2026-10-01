"""资源榜显式加载：配置绑定期次，页面证明身份，完整 Runtime 事实落库。"""
from sqlmodel import Session

from backend.db import engine
from backend.core.fanxiu.activity.ranking_reconcile import seed_ranking_occurrence
from backend.core.fanxiu.activity.standard_observation import store_runtime_activity_rank_fact
from backend.core.fanxiu.instrumentation.activity_rank_page import read_activity_rank_page_snapshot, PAGE_KIND_LOCAL_RANK
from backend.core.fanxiu.instrumentation.activity_rank_runtime import read_activity_rank_runtime_snapshot, prepare_activity_rank_runtime
from .resource_rank_daily_gift import RESOURCE_RANK_GIFT_ADAPTERS, open_resource_rank_activity_page


def refresh_resource_rank_page(context, *, occurrence, now):
    """只初始化和更新当前榜，不消耗养成资源；未开始的期次由父对账等待。"""
    adapter = next(a for a in RESOURCE_RANK_GIFT_ADAPTERS if a.key == occurrence.activity_type)
    scene = yield from open_resource_rank_activity_page(
        context, adapter, activity_id=occurrence.activity_id, now=now,
    )
    page = read_activity_rank_page_snapshot()
    if not page.get('complete') or page.get('activity_id') != occurrence.activity_id:
        raise RuntimeError(f'资源榜页面身份未对齐：{page}')
    # A previously open panel can still be at its last response page. Start
    # this explicit refresh with the occurrence's fresh first-page response.
    yield from context.click_shape_center_then_scene(scene, '返回', 66, timeout=20)
    scene = yield from open_resource_rank_activity_page(
        context, adapter, activity_id=occurrence.activity_id, now=now,
    )
    page = read_activity_rank_page_snapshot()
    if not page.get('complete') or page.get('activity_id') != occurrence.activity_id:
        raise RuntimeError(f'资源榜重载后页面身份未对齐：{page}')
    with Session(engine) as session:
        activity = seed_ranking_occurrence(session, occurrence, captured_at=now.isoformat())
        rank_id = activity.game_rank_activity_id
        session.commit()
    snapshot = read_activity_rank_runtime_snapshot(rank_id)
    if snapshot.get('error_code') in {'process_cache_miss', 'root_cache_miss'}:
        prepare_activity_rank_runtime([rank_id])
        snapshot = read_activity_rank_runtime_snapshot(rank_id)
    if not (snapshot.get('ok') and snapshot.get('complete')
            and snapshot.get('loaded_rank_count') == snapshot.get('rank_list_size')):
        from .lianti_faxiang import collect_lianti_rank_page
        from backend.core.fanxiu.activity.rank_page_merge import RankPageMergeError
        def reload_first_page():
            nonlocal scene
            yield from context.click_shape_center_then_scene(scene, '返回', 66, timeout=20)
            scene = yield from open_resource_rank_activity_page(
                context, adapter, activity_id=occurrence.activity_id, now=now,
            )
        for attempt in range(3):
            try:
                collected = yield from collect_lianti_rank_page(
                    context, activity_id=occurrence.activity_id, rank_activity_id=rank_id,
                    label=adapter.label, use_ui_rows=page.get('page_kind') != PAGE_KIND_LOCAL_RANK,
                    loaded_page_only=True,
                    reload_first_page=reload_first_page,
                    rank_scene_id=scene, rank_list_shape='排名列表',
                )
                break
            except RankPageMergeError:
                if attempt == 2:
                    raise
                # V_RankDic accumulates separate server responses. A player
                # moving across their boundary leaves duplicate identities in
                # that cache; reload the exact occurrence rather than editing
                # or silently deduplicating the observed facts.
                yield from context.click_shape_center_then_scene(scene, '返回', 66, timeout=20)
                scene = yield from open_resource_rank_activity_page(
                    context, adapter, activity_id=occurrence.activity_id, now=now,
                )
        snapshot = collected['rank']
    elif snapshot.get('rank_list_size', 0) > 0:
        from backend.core.fanxiu.activity.rank_page_merge import merge_activity_rank_pages
        snapshot = merge_activity_rank_pages(
            [snapshot], rank_activity_id=rank_id, captured_at=snapshot['captured_at'],
        )
    # A confirmed empty board (size=0, complete=True) is valid on opening day.
    # Missing/unloaded managers never reach the persistence API as empty data.
    with Session(engine) as session:
        store_runtime_activity_rank_fact(session, snapshot, occurrence_runtime_id=occurrence.runtime_id)
        session.commit()
    yield from context.click_shape_center_then_scene(scene, '返回', 66, timeout=20)
    yield from context.go_scene(34)
    return {'status': 'completed', 'rank_list_size': snapshot.get('rank_list_size')}
