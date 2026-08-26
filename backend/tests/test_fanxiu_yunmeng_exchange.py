from __future__ import annotations

from datetime import date

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.exchange_event import (
    list_exchange_activity_snapshot,
    update_exchange_priorities,
    update_exchange_shop_item_lock,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.activity.yunmeng_exchange import (
    _period_from_item,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _item(
    goods_id: int,
    *,
    name: str,
    token_cost: int = 100,
    purchase_limit: int = 10,
    purchased_count: int = 0,
) -> dict:
    return {
        "goods_id": goods_id,
        "item_id": goods_id + 10000,
        "source_order": goods_id,
        "name": name,
        "goods_num": 1,
        "token_cost": token_cost,
        "purchase_limit": purchase_limit,
        "purchased_count": purchased_count,
    }


def _payload(
    *,
    start_date: str,
    end_date: str,
    items: list[dict],
    current_currency: int = 0,
    cumulative_currency: int = 0,
    refresh_status: dict | None = None,
) -> dict:
    return {
        "activity_type": "yunmeng-trial",
        "cross_count": 8,
        "start_date": start_date,
        "end_date": end_date,
        "game_rank_activity_id": 210801,
        "game_shop_base_id": 210001,
        "currency_type": 19,
        "currency_name": "论剑玉",
        "current_currency": current_currency,
        "cumulative_currency": cumulative_currency,
        "captured_at": f"{start_date}T12:00:00+08:00",
        "source_kind": "read_only_runtime_facts",
        "expected_shop_item_count": len(items),
        "shop_items": items,
        "evidence": {
            "refresh_status": refresh_status
            or {
                "currency": "updated",
                "currency_stale": False,
                "shop": "updated",
                "currency_captured_at": f"{start_date}T12:00:00+08:00",
            }
        },
    }


def test_yunmeng_period_uses_runtime_identity_and_real_activity_id() -> None:
    period = _period_from_item(
        {
            "class": "YunmengActivityVO",
            "activityType": 21,
            "activityId": 8210001,
            "name": "云梦试剑",
            "serverCount": 8,
            "startTime": 1786672800000,
            "endTime": 1786716000000,
            "closePanelTime": 1786809539000,
        },
        cross_count=8,
        target_date=date(2026, 8, 14),
        captured_at="2026-08-14T10:02:19+08:00",
        record_id="runtime:8210001",
        packet_id="",
    )

    assert period is not None
    assert period["game_activity_id"] == 8210001
    assert period["start_date"] == "2026-08-14"
    assert period["end_date"] == "2026-08-14"
    assert period["close_panel_date"] == "2026-08-15"


def test_each_yunmeng_period_replans_its_dynamic_goods_without_legacy_state() -> None:
    with _session() as session:
        old_id = upsert_exchange_activity_snapshot(
            session,
            _payload(
                start_date="2026-08-02",
                end_date="2026-08-02",
                items=[_item(1, name="旧期限定物品")],
            ),
        )
        update_exchange_priorities(
            session,
            activity_type="yunmeng-trial",
            activity_id=old_id,
            ordered_goods_ids=[1],
        )
        update_exchange_shop_item_lock(
            session,
            activity_type="yunmeng-trial",
            activity_id=old_id,
            goods_id=1,
            locked=True,
        )

        new_id = upsert_exchange_activity_snapshot(
            session,
            _payload(
                start_date="2026-08-14",
                end_date="2026-08-14",
                items=[
                    _item(1, name="本期同ID新配置"),
                    _item(2, name="本期新增物品"),
                ],
            ),
        )
        detail = list_exchange_activity_snapshot(
            session,
            activity_type="yunmeng-trial",
            activity_id=new_id,
        ).selected_activity

        assert detail is not None
        assert {item.name for item in detail.shop_items} == {
            "本期同ID新配置",
            "本期新增物品",
        }
        assert all(not item.locked for item in detail.shop_items)
        assert sorted(
            item.priority_order for item in detail.shop_items
            if item.priority_order is not None
        ) == [1, 2]
        assert detail.exchange_plan["observed_item_universe_count"] == 3


def test_yunmeng_closing_goods_budget_uses_purchase_balance_history_and_freshness() -> None:
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(
            session,
            _payload(
                start_date="2026-08-14",
                end_date="2026-08-14",
                items=[
                    _item(
                        9,
                        name="本期动态限购物品",
                        token_cost=100,
                        purchase_limit=10,
                        purchased_count=4,
                    )
                ],
                current_currency=300,
                cumulative_currency=500,
            ),
        )
        detail = list_exchange_activity_snapshot(
            session,
            activity_type="yunmeng-trial",
            activity_id=activity_id,
        ).selected_activity

        assert detail is not None
        assert detail.budget_ready is True
        assert detail.exchange_plan["target_budgets"]["收尾道具"] == {
            "target_total_tokens": 1000,
            "target_remaining_tokens": 600,
            "current_currency": 300,
            "cumulative_currency": 500,
            "balance_gap": 300,
            "cumulative_gap": 500,
            "required_new_currency": 500,
        }

        stale_id = upsert_exchange_activity_snapshot(
            session,
            _payload(
                start_date="2026-08-15",
                end_date="2026-08-15",
                items=[_item(10, name="另一动态限购物品")],
                refresh_status={
                    "currency": "retained",
                    "currency_stale": True,
                    "shop": "updated",
                },
            ),
        )
        stale = list_exchange_activity_snapshot(
            session,
            activity_type="yunmeng-trial",
            activity_id=stale_id,
        ).selected_activity

        assert stale is not None
        assert stale.budget_ready is False
        assert "同窗口" in stale.budget_block_reason
