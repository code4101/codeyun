from __future__ import annotations

from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.activity import beast_abyss
from backend.core.fanxiu.activity.standard_observation import (
    ActivityObservationUnavailable,
)
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
        beast_abyss,
        "_runtime_currency_snapshot",
        lambda: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("测试环境钱包缓存未加载")
        ),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule.refresh_cached_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: None,
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
    personal_captured_at: str = "2026-08-12 12:12:21",
    team_captured_at: str = "2026-08-12 12:12:21",
) -> None:
    _add_business_fact(
        session,
        row_id="currency",
        domain="resource_state",
        entity_id="14",
        captured_at="2026-08-12 12:12:21",
        payload={"amount": 36_474, "history": 36_474, "borrow": 0},
        record_key="currency:14",
    )
    _add_business_fact(
        session,
        row_id="personal",
        domain="activity_rank",
        entity_id="110104",
        captured_at=personal_captured_at,
        payload={"snapshot": {
            "rank_vo_type": "ActivityRankPersonalVO",
            "rank_list_size": 2,
            "personal_item": {
                "id": 1001, "key": "role:1", "name": "本人",
                "rank": 1, "score": 2000, "server_id": 22077,
            },
            "items": [
                {"id": 1001, "key": "role:1", "name": "本人", "rank": 1, "score": 2000, "server_id": 22077},
                {"id": 1002, "key": "role:2", "name": "榜尾", "rank": 2, "score": 1000, "server_id": 22078},
            ],
        }},
    )
    _add_business_fact(
        session,
        row_id=f"team:{team_captured_at}",
        domain="activity_rank",
        entity_id="110204",
        captured_at=team_captured_at,
        payload={"snapshot": {
            "rank_vo_type": "ActivityRankTeamVO",
            "rank_list_size": 2,
            "personal_item": {
                "id": 9001, "key": "team:1", "name": "甲队",
                "rank": 1, "score": 3000,
            },
            "items": [
                {"id": 9001, "key": "team:1", "name": "甲队", "rank": 1, "score": 3000},
                {"id": 9002, "key": "team:2", "name": "乙队", "rank": 2, "score": 1500},
            ],
        }},
    )
    session.commit()


def _patch_collect_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        beast_abyss,
        "_runtime_period",
        lambda _session, **_kwargs: {
            "game_activity_id": 4150001,
            "cross_count": 4,
            "start_date": "2026-08-11",
            "end_date": "2026-08-12",
            "captured_at": "2026-08-12 12:12:21",
            "record_id": "runtime:4150001",
            "runtime_id": "4150001400002",
            "packet_id": "",
            "world_level": 212,
            "source_kind": "worldline_activity_runtime_memory",
        },
    )
    monkeypatch.setattr(
        beast_abyss,
        "_activity_definition",
        lambda _activity_id: {"follow": [110104, 110204]},
    )
    # Keep collector contract tests independent from the developer machine's
    # live Runtime rank cache.  The durable facts seeded by each test are the
    # intended observation source here; Runtime ingestion has its own tests.
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.activity_rank_runtime.read_activity_rank_runtime_snapshot",
        lambda _rank_id: {"ok": True, "complete": True},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.standard_observation.store_runtime_activity_rank_fact",
        lambda *_args, **_kwargs: None,
    )


def _shop_snapshot(token_cost: int = 1000) -> dict:
    return {
        "active_shop_item_count": 1,
        "items": [{
            "goods_id": 15000001,
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


def test_beast_period_prefers_current_runtime_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule.get_cached_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: {
            "available": True,
            "created_at": "2026-08-12T11:42:38+08:00",
            "source_kind": "worldline_activity_runtime_memory",
            "items": [
                {
                    "id": 4150001400002,
                    "activityId": 4150001,
                    "activityType": 15,
                    "name": "兽渊探秘",
                    "serverCount": 4,
                    "avgWorldLevel": 212,
                    "startTime": 1786413600000,
                    "endTime": 1786543200000,
                }
            ],
        },
    )

    with _session() as session:
        period = beast_abyss._runtime_period(
            session, target_date=date(2026, 8, 12)
        )

    assert period["game_activity_id"] == 4150001
    assert period["cross_count"] == 4
    assert period["start_date"] == "2026-08-11"
    assert period["end_date"] == "2026-08-12"
    assert period["close_panel_date"] == "2026-08-12"
    assert period["source_kind"] == "worldline_activity_runtime_memory"


def test_beast_period_rejects_historical_packet_for_current_date(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule.get_cached_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: {"available": False, "items": []},
    )
    with _session() as session:
        session.add(
            FanxiuPacketBusinessRecord(
                domain="worldline_activity",
                record_key="old-beast",
                protocol="SM_WorldLineActivitySync",
                packet_id="packet-old",
                entity_id="8150001",
                captured_at="2026-07-30 10:00:00",
                payload={
                    "item": {
                        "class": "BeastExplodeActivityVO",
                        "activityId": 8150001,
                        "serverCount": 8,
                        "startTime": 1785376800000,
                        "endTime": 1785506400000,
                    }
                },
            )
        )
        session.commit()

        with pytest.raises(ValueError, match="未找到兽渊探秘运行时活动实例"):
            beast_abyss._runtime_period(
                session, target_date=date(2026, 8, 12)
            )


def test_beast_shop_snapshot_rejects_incomplete_runtime_projection(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        beast_abyss,
        "load_fanxiu_item_runtime_index",
        lambda **_kwargs: {"cards_by_id": {}},
    )
    monkeypatch.setattr(
        beast_abyss,
        "collect_activity_shop_runtime",
        lambda **_kwargs: {"complete": False},
    )

    with pytest.raises(ValueError, match="运行态快照不完整"):
        beast_abyss._shop_snapshot(cross_count=4)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            FanxiuActivityShopNotLoadedError("目标活动兑换页当前未打开"),
            "兑换宝阁尚未加载",
        ),
        (
            FanxiuActivityShopCollectionError("V_ShowList 跨服不一致"),
            "兑换宝阁采集失败",
        ),
    ],
)
def test_first_collect_fails_closed_when_shop_projection_failed(
    monkeypatch,
    error: Exception,
    message: str,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session)
        _patch_collect_contract(monkeypatch)
        monkeypatch.setattr(
            beast_abyss,
            "_shop_snapshot",
            lambda **_kwargs: (_ for _ in ()).throw(error),
        )

        with pytest.raises(ValueError, match=message):
            beast_abyss.collect_and_store_beast_abyss_activity(session)

        assert session.exec(select(FanxiuExchangeActivity)).all() == []


