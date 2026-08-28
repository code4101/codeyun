from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.activity.exchange_event import (
    _ranking_view,
    is_exchange_activity_active,
    latest_exchange_activity_snapshot,
    list_exchange_activity_observations,
    list_exchange_activity_snapshot,
    update_exchange_priorities,
    update_exchange_shop_item_lock,
    upsert_exchange_activity_snapshot,
)
from backend.models import FanxiuExchangeActivity, FanxiuExchangeRanking


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _payload() -> dict:
    return {
        "activity_type": "xutian-palace",
        "cross_count": 32,
        "start_date": "2026-08-03",
        "end_date": "2026-08-06",
        "game_shop_base_id": 80000,
        "currency_type": 12,
        "currency_name": "纳元晶",
        "current_currency": 0,
        "cumulative_currency": 0,
        "captured_at": "2026-08-03T11:00:00",
        "source_kind": "static_config_confirmed_by_game_view",
        "expected_shop_item_count": 2,
        "shop_items": [
            {
                "goods_id": 1,
                "item_id": 29601,
                "source_order": 1,
                "name": "金蛟心·绝品",
                "goods_num": 1,
                "token_cost": 1000,
                "purchase_limit": 25,
            },
            {
                "goods_id": 2,
                "item_id": 3134003,
                "source_order": 2,
                "name": "悟境残页·水衍",
                "goods_num": 20,
                "token_cost": 10000,
                "purchase_limit": 1,
                "discount": 50,
                "original_price": 20000,
            },
        ],
    }


def test_server_ranking_view_resolves_display_name_in_shared_projection() -> None:
    row = FanxiuExchangeRanking(
        activity_id="lingchong-jingwu-8-2026-08-12-2026-08-13",
        ranking_scope="plane",
        rank=1,
        score=464988,
        role_key="22054",
        server_id=22054,
    )

    result = _ranking_view(row, subject_kind="server")

    assert result.name == "时节如流"
    assert result.server_name == "时节如流"
    assert result.subject is not None
    assert result.subject.name == "时节如流"
    assert result.subject.server_name == "时节如流"

    personal = _ranking_view(
        FanxiuExchangeRanking(
            activity_id=row.activity_id,
            ranking_scope="personal",
            rank=1,
            role_key="player",
            name="玩家",
            server_id=22054,
        ),
        subject_kind="role",
    )
    assert personal.name == "玩家"
    assert personal.server_name == "时节如流"


def test_exchange_activity_snapshot_is_scoped_and_sorted() -> None:
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(session, _payload())
        snapshot = list_exchange_activity_snapshot(
            session, activity_type="xutian-palace"
        )
        assert snapshot.selected_activity is not None
        assert snapshot.selected_activity.id == activity_id
        assert snapshot.activities[0].label == "32跨,2026/8/3-8/6"
        assert [row.name for row in snapshot.selected_activity.shop_items] == [
            "金蛟心·绝品",
            "悟境残页·水衍",
        ]


def test_latest_exchange_activity_snapshot_uses_one_persisted_occurrence() -> None:
    with _session() as session:
        older = _payload()
        older["activity_type"] = "yunmeng-trial"
        older["cross_count"] = 8
        older["start_date"] = "2026-08-01"
        older["end_date"] = "2026-08-02"
        newer = _payload()
        newer["activity_type"] = "beast-abyss"
        newer["cross_count"] = 4
        newer["start_date"] = "2026-08-11"
        newer["end_date"] = "2026-08-12"
        upsert_exchange_activity_snapshot(session, older)
        newer_id = upsert_exchange_activity_snapshot(session, newer)

        result = latest_exchange_activity_snapshot(
            session,
            activity_types=["yunmeng-trial", "beast-abyss"],
        )

    assert result.activity_type == "beast-abyss"
    assert result.snapshot is not None
    assert result.snapshot.selected_activity is not None
    assert result.snapshot.selected_activity.id == newer_id


