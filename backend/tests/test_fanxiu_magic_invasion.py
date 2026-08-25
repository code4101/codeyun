import json
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.activity import magic_invasion
from backend.core.fanxiu.instrumentation.activity_shop import (
    FanxiuActivityShopCollectionError,
    FanxiuActivityShopNotLoadedError,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)
from backend.models import (
    FanxiuExchangeActivity,
    FanxiuExchangeRanking,
    FanxiuPacketBusinessRecord,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


@pytest.fixture(autouse=True)
def _cold_runtime_wallet(monkeypatch) -> None:
    monkeypatch.setattr(
        magic_invasion,
        "_runtime_currency_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("测试环境钱包缓存未加载")
        ),
    )


def _add_business_fact(
    session: Session,
    *,
    row_id: str,
    domain: str,
    entity_id: str,
    captured_at: str,
    payload: dict,
    record_key: str | None = None,
) -> None:
    session.add(FanxiuPacketBusinessRecord(
        id=row_id,
        domain=domain,
        record_key=record_key or row_id,
        protocol="SM_Test",
        packet_id=f"packet:{row_id}",
        entity_id=entity_id,
        captured_at=captured_at,
        payload=payload,
    ))


def _seed_collectable_facts(
    session: Session,
    *,
    plane_captured_at: str = "2026-08-10 18:40:00",
) -> None:
    _add_business_fact(
        session,
        row_id="period",
        domain="worldline_activity",
        entity_id="8070001",
        captured_at="2026-08-10 10:02:47",
        payload={"item": {
            "class": "MagicInvadeActivityVO",
            "activityId": 8070001,
            "serverCount": 8,
            "avgWorldLevel": 221,
            "startTime": 1786327200000,
            "endTime": 1786370400000,
        }},
    )
    _add_business_fact(
        session,
        row_id="currency",
        domain="resource_state",
        entity_id="17",
        captured_at="2026-08-10 18:33:19",
        payload={"amount": 143778, "history": 143778, "borrow": 0},
        record_key="currency:17",
    )
    _add_business_fact(
        session,
        row_id="personal",
        domain="activity_rank",
        entity_id="70841",
        captured_at="2026-08-10 18:48:39",
        payload={"snapshot": {
            "rank_vo_type": "ActivityRankPersonalVO",
            "rank_list_size": 73,
            "personal_item": {
                "id": 1001, "key": "role:1", "name": "测试角色",
                "rank": 15, "score": 729134, "server_id": 22077,
            },
            "items": [],
        }},
    )
    _add_business_fact(
        session,
        row_id=f"plane:{plane_captured_at}",
        domain="activity_rank",
        entity_id="70842",
        captured_at=plane_captured_at,
        payload={"snapshot": {
            "rank_vo_type": "ActivityRankCrossServerVO",
            "rank_list_size": 8,
            "personal_item": {
                "id": 22077, "key": "22077", "name": "",
                "rank": 5, "score": 8473998, "server_id": 22077,
            },
            "items": [{
                "id": 22077, "key": "22077", "name": "凌霄道宗",
                "rank": 5, "score": 8473998, "server_id": 22077,
            }],
        }},
    )
    session.commit()


def _shop_snapshot(token_cost: int = 1000) -> dict:
    return {
        "active_shop_item_count": 1,
        "items": [{
            "goods_id": 8000001,
            "item_id": 9070095,
            "name": "天资丹",
            "goods_num": 1,
            "token_cost": token_cost,
            "purchase_limit": 2,
            "purchased_count": 0,
            "source_order": 1,
        }],
        "evidence": {"source": "test-runtime-shop"},
    }


def test_magic_invasion_runtime_fact_defines_one_activity_instance() -> None:
    with _session() as session:
        session.add(FanxiuPacketBusinessRecord(
            domain="worldline_activity",
            record_key="magic-instance",
            protocol="SM_ActivitySync",
            packet_id="packet-1",
            entity_id="8070001",
            captured_at="2026-08-10 10:02:47",
            payload={"item": {
                "class": "MagicInvadeActivityVO",
                "activityId": 8070001,
                "serverCount": 8,
                "avgWorldLevel": 221,
                "startTime": 1786327200000,
                "endTime": 1786370400000,
            }},
        ))
        session.commit()

        period = magic_invasion._runtime_period(session)

    assert period["game_activity_id"] == 8070001
    assert period["cross_count"] == 8
    assert period["start_date"] == "2026-08-10"
    assert period["end_date"] == "2026-08-10"
    assert period["world_level"] == 221


def test_magic_shop_identity_keeps_server_and_cross_instances_independent() -> None:
    assert magic_invasion._shop_identity(cross_count=1) == (70000, 15, None)
    assert magic_invasion._shop_identity(cross_count=8) == (70001, 17, 8)


