from __future__ import annotations

"""Activity-neutral exchange-tail allocation and GUI verification."""

from dataclasses import dataclass
from datetime import date
import re
from typing import Any


@dataclass(frozen=True)
class ExchangeTailPurchase:
    goods_id: int
    source_order: int
    name: str
    quantity: int
    unit_price: int


@dataclass(frozen=True)
class ExchangeTailPhysicalAction:
    """One purchase placed on the cheapest monotonic GUI traversal path."""

    goods_id: int
    name: str
    quantity: int
    unit_price: int
    slot: int
    scroll_rows: int
    clears_row: bool


def exchange_quantity_clicks(quantity: int, *, buying_to_cap: bool) -> tuple[int, int]:
    value = int(quantity)
    if value < 1:
        raise ValueError("兑换数量至少为 1")
    if buying_to_cap:
        return ((value - 1 + 9) // 10, 0)
    return divmod(value - 1, 10)


def authorize_exchange_purchase(
    *,
    current_wallet: int,
    quantity: int,
    unit_price: int,
    reserved_tokens: int,
    name: str,
    label: str = "玩法榜_收尾",
) -> tuple[int, int]:
    """Return ``(cost, remaining_wallet)`` only when the purchase is safe."""

    wallet = int(current_wallet)
    amount = int(quantity)
    price = int(unit_price)
    reserve = int(reserved_tokens)
    if wallet < 0 or reserve < 0:
        raise RuntimeError(f"{label}：钱包或预留额不能为负数")
    if amount < 1 or price < 1:
        raise RuntimeError(f"{label}：{name} 缺少有效购买数量或单价")
    cost = amount * price
    remaining = wallet - cost
    if remaining < reserve:
        raise RuntimeError(f"{label}：{name} 将突破锁定资源预留额")
    return cost, remaining


def verify_exchange_wallet(
    expected_wallet: int,
    observed_wallets: dict[str, Any],
    *,
    label: str = "玩法榜_收尾",
    stage: str = "最终",
) -> int:
    """Require every authoritative wallet source to equal one expectation."""

    expected = int(expected_wallet)
    if expected < 0:
        raise RuntimeError(f"{label}：{stage}预期钱包不能为负数")
    if not observed_wallets:
        raise RuntimeError(f"{label}：{stage}钱包缺少权威读数")
    mismatches = {
        str(source): int(value)
        for source, value in observed_wallets.items()
        if int(value) != expected
    }
    if mismatches:
        visible = "，".join(f"{source}={value}" for source, value in mismatches.items())
        raise RuntimeError(f"{label}：{stage}钱包没有闭环为 {expected}：{visible}")
    return expected


def verify_exchange_purchase_counts(
    initial_shop_items: Any,
    final_shop_items: Any,
    purchases: Any,
    *,
    label: str = "玩法榜_收尾",
) -> dict[int, int]:
    """Verify finite-row purchase counts after an irreversible exchange batch."""

    def unique_rows(items: Any, *, stage: str) -> dict[int, Any]:
        rows: dict[int, Any] = {}
        for item in items:
            goods_id = int(item.goods_id)
            if goods_id in rows:
                raise RuntimeError(f"{label}：{stage}商品 {goods_id} 重复")
            rows[goods_id] = item
        return rows

    initial_rows = unique_rows(initial_shop_items, stage="购买前")
    final_rows = unique_rows(final_shop_items, stage="购买后")
    expected_counts: dict[int, int] = {}
    seen_purchases: set[int] = set()
    for purchase in purchases:
        goods_id = int(purchase.goods_id)
        if goods_id in seen_purchases:
            raise RuntimeError(f"{label}：商品 {goods_id} 出现重复购买计划")
        seen_purchases.add(goods_id)
        original = initial_rows.get(goods_id)
        if original is None:
            raise RuntimeError(f"{label}：购买前缺少商品 {goods_id}/{purchase.name}")
        if int(original.purchase_limit) < 0:
            continue
        expected_count = int(original.purchased_count) + int(purchase.quantity)
        actual = final_rows.get(goods_id)
        actual_count = int(actual.purchased_count) if actual is not None else -1
        if actual_count != expected_count:
            raise RuntimeError(
                f"{label}：{purchase.name} 最终购买数 {actual_count} != {expected_count}"
            )
        expected_counts[goods_id] = expected_count
    return expected_counts


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or ""))


