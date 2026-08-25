from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.activity.lingzhuang_relationship import (
    RELATIONSHIP_SAMPLE_DOMAIN,
    list_lingzhuang_relationship_samples,
    record_lingzhuang_relationship_sample,
    record_lingzhuang_strengthening_action_sample,
)
from backend.models import (
    FanxiuExchangeRanking,
    FanxiuPacketBusinessRecord,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed_source_facts(
    session: Session,
    *,
    activity_id: str = "lingzhuang-16-2026-08-03",
    consumed: int = 300,
    ranking_score: int = 448,
    task_score: int = 75_000,
    score_round: int = 1,
    complete: bool = True,
) -> None:
    captured_at = "2026-08-03T20:05:40+08:00"
    snapshot_row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == "lingzhuang_strengthening_snapshot",
            FanxiuPacketBusinessRecord.record_key == "current",
        )
    ).first()
    payload = {
        "activity_id": activity_id,
        "captured_at": captured_at,
        "complete": complete,
        "equipment_current": consumed,
        "score_current": task_score,
        "score_round": score_round,
    }
    if snapshot_row is None:
        snapshot_row = FanxiuPacketBusinessRecord(
            domain="lingzhuang_strengthening_snapshot",
            record_key="current",
            source_kind="read_only_runtime_memory",
            entity_id=activity_id,
            captured_at=captured_at,
            captured_date="2026-08-03",
            payload=payload,
        )
    else:
        snapshot_row.entity_id = activity_id
        snapshot_row.payload = payload
    session.add(snapshot_row)
    session.add(
        FanxiuExchangeRanking(
            activity_id=activity_id,
            ranking_scope="personal",
            rank=108,
            score=ranking_score,
            role_key="self",
            name="止清",
            is_self=True,
            has_player=True,
            captured_at=captured_at,
        )
    )
    session.commit()


def test_record_relationship_sample_uses_persisted_source_facts() -> None:
    with _session() as session:
        _seed_source_facts(session)
        dataset = record_lingzhuang_relationship_sample(
            session,
            activity_id="lingzhuang-16-2026-08-03",
        )

    assert len(dataset.samples) == 1
    assert dataset.samples[0].x == 300
    assert dataset.samples[0].values == {
        "ranking_score": 448,
        "task_score": 75_000,
    }


def test_same_x_updates_relationship_sample_instead_of_duplicating() -> None:
    with _session() as session:
        activity_id = "lingzhuang-16-2026-08-03"
        _seed_source_facts(session, activity_id=activity_id)
        record_lingzhuang_relationship_sample(session, activity_id=activity_id)
        ranking = session.exec(
            select(FanxiuExchangeRanking).where(FanxiuExchangeRanking.is_self.is_(True))
        ).one()
        ranking.score = 450
        session.add(ranking)
        session.commit()
        dataset = record_lingzhuang_relationship_sample(session, activity_id=activity_id)
        stored = session.exec(
            select(FanxiuPacketBusinessRecord).where(
                FanxiuPacketBusinessRecord.domain == RELATIONSHIP_SAMPLE_DOMAIN
            )
        ).all()

    assert len(dataset.samples) == 1
    assert dataset.samples[0].values["ranking_score"] == 450
    assert len(stored) == 1


def test_later_round_task_score_is_stored_as_cumulative_score() -> None:
    with _session() as session:
        activity_id = "lingzhuang-16-2026-08-03"
        _seed_source_facts(
            session,
            activity_id=activity_id,
            score_round=2,
            task_score=75_000,
        )
        dataset = record_lingzhuang_relationship_sample(session, activity_id=activity_id)

    assert dataset.samples[0].values["task_score"] == 11_575_000


