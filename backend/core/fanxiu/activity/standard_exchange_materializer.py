from __future__ import annotations

"""Small shared persistence primitives for gameplay Exchange collectors."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, select

from backend.models import FanxiuExchangeRanking


@dataclass(frozen=True)
class RankingMergeResult:
    """One occurrence's current and retained ranking projection."""

    rankings: tuple[dict[str, Any], ...]
    current_related_scopes: frozenset[str]
    retained_related_scopes: frozenset[str]
    captured_at: str


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


def merge_occurrence_rankings(
    session: Session,
    *,
    observation: Mapping[str, Any],
    existing_activity_id: str | None,
    primary_scope: str,
    related_rank_activity_ids: Sequence[tuple[str, int]],
    valid_from: str,
    valid_through: str,
) -> RankingMergeResult:
    """Merge current companion facts with retained rows from this occurrence.

    Related scopes are current only when their fact timestamp belongs to the
    exact occurrence window.  Missing or out-of-window companion scopes retain
    the previously persisted rows for this activity instance; they never leak
    rows from another occurrence.
    """

    from backend.core.fanxiu.activity.standard_observation import (
        ActivityObservationUnavailable,
        read_activity_rank_fact,
    )

    declared_scopes = {str(scope) for scope, _rank_id in related_rank_activity_ids}
    current_scopes: set[str] = set()
    related_times: list[str] = []
    for scope, rank_id in related_rank_activity_ids:
        try:
            fact = read_activity_rank_fact(session, int(rank_id))
        except ActivityObservationUnavailable:
            continue
        captured_at = str(fact.get("captured_at") or "")
        if str(valid_from) <= captured_at[:10] <= str(valid_through):
            current_scopes.add(str(scope))
            related_times.append(captured_at)

    rankings = [
        dict(row)
        for row in observation.get("rankings") or ()
        if str(row.get("ranking_scope") or primary_scope) == primary_scope
        or str(row.get("ranking_scope") or "") in current_scopes
    ]
    retained_scopes: set[str] = set()
    if existing_activity_id:
        retained = load_stored_exchange_rankings(
            session,
            activity_id=str(existing_activity_id),
            scopes=declared_scopes - current_scopes,
        )
        rankings.extend(retained)
        retained_scopes = {str(row["ranking_scope"]) for row in retained}

    evidence = dict(observation.get("evidence") or {})
    captured_at = max(
        (
            str(value)
            for value in (
                evidence.get("rank_captured_at") or "",
                evidence.get("currency_captured_at") or "",
                *related_times,
            )
            if str(value)
        ),
        default="",
    )
    return RankingMergeResult(
        rankings=tuple(rankings),
        current_related_scopes=frozenset(current_scopes),
        retained_related_scopes=frozenset(retained_scopes),
        captured_at=captured_at,
    )


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
    "RankingMergeResult",
    "load_stored_exchange_rankings",
    "merge_occurrence_rankings",
    "persist_exchange_materialization",
]
