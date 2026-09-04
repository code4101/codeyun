from __future__ import annotations

from datetime import date, datetime
from typing import Any

from backend.core.fanxiu.activity.beast_abyss import (
    collect_and_store_beast_abyss_activity,
)
from backend.core.fanxiu.data_annotation.tasks.exchange_tail_planning import (
    exchange_quantity_clicks as yunmeng_quantity_clicks,
    ocr_contains_amount as _ocr_contains_amount,
    plan_exchange_tail_physical_actions as plan_yunmeng_tail_physical_actions,
    plan_exchange_tail_purchases,
    verify_exchange_detail as _detail_matches,
)


BEAST_ABYSS_SHOP_SCENE = 536
COMMON_SHOP_DETAIL_SCENE = 566


def _validate_fresh_exchange_snapshot(
    detail: Any,
    evidence: dict[str, Any],
    *,
    attempt_started_at: datetime,
    label: str,
) -> None:
    """Reject retained or cross-process facts before/after physical purchases."""

    refresh = dict(evidence.get("refresh_status") or {})
    if (
        not bool(detail.currency_fact_fresh)
        or not bool(detail.shop_fact_fresh)
        or refresh.get("currency") != "updated"
        or refresh.get("shop") != "updated"
    ):
        raise RuntimeError(f"{label}：钱包或兑换宝阁不是本次刷新事实")
    try:
        currency_at = datetime.fromisoformat(str(detail.currency_captured_at or ""))
        shop_at = datetime.fromisoformat(str(detail.shop_snapshot_captured_at or ""))
    except ValueError as exc:
        raise RuntimeError(f"{label}：本次刷新时间水位无效") from exc
    watermark = attempt_started_at.replace(microsecond=0)
    if currency_at < watermark or shop_at < watermark:
        raise RuntimeError(f"{label}：钱包或兑换宝阁仍是本 attempt 之前的快照")
    currency_process = dict(evidence.get("currency_runtime") or {})
    shop_process = dict(evidence.get("shop") or {})
    currency_identity = (
        int(currency_process.get("pid") or 0),
        int(currency_process.get("process_start_ticks") or 0),
    )
    shop_identity = (
        int(shop_process.get("pid") or 0),
        int(shop_process.get("process_start_ticks") or 0),
    )
    if 0 in currency_identity or currency_identity != shop_identity:
        raise RuntimeError(f"{label}：钱包与兑换宝阁不来自同一游戏进程")


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
    from backend.models import FanxiuExchangeActivity

    label = "兽渊_兑换"
    context = runner._behavior_tree_context(ctx, stop_event=stop_event)
    scene_id, _score, _frame = context.sample_scene_once(
        (BEAST_ABYSS_SHOP_SCENE, COMMON_SHOP_DETAIL_SCENE),
        update=True,
    )
    if scene_id == COMMON_SHOP_DETAIL_SCENE:
        context.click_shape_center(COMMON_SHOP_DETAIL_SCENE, "关闭详情")
        yield from context.wait_scene(
            [BEAST_ABYSS_SHOP_SCENE],
            wait=15.0,
            label=f"{label}：关闭遗留商品详情",
        )
    elif scene_id != BEAST_ABYSS_SHOP_SCENE:
        raise RuntimeError(f"{label}：要求从 #536 兑换宝阁开始")

    collection_started_at = datetime.now().astimezone()
    with Session(engine) as session:
        detail = collect_and_store_beast_abyss_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=True,
            collect_runtime_rank=False,
        )
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None:
            raise RuntimeError(f"{label}：刷新后失去本期实例")
        collection_evidence = dict(activity.evidence or {})
        session.commit()
    _validate_fresh_exchange_snapshot(
        detail,
        collection_evidence,
        attempt_started_at=collection_started_at,
        label=label,
    )
    wallet = read_wallet_currency_snapshot(14, allow_discovery=False)
    if wallet.get("source") != "runtime_memory":
        raise RuntimeError(f"{label}：钱包没有提供 Runtime 实时事实")
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
            yield from context.scroll_shape_content(
                BEAST_ABYSS_SHOP_SCENE,
                "商品列表",
                direction="down",
            )

        context.click_shape_center(
            BEAST_ABYSS_SHOP_SCENE,
            f"商品行{action.slot}",
        )
        yield from context.wait_scene(
            [COMMON_SHOP_DETAIL_SCENE],
            wait=15.0,
            label=f"{label}：等待 {action.name} 商品详情",
        )
        _detail_matches(
            context,
            expected_name=action.name,
            expected_price=action.unit_price,
        )
        plus_ten_count, plus_one_count = yunmeng_quantity_clicks(
            action.quantity,
            buying_to_cap=action.clears_row,
        )
        for index in range(plus_ten_count):
            context.click_shape_center_fast(COMMON_SHOP_DETAIL_SCENE, "+10")
            if (index + 1) % 25 == 0:
                yield from context.wait_action_settle(0.05)
        for index in range(plus_one_count):
            context.click_shape_center_fast(COMMON_SHOP_DETAIL_SCENE, "+")
            if (index + 1) % 25 == 0:
                yield from context.wait_action_settle(0.05)
        yield from context.wait_action_settle(0.4)

        expected_total = int(action.quantity) * int(action.unit_price)
        if expected_wallet - expected_total < reserved_tokens:
            raise RuntimeError(
                f"{label}：{action.name} 将突破锁定资源保留额 {reserved_tokens}"
            )
        totals: list[int] = []
        total_text = ""
        for _ in range(3):
            price_frame = context.cur_frame(update=True)
            totals, total_text = context.ocr_numbers_in_shapes(
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
            yield from context.wait_action_settle(0.4)
        if not _ocr_contains_amount(totals, total_text, expected_total):
            raise RuntimeError(
                f"{label}：{action.name} 数量调整后总价未闭环为 {expected_total}"
            )
        yield from context.click_shape_center_then_scene(
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

    verification_started_at = datetime.now().astimezone()
    with Session(engine) as session:
        final_detail = collect_and_store_beast_abyss_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=True,
            collect_runtime_rank=False,
        )
        final_activity = session.get(FanxiuExchangeActivity, activity_id)
        if final_activity is None:
            raise RuntimeError(f"{label}：购买后失去本期实例")
        final_evidence = dict(final_activity.evidence or {})
        session.commit()
    _validate_fresh_exchange_snapshot(
        final_detail,
        final_evidence,
        attempt_started_at=verification_started_at,
        label=label,
    )
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
        final_wallet.get("source") != "runtime_memory"
        or int(final_detail.current_currency) != expected_wallet
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
