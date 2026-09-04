from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks.magic_invasion_supply import (
    TIANLEI_BAMBOO_ITEM_ID,
    TIANYAN_ITEM_ID,
    plan_magic_tianyan_supply,
    verify_magic_tianyan_supply_delta,
)


def _snapshot(*, bamboo: int, tianyan: int, fingerprint: str = "a") -> dict:
    return {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": fingerprint,
        "evidence": {"pid": 11, "process_start_ticks": 22},
        "items": [
            {
                "base_id": TIANLEI_BAMBOO_ITEM_ID,
                "num": bamboo,
                "instance_id": "bamboo",
            },
            {
                "base_id": TIANYAN_ITEM_ID,
                "num": tianyan,
                "instance_id": "tianyan",
            },
        ],
    }


def _shop(*, bought: int = 0, limit: int = -1) -> dict:
    return {
        "complete": True,
        "rows": [
            {
                "entries": [
                    {
                        "item_id": TIANYAN_ITEM_ID,
                        "name": "天眼符",
                        "goods_num": 100,
                        "cost_item_id": TIANLEI_BAMBOO_ITEM_ID,
                        "cost_num": 20,
                        "limit_times": limit,
                        "bought": bought,
                    }
                ]
            }
        ],
    }


def test_plan_exchanges_only_enough_tianlei_bamboo_to_reach_3000() -> None:
    plan = plan_magic_tianyan_supply(
        _snapshot(bamboo=1000, tianyan=286),
        _shop(),
        required_tianyan=3000,
    )

    assert plan.exchange_count == 28
    assert plan.total_cost == 560
    assert plan.projected_stock == 3086
    assert plan.cost_item_id == TIANLEI_BAMBOO_ITEM_ID


def test_plan_fails_closed_when_tianlei_bamboo_cannot_reach_target() -> None:
    with pytest.raises(RuntimeError, match="当前天雷竹 100.*目标 3000"):
        plan_magic_tianyan_supply(
            _snapshot(bamboo=100, tianyan=286),
            _shop(),
            required_tianyan=3000,
        )


def test_plan_fails_closed_when_shop_cost_item_is_not_tianlei_bamboo() -> None:
    shop = _shop()
    shop["rows"][0]["entries"][0]["cost_item_id"] = 123
    with pytest.raises(RuntimeError, match="123"):
        plan_magic_tianyan_supply(
            _snapshot(bamboo=1000, tianyan=286),
            shop,
            required_tianyan=3000,
        )


def test_supply_delta_requires_exact_bamboo_cost_and_tianyan_gain() -> None:
    before = _snapshot(bamboo=1000, tianyan=286)
    plan = plan_magic_tianyan_supply(before, _shop(), required_tianyan=3000)
    verify_magic_tianyan_supply_delta(
        before,
        _snapshot(bamboo=440, tianyan=3086, fingerprint="b"),
        plan,
    )
    with pytest.raises(RuntimeError, match="Runtime 双差值"):
        verify_magic_tianyan_supply_delta(
            before,
            _snapshot(bamboo=441, tianyan=3086, fingerprint="c"),
            plan,
        )
