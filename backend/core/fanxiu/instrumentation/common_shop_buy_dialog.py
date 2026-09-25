from __future__ import annotations

"""Strict read-only projection and quantity planning for CommonShop buy tips."""

import math
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from backend.core.fanxiu.instrumentation.exchange_shop import project_exchange_shop_numeric_row
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    active_ui_component_objects,
    has_ui_object_fields,
    read_ui_object_field,
    read_ui_runtime_snapshot,
)


_REQUIRED_KEYS = frozenset({
    "ShopCfg", "BuyBtn", "Slider", "maxNum", "minNum", "needNum", "showNum",
    "Price", "HadPrice", "goodsNum", "CanBuy", "isEnough", "ShopModelType",
    "itemCfg", "costItemCfg", "id", "goodsId",
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
        if has_ui_object_fields(
            context, component.address, {"ShopCfg", "BuyBtn", "Slider", "showNum", "maxNum"}
        ):
            candidates.append(component)
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            f"active CommonShop 购买框数量为 {len(candidates)}",
            code="data_not_loaded" if not candidates else "runtime_incomplete",
        )
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
    identities = {}
    for output, field, config_key in (
        ("item_id", "itemCfg", "id"),
        ("cost_item_id", "costItemCfg", "id"),
        ("goods_id", "ShopCfg", "goodsId"),
    ):
        config = table_ref(read_ui_object_field(context, panel.address, field))
        identifier = None
        if config is not None:
            identifier = as_int(read_ui_object_field(context, config.address, config_key))
            if identifier is None:
                # Item.id and CommonShop goodsId are generated column 1.
                row = project_exchange_shop_numeric_row(context.reader.table(config.address))
                identifier = as_int(row[1]) if len(row) > 1 else None
        identities[output] = identifier if identifier is not None and identifier > 0 else None
    return {
        "ok": True,
        "complete": True,
        "source": "active_common_shop_buy_tips",
        "pid": context.binding.pid,
        "process_start_ticks": context.binding.process_start_ticks,
        **values,
        **identities,
        "identity_complete": all(value is not None for value in identities.values()),
        "CanBuy": can_buy,
        "isEnough": enough,
        "panel_address": f"0x{panel.address:x}",
        "read_only": True,
    }


def read_common_shop_buy_dialog_snapshot() -> dict[str, Any]:
    """Read the active panel through the shared bounded snapshot recovery.

    item_id/cost_item_id/goods_id describe this observation's selected product;
    identity_complete is false if any identity is unavailable (including free
    goods without a currency). Callers requiring a paid exchange must require
    all three and compare them before clicking. Live acceptance covered the
    treasure-shop SpiritWare purchase dialog (item/currency/goods identity);
    other shop variants and changing goods while open remain unverified.

    CommonShopBuyTips rebuilds its field table while the slider animates.  A
    child address is therefore scoped to one coherent read only.  The generic
    UI snapshot layer owns the one fresh-context retry for memory faults.
    An outer retry used to multiply that budget to four reads and discard
    unrelated UI roots.  This adapter only records actual projection attempts.

    Live acceptance still needs slider animation, closing/reopening the dialog
    and changing goods: confirm current quantity/goods identity after each
    action.  Repeated faults require investigating the first failing field and
    panel lifetime, not increasing retries or clearing the global root cache.
    """

    rebind_reasons: list[str] = []
    read_attempts = 0

    def read_panel(context: UiRuntimeContext) -> dict[str, Any]:
        nonlocal read_attempts
        read_attempts += 1
        try:
            return _read_snapshot(context)
        except FanxiuRuntimeMemoryError as exc:
            rebind_reasons.append(str(exc))
            raise

    try:
        snapshot = dict(read_ui_runtime_snapshot(_REQUIRED_KEYS, read_panel, fast=True))
        snapshot["panel_rebinds"] = max(0, read_attempts - 1)
        snapshot["read_attempts"] = read_attempts
        if rebind_reasons:
            snapshot["panel_rebind_reasons"] = tuple(rebind_reasons)
        return snapshot
    except (FanxiuRuntimeMemoryError, KeyError, AttributeError, TypeError, ValueError) as exc:
        error = exc
        import traceback
        diagnostic_traceback = traceback.format_exc()
    return {
        "ok": False,
        "complete": False,
        "source": "active_common_shop_buy_tips",
        "reason": str(error),
        "diagnostic_traceback": diagnostic_traceback,
        "panel_rebinds": max(0, read_attempts - 1),
        "read_attempts": max(1, read_attempts),
        "panel_rebind_reasons": tuple(rebind_reasons),
        "read_only": True,
    }


__all__ = ["plan_common_shop_quantity", "read_common_shop_buy_dialog_snapshot"]
