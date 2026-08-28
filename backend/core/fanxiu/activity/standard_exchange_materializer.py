from __future__ import annotations

"""Small shared persistence primitives for gameplay Exchange collectors."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlmodel import Session, select

from backend.models import FanxiuExchangeRanking


def load_stored_exchange_rankings(
    session: Session,
    *,
    activity_id: str,
    scopes: Iterable[str],
) -> list[dict[str, Any]]:
    """Return validated rows retained for the same persisted occurrence."""

    resolved_scopes = {str(scope) for scope in scopes if str(scope)}
    if not resolved_scopes:
        return []
    rows = session.exec(
        select(FanxiuExchangeRanking).where(
            FanxiuExchangeRanking.activity_id == str(activity_id),
            FanxiuExchangeRanking.ranking_scope.in_(resolved_scopes),
        )
    ).all()
    return [
        {
            "ranking_scope": row.ranking_scope,
            "rank": row.rank,
            "score": row.score,
            "role_key": row.role_key,
            "name": row.name,
            "server_id": row.server_id,
            "server_name": row.server_name,
            "club_name": row.club_name,
            "is_self": row.is_self,
            "is_reward_guard": row.is_reward_guard,
            "is_last_player": row.is_last_player,
            "has_player": row.has_player,
            "reward_rank_start": row.reward_rank_start,
            "reward_rank_end": row.reward_rank_end,
            "raw_data": dict(row.raw_data or {}),
        }
        for row in rows
    ]


def persist_exchange_materialization(
    session: Session,
    *,
    activity_type: str,
    payload: dict[str, Any],
    rankings: Sequence[Mapping[str, Any]],
    captured_at: str,
) -> Any:
    """Persist one collector result using the canonical write/readback order."""

    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_activity_snapshot,
        replace_exchange_rankings,
        upsert_exchange_activity_snapshot,
    )

    persisted_id = upsert_exchange_activity_snapshot(session, payload)
    replace_exchange_rankings(
        session,
        activity_type=str(activity_type),
        activity_id=persisted_id,
        rows=list(rankings),
        captured_at=str(captured_at),
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=str(activity_type),
        activity_id=persisted_id,
    ).selected_activity


__all__ = [
    "load_stored_exchange_rankings",
    "persist_exchange_materialization",
]
