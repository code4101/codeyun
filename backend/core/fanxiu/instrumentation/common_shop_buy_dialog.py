from __future__ import annotations

"""Strict read-only projection and quantity planning for CommonShop buy tips."""

import math
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError, as_int
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    active_ui_component_objects,
    read_ui_object_field,
    read_ui_runtime_snapshot,
)


_REQUIRED_KEYS = frozenset({
    "ShopCfg", "BuyBtn", "Slider", "maxNum", "minNum", "needNum", "showNum",
    "Price", "HadPrice", "goodsNum", "CanBuy", "isEnough", "ShopModelType",
})


def plan_common_shop_quantity(
    *,
    inventory: int,
    target_inventory: int,
    goods_num: int,
    unit_price: int,
    max_num: int,
    currency: int,
) -> dict[str, int | bool]:
    """Return the smallest purchase quantity that reaches an inventory floor."""

    values = (inventory, target_inventory, goods_num, unit_price, max_num, currency)
    if any(isinstance(value, bool) or int(value) < 0 for value in values):
        raise ValueError("兑换数量规划参数必须是非负整数")
    if goods_num <= 0 or unit_price <= 0:
        raise ValueError("单次产出和单价必须为正数")
    missing = max(0, int(target_inventory) - int(inventory))
    quantity = math.ceil(missing / int(goods_num)) if missing else 0
    cost = quantity * int(unit_price)
    return {
        "quantity": quantity,
        "cost": cost,
        "result_inventory": int(inventory) + quantity * int(goods_num),
        "within_dialog_max": quantity <= int(max_num),
        "currency_sufficient": cost <= int(currency),
        "ready": quantity > 0 and quantity <= int(max_num) and cost <= int(currency),
    }


def _required_int(context: UiRuntimeContext, address: int, field: str) -> int:
    value = as_int(read_ui_object_field(context, address, field))
    if value is None:
        raise FanxiuRuntimeMemoryError(f"CommonShop 购买框 {field} 不是整数")
    return int(value)


def _read_snapshot(context: UiRuntimeContext) -> dict[str, Any]:
    candidates = []
    for component in active_ui_component_objects(context):
        fields = context.reader.fields(component)
        if {"ShopCfg", "BuyBtn", "Slider", "showNum", "maxNum"}.issubset(fields):
            candidates.append(component)
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(f"active CommonShop 购买框数量为 {len(candidates)}")
    panel = candidates[0]
    values = {
        field: _required_int(context, panel.address, field)
        for field in ("maxNum", "minNum", "needNum", "showNum", "Price", "HadPrice", "goodsNum", "ShopModelType")
    }
    can_buy = read_ui_object_field(context, panel.address, "CanBuy")
    enough = read_ui_object_field(context, panel.address, "isEnough")
    if not isinstance(can_buy, bool) or not isinstance(enough, bool):
        raise FanxiuRuntimeMemoryError("CommonShop 购买框资格字段不是布尔值")
    if not values["minNum"] <= values["showNum"] <= values["maxNum"]:
        raise FanxiuRuntimeMemoryError("CommonShop 购买框数量超出 min/max")
    return {
        "ok": True,
        "complete": True,
        "source": "active_common_shop_buy_tips",
        "pid": context.binding.pid,
        "process_start_ticks": context.binding.process_start_ticks,
        **values,
        "CanBuy": can_buy,
        "isEnough": enough,
        "panel_address": f"0x{panel.address:x}",
        "read_only": True,
    }


def read_common_shop_buy_dialog_snapshot() -> dict[str, Any]:
    try:
        return read_ui_runtime_snapshot(_REQUIRED_KEYS, _read_snapshot, fast=True)
    except (FanxiuRuntimeMemoryError, KeyError, AttributeError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "complete": False,
            "source": "active_common_shop_buy_tips",
            "reason": str(exc),
            "read_only": True,
        }


__all__ = ["plan_common_shop_quantity", "read_common_shop_buy_dialog_snapshot"]
