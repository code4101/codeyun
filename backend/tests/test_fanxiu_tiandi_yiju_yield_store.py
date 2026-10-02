"""Batch journal transaction tests; no game Runtime or GUI doubles."""
import pytest
from sqlmodel import Session, create_engine

from backend.models import FanxiuExchangeActivity
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_yield import (
    TIANDI_YIJU_YIELD_LEDGER_KEY, record_tiandi_yiju_completed_batch,
    list_tiandi_yiju_completed_batches,
)


@pytest.fixture
def engine():
    value = create_engine("sqlite://")
    FanxiuExchangeActivity.__table__.create(value)
    with Session(value) as session:
        session.add(FanxiuExchangeActivity(
            id="board", instance_key="occurrence", activity_type="tiandi-yiju",
            start_date="2026-10-02", end_date="2026-10-03", evidence={"preserved": True},
        ))
        session.commit()
    yield value
    value.dispose()


def test_batch_watermark_deduplicates_across_sessions_and_retains_other_batches(engine):
    for watermark, expected in [("pid:ticks:6090", True), ("pid:ticks:6090", False), ("pid:ticks:12180", True)]:
        with Session(engine) as session:
            assert record_tiandi_yiju_completed_batch(
                session, activity_id="board", occurrence_instance_key="occurrence",
                batch_id=watermark, rounds=100, currency_delta=6090,
                process_identity=(13, "wallet", 2737, 6230), feature_item_usage=None,
            ) is expected
    with Session(engine) as session:
        evidence = session.get(FanxiuExchangeActivity, "board").evidence
        assert evidence["preserved"] is True
        assert len(evidence[TIANDI_YIJU_YIELD_LEDGER_KEY]) == 2
        receipts = list_tiandi_yiju_completed_batches(
            session, activity_id="board", occurrence_instance_key="occurrence"
        )
        assert [row["batch_id"] for row in receipts] == ["pid:ticks:6090", "pid:ticks:12180"]


def test_wrong_occurrence_cannot_write_batch(engine):
    with Session(engine) as session:
        with pytest.raises(RuntimeError, match="身份不一致"):
            record_tiandi_yiju_completed_batch(
                session, activity_id="board", occurrence_instance_key="sibling",
                batch_id="pid:ticks:6090", rounds=100, currency_delta=6090,
                process_identity=(13, "wallet", 2737, 6230), feature_item_usage=None,
            )
        with pytest.raises(RuntimeError, match="身份不一致"):
            list_tiandi_yiju_completed_batches(
                session, activity_id="board", occurrence_instance_key="sibling"
            )
        assert session.get(FanxiuExchangeActivity, "board").evidence == {"preserved": True}
