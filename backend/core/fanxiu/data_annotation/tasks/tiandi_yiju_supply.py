from __future__ import annotations

"""天地弈局通过公共神物兑换事务补充弈技·仙弈盒。"""

from collections.abc import Mapping
from typing import Any

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.data_annotation.tasks.sacred_exchange_supply import (
    SacredExchangeSupplySpec,
    ensure_sacred_exchange_stock,
    plan_sacred_exchange_supply,
    verify_sacred_exchange_supply_delta,
)
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import (
    read_common_shop_buy_dialog_snapshot,
)
from backend.core.fanxiu.instrumentation.sacred_exchange_shop import (
    read_sacred_exchange_shop_snapshot,
)
from backend.core.fanxiu.resources.sacred_exchange_planner import (
    SacredExchangeStockPlan,
)


SACRED_TREE_ITEM_ID = 1_300_755
TIANDI_YIJU_BOX_ITEM_ID = 100_000_004
TIANDI_YIJU_SUPPLY = SacredExchangeSupplySpec(
    label="天地弈局仙弈盒补给",
    source_item_id=SACRED_TREE_ITEM_ID,
    source_item_name="灵眼神树",
    target_item_id=TIANDI_YIJU_BOX_ITEM_ID,
    allow_partial=True,
)


def plan_tiandi_yiju_supply_admission(counts: Mapping[int, int], *, required_boxes: int):
    """Resolve stock sufficiency/absent source before any storage navigation."""
    stock = int(counts.get(TIANDI_YIJU_BOX_ITEM_ID, 0))
    if stock >= max(0, int(required_boxes)):
        return {"status": "sufficient", "boxes_after": stock}
    if int(counts.get(SACRED_TREE_ITEM_ID, 0)) == 0:
        return {"status": "unavailable", "reason": "source_item_absent", "boxes_after": stock}
    return None


def plan_tiandi_yiju_supply(
    backpack: Mapping[str, Any],
    shop: Mapping[str, Any],
    *,
    required_boxes: int,
) -> SacredExchangeStockPlan:
    """Plan the missing box stock through the shared exchange planner."""

    return plan_sacred_exchange_supply(
        backpack,
        shop,
        spec=TIANDI_YIJU_SUPPLY,
        required_stock=required_boxes,
    )


def verify_tiandi_yiju_supply_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    plan: SacredExchangeStockPlan,
) -> None:
    """Require the shared same-process cost/reward double delta."""

    verify_sacred_exchange_supply_delta(before, after, plan)


def ensure_tiandi_yiju_round_supply(
    context: Any,
    *,
    required_boxes: int,
    snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,
    shop_reader=read_sacred_exchange_shop_snapshot,
    buy_reader=read_common_shop_buy_dialog_snapshot,
    catalog_reader=lambda: load_fanxiu_item_runtime_index(rebuild_missing=False)[
        "cards_by_id"
    ],
):
    """Supply boxes, returning a legal shortage when no source item exists."""

    # Item absence is a resource outcome, not a failed attempt to find and
    # click a storage row. Keep stock admission in this supplying API.
    from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
    counts, _ = read_backpack_item_counts(
        [TIANDI_YIJU_BOX_ITEM_ID, SACRED_TREE_ITEM_ID],
        manager_key="tiandi-yiju-supply-admission",
    )
    admission = plan_tiandi_yiju_supply_admission(counts, required_boxes=required_boxes)
    if admission is not None:
        return admission

    result = yield from ensure_sacred_exchange_stock(
        context,
        spec=TIANDI_YIJU_SUPPLY,
        required_stock=required_boxes,
        snapshot_reader=snapshot_reader,
        shop_reader=shop_reader,
        buy_reader=buy_reader,
        catalog_reader=catalog_reader,
    )
    adapted = {
        "status": str(result.get("status") or ""),
        "boxes_after": int(result.get("stock_after") or 0),
    }
    if adapted["status"] == "supplied":
        adapted.update({
            "exchange_count": int(result.get("exchange_count") or 0),
            "tree_spent": int(result.get("cost_spent") or 0),
        })
    return adapted


__all__ = [
    "SACRED_TREE_ITEM_ID",
    "TIANDI_YIJU_BOX_ITEM_ID",
    "TIANDI_YIJU_SUPPLY",
    "ensure_tiandi_yiju_round_supply",
    "plan_tiandi_yiju_supply",
    "plan_tiandi_yiju_supply_admission",
    "verify_tiandi_yiju_supply_delta",
]
