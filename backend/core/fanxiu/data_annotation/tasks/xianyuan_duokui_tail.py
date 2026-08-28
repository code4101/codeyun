from __future__ import annotations

"""Settlement-window exchange execution for 仙缘夺魁."""

from datetime import datetime
import re
from typing import Any

from sqlmodel import Session, select

from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.ocr_spatial import (
    group_ocr_tokens as _group_ocr_tokens,
)
from backend.core.fanxiu.data_annotation.tasks.exchange_tail_planning import (
    exchange_quantity_clicks as yunmeng_quantity_clicks,
    ocr_contains_amount as _ocr_contains_amount,
    plan_exchange_tail_physical_actions as plan_yunmeng_tail_physical_actions,
    plan_exchange_tail_purchases,
    verify_exchange_detail as _detail_matches,
)
from backend.core.fanxiu.runtime_gui.activity_bottom_tab import (
    resolve_vertical_bottom_tab as resolve_magic_invasion_bottom_tab,
)


XIANYUAN_SHOP_GEOMETRY_SCENE = 559
COMMON_SHOP_DIALOG_SCENE = 566


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or ""))


def read_xianyuan_shop_wallet_from_ocr(runtime: Any, *, update: bool = True) -> tuple[int, int]:
    """Read the two exact activity-local counters from the open shop header."""

    lines = _group_ocr_tokens(runtime.full_frame_ocr_tokens(update=update))
    numeric_lines = [
        line
        for line in lines
        if re.fullmatch(r"\d+", _compact(line.get("text")))
    ]

    def amount_after(label: str) -> int:
        inline = []
        for line in lines:
            text = _compact(line.get("text"))
            match = re.fullmatch(re.escape(label) + r"(\d+)", text)
            if match:
                inline.append(int(match.group(1)))
        if len(inline) == 1:
            return inline[0]
        if len(inline) > 1:
            raise RuntimeError(f"{label}同行合并数值命中数为 {len(inline)}")
        labels = [line for line in lines if _compact(line.get("text")) == label]
        if len(labels) != 1:
            raise RuntimeError(f"{label}命中数为 {len(labels)}")
        row = labels[0]
        center_y = float(row.get("y") or 0) + float(row.get("h") or 0) / 2
        right = float(row.get("x") or 0) + float(row.get("w") or 0)
        candidates = [
            line
            for line in numeric_lines
            if float(line.get("x") or 0) >= right - 8
            and abs(
                float(line.get("y") or 0)
                + float(line.get("h") or 0) / 2
                - center_y
            ) <= 24
        ]
        if len(candidates) != 1:
            raise RuntimeError(f"{label}同行数值命中数为 {len(candidates)}")
        return int(_compact(candidates[0].get("text")))

    try:
        current = amount_after("当前拥有夺魁灵玉")
        cumulative = amount_after("活动期间累计夺魁灵玉")
        if current < 0 or cumulative < 0 or current > cumulative:
            raise RuntimeError(f"钱包数值不满足 0 <= 当前 {current} <= 累计 {cumulative}")
    except RuntimeError as exc:
        visible = " | ".join(_compact(line.get("text")) for line in lines)
        raise RuntimeError(
            f"仙缘_兑换收尾：钱包标题未唯一对齐：{exc}；{visible[:1000]}"
        ) from exc
    return current, cumulative


def _shop_ready(runtime: Any, *, attempts: int = 20, fail_if_missing: bool = True):
    last = ""
    previous: tuple[int, int] | None = None
    for _ in range(max(1, int(attempts))):
        try:
            wallet = read_xianyuan_shop_wallet_from_ocr(runtime, update=True)
            if wallet == previous:
                return wallet
            previous = wallet
            last = f"仙缘_兑换收尾：钱包等候连续两帧一致，当前 {wallet}"
        except RuntimeError as exc:
            last = str(exc)
            previous = None
        yield from runtime.wait_action_settle(1.0)
    if fail_if_missing:
        raise RuntimeError(last or "仙缘_兑换收尾：兑换宝阁未就绪")
    return None


def _open_exchange_tab(runtime: Any, scene: int) -> None:
    lines = _group_ocr_tokens(runtime.full_frame_ocr_tokens(update=True))
    target = resolve_magic_invasion_bottom_tab(
        lines,
        tab_name="兑换宝阁",
        frame_width=900,
        frame_height=1600,
    )
    runtime.click_frame_point(scene, target.x, target.y)


def _click_exact_compact_shop_name(runtime: Any, expected_name: str) -> None:
    """Click one visible shop name after punctuation-insensitive exact matching."""

    target = _compact(expected_name)
    lines = _group_ocr_tokens(runtime.full_frame_ocr_tokens(update=True))
    matches = [
        line
        for line in lines
        if _compact(line.get("text")) == target
        and 120 <= float(line.get("x") or 0) <= 700
        and 250 <= float(line.get("y") or 0) <= 1400
    ]
    if len(matches) != 1:
        visible = " | ".join(_compact(line.get("text")) for line in lines)
        raise RuntimeError(
            f"仙缘_兑换收尾：商品名 {expected_name} 唯一命中数为 {len(matches)}；"
            f"{visible[:1000]}"
        )
    row = matches[0]
    runtime.click_frame_point(
        XIANYUAN_SHOP_GEOMETRY_SCENE,
        float(row.get("x") or 0) + float(row.get("w") or 0) / 2,
        float(row.get("y") or 0) + float(row.get("h") or 0) / 2,
    )