def test_collect_materializes_and_refreshes_shop_and_both_rank_scopes(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session)
        _patch_collect_contract(monkeypatch)
        costs = iter((1000, 1200))
        monkeypatch.setattr(
            beast_abyss,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(next(costs)),
        )

        first = beast_abyss.collect_and_store_beast_abyss_activity(session)
        refreshed = beast_abyss.collect_and_store_beast_abyss_activity(
            session,
            activity_id=first.id,
        )
        rankings = session.exec(
            select(FanxiuExchangeRanking).where(
                FanxiuExchangeRanking.activity_id == first.id
            )
        ).all()
        stored = session.get(FanxiuExchangeActivity, first.id)

    assert refreshed.shop_items[0].token_cost == 1200
    assert {row.ranking_scope for row in rankings} == {"personal", "team"}
    assert stored is not None
    assert stored.evidence["current_related_ranking_scopes"] == ["team"]
    assert stored.evidence["shop"]["source"] == "test-runtime-shop"
    assert stored.evidence["refresh_status"]["currency"] == "retained"
    assert stored.evidence["refresh_status"]["currency_stale"] is True


def test_explicit_refresh_resolves_the_persisted_period_after_activity_end(
    monkeypatch,
) -> None:
    """The shop grace day must not require an activity covering today."""

    with _session() as session:
        _seed_collectable_facts(session)
        _patch_collect_contract(monkeypatch)
        monkeypatch.setattr(
            beast_abyss,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(),
        )
        first = beast_abyss.collect_and_store_beast_abyss_activity(session)
        seen: dict[str, object] = {}

        def resolve_persisted_period(_session, **kwargs):
            seen.update(kwargs)
            return {
                "game_activity_id": 4150001,
                "cross_count": 4,
                "start_date": "2026-08-11",
                "end_date": "2026-08-12",
                "captured_at": "2026-08-12 12:12:21",
                "record_id": "packet:4150001",
                "runtime_id": "4150001400002",
                "packet_id": "packet:4150001",
                "world_level": 212,
                "source_kind": "activity_packet_business_record",
            }

        monkeypatch.setattr(beast_abyss, "_runtime_period", resolve_persisted_period)
        beast_abyss.collect_and_store_beast_abyss_activity(
            session,
            activity_id=first.id,
        )

    assert seen["cross_count"] == 4
    assert seen["target_date"] == date(2026, 8, 12)


