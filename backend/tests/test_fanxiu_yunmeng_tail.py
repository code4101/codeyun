from datetime import date, timedelta
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks import yunmeng_tail
from backend.core.fanxiu.data_annotation.tasks.yunmeng_tail import (
    _ocr_contains_amount,
    plan_yunmeng_tail_physical_actions,
    plan_yunmeng_tail_purchases,
    refresh_yunmeng_final_rankings,
    yunmeng_quantity_clicks,
)


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stopped:
            return stopped.value


def test_expired_legacy_scheduler_instance_retires_without_gui_actions(
    monkeypatch,
) -> None:
    today = date.today()
    activity = SimpleNamespace(
        id="old-yunmeng",
        end_date=(today - timedelta(days=2)).isoformat(),
        evidence={"period_close_panel_date": (today - timedelta(days=1)).isoformat()},
    )
    persisted = []

    class QueryResult:
        def first(self):
            return activity

    class Session:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def exec(self, _query):
            return QueryResult()

    class Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            raise AssertionError("expired legacy instance must not create GUI runtime")

        def _persist_scheduler_task_next_time(self, task_id, next_time):
            persisted.append((task_id, next_time))

    monkeypatch.setattr("sqlmodel.Session", Session)
    result = _drain(yunmeng_tail.execute_yunmeng_tail_job(
        Runner(),
        {"scheduler_task_id": "yunmeng-tail"},
        {},
        SimpleNamespace(is_set=lambda: False),
    ))

    assert result["result"] == "success"
    assert "已退役" in result["message"]
    assert persisted == [("yunmeng-tail", None)]


def test_bounded_price_ocr_accepts_adjacent_number_concatenation() -> None:
    assert _ocr_contains_amount([500010000, 737826], "价格：500010000拥有:737826", 5000)
    assert _ocr_contains_amount([500010000, 737826], "价格：500010000拥有:737826", 10000)
    assert not _ocr_contains_amount([600010000], "价格：600010000", 5000)


def _item(
    goods_id: int,
    name: str,
    order: int,
    cost: int,
    limit: int = 1,
    purchased: int = 0,
):
    return SimpleNamespace(
        goods_id=goods_id,
        name=name,
        source_order=order,
        token_cost=cost,
        purchase_limit=limit,
        purchased_count=purchased,
    )


def _detail(*, closing_goods_gap: int, next_prayer: str | None = None):
    items = [
        _item(1, "下周祈愿", 1, 10),
        _item(2, "普通锁定", 2, 20),
        _item(3, "优先道具", 3, 30),
    ]
    locked = [2] + ([1] if next_prayer else [])
    return SimpleNamespace(
        current_currency=100,
        shop_items=items,
        exchange_plan={
            "budget_ready": True,
            "ordered_goods_ids": [3, 1, 2],
            "locked_goods_ids": locked,
            "target_budgets": {
                "收尾道具": {"required_new_currency": closing_goods_gap}
            },
            "card_mail_resource": "普通锁定",
            "next_prayer_resource": next_prayer,
        },
    )


def test_closing_goods_complete_keeps_ordinary_mail_lock() -> None:
    purchases, retained, facts = plan_yunmeng_tail_purchases(
        _detail(closing_goods_gap=0), run_date=date(2026, 8, 15)
    )

    assert retained == {2}
    assert [row.goods_id for row in purchases] == [3, 1]
    assert facts["closing_goods_reached"] is True


def test_incomplete_closing_goods_unlocks_ordinary_and_monday_prayer_locks() -> None:
    purchases, retained, facts = plan_yunmeng_tail_purchases(
        _detail(closing_goods_gap=1, next_prayer="下周祈愿"),
        run_date=date(2026, 8, 17),
    )

    assert retained == set()
    assert [row.goods_id for row in purchases] == [3, 1, 2]
    assert facts["closing_goods_reached"] is False


def test_buying_to_limit_uses_plus_ten_cap_instead_of_single_steps() -> None:
    assert yunmeng_quantity_clicks(25, buying_to_cap=True) == (3, 0)
    assert yunmeng_quantity_clicks(25, buying_to_cap=False) == (2, 4)


def test_theory_priority_allocates_budget_but_physical_order_executes_once() -> None:
    # B has higher theoretical priority although A appears first.  B receives
    # its full budget first; A gets only the exact remainder and stays in row 1.
    items = [
        _item(1, "A", 1, 1, limit=100),
        _item(2, "B", 2, 10, limit=8),
    ]
    detail = SimpleNamespace(
        current_currency=100,
        shop_items=items,
        exchange_plan={
            "budget_ready": True,
            "ordered_goods_ids": [2, 1],
            "locked_goods_ids": [],
            "target_budgets": {"收尾道具": {"required_new_currency": 1}},
            "card_mail_resource": "",
            "next_prayer_resource": "",
        },
    )

    purchases, _retained, facts = plan_yunmeng_tail_purchases(
        detail, run_date=date(2026, 8, 15)
    )
    actions = plan_yunmeng_tail_physical_actions(items, purchases)

    assert [(row.goods_id, row.quantity) for row in purchases] == [(2, 8), (1, 20)]
    assert [
        (row.goods_id, row.quantity, row.slot, row.clears_row)
        for row in actions
    ] == [
        (1, 20, 1, False),
        (2, 8, 2, True),
    ]
    assert facts["planned_remaining_tokens"] == 0