def test_legacy_runtime_snapshot_without_freshness_envelope_is_not_budget_ready() -> None:
    payload = _payload()
    payload["source_kind"] = "read_only_runtime_facts"
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(session, payload)
        detail = list_exchange_activity_snapshot(
            session,
            activity_type="xutian-palace",
            activity_id=activity_id,
        ).selected_activity

    assert detail is not None
    assert detail.currency_fact_fresh is False
    assert detail.shop_fact_fresh is False
    assert detail.budget_ready is False
    assert "freshness" in detail.budget_block_reason


def test_exchange_activity_active_period_is_backend_authoritative() -> None:
    activity = FanxiuExchangeActivity(
        id="period-test",
        instance_key="period-test",
        activity_type="lingzhuang-huadao",
        cross_count=16,
        start_date="2026-08-03",
        end_date="2026-08-04",
    )
    assert is_exchange_activity_active(activity, today=date(2026, 8, 3)) is True
    assert is_exchange_activity_active(activity, today=date(2026, 8, 4)) is True
    assert is_exchange_activity_active(activity, today=date(2026, 8, 2)) is False
    assert is_exchange_activity_active(activity, today=date(2026, 8, 5)) is False


def test_priorities_and_locks_drive_cumulative_currency() -> None:
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(session, _payload())
        detail = update_exchange_priorities(
            session,
            activity_type="xutian-palace",
            activity_id=activity_id,
            ordered_goods_ids=[2, 1],
        )
        assert [row.cumulative_tokens for row in detail.shop_items] == [35000, 10000]
        detail = update_exchange_shop_item_lock(
            session,
            activity_type="xutian-palace",
            activity_id=activity_id,
            goods_id=2,
            locked=True,
        )
        assert next(row for row in detail.shop_items if row.goods_id == 2).locked


def test_exchange_shop_rejects_more_than_two_locked_rows() -> None:
    payload = _payload()
    payload["expected_shop_item_count"] = 3
    payload["shop_items"].append(
        {
            "goods_id": 3,
            "item_id": 999,
            "source_order": 3,
            "name": "普通资源",
            "goods_num": 1,
            "token_cost": 100,
            "purchase_limit": 1,
        }
    )
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(session, payload)
        for goods_id in (1, 2):
            update_exchange_shop_item_lock(
                session,
                activity_type="xutian-palace",
                activity_id=activity_id,
                goods_id=goods_id,
                locked=True,
            )
        with pytest.raises(ValueError, match="最多锁定两个商品行"):
            update_exchange_shop_item_lock(
                session,
                activity_type="xutian-palace",
                activity_id=activity_id,
                goods_id=3,
                locked=True,
            )


def test_incomplete_exchange_snapshot_is_rejected() -> None:
    payload = _payload()
    payload["expected_shop_item_count"] = 3
    with _session() as session:
        try:
            upsert_exchange_activity_snapshot(session, payload)
        except ValueError as exc:
            assert "快照不完整" in str(exc)
        else:
            raise AssertionError("incomplete snapshot must be rejected")


def test_xutian_runtime_period_can_be_validated_against_activity_config() -> None:
    from backend.core.fanxiu.activity.xutian_palace_instrumentation import (
        resolve_xutian_palace_end_date,
    )

    assert resolve_xutian_palace_end_date(
        start_date="2026-08-03",
        cross_count=32,
        activity_rows=[
            {
                "baseId": 80000,
                "crossGroup": 32,
                "startTime": "ARIT|1_10 00 0",
                "endTime": "ARIT|4_22 0 0",
            },
            {"baseId": 80000, "crossGroup": 16, "endTime": "ARIT|3_22 0 0"},
        ],
    ) == "2026-08-06"


def test_exchange_lifecycle_keeps_settlement_window_distinct() -> None:
    from backend.core.fanxiu.activity.exchange_event import (
        exchange_activity_lifecycle_phase,
    )
    from backend.models import FanxiuExchangeActivity

    activity = FanxiuExchangeActivity(
        instance_key="xutian-settlement-date-test",
        activity_type="xutian-palace",
        cross_count=8,
        start_date="2026-08-18",
        end_date="2026-08-20",
        evidence={"period_close_panel_date": "2026-08-21"},
    )

    assert exchange_activity_lifecycle_phase(activity, today=date(2026, 8, 20)) == "active"
    assert exchange_activity_lifecycle_phase(activity, today=date(2026, 8, 21)) == "settlement"
    assert exchange_activity_lifecycle_phase(activity, today=date(2026, 8, 22)) == "closed"


