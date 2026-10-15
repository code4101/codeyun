"""Explicit map leaderboard refresh and the first-day 21:00 Yandi census."""
import json
from datetime import datetime, time

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.xutian_runtime import read_xutian_map_progress_snapshot
from backend.core.fanxiu.instrumentation.activity_rank_runtime import read_activity_rank_runtime_snapshot
from backend.core.fanxiu.activity.xutian_population_planning import plan_xutian_yandi_day
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
from .xutian_open_collection import enter_xutian_activity_home


def open_xutian_map_rank_page(context, *, occurrence):
    """Open this occurrence's map board and observe its selected native tab.

    No population or selected-map identity is inferred from cached ranks.
    The caller owns selecting a verified map and safely closing scene #453.
    """
    from backend.core.fanxiu.instrumentation.xutian_rank_page import read_xutian_rank_page_snapshot
    home = yield from enter_xutian_activity_home(context, occurrence)
    if home != 616:
        raise RuntimeError('虚天榜入口尚未验收该首页布局')
    yield from context.wait_click(616, '虚天榜')
    yield from context.wait_scene([453], wait=30)
    page = read_xutian_rank_page_snapshot(int(occurrence.activity_id))
    return {'instance_key': occurrence.instance_key,
            'page_identity_complete': page.get('complete') is True,
            'map_progress': read_xutian_map_progress_snapshot(activity_id=int(occurrence.activity_id)),
            'page_snapshot': page}


def inspect_xutian_map_population(context, *, occurrence):
    """Refresh the actual current-map board; this never consumes items.

    Native map definitions supply rankBaseId. The exact owning Activity.follow
    supplies its rank ID; an unrelated loaded leaderboard cannot choose it.
    Scene readiness requires the loaded ranking page rather than its blank
    transition. This snapshot is a census at observation time, not a forecast.
    """
    home = yield from enter_xutian_activity_home(context, occurrence)
    if home != 616:
        raise RuntimeError("虚天人数采集尚未验收该活动首页布局")
    context.click_shape_center(616, '虚天榜')
    yield from context.wait_scene([453], wait=30)
    progress = read_xutian_map_progress_snapshot(activity_id=int(occurrence.activity_id))
    current = next((m for m in progress['map_definitions']
                    if int(m['id']) == progress['current_heaven']), None)
    if current is None:
        raise RuntimeError("虚天人数采集缺少当前地图的原生榜域")
    rows = json.loads((resolve_fanxiu_export_root() / 'parsed_configs/Activity/rows.json').read_text(encoding='utf-8'))
    parents = [r for r in rows if int(r['id']) == int(occurrence.activity_id)
               and int(r.get('crossGroup') or 1) == int(occurrence.cross_count)]
    if len(parents) != 1:
        raise RuntimeError("虚天人数采集父活动身份不唯一")
    follow = set(parents[0]['follow'])
    matches = [int(r['id']) for r in rows if r['id'] in follow
               and int(r.get('baseId') or 0) == int(current['rankBaseId'])]
    if len(matches) != 1:
        raise RuntimeError("虚天人数采集地图榜域不唯一")
    if int(current['sort']) != 1:
        # A later-day board defaults to its map tab, but cached manager ranks
        # cannot prove that selection. Require the real template rank leaf.
        from backend.core.fanxiu.instrumentation.xutian_rank_page import read_xutian_rank_page_snapshot
        page = read_xutian_rank_page_snapshot(int(occurrence.activity_id))
        if (page.get('complete') is not True
                or page.get('selected_rank_activity_id') != matches[0]
                or any(page['evidence'].get(k) != progress['evidence'].get(k)
                       for k in ('pid', 'process_start_ticks'))):
            raise RuntimeError('虚天当前地图的原生选中榜域尚未对齐，禁止使用旧榜缓存')
    rank = read_activity_rank_runtime_snapshot(matches[0])
    if (rank.get('ok') is not True or rank.get('complete') is not True
            or type(rank.get('rank_list_size')) is not int or rank['rank_list_size'] <= 0
            or any(rank['evidence'].get(k) != progress['evidence'].get(k)
                   for k in ('pid', 'process_start_ticks'))):
        raise RuntimeError(f"虚天人数采集未取得完整同进程榜单：{rank.get('reason')}")
    result = {'instance_key': occurrence.instance_key, 'captured_at': rank['captured_at'],
              'population': rank['rank_list_size'], 'rank_activity_id': matches[0],
              'map_progress': progress, 'rank_snapshot': rank, 'gui_refreshed': True}
    yield from context.go_scene(34)
    return result


