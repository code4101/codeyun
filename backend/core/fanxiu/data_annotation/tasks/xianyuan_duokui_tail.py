from __future__ import annotations

"""Settlement-window exchange execution for 仙缘夺魁."""

from datetime import datetime
import re
from typing import Any

from sqlmodel import Session, select

from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.tasks.magic_invasion_tail import (
    _group_ocr_tokens,
)
from backend.core.fanxiu.data_annotation.tasks.yunmeng_tail import (
    _detail_matches,
    plan_exchange_tail_purchases,
    plan_yunmeng_tail_physical_actions,
    yunmeng_quantity_clicks,
)
from backend.core.fanxiu.runtime_gui.magic_invasion import (
    resolve_magic_invasion_bottom_tab,
)


XIANYUAN_SHOP_GEOMETRY_SCENE = 559
COMMON_SHOP_DIALOG_SCENE = 566


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or ""))


def read_xianyuan_shop_wallet_from_ocr(runtime: Any, *, update: bool = True) -> tuple[int, int]:
    """Read the two exact activity-local counters from the open shop header."""

    lines = _group_ocr_tokens(runtime.full_frame_ocr_tokens(update=update))
    current: list[int] = []
    cumulative: list[int] = []
    for line in lines:
        text = _compact(line.get("text"))
        current_match = re.search(r"当前拥有夺魁灵玉(\d+)$", text)
        cumulative_match = re.search(r"活动期间累计夺魁灵玉(\d+)$", text)
        if current_match:
            current.append(int(current_match.group(1)))
        if cumulative_match:
            cumulative.append(int(cumulative_match.group(1)))
    if len(current) != 1 or len(cumulative) != 1:
        visible = " | ".join(_compact(line.get("text")) for line in lines)
        raise RuntimeError(f"仙缘_兑换收尾：钱包标题未唯一对齐：{visible[:1000]}")
    return current[0], cumulative[0]


def _shop_ready(runtime: Any, *, attempts: int = 20, fail_if_missing: bool = True):
    last = ""
    for _ in range(max(1, int(attempts))):
        try:
            read_xianyuan_shop_wallet_from_ocr(runtime, update=True)
            return True
        except RuntimeError as exc:
            last = str(exc)
        yield from runtime.wait_action_settle(1.0)
    if fail_if_missing:
        raise RuntimeError(last or "仙缘_兑换收尾：兑换宝阁未就绪")
    return False


def _open_exchange_tab(runtime: Any, scene: int) -> None:
    lines = _group_ocr_tokens(runtime.full_frame_ocr_tokens(update=True))
    target = resolve_magic_invasion_bottom_tab(
        lines,
        tab_name="兑换宝阁",
        frame_width=900,
        frame_height=1600,
    )
    runtime.click_frame_point(scene, target.x, target.y)


def _store_panel_wallet(
    session: Session,
    *,
    activity: Any,
    current: int,
    cumulative: int,
) -> None:
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    evidence = dict(activity.evidence or {})
    refresh = dict(evidence.get("refresh_status") or {})
    refresh.update({
        "currency": "updated",
        "currency_reason": "",
        "currency_stale": False,
        "currency_captured_at": captured_at,
    })
    evidence.update({
        "refresh_status": refresh,
        "currency_runtime": {
            "source": "exact_open_xianyuan_shop_header_ocr",
            "wallet_type": 23002,
            "same_window_shop_runtime": True,
        },
    })
    activity.current_currency = int(current)
    activity.cumulative_currency = int(cumulative)
    activity.captured_at = captured_at
    activity.evidence = evidence
    session.add(activity)


