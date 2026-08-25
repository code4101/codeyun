from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Mapping


@dataclass(frozen=True)
class SacredExchangeStockPlan:
    target_item_id: int
    target_item_name: str
    current_stock: int
    target_stock: int
    exchange_count: int
    goods_per_exchange: int
    cost_item_id: int
    cost_per_exchange: int
    total_cost: int
    projected_stock: int
    ready: bool
    reason: str


def plan_sacred_exchange_stock(
    snapshot: Mapping[str, Any],
    *,
    target_item_id: int,
    current_stock: int,
    target_stock: int,
) -> SacredExchangeStockPlan:
    """Plan enough common divine-item exchanges to reach an inventory floor.

    The plan consumes the complete read-only type=3 shop projection. It never
    guesses a row from OCR and never asks for a fixed number of exchanges when
    the backpack already contains part of the target stock.
    """

    if not bool(snapshot.get("complete")):
        raise ValueError("神物兑换商品 Runtime 快照不完整")
    if min(int(target_item_id), int(target_stock), int(current_stock)) < 0:
        raise ValueError("神物兑换库存参数不能为负数")
    matches = [
        dict(entry)
        for group in snapshot.get("rows") or ()
        if isinstance(group, Mapping)
        for entry in group.get("entries") or ()
        if isinstance(entry, Mapping)
        and int(entry.get("item_id") or 0) == int(target_item_id)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"神物兑换目标 item_id={int(target_item_id)} 命中 {len(matches)} 行，拒绝猜测"
        )
    row = matches[0]
    goods_num = int(row.get("goods_num") or 0)
    cost_item_id = int(row.get("cost_item_id") or 0)
    cost_num = int(row.get("cost_num") or 0)
    if min(goods_num, cost_item_id, cost_num) <= 0:
        raise ValueError("神物兑换目标商品数量或消耗无效")

    missing = max(0, int(target_stock) - int(current_stock))
    needed = ceil(missing / goods_num) if missing else 0
    bought = max(0, int(row.get("bought") or 0))
    limit = int(row.get("limit_times") if row.get("limit_times") is not None else -1)
    available = needed if limit < 0 else max(0, limit - bought)
    executable = min(needed, available)
    ready = executable == needed
    projected = int(current_stock) + executable * goods_num
    reason = (
        f"库存已达到 {int(target_stock)}"
        if needed == 0
        else f"兑换 {needed} 次后库存至少 {int(current_stock) + needed * goods_num}"
        if ready
        else f"限购仅剩 {available} 次，最多达到库存 {projected}"
    )
    return SacredExchangeStockPlan(
        target_item_id=int(target_item_id),
        target_item_name=str(row.get("name") or "").strip(),
        current_stock=int(current_stock),
        target_stock=int(target_stock),
        exchange_count=executable,
        goods_per_exchange=goods_num,
        cost_item_id=cost_item_id,
        cost_per_exchange=cost_num,
        total_cost=executable * cost_num,
        projected_stock=projected,
        ready=ready,
        reason=reason,
    )


__all__ = ["SacredExchangeStockPlan", "plan_sacred_exchange_stock"]