def record_xutian_daily_population(context, *, occurrence, business_date):
    """Record one later-day current-map census per business date.

    Day one keeps its dedicated 21:00 census. Later days observe the board of
    the map actually entered today; the receipt is evidence only and never
    rewrites the accepted Yandi day plan or consumes items.
    """
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts
    if business_date == occurrence.start_at.date().isoformat():
        return {'status': 'not_applicable', 'message': '首日人数由21点基准采集'}
    key = 'xutian-daily-population:' + occurrence.instance_key + ':' + business_date
    previous = read_world_facts().get('discoveries', {}).get(key)
    if isinstance(previous, dict) and previous.get('status') == 'observed':
        return {**previous, 'replayed_actions': False}
    snapshot = yield from inspect_xutian_map_population(context, occurrence=occurrence)
    progress = snapshot['map_progress']
    result = {'status': 'observed', 'instance_key': occurrence.instance_key,
              'business_date': business_date, 'captured_at': snapshot['captured_at'],
              'current_heaven': progress['current_heaven'],
              'rank_activity_id': snapshot['rank_activity_id'],
              'population': snapshot['population'],
              'evidence': snapshot['rank_snapshot']['evidence'], 'consumed': False}
    record_world_discovery(key, result)
    return result


def execute_xutian_population_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
    """Record one occurrence's real first-day 21:00 census and selected day."""
    from backend.core.fanxiu.data_annotation.effective_time import job_now
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts
    key = 'xutian-yandi-day-plan:' + occurrence.instance_key
    previous = read_world_facts().get('discoveries', {}).get(key)
    if (isinstance(previous, dict) and previous.get('status') == 'completed'
            and previous.get('instance_key') == occurrence.instance_key
            and previous.get('owner_seq') == 3023):
        snipe = yield from execute_planned_xutian_yandi(runner, ctx, payload, stop_event)
        return {**previous, 'snipe': snipe,
                'war_order_consumed': snipe['status'] == 'sniped_verified',
                'message': '复用已验收的21点人数选日计划'}
    now = job_now().astimezone(occurrence.start_at.tzinfo)
    census_at = datetime.combine(occurrence.start_at.date(), time(21), tzinfo=occurrence.start_at.tzinfo)
    if now < census_at:
        return {'status': 'pending', 'retry_at': census_at.isoformat(), 'message': '等待首日21点虚天人数基准'}
    if now.date() != occurrence.start_at.date() or now >= occurrence.end_at:
        raise RuntimeError("错过虚天首日21点真实人数基准，不能用后日人数补造")
    context = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
    snapshot = yield from inspect_xutian_map_population(context, occurrence=occurrence)
    plan = plan_xutian_yandi_day(start_date=occurrence.start_at.date(),
        first_day_population=snapshot['population'],
        map_definitions=snapshot['map_progress']['map_definitions'],
        thresholds=snapshot['map_progress']['configured_thresholds'])
    result = {**plan, 'instance_key': occurrence.instance_key, 'owner_seq': 3023,
              'census': snapshot, 'status': 'completed', 'war_order_consumed': False}
    record_world_discovery(key, result)
    snipe = yield from execute_planned_xutian_yandi(runner, ctx, payload, stop_event)
    return {**result, 'snipe': snipe,
            'war_order_consumed': snipe['status'] == 'sniped_verified',
            'message': f"首日21点人数{snapshot['population']}，炎帝计划日{plan['selected_date']}，当前处理{snipe['status']}"}


def execute_planned_xutian_yandi(runner, ctx, payload, stop_event):
    """Reuse the single-decree provider only on the verified selected day."""
    from backend.core.fanxiu.data_annotation.effective_time import job_now
    from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
    from .xutian_yandi_prepare import xutian_yandi_window, execute_xutian_yandi_snipe_job
    window = xutian_yandi_window(job_now(), read_fanxiu_activity_runtime_schedule(allow_discovery=True))
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts
    key = 'xutian-yandi-once:' + str(window.get('instance_key') or '')
    previous = read_world_facts().get('discoveries', {}).get(key)
    if isinstance(previous, dict) and previous.get('outcome') == 'sniped_verified':
        return {'status': 'sniped_verified', 'receipt': previous, 'replayed_actions': False}
    if not window['eligible'] and not previous:
        return {'status': 'not_due', 'window': window}
    policy = payload.get('xutian_yandi_target_policy')
    if policy != 'first_recommended':
        raise RuntimeError('炎帝选日已到，但未配置已授权的推荐首位目标策略')
    result = yield from execute_xutian_yandi_snipe_job(runner, ctx, {'target_policy': policy}, stop_event)
    if result.get('outcome') != 'sniped_verified':
        raise RuntimeError('炎帝单次消费尚未验收战报，保留凭证禁止重放')
    context = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
    yield from context.go_scene(34)
    return {'status': 'sniped_verified', 'receipt': result}
