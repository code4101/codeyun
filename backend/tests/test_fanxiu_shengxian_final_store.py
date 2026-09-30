"""Final-board atomic replacement and public completeness contract."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import SQLModel, Session, create_engine

from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.activity.shengxian_hui import store_shengxian_peak_rankings
from backend.core.fanxiu.activity.exchange_event import list_exchange_rankings


def test_final_board_replacement_is_complete_idempotent_and_rejects_partial():
    tz = ZoneInfo('Asia/Shanghai')
    occurrence = RankingOccurrence(
        activity_type='shengxian-hui', family='gameplay_rank',
        runtime_id='16010001400004', activity_id=16010001, base_id=10000,
        cross_count=16, prepare_at=datetime(2026, 9, 29, 5, tzinfo=tz),
        start_at=datetime(2026, 9, 29, 10, tzinfo=tz),
        end_at=datetime(2026, 9, 29, 22, 35, tzinfo=tz),
        close_at=datetime(2026, 9, 29, 23, 58, 59, tzinfo=tz),
    )
    snapshot = dict(complete=True, settled=True, stage=4,
                    captured_at='2026-09-29T23:30:00+08:00',
                    peak_end_ms=int(occurrence.end_at.timestamp() * 1000),
                    rankings=[dict(rank=n, role_key=str(n), name=f'角色{n}',
                                   score=100-n, is_self=n == 2) for n in range(1, 4)])
    engine = create_engine('sqlite:///:memory:')
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for _ in range(2):
            result = store_shengxian_peak_rankings(session, occurrence=occurrence, snapshot=snapshot)
        with pytest.raises(ValueError):
            store_shengxian_peak_rankings(session, occurrence=occurrence,
                                         snapshot={**snapshot, 'complete': False})
        page = list_exchange_rankings(session, activity_type='shengxian-hui',
                                      activity_id=result['activity_id'], ranking_scope='personal')
        assert page.complete and page.declared_rank_count == page.entry_total == 3
        assert [row.rank for row in page.entries] == [1, 2, 3]
        assert page.self_entry.rank == 2
        assert page.last_entry.rank == 3
