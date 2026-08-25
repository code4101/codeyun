from __future__ import annotations

from backend.core.fanxiu.instrumentation.sacred_exchange_shop import (
    decode_sacred_exchange_shop_config,
)


def test_decode_tianyan_talisman_divine_exchange_row() -> None:
    values = [None] * 19
    values[1] = 5_020_005
    values[2] = 3_000_005
    values[3] = 3
    values[6] = 1_010_004
    values[7] = 1
    values[12] = -1
    values[17] = 7

    row = decode_sacred_exchange_shop_config(
        values,
        cost_values=(None, "Item|4000001_20", None),
    )

    assert row == {
        "goods_id": 5_020_005,
        "group_id": 3_000_005,
        "item_id": 1_010_004,
        "goods_num": 1,
        "cost_item_id": 4_000_001,
        "cost_num": 20,
        "position": 7,
        "limit_times": -1,
        "unlimited": True,
    }
