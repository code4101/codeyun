from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.rank_reward_context import (
    resolve_exchange_rank_reward_context,
    server_day_for_start_time,
)
from backend.models import FanxiuExchangeActivity


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_server_day_stays_unknown_until_runtime_exposes_open_server_time() -> None:
    with _session() as session:
        assert server_day_for_start_time(session, 1_786_413_600_000) is None


def test_exchange_context_reconstructs_old_activity_from_cached_occurrence(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.rank_reward_context._persisted_schedule_occurrences",
        lambda: [{
            "activityId": 4_150_001,
            "startTime": 1_786_413_600_000,
            "endTime": 1_786_543_200_000,
            "avgWorldLevel": 212,
        }],
    )
    with _session() as session:
        activity = FanxiuExchangeActivity(
            id="beast-period",
            activity_type="beast-abyss",
            start_date="2026-08-11",
            end_date="2026-08-12",
            evidence={"game_activity_id": 4_150_001},
        )
        session.add(activity)
        session.commit()

        assert resolve_exchange_rank_reward_context(session, activity) == {
            "server_day": None,
            "world_level": 212,
        }


def test_exchange_context_does_not_guess_from_wrong_period(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.rank_reward_context._persisted_schedule_occurrences",
        lambda: [{
            "activityId": 4_150_001,
            "startTime": 1_783_303_200_000,
            "endTime": 1_783_432_800_000,
            "avgWorldLevel": 216,
        }],
    )
    with _session() as session:
        activity = FanxiuExchangeActivity(
            id="beast-period",
            activity_type="beast-abyss",
            start_date="2026-08-11",
            end_date="2026-08-12",
            evidence={"game_activity_id": 4_150_001},
        )
        session.add(activity)
        session.commit()

        assert resolve_exchange_rank_reward_context(session, activity) == {
            "server_day": None,
            "world_level": None,
        }
