from __future__ import annotations

"""Final Yunmeng ranking refresh and Runtime-aligned exchange redemption."""

from datetime import date, datetime
from typing import Any

from backend.core.fanxiu.data_annotation.tasks.exchange_tail_planning import (
    ExchangeTailPhysicalAction as YunmengTailPhysicalAction,
    ExchangeTailPurchase as YunmengTailPurchase,
    exchange_quantity_clicks as yunmeng_quantity_clicks,
    ocr_contains_amount as _ocr_contains_amount,
    plan_exchange_tail_physical_actions as plan_yunmeng_tail_physical_actions,
    plan_exchange_tail_purchases,
    verify_exchange_detail as _detail_matches,
)


YUNMENG_HOME_SCENE = 558
YUNMENG_SHOP_SCENE = 559


def plan_yunmeng_tail_purchases(
    detail: Any,
    *,
    run_date: date,
) -> tuple[list[YunmengTailPurchase], set[int], dict[str, Any]]:
    """Compatibility wrapper for the original Yunmeng task/tests."""

    return plan_exchange_tail_purchases(
        detail,
        run_date=run_date,
        label="云梦_收尾",
    )


def store_yunmeng_final_rankings(activity_id: str) -> dict[str, Any]:
    """Persist the current personal and plane facts before shop redemption."""

    from sqlmodel import Session, select

    from backend.core.fanxiu.activity.yunmeng_exchange import (
        collect_and_store_yunmeng_exchange_activity,
    )
    from backend.db import engine
    from backend.models import FanxiuExchangeRanking

    with Session(engine) as session:
        detail = collect_and_store_yunmeng_exchange_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=False,
        )
        session.flush()
        rows = session.exec(
            select(FanxiuExchangeRanking).where(
                FanxiuExchangeRanking.activity_id == activity_id
            )
        ).all()
        counts = {
            scope: sum(row.ranking_scope == scope for row in rows)
            for scope in ("personal", "plane")
        }
        evidence = dict(detail.evidence or {})
        current_related = {
            str(value)
            for value in evidence.get("current_related_ranking_scopes") or []
        }
        if counts["personal"] <= 0:
            raise RuntimeError("云梦_收尾：最终个人榜没有成功更新")
        if counts["plane"] <= 0 or "plane" not in current_related:
            raise RuntimeError("云梦_收尾：最终位面榜没有成功更新为本期事实")
        session.commit()
        return {
            "activity_id": activity_id,
            "personal_count": counts["personal"],
            "plane_count": counts["plane"],
            "captured_at": str(detail.captured_at or ""),
        }


def refresh_yunmeng_final_rankings(
    runtime: Any,
    *,
    activity_id: str,
):
    """Refresh both final ranking tabs and then materialize their facts."""

    # Entering #565 loads the personal tab.  Click it explicitly so this
    # reusable step also works when the page restores the previously selected
    # plane tab, then open plane once before consuming the packet facts.
    runtime.click_shape_center(565, "个人")
    yield from runtime.wait_action_settle(1.0)
    runtime.click_shape_center(565, "位面")
    yield from runtime.wait_action_settle(1.0)
    return store_yunmeng_final_rankings(activity_id)


