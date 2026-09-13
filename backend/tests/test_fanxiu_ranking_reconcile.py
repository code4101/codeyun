from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity import ranking_reconcile
from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.models import FanxiuExchangeActivity, FanxiuExchangeShopItem


def test_schedule_registration_is_idempotent_without_enabling_gameplay():
    from datetime import timezone, timedelta
    from backend.core.fanxiu.activity.schedule_page import load_fanxiu_schedule_ranking_snapshot
    from backend.core.fanxiu.activity.ranking_lifecycle import due_ranking_checkpoints, discover_ranking_occurrences

    tz = timezone(timedelta(hours=8))
    now = datetime(2026, 9, 9, 18, tzinfo=tz)

    def stamp(day, hour):
        return int(datetime(2026, 9, day, hour, tzinfo=tz).timestamp() * 1000)

    schedule = {
        "available": True, "complete": True, "captured_at": now.isoformat(),
        "items": [
            {"id": 8080001400004, "activityId": 8080001, "activityType": 8,
             "serverCount": 8, "startTime": stamp(9, 10), "endTime": stamp(11, 22),
             "prepareEndTime": stamp(9, 5), "closePanelTime": stamp(12, 23)},
            {"id": 8043801400027, "activityId": 8043801, "activityType": 12,
             "serverCount": 8, "startTime": stamp(8, 5), "endTime": stamp(9, 22),
             "prepareEndTime": stamp(7, 5), "closePanelTime": stamp(9, 23)},
        ],
    }
    with _session() as session:
        first = ranking_reconcile.sync_ranking_schedule(session, schedule, now=now)
        assert ranking_reconcile.sync_ranking_schedule(session, schedule, now=now) == first
        snapshot = load_fanxiu_schedule_ranking_snapshot(session, business_date=now.date())
        assert snapshot.gameplay_rank.activity_type == "xutian-palace"
        assert snapshot.resource_rank.activity_type == "xiling-zhengwu"
        assert len(snapshot.resource_rank.snapshot.activities) == 1
        activity = session.get(FanxiuExchangeActivity, first[0])
        if activity.activity_type != "xiling-zhengwu":
            activity = session.get(FanxiuExchangeActivity, first[1])
        assert activity.game_rank_activity_id == 43805
        assert activity.evidence["rank_scope_identities"]["plane"]["runtime_rank_activity_id"] == 43806
        assert activity.evidence["refresh_status"]["rankings"] == "unavailable"
    assert not [
        checkpoint for checkpoint in due_ranking_checkpoints(
            discover_ranking_occurrences(schedule), now=now, production_only=True,
        ) if checkpoint.activity_type == "xutian-palace"
    ]


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


def _tiandi_occurrence(activity_id: int, cross_count: int) -> RankingOccurrence:
    timezone = datetime.now().astimezone().tzinfo
    assert timezone is not None
    return RankingOccurrence(
        activity_type="tiandi-yiju",
        family="gameplay_rank",
        runtime_id=f"tiandi-{activity_id}",
        activity_id=activity_id,
        start_at=datetime(2026, 8, 27, 10, tzinfo=timezone),
        end_at=datetime(2026, 8, 27, 22, tzinfo=timezone),
        prepare_at=datetime(2026, 8, 26, 10, tzinfo=timezone),
        close_at=datetime(2026, 8, 30, 23, 59, 59, tzinfo=timezone),
        cross_count=cross_count,
    )


def test_seed_materializes_tiandi_phase_specific_rank_and_shop_contracts() -> None:
    with _session() as session:
        server = ranking_reconcile.seed_ranking_occurrence(
            session,
            _tiandi_occurrence(8090001, 1),
            captured_at="2026-08-27T00:30:00+08:00",
        )
        cross = ranking_reconcile.seed_ranking_occurrence(
            session,
            _tiandi_occurrence(8090004, 8),
            captured_at="2026-08-27T00:30:00+08:00",
        )
        server_contract = (
            server.game_shop_base_id,
            server.currency_type,
            dict(server.evidence["rank_scope_identities"]),
        )
        cross_contract = (
            cross.game_shop_base_id,
            cross.currency_type,
            dict(cross.evidence["rank_scope_identities"]),
        )

    assert server_contract[:2] == (90000, 11)
    assert server_contract[2] == {
        "personal": {"runtime_rank_activity_id": 90101, "reward_activity_id": 90101},
        "alliance": {"runtime_rank_activity_id": 90102, "reward_activity_id": 90102},
    }
    assert cross_contract[:2] == (90002, 13)
    assert cross_contract[2] == {
        "personal": {"runtime_rank_activity_id": 90808, "reward_activity_id": 90808},
        "alliance": {"runtime_rank_activity_id": 90813, "reward_activity_id": 90813},
    }