def ocr_contains_amount(values: Any, text: Any, expected: int) -> bool:
    amount = int(expected)
    if amount in {int(value) for value in values}:
        return True
    # The GUI renders unit price, total price and wallet close together. OCR
    # can concatenate adjacent number runs (for example 5000 + 10000 becomes
    # 500010000). Runtime title/row alignment remains authoritative; accept
    # the expected amount inside that bounded price shape instead of requiring
    # an artificially isolated OCR token.
    digits = re.sub(r"\D+", "", str(text or ""))
    return str(amount) in digits


def plan_exchange_tail_purchases(
    detail: Any,
    *,
    run_date: date,
    label: str = "玩法榜_收尾",
) -> tuple[list[ExchangeTailPurchase], set[int], dict[str, Any]]:
    """Allocate the final wallet by priority, then expose a GUI-efficient set.

    Priority determines which rows receive currency.  The selected rows may be
    visited in Runtime ``source_order`` by the GUI driver because that does not
    change the allocation.
    """

    plan = dict(detail.exchange_plan or {})
    if not bool(plan.get("budget_ready")):
        raise RuntimeError(f"{label}：钱包与商店购买进度不是同窗口最新 Runtime 事实")
    ordered_ids = [int(value) for value in plan.get("ordered_goods_ids") or []]
    rows = {int(item.goods_id): item for item in detail.shop_items}
    if not ordered_ids or any(goods_id not in rows for goods_id in ordered_ids):
        raise RuntimeError(f"{label}：兑换优先级与本期 Runtime 商品集合不一致")

    target_budgets = dict(plan.get("target_budgets") or {})
    closing_goods_budget = dict(target_budgets.get("收尾道具") or {})
    closing_goods_reached = (
        int(closing_goods_budget.get("required_new_currency") or 0) == 0
    )
    card_name = str(plan.get("card_mail_resource") or "")
    prayer_name = str(plan.get("next_prayer_resource") or "")
    original_locked = {int(value) for value in plan.get("locked_goods_ids") or []}
    retained_locked: set[int] = set()
    for goods_id in original_locked:
        row = rows.get(goods_id)
        if row is None:
            continue
        name = str(row.name or "")
        if prayer_name and name == prayer_name:
            if run_date.weekday() != 0:
                retained_locked.add(goods_id)
        elif card_name and name == card_name:
            if closing_goods_reached:
                retained_locked.add(goods_id)
        else:
            raise RuntimeError(f"{label}：发现未分类锁定商品 {goods_id}/{name}")
    if len(retained_locked) > 2:
        raise RuntimeError(f"{label}：最终锁定商品超过两项")

    reserve = sum(
        max(0, int(rows[goods_id].purchase_limit) - int(rows[goods_id].purchased_count))
        * int(rows[goods_id].token_cost)
        for goods_id in retained_locked
        if int(rows[goods_id].purchase_limit) >= 0
    )
    spendable = max(0, int(detail.current_currency) - reserve)
    purchases: list[ExchangeTailPurchase] = []
    for goods_id in ordered_ids:
        if goods_id in retained_locked:
            continue
        row = rows[goods_id]
        price = int(row.token_cost)
        if price <= 0:
            raise RuntimeError(f"{label}：商品 {goods_id} 缺少有效价格")
        limit = int(row.purchase_limit)
        if limit >= 0:
            wanted = max(0, limit - int(row.purchased_count))
        else:
            wanted = spendable // price
        quantity = min(wanted, spendable // price)
        if quantity <= 0:
            continue
        purchases.append(
            ExchangeTailPurchase(
                goods_id=goods_id,
                source_order=int(row.source_order),
                name=str(row.name),
                quantity=quantity,
                unit_price=price,
            )
        )
        spendable -= quantity * price
    return purchases, retained_locked, {
        "closing_goods_reached": closing_goods_reached,
        "retained_locked_goods_ids": sorted(retained_locked),
        "reserved_tokens": reserve,
        "planned_spend_tokens": int(detail.current_currency) - (spendable + reserve),
        "planned_remaining_tokens": spendable + reserve,
        "complete": not purchases,
    }


def plan_exchange_tail_physical_actions(
    shop_items: Any,
    purchases: list[ExchangeTailPurchase],
    *,
    window_size: int = 5,
    label: str = "玩法榜_收尾",
) -> list[ExchangeTailPhysicalAction]:
    """Map theoretical allocations onto one downward GUI traversal.

    Priority has already decided every target quantity.  This function only
    chooses the cheap physical path.  A fully bought finite row disappears
    from the active prefix; a partial or skipped row keeps occupying its
    position.  No Runtime refresh is needed to derive those transitions.
    """

    if int(window_size) < 1:
        raise ValueError("兑换宝阁可见行数至少为 1")
    targets = {int(row.goods_id): row for row in purchases}
    if len(targets) != len(purchases):
        raise RuntimeError(f"{label}：同一商品出现重复理论分配")

    active: list[dict[str, Any]] = []
    known_ids: set[int] = set()
    for item in sorted(
        shop_items,
        key=lambda row: (int(row.source_order), int(row.goods_id)),
    ):
        goods_id = int(item.goods_id)
        known_ids.add(goods_id)
        limit = int(item.purchase_limit)
        remaining = (
            max(0, limit - int(item.purchased_count)) if limit >= 0 else None
        )
        if remaining == 0:
            continue
        target = targets.get(goods_id)
        target_quantity = int(target.quantity) if target is not None else 0
        if remaining is not None and target_quantity > remaining:
            raise RuntimeError(f"{label}：商品 {goods_id} 理论数量超过当前余量")
        active.append({
            "goods_id": goods_id,
            "name": str(item.name),
            "remaining": remaining,
            "target": target_quantity,
        })
    missing = set(targets) - known_ids
    if missing:
        raise RuntimeError(f"{label}：理论目标不在 Runtime 商品序列：{min(missing)}")

    actions: list[ExchangeTailPhysicalAction] = []
    cursor = 0
    top = 0
    while any(int(row["target"]) > 0 for row in active):
        if cursor >= len(active):
            raise RuntimeError(f"{label}：物理序列结束后仍有未执行理论目标")
        row = active[cursor]
        quantity = int(row["target"])
        if quantity <= 0:
            cursor += 1
            continue

        wanted_top = max(top, cursor - int(window_size) + 1)
        scroll_rows = wanted_top - top
        top = wanted_top
        purchase = targets[int(row["goods_id"])]
        remaining = row["remaining"]
        clears_row = remaining is not None and quantity == int(remaining)
        actions.append(ExchangeTailPhysicalAction(
            goods_id=int(row["goods_id"]),
            name=str(row["name"]),
            quantity=quantity,
            unit_price=int(purchase.unit_price),
            slot=cursor - top + 1,
            scroll_rows=scroll_rows,
            clears_row=clears_row,
        ))

        if clears_row:
            active.pop(cursor)
            # Near the bottom the GUI clamps its top row after a row moves to
            # the completed tail.  Mirror that deterministic local transition.
            top = min(top, max(0, len(active) - int(window_size)))
        else:
            row["target"] = 0
            if remaining is not None:
                row["remaining"] = int(remaining) - quantity
            cursor += 1
    return actions


def verify_exchange_detail(
    runtime: Any,
    *,
    expected_name: str,
    expected_price: int,
    scene_id: int = 566,
    label: str = "玩法榜_收尾",
) -> None:
    compact_name = _compact(expected_name)
    title = runtime.ocr_text_in_shapes(scene_id, ("商品标题",), padding=10)
    if not compact_name or compact_name not in _compact(title):
        raise RuntimeError(f"{label}：Runtime 行未对齐 GUI 商品详情 {expected_name}")
    prices, price_text = runtime.ocr_numbers_in_shapes(scene_id, ("价格",), padding=8)
    if not ocr_contains_amount(prices, price_text, expected_price):
        raise RuntimeError(
            f"{label}：{expected_name} GUI 单价与 Runtime {expected_price} 不一致：{price_text}"
        )

__all__ = [
    "ExchangeTailPhysicalAction",
    "ExchangeTailPurchase",
    "authorize_exchange_purchase",
    "exchange_quantity_clicks",
    "ocr_contains_amount",
    "plan_exchange_tail_physical_actions",
    "plan_exchange_tail_purchases",
    "verify_exchange_detail",
    "verify_exchange_purchase_counts",
    "verify_exchange_wallet",
]
