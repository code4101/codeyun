from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity import exchange_event as exchange_event_module
from backend.core.fanxiu.activity.exchange_event import (
    apply_exchange_shop_plan,
    list_exchange_activity_snapshot,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.activity.exchange_shop_planner import (
    DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY,
    ExchangePriorityId,
    PARTNER_ROOT_NAMES,
    build_exchange_shop_plan,
    exchange_shop_priority_policy,
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
    assert plan.ordered_goods_ids == (7, 1, 2, 3, 4, 5, 6, 8)
    assert plan.priority_group_goods_ids[ExchangePriorityId.CURRENT_PRAYER] == (1,)
    assert plan.priority_group_goods_ids[ExchangePriorityId.CARD_MAIL] == (2,)
    assert 2 not in plan.target_goods_ids[ExchangePriorityId.OTHER_DISCOUNT]
    assert plan.locked_reserved_tokens == 100
    assert plan.target_total_tokens[ExchangePriorityId.OTHER_DISCOUNT] == 800
    assert plan.target_remaining_tokens[ExchangePriorityId.OTHER_DISCOUNT] == 800


def test_dao_fragments_keep_same_layer_with_edge_after_spirit() -> None:
    items = [
        _item(1, "道则碎片·淬锋域", source_order=1),
        _item(2, "普通限量物品", source_order=2),
        _item(3, "道则碎片·淬灵域", source_order=3),
    ]

    plan = build_exchange_shop_plan(items, activity_end_date="2026-08-12")

    assert plan.ordered_goods_ids == (3, 1, 2)
    assert plan.priority_group_goods_ids[ExchangePriorityId.DAO_FRAGMENT] == (3, 1)


def test_xianyuan_discounted_daier_is_absolute_front_and_economical_target() -> None:
    items = [
        _item(1, "瑶池玉莲", source_order=1),
        _item(2, "誓约·黛儿", source_order=2, item_id=9023, token_cost=10_000, discount=50),
        _item(3, "誓约·黛儿", source_order=3, item_id=9023, token_cost=20_000),
        _item(4, "道则碎片·淬锋域", source_order=4),
        _item(5, "道则碎片·淬灵域", source_order=5),
    ]

    plan = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-26",
        policy=exchange_shop_priority_policy("xianyuan-duokui"),
    )

    assert plan.ordered_goods_ids == (2, 5, 4, 1, 3)
    assert plan.priority_group_goods_ids[ExchangePriorityId.DAIER] == (2,)
    assert plan.priority_group_goods_ids[ExchangePriorityId.DAO_FRAGMENT] == (5, 4)
    assert plan.priority_group_goods_ids[ExchangePriorityId.CURRENT_PRAYER] == (1,)
    assert plan.priority_group_goods_ids[ExchangePriorityId.ORDERED_GOODS] == (3,)
    assert plan.target_total_tokens[ExchangePriorityId.DAIER] == 10_000
    assert plan.target_remaining_tokens[ExchangePriorityId.DAIER] == 10_000


def test_xianyuan_only_exact_half_price_daier_enters_daier_group() -> None:
    items = [
        _item(1, "誓约·黛儿", source_order=1, item_id=9023, discount=70),
        _item(2, "誓约·黛儿", source_order=2, item_id=9023, discount=50),
        _item(3, "普通道具", source_order=3),
    ]

    plan = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-26",
        policy=exchange_shop_priority_policy("xianyuan-duokui"),
    )

    assert plan.priority_group_goods_ids[ExchangePriorityId.DAIER] == (2,)
    assert plan.ordered_goods_ids == (2, 1, 3)


