from __future__ import annotations

"""Durable checkpoint store for the shared ranking lifecycle Job."""

from datetime import datetime
import time
from typing import Any, Iterable, Mapping

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from backend.core.fanxiu.activity.ranking_lifecycle import RankingCheckpoint
from backend.models import FanxiuRankingLifecycleCheckpoint


TERMINAL_CHECKPOINT_STATUSES = frozenset({"completed", "retained", "unavailable"})


def ensure_ranking_lifecycle_checkpoint_table(bind: Engine) -> None:
    FanxiuRankingLifecycleCheckpoint.__table__.create(bind, checkfirst=True)


def completed_ranking_checkpoint_keys(
    session: Session,
) -> set[tuple[str, str, str]]:
    rows = session.exec(
        select(FanxiuRankingLifecycleCheckpoint).where(
            FanxiuRankingLifecycleCheckpoint.status.in_(
                TERMINAL_CHECKPOINT_STATUSES
            )
        )
    ).all()
    return {
        (row.instance_key, row.checkpoint_kind, row.business_date)
        for row in rows
    }


def ranking_checkpoint_retry_times(session: Session) -> tuple[datetime, ...]:
    result: list[datetime] = []
    rows = session.exec(
        select(FanxiuRankingLifecycleCheckpoint).where(
            FanxiuRankingLifecycleCheckpoint.retry_at != ""
        )
    ).all()
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
    row.evidence = dict(evidence or {})
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
    "record_ranking_checkpoint_result",
]
