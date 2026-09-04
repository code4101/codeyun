from __future__ import annotations

from backend.core.fanxiu.data_annotation.tasks.sacred_exchange_supply import (
    SacredExchangeSupplySpec,
    plan_sacred_exchange_supply,
    verify_sacred_exchange_supply_delta,
)


def _backpack(*, source: int, target: int, fingerprint: str) -> dict:
    return {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": fingerprint,
        "evidence": {"pid": 17, "process_start_ticks": 29},
        "items": [
            {"base_id": 7001, "num": source},
            {"base_id": 8002, "num": target},
        ],
    }


def test_generic_sacred_exchange_uses_spec_and_live_trade_ratio() -> None:
    spec = SacredExchangeSupplySpec(
        label="任意玩法榜补给",
        source_item_id=7001,
        source_item_name="测试神物",
        target_item_id=8002,
        storage_category="日程",
    )
    shop = {
        "complete": True,
        "rows": [{
            "entries": [{
                "item_id": 8002,
                "name": "测试目标道具",
                "goods_num": 30,
                "cost_item_id": 7001,
                "cost_num": 7,
                "limit_times": 20,
                "bought": 2,
            }],
        }],
    }
    before = _backpack(source=100, target=11, fingerprint="before")

    plan = plan_sacred_exchange_supply(
        before,
        shop,
        spec=spec,
        required_stock=100,
    )

    # 缺 89 个目标道具，但商店每次产出 30 个：滑轨目标是 3 次，
    # 神物消耗是 3*7；三者是不同业务量，不能互相冒充。
    assert plan.exchange_count == 3
    assert plan.goods_per_exchange == 30
    assert plan.total_cost == 21
    assert plan.projected_stock == 101
    verify_sacred_exchange_supply_delta(
        before,
        _backpack(source=79, target=101, fingerprint="after"),
        plan,
    )
