from __future__ import annotations

import pytest

from backend.core.fanxiu.resources.sacred_exchange_planner import (
    plan_sacred_exchange_stock,
)


def _snapshot(*, limit_times: int = -1, bought: int = 0) -> dict:
    return {
        "complete": True,
        "rows": [{
            "entries": [{
                "item_id": 9001,
                "name": "天眼符",
                "goods_num": 100,
                "cost_item_id": 8001,
                "cost_num": 2,
                "limit_times": limit_times,
                "bought": bought,
            }],
        }],
    }


def test_stock_floor_uses_existing_inventory_instead_of_fixed_exchange_count() -> None:
    plan = plan_sacred_exchange_stock(
        _snapshot(), target_item_id=9001, current_stock=1502, target_stock=3000
    )

    assert plan.exchange_count == 15
    assert plan.total_cost == 30
    assert plan.projected_stock == 3002
    assert plan.ready is True


def test_stock_floor_is_idempotent_when_inventory_is_already_enough() -> None:
    plan = plan_sacred_exchange_stock(
        _snapshot(), target_item_id=9001, current_stock=3376, target_stock=3000
    )

    assert plan.exchange_count == 0
    assert plan.total_cost == 0
    assert plan.projected_stock == 3376


def test_stock_floor_reports_finite_shop_shortfall_without_overbuying() -> None:
    plan = plan_sacred_exchange_stock(
        _snapshot(limit_times=10, bought=8),
        target_item_id=9001,
        current_stock=1502,
        target_stock=3000,
    )

    assert plan.exchange_count == 2
    assert plan.projected_stock == 1702
    assert plan.ready is False


def test_stock_floor_rejects_ambiguous_runtime_rows() -> None:
    snapshot = _snapshot()
    snapshot["rows"].append(snapshot["rows"][0])

    with pytest.raises(ValueError, match="命中 2 行"):
        plan_sacred_exchange_stock(
            snapshot, target_item_id=9001, current_stock=0, target_stock=3000
        )