def execute_xianyuan_duokui_tail_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: Any,
    *,
    occurrence: RankingOccurrence,
):
    from backend.core.fanxiu.activity.exchange_event import list_exchange_activity_snapshot
    from backend.core.fanxiu.activity.xianyuan_duokui import (
        collect_and_store_xianyuan_duokui_activity,
        ensure_xianyuan_duokui_activity,
    )
    from backend.core.fanxiu.data_annotation.effective_time import job_now
    from backend.core.fanxiu.data_annotation.schedule_navigation import select_schedule_activity
    from backend.db import engine
    from backend.models import FanxiuExchangeActivity

    del payload
    label = "仙缘_兑换收尾"
    now = job_now()
    if now.tzinfo is None:
        now = now.astimezone()
    if not occurrence.end_at < now < occurrence.close_at:
        raise RuntimeError(f"{label}：当前不在正式结束后的兑换保留阶段")

    with Session(engine) as session:
        activity_id = ensure_xianyuan_duokui_activity(session)
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or (
            int(activity.cross_count) != int(occurrence.cross_count)
            or activity.end_date != occurrence.end_at.date().isoformat()
        ):
            raise RuntimeError(f"{label}：数据库实例与 Runtime occurrence 未对齐")
        session.commit()

    runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
    already_shop = yield from _shop_ready(runtime, attempts=2, fail_if_missing=False)
    if already_shop:
        runtime.click_shape_center(XIANYUAN_SHOP_GEOMETRY_SCENE, "返回")
        yield from runtime.wait_action_settle(1.0)
    yield from runtime.goto_view(34)
    yield from runtime.goto_view(66)
    anchor = occurrence.end_at.replace(hour=12, minute=0, second=0, microsecond=0)
    yield from select_schedule_activity(
        runtime,
        r"仙缘夺魁",
        enter=True,
        require_runtime_alignment=True,
        now=anchor,
    )
    yield from runtime.wait_view_or_ocr(
        34,
        lambda text: "仙缘夺魁" in text and "活动已结束" in text,
        timeout=30.0,
        label=f"{label}：等待结束态主页",
    )
    _open_exchange_tab(runtime, 34)
    yield from _shop_ready(runtime)

    current, cumulative = read_xianyuan_shop_wallet_from_ocr(runtime, update=True)
    with Session(engine) as session:
        detail = collect_and_store_xianyuan_duokui_activity(session, activity_id=activity_id)
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None:
            raise RuntimeError(f"{label}：活动实例消失")
        _store_panel_wallet(
            session,
            activity=activity,
            current=current,
            cumulative=cumulative,
        )
        session.commit()
        detail = list_exchange_activity_snapshot(
            session,
            activity_type="xianyuan-duokui",
            activity_id=activity_id,
        ).selected_activity
    if detail is None or not bool(detail.exchange_plan.get("budget_ready")):
        raise RuntimeError(f"{label}：同窗口钱包与商店事实未形成可执行预算")

    purchases, retained_locked, planning = plan_exchange_tail_purchases(
        detail,
        run_date=occurrence.end_at.date(),
        label=label,
    )
    actions = plan_yunmeng_tail_physical_actions(detail.shop_items, purchases)
    initial_counts = {int(item.goods_id): int(item.purchased_count) for item in detail.shop_items}
    expected_wallet = int(current)
    executed: list[dict[str, Any]] = []
    for action in actions:
        if stop_event.is_set():
            raise InterruptedError()
        for _ in range(action.scroll_rows):
            runtime.drag_frame_point(
                XIANYUAN_SHOP_GEOMETRY_SCENE, 450, 900, 450, 775, duration_ms=800
            )
            yield from runtime.wait_action_settle(0.25)
        runtime.click_shape_center(XIANYUAN_SHOP_GEOMETRY_SCENE, f"商品行{action.slot}")
        yield from runtime.wait_view(
            COMMON_SHOP_DIALOG_SCENE,
            timeout=15.0,
            label=f"{label}：等待 {action.name} 购买框",
        )
        _detail_matches(runtime, expected_name=action.name, expected_price=action.unit_price)
        plus_ten, plus_one = yunmeng_quantity_clicks(
            action.quantity,
            buying_to_cap=action.clears_row,
        )
        for index in range(plus_ten):
            runtime.click_shape_center_fast(COMMON_SHOP_DIALOG_SCENE, "+10")
            if (index + 1) % 25 == 0:
                yield from runtime.wait_action_settle(0.05)
        for index in range(plus_one):
            runtime.click_shape_center_fast(COMMON_SHOP_DIALOG_SCENE, "+")
            if (index + 1) % 25 == 0:
                yield from runtime.wait_action_settle(0.05)
        yield from runtime.wait_action_settle(0.35)
        cost = int(action.quantity) * int(action.unit_price)
        if expected_wallet - cost < int(planning["reserved_tokens"]):
            raise RuntimeError(f"{label}：{action.name} 将突破锁定资源预留额")
        runtime.click_shape_center(COMMON_SHOP_DIALOG_SCENE, "购买")
        yield from _shop_ready(runtime)
        expected_wallet -= cost
        actual_wallet, _ = read_xianyuan_shop_wallet_from_ocr(runtime, update=True)
        if actual_wallet != expected_wallet:
            raise RuntimeError(
                f"{label}：{action.name} 后余额 {actual_wallet} != {expected_wallet}"
            )
        executed.append({
            "goods_id": action.goods_id,
            "name": action.name,
            "quantity": action.quantity,
            "unit_price": action.unit_price,
        })

    final_current, final_cumulative = read_xianyuan_shop_wallet_from_ocr(runtime, update=True)
    if final_current != int(planning["planned_remaining_tokens"]):
        raise RuntimeError(f"{label}：最终余额没有闭环为 {planning['planned_remaining_tokens']}")
    with Session(engine) as session:
        final_detail = collect_and_store_xianyuan_duokui_activity(session, activity_id=activity_id)
        final_rows = {int(item.goods_id): item for item in final_detail.shop_items}
        for purchase in purchases:
            original = next(item for item in detail.shop_items if int(item.goods_id) == purchase.goods_id)
            if int(original.purchase_limit) < 0:
                continue
            actual = final_rows.get(purchase.goods_id)
            actual_count = int(actual.purchased_count) if actual is not None else -1
            expected_count = initial_counts[purchase.goods_id] + purchase.quantity
            if actual_count != expected_count:
                raise RuntimeError(
                    f"{label}：{purchase.name} 最终购买数 {actual_count} != {expected_count}"
                )
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None:
            raise RuntimeError(f"{label}：活动实例消失")
        _store_panel_wallet(
            session,
            activity=activity,
            current=final_current,
            cumulative=final_cumulative,
        )
        session.commit()

    # Purchase verification is already closed before navigation.  Returning
    # home is best-effort with respect to idempotency: a navigation failure
    # must never cause the irreversible batch to replay.
    try:
        runtime.click_shape_center(XIANYUAN_SHOP_GEOMETRY_SCENE, "返回")
        yield from runtime.wait_action_settle(1.0)
        yield from runtime.goto_view(34)
    except Exception as exc:  # pragma: no cover - exercised by live monitor
        runner._log("warning", f"{label}已完成兑换，但返回 #34 失败：{exc}")

    return {
        "status": "completed",
        "message": (
            f"仙缘 occurrence {occurrence.runtime_id} 兑换完成："
            f"兑换 {len(executed)} 种，余额 {final_current}"
        ),
        "activity_id": activity_id,
        "purchases": executed,
        "planning": planning,
        "currency_remaining": final_current,
        "retained_locked_goods_ids": sorted(retained_locked),
    }


__all__ = [
    "execute_xianyuan_duokui_tail_checkpoint",
    "read_xianyuan_shop_wallet_from_ocr",
]