def test_magic_invasion_rank_ids_come_from_activity_definition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "parsed_configs" / "Activity"
    config_dir.mkdir(parents=True)
    (config_dir / "rows.json").write_text(json.dumps([
        {"id": 8070001, "name": "魔道入侵", "follow": [70841, 70842]},
    ]), encoding="utf-8")
    monkeypatch.setattr(magic_invasion, "resolve_fanxiu_export_root", lambda: tmp_path)

    definition = magic_invasion._activity_definition(8070001)

    assert definition["follow"] == [70841, 70842]


def test_magic_shop_snapshot_rejects_incomplete_runtime_projection(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        magic_invasion,
        "load_fanxiu_item_runtime_index",
        lambda **_kwargs: {"cards_by_id": {}},
    )
    monkeypatch.setattr(
        magic_invasion,
        "collect_activity_shop_runtime",
        lambda **_kwargs: {"complete": False},
    )

    with pytest.raises(ValueError, match="运行态快照不完整"):
        magic_invasion._shop_snapshot(cross_count=8)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            FanxiuActivityShopNotLoadedError("游戏尚未加载 V_ShopCfg"),
            "兑换宝阁尚未加载",
        ),
        (
            FanxiuActivityShopCollectionError("没有找到商店 70001"),
            "兑换宝阁采集失败",
        ),
    ],
)
def test_first_collect_does_not_report_success_when_shop_projection_failed(
    monkeypatch,
    error: Exception,
    message: str,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session)
        monkeypatch.setattr(
            magic_invasion,
            "_activity_definition",
            lambda _activity_id: {"follow": [70841, 70842]},
        )
        monkeypatch.setattr(
            magic_invasion,
            "_shop_snapshot",
            lambda **_kwargs: (_ for _ in ()).throw(error),
        )

        with pytest.raises(ValueError, match=message):
            magic_invasion.collect_and_store_magic_invasion_activity(session)

        assert session.exec(select(FanxiuExchangeActivity)).all() == []


def test_collect_materializes_shop_personal_and_current_plane_and_refreshes_shop(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session)
        monkeypatch.setattr(
            magic_invasion,
            "_activity_definition",
            lambda _activity_id: {"follow": [70841, 70842]},
        )
        shop_costs = iter((1000, 1200))
        monkeypatch.setattr(
            magic_invasion,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(next(shop_costs)),
        )

        first = magic_invasion.collect_and_store_magic_invasion_activity(session)
        second = magic_invasion.collect_and_store_magic_invasion_activity(
            session,
            activity_id=first.id,
        )
        rows = session.exec(
            select(FanxiuExchangeRanking).where(
                FanxiuExchangeRanking.activity_id == first.id
            )
        ).all()
        stored = session.get(FanxiuExchangeActivity, first.id)

    assert second.shop_items[0].token_cost == 1200
    assert {row.ranking_scope for row in rows} == {"personal", "plane"}
    personal_rows = [row for row in rows if row.ranking_scope == "personal"]
    assert len(personal_rows) == 1
    assert personal_rows[0].is_self is True
    assert personal_rows[0].raw_data["row_source"] == "personal_item_fallback"
    assert personal_rows[0].raw_data["reported_rank_list_size"] == 73
    assert not any(row.role_key.startswith("last-player:") for row in personal_rows)
    assert stored is not None
    assert stored.evidence["current_related_ranking_scopes"] == ["plane"]
    assert stored.evidence["shop"]["source"] == "test-runtime-shop"
    assert stored.evidence["refresh_status"]["currency"] == "retained"
    assert stored.evidence["refresh_status"]["currency_stale"] is True


def test_explicit_collect_refreshes_absolute_wallet_history_before_materializing(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session)
        monkeypatch.setattr(
            magic_invasion,
            "_activity_definition",
            lambda _activity_id: {"follow": [70841, 70842]},
        )
        monkeypatch.setattr(
            magic_invasion,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(),
        )
        monkeypatch.setattr(
            magic_invasion,
            "_runtime_currency_snapshot",
            lambda **_kwargs: {
                "currency_type": 17,
                "exchange_currency": 145_000,
                "currency_amount": 145_000,
                "currency_borrow": 0,
                "cumulative_currency": 200_000,
                "captured_at": "2026-08-10 20:00:00",
                "evidence": {"process_start_ticks": 123},
            },
        )

        detail = magic_invasion.collect_and_store_magic_invasion_activity(session)
        fact = session.exec(
            select(FanxiuPacketBusinessRecord).where(
                FanxiuPacketBusinessRecord.domain == "resource_state",
                FanxiuPacketBusinessRecord.record_key == "currency:17",
            )
        ).first()
        stored = session.get(FanxiuExchangeActivity, detail.id)

    assert detail.current_currency == 145_000
    assert detail.cumulative_currency == 200_000
    assert fact is not None
    assert fact.protocol == "runtime_memory_wallet"
    assert fact.payload["history"] == 200_000
    assert stored is not None
    assert stored.evidence["refresh_status"]["currency"] == "updated"
    assert stored.evidence["refresh_status"]["currency_stale"] is False