def test_skipped_rows_keep_slots_and_late_target_only_scrolls_down() -> None:
    items = [
        _item(index, f"skip-{index}", index, 100, limit=1)
        for index in range(1, 7)
    ] + [_item(7, "target", 7, 10, limit=1)]
    purchase = [
        SimpleNamespace(
            goods_id=7,
            source_order=7,
            name="target",
            quantity=1,
            unit_price=10,
        )
    ]

    actions = plan_yunmeng_tail_physical_actions(items, purchase)

    assert len(actions) == 1
    assert actions[0].goods_id == 7
    assert actions[0].slot == 5
    assert actions[0].scroll_rows == 2


def test_cleared_front_row_reuses_same_physical_slot() -> None:
    items = [
        _item(1, "first", 1, 10, limit=1),
        _item(2, "second", 2, 20, limit=1),
    ]
    purchases = [
        SimpleNamespace(
            goods_id=1,
            source_order=1,
            name="first",
            quantity=1,
            unit_price=10,
        ),
        SimpleNamespace(
            goods_id=2,
            source_order=2,
            name="second",
            quantity=1,
            unit_price=20,
        ),
    ]

    actions = plan_yunmeng_tail_physical_actions(items, purchases)

    assert [(row.goods_id, row.slot, row.clears_row) for row in actions] == [
        (1, 1, True),
        (2, 1, True),
    ]


def test_terminal_reserved_wallet_does_not_rebuy_unlimited_rows() -> None:
    items = [
        _item(1, "卡邮件资源", 1, 100, limit=100),
        _item(2, "无限甲", 2, 60, limit=-1),
        _item(3, "无限乙", 3, 10, limit=-1),
    ]
    detail = SimpleNamespace(
        current_currency=10006,
        shop_items=items,
        exchange_plan={
            "budget_ready": True,
            "ordered_goods_ids": [2, 3, 1],
            "locked_goods_ids": [1],
            "target_budgets": {"收尾道具": {"required_new_currency": 0}},
            "card_mail_resource": "卡邮件资源",
            "next_prayer_resource": "",
        },
    )

    purchases, retained, facts = plan_yunmeng_tail_purchases(
        detail, run_date=date(2026, 8, 15)
    )
    actions = plan_yunmeng_tail_physical_actions(items, purchases)

    assert purchases == []
    assert actions == []
    assert retained == {1}
    assert facts["reserved_tokens"] == 10000
    assert facts["planned_remaining_tokens"] == 10006
    assert facts["complete"] is True


def test_completion_floor_is_derived_for_varying_balances_and_lock_remainders() -> None:
    for locked_purchased in (0, 37, 99):
        reserved = (100 - locked_purchased) * 100
        for extra in range(0, 301):
            items = [
                _item(
                    1,
                    "动态卡邮件资源",
                    1,
                    100,
                    limit=100,
                    purchased=locked_purchased,
                ),
                _item(2, "高优先无限资源", 2, 60, limit=-1),
                _item(3, "低优先无限资源", 3, 10, limit=-1),
            ]
            detail = SimpleNamespace(
                current_currency=reserved + extra,
                shop_items=items,
                exchange_plan={
                    "budget_ready": True,
                    "ordered_goods_ids": [2, 3, 1],
                    "locked_goods_ids": [1],
                    "target_budgets": {"收尾道具": {"required_new_currency": 0}},
                    "card_mail_resource": "动态卡邮件资源",
                    "next_prayer_resource": "",
                },
            )

            purchases, retained, facts = plan_yunmeng_tail_purchases(
                detail, run_date=date(2026, 8, 15)
            )
            actions = plan_yunmeng_tail_physical_actions(items, purchases)
            spent = sum(row.quantity * row.unit_price for row in purchases)

            assert retained == {1}
            assert facts["reserved_tokens"] == reserved
            assert spent + facts["planned_remaining_tokens"] == reserved + extra
            assert reserved <= facts["planned_remaining_tokens"] < reserved + 10
            assert sum(row.quantity * row.unit_price for row in actions) == spent
            assert facts["complete"] is (extra < 10)


def test_final_ranking_step_refreshes_both_tabs_before_storing(monkeypatch) -> None:
    events: list[tuple[str, object]] = []

    class Runtime:
        def click_shape_center(self, scene: int, shape: str) -> None:
            events.append(("click", (scene, shape)))

        def wait_action_settle(self, seconds: float):
            events.append(("settle", seconds))
            yield None

    expected = {
        "activity_id": "activity-1",
        "personal_count": 100,
        "plane_count": 50,
        "captured_at": "2026-08-15T16:00:00+08:00",
    }

    def store(activity_id: str):
        events.append(("store", activity_id))
        return expected

    monkeypatch.setattr(yunmeng_tail, "store_yunmeng_final_rankings", store)
    generator = refresh_yunmeng_final_rankings(Runtime(), activity_id="activity-1")
    while True:
        try:
            next(generator)
        except StopIteration as stopped:
            result = stopped.value
            break

    assert result == expected
    assert events == [
        ("click", (565, "个人")),
        ("settle", 1.0),
        ("click", (565, "位面")),
        ("settle", 1.0),
        ("store", "activity-1"),
    ]