def test_exchange_lifecycle_uses_exact_runtime_boundaries_when_timestamp_is_available() -> None:
    from datetime import datetime

    from backend.core.fanxiu.activity.exchange_event import (
        exchange_activity_lifecycle_phase,
    )
    from backend.models import FanxiuExchangeActivity

    activity = FanxiuExchangeActivity(
        instance_key="xutian-settlement-time-test",
        activity_type="xutian-palace",
        cross_count=8,
        start_date="2026-08-18",
        end_date="2026-08-20",
        evidence={
            "period_start_time": 1787018400000,
            "period_end_time": 1787234400000,
            "period_close_panel_time": 1787327939000,
        },
    )

    assert exchange_activity_lifecycle_phase(
        activity, at=datetime.fromisoformat("2026-08-20T21:59:59+08:00")
    ) == "active"
    assert exchange_activity_lifecycle_phase(
        activity, at=datetime.fromisoformat("2026-08-20T23:36:41+08:00")
    ) == "settlement"
    assert exchange_activity_lifecycle_phase(
        activity, at=datetime.fromisoformat("2026-08-22T00:00:00+08:00")
    ) == "closed"


def test_xutian_period_comes_from_incremental_runtime_activity_fact() -> None:
    from backend.core.fanxiu.activity.xutian_palace_instrumentation import (
        read_xutian_palace_runtime_period,
    )
    from backend.models import FanxiuPacketBusinessRecord

    with _session() as session:
        session.add(
            FanxiuPacketBusinessRecord(
                domain="worldline_activity",
                record_key="32080001|32080001400002",
                protocol="SM_ActivitySync",
                packet_id="packet-1",
                captured_at="2026-08-03 05:02:52",
                payload={
                    "item": {
                        "class": "HeavenActivityVO",
                        "activityId": 32080001,
                        "activityType": 8,
                        "serverCount": 32,
                        "startTime": 1785722400000,
                        "startTimeText": "2026-08-03 10:00:00",
                        "endTime": 1786024800000,
                        "endTimeText": "2026-08-06 22:00:00",
                        "closePanelTime": 1786118339000,
                    }
                },
            )
        )
        session.commit()

        period = read_xutian_palace_runtime_period(session, cross_count=32)

        assert period["start_date"] == "2026-08-03"
        assert period["end_date"] == "2026-08-06"
        assert period["close_panel_date"] == "2026-08-07"
        assert period["protocol"] == "SM_ActivitySync"


