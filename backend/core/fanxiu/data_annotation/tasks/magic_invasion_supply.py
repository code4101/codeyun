from __future__ import annotations

"""魔道入侵通过天雷竹神物兑换补充天眼符。"""

import threading
from typing import Any, Mapping

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
from backend.core.fanxiu.resources.sacred_exchange_planner import SacredExchangeStockPlan


TIANYAN_ITEM_ID = 1_010_004
TIANLEI_BAMBOO_ITEM_ID = 4_000_001
MAGIC_TIANYAN_SUPPLY = SacredExchangeSupplySpec(
    label="魔道入侵天眼符补给",
    source_item_id=TIANLEI_BAMBOO_ITEM_ID,
    source_item_name="天雷竹",
    target_item_id=TIANYAN_ITEM_ID,
    detail_scene=858,
)


def plan_magic_tianyan_supply(
    backpack: Mapping[str, Any],
    shop: Mapping[str, Any],
    *,
    required_tianyan: int,
) -> SacredExchangeStockPlan:
    return plan_sacred_exchange_supply(
        backpack,
        shop,
        spec=MAGIC_TIANYAN_SUPPLY,
        required_stock=required_tianyan,
    )


def verify_magic_tianyan_supply_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    plan: SacredExchangeStockPlan,
) -> None:
    verify_sacred_exchange_supply_delta(before, after, plan)


def ensure_magic_tianyan_supply(
    runner: Any,
    ctx: dict[str, Any],
    stop_event: threading.Event,
    *,
    required_tianyan: int,
    snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,
    shop_reader=read_sacred_exchange_shop_snapshot,
    buy_reader=read_common_shop_buy_dialog_snapshot,
    catalog_reader=lambda: load_fanxiu_item_runtime_index(rebuild_missing=False)[
        "cards_by_id"
    ],
):
    """Ensure a 天眼符 floor through the shared public 神物兑换 transaction."""

    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    result = yield from ensure_sacred_exchange_stock(
        context,
        spec=MAGIC_TIANYAN_SUPPLY,
        required_stock=required_tianyan,
        snapshot_reader=snapshot_reader,
        shop_reader=shop_reader,
        buy_reader=buy_reader,
        catalog_reader=catalog_reader,
    )
    return {
        **dict(result),
        "tianlei_bamboo_spent": int(result.get("cost_spent") or 0),
        "tianyan_after": int(result.get("stock_after") or 0),
    }


__all__ = [
    "TIANLEI_BAMBOO_ITEM_ID",
    "TIANYAN_ITEM_ID",
    "ensure_magic_tianyan_supply",
    "plan_magic_tianyan_supply",
    "verify_magic_tianyan_supply_delta",
]
