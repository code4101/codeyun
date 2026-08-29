from __future__ import annotations

"""Idempotent final shop redemption for one 天地弈局 occurrence."""

import time
from typing import Any, Literal

from sqlmodel import Session, select

from backend.core.fanxiu.activity.exchange_event import list_exchange_activity_snapshot
from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
from backend.core.fanxiu.data_annotation.tasks.exchange_tail_planning import (
    authorize_exchange_purchase,
    exchange_quantity_clicks,
    ocr_contains_amount,
    plan_exchange_tail_physical_actions,
    plan_exchange_tail_purchases,
    verify_exchange_detail,
    verify_exchange_purchase_counts,
    verify_exchange_wallet,
)
from backend.core.fanxiu.runtime_gui.exchange_shop import resolve_exchange_shop_item
from backend.db import engine
from backend.models import FanxiuExchangeActivity


SHOP_GEOMETRY_SCENE = 559
COMMON_PURCHASE_DIALOG_SCENE = 566
TIANDI_YIJU_HOME_SCENE = 677
_RECEIPT_KEY = "tiandi_yiju_exchange_tail"


def _load_persisted_detail(
    occurrence: RankingOccurrence,
) -> tuple[Any, dict[str, Any]]:
    with Session(engine) as session:
        activity = session.exec(
            select(FanxiuExchangeActivity).where(
                FanxiuExchangeActivity.activity_type == "tiandi-yiju",
                FanxiuExchangeActivity.instance_key == occurrence.instance_key,
            )
        ).first()
        if activity is None:
            raise RuntimeError("天地弈局兑换收尾缺少同一 occurrence 的持久化活动")
        detail = list_exchange_activity_snapshot(
            session,
            activity_type="tiandi-yiju",
            activity_id=str(activity.id),
        ).selected_activity
        receipt = dict(dict(activity.evidence or {}).get(_RECEIPT_KEY) or {})
    if detail is None or int(detail.game_activity_id or 0) != int(occurrence.activity_id):
        raise RuntimeError("天地弈局兑换收尾的持久化事实与 occurrence 不一致")
    return detail, receipt


def _refresh_persisted_detail(activity_id: str) -> Any:
    from backend.core.fanxiu.activity.tiandi_yiju import (
        collect_and_store_tiandi_yiju_activity,
    )

    with Session(engine) as session:
        detail = collect_and_store_tiandi_yiju_activity(
            session,
            activity_id=activity_id,
        )
        session.commit()
    return detail


def _store_completion_receipt(
    *,
    activity_id: str,
    occurrence: RankingOccurrence,
    current_currency: int,
) -> None:
    with Session(engine) as session:
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or activity.instance_key != occurrence.instance_key:
            raise RuntimeError("天地弈局兑换收尾写回时活动实例发生切换")
        evidence = dict(activity.evidence or {})
        evidence[_RECEIPT_KEY] = {
            "status": "completed",
            "instance_key": occurrence.instance_key,
            "current_currency": int(current_currency),
        }
        activity.evidence = evidence
        session.add(activity)
        session.commit()


def _is_completed_receipt(
    receipt: dict[str, Any],
    *,
    occurrence: RankingOccurrence,
    current_currency: int,
) -> bool:
    return (
        receipt.get("status") == "completed"
        and receipt.get("instance_key") == occurrence.instance_key
        and int(receipt.get("current_currency") or -1) == int(current_currency)
    )


def _click_planned_product(runtime: Any, *, name: str, unit_price: int) -> None:
    view = runtime.view(SHOP_GEOMETRY_SCENE)
    product_list = view.get_shape("商品列表")
    rows = [view.get_shape(f"商品行{slot}") for slot in range(1, 6)]
    if product_list is None or any(row is None for row in rows):
        raise RuntimeError("天地弈局兑换收尾缺少 #559 商品列表几何")
    target = resolve_exchange_shop_item(
        group_ocr_tokens(runtime.full_frame_ocr_tokens(update=True)),
        product_list_box=product_list.box(),
        product_row_boxes=[row.box() for row in rows],
        expected_name=name,
        expected_unit_price=unit_price,
    )
    runtime.click_frame_point(SHOP_GEOMETRY_SCENE, target.x, target.y)