def test_xutian_refresh_updates_currency_when_shop_and_rank_are_not_loaded(
    monkeypatch,
) -> None:
    from backend.core.fanxiu.activity import standard_observation
    from backend.core.fanxiu.activity import xutian_palace_instrumentation as xutian
    from backend.core.fanxiu.instrumentation.activity_shop import (
        FanxiuActivityShopNotLoadedError,
    )
    from backend.models import (
        FanxiuExchangeActivity,
        FanxiuExchangeActivityObservation,
        FanxiuPacketBusinessRecord,
    )

    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(session, _payload())
        session.add(
            FanxiuPacketBusinessRecord(
                domain="worldline_activity",
                record_key="32080001|32080001400002",
                protocol="SM_ActivitySync",
                packet_id="packet-1",
                captured_at="2026-08-03 05:02:52",
                payload={
                    "item": {
                        "class": "HeavenActivityVO",
                        "activityId": 32080001,
                        "activityType": 8,
                        "serverCount": 32,
                        "startTime": 1785722400000,
                        "endTime": 1786024800000,
                        "closePanelTime": 1786118339000,
                    }
                },
            )
        )
        session.add(
            FanxiuPacketBusinessRecord(
                domain="resource_state",
                record_key="currency:12",
                protocol="SM_Wallet",
                packet_id="wallet-1",
                entity_id="12",
                captured_at="2026-08-03 13:20:59",
                payload={"amount": 107277, "history": 207577, "borrow": 0},
            )
        )
        session.commit()

        shop_reads = []

        def shop_not_loaded(**kwargs):
            shop_reads.append(kwargs)
            raise FanxiuActivityShopNotLoadedError("游戏尚未加载 V_ShopCfg")

        monkeypatch.setattr(xutian, "collect_xutian_palace_shop_snapshot", shop_not_loaded)
        monkeypatch.setattr(
            xutian,
            "_runtime_currency_snapshot",
            lambda: {
                "currency_type": 12,
                "currency_amount": 117277,
                "currency_borrow": 0,
                "exchange_currency": 117277,
                "cumulative_currency": 217577,
                "captured_at": "2026-08-07T12:00:00+08:00",
                "evidence": {"process_start_ticks": 123},
            },
        )
        monkeypatch.setattr(
            standard_observation,
            "collect_standard_activity_observation",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                standard_observation.ActivityObservationUnavailable("活动榜尚无标准运行态事实")
            ),
        )

        detail = xutian.collect_and_store_xutian_palace_activity(
            session,
            activity_id=activity_id,
            today=date(2026, 8, 7),
            prefer_runtime_rankings=False,
        )

        assert detail.current_currency == 117277
        assert detail.cumulative_currency == 217577
        assert len(detail.shop_items) == 2
        assert shop_reads == [{"expected_cross_count": 32}]
        stored = session.get(FanxiuExchangeActivity, detail.id)
        assert stored is not None
        assert stored.evidence["refresh_status"]["shop"] == "retained"
        assert stored.evidence["refresh_status"]["rankings"] == "retained"
        assert stored.evidence["period_close_panel_date"] == "2026-08-07"
        snapshots = session.exec(
            select(FanxiuExchangeActivityObservation).where(
                FanxiuExchangeActivityObservation.activity_id == activity_id
            )
        ).all()
        assert len(snapshots) == 1
        assert snapshots[0].lifecycle_phase == "settlement"
        assert snapshots[0].snapshot_kind == "formal_end"
        assert snapshots[0].current_currency == 117277
        assert snapshots[0].shop_status == "retained"
        assert len(snapshots[0].payload["shop_items"]) == 2
        page = list_exchange_activity_observations(
            session,
            activity_type="xutian-palace",
            activity_id=activity_id,
        )
        assert page.total == 1
        assert page.items[0].snapshot_kind == "formal_end"


