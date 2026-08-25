from __future__ import annotations

from datetime import date
from typing import Any

from backend.core.fanxiu.activity.beast_abyss import (
    collect_and_store_beast_abyss_activity,
)
from backend.core.fanxiu.data_annotation.tasks.yunmeng_tail import (
    _detail_matches,
    _ocr_contains_amount,
    plan_exchange_tail_purchases,
    plan_yunmeng_tail_physical_actions,
    yunmeng_quantity_clicks,
)


BEAST_ABYSS_SHOP_SCENE = 536
COMMON_SHOP_DETAIL_SCENE = 566


def execute_beast_abyss_exchange(
    runner: Any,
    ctx: dict[str, Any],
    *,
    activity_id: str,
    stop_event: Any,
):
    """Redeem one fresh Beast Abyss plan and retain every locked row."""

    from sqlmodel import Session

    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )
    from backend.db import engine

    label = "兽渊_兑换"
    runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
    scene_id, _score, _frame = runtime.current_scene(
        (BEAST_ABYSS_SHOP_SCENE, COMMON_SHOP_DETAIL_SCENE),
        update=True,
    )
    if scene_id == COMMON_SHOP_DETAIL_SCENE:
        runtime.click_shape_center(COMMON_SHOP_DETAIL_SCENE, "关闭详情")
        yield from runtime.wait_view(
            BEAST_ABYSS_SHOP_SCENE,
            timeout=15.0,
            label=f"{label}：关闭遗留商品详情",
        )
    elif scene_id != BEAST_ABYSS_SHOP_SCENE:
        raise RuntimeError(f"{label}：要求从 #536 兑换宝阁开始")

    with Session(engine) as session:
        detail = collect_and_store_beast_abyss_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=True,
        )
        session.commit()
    wallet = read_wallet_currency_snapshot(14, allow_discovery=False)
    expected_wallet = int(wallet["exchange_currency"])
    if int(detail.current_currency) != expected_wallet:
        raise RuntimeError(
            f"{label}：商店与钱包不同窗：activity={detail.current_currency}, "
            f"wallet={expected_wallet}"
        )

    purchases, retained_locked, planning = plan_exchange_tail_purchases(
        detail,
        run_date=date.today(),
        label=label,
    )
    actions = plan_yunmeng_tail_physical_actions(detail.shop_items, purchases)
    initial_counts = {
        int(row.goods_id): int(row.purchased_count) for row in detail.shop_items
    }
    reserved_tokens = int(planning["reserved_tokens"])
    executed: list[dict[str, Any]] = []

    for action in actions:
        if stop_event.is_set():
            raise InterruptedError()
        for _ in range(action.scroll_rows):
            yield from runtime.scroll_shape_content(
                BEAST_ABYSS_SHOP_SCENE,
                "商品列表",
                direction="down",
            )

        runtime.click_shape_center(
            BEAST_ABYSS_SHOP_SCENE,
            f"商品行{action.slot}",
        )
        yield from runtime.wait_view(
            COMMON_SHOP_DETAIL_SCENE,
            timeout=15.0,
            label=f"{label}：等待 {action.name} 商品详情",
        )
        _detail_matches(
            runtime,
            expected_name=action.name,
            expected_price=action.unit_price,
        )
        plus_ten_count, plus_one_count = yunmeng_quantity_clicks(
            action.quantity,
            buying_to_cap=action.clears_row,
        )
        for index in range(plus_ten_count):
            runtime.click_shape_center_fast(COMMON_SHOP_DETAIL_SCENE, "+10")
            if (index + 1) % 25 == 0:
                yield from runtime.wait_action_settle(0.05)
        for index in range(plus_one_count):
            runtime.click_shape_center_fast(COMMON_SHOP_DETAIL_SCENE, "+")
            if (index + 1) % 25 == 0:
                yield from runtime.wait_action_settle(0.05)
        yield from runtime.wait_action_settle(0.4)

        expected_total = int(action.quantity) * int(action.unit_price)
        if expected_wallet - expected_total < reserved_tokens:
            raise RuntimeError(
                f"{label}：{action.name} 将突破锁定资源保留额 {reserved_tokens}"
            )
        totals: list[int] = []
        total_text = ""
        for _ in range(3):
            price_frame = runtime.cur_frame(update=True)
            totals, total_text = runtime.ocr_numbers_in_shapes(
                COMMON_SHOP_DETAIL_SCENE,
                ("价格",),
                # Beast Abyss renders the total one token row below the common
                # label crop; 30px keeps the amount and wallet in the bounded
                # purchase panel while 8px only sees the label on real #566.
                padding=30,
                frame_data_url=price_frame,
            )
            if _ocr_contains_amount(totals, total_text, expected_total):
                break
            yield from runtime.wait_action_settle(0.4)
        if not _ocr_contains_amount(totals, total_text, expected_total):
            raise RuntimeError(
                f"{label}：{action.name} 数量调整后总价未闭环为 {expected_total}"
            )
        yield from runtime.click_shape_center_then_view(
            COMMON_SHOP_DETAIL_SCENE,
            "购买",
            BEAST_ABYSS_SHOP_SCENE,
            timeout=15.0,
            label=f"{label}：购买 {action.name} 后返回宝阁",
        )
        expected_wallet -= expected_total
        executed.append({
            "goods_id": int(action.goods_id),
            "name": str(action.name),
            "quantity": int(action.quantity),
            "unit_price": int(action.unit_price),
        })

    if expected_wallet != int(planning["planned_remaining_tokens"]):
        raise RuntimeError(f"{label}：物理动作没有完整核销理论预算")

    with Session(engine) as session:
        final_detail = collect_and_store_beast_abyss_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=True,
        )
        session.commit()
    final_rows = {int(row.goods_id): row for row in final_detail.shop_items}
    for purchase in purchases:
        original = next(
            row for row in detail.shop_items
            if int(row.goods_id) == int(purchase.goods_id)
        )
        if int(original.purchase_limit) < 0:
            continue
        expected_count = initial_counts[int(purchase.goods_id)] + int(purchase.quantity)
        actual = final_rows.get(int(purchase.goods_id))
        if actual is None or int(actual.purchased_count) != expected_count:
            raise RuntimeError(
                f"{label}：商品 {purchase.goods_id} 最终购买数没有闭环"
            )
    final_wallet = read_wallet_currency_snapshot(14, allow_discovery=False)
    if (
        int(final_detail.current_currency) != expected_wallet
        or int(final_wallet["exchange_currency"]) != expected_wallet
    ):
        raise RuntimeError(
            f"{label}：最终钱包未闭环为 {expected_wallet}"
        )
    return {
        "result": "success",
        "message": (
            f"{label}完成：兑换 {len(executed)} 种，"
            f"保留锁定 {len(retained_locked)} 种，余额 {expected_wallet}"
        ),
        "activity_id": activity_id,
        "purchases": executed,
        "planning": planning,
        "currency_remaining": expected_wallet,
        "current_scene": BEAST_ABYSS_SHOP_SCENE,
    }


__all__ = ["execute_beast_abyss_exchange"]
