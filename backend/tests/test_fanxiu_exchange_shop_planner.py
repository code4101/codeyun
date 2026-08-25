from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity import exchange_event as exchange_event_module
from backend.core.fanxiu.activity.exchange_event import (
    apply_exchange_shop_plan,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.activity.exchange_shop_planner import (
    DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY,
    PARTNER_ROOT_NAMES,
    build_exchange_shop_plan,
    exchange_shop_funding_status,
)
from backend.models import FanxiuExchangeActivity


@dataclass
class Item:
    goods_id: int
    item_id: int
    source_order: int
    name: str
    purchase_limit: int = 1
    purchased_count: int = 0
    token_cost: int = 100
    discount: int | None = None


def _item(goods_id: int, name: str, **kwargs) -> Item:
    return Item(
        goods_id=goods_id,
        item_id=int(kwargs.pop("item_id", goods_id)),
        source_order=int(kwargs.pop("source_order", goods_id)),
        name=name,
        **kwargs,
    )


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_wednesday_plan_reserves_one_card_mail_resource() -> None:
    items = [
        _item(1, "珍品饲灵丸"),
        _item(2, "瑶池玉莲"),
        _item(3, "洗灵奇石"),
        _item(4, "淬体精魄"),
        _item(5, "炼丹灵草匣"),
        _item(6, "装备玄铁宝匣"),
        _item(7, "道则碎片·淬灵域"),
        _item(8, "息壤土·绝品", item_id=29605),
    ]

    plan = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-12",
    )

    assert plan.current_prayer_cycle == "灵兽"
    assert plan.current_prayer_resource == "珍品饲灵丸"
    assert plan.next_prayer_resource is None
    assert plan.card_mail_resource == "瑶池玉莲"
    assert plan.locked_goods_ids == (2,)
    assert plan.ordered_goods_ids == (1, 2, 3, 4, 5, 6, 7, 8)
    assert 2 not in plan.stage8_goods_ids
    assert plan.locked_reserved_tokens == 100
    assert plan.stage8_total_tokens == 800
    assert plan.stage8_remaining_tokens == 800


def test_sunday_plan_reserves_next_week_then_card_mail() -> None:
    items = [
        _item(1, "珍品饲灵丸"),
        _item(2, "洗灵奇石"),
        _item(3, "瑶池玉莲"),
        _item(4, "淬体精魄"),
    ]

    plan = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-16",
    )

    assert plan.activity_ends_on_sunday is True
    assert plan.next_prayer_resource == "洗灵奇石"
    assert plan.card_mail_resource == "瑶池玉莲"
    assert plan.ordered_goods_ids[:3] == (1, 2, 3)
    assert plan.locked_goods_ids == (2, 3)
    assert plan.locked_reserved_tokens == 200


def test_stage9_completion_excludes_locked_goods_but_requires_funded_reserve() -> None:
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(
            session,
            {
                "activity_type": "beast-abyss",
                "cross_count": 4,
                "start_date": "2026-08-11",
                "end_date": "2026-08-12",
                "current_currency": 0,
                "cumulative_currency": 200,
                "expected_shop_item_count": 3,
                "shop_items": [
                    {"goods_id": 1, "item_id": 8022001, "source_order": 1, "name": "珍品饲灵丸", "token_cost": 100, "purchase_limit": 1, "purchased_count": 1},
                    {"goods_id": 2, "item_id": 7020014, "source_order": 2, "name": "瑶池玉莲", "token_cost": 100, "purchase_limit": 1, "purchased_count": 0},
                    {"goods_id": 3, "item_id": 999, "source_order": 3, "name": "普通限量物品", "token_cost": 100, "purchase_limit": 1, "purchased_count": 1},
                ],
            },
        )
        unfunded = session.get(FanxiuExchangeActivity, activity_id)
        assert unfunded is not None
        assert unfunded.evidence["exchange_plan"]["stage9_items_complete"] is True
        assert unfunded.evidence["exchange_plan"]["stage9_complete"] is False
        assert unfunded.evidence["exchange_plan"]["card_mail_close_action"] == "redeem_during_grace_period"

        unfunded.current_currency = 100
        unfunded.cumulative_currency = 300
        session.add(unfunded)
        session.commit()
        apply_exchange_shop_plan(
            session,
            activity_type="beast-abyss",
            activity_id=activity_id,
        )
        funded = session.get(FanxiuExchangeActivity, activity_id)

    assert funded is not None
    plan_evidence = funded.evidence["exchange_plan"]
    assert plan_evidence["locked_reserved_tokens"] == 100
    assert plan_evidence["locked_reserve_funded"] is True
    assert plan_evidence["stage9_complete"] is True
    assert plan_evidence["card_mail_close_action"] == "leave_for_mail"


