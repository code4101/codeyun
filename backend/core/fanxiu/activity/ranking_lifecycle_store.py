from __future__ import annotations

"""Durable checkpoint store for the shared ranking lifecycle Job."""

from datetime import datetime
import time
from typing import Any, Iterable, Mapping

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from backend.core.fanxiu.activity.ranking_lifecycle import (
    RankingCheckpoint,
    RankingOccurrence,
)
from backend.models import FanxiuRankingLifecycleCheckpoint


TERMINAL_CHECKPOINT_STATUSES = frozenset({"completed", "retained", "unavailable"})


def ensure_ranking_lifecycle_checkpoint_table(bind: Engine) -> None:
    FanxiuRankingLifecycleCheckpoint.__table__.create(bind, checkfirst=True)


def completed_ranking_checkpoint_keys(
    session: Session,
    *,
    family: str | None = None,
) -> set[tuple[str, str, str]]:
    statement = select(FanxiuRankingLifecycleCheckpoint).where(
        FanxiuRankingLifecycleCheckpoint.status.in_(TERMINAL_CHECKPOINT_STATUSES)
    )
    if family is not None:
        statement = statement.where(FanxiuRankingLifecycleCheckpoint.family == family)
    rows = session.exec(statement).all()
    return {
        (row.instance_key, row.checkpoint_kind, row.business_date)
        for row in rows
    }


def ranking_checkpoint_retry_times(
    session: Session,
    *,
    family: str | None = None,
) -> tuple[datetime, ...]:
    result: list[datetime] = []
    statement = select(FanxiuRankingLifecycleCheckpoint).where(
        FanxiuRankingLifecycleCheckpoint.retry_at != ""
    )
    if family is not None:
        statement = statement.where(FanxiuRankingLifecycleCheckpoint.family == family)
    rows = session.exec(statement).all()
    for row in rows:
        try:
            value = datetime.fromisoformat(row.retry_at)
        except ValueError:
            continue
        if value.tzinfo is not None:
            result.append(value)
    return tuple(result)


