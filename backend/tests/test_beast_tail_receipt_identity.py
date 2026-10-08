"""Early explicit settlement shares the scheduled receipt; no game simulation."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, create_engine

from backend.core.fanxiu.activity.ranking_lifecycle import (
    EXCHANGE_TAIL_KIND, RankingOccurrence, due_ranking_checkpoints,
)
from backend.core.fanxiu.activity.ranking_lifecycle_store import (
    completed_ranking_checkpoint_keys, ensure_ranking_lifecycle_checkpoint_table,
    record_ranking_checkpoint_result,
)
from backend.core.fanxiu.data_annotation.tasks.ranking_lifecycle import (
    exchange_tail_checkpoint_for_occurrence,
)

TZ = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize("close", [
    datetime(2026, 10, 9, 23, 58, 59, tzinfo=TZ),
    datetime(2026, 10, 9, 0, 30, tzinfo=TZ),
])
def test_early_tail_keeps_scheduled_identity_and_is_skipped_after_sealing(tmp_path, close):
    occurrence = RankingOccurrence(
        activity_type="beast-abyss", family="gameplay_rank",
        runtime_id="8150001400004", activity_id=8150001,
        prepare_at=datetime(2026, 10, 6, 5, tzinfo=TZ),
        start_at=datetime(2026, 10, 7, 10, tzinfo=TZ),
        end_at=datetime(2026, 10, 8, 22, tzinfo=TZ),
        close_at=close, cross_count=8,
    )
    checkpoint = exchange_tail_checkpoint_for_occurrence(occurrence)
    assert checkpoint.checkpoint_kind == EXCHANGE_TAIL_KIND
    assert checkpoint.business_date == "2026-10-09"
    assert checkpoint.due_at == datetime(2026, 10, 9, 0, 10, tzinfo=TZ)
    engine = create_engine(f"sqlite:///{tmp_path / 'receipts.sqlite'}")
    ensure_ranking_lifecycle_checkpoint_table(engine)
    with Session(engine) as session:
        record_ranking_checkpoint_result(
            session, checkpoint, status="completed",
            completed_at=datetime(2026, 10, 8, 22, 15, tzinfo=TZ),
            result={"phase": "exchange_tail"},
        )
        completed = completed_ranking_checkpoint_keys(session, family="gameplay_rank")
    assert checkpoint.key in completed
    due = due_ranking_checkpoints(
        (occurrence,), now=datetime(2026, 10, 9, 0, 10, tzinfo=TZ),
        completed_keys=completed,
    )
    assert all(item.key != checkpoint.key for item in due)
