from __future__ import annotations

from dataclasses import replace
from datetime import date

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity import exchange_activity_registry as registry
from backend.core.fanxiu.activity.schedule_page import (
    load_fanxiu_schedule_ranking_snapshot,
)
from backend.models import FanxiuExchangeActivity


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _activity(
    activity_type: str,
    family: str,
    *,
    start_date: str,
    end_date: str,
    close_date: str,
) -> FanxiuExchangeActivity:
    return FanxiuExchangeActivity(
        id=f"{activity_type}-{start_date}",
        instance_key=f"test:{activity_type}:{start_date}",
        family=family,
        activity_type=activity_type,
        cross_count=1,
        prepare_at=f"{start_date}T05:00:00+08:00",
        start_at=f"{start_date}T10:00:00+08:00",
        end_at=f"{end_date}T22:00:00+08:00",
        close_at=f"{close_date}T23:58:59+08:00",
        start_date=start_date,
        end_date=end_date,
    )


def test_schedule_selects_today_gameplay_and_does_not_relabel_old_resource_data() -> None:
    engine = _engine()
    with Session(engine) as session:
        session.add(_activity(
            "xutian-palace",
            "gameplay_rank",
            start_date="2026-08-31",
            end_date="2026-09-01",
            close_date="2026-09-02",
        ))
        session.add(_activity(
            "beast-abyss",
            "gameplay_rank",
            start_date="2026-09-02",
            end_date="2026-09-03",
            close_date="2026-09-04",
        ))
        session.add(_activity(
            "yaochi-flower-festival",
            "resource_rank",
            start_date="2026-08-29",
            end_date="2026-08-30",
            close_date="2026-08-30",
        ))
        session.commit()

        snapshot = load_fanxiu_schedule_ranking_snapshot(
            session,
            business_date=date(2026, 9, 2),
        )

    assert snapshot.gameplay_rank.activity_type == "beast-abyss"
    assert snapshot.gameplay_rank.snapshot is not None
    assert snapshot.gameplay_rank.snapshot.selected_activity is not None
    assert snapshot.gameplay_rank.snapshot.selected_activity.activity_type == "beast-abyss"
    assert snapshot.resource_rank.activity_type is None
    assert snapshot.resource_rank.snapshot is None


def test_materializer_failure_keeps_persisted_beast_abyss_history_visible(monkeypatch) -> None:
    class UnavailableRuntimeAdapter:
        def collect_activity(self, session: Session, *, activity_id: str):
            del session, activity_id
            raise AssertionError("test adapter only exercises materialization")

        def materialize_activity(self, session: Session) -> str:
            del session
            raise ValueError("未找到兽渊探秘运行时活动实例")

    engine = _engine()
    with Session(engine) as session:
        old = _activity(
            "beast-abyss",
            "gameplay_rank",
            start_date="2026-08-23",
            end_date="2026-08-24",
            close_date="2026-08-25",
        )
        session.add(old)
        session.commit()
        monkeypatch.setattr(
            registry,
            "EXCHANGE_ACTIVITY_SPECS",
            {
                **registry.EXCHANGE_ACTIVITY_SPECS,
                "beast-abyss": replace(
                    registry.EXCHANGE_ACTIVITY_SPECS["beast-abyss"],
                    adapter=UnavailableRuntimeAdapter(),
                ),
            },
        )

        selected_id = registry.materialize_registered_exchange_activity(
            session,
            activity_type="beast-abyss",
        )

    assert selected_id == old.id