def test_explicit_refresh_accepts_final_rank_facts_during_shop_grace_day(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(
            session,
            personal_captured_at="2026-08-13 00:27:49",
            team_captured_at="2026-08-13 02:05:06",
        )
        _patch_collect_contract(monkeypatch)
        monkeypatch.setattr(
            beast_abyss,
            "_runtime_period",
            lambda _session, **_kwargs: {
                "game_activity_id": 4150001,
                "cross_count": 4,
                "start_date": "2026-08-11",
                "end_date": "2026-08-12",
                "close_panel_date": "2026-08-13",
                "captured_at": "2026-08-13 00:01:26",
                "record_id": "packet:4150001",
                "runtime_id": "4150001400002",
                "packet_id": "packet:4150001",
                "world_level": 212,
                "source_kind": "activity_packet_business_record",
            },
        )
        monkeypatch.setattr(
            beast_abyss,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(),
        )

        detail = beast_abyss.collect_and_store_beast_abyss_activity(session)
        scopes = {
            row.ranking_scope
            for row in session.exec(
                select(FanxiuExchangeRanking).where(
                    FanxiuExchangeRanking.activity_id == detail.id
                )
            ).all()
        }

    assert scopes == {"personal", "team"}


def test_explicit_collect_refreshes_absolute_wallet_before_materializing(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session)
        _patch_collect_contract(monkeypatch)
        monkeypatch.setattr(
            beast_abyss,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(),
        )
        monkeypatch.setattr(
            beast_abyss,
            "_runtime_currency_snapshot",
            lambda: {
                "currency_type": 14,
                "exchange_currency": 40_000,
                "currency_amount": 40_000,
                "currency_borrow": 0,
                "cumulative_currency": 50_000,
                "captured_at": "2026-08-12 12:20:00",
                "evidence": {"process_start_ticks": 123},
            },
        )

        detail = beast_abyss.collect_and_store_beast_abyss_activity(session)
        fact = session.exec(
            select(FanxiuPacketBusinessRecord).where(
                FanxiuPacketBusinessRecord.domain == "resource_state",
                FanxiuPacketBusinessRecord.record_key == "currency:14",
            )
        ).first()
        stored = session.get(FanxiuExchangeActivity, detail.id)

    assert detail.current_currency == 40_000
    assert detail.cumulative_currency == 50_000
    assert fact is not None
    assert fact.protocol == "runtime_memory_wallet"
    assert stored is not None
    assert stored.evidence["refresh_status"]["currency"] == "updated"
    assert stored.evidence["refresh_status"]["currency_stale"] is False


def test_ranking_only_refresh_preserves_explicit_wallet_and_shop_freshness(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session)
        _patch_collect_contract(monkeypatch)
        monkeypatch.setattr(
            beast_abyss,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(),
        )
        monkeypatch.setattr(
            beast_abyss,
            "_runtime_currency_snapshot",
            lambda: {
                "currency_type": 14,
                "exchange_currency": 40_000,
                "currency_amount": 40_000,
                "currency_borrow": 0,
                "cumulative_currency": 50_000,
                "captured_at": "2026-08-12 12:20:00",
                "evidence": {"process_start_ticks": 123},
            },
        )

        first = beast_abyss.collect_and_store_beast_abyss_activity(session)
        refreshed = beast_abyss.collect_and_store_beast_abyss_activity(
            session,
            activity_id=first.id,
            collect_runtime_shop=False,
        )
        stored = session.get(FanxiuExchangeActivity, first.id)

    assert refreshed.budget_ready is True
    assert refreshed.currency_fact_fresh is True
    assert refreshed.shop_fact_fresh is True
    assert stored is not None
    assert stored.evidence["refresh_status"]["currency"] == "updated"
    assert stored.evidence["refresh_status"]["shop"] == "updated"


def test_collect_rejects_personal_rank_from_another_occurrence(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(
            session,
            personal_captured_at="2026-08-10 23:59:59",
        )
        _patch_collect_contract(monkeypatch)
        monkeypatch.setattr(
            beast_abyss,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(),
        )

        with pytest.raises(
            ActivityObservationUnavailable,
            match="个人榜事实不属于当前活动周期",
        ):
            beast_abyss.collect_and_store_beast_abyss_activity(session)

        assert session.exec(select(FanxiuExchangeActivity)).all() == []


def test_refresh_retains_team_rows_when_current_fact_temporarily_disappears(
    monkeypatch,
) -> None:
    with _session() as session:
        _seed_collectable_facts(session)
        _patch_collect_contract(monkeypatch)
        monkeypatch.setattr(
            beast_abyss,
            "_shop_snapshot",
            lambda **_kwargs: _shop_snapshot(),
        )
        first = beast_abyss.collect_and_store_beast_abyss_activity(session)
        _add_business_fact(
            session,
            row_id="future-team",
            domain="activity_rank",
            entity_id="110204",
            captured_at="2026-08-13 00:00:01",
            payload={"snapshot": {
                "rank_vo_type": "ActivityRankTeamVO",
                "rank_list_size": 1,
                "personal_item": {
                    "id": 9999,
                    "key": "team:future",
                    "name": "未来队伍",
                    "rank": 1,
                    "score": 9999,
                },
                "items": [{
                    "id": 9999,
                    "key": "team:future",
                    "name": "未来队伍",
                    "rank": 1,
                    "score": 9999,
                }],
            }},
        )
        session.commit()

        beast_abyss.collect_and_store_beast_abyss_activity(
            session,
            activity_id=first.id,
        )
        team_rows = session.exec(
            select(FanxiuExchangeRanking).where(
                FanxiuExchangeRanking.activity_id == first.id,
                FanxiuExchangeRanking.ranking_scope == "team",
            )
        ).all()
        stored = session.get(FanxiuExchangeActivity, first.id)

    assert [(row.rank, row.name) for row in team_rows] == [(1, "甲队"), (2, "乙队")]
    assert stored is not None
    assert stored.evidence["retained_related_ranking_scopes"] == ["team"]
