"""配置语义识别与实例定位正交；不模拟游戏流程。"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.core.fanxiu.activity.ranking_lifecycle import discover_ranking_occurrences
from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import active_resource_rank_gift_adapters
from backend.core.fanxiu.data_annotation.schedule_cards import align_schedule_card_title


@pytest.mark.parametrize('name,family', [('洗灵证武', 'resource_rank'), ('丹道问鼎', 'resource_rank')])
def test_configuration_family_accepts_new_template_and_instance(name, family):
    now = datetime(2026, 9, 27, 9, tzinfo=timezone(timedelta(hours=8)))
    rows = [dict(id=99900001, activityId=999999, name=name, identityComplete=True,
                 activityType=4, serverCount=1, startTime=int(now.timestamp()*1000),
                 endTime=int((now+timedelta(hours=12)).timestamp()*1000))]
    result = discover_ranking_occurrences({'items': rows})
    assert len(result) == 1 and result[0].family == family
    assert result[0].activity_id == 999999 and result[0].runtime_id == '99900001'
    gifts = active_resource_rank_gift_adapters({'occurrences': [dict(
        name=name, identity_complete=True, activity_id=999999,
        start_at=now.isoformat(), end_at=(now+timedelta(hours=12)).isoformat(),
    )]}, now=now)
    assert len(gifts) == 1 and gifts[0][1] == 999999


def test_explicit_preliminary_title_excludes_cross_server_sibling():
    rows = [dict(key='pre', title='洗灵证武(预赛)'),
            dict(key='cross', title='洗灵证武跨服[4]')]
    result = align_schedule_card_title('洗灵证武（预赛）', {'complete': True, 'items': rows})
    assert result['status'] == 'aligned' and result['task']['key'] == 'pre'


def test_future_resource_reconcile_waits_for_start_without_live_collection(monkeypatch):
    from types import SimpleNamespace
    from sqlmodel import Session, create_engine
    from backend.core.fanxiu.activity import ranking_reconcile as module
    from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
    now = datetime(2026, 9, 27, 9, tzinfo=timezone(timedelta(hours=8)))
    occurrence = RankingOccurrence(
        activity_type='xiling-zhengwu', family='resource_rank', runtime_id='future',
        activity_id=999999, cross_count=4, prepare_at=now,
        start_at=now+timedelta(days=1), end_at=now+timedelta(days=2), close_at=now+timedelta(days=2),
    )
    monkeypatch.setattr(module, 'seed_ranking_occurrence', lambda *a, **kw: SimpleNamespace(id='seeded'))
    def unexpected(*a, **kw):
        pytest.fail('future occurrence must not collect live facts')
    monkeypatch.setattr(module, 'materialize_registered_exchange_activity', unexpected)
    monkeypatch.setattr(module, 'collect_registered_exchange_activity', unexpected)
    with Session(create_engine('sqlite://')) as session:
        result = module.reconcile_ranking_occurrence(session, occurrence, captured_at=now.isoformat())
    assert result['status'] == 'pending'
    assert result['retry_at'] == occurrence.start_at.isoformat(timespec='seconds')