def test_xutian_refresh_uses_personal_total_rank_instead_of_inner_hall(
    monkeypatch,
) -> None:
    from backend.core.fanxiu.activity import xutian_palace_instrumentation as xutian
    from backend.core.fanxiu.activity import rank_reward
    from backend.core.fanxiu.activity.exchange_event import list_exchange_rankings
    from backend.core.fanxiu.instrumentation.activity_shop import (
        FanxiuActivityShopNotLoadedError,
    )
    from backend.models import FanxiuPacketBusinessRecord

    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(session, _payload())
        session.add_all(
            [
                FanxiuPacketBusinessRecord(
                    domain="worldline_activity",
                    record_key="32080001|32080001400002",
                    protocol="SM_ActivitySync",
                    packet_id="period-1",
                    captured_at="2026-08-06 20:58:16",
                    payload={
                        "item": {
                            "class": "HeavenActivityVO",
                            "activityId": 32080001,
                            "activityType": 8,
                            "serverCount": 32,
                            "startTime": 1785722400000,
                            "endTime": 1786024800000,
                        }
                    },
                ),
                FanxiuPacketBusinessRecord(
                    domain="resource_state",
                    record_key="currency:12",
                    protocol="SM_Wallet",
                    packet_id="wallet-1",
                    entity_id="12",
                    captured_at="2026-08-06 20:58:16",
                    payload={"amount": 13727, "history": 264027, "borrow": 0},
                ),
                FanxiuPacketBusinessRecord(
                    domain="activity_rank",
                    record_key="83241|0",
                    protocol="SM_ActivityRankSync",
                    packet_id="inner-hall-1",
                    entity_id="83241",
                    captured_at="2026-08-06 20:58:16",
                    payload={
                        "snapshot": {
                            "activity_id": "83241",
                            "rank_list_size": 100,
                            "rank_vo_type": "ActivityRankPersonalVO",
                            "personal_item": {
                                "rank": 36,
                                "score": 972003,
                                "key": "self",
                                "name": "止清ღ羊驼",
                            },
                            "items": [],
                        }
                    },
                ),
                FanxiuPacketBusinessRecord(
                    domain="activity_rank",
                    record_key="83291|0",
                    protocol="SM_ActivityRankSync",
                    packet_id="personal-total-1",
                    entity_id="83291",
                    captured_at="2026-08-06 20:58:18",
                    payload={
                        "snapshot": {
                            "activity_id": "83291",
                            "rank_list_size": 358,
                            "rank_vo_type": "ActivityRankPersonalVO",
                            "personal_item": {
                                "rank": 29,
                                "score": 1254639,
                                "key": "self",
                                "name": "止清ღ羊驼",
                            },
                            "items": [],
                        }
                    },
                ),
                FanxiuPacketBusinessRecord(
                    domain="activity_rank",
                    record_key="83271|0",
                    protocol="SM_ActivityRankSync",
                    packet_id="plane-total-1",
                    entity_id="83271",
                    captured_at="2026-08-06 20:58:19",
                    payload={
                        "snapshot": {
                            "activity_id": "83271",
                            "rank_list_size": 3,
                            "rank_vo_type": "ActivityRankCrossServerVO",
                            "personal_item": {
                                "rank": 2,
                                "score": 23456789,
                                "id": 22031,
                                "key": "22031",
                                "server_id": 22031,
                                "server_name": "福泽天下",
                            },
                            "items": [
                                {
                                    "rank": 1,
                                    "score": 53563638,
                                    "id": 22037,
                                    "key": "22037",
                                    "server_id": 22037,
                                    "server_name": "夜以继日",
                                },
                                {
                                    "rank": 2,
                                    "score": 23456789,
                                    "id": 22031,
                                    "key": "22031",
                                    "server_id": 22031,
                                    "server_name": "福泽天下",
                                },
                                {
                                    "rank": 3,
                                    "score": 7439740,
                                    "id": 22087,
                                    "key": "22087",
                                    "server_id": 22087,
                                    "server_name": "脱颖而出",
                                },
                            ],
                        }
                    },
                ),
            ]
        )
        session.commit()
        monkeypatch.setattr(
            rank_reward,
            "load_activity_rank_reward_tiers",
            lambda **kwargs: (
                [{"rank_start": 17, "rank_end": 32, "rewards": []}]
                if int(kwargs["rank_activity_id"]) == 83291
                else [
                    {"rank_start": 1, "rank_end": 1, "rewards": []},
                    {"rank_start": 2, "rank_end": 2, "rewards": []},
                ]
            ),
        )
        monkeypatch.setattr(
            xutian,
            "collect_xutian_palace_shop_snapshot",
            lambda **_kwargs: (_ for _ in ()).throw(
                FanxiuActivityShopNotLoadedError("测试未加载活动商店")
            ),
        )

        xutian.collect_and_store_xutian_palace_activity(
            session,
            activity_id=activity_id,
            today=date(2026, 8, 6),
            prefer_runtime_rankings=False,
            collect_runtime_wallet=False,
        )
        rankings = list_exchange_rankings(
            session,
            activity_type="xutian-palace",
            activity_id=activity_id,
            page=1,
            page_size=100,
            ranking_scope="personal",
        )

        self_row = next(row for row in rankings.items if row.is_self)
        assert self_row.rank == 29
        assert self_row.score == 1254639

        plane_rankings = list_exchange_rankings(
            session,
            activity_type="xutian-palace",
            activity_id=activity_id,
            page=1,
            page_size=100,
            ranking_scope="plane",
        )
        assert [(row.rank, row.server_id, row.score) for row in plane_rankings.items] == [
            (1, 22037, 53563638),
            (2, 22031, 23456789),
            (3, 22087, 7439740),
        ]
        assert next(row for row in plane_rankings.items if row.is_self).rank == 2