def record_ranking_checkpoint_result(
    session: Session,
    checkpoint: RankingCheckpoint,
    *,
    status: str,
    message: str = "",
    result: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    retry_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> FanxiuRankingLifecycleCheckpoint:
    """Insert or update one exact checkpoint without touching siblings."""

    normalized = str(status or "").strip().lower()
    allowed = {*TERMINAL_CHECKPOINT_STATUSES, "pending", "blocked", "error"}
    if normalized not in allowed:
        raise ValueError(f"未知榜单 checkpoint 状态：{status}")
    if retry_at is not None and retry_at.tzinfo is None:
        raise ValueError("榜单 checkpoint retry_at 必须带时区")
    if completed_at is not None and completed_at.tzinfo is None:
        raise ValueError("榜单 checkpoint completed_at 必须带时区")
    row = session.exec(
        select(FanxiuRankingLifecycleCheckpoint).where(
            FanxiuRankingLifecycleCheckpoint.instance_key
            == checkpoint.instance_key,
            FanxiuRankingLifecycleCheckpoint.checkpoint_kind
            == checkpoint.checkpoint_kind,
            FanxiuRankingLifecycleCheckpoint.business_date
            == checkpoint.business_date,
        )
    ).first()
    if row is None:
        row = FanxiuRankingLifecycleCheckpoint(
            activity_type=checkpoint.activity_type,
            family=checkpoint.family,
            instance_key=checkpoint.instance_key,
            runtime_id=checkpoint.runtime_id,
            activity_id=checkpoint.activity_id,
            checkpoint_kind=checkpoint.checkpoint_kind,
            business_date=checkpoint.business_date,
            due_at=checkpoint.due_at.isoformat(timespec="seconds"),
        )
    row.status = normalized
    row.attempt_count = int(row.attempt_count or 0) + 1
    row.retry_at = (
        retry_at.isoformat(timespec="seconds") if retry_at is not None else ""
    )
    row.completed_at = (
        (completed_at or datetime.now().astimezone()).isoformat(timespec="seconds")
        if normalized in TERMINAL_CHECKPOINT_STATUSES
        else ""
    )
    row.message = str(message or "")
    row.result = dict(result or {})
    if evidence is not None:
        row.evidence = dict(evidence)
    row.updated_at = time.time()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def reopen_failed_ranking_checkpoint(
    session: Session,
    *,
    instance_key: str,
    checkpoint_kind: str,
    business_date: str,
    occurrence: "RankingOccurrence | None" = None,
    now: datetime | None = None,
) -> FanxiuRankingLifecycleCheckpoint:
    """Reopen one technical failure without recording a new attempt.

    Two paths qualify:

    * the legacy retry-budget/error marker (unchanged); and
    * an ``activity_out_of_effective_dates`` terminal that the caller proves is
      still open by passing the real Runtime ``occurrence`` and ``now``.  The
      occurrence must match this row's ``instance_key``/``runtime_id``/
      ``activity_id`` and satisfy ``prepare_at <= now <= close_at``. A future
      start is not an expired activity; reconciliation will wait for start_at.

    Completed, retained and genuine business-unavailable outcomes are
    protected.  The caller still owns scheduling the family Job; this function
    never touches the Scheduler or game.  Attempt history, result and evidence
    remain intact, and the update stays a compare-and-swap on ``updated_at``.
    """

    row = session.exec(
        select(FanxiuRankingLifecycleCheckpoint).where(
            FanxiuRankingLifecycleCheckpoint.instance_key == instance_key,
            FanxiuRankingLifecycleCheckpoint.checkpoint_kind == checkpoint_kind,
            FanxiuRankingLifecycleCheckpoint.business_date == business_date,
        )
    ).one_or_none()
    if row is None:
        raise ValueError("Ranking checkpoint does not exist")
    result = dict(row.result or {})
    legacy_failure = bool(result.get("error_type")) or (
        result.get("terminal_reason") == "implicit_retry_budget_exhausted"
    )
    if legacy_failure:
        qualifies = row.status == "unavailable"
    else:
        qualifies = (
            row.status == "unavailable"
            and result.get("terminal_reason") == "activity_out_of_effective_dates"
            and _occurrence_proves_open(row, occurrence=occurrence, now=now)
        )
    if not qualifies:
        raise ValueError("Only legacy technical-unavailable checkpoints can be reopened")
    changed = session.exec(
        update(FanxiuRankingLifecycleCheckpoint)
        .where(
            FanxiuRankingLifecycleCheckpoint.id == row.id,
            FanxiuRankingLifecycleCheckpoint.status == "unavailable",
            FanxiuRankingLifecycleCheckpoint.updated_at == row.updated_at,
        )
        .values(status="error", completed_at="", retry_at="", updated_at=time.time())
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        session.rollback()
        raise RuntimeError("Ranking checkpoint changed while reopening; refresh before retry")
    session.commit()
    session.refresh(row)
    return row


def _occurrence_proves_open(
    row: FanxiuRankingLifecycleCheckpoint,
    *,
    occurrence: "RankingOccurrence | None",
    now: datetime | None,
) -> bool:
    """Whether the supplied Runtime occurrence proves this row is still open."""

    if occurrence is None or now is None or now.tzinfo is None:
        return False
    if (
        str(occurrence.instance_key) != str(row.instance_key)
        or str(occurrence.runtime_id) != str(row.runtime_id)
        or int(occurrence.activity_id) != int(row.activity_id)
    ):
        return False
    return occurrence.prepare_at <= now <= occurrence.close_at


def ranking_checkpoint_evidence(
    session: Session,
    checkpoint: RankingCheckpoint,
) -> dict[str, Any]:
    row = session.exec(
        select(FanxiuRankingLifecycleCheckpoint).where(
            FanxiuRankingLifecycleCheckpoint.instance_key == checkpoint.instance_key,
            FanxiuRankingLifecycleCheckpoint.checkpoint_kind == checkpoint.checkpoint_kind,
            FanxiuRankingLifecycleCheckpoint.business_date == checkpoint.business_date,
        )
    ).first()
    return dict(row.evidence or {}) if row is not None else {}


def record_ranking_checkpoint_evidence(
    session: Session,
    checkpoint: RankingCheckpoint,
    *,
    evidence: Mapping[str, Any],
    message: str = "",
) -> FanxiuRankingLifecycleCheckpoint:
    """Persist occurrence evidence without changing attempt or Scheduler state."""

    row = session.exec(
        select(FanxiuRankingLifecycleCheckpoint).where(
            FanxiuRankingLifecycleCheckpoint.instance_key == checkpoint.instance_key,
            FanxiuRankingLifecycleCheckpoint.checkpoint_kind == checkpoint.checkpoint_kind,
            FanxiuRankingLifecycleCheckpoint.business_date == checkpoint.business_date,
        )
    ).first()
    if row is None:
        row = FanxiuRankingLifecycleCheckpoint(
            activity_type=checkpoint.activity_type,
            family=checkpoint.family,
            instance_key=checkpoint.instance_key,
            runtime_id=checkpoint.runtime_id,
            activity_id=checkpoint.activity_id,
            checkpoint_kind=checkpoint.checkpoint_kind,
            business_date=checkpoint.business_date,
            due_at=checkpoint.due_at.isoformat(timespec="seconds"),
            status="pending",
        )
    row.evidence = dict(evidence)
    row.message = str(message or row.message or "")
    row.updated_at = time.time()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_ranking_checkpoint_rows(
    session: Session,
    *,
    instance_keys: Iterable[str] | None = None,
) -> list[FanxiuRankingLifecycleCheckpoint]:
    statement = select(FanxiuRankingLifecycleCheckpoint)
    keys = tuple(str(value) for value in (instance_keys or ()) if str(value))
    if keys:
        statement = statement.where(
            FanxiuRankingLifecycleCheckpoint.instance_key.in_(keys)
        )
    return list(session.exec(statement).all())


__all__ = [
    "TERMINAL_CHECKPOINT_STATUSES",
    "completed_ranking_checkpoint_keys",
    "ensure_ranking_lifecycle_checkpoint_table",
    "list_ranking_checkpoint_rows",
    "ranking_checkpoint_retry_times",
    "reopen_failed_ranking_checkpoint",
    "ranking_checkpoint_evidence",
    "record_ranking_checkpoint_evidence",
    "record_ranking_checkpoint_result",
]
