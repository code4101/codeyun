from __future__ import annotations

"""Deterministic contracts for the Lianti settlement collect + checkpoint reopen.

No game, GUI, or Kernel is touched here: the fact binding/date logic is a pure
function and the reopen path is exercised through the shared checkpoint store.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.lianti_faxiang import (
    _lianti_rank_snapshot_is_complete,
    _validate_lianti_fact_binding,
)
from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.activity.ranking_lifecycle_store import (
    completed_ranking_checkpoint_keys,
    record_ranking_checkpoint_result,
    reopen_failed_ranking_checkpoint,
)
from backend.models import FanxiuExchangeActivity, FanxiuRankingLifecycleCheckpoint


TZ = ZoneInfo("Asia/Shanghai")
START_AT = datetime(2026, 9, 18, 5, 0, 5, tzinfo=TZ)
END_AT = datetime(2026, 9, 18, 22, 0, tzinfo=TZ)
CLOSE_AT = datetime(2026, 9, 20, 23, 58, 59, tzinfo=TZ)
OCCURRENCE_RUNTIME_ID = "1043011-2026-09-18"


def _activity() -> FanxiuExchangeActivity:
    return FanxiuExchangeActivity(
        instance_key=f"runtime:{OCCURRENCE_RUNTIME_ID}:activity:1043011",
        activity_type="lianti-faxiang",
        runtime_id=OCCURRENCE_RUNTIME_ID,
        game_activity_id=1043011,
        start_at=START_AT.isoformat(timespec="seconds"),
        end_at=END_AT.isoformat(timespec="seconds"),
        close_at=CLOSE_AT.isoformat(timespec="seconds"),
        start_date=START_AT.date().isoformat(),
        end_date=END_AT.date().isoformat(),
        evidence={"runtime_id": OCCURRENCE_RUNTIME_ID},
    )


def _fact(
    captured_at: str,
    *,
    rank_vo_type: str = "runtime_memory_activity_rank",
    occurrence_runtime_id: str = OCCURRENCE_RUNTIME_ID,
) -> dict:
    return {
        "captured_at": captured_at,
        "rank_vo_type": rank_vo_type,
        "evidence": {"occurrence_runtime_id": occurrence_runtime_id},
    }


def test_settlement_runtime_fact_before_close_is_accepted() -> None:
    _validate_lianti_fact_binding(
        _fact("2026-09-20 00:50:00"),
        activity=_activity(),
        phase="settlement",
    )


def test_fact_after_close_panel_is_rejected() -> None:
    with pytest.raises(ValueError, match="不属于所选活动周期"):
        _validate_lianti_fact_binding(
            _fact("2026-09-21 00:10:00"),
            activity=_activity(),
            phase="settlement",
        )


def test_settlement_requires_runtime_fact() -> None:
    with pytest.raises(ValueError, match="结算期新采集"):
        _validate_lianti_fact_binding(
            _fact("2026-09-20 00:50:00", rank_vo_type="ActivityRankPersonalVO"),
            activity=_activity(),
            phase="settlement",
        )


def test_runtime_fact_with_foreign_occurrence_binding_is_rejected() -> None:
    with pytest.raises(ValueError, match="未绑定所选活动实例"):
        _validate_lianti_fact_binding(
            _fact("2026-09-20 00:50:00", occurrence_runtime_id="1043011-2026-08-14"),
            activity=_activity(),
            phase="settlement",
        )


def test_active_non_runtime_fact_keeps_date_range_compatibility() -> None:
    _validate_lianti_fact_binding(
        _fact(
            "2026-09-18 12:00:00",
            rank_vo_type="ActivityRankPersonalVO",
            occurrence_runtime_id="",
        ),
        activity=_activity(),
        phase="active",
    )


def test_runtime_fact_with_unparseable_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="缺少可解析的采集时刻"):
        _validate_lianti_fact_binding(
            _fact("not-a-timestamp"),
            activity=_activity(),
            phase="active",
        )


def _page(*ranks: int, total: int, declared: int) -> dict:
    return {
        "rank_list_size": total,
        "declared_rank_count": declared,
        "loaded_rank_count": declared,
        "rankings": [{"rank": rank, "score": 100 - rank} for rank in ranks],
    }


def test_partial_first_page_is_rejected() -> None:
    assert not _lianti_rank_snapshot_is_complete(
        _page(*range(1, 51), total=60, declared=50)
    )


def test_partial_tail_page_is_rejected() -> None:
    assert not _lianti_rank_snapshot_is_complete(
        _page(*range(51, 61), total=60, declared=10)
    )


def test_full_sixty_rows_is_accepted() -> None:
    assert _lianti_rank_snapshot_is_complete(
        _page(*range(1, 61), total=60, declared=60)
    )


def test_duplicate_or_missing_rank_is_rejected() -> None:
    rows = list(range(1, 61))
    rows[0] = 2  # duplicate rank 2, rank 1 missing
    assert not _lianti_rank_snapshot_is_complete(
        _page(*rows, total=60, declared=60)
    )


def test_zero_total_is_rejected() -> None:
    assert not _lianti_rank_snapshot_is_complete(
        _page(*range(1, 61), total=0, declared=60)
    )


def _occurrence() -> RankingOccurrence:
    return RankingOccurrence(
        activity_type="lianti-faxiang",
        family="gameplay_rank",
        runtime_id=OCCURRENCE_RUNTIME_ID,
        activity_id=1043011,
        start_at=START_AT,
        end_at=END_AT,
        prepare_at=START_AT,
        close_at=CLOSE_AT,
        cross_count=1,
    )


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _unavailable_checkpoint(session: Session) -> FanxiuRankingLifecycleCheckpoint:
    occurrence = _occurrence()
    from backend.core.fanxiu.activity.ranking_lifecycle import RankingCheckpoint

    checkpoint = RankingCheckpoint(
        instance_key=occurrence.instance_key,
        activity_type=occurrence.activity_type,
        family=occurrence.family,
        runtime_id=occurrence.runtime_id,
        activity_id=occurrence.activity_id,
        checkpoint_kind="daily_reconcile",
        business_date="2026-09-20",
        due_at=datetime(2026, 9, 20, 0, 10, tzinfo=TZ),
    )
    return record_ranking_checkpoint_result(
        session,
        checkpoint,
        status="unavailable",
        result={"terminal_reason": "activity_out_of_effective_dates"},
        evidence={"fact": "keep"},
        message="out of effective dates",
    )


def test_reopen_effective_dates_requires_matching_open_occurrence() -> None:
    occurrence = _occurrence()
    with _session() as session:
        _unavailable_checkpoint(session)
        args = dict(
            instance_key=occurrence.instance_key,
            checkpoint_kind="daily_reconcile",
            business_date="2026-09-20",
        )

        # No occurrence proof -> refused, row untouched.
        with pytest.raises(ValueError, match="Only legacy"):
            reopen_failed_ranking_checkpoint(session, **args)

        # Occurrence matches but the real clock is past close -> refused.
        with pytest.raises(ValueError, match="Only legacy"):
            reopen_failed_ranking_checkpoint(
                session,
                **args,
                occurrence=occurrence,
                now=datetime(2026, 9, 21, 0, 10, tzinfo=TZ),
            )

        # A still-open occurrence reopens to error, preserving history.
        repaired = reopen_failed_ranking_checkpoint(
            session,
            **args,
            occurrence=occurrence,
            now=datetime(2026, 9, 20, 0, 50, tzinfo=TZ),
        )
        assert repaired.status == "error"
        assert repaired.completed_at == repaired.retry_at == ""
        assert repaired.evidence == {"fact": "keep"}
        assert repaired.result == {"terminal_reason": "activity_out_of_effective_dates"}
        assert repaired.message == "out of effective dates"
        assert completed_ranking_checkpoint_keys(session) == set()


def test_reopen_effective_dates_rejects_foreign_occurrence_identity() -> None:
    occurrence = _occurrence()
    foreign = RankingOccurrence(
        activity_type=occurrence.activity_type,
        family=occurrence.family,
        runtime_id="1043011-2026-08-14",
        activity_id=occurrence.activity_id,
        start_at=occurrence.start_at,
        end_at=occurrence.end_at,
        prepare_at=occurrence.prepare_at,
        close_at=occurrence.close_at,
        cross_count=occurrence.cross_count,
    )
    with _session() as session:
        _unavailable_checkpoint(session)
        with pytest.raises(ValueError, match="Only legacy"):
            reopen_failed_ranking_checkpoint(
                session,
                instance_key=occurrence.instance_key,
                checkpoint_kind="daily_reconcile",
                business_date="2026-09-20",
                occurrence=foreign,
                now=datetime(2026, 9, 20, 0, 50, tzinfo=TZ),
            )