def test_prayer_priority_is_recomputed_from_each_activity_end_week() -> None:
    items = [
        _item(1, "珍品饲灵丸"),
        _item(2, "洗灵奇石"),
        _item(3, "瑶池玉莲"),
        _item(4, "淬体精魄"),
        _item(5, "炼丹灵草匣"),
    ]

    beast_week = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-12",
    )
    cleanse_week = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-19",
    )
    flower_week = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-26",
    )

    assert (beast_week.current_prayer_cycle, beast_week.ordered_goods_ids[:2]) == (
        "灵兽",
        (1, 3),
    )
    assert (cleanse_week.current_prayer_cycle, cleanse_week.ordered_goods_ids[:2]) == (
        "洗灵",
        (2, 3),
    )
    assert (flower_week.current_prayer_cycle, flower_week.ordered_goods_ids[:2]) == (
        "仙花",
        (3, 2),
    )


def test_cross_week_activity_uses_live_planning_date_not_end_week() -> None:
    items = [
        _item(1, "珍品饲灵丸"),
        _item(2, "洗灵奇石"),
        _item(3, "瑶池玉莲"),
        _item(4, "淬体精魄"),
    ]

    sunday = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-18",
        planning_date="2026-08-16",
    )
    monday = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-18",
        planning_date="2026-08-17",
    )

    assert sunday.planning_date == "2026-08-16"
    assert sunday.current_prayer_cycle == "灵兽"
    assert sunday.ordered_goods_ids[0] == 1
    assert monday.planning_date == "2026-08-17"
    assert monday.current_prayer_cycle == "洗灵"
    assert monday.ordered_goods_ids[0] == 2


def test_all_five_partner_root_items_are_a_bounded_family() -> None:
    items = [
        _item(index, name, item_id=29600 + index)
        for index, name in enumerate(PARTNER_ROOT_NAMES, start=1)
    ] + [_item(20, "功法残篇·绝品", item_id=3020002)]

    plan = build_exchange_shop_plan(items, activity_end_date="2026-08-12")

    assert plan.ordered_goods_ids[:5] == (1, 2, 3, 4, 5)
    assert plan.ordered_goods_ids[5:] == (20,)


def test_discounted_books_finish_each_books_best_row_before_second_rows() -> None:
    items = [
        _item(1, "甲诀残页", item_id=100, source_order=1, discount=70),
        _item(2, "乙诀残页", item_id=200, source_order=2, discount=50),
        _item(3, "甲诀残页", item_id=100, source_order=3, discount=50),
        _item(4, "乙诀残页", item_id=200, source_order=4, discount=70),
        _item(5, "甲诀残页", item_id=100, source_order=5, discount=100),
    ]

    plan = build_exchange_shop_plan(items, activity_end_date="2026-08-12")

    # Best rows: 甲5折、乙5折；then remaining discounted 7折 rows.
    assert plan.discounted_book_goods_ids == (2, 3, 1, 4)
    assert plan.ordered_goods_ids == (2, 3, 1, 4, 5)
    assert plan.stage9_goods_ids == (5,)


def test_stage9_has_declarative_front_and_tail_tuning_points() -> None:
    items = [
        _item(1, "普通甲", source_order=1),
        _item(2, "兽渊废料匣·壹", source_order=2),
        _item(3, "普通乙", source_order=3),
        _item(4, "灵根补全自选匣", source_order=4),
        _item(5, "授业玉简", source_order=5),
        _item(6, "玄血丹·珍", source_order=6, purchase_limit=-1),
        _item(7, "玄灵丹·珍", source_order=7, purchase_limit=-1),
        _item(8, "玄灵丹·尚", source_order=8, purchase_limit=-1),
    ]

    plan = build_exchange_shop_plan(items, activity_end_date="2026-08-12")

    assert plan.stage9_goods_ids == (2, 1, 3, 5, 4)
    assert plan.ordered_goods_ids == (2, 1, 3, 5, 4, 7, 8)

    tuned = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-12",
        policy=replace(
            DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY,
            stage9_tail_names=("灵根补全自选匣", "授业玉简"),
        ),
    )
    assert tuned.stage9_goods_ids[-2:] == (4, 5)


def test_persisted_plan_expands_observed_universe_and_fails_closed_on_unknown_unlimited() -> None:
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(
            session,
            {
                "activity_type": "beast-abyss",
                "cross_count": 4,
                "start_date": "2026-08-11",
                "end_date": "2026-08-12",
                "expected_shop_item_count": 3,
                "shop_items": [
                    {"goods_id": 1, "item_id": 8022001, "source_order": 1, "name": "珍品饲灵丸", "token_cost": 100, "purchase_limit": 100},
                    {"goods_id": 2, "item_id": 7020014, "source_order": 2, "name": "瑶池玉莲", "token_cost": 100, "purchase_limit": 100},
                    {"goods_id": 3, "item_id": 999, "source_order": 3, "name": "未知不限量丹", "token_cost": 1, "purchase_limit": -1},
                ],
            },
        )
        upsert_exchange_activity_snapshot(
            session,
            {
                "activity_type": "magic-invasion",
                "cross_count": 8,
                "start_date": "2026-08-01",
                "end_date": "2026-08-02",
                "expected_shop_item_count": 1,
                "shop_items": [
                    {"goods_id": 10, "item_id": 14000002, "source_order": 1, "name": "洗灵奇石", "token_cost": 100, "purchase_limit": 100},
                ],
            },
        )

        detail = apply_exchange_shop_plan(
            session,
            activity_type="beast-abyss",
            activity_id=activity_id,
        )
        stored = session.get(FanxiuExchangeActivity, activity_id)

    assert [(row.goods_id, row.priority_order, row.locked) for row in detail.shop_items] == [
        (1, 1, False),
        (2, 2, True),
        (3, None, False),
    ]
    assert stored is not None
    assert stored.evidence["exchange_plan"]["observed_item_universe_count"] == 4
    assert stored.evidence["exchange_plan"]["card_mail_close_action"] == "redeem_during_grace_period"


