"""Final pinnacle rankings share the existing activity and ranking store."""
from datetime import datetime

from .ranking_lifecycle import discover_ranking_occurrences
from .runtime_schedule import read_fanxiu_activity_runtime_schedule


def current_shengxian_occurrence(*, instance_key=None):
    """Resolve the carousel parent, excluding the same-period child activity."""
    schedule = read_fanxiu_activity_runtime_schedule()
    if not schedule.get('complete'):
        raise RuntimeError('升仙会当前日程不完整')
    now = datetime.now().astimezone()
    matches = [o for o in discover_ranking_occurrences(schedule)
               if o.activity_type == 'shengxian-hui' and o.base_id == 10000
               and o.end_at <= now < o.close_at
               and (instance_key is None or o.instance_key == instance_key)]
    if len(matches) != 1:
        raise RuntimeError('无法唯一确认尚未关闭的升仙会结束期实例')
    return matches[0]


def store_shengxian_peak_rankings(session, *, occurrence, snapshot):
    """Validate final-stage identity and completeness before replacing rows.

    Repeated collection replaces the same occurrence/scope atomically through
    the shared store. An incomplete observation cannot erase an existing board.
    """
    from .ranking_reconcile import seed_ranking_occurrence
    from .exchange_event import replace_exchange_rankings
    if occurrence.activity_type != 'shengxian-hui' or occurrence.base_id != 10000:
        raise ValueError('只能保存升仙会日程父实例')
    if not snapshot.get('complete') or not snapshot.get('settled') or snapshot.get('stage') != 4:
        raise ValueError('只能保存已结算且完整的巅峰赛榜单')
    captured = datetime.fromisoformat(snapshot['captured_at'])
    if not occurrence.end_at <= captured < occurrence.close_at:
        raise ValueError('巅峰榜采集时间不在本期结束至关闭窗口')
    if snapshot.get('peak_end_ms') != int(occurrence.end_at.timestamp() * 1000):
        raise ValueError('巅峰赛结束时间与本期实例不一致')
    rows = snapshot['rankings']
    if not rows or [r['rank'] for r in rows] != list(range(1, len(rows) + 1)):
        raise ValueError('最终榜名次不完整')
    # Publish completeness in the shared ranking API's per-row contract.
    rows = [dict(row, is_last_player=index == len(rows) - 1,
                 raw_data={**(row.get('raw_data') or {}),
                           'reported_rank_list_size': len(rows),
                           'loaded_player_count': len(rows), 'scope_complete': True})
            for index, row in enumerate(rows)]
    activity = seed_ranking_occurrence(session, occurrence, captured_at=snapshot['captured_at'])
    evidence = dict(activity.evidence or {})
    evidence['shengxian_peak_final'] = {k: v for k, v in snapshot.items() if k != 'rankings'}
    evidence['shengxian_peak_final']['row_count'] = len(rows)
    evidence['refresh_status'] = {'rankings': 'complete', 'shop': 'not_applicable', 'currency': 'not_applicable'}
    evidence['rank_snapshot_kind'] = 'final'
    activity.evidence = evidence
    session.add(activity)
    replace_exchange_rankings(session, activity_type='shengxian-hui', activity_id=activity.id,
                              rows=rows, captured_at=snapshot['captured_at'], ranking_scopes={'personal'})
    return {'status': 'completed', 'activity_id': activity.id, 'instance_key': occurrence.instance_key,
            'rank_count': len(rows), 'self_rank': snapshot.get('self_rank'),
            'captured_at': snapshot['captured_at'], 'stage': 'peak_final'}


def collect_and_store_shengxian_peak_rankings(session, *, activity_id):
    """Refresh an already opened board; this API never navigates the game."""
    from backend.models import FanxiuExchangeActivity
    from backend.core.fanxiu.instrumentation.shengxian_hui import read_shengxian_peak_rank_snapshot
    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != 'shengxian-hui':
        raise ValueError('升仙会活动不存在')
    occurrence = current_shengxian_occurrence(instance_key=activity.instance_key)
    return store_shengxian_peak_rankings(session, occurrence=occurrence,
                                        snapshot=read_shengxian_peak_rank_snapshot())