def test_xutian_runtime_rank_snapshot_keeps_full_rows_for_storage(monkeypatch) -> None:
    from backend.core.fanxiu.activity import xutian_palace_instrumentation as xutian
    from backend.core.fanxiu.activity import rank_reward
    from backend.core.fanxiu.instrumentation import (
        activity_rank_runtime,
        resource_ranking,
        runtime_memory,
    )

    class Memory:
        pid = 123
        process_start_ticks = 456

    monkeypatch.setattr(runtime_memory.MumuProcessMemory, "discover", lambda: Memory())
    monkeypatch.setattr(runtime_memory, "LuaJitReader", lambda _memory: object())
    monkeypatch.setattr(
        activity_rank_runtime,
        "resolve_activity_rank_root",
        lambda *_args, **_kwargs: (0x1234, True),
    )
    monkeypatch.setattr(
        rank_reward,
        "load_activity_rank_reward_tiers",
        lambda **kwargs: [
            {
                "rank_start": 1,
                "rank_end": 1 if int(kwargs["rank_activity_id"]) == 83291 else 2,
                "rewards": [],
            }
        ],
    )
    calls: list[tuple[int, bool, list[dict]]] = []

    def fake_rank_data(
        _reader,
        _root,
        activity_id,
        *,
        reward_tiers=None,
        key_points_only,
    ):
        calls.append((int(activity_id), bool(key_points_only), list(reward_tiers or [])))
        scope_rank = 29 if int(activity_id) == 83291 else 8
        return {
            "rank_list_size": 358 if int(activity_id) == 83291 else 32,
            "loaded_rank_count": 201 if int(activity_id) == 83291 else 32,
            "rankings": [
                {
                    "rank": scope_rank,
                    "score": 123,
                    "role_key": str(activity_id),
                    "name": "我" if int(activity_id) == 83291 else "",
                    "server_id": 22077,
                    "server_name": "岁序更替",
                    "club_name": "",
                    "is_self": True,
                    "is_reward_guard": False,
                },
                {
                    "rank": scope_rank + 1,
                    "score": 100,
                    "role_key": f"ordinary-{activity_id}",
                    "name": "普通玩家",
                    "server_id": 22078,
                    "server_name": "海浪无声",
                    "club_name": "",
                    "is_self": False,
                    "is_reward_guard": False,
                },
            ],
        }

    monkeypatch.setattr(resource_ranking, "_rank_data", fake_rank_data)
    snapshot = xutian.collect_xutian_palace_rank_snapshot(
        event_date="2026-08-03",
        cross_count=32,
        server_day=9999,
        allow_discovery=True,
    )

    assert [(row[0], row[1]) for row in calls] == [
        (83291, False),
        (83271, False),
    ]
    assert [(row["ranking_scope"], row["rank"]) for row in snapshot["rankings"]] == [
        ("personal", 29),
        ("personal", 30),
        ("plane", 8),
        ("plane", 9),
    ]


def test_exchange_ranking_page_returns_full_entries_and_separate_reward_tiers(monkeypatch) -> None:
    from backend.core.fanxiu.activity import rank_reward
    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_rankings,
        replace_exchange_rankings,
    )

    monkeypatch.setattr(
        rank_reward,
        "load_activity_rank_reward_tiers",
        lambda **_: [
            {"rank_start": 1, "rank_end": 1, "rewards": ["Item|9070095_320"]},
        ],
    )
    with _session() as session:
        payload = _payload()
        payload["game_rank_activity_id"] = 83291
        activity_id = upsert_exchange_activity_snapshot(session, payload)
        replace_exchange_rankings(
            session,
            activity_type="xutian-palace",
            activity_id=activity_id,
            captured_at="2026-08-06T23:00:00+08:00",
            rows=[
                {"rank": 1, "score": 500, "role_key": "one", "name": "守门员"},
                {"rank": 2, "score": 400, "role_key": "self", "name": "自己", "is_self": True},
                {"rank": 3, "score": 300, "role_key": "ordinary", "name": "普通玩家"},
                {
                    "rank": 4,
                    "score": 200,
                    "role_key": "last",
                    "name": "末位玩家",
                    "is_last_player": True,
                },
            ],
        )

        result = list_exchange_rankings(
            session,
            activity_type="xutian-palace",
            activity_id=activity_id,
            ranking_scope="personal",
            page_size=100,
        )

        assert [(row.rank, row.name) for row in result.entries] == [
            (1, "守门员"),
            (2, "自己"),
            (3, "普通玩家"),
            (4, "末位玩家"),
        ]
        assert result.items[0].is_reward_guard is True
        assert result.entry_total == 4
        assert result.loaded_entry_count == 4
        assert [(row.rank, row.name) for row in result.items] == [
            (1, "守门员"),
            (2, "自己"),
            (4, "末位玩家"),
        ]
        assert result.total == 3
        assert result.reward_tiers[0].has_player is False
        assert result.reward_tiers[0].reward_rank_end == 1
        assert result.scope.model_dump() == {
            "key": "personal",
            "label": "个人榜",
            "role": "primary",
            "subject_kind": "role",
        }