def test_seed_rejects_tiandi_activity_and_cross_count_mismatch() -> None:
    with _session() as session:
        with pytest.raises(ValueError, match="活动与跨数不一致"):
            ranking_reconcile.seed_ranking_occurrence(
                session,
                _tiandi_occurrence(8090001, 8),
                captured_at="2026-08-27T00:30:00+08:00",
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


def test_seed_existing_occurrence_preserves_observation_state(monkeypatch) -> None:
    monkeypatch.setattr(
        ranking_reconcile,
        "_activity_definition_index",
        lambda: {700014: {"id": 700014, "follow": [7000114, 7000214]}},
    )
    occurrence = _magic_occurrence(cross_count=8)
    with _session() as session:
        activity = ranking_reconcile.seed_ranking_occurrence(
            session,
            occurrence,
            captured_at="2026-08-21T00:30:00+08:00",
        )
        activity.captured_at = "2026-08-21T23:42:58+08:00"
        activity.source_kind = "beast_abyss_runtime_collection"
        activity.instance_data = {
            **dict(activity.instance_data or {}),
            "beast_abyss_initialization": {"status": "not_started"},
            "custom_state": {"keep": True},
        }
        session.add(activity)
        session.flush()

        reseeded = ranking_reconcile.seed_ranking_occurrence(
            session,
            occurrence,
            captured_at="2026-08-22T00:30:00+08:00",
        )

        assert reseeded.captured_at == "2026-08-21T23:42:58+08:00"
        assert reseeded.source_kind == "beast_abyss_runtime_collection"
        assert reseeded.instance_data["beast_abyss_initialization"] == {
            "status": "not_started"
        }
        assert reseeded.instance_data["custom_state"] == {"keep": True}
        assert reseeded.instance_data["world_level"] == occurrence.world_level


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
        lambda _session, *, activity_type, activity_id: (
            collected.append((activity_type, activity_id))
            or SimpleNamespace(
                shop_refresh_status="updated",
                shop_refresh_reason="",
            )
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


def test_reconcile_does_not_complete_when_shop_collection_was_retained(
    monkeypatch,
) -> None:
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
        lambda *_args, **_kwargs: SimpleNamespace(
            shop_refresh_status="retained",
            shop_refresh_reason="目标活动兑换页当前未打开，无法读取 V_ShowList",
        ),
    )
    monkeypatch.setattr(
        ranking_reconcile,
        "list_exchange_rankings",
        lambda *_args, **_kwargs: SimpleNamespace(
            reward_tiers=[object()],
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

    assert result["status"] == "blocked"
    assert result["facts"]["shop"] == "retained"
    assert result["facts"]["shop_refresh_status"] == "retained"
    assert "V_ShowList" in result["message"]


def test_reconcile_does_not_complete_without_required_reward_tiers(
    monkeypatch,
) -> None:
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
        lambda *_args, **_kwargs: SimpleNamespace(
            shop_refresh_status="updated",
            shop_refresh_reason="",
        ),
    )
    monkeypatch.setattr(
        ranking_reconcile,
        "list_exchange_rankings",
        lambda *_args, **_kwargs: SimpleNamespace(
            reward_tiers=[],
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

    assert result["status"] == "blocked"
    assert result["facts"]["shop"] == "updated"
    assert "榜单奖励档次本次未加载" in result["message"]


def test_reconcile_accepts_occurrence_shop_snapshot_covering_checkpoint_watermark(
    monkeypatch,
) -> None:
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

    def retain_after_open(session, *, activity_type, activity_id):
        assert activity_type == "magic-invasion"
        activity = session.get(FanxiuExchangeActivity, activity_id)
        assert activity is not None
        activity.evidence = {
            **dict(activity.evidence or {}),
            "shop_snapshot_captured_at": "2026-08-21T00:31:00+08:00",
            "refresh_status": {
                "shop": "retained",
                "shop_reason": "目标活动兑换页当前未打开，无法读取 V_ShowList",
            },
        }
        session.add(activity)
        session.add(
            FanxiuExchangeShopItem(
                activity_id=activity_id,
                goods_id=1,
                item_id=1,
                name="已采商品",
                token_cost=100,
            )
        )
        session.commit()
        return SimpleNamespace(
            shop_refresh_status="retained",
            shop_refresh_reason="目标活动兑换页当前未打开，无法读取 V_ShowList",
            shop_snapshot_captured_at="2026-08-21T00:31:00+08:00",
        )

    monkeypatch.setattr(
        ranking_reconcile,
        "collect_registered_exchange_activity",
        retain_after_open,
    )
    monkeypatch.setattr(
        ranking_reconcile,
        "list_exchange_rankings",
        lambda *_args, **_kwargs: SimpleNamespace(
            reward_tiers=[object()],
            loaded_entry_count=0,
            declared_rank_count=0,
            complete=False,
        ),
    )

    with _session() as session:
        result = ranking_reconcile.reconcile_ranking_occurrence(
            session,
            _magic_occurrence(cross_count=8),
            captured_at="2026-08-21T00:40:00+08:00",
            required_fact_watermark=datetime.fromisoformat(
                "2026-08-21T00:30:00+08:00"
            ),
        )

    assert result["status"] == "completed"
    assert result["facts"]["shop"] == "updated"
    assert result["facts"]["shop_refresh_status"] == "retained"
    assert result["facts"]["shop_watermark_satisfied"] is True


def test_reconcile_does_not_complete_when_live_collection_failed(monkeypatch) -> None:
    monkeypatch.setattr(
        ranking_reconcile,
        "materialize_registered_exchange_activity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        ranking_reconcile,
        "collect_registered_exchange_activity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("兑换页当前未打开")
        ),
    )
    monkeypatch.setattr(
        ranking_reconcile,
        "list_exchange_rankings",
        lambda *_args, **_kwargs: SimpleNamespace(
            reward_tiers=[object()],
            loaded_entry_count=0,
            declared_rank_count=0,
            complete=False,
        ),
    )
    with _session() as session:
        result = ranking_reconcile.reconcile_ranking_occurrence(
            session,
            _tiandi_occurrence(8090001, 1),
            captured_at="2026-08-28T00:30:00+08:00",
        )

    assert result["status"] == "blocked"
    assert result["collect_error"] == "兑换页当前未打开"
    assert "Runtime 采集未完成" in result["message"]


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
                instance_key="proven-server-day-anchor",
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
    assert activity.evidence["rank_scope_identities"] == {
        "personal": {"runtime_rank_activity_id": 43103, "reward_activity_id": 43103},
        "plane": {"runtime_rank_activity_id": 43104, "reward_activity_id": 43104},
    }
    assert activity.evidence["server_day"] == 31
    assert "monotonic lower bound" in activity.evidence["server_day_evidence"]


@pytest.mark.parametrize("snapshot_at,expected_status", [
    ("2026-08-21T00:31:00+08:00", "completed"),
    ("2026-08-20T00:31:00+08:00", "blocked"),
])
def test_reconcile_persisted_facts_never_collects_and_requires_fresh_shop(
    monkeypatch, snapshot_at, expected_status,
):
    """A retained 'updated' flag cannot make yesterday's snapshot fresh."""
    monkeypatch.setattr(ranking_reconcile, "_activity_definition_index",
                        lambda: {700014: {"id": 700014, "follow": [7000114, 7000214]}})
    def unexpected(*args, **kwargs):
        raise AssertionError("persisted projection must not collect Runtime")
    monkeypatch.setattr(ranking_reconcile, "materialize_registered_exchange_activity", unexpected)
    monkeypatch.setattr(ranking_reconcile, "collect_registered_exchange_activity", unexpected)
    monkeypatch.setattr(ranking_reconcile, "list_exchange_rankings", lambda *args, **kwargs:
        SimpleNamespace(reward_tiers=[object()], loaded_entry_count=0,
                        declared_rank_count=0, complete=False))
    occurrence = _magic_occurrence(cross_count=8)
    with _session() as session:
        activity = ranking_reconcile.seed_ranking_occurrence(
            session, occurrence, captured_at=snapshot_at,
        )
        activity.evidence = {**dict(activity.evidence or {}),
            "shop_snapshot_captured_at": snapshot_at,
            "refresh_status": {"shop": "updated"}}
        session.add(activity)
        session.add(FanxiuExchangeShopItem(activity_id=activity.id, goods_id=1,
                    item_id=1, name="已采商品", token_cost=100, locked=True))
        session.commit()
        result = ranking_reconcile.reconcile_ranking_occurrence(
            session, occurrence, captured_at="2026-08-21T00:40:00+08:00",
            required_fact_watermark=datetime.fromisoformat("2026-08-21T00:30:00+08:00"),
            collect_live_facts=False,
        )
        from sqlmodel import select
        assert session.exec(select(FanxiuExchangeShopItem).where(
            FanxiuExchangeShopItem.activity_id == activity.id)).one().locked
        assert result["activity_id"] == activity.id
        assert result["status"] == expected_status
        assert result["facts"]["shop_item_count"] == 1
        assert result["facts"]["shop_watermark_satisfied"] == (expected_status == "completed")