def _wait_purchase_persisted(
    runtime: Any,
    *,
    activity_id: str,
    expected_wallet: int,
    initial_shop_items: Any,
    executed_purchases: Any,
    timeout: float = 20.0,
):
    deadline = time.monotonic() + float(timeout)
    last = ""
    while True:
        try:
            detail = _refresh_persisted_detail(activity_id)
            verify_exchange_wallet(
                expected_wallet,
                {"持久化钱包": detail.current_currency},
                label="天地弈局_兑换收尾",
                stage="购买后",
            )
            verify_exchange_purchase_counts(
                initial_shop_items,
                detail.shop_items,
                executed_purchases,
                label="天地弈局_兑换收尾",
            )
            return detail
        except (RuntimeError, ValueError) as exc:
            last = str(exc)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"天地弈局购买后持久化闭环超时：{last}")
        yield from runtime.wait_action_settle(0.8)


def execute_tiandi_yiju_exchange_tail(
    runner: Any,
    ctx: dict[str, Any],
    *,
    occurrence: RankingOccurrence,
    stop_event: Any,
    runtime: Any | None = None,
    start: Literal["home", "shop"] = "home",
    return_to_world: bool = False,
):
    """Redeem from one persisted occurrence; a completed replay does no GUI work."""

    label = "天地弈局_兑换收尾"
    detail, receipt = _load_persisted_detail(occurrence)
    purchases, retained_locked, planning = plan_exchange_tail_purchases(
        detail,
        run_date=occurrence.end_at.date(),
        label=label,
    )
    if not purchases and _is_completed_receipt(
        receipt,
        occurrence=occurrence,
        current_currency=int(detail.current_currency),
    ):
        return {
            "status": "completed",
            "activity_id": str(detail.id),
            "purchases": [],
            "planning": planning,
            "currency_remaining": int(detail.current_currency),
            "retained_locked_goods_ids": sorted(retained_locked),
        }

    if runtime is None:
        runtime = runner._fanxiu_runtime(
            ctx,
            ctx.get("asset_tree_path"),
            stop_event=stop_event,
        )
        from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju import (
            enter_tiandi_yiju_occurrence_home,
        )

        yield from enter_tiandi_yiju_occurrence_home(runtime, occurrence=occurrence)
        start = "home"
    if start == "home":
        runtime.click_shape_center(TIANDI_YIJU_HOME_SCENE, "兑换宝阁")
        yield from runtime.wait_action_settle(0.8)
    elif start != "shop":
        raise ValueError(f"天地弈局兑换收尾不支持起点 {start!r}")

    # Entering the shop makes wallet and purchase counts a same-window fact.
    detail = _refresh_persisted_detail(str(detail.id))
    purchases, retained_locked, planning = plan_exchange_tail_purchases(
        detail,
        run_date=occurrence.end_at.date(),
        label=label,
    )
    actions = plan_exchange_tail_physical_actions(
        detail.shop_items,
        purchases,
        label=label,
    )
    initial_shop_items = tuple(detail.shop_items)
    expected_wallet = int(detail.current_currency)
    executed: list[dict[str, Any]] = []

    if actions:
        for _ in range(8):
            runtime.drag_frame_point(
                SHOP_GEOMETRY_SCENE, 450, 520, 450, 1100, duration_ms=1000
            )
            yield from runtime.wait_action_settle(0.2)
    for action in actions:
        if stop_event.is_set():
            raise InterruptedError()
        for _ in range(action.scroll_rows):
            runtime.drag_frame_point(
                SHOP_GEOMETRY_SCENE, 450, 900, 450, 720, duration_ms=1000
            )
            yield from runtime.wait_action_settle(0.25)
        _click_planned_product(
            runtime,
            name=action.name,
            unit_price=action.unit_price,
        )
        yield from runtime.wait_view(
            COMMON_PURCHASE_DIALOG_SCENE,
            timeout=15.0,
            label=f"{label}：等待 {action.name} 购买框",
        )
        verify_exchange_detail(
            runtime,
            expected_name=action.name,
            expected_price=action.unit_price,
            label=label,
        )
        plus_ten, plus_one = exchange_quantity_clicks(
            action.quantity,
            buying_to_cap=action.clears_row,
        )
        for index in range(plus_ten):
            runtime.click_shape_center_fast(COMMON_PURCHASE_DIALOG_SCENE, "+10")
            if (index + 1) % 25 == 0:
                yield from runtime.wait_action_settle(0.05)
        for index in range(plus_one):
            runtime.click_shape_center_fast(COMMON_PURCHASE_DIALOG_SCENE, "+")
            if (index + 1) % 25 == 0:
                yield from runtime.wait_action_settle(0.05)
        yield from runtime.wait_action_settle(0.35)
        cost, remaining_wallet = authorize_exchange_purchase(
            current_wallet=expected_wallet,
            quantity=action.quantity,
            unit_price=action.unit_price,
            reserved_tokens=planning["reserved_tokens"],
            name=action.name,
            label=label,
        )
        totals, total_text = runtime.ocr_numbers_in_shapes(
            COMMON_PURCHASE_DIALOG_SCENE,
            ("价格",),
            padding=8,
        )
        if not ocr_contains_amount(totals, total_text, cost):
            raise RuntimeError(f"{label}：{action.name} 总价未闭环为 {cost}")
        runtime.click_shape_center(COMMON_PURCHASE_DIALOG_SCENE, "购买")
        expected_wallet = remaining_wallet
        executed_actions = [*actions[:len(executed)], action]
        detail = yield from _wait_purchase_persisted(
            runtime,
            activity_id=str(detail.id),
            expected_wallet=expected_wallet,
            initial_shop_items=initial_shop_items,
            executed_purchases=executed_actions,
        )
        executed.append({
            "goods_id": int(action.goods_id),
            "name": str(action.name),
            "quantity": int(action.quantity),
            "unit_price": int(action.unit_price),
        })

    remaining, retained_locked, final_planning = plan_exchange_tail_purchases(
        detail,
        run_date=occurrence.end_at.date(),
        label=label,
    )
    if remaining or int(detail.current_currency) != int(
        planning["planned_remaining_tokens"]
    ):
        raise RuntimeError("天地弈局兑换收尾后仍存在未核销购买计划")
    verify_exchange_wallet(
        int(planning["planned_remaining_tokens"]),
        {"持久化钱包": detail.current_currency, "执行账本": expected_wallet},
        label=label,
    )
    verify_exchange_purchase_counts(
        initial_shop_items,
        detail.shop_items,
        actions,
        label=label,
    )
    _store_completion_receipt(
        activity_id=str(detail.id),
        occurrence=occurrence,
        current_currency=int(detail.current_currency),
    )
    try:
        runtime.click_shape_center(SHOP_GEOMETRY_SCENE, "返回")
        yield from runtime.wait_action_settle(0.8)
        if return_to_world:
            yield from runtime.goto_view(34)
    except Exception as exc:  # pragma: no cover - live navigation safeguard
        if runner is not None:
            runner._log("warning", f"{label}已核销，但离开兑换宝阁失败：{exc}")
    return {
        "status": "completed",
        "activity_id": str(detail.id),
        "purchases": executed,
        "planning": final_planning,
        "currency_remaining": int(detail.current_currency),
        "retained_locked_goods_ids": sorted(retained_locked),
    }


def execute_tiandi_yiju_exchange_tail_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: Any,
    *,
    occurrence: RankingOccurrence,
):
    del payload
    return (yield from execute_tiandi_yiju_exchange_tail(
        runner,
        ctx,
        occurrence=occurrence,
        stop_event=stop_event,
        return_to_world=True,
    ))


__all__ = [
    "execute_tiandi_yiju_exchange_tail",
    "execute_tiandi_yiju_exchange_tail_checkpoint",
]