def test_collect_does_not_materialize_plane_fact_from_another_occurrence(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session, plane_captured_at="2026-06-24 01:29:36")
        monkeypatch.setattr(
            magic_invasion,
            "_activity_definition",
            lambda _activity_id: {"follow": [70841, 70842]},
        )
        monkeypatch.setattr(
            magic_invasion,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(),
        )

        result = magic_invasion.collect_and_store_magic_invasion_activity(session)
        plane_rows = session.exec(
            select(FanxiuExchangeRanking).where(
                FanxiuExchangeRanking.activity_id == result.id,
                FanxiuExchangeRanking.ranking_scope == "plane",
            )
        ).all()
        stored = session.get(FanxiuExchangeActivity, result.id)

    assert plane_rows == []
    assert result.captured_at == "2026-08-10 18:48:39"
    assert stored is not None
    assert stored.evidence["current_related_ranking_scopes"] == []


def test_collect_does_not_materialize_personal_fact_from_another_occurrence(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session)
        personal = session.get(FanxiuPacketBusinessRecord, "personal")
        assert personal is not None
        personal.captured_at = "2026-06-24 01:29:36"
        session.add(personal)
        session.commit()
        monkeypatch.setattr(
            magic_invasion,
            "_activity_definition",
            lambda _activity_id: {"follow": [70841, 70842]},
        )

        magic_invasion.ensure_magic_invasion_activity(session)
        activities = session.exec(select(FanxiuExchangeActivity)).all()

    assert activities == []


def test_collect_rejects_unknown_explicit_activity_id() -> None:
    with _session() as session:
        _seed_collectable_facts(session)

        try:
            magic_invasion.collect_and_store_magic_invasion_activity(
                session,
                activity_id="missing-instance",
            )
        except ValueError as exc:
            assert str(exc) == "魔道入侵活动实例不存在"
        else:
            raise AssertionError("unknown explicit activity id must be rejected")


def test_refresh_retains_plane_rows_when_companion_fact_is_temporarily_not_current(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session)
        monkeypatch.setattr(
            magic_invasion,
            "_activity_definition",
            lambda _activity_id: {"follow": [70841, 70842]},
        )
        monkeypatch.setattr(
            magic_invasion,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(),
        )
        first = magic_invasion.collect_and_store_magic_invasion_activity(session)
        _add_business_fact(
            session,
            row_id="future-plane",
            domain="activity_rank",
            entity_id="70842",
            captured_at="2026-08-11 18:40:00",
            payload={"snapshot": {
                "rank_vo_type": "ActivityRankCrossServerVO",
                "rank_list_size": 1,
                "personal_item": {
                    "id": 99999, "rank": 1, "score": 1,
                },
                "items": [{"id": 99999, "rank": 1, "score": 1}],
            }},
        )
        session.commit()

        refreshed = magic_invasion.collect_and_store_magic_invasion_activity(
            session,
            activity_id=first.id,
        )
        plane_rows = session.exec(
            select(FanxiuExchangeRanking).where(
                FanxiuExchangeRanking.activity_id == first.id,
                FanxiuExchangeRanking.ranking_scope == "plane",
            )
        ).all()
        stored = session.get(FanxiuExchangeActivity, first.id)

    assert [(row.rank, row.score) for row in plane_rows] == [(5, 8473998)]
    assert refreshed.captured_at == "2026-08-10 18:48:39"
    assert stored is not None
    assert stored.evidence["retained_related_ranking_scopes"] == ["plane"]


def test_ensure_catches_existing_instance_up_to_new_db_facts_without_game_shop(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session)
        monkeypatch.setattr(
            magic_invasion,
            "_activity_definition",
            lambda _activity_id: {"follow": [70841, 70842]},
        )
        monkeypatch.setattr(
            magic_invasion,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(),
        )
        first = magic_invasion.collect_and_store_magic_invasion_activity(session)
        _add_business_fact(
            session,
            row_id="new-personal",
            domain="activity_rank",
            entity_id="70841",
            captured_at="2026-08-10 19:00:00",
            payload={"snapshot": {
                "rank_vo_type": "ActivityRankPersonalVO",
                "rank_list_size": 73,
                "personal_item": {
                    "id": 1001, "key": "role:1", "name": "测试角色",
                    "rank": 12, "score": 800000, "server_id": 22077,
                },
                "items": [],
            }},
        )
        session.commit()
        monkeypatch.setattr(
            magic_invasion,
            "_shop_snapshot",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("GET ensure must not inspect game memory")
            ),
        )

        magic_invasion.ensure_magic_invasion_activity(session)
        self_row = session.exec(
            select(FanxiuExchangeRanking).where(
                FanxiuExchangeRanking.activity_id == first.id,
                FanxiuExchangeRanking.ranking_scope == "personal",
                FanxiuExchangeRanking.is_self.is_(True),
            )
        ).one()
        stored = session.get(FanxiuExchangeActivity, first.id)

    assert self_row.rank == 12
    assert self_row.score == 800000
    assert stored is not None
    assert stored.captured_at == "2026-08-10 19:00:00"