def test_relationship_samples_are_isolated_by_activity_and_sorted_by_x() -> None:
    with _session() as session:
        for activity_id, consumed in (("activity-b", 600), ("activity-a", 300)):
            _seed_source_facts(session, activity_id=activity_id, consumed=consumed)
            record_lingzhuang_relationship_sample(session, activity_id=activity_id)
        activity_a = list_lingzhuang_relationship_samples(session, activity_id="activity-a")
        activity_b = list_lingzhuang_relationship_samples(session, activity_id="activity-b")

    assert [sample.x for sample in activity_a.samples] == [300]
    assert [sample.x for sample in activity_b.samples] == [600]


def test_incomplete_strengthening_does_not_create_relationship_sample() -> None:
    with _session() as session:
        _seed_source_facts(session, complete=False)
        try:
            record_lingzhuang_relationship_sample(
                session,
                activity_id="lingzhuang-16-2026-08-03",
            )
        except ValueError as exc:
            assert "未完整更新" in str(exc)
        else:
            raise AssertionError("incomplete snapshots must not create samples")
        dataset = list_lingzhuang_relationship_samples(
            session,
            activity_id="lingzhuang-16-2026-08-03",
        )

    assert dataset.samples == []


def _runtime_snapshot(*, stock: int, equipment_progress: int, score: int) -> dict:
    return {
        "activity_id": "lingzhuang-16-2026-08-03",
        "captured_at": "2026-08-04T10:30:00+08:00",
        "complete": True,
        "rows": [
            {
                "part": "气铠",
                "initial": {
                    "material_id": 10_001_002,
                    "material_name": "气铠玄铁石",
                    "material_count": stock,
                },
                "dongxuan": {
                    "material_id": 10_001_002,
                    "material_name": "气铠玄铁石",
                    "material_count": stock,
                },
            }
        ],
        "equipment_current": equipment_progress,
        "score_round": 1,
        "score_current": score,
        "score_rounds": [
            {"round": 1, "target": 11_500_000},
            {"round": 2, "target": 11_870_500},
        ],
    }


def test_action_sample_accumulates_exact_backpack_delta() -> None:
    with _session() as session:
        activity_id = "lingzhuang-16-2026-08-03"
        session.add(
            FanxiuPacketBusinessRecord(
                domain=RELATIONSHIP_SAMPLE_DOMAIN,
                record_key=f"lingzhuang-huadao-material-score:{activity_id}:300",
                source_kind="seed",
                entity_id=activity_id,
                captured_at="2026-08-04T10:00:00+08:00",
                captured_date="2026-08-04",
                payload={"x": 300, "values": {"task_score": 75_000}},
            )
        )
        session.commit()
        dataset = record_lingzhuang_strengthening_action_sample(
            session,
            activity_id=activity_id,
            before=_runtime_snapshot(stock=3216, equipment_progress=300, score=75_000),
            after=_runtime_snapshot(stock=2816, equipment_progress=760, score=136_000),
            part="气铠",
            category="洞玄",
        )
        action_row = session.exec(
            select(FanxiuPacketBusinessRecord).where(
                FanxiuPacketBusinessRecord.record_key.endswith(":700")
            )
        ).one()

    assert [sample.x for sample in dataset.samples] == [300, 700]
    assert dataset.samples[-1].values == {
        "equipment_task_progress": 760,
        "task_score": 136_000,
    }
    assert action_row.payload["action"]["consumed"] == 400


def test_action_sample_accepts_category_complete_snapshot_with_false_global_flag() -> None:
    before = _runtime_snapshot(stock=8653, equipment_progress=4015, score=1_003_750)
    after = _runtime_snapshot(stock=8153, equipment_progress=4515, score=1_128_750)
    before["complete"] = False

    with _session() as session:
        dataset = record_lingzhuang_strengthening_action_sample(
            session,
            activity_id="lingzhuang-16-2026-08-03",
            before=before,
            after=after,
            part="气铠",
            category="洞玄",
        )

    assert dataset.samples[-1].x == 500
    assert dataset.samples[-1].values["equipment_task_progress"] == 4515