def _wait_ended_home_ready(runtime: Any, *, attempts: int = 20):
    last = ""
    for _ in range(max(1, int(attempts))):
        tokens = runtime.full_frame_ocr_tokens(update=True)
        last = _compact("".join(str(token.get("text") or "") for token in tokens))
        if (
            "仙缘夺魁" in last
            and "活动已结束" in last
            and "兑换宝阁" in last
        ):
            return True
        yield from runtime.wait_action_settle(1.0)
    raise RuntimeError(f"仙缘_兑换收尾：结束态主页业务文本未就绪：{last[:1000]}")


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
    # The collector may have materialized this same schema moments earlier
    # while the generic WalletVO path was unavailable.  Invalidate only that
    # derived plan so the next read recomputes budgets from the exact panel
    # counters and the already-current V_ShowList purchase progress.
    exchange_plan = dict(evidence.get("exchange_plan") or {})
    exchange_plan["schema"] = 0
    evidence["exchange_plan"] = exchange_plan
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
    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        resolve_schedule_runtime_activity_targets,
        runtime_activity_entities_for_date,
    )
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
    # Use full-frame OCR before any inherited shape OCR touches this frame.
    # The current #66 variant exposes a healthy header/calendar to the full
    # detector while the two cropped shape reads may both return empty.
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError(f"{label}：Runtime 日程不可用或不完整")
    ui_today = datetime.now().astimezone().date()
    day_offset = (occurrence.end_at.date() - ui_today).days
    full_lines: list[dict[str, Any]] = []
    for _attempt in range(5):
        full_lines = _group_ocr_tokens(runtime.full_frame_ocr_tokens(update=True))
        visible = [_compact(line.get("text")) for line in full_lines]
        if (
            any("今天" in text for text in visible)
            and any("仙缘夺魁" in text for text in visible)
            and any("跨服8" in text for text in visible)
        ):
            break
        yield from runtime.wait_action_settle(1.0)
    header_lines = [line for line in full_lines if 190 <= float(line["y"]) < 340]
    calendar_lines = [line for line in full_lines if 295 <= float(line["y"]) < 750]
    entities = runtime_activity_entities_for_date(
        schedule,
        r"仙缘夺魁",
        target_date=occurrence.end_at.date(),
    )
    exact_entities = tuple(
        entity
        for entity in entities
        if occurrence.runtime_id in str(entity.key).split("|")
    )
    if len(exact_entities) != 1:
        raise RuntimeError(
            f"{label}：Runtime 日程未唯一包含 occurrence {occurrence.runtime_id}"
        )
    targets = resolve_schedule_runtime_activity_targets(
        header_lines=header_lines,
        calendar_lines=calendar_lines,
        runtime_entities=exact_entities,
        day_offset=day_offset,
        anchor_date=ui_today,
    )
    exact_targets = [
        target
        for target in targets
        if occurrence.runtime_id in target.runtime_key.split("|")
    ]
    if len(exact_targets) != 1:
        raise RuntimeError(
            f"{label}：#66 未唯一对齐 occurrence {occurrence.runtime_id}"
        )
    runtime.click_frame_point(66, exact_targets[0].x, exact_targets[0].y)
    yield from runtime.wait_action_settle(0.8)
    yield from _wait_ended_home_ready(runtime)
    _open_exchange_tab(runtime, 34)
    current, cumulative = yield from _shop_ready(runtime)
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

    # The game preserves this shop's scroll offset after leaving and re-entering.
    # Normalize to its clamped top boundary so the Runtime order and the five-row
    # physical window start from the same fact.  These are navigation-only drags;
    # no irreversible action occurs before the row OCR gate below succeeds.
    for _ in range(8):
        runtime.drag_frame_point(
            XIANYUAN_SHOP_GEOMETRY_SCENE, 450, 520, 450, 1100, duration_ms=1000
        )
        yield from runtime.wait_action_settle(0.2)

    for action in actions:
        if stop_event.is_set():
            raise InterruptedError()
        for _ in range(action.scroll_rows):
            runtime.drag_frame_point(
                XIANYUAN_SHOP_GEOMETRY_SCENE, 450, 900, 450, 720, duration_ms=1000
            )
            yield from runtime.wait_action_settle(0.25)
        # Near the bottom the GUI can expose a sixth partial row while the
        # generic five-slot planner is clamped.  Click the exact OCR name,
        # rather than a slot center that may fall between the fifth and sixth
        # cards.  Runtime detail verification still closes the identity gate.
        _click_exact_compact_shop_name(runtime, action.name)
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
            yield from runtime.wait_action_settle(0.08)
        for index in range(plus_one):
            runtime.click_shape_center_fast(COMMON_SHOP_DIALOG_SCENE, "+")
            yield from runtime.wait_action_settle(0.08)
        yield from runtime.wait_action_settle(0.35)
        cost = int(action.quantity) * int(action.unit_price)
        if expected_wallet - cost < int(planning["reserved_tokens"]):
            raise RuntimeError(f"{label}：{action.name} 将突破锁定资源预留额")
        totals, total_text = runtime.ocr_numbers_in_shapes(
            COMMON_SHOP_DIALOG_SCENE,
            ("价格",),
            padding=8,
        )
        if not _ocr_contains_amount(totals, total_text, cost):
            raise RuntimeError(
                f"{label}：{action.name} 数量调整后总价未闭环为 {cost}，拒绝购买"
            )
        runtime.click_shape_center(COMMON_SHOP_DIALOG_SCENE, "购买")
        actual_wallet, _ = yield from _shop_ready(runtime)
        expected_wallet -= cost
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

    final_current, final_cumulative = yield from _shop_ready(runtime)
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
