"""Pure authorization/allocation contract; no simulated game interaction."""
from datetime import date
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks.exchange_tail_planning import plan_exchange_tail_purchases


def test_finite_scope_retains_reserve_and_reconciles_without_unlimited_spending():
    locked = SimpleNamespace(goods_id=1, name='次期祈愿', purchase_limit=1,
                             purchased_count=0, token_cost=100, source_order=0)
    finite = SimpleNamespace(goods_id=2, name='有限档', purchase_limit=1,
                             purchased_count=0, token_cost=100, source_order=1)
    unlimited = SimpleNamespace(goods_id=3, name='无限档', purchase_limit=-1,
                                purchased_count=0, token_cost=10, source_order=2)
    detail = SimpleNamespace(current_currency=250, shop_items=[locked, finite, unlimited],
                             exchange_plan=dict(budget_ready=True, ordered_goods_ids=[1, 2, 3],
                                                locked_goods_ids=[1], next_prayer_resource='次期祈愿'))
    args = dict(run_date=date(2026, 10, 6))
    legacy, _, _ = plan_exchange_tail_purchases(detail, **args)
    assert [(p.goods_id, p.quantity) for p in legacy] == [(2, 1), (3, 5)]
    purchases, retained, budget = plan_exchange_tail_purchases(detail, **args, finite_only=True)
    assert [(p.goods_id, p.quantity) for p in purchases] == [(2, 1)]
    assert retained == {1}
    assert budget['reserved_tokens'] == 100
    assert budget['planned_spend_tokens'] == 100
    assert budget['planned_remaining_tokens'] == 150
    finite.purchased_count = 1
    detail.current_currency = 150
    remaining, retained, budget = plan_exchange_tail_purchases(detail, **args, finite_only=True)
    assert remaining == [] and retained == {1}
    assert budget['complete'] is True and budget['planned_remaining_tokens'] == 150


def test_reserve_never_exceeds_the_balance_that_exists():
    locked = SimpleNamespace(goods_id=1, name='次期祈愿', purchase_limit=1,
                             purchased_count=0, token_cost=4000, source_order=0)
    detail = SimpleNamespace(current_currency=0, shop_items=[locked],
                             exchange_plan=dict(budget_ready=True, ordered_goods_ids=[1],
                                                locked_goods_ids=[1], next_prayer_resource='次期祈愿'))
    purchases, retained, budget = plan_exchange_tail_purchases(detail, run_date=date(2026, 10, 6))
    assert purchases == [] and retained == {1}
    assert budget['planned_remaining_tokens'] == 0
    assert budget['planned_spend_tokens'] == 0
