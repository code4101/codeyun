"""资源榜显式加载：配置绑定期次，页面证明身份，完整 Runtime 事实落库。"""
from sqlmodel import Session

from backend.db import engine
from backend.core.fanxiu.activity.ranking_reconcile import seed_ranking_occurrence
from backend.core.fanxiu.activity.standard_observation import store_runtime_activity_rank_fact
from backend.core.fanxiu.instrumentation.activity_rank_page import read_activity_rank_page_snapshot, PAGE_KIND_LOCAL_RANK, PAGE_KIND_SERVER_RANK
from backend.core.fanxiu.instrumentation.activity_rank_runtime import read_activity_rank_runtime_snapshot, prepare_activity_rank_runtime
from .resource_rank_daily_gift import RESOURCE_RANK_GIFT_ADAPTERS, ResourceRankGiftAdapter, open_resource_rank_activity_page


# The preliminary uses ActivityRankMainView, not the alliance leaderboard
# (#745) or the separate peak-race stage (#702). Keep its page identity out of
# the free-gift registry until that business flow has its own live acceptance.
LINGCHONG_PRELIMINARY_RANK_ADAPTER = ResourceRankGiftAdapter(
    key='lingchong-jingwu', label='灵宠竞武', schedule_pattern=r'灵宠竞武',
    activity_ids=(1042911,), page_scene_ids=(933,), intro_scene_id=744,
    schedule_entry='card',
)

# Live 16042901 acceptance: the cross-server personal board has its own
# personal/plane tabs and rank-list geometry. Neither #933 nor the alliance
# board #745 proves this page. Runtime additionally binds the exact occurrence
# and the personal tab before any rank facts can be stored.
LINGCHONG_CROSS_RANK_ADAPTER = ResourceRankGiftAdapter(
    key='lingchong-jingwu', label='灵宠竞武', schedule_pattern=r'灵宠竞武',
    activity_ids=(2042901, 4042901, 8042901, 16042901, 32042901),
    page_scene_ids=(938, 939), intro_scene_id=744, schedule_entry='card',
)


def resource_rank_page_matches_occurrence(page, occurrence, *, scope='personal', rank_activity_id=None):
    """Require an exact panel and requested scope, never an old cached board."""
    if not page.get('complete') or page.get('activity_id') != occurrence.activity_id:
        return False
    if occurrence.activity_type in {'lingchong-jingwu', 'yaochi-flower-festival'}:
        kind = PAGE_KIND_LOCAL_RANK if occurrence.cross_count == 1 else PAGE_KIND_SERVER_RANK
        if scope not in {'personal', 'plane'} or (scope == 'plane' and occurrence.cross_count == 1):
            return False
        return (page.get('page_kind') == kind and page.get('tab_index') == 0
                and (occurrence.cross_count == 1 or (
                    rank_activity_id is not None
                    and page.get('selected_rank_activity_id') == rank_activity_id
                )))
    return True


def refresh_resource_rank_page(context, *, occurrence, now):
    """刷新本期榜域；跨服灵宠、花会采集个人与位面，不消耗养成资源。"""
    scopes = ('personal', 'plane') if (
        occurrence.activity_type in {'lingchong-jingwu', 'yaochi-flower-festival'}
        and occurrence.cross_count != 1
    ) else ('personal',)
    results = {}
    for scope in scopes:
        results[scope] = yield from refresh_resource_rank_scope_page(
            context, occurrence=occurrence, now=now, scope=scope,
        )
    yield from context.go_scene(34)
    return {'status': 'completed', 'scopes': results}


def refresh_resource_rank_scope_page(context, *, occurrence, now, scope='personal'):
    """Load and persist one exact scope; every reload restores that same tab."""
    if occurrence.activity_type == 'lingchong-jingwu':
        adapter = (LINGCHONG_PRELIMINARY_RANK_ADAPTER if occurrence.cross_count == 1
                   else LINGCHONG_CROSS_RANK_ADAPTER)
    else:
        adapter = next(a for a in RESOURCE_RANK_GIFT_ADAPTERS if a.key == occurrence.activity_type)
    with Session(engine) as session:
        activity = seed_ranking_occurrence(session, occurrence, captured_at=now.isoformat())
        rank_id = (activity.game_rank_activity_id if scope == 'personal' else
                   (activity.instance_data or {})['rank_scope_identities'][scope]['runtime_rank_activity_id'])
        session.commit()

    def page_matches(page):
        return resource_rank_page_matches_occurrence(
            page, occurrence, scope=scope, rank_activity_id=rank_id,
        )
    def open_scope_page():
        scene = yield from open_resource_rank_activity_page(
            context, adapter, activity_id=occurrence.activity_id, now=now,
        )
        if occurrence.activity_type in {'lingchong-jingwu', 'yaochi-flower-festival'} and occurrence.cross_count != 1:
            personal_scene, plane_scene = (
                (938, 939) if occurrence.activity_type == 'lingchong-jingwu'
                else (458, 459)
            )
            target = personal_scene if scope == 'personal' else plane_scene
            if scene != target:
                tab_shape = ('个人页签' if scope == 'personal' else '位面页签') if (
                    occurrence.activity_type == 'lingchong-jingwu'
                ) else ('个人' if scope == 'personal' else '位面')
                scene = yield from context.wait_click_then_scene(
                    scene, tab_shape,
                    target, timeout=20,
                )
                scene = int(getattr(scene, 'id', scene))
        return scene

    scene = yield from open_scope_page()
    page = read_activity_rank_page_snapshot()
    if not page_matches(page):
        raise RuntimeError(f'资源榜页面身份未对齐：{page}')
    # A previously open panel can still be at its last response page. Start
    # this explicit refresh with the occurrence's fresh first-page response.
    yield from context.click_shape_center_then_scene(scene, '返回', 66, timeout=20)
    scene = yield from open_scope_page()
    page = read_activity_rank_page_snapshot()
    if not page_matches(page):
        raise RuntimeError(f'资源榜重载后页面身份未对齐：{page}')
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
            scene = yield from open_scope_page()
            if not page_matches(read_activity_rank_page_snapshot()):
                raise RuntimeError('资源榜分页重载未恢复所选榜域')
        for attempt in range(3):
            try:
                collected = yield from collect_lianti_rank_page(
                    context, activity_id=occurrence.activity_id, rank_activity_id=rank_id,
                    label=adapter.label, use_ui_rows=page.get('page_kind') != PAGE_KIND_LOCAL_RANK,
                    loaded_page_only=True,
                    reload_first_page=reload_first_page,
                    # Cross-32 boards now exceed 100 rows. The live table read
                    # costs about 13s; batches of eight proven short drags stay
                    # below one 50-row response page while reducing read cost.
                    # 230-row live board needs more than the former 40 drags
                    # (only 200 rows loaded). Keep explicit finite budgets and
                    # the unchanged complete/unique/stable coverage gates.
                    collect_deadline_seconds=600,
                    drag_batch_size=8,
                    max_drags=80,
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
                scene = yield from open_scope_page()
                if not page_matches(read_activity_rank_page_snapshot()):
                    raise RuntimeError('资源榜重试未恢复所选榜域')
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
    return {'status': 'completed', 'rank_list_size': snapshot.get('rank_list_size')}
