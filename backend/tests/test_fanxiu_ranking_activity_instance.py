from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.activity import ranking_reconcile
from backend.core.fanxiu.activity.exchange_event import (
    list_exchange_activity_snapshot,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.migrations.manager import (
    v108_unify_fanxiu_ranking_activity_instances,
    v109_merge_split_xutian_occurrence_roots,
)
from backend.models import (
    FanxiuExchangeActivity,
    FanxiuExchangeActivityObservation,
    FanxiuExchangeShopItem,
)


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _payload(instance_key: str, runtime_id: str, goods_id: int) -> dict:
    return {
        "instance_key": instance_key,
        "family": "gameplay_rank",
        "activity_type": "tiandi-yiju",
        "runtime_id": runtime_id,
        "game_activity_id": 8090001,
        "cross_count": 1,
        "prepare_at": "2026-08-27T05:00:00+08:00",
        "start_at": "2026-08-27T10:00:00+08:00",
        "end_at": "2026-08-27T22:00:00+08:00",
        "close_at": "2026-08-27T23:59:59+08:00",
        "start_date": "2026-08-27",
        "end_date": "2026-08-27",
        "currency_name": "棋符",
        "instance_data": {"rank_scope_activity_ids": {"personal": 90101}},
        "evidence": {"instance_key": instance_key, "runtime_id": runtime_id},
        "expected_shop_item_count": 1,
        "shop_items": [{
            "goods_id": goods_id,
            "item_id": goods_id + 100,
            "name": f"商品{goods_id}",
            "token_cost": 10,
            "purchase_limit": 1,
        }],
    }


def test_same_day_occurrences_are_distinct_aggregate_instances() -> None:
    engine = _engine()
    with Session(engine) as session:
        first_id = upsert_exchange_activity_snapshot(
            session, _payload("runtime:a:activity:8090001:first", "a", 1)
        )
        second_id = upsert_exchange_activity_snapshot(
            session, _payload("runtime:b:activity:8090001:second", "b", 2)
        )
        rows = list(session.exec(select(FanxiuExchangeActivity)).all())
        snapshot = list_exchange_activity_snapshot(
            session, activity_type="tiandi-yiju", activity_id=second_id
        )

    assert first_id != second_id
    assert {row.instance_key for row in rows} == {
        "runtime:a:activity:8090001:first",
        "runtime:b:activity:8090001:second",
    }
    assert snapshot.selected_activity is not None
    assert snapshot.selected_activity.instance_key == "runtime:b:activity:8090001:second"
    assert snapshot.selected_activity.instance_data["rank_scope_identities"] == {
        "personal": {"runtime_rank_activity_id": 90101, "reward_activity_id": 90101}
    }
    assert [item.goods_id for item in snapshot.selected_activity.shop_items] == [2]


def test_schedule_seed_promotes_occurrence_identity_to_root_columns(monkeypatch) -> None:
    monkeypatch.setattr(
        ranking_reconcile,
        "_activity_definition_index",
        lambda: {8090001: {"id": 8090001, "follow": []}},
    )
    occurrence = RankingOccurrence(
        activity_type="tiandi-yiju",
        family="gameplay_rank",
        runtime_id="8090001400001",
        activity_id=8090001,
        prepare_at=datetime.fromisoformat("2026-08-27T05:00:00+08:00"),
        start_at=datetime.fromisoformat("2026-08-27T10:00:00+08:00"),
        end_at=datetime.fromisoformat("2026-08-27T22:00:00+08:00"),
        close_at=datetime.fromisoformat("2026-08-27T23:59:59+08:00"),
        cross_count=1,
        world_level=190,
        base_id=90000,
    )
    engine = _engine()
    with Session(engine) as session:
        activity = ranking_reconcile.seed_ranking_occurrence(
            session, occurrence, captured_at="2026-08-27T00:30:00+08:00"
        )

    assert activity.instance_key == occurrence.instance_key
    assert activity.family == "gameplay_rank"
    assert activity.runtime_id == occurrence.runtime_id
    assert activity.game_activity_id == 8090001
    assert activity.prepare_at == "2026-08-27T05:00:00+08:00"
    assert activity.close_at == "2026-08-27T23:59:59+08:00"
    assert activity.instance_data == {
        "base_id": 90000,
        "world_level": 190,
        "rank_scope_identities": {
            "personal": {"runtime_rank_activity_id": 90101, "reward_activity_id": 90101},
            "alliance": {"runtime_rank_activity_id": 90102, "reward_activity_id": 90102},
        },
    }


def test_date_envelope_refresh_cannot_downgrade_exact_instance_identity() -> None:
    engine = _engine()
    with Session(engine) as session:
        exact = _payload("runtime:exact", "exact", 3)
        activity_id = upsert_exchange_activity_snapshot(session, exact)
        refresh = {
            key: value
            for key, value in exact.items()
            if key not in {
                "instance_key", "family", "runtime_id", "game_activity_id",
                "prepare_at", "start_at", "end_at", "close_at", "instance_data",
            }
        }
        refresh["current_currency"] = 88
        upsert_exchange_activity_snapshot(session, refresh)
        activity = session.get(FanxiuExchangeActivity, activity_id)

    assert activity is not None
    assert activity.instance_key == "runtime:exact"
    assert activity.runtime_id == "exact"
    assert activity.game_activity_id == 8090001
    assert activity.current_currency == 88


def test_v108_removes_date_identity_constraint_and_preserves_children() -> None:
    engine = _engine()
    with Session(engine) as session:
        activity_id = upsert_exchange_activity_snapshot(
            session, _payload("runtime:migrated", "migrated", 7)
        )
        session.execute(text(
            "CREATE UNIQUE INDEX uq_legacy_exchange_dates ON fanxiuexchangeactivity "
            "(activity_type, cross_count, start_date, end_date)"
        ))
        session.commit()

        v108_unify_fanxiu_ranking_activity_instances(session)

        activity = session.get(FanxiuExchangeActivity, activity_id)
        items = list(session.exec(
            select(FanxiuExchangeShopItem).where(
                FanxiuExchangeShopItem.activity_id == activity_id
            )
        ).all())
        unique_indexes = {
            tuple(
                str(info[2])
                for info in session.exec(
                    text(f'PRAGMA index_info("{row[1]}")')
                ).all()
            )
            for row in session.exec(text(
                'PRAGMA index_list("fanxiuexchangeactivity")'
            )).all()
            if bool(row[2])
        }
        child_targets = {
            str(row[2])
            for row in session.exec(text(
                'PRAGMA foreign_key_list("fanxiuexchangeshopitem")'
            )).all()
        }

    assert activity is not None
    assert activity.instance_key == "runtime:migrated"
    assert [item.goods_id for item in items] == [7]
    assert ("instance_key",) in unique_indexes
    assert ("activity_type", "cross_count", "start_date", "end_date") not in unique_indexes
    assert "fanxiuexchangeactivity" in child_targets


def test_v109_merges_xutian_runtime_identity_split_without_losing_children() -> None:
    engine = _engine()
    stable_key = (
        "activity:xutian-palace:4080001:"
        "2026-08-31T10:00:00+08:00:2026-09-01T22:00:00+08:00"
    )
    common = {
        "family": "gameplay_rank",
        "activity_type": "xutian-palace",
        "game_activity_id": 4080001,
        "cross_count": 4,
        "prepare_at": "2026-08-31T00:00:00+08:00",
        "start_at": "2026-08-31T10:00:00+08:00",
        "end_at": "2026-09-01T22:00:00+08:00",
        "close_at": "2026-09-02T23:59:59+08:00",
        "start_date": "2026-08-31",
        "end_date": "2026-09-01",
        "game_rank_activity_id": 80451,
        "currency_name": "纳元晶",
    }
    with Session(engine) as session:
        legacy_id = upsert_exchange_activity_snapshot(session, {
            **common,
            "instance_key": "runtime:4080001400020:activity:4080001:old",
            "runtime_id": "4080001400020",
        })
        stable_id = upsert_exchange_activity_snapshot(session, {
            **common,
            "instance_key": stable_key,
            "runtime_id": "4080001400004",
            "shop_items": [{
                "goods_id": 7,
                "item_id": 107,
                "name": "商品7",
                "token_cost": 10,
                "purchase_limit": 1,
            }],
        })
        session.add(FanxiuExchangeActivityObservation(
            activity_id=legacy_id,
            fingerprint="legacy-fact",
            payload={"source": "legacy"},
        ))
        session.commit()

        v109_merge_split_xutian_occurrence_roots(session)

        activities = list(session.exec(select(FanxiuExchangeActivity)).all())
        shops = list(session.exec(select(FanxiuExchangeShopItem)).all())
        observations = list(session.exec(select(FanxiuExchangeActivityObservation)).all())

    assert [(row.id, row.instance_key) for row in activities] == [(stable_id, stable_key)]
    assert [(row.activity_id, row.goods_id) for row in shops] == [(stable_id, 7)]
    assert [(row.activity_id, row.fingerprint) for row in observations] == [
        (stable_id, "legacy-fact")
    ]
    assert activities[0].evidence["merged_instance_roots"][0]["id"] == legacy_id