def test_xianyuan_original_price_daier_falls_into_ordered_goods() -> None:
    items = [
        _item(1, "道则碎片·淬灵域", source_order=1),
        _item(2, "瑶池玉莲", source_order=2),
        _item(3, "誓约·黛儿", source_order=3, item_id=9023, discount=50),
        _item(4, "誓约·黛儿", source_order=4, item_id=9023, discount=None),
        _item(5, "誓约·黛儿", source_order=5, item_id=9023, discount=100),
    ]
    plan = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-26",
        policy=exchange_shop_priority_policy("xianyuan-duokui"),
    )
    assert plan.priority_order_ids[:4] == (
        "黛儿",
        "道则碎片",
        "本周祈愿",
        "下周祈愿",
    )
    assert plan.priority_group_goods_ids[ExchangePriorityId.DAIER] == (3,)
    assert plan.priority_group_goods_ids[ExchangePriorityId.ORDERED_GOODS] == (4, 5)
    assert plan.ordered_goods_ids == (3, 1, 2, 4, 5)


def test_discount_groups_cover_real_goods_not_only_book_suffixes() -> None:
    items = [
        _item(1, "神炼元炁·灭仙", source_order=1, item_id=4400026, discount=50),
        _item(2, "神炼元炁·灭仙", source_order=2, item_id=4400026, discount=70),
        _item(3, "通玄残简·皓月", source_order=3, item_id=3014105, discount=50),
    ]
    plan = build_exchange_shop_plan(items, activity_end_date="2026-08-26")
    assert plan.priority_group_goods_ids[ExchangePriorityId.LOWEST_DISCOUNT] == (1, 3)
    assert plan.priority_group_goods_ids[ExchangePriorityId.OTHER_DISCOUNT] == (2,)


def test_xianyuan_persisted_plan_uses_daier_as_economical_target() -> None:
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(
            session,
            {
                "activity_type": "xianyuan-duokui",
                "cross_count": 8,
                "start_date": "2026-08-26",
                "end_date": "2026-08-26",
                "current_currency": 0,
                "cumulative_currency": 0,
                "resource_strategy": {
                    "常规目标": "尽量完成到其他折扣",
                    "条件目标": "有条件完成到收尾道具",
                },
                "expected_shop_item_count": 3,
                "shop_items": [
                    {"goods_id": 1, "item_id": 7020014, "source_order": 1, "name": "瑶池玉莲", "token_cost": 100, "purchase_limit": 100},
                    {"goods_id": 2, "item_id": 9023, "source_order": 2, "name": "誓约·黛儿", "token_cost": 10_000, "purchase_limit": 1, "discount": 50},
                    {"goods_id": 3, "item_id": 9023, "source_order": 3, "name": "誓约·黛儿", "token_cost": 20_000, "purchase_limit": 3},
                ],
            },
        )
        stored = session.get(FanxiuExchangeActivity, activity_id)

    assert stored is not None
    plan = stored.evidence["exchange_plan"]
    assert plan["ordered_goods_ids"] == [2, 1, 3]
    assert plan["priority_group_goods_ids"]["黛儿"] == [2]
    assert plan["priority_group_goods_ids"]["顺序道具"] == [3]
    assert plan["economical_target_id"] == "黛儿"
    assert plan["economical_budget"]["target_total_tokens"] == 10_000
    assert plan["target_budgets"]["其他折扣"]["target_total_tokens"] == 20_000
    assert stored.resource_strategy["常规目标"].startswith("只生产足够兑换5折誓约·黛儿")
    assert stored.resource_strategy["条件目标"].startswith("仅用自然多出的兑币")


def test_shop_closing_after_next_monday_0100_reserves_next_week_then_card_mail() -> None:
    items = [
        _item(1, "珍品饲灵丸"),
        _item(2, "洗灵奇石"),
        _item(3, "瑶池玉莲"),
        _item(4, "淬体精魄"),
    ]

    plan = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-16",
        shop_close_at="2026-08-17T01:00:01+08:00",
    )

    assert plan.next_prayer_cutoff_at == "2026-08-17T01:00:00+08:00"
    assert plan.activity_page_closes_after_next_prayer_cutoff is True
    assert plan.next_prayer_resource == "洗灵奇石"
    assert plan.card_mail_resource == "瑶池玉莲"
    assert plan.ordered_goods_ids[:3] == (1, 2, 3)
    assert plan.locked_goods_ids == (2, 3)
    assert plan.locked_reserved_tokens == 200