def test_exchange_ranking_page_accepts_registered_team_scope(monkeypatch) -> None:
    from dataclasses import replace

    from backend.core.fanxiu.activity import exchange_activity_registry as registry
    from backend.core.fanxiu.activity.exchange_activity_spec import (
        RankActivityIdBinding,
        RankScopeSpec,
    )
    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_rankings,
        replace_exchange_rankings,
    )

    team_scope = RankScopeSpec(
        scope="team",
        label="队伍榜",
        role="comparative",
        subject="team",
        required=False,
        accepted_vo_types=("ActivityRankTeamVO",),
        activity_id=RankActivityIdBinding(source="activity_follow", follow_index=1),
        row_mode="full_observed",
        reward_tiers_enabled=False,
    )
    team_spec = replace(
        registry.XUTIAN_PALACE_SPEC,
        rank_scopes=(registry.XUTIAN_PALACE_SPEC.rank_scopes[0], team_scope),
        page=replace(
            registry.XUTIAN_PALACE_SPEC.page,
            ranking_scopes=("personal", "team"),
        ),
    )
    monkeypatch.setattr(registry, "get_exchange_activity_spec", lambda _: team_spec)

    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(session, _payload())
        replace_exchange_rankings(
            session,
            activity_type="xutian-palace",
            activity_id=activity_id,
            captured_at="2026-08-10 20:00:00",
            rows=[{
                "ranking_scope": "team",
                "rank": 1,
                "score": 123,
                "role_key": "team:1",
                "name": "第一队",
                "raw_data": {
                    "rank_activity_id": 110208,
                    "reported_rank_list_size": 1,
                    "loaded_player_count": 1,
                    "scope_complete": True,
                    "members": [{"name": "甲"}],
                },
            }],
        )

        result = list_exchange_rankings(
            session,
            activity_type="xutian-palace",
            activity_id=activity_id,
            ranking_scope="team",
        )

    assert result.scope.subject_kind == "team"
    assert result.entry_total == 1
    assert result.complete is True
    assert result.entries[0].subject is not None
    assert result.entries[0].subject.members == [{"name": "甲"}]


def test_xutian_refresh_requires_a_standard_currency_fact() -> None:
    from backend.core.fanxiu.activity import xutian_palace_instrumentation as xutian
    from backend.core.fanxiu.activity.standard_observation import (
        ActivityObservationUnavailable,
    )
    from backend.models import FanxiuExchangeActivity, FanxiuPacketBusinessRecord

    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(session, _payload())
        session.add(
            FanxiuPacketBusinessRecord(
                domain="worldline_activity",
                record_key="32080001|32080001400002",
                protocol="SM_ActivitySync",
                packet_id="packet-1",
                captured_at="2026-08-03 05:02:52",
                payload={
                    "item": {
                        "class": "HeavenActivityVO",
                        "activityId": 32080001,
                        "activityType": 8,
                        "serverCount": 32,
                        "startTime": 1785722400000,
                        "endTime": 1786024800000,
                    }
                },
            )
        )
        session.commit()
        try:
            xutian.collect_and_store_xutian_palace_activity(
                session,
                activity_id=activity_id,
                today=date(2026, 8, 3),
                prefer_runtime_rankings=False,
                collect_runtime_wallet=False,
            )
        except ActivityObservationUnavailable as exc:
            assert "资源类型 12" in str(exc)
        else:
            raise AssertionError("missing standard currency fact must fail")

        stored = session.get(FanxiuExchangeActivity, activity_id)
        assert stored is not None
        assert stored.current_currency == 0
        assert stored.cumulative_currency == 0