def execute_yunmeng_tail_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: Any,
):
    from sqlmodel import Session, col, select

    from backend.db import engine
    from backend.models import FanxiuExchangeActivity
    from backend.core.fanxiu.activity.yunmeng_exchange import (
        collect_and_store_yunmeng_exchange_activity,
    )
    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        select_schedule_activity,
    )
    from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot

    label = "云梦_收尾"
    scheduler_task_id = str(
        payload.get("__scheduler_task_id")
        or ctx.get("scheduler_task_id")
        or ""
    )
    with Session(engine) as session:
        activity = session.exec(
            select(FanxiuExchangeActivity)
            .where(FanxiuExchangeActivity.activity_type == "yunmeng-trial")
            .order_by(col(FanxiuExchangeActivity.end_date).desc())
        ).first()
        if activity is None:
            raise RuntimeError(f"{label}：尚无云梦通用活动实例")
        activity_id = str(activity.id)
        end_date = date.fromisoformat(activity.end_date)
        close_date = date.fromisoformat(str((activity.evidence or {}).get("period_close_panel_date") or activity.end_date))
        if not end_date < date.today() <= close_date:
            # ``yunmeng-tail`` was retired when ranking-lifecycle became the
            # sole Scheduler owner.  A legacy persisted instance may survive
            # in an already-running service, however.  Its expired window is
            # a normal terminal business state, not a technical failure that
            # should retry forever.  Do not clear the canonical parent here:
            # it owns all ranking occurrences and computes its own next time.
            if scheduler_task_id == "yunmeng-tail":
                runner._persist_scheduler_task_next_time(scheduler_task_id, None)
                return {
                    "result": "success",
                    "message": f"{label}：旧调度实例已退役，当前兑换保留期已结束",
                    "activity_id": activity_id,
                    "current_scene": None,
                }
            raise RuntimeError(f"{label}：当前不在正式结束后的兑换保留阶段")

    runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
    initial_scene, _score, _frame = runtime.current_scene((565, 566), update=True)
    if initial_scene == 565:
        runtime.click_shape_center(565, "云梦试剑")
        yield from runtime.wait_action_settle(1.0)
    elif initial_scene == 566:
        runtime.click_shape_center(566, "关闭详情")
        yield from runtime.wait_action_settle(1.0)

    # Always normalize through the world anchor before selecting the dated occurrence.
    yield from runtime.goto_view(34)
    yield from runtime.goto_view(66)
    anchor = datetime.now().astimezone().replace(
        year=end_date.year, month=end_date.month, day=end_date.day, hour=12, minute=0, second=0
    )
    yield from select_schedule_activity(
        runtime,
        r"云梦试剑",
        enter=True,
        require_runtime_alignment=True,
        now=anchor,
    )
    yield from runtime.wait_view_or_ocr(
        YUNMENG_HOME_SCENE,
        lambda text: "云梦试剑" in text and "挑战次数" in text,
        timeout=30.0,
        label=f"{label}：等待云梦结束态主页",
    )

    runtime.click_shape_center(YUNMENG_HOME_SCENE, "榜单")
    yield from runtime.wait_view(565, timeout=20.0, label=f"{label}：等待最终榜单")
    ranking_summary = yield from refresh_yunmeng_final_rankings(
        runtime,
        activity_id=activity_id,
    )
    runtime.click_shape_center(565, "云梦试剑")
    yield from runtime.wait_view_or_ocr(
        YUNMENG_HOME_SCENE,
        lambda text: "挑战次数" in text,
        timeout=20.0,
        label=f"{label}：榜单刷新后返回云梦主页",
    )

    runtime.click_shape_center(YUNMENG_HOME_SCENE, "兑换宝阁")
    yield from runtime.wait_view(YUNMENG_SHOP_SCENE, timeout=20.0, label=f"{label}：进入兑换宝阁")

    with Session(engine) as session:
        detail = collect_and_store_yunmeng_exchange_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=True,
        )
        session.commit()
    wallet = read_wallet_currency_snapshot(19, allow_discovery=False)
    expected_wallet = int(wallet["exchange_currency"])
    if int(detail.current_currency) != expected_wallet:
        raise RuntimeError(
            f"{label}：商店 Runtime 与钱包不是同窗口事实："
            f"activity={detail.current_currency}, wallet={expected_wallet}"
        )
    executed: list[dict[str, Any]] = []
    purchases, retained_locked, planning = plan_exchange_tail_purchases(
        detail,
        run_date=date.today(),
        label=label,
    )
    actions = plan_yunmeng_tail_physical_actions(detail.shop_items, purchases)
    initial_counts = {
        int(row.goods_id): int(row.purchased_count)
        for row in detail.shop_items
    }
    reserved_tokens = int(planning["reserved_tokens"])

    # Entering the shop produces its first physical window.  Traverse only
    # downward from there; do not repeatedly drag to a guessed "top".
    for action in actions:
        if stop_event.is_set():
            raise InterruptedError()
        for _ in range(action.scroll_rows):
            runtime.drag_frame_point(
                YUNMENG_SHOP_SCENE, 450, 900, 450, 810, duration_ms=800
            )
            yield from runtime.wait_action_settle(0.25)

        runtime.click_shape_center(YUNMENG_SHOP_SCENE, f"商品行{action.slot}")
        yield from runtime.wait_view(566, timeout=15.0, label=f"{label}：等待商品详情")
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
            runtime.click_shape_center_fast(566, "+10")
            if (index + 1) % 25 == 0:
                yield from runtime.wait_action_settle(0.05)
        for index in range(plus_one_count):
            runtime.click_shape_center_fast(566, "+")
            if (index + 1) % 25 == 0:
                yield from runtime.wait_action_settle(0.05)
        yield from runtime.wait_action_settle(0.4)

        expected_total = action.quantity * action.unit_price
        if expected_wallet - expected_total < reserved_tokens:
            raise RuntimeError(
                f"{label}：{action.name} 将突破锁定资源保留额 {reserved_tokens}"
            )
        totals, total_text = runtime.ocr_numbers_in_shapes(566, ("价格",), padding=8)
        if not _ocr_contains_amount(totals, total_text, expected_total):
            raise RuntimeError(
                f"{label}：{action.name} 数量调整后总价未闭环为 {expected_total}"
            )
        yield from runtime.click_shape_center_then_view(
            566, "购买", YUNMENG_SHOP_SCENE, timeout=15.0,
            label=f"{label}：购买 {action.name} 后返回宝阁",
        )
        expected_wallet -= expected_total
        executed.append({
            "goods_id": action.goods_id,
            "name": action.name,
            "quantity": action.quantity,
            "unit_price": action.unit_price,
        })

    if expected_wallet != int(planning["planned_remaining_tokens"]):
        raise RuntimeError(
            f"{label}：物理动作未核销完整理论预算："
            f"expected={planning['planned_remaining_tokens']}, actual={expected_wallet}"
        )

    # One final authoritative read closes the batch.  Finite rows have a
    # reliable accumulated count; unlimited rows are closed by the wallet,
    # because their GUI row intentionally remains and cannot signal completion.
    with Session(engine) as session:
        final_detail = collect_and_store_yunmeng_exchange_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=True,
        )
        session.commit()
    final_rows = {int(row.goods_id): row for row in final_detail.shop_items}
    detail_rows = {int(row.goods_id): row for row in detail.shop_items}
    for purchase in purchases:
        original = detail_rows[purchase.goods_id]
        if int(original.purchase_limit) < 0:
            continue
        expected_count = initial_counts[purchase.goods_id] + purchase.quantity
        actual = final_rows.get(purchase.goods_id)
        actual_count = int(actual.purchased_count) if actual is not None else -1
        if actual_count != expected_count:
            raise RuntimeError(
                f"{label}：{purchase.name} 最终 Runtime 购买数 {actual_count} != {expected_count}"
            )
    final_wallet = read_wallet_currency_snapshot(19, allow_discovery=False)
    if (
        int(final_detail.current_currency) != expected_wallet
        or int(final_wallet["exchange_currency"]) != expected_wallet
    ):
        raise RuntimeError(
            f"{label}：最终钱包未闭环为 {expected_wallet}："
            f"activity={final_detail.current_currency}, wallet={final_wallet['exchange_currency']}"
        )
    return {
        "result": "success",
        "message": f"{label}完成：最终榜单已刷新，兑换 {len(executed)} 种，保留锁定 {len(retained_locked)} 种",
        "activity_id": activity_id,
        "purchases": executed,
        "final_rankings": ranking_summary,
        "planning": planning,
        "currency_remaining": int(final_detail.current_currency),
        "current_scene": YUNMENG_SHOP_SCENE,
    }


__all__ = [
    "YunmengTailPurchase",
    "YunmengTailPhysicalAction",
    "execute_yunmeng_tail_job",
    "plan_exchange_tail_purchases",
    "plan_yunmeng_tail_physical_actions",
    "plan_yunmeng_tail_purchases",
    "refresh_yunmeng_final_rankings",
    "store_yunmeng_final_rankings",
    "yunmeng_quantity_clicks",
]