def test_next_week_reservation_uses_strict_monday_0100_close_boundary() -> None:
    items = [
        _item(1, "珍品饲灵丸"),
        _item(2, "洗灵奇石"),
        _item(3, "瑶池玉莲"),
    ]

    sunday_before_cutoff = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-16",
        shop_close_at="2026-08-17T00:59:59+08:00",
    )
    sunday_without_exact_close = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-16",
    )
    sunday_at_cutoff = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-16",
        shop_close_at="2026-08-17T01:00:00+08:00",
    )
    saturday_after_cutoff = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-15",
        planning_date="2026-08-15",
        shop_close_at="2026-08-17T01:00:01+08:00",
    )

    assert sunday_before_cutoff.next_prayer_resource is None
    assert sunday_without_exact_close.next_prayer_resource is None
    assert sunday_without_exact_close.activity_page_close_at is None
    assert sunday_at_cutoff.next_prayer_resource is None
    assert sunday_at_cutoff.activity_page_closes_after_next_prayer_cutoff is False
    assert saturday_after_cutoff.next_prayer_resource == "洗灵奇石"
    assert saturday_after_cutoff.activity_page_closes_after_next_prayer_cutoff is True


def test_persisted_plan_consumes_exact_runtime_close_panel_time() -> None:
    close_ms = int(
        datetime.fromisoformat("2026-08-17T01:00:01+08:00").timestamp() * 1000
    )
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(
            session,
            {
                "activity_type": "beast-abyss",
                "cross_count": 4,
                "start_date": "2026-08-15",
                "end_date": "2026-08-15",
                "evidence": {"period_close_panel_time": close_ms},
                "expected_shop_item_count": 3,
                "shop_items": [
                    {"goods_id": 1, "item_id": 1, "source_order": 1, "name": "珍品饲灵丸", "token_cost": 100, "purchase_limit": 1},
                    {"goods_id": 2, "item_id": 2, "source_order": 2, "name": "洗灵奇石", "token_cost": 100, "purchase_limit": 1},
                    {"goods_id": 3, "item_id": 3, "source_order": 3, "name": "瑶池玉莲", "token_cost": 100, "purchase_limit": 1},
                ],
            },
        )
        stored = session.get(FanxiuExchangeActivity, activity_id)

    assert stored is not None
    plan = stored.evidence["exchange_plan"]
    assert plan["next_prayer_cutoff_at"] == "2026-08-17T01:00:00+08:00"
    assert plan["activity_page_closes_after_next_prayer_cutoff"] is True
    assert plan["next_prayer_resource"] == "洗灵奇石"
    assert stored.resource_strategy["跨周祈愿预留"] == "洗灵奇石"
    assert "周日顺延预留" not in stored.resource_strategy


def test_closing_goods_completion_excludes_locked_goods_but_requires_funded_reserve() -> None:
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
        assert unfunded.evidence["exchange_plan"]["closing_goods_items_complete"] is True
        assert unfunded.evidence["exchange_plan"]["closing_goods_complete"] is False
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
    assert plan_evidence["closing_goods_complete"] is True
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
    assert plan.priority_group_goods_ids[ExchangePriorityId.LOWEST_DISCOUNT] == (2, 3)
    assert plan.priority_group_goods_ids[ExchangePriorityId.OTHER_DISCOUNT] == (1, 4)
    assert plan.ordered_goods_ids == (2, 3, 1, 4, 5)
    assert plan.priority_group_goods_ids[ExchangePriorityId.ORDERED_GOODS] == (5,)