def test_complete_shop_refresh_keeps_live_week_when_end_moves_next_week(monkeypatch) -> None:
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 12)

    monkeypatch.setattr(exchange_event_module, "date", FixedDate)
    with _session() as session:
        payload = {
            "activity_type": "beast-abyss",
            "cross_count": 4,
            "start_date": "2026-08-11",
            "end_date": "2026-08-12",
            "resource_strategy": {"活动方式": "探索"},
            "expected_shop_item_count": 2,
            "shop_items": [
                {"goods_id": 1, "item_id": 8022001, "source_order": 1, "name": "珍品饲灵丸", "token_cost": 100, "purchase_limit": 100},
                {"goods_id": 2, "item_id": 7020014, "source_order": 2, "name": "瑶池玉莲", "token_cost": 100, "purchase_limit": 100},
            ],
        }
        activity_id = upsert_exchange_activity_snapshot(session, payload)
        first = session.get(FanxiuExchangeActivity, activity_id)
        assert first is not None
        assert first.resource_strategy["本周祈愿"].startswith("灵兽")
        assert first.evidence["exchange_plan"]["locked_goods_ids"] == [2]

        payload["end_date"] = "2026-08-19"
        payload["resource_strategy"] = {"活动方式": "探索-刷新"}
        refreshed_id = upsert_exchange_activity_snapshot(session, payload)
        refreshed = session.get(FanxiuExchangeActivity, refreshed_id)

    assert refreshed is not None
    assert refreshed.resource_strategy["活动方式"] == "探索-刷新"
    assert refreshed.resource_strategy["本周祈愿"].startswith("灵兽")
    assert refreshed.evidence["exchange_plan"]["current_prayer_cycle"] == "灵兽"
    assert refreshed.evidence["exchange_plan"]["planning_date"] == "2026-08-12"


def test_runtime_plan_fails_closed_when_currency_fact_is_stale() -> None:
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(
            session,
            {
                "activity_type": "beast-abyss",
                "cross_count": 4,
                "start_date": "2026-08-11",
                "end_date": "2026-08-12",
                "current_currency": 100,
                "cumulative_currency": 300,
                "evidence": {
                    "refresh_status": {
                        "currency": "retained",
                        "currency_stale": True,
                        "shop": "updated",
                    }
                },
                "expected_shop_item_count": 3,
                "shop_items": [
                    {"goods_id": 1, "item_id": 8022001, "source_order": 1, "name": "珍品饲灵丸", "token_cost": 100, "purchase_limit": 1, "purchased_count": 1},
                    {"goods_id": 2, "item_id": 7020014, "source_order": 2, "name": "瑶池玉莲", "token_cost": 100, "purchase_limit": 1, "purchased_count": 0},
                    {"goods_id": 3, "item_id": 999, "source_order": 3, "name": "普通限量物品", "token_cost": 100, "purchase_limit": 1, "purchased_count": 1},
                ],
            },
        )
        stored = session.get(FanxiuExchangeActivity, activity_id)
        detail = apply_exchange_shop_plan(
            session,
            activity_type="beast-abyss",
            activity_id=activity_id,
        )

    assert stored is not None
    plan = stored.evidence["exchange_plan"]
    assert plan["stage9_items_complete"] is True
    assert plan["locked_reserve_funded"] is True
    assert plan["budget_ready"] is False
    assert plan["stage9_complete"] is False
    assert plan["card_mail_close_action"] == "redeem_during_grace_period"
    assert detail.currency_fact_fresh is False
    assert detail.shop_fact_fresh is True
    assert detail.budget_ready is False
    assert detail.budget_block_reason == "钱包 amount/history 与购买进度不是同窗口最新 Runtime 事实"
    assert detail.exchange_plan["stage9_budget"]["required_new_currency"] == 0


def test_stage9_funding_reports_only_additional_tokens_to_earn() -> None:
    base = build_exchange_shop_plan(
        [_item(1, "普通限量物品", token_cost=100, purchase_limit=1)],
        activity_end_date="2026-08-23",
        planning_date="2026-08-21",
    )
    plan = replace(base, stage9_remaining_tokens=137_500)

    status = exchange_shop_funding_status(plan, current_tokens=8_560)

    assert status.remaining_tokens == 137_500
    assert status.additional_tokens_required == 128_940
    assert status.funded is False
