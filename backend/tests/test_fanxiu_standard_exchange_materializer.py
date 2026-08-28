from __future__ import annotations

from typing import Any, cast

from sqlmodel import Session

from backend.core.fanxiu.activity import standard_exchange_materializer
from backend.core.fanxiu.activity import standard_observation


def _row(scope: str, rank: int) -> dict[str, Any]:
    return {
        "ranking_scope": scope,
        "rank": rank,
        "score": 100 - rank,
        "role_key": f"{scope}:{rank}",
    }


def test_merge_occurrence_rankings_keeps_only_current_companion_facts(
    monkeypatch,
) -> None:
    facts = {
        201: {"captured_at": "2026-08-12T13:00:00+08:00"},
        202: {"captured_at": "2026-08-09T23:59:59+08:00"},
    }
    monkeypatch.setattr(
        standard_observation,
        "read_activity_rank_fact",
        lambda _session, rank_id: facts[rank_id],
    )

    result = standard_exchange_materializer.merge_occurrence_rankings(
        cast(Session, object()),
        observation={
            "rankings": [_row("personal", 1), _row("alliance", 2), _row("plane", 3)],
            "evidence": {
                "rank_captured_at": "2026-08-12T12:00:00+08:00",
                "currency_captured_at": "2026-08-12T12:30:00+08:00",
            },
        },
        existing_activity_id=None,
        primary_scope="personal",
        related_rank_activity_ids=(("alliance", 201), ("plane", 202)),
        valid_from="2026-08-10",
        valid_through="2026-08-13",
    )

    assert [row["ranking_scope"] for row in result.rankings] == [
        "personal",
        "alliance",
    ]
    assert result.current_related_scopes == frozenset({"alliance"})
    assert result.retained_related_scopes == frozenset()
    assert result.captured_at == "2026-08-12T13:00:00+08:00"


def test_merge_occurrence_rankings_retains_missing_scope_from_exact_instance(
    monkeypatch,
) -> None:
    def unavailable(_session, _rank_id):
        raise standard_observation.ActivityObservationUnavailable("missing")

    retained_calls: list[tuple[str, set[str]]] = []
    monkeypatch.setattr(standard_observation, "read_activity_rank_fact", unavailable)
    monkeypatch.setattr(
        standard_exchange_materializer,
        "load_stored_exchange_rankings",
        lambda _session, *, activity_id, scopes: (
            retained_calls.append((activity_id, set(scopes))) or [_row("alliance", 8)]
        ),
    )

    result = standard_exchange_materializer.merge_occurrence_rankings(
        cast(Session, object()),
        observation={
            "rankings": [_row("personal", 5)],
            "evidence": {"rank_captured_at": "2026-08-12T12:00:00+08:00"},
        },
        existing_activity_id="exact-occurrence-id",
        primary_scope="personal",
        related_rank_activity_ids=(("alliance", 201),),
        valid_from="2026-08-10",
        valid_through="2026-08-13",
    )

    assert retained_calls == [("exact-occurrence-id", {"alliance"})]
    assert [row["ranking_scope"] for row in result.rankings] == [
        "personal",
        "alliance",
    ]
    assert result.current_related_scopes == frozenset()
    assert result.retained_related_scopes == frozenset({"alliance"})