def test_ordered_goods_keep_page_order_and_closing_goods_have_fixed_tail_order() -> None:
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

    assert plan.priority_group_goods_ids[ExchangePriorityId.ORDERED_GOODS] == (1, 2, 3)
    assert plan.priority_group_goods_ids[ExchangePriorityId.CLOSING_GOODS] == (5, 4)
    assert plan.priority_group_goods_ids[ExchangePriorityId.OVERFLOW_PILL] == (7, 8)
    assert plan.priority_group_goods_ids[ExchangePriorityId.NOT_NEEDED] == (6,)
    assert plan.priority_order_ids[-1] == "不需要领"
    assert plan.ordered_goods_ids == (1, 2, 3, 5, 4, 7, 8)

    tuned = build_exchange_shop_plan(
        items,
        activity_end_date="2026-08-12",
        policy=replace(
            DEFAULT_EXCHANGE_SHOP_PRIORITY_POLICY,
            closing_goods_names=("灵根补全自选匣", "授业玉简"),
        ),
    )
    assert tuned.priority_group_goods_ids[ExchangePriorityId.CLOSING_GOODS] == (4, 5)


def test_get_snapshot_rematerializes_old_plan_schema_without_touching_game() -> None:
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(
            session,
            {
                "activity_type": "xianyuan-duokui",
                "cross_count": 8,
                "start_date": "2026-08-26",
                "end_date": "2026-08-26",
                "expected_shop_item_count": 2,
                "shop_items": [
                    {"goods_id": 1, "item_id": 1, "source_order": 1, "name": "玄血丹·珍", "token_cost": 60, "purchase_limit": -1},
                    {"goods_id": 2, "item_id": 2, "source_order": 2, "name": "玄血丹·尚", "token_cost": 10, "purchase_limit": -1},
                ],
            },
        )
        activity = session.get(FanxiuExchangeActivity, activity_id)
        assert activity is not None
        evidence = dict(activity.evidence or {})
        stale_plan = dict(evidence["exchange_plan"])
        stale_plan["schema"] = 5
        stale_plan["priority_order_ids"] = stale_plan["priority_order_ids"][:-1]
        stale_plan["priority_group_goods_ids"].pop("不需要领", None)
        evidence["exchange_plan"] = stale_plan
        activity.evidence = evidence
        session.add(activity)
        session.commit()

        snapshot = list_exchange_activity_snapshot(
            session,
            activity_type="xianyuan-duokui",
            activity_id=activity_id,
        )

    assert snapshot.selected_activity is not None
    plan = snapshot.selected_activity.exchange_plan
    assert plan["schema"] == 9
    assert plan["priority_order_ids"][-1] == "不需要领"
    assert plan["priority_group_goods_ids"]["不需要领"] == [1, 2]
    assert plan["ordered_goods_ids"] == []


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
    assert plan["closing_goods_items_complete"] is True
    assert plan["locked_reserve_funded"] is True
    assert plan["budget_ready"] is False
    assert plan["closing_goods_complete"] is False
    assert plan["card_mail_close_action"] == "redeem_during_grace_period"
    assert detail.currency_fact_fresh is False
    assert detail.shop_fact_fresh is True
    assert detail.budget_ready is False
    assert detail.budget_block_reason == "钱包 amount/history 与购买进度不是同窗口最新 Runtime 事实"
    assert detail.exchange_plan["target_budgets"]["收尾道具"]["required_new_currency"] == 0


def test_closing_goods_funding_reports_only_additional_tokens_to_earn() -> None:
    base = build_exchange_shop_plan(
        [_item(1, "普通限量物品", token_cost=100, purchase_limit=1)],
        activity_end_date="2026-08-23",
        planning_date="2026-08-21",
    )
    plan = replace(
        base,
        target_remaining_tokens={
            **base.target_remaining_tokens,
            str(ExchangePriorityId.CLOSING_GOODS): 137_500,
        },
    )

    status = exchange_shop_funding_status(
        plan,
        current_tokens=8_560,
        target_id=ExchangePriorityId.CLOSING_GOODS,
    )

    assert status.remaining_tokens == 137_500
    assert status.additional_tokens_required == 128_940
    assert status.funded is False
