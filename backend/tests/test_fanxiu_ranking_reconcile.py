from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity import ranking_reconcile
from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.models import FanxiuExchangeActivity


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _magic_occurrence(*, cross_count: int) -> RankingOccurrence:
    timezone = datetime.now().astimezone().tzinfo
    assert timezone is not None
    return RankingOccurrence(
        activity_type="magic-invasion",
        family="gameplay_rank",
        runtime_id=f"magic-{cross_count}",
        activity_id=700014,
        start_at=datetime(2026, 8, 21, 10, tzinfo=timezone),
        end_at=datetime(2026, 8, 21, 22, tzinfo=timezone),
        prepare_at=datetime(2026, 8, 21, 0, tzinfo=timezone),
        close_at=datetime(2026, 8, 22, 0, tzinfo=timezone),
        cross_count=cross_count,
        world_level=310,
    )


def test_seed_resolves_server_and_cross_magic_shop_independently(monkeypatch) -> None:
    monkeypatch.setattr(
        ranking_reconcile,
        "_activity_definition_index",
        lambda: {700014: {"id": 700014, "follow": [7000114, 7000214]}},
    )
    with _session() as session:
        server = ranking_reconcile.seed_ranking_occurrence(
            session,
            _magic_occurrence(cross_count=1),
            captured_at="2026-08-21T00:30:00+08:00",
        )
        cross = ranking_reconcile.seed_ranking_occurrence(
            session,
            _magic_occurrence(cross_count=8),
            captured_at="2026-08-21T00:30:00+08:00",
        )

        assert (server.game_shop_base_id, server.currency_type) == (70000, 15)
        assert (cross.game_shop_base_id, cross.currency_type) == (70001, 17)
        assert server.evidence["instance_key"] != cross.evidence["instance_key"]
        assert server.evidence["period_close_panel_date"] == "2026-08-22"
        assert server.evidence["period_close_panel_time"] == int(
            _magic_occurrence(cross_count=1).close_at.timestamp() * 1000
        )
        assert "period_close_time" not in server.evidence


def test_reconcile_projects_static_tiers_without_live_rank(monkeypatch) -> None:
    collected: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ranking_reconcile,
        "_activity_definition_index",
        lambda: {700014: {"id": 700014, "follow": [7000114, 7000214]}},
    )
    monkeypatch.setattr(
        ranking_reconcile,
        "materialize_registered_exchange_activity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        ranking_reconcile,
        "collect_registered_exchange_activity",
        lambda _session, *, activity_type, activity_id: collected.append(
            (activity_type, activity_id)
        ),
    )
    monkeypatch.setattr(
        ranking_reconcile,
        "list_exchange_rankings",
        lambda *_args, **_kwargs: SimpleNamespace(
            reward_tiers=[object(), object()],
            loaded_entry_count=0,
            declared_rank_count=0,
            complete=False,
        ),
    )
    with _session() as session:
        result = ranking_reconcile.reconcile_ranking_occurrence(
            session,
            _magic_occurrence(cross_count=8),
            captured_at="2026-08-21T00:30:00+08:00",
        )

    assert result["status"] == "completed"
    assert result["facts"]["reward_tier_count"] == 4
    assert result["facts"]["rankings"] == "retained"
    assert result["snapshot_kind"] == "running"
    assert collected == [("magic-invasion", "magic-invasion-8-2026-08-21-2026-08-21")]
    assert result["collect_error"] == ""


def test_snapshot_kind_marks_close_day_0030_as_reachable_final() -> None:
    occurrence = replace(
        _magic_occurrence(cross_count=8),
        close_at=datetime.fromisoformat("2026-08-22T23:59:59+08:00"),
    )

    assert ranking_reconcile._ranking_snapshot_kind(
        datetime.fromisoformat("2026-08-21T21:00:00+08:00"), occurrence
    ) == "running"
    assert ranking_reconcile._ranking_snapshot_kind(
        datetime.fromisoformat("2026-08-22T00:30:00+08:00"), occurrence
    ) == "final"


def test_snapshot_kind_keeps_intermediate_post_end_day_as_formal_end() -> None:
    occurrence = replace(
        _magic_occurrence(cross_count=8),
        close_at=datetime.fromisoformat("2026-08-23T23:59:59+08:00"),
    )

    assert ranking_reconcile._ranking_snapshot_kind(
        datetime.fromisoformat("2026-08-22T00:30:00+08:00"), occurrence
    ) == "formal_end"


def test_seed_inherits_only_explicit_global_monotonic_server_day_floor(monkeypatch) -> None:
    monkeypatch.setattr(
        ranking_reconcile,
        "_activity_definition_index",
        lambda: {4043101: {"id": 4043101, "follow": [43103, 43104]}},
    )
    timezone = datetime.now().astimezone().tzinfo
    assert timezone is not None
    occurrence = RankingOccurrence(
        activity_type="dandao-wending",
        family="resource_rank",
        runtime_id="4043101400004",
        activity_id=4043101,
        start_at=datetime(2026, 8, 20, 5, 0, 5, tzinfo=timezone),
        end_at=datetime(2026, 8, 21, 22, tzinfo=timezone),
        prepare_at=datetime(2026, 8, 19, 5, tzinfo=timezone),
        close_at=datetime(2026, 8, 21, 23, 58, 59, tzinfo=timezone),
        cross_count=4,
    )
    with _session() as session:
        session.add(
            FanxiuExchangeActivity(
                id="proven-server-day-anchor",
                activity_type="magic-invasion",
                start_date="2026-08-19",
                end_date="2026-08-19",
                evidence={
                    "server_day": 31,
                    "server_day_evidence": "reward page proved >=31 tier",
                },
            )
        )
        session.commit()
        activity = ranking_reconcile.seed_ranking_occurrence(
            session,
            occurrence,
            captured_at="2026-08-21T00:30:00+08:00",
        )

    assert activity.game_rank_activity_id == 43103
    assert activity.evidence["rank_scope_activity_ids"] == {
        "personal": 43103,
        "plane": 43104,
    }
    assert activity.evidence["server_day"] == 31
    assert "monotonic lower bound" in activity.evidence["server_day_evidence"]
