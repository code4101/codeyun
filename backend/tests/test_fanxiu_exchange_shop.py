import pytest

from backend.core.fanxiu.instrumentation.exchange_shop import (
    FanxiuExchangeShopCollectionError,
    decode_exchange_shop_config,
    project_exchange_shop_items,
)


def _row(goods_id: int, *, bought: int, limit: int, position: int = 3):
    return {
        "goods_id": goods_id,
        "item_id": 3012109,
        "goods_num": 1,
        "limit_buy": 3,
        "second_tag": 1,
        "third_tag": -1,
        "group": 3012109,
        "scope_type": 4,
        "limit_times": limit,
        "position": position,
        "cost_item_id": 3020143,
        "cost_num": 80,
        "has_buy_time": bought,
    }


def test_decode_exchange_shop_config_uses_proven_generated_row_layout():
    values = [None, 12027, 3012109, None, 3, 1, -1, 3012109, 1, object(), 4, 47, "CL|1", None, "CL|171", "CL|999", 3, 1]
    result = decode_exchange_shop_config(values, cost_values=[None, "Item|3020143_80", None])
    assert result == {
        "goods_id": 12027,
        "item_id": 3012109,
        "goods_num": 1,
        "limit_buy": 3,
        "second_tag": 1,
        "third_tag": -1,
        "group": 3012109,
        "scope_type": 4,
        "limit_times": 47,
        "position": 3,
        "cost_item_id": 3020143,
        "cost_num": 80,
    }


def test_project_exchange_shop_items_merges_permanent_limit_tiers():
    [item] = project_exchange_shop_items(
        [_row(12003, bought=0, limit=3), _row(12027, bought=1, limit=47)],
        catalog_by_id={3012109: {"id": 3012109, "name": "悟·青锋映日", "type": 999, "sub_type": 33, "linked_gongfa_id": 316103}},
    )
    assert item["limit_total"] == 50
    assert item["bought_total"] == 1
    assert item["remaining"] == 49
    assert item["unlimited"] is False
    assert item["goods_ids"] == [12003, 12027]
    assert item["linked_gongfa_id"] == 316103


def test_project_exchange_shop_items_fails_closed_on_mixed_prices():
    second = _row(12027, bought=1, limit=47)
    second["cost_num"] = 81
    with pytest.raises(FanxiuExchangeShopCollectionError, match="价格"):
        project_exchange_shop_items(
            [_row(12003, bought=0, limit=3), second],
            catalog_by_id={3012109: {"id": 3012109, "name": "悟·青锋映日"}},
        )


def test_project_exchange_shop_items_supports_unlimited_langya_books():
    row = _row(1033, bought=3, limit=-1, position=1)
    row.update(item_id=3120001, group=3120001, scope_type=None, second_tag=5, cost_item_id=3135001)
    [item] = project_exchange_shop_items(
        [row],
        catalog_by_id={3120001: {"id": 3120001, "name": "须弥感应篇", "type": 3, "sub_type": 8, "linked_gongfa_id": 400101}},
    )
    assert item["scope_type"] is None
    assert item["unlimited"] is True
    assert item["limit_total"] is None
    assert item["remaining"] is None
