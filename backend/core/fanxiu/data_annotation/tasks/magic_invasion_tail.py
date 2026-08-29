from __future__ import annotations

"""Settlement-window exchange tail for one Magic Invasion occurrence."""

from datetime import date, datetime
import re
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.ocr_spatial import (
    group_ocr_tokens as _group_ocr_tokens,
)
from backend.core.fanxiu.data_annotation.tasks.exchange_tail_planning import (
    authorize_exchange_purchase,
    exchange_quantity_clicks,
    plan_exchange_tail_physical_actions,
    plan_exchange_tail_purchases,
    verify_exchange_purchase_counts,
    verify_exchange_wallet,
)
from backend.core.fanxiu.runtime_gui.activity_bottom_tab import (
    resolve_vertical_bottom_tab as resolve_magic_invasion_bottom_tab,
)
from backend.core.fanxiu.runtime_gui import ocr_name_similarity


MAGIC_SHOP_SCENE = 519
MAGIC_ENDED_HOME_SCENE = 522
COMMON_SHOP_DIALOG_SCENE = 566


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or ""))


def _open_exchange_tab(runtime: Any, scene: int) -> None:
    tokens = runtime.full_frame_ocr_tokens(update=True)
    target = resolve_magic_invasion_bottom_tab(
        _group_ocr_tokens(tokens),
        tab_name="兑换宝阁",
        frame_width=900,
        frame_height=1600,
    )
    runtime.click_frame_point(scene, target.x, target.y)


def _exchange_shop_business_ready(lines: list[dict[str, Any]]) -> bool:
    visible = [_compact(line.get("text")) for line in lines]
    title_ready = any(
        "兑换宝阁" in text
        and float(line.get("y") or 0) < 500
        and float(line.get("w") or 0) > float(line.get("h") or 0)
        for text, line in zip(visible, lines)
    )
    wallet_ready = any(
        re.search(r"当前拥有(?:位面)?魔晶", text) is not None
        for text in visible
    )
    total_ready = any(
        re.search(r"活动期间累计(?:位面)?魔晶", text) is not None
        for text in visible
    )
    try:
        resolve_magic_invasion_bottom_tab(
            lines,
            tab_name="兑换宝阁",
            frame_width=900,
            frame_height=1600,
        )
        tab_ready = True
    except RuntimeError:
        tab_ready = False
    return title_ready and wallet_ready and total_ready and tab_ready


def _wait_exchange_shop_ready(
    runtime: Any,
    *,
    label: str,
    attempts: int = 20,
    fail_if_missing: bool = True,
):
    """Wait for the shop by business text when the strict View scores 82%."""

    last_visible = ""
    for _attempt in range(max(1, int(attempts))):
        lines = _group_ocr_tokens(runtime.full_frame_ocr_tokens(update=True))
        visible = [_compact(line.get("text")) for line in lines]
        last_visible = " | ".join(text for text in visible if text)
        if _exchange_shop_business_ready(lines):
            return True
        yield from runtime.wait_action_settle(1.0)
    if not fail_if_missing:
        return False
    raise RuntimeError(
        f"{label}：兑换宝阁业务终态未就绪：{last_visible[:1200]}"
    )


def _ui_calendar_day_offset(*, target_date: date, ui_today: date) -> int:
    """Map an occurrence date onto the currently rendered #66 calendar.

    ``job_now()`` is a business clock and may intentionally be advanced by a
    planned Scheduler run.  The game's ``今天`` column, however, follows the
    device wall clock.  Mixing the two selects yesterday's occurrence during
    an early 00:30 settlement rehearsal.
    """

    return (target_date - ui_today).days


def _resolve_exact_magic_calendar_fallback(
    *,
    calendar_lines: list[dict[str, Any]],
    runtime_entity: Any,
    day_offset: int,
    target_x: float,
) -> tuple[Any, ...]:
    """Recover one noisy title only when the instance qualifier stays exact."""

    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        ScheduleActivityTarget,
    )

    payload = runtime_entity.payload
    activity_name = str(payload.get("name") or "魔道入侵")
    qualifier = str(
        payload.get("littleName")
        or payload.get("little_name")
        or payload.get("subtitle")
        or ""
    ).strip()
    if not qualifier:
        return ()
    title_rows = [
        row
        for row in calendar_lines
        if ocr_name_similarity(activity_name, str(row.get("text") or "")) >= 0.68
    ]
    qualifier_rows = [
        row
        for row in calendar_lines
        if ocr_name_similarity(qualifier, str(row.get("text") or "")) >= 0.90
    ]
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for title in title_rows:
        title_x = float(title.get("x") or 0) + float(title.get("w") or 0) / 2
        title_y = float(title.get("y") or 0) + float(title.get("h") or 0) / 2
        for subtitle in qualifier_rows:
            subtitle_x = float(subtitle.get("x") or 0) + float(subtitle.get("w") or 0) / 2
            subtitle_y = float(subtitle.get("y") or 0) + float(subtitle.get("h") or 0) / 2
            if abs(title_x - subtitle_x) <= 90 and 0 < subtitle_y - title_y <= 80:
                pairs.append((title, subtitle))
    if len(pairs) != 1:
        return ()
    title, subtitle = pairs[0]
    return (
        ScheduleActivityTarget(
            day_offset=int(day_offset),
            x=float(target_x),
            y=float(title.get("y") or 0) + float(title.get("h") or 0) / 2,
            matched_text=(
                f"{str(title.get('text') or '').strip()} "
                f"{str(subtitle.get('text') or '').strip()}"
            ).strip(),
            runtime_key=str(runtime_entity.key),
            alignment_score=1.0,
        ),
    )


def _verify_dialog(runtime: Any, *, name: str, unit_price: int) -> None:
    title = runtime.ocr_text_in_shapes(
        COMMON_SHOP_DIALOG_SCENE,
        ("商品标题",),
        padding=10,
    )
    if ocr_name_similarity(_compact(name), _compact(title)) < 0.78:
        raise RuntimeError(f"魔道_兑换收尾：购买框商品未对齐 {name}：{title}")
    values, text = runtime.ocr_numbers_in_shapes(
        COMMON_SHOP_DIALOG_SCENE,
        ("价格",),
        padding=8,
    )
    digits = re.sub(r"\D+", "", str(text or ""))
    if int(unit_price) not in {int(value) for value in values} and str(unit_price) not in digits:
        raise RuntimeError(
            f"魔道_兑换收尾：{name} 单价未对齐 Runtime {unit_price}：{text}"
        )


def execute_magic_invasion_tail_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: Any,
    *,
    occurrence: RankingOccurrence,
):
    """Refresh same-window facts, redeem by common policy, and verify wallet."""

    from backend.core.fanxiu.activity.magic_invasion import (
        collect_and_store_magic_invasion_activity,
    )
    from backend.core.fanxiu.activity.ranking_reconcile import seed_ranking_occurrence
    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.effective_time import job_now
    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        ScheduleActivityTarget,
        parse_schedule_header,
        resolve_schedule_runtime_activity_targets,
        runtime_activity_entities_for_date,
    )
    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )
    from backend.db import engine

    del payload
    label = "魔道_兑换收尾"
    business_now = job_now()
    if business_now.tzinfo is None:
        business_now = business_now.astimezone()
    ui_today = datetime.now().astimezone().date()
    if not occurrence.end_at < business_now < occurrence.close_at:
        raise RuntimeError(f"{label}：当前不在正式结束后的兑换保留阶段")

    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError(f"{label}：Runtime 日程不可用或不完整")

    with Session(engine) as session:
        activity = seed_ranking_occurrence(
            session,
            occurrence,
            captured_at=business_now.isoformat(timespec="seconds"),
        )
        activity_id = str(activity.id)
        session.commit()

    runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
    dialog_scene, _dialog_score, _dialog_frame = runtime.current_scene(
        (COMMON_SHOP_DIALOG_SCENE,),
        update=True,
        handle_interruptions=False,
    )
    if dialog_scene == COMMON_SHOP_DIALOG_SCENE:
        runtime.click_shape_center(COMMON_SHOP_DIALOG_SCENE, "关闭详情")
        yield from _wait_exchange_shop_ready(
            runtime,
            label=f"{label}：关闭上次安全拦截的购买框",
        )
    initial_shop_ready = yield from _wait_exchange_shop_ready(
        runtime,
        label=f"{label}：识别补跑起点",
        attempts=3,
        fail_if_missing=False,
    )
    if initial_shop_ready:
        # A previous fail-closed attempt may legitimately leave this exact
        # checkpoint inside its shop. Normalize through the annotated
        # activity return before asking the global navigator for #34/#66;
        # otherwise the intentionally OCR-heavy shop can be confused with an
        # unrelated full-frame candidate and safe navigation will refuse it.
        runtime.click_shape_center(MAGIC_SHOP_SCENE, "返回")
        yield from runtime.wait_scene(
            34,
            509,
            MAGIC_ENDED_HOME_SCENE,
            timeout=20.0,
            label=f"{label}：从本期兑换宝阁返回活动主页",
        )
    current_scene, _score, _frame = runtime.current_scene(
        (34, 66),
        update=True,
        handle_interruptions=False,
    )
    if current_scene == 34:
        yield from runtime.goto_view(66)
    elif current_scene != 66:
        # Only fall back to the standard bounded navigation chain when the
        # task did not start on either of its two known safe entry scenes.
        # In particular, do not leave #66 for the world and immediately enter
        # it again: that needlessly crosses the HUD readiness boundary.
        yield from runtime.goto_view(34)
        yield from runtime.goto_view(66)
    # Settlement cards may have left the rotating promo carousel even though
    # their dated calendar cell and shop are still open.  Resolve the exact
    # historical cell from Runtime identity + visible date axis, then click
    # that cell (calendar clicks enter the activity directly).
    # Use the full-frame detector first. On the current #66 variant, invoking
    # inherited shape OCR first can populate an empty cache entry for the same
    # frame and hide otherwise healthy calendar text.
    header_lines: list[dict[str, Any]] = []
    calendar_lines: list[dict[str, Any]] = []
    for _attempt in range(3):
        full_lines = _group_ocr_tokens(runtime.full_frame_ocr_tokens(update=True))
        header_lines = [line for line in full_lines if 200 <= float(line["y"]) < 340]
        calendar_lines = [line for line in full_lines if 295 <= float(line["y"]) < 750]
        if header_lines and calendar_lines:
            break
        yield from runtime.wait_action_settle(1.0)
        yield from runtime.wait_view(66, timeout=10.0, label=f"{label}：等待日程稳定")
    entities = runtime_activity_entities_for_date(
        schedule,
        r"魔道入侵",
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
    day_offset = _ui_calendar_day_offset(
        target_date=occurrence.end_at.date(),
        ui_today=ui_today,
    )
    try:
        targets = resolve_schedule_runtime_activity_targets(
            header_lines=header_lines,
            calendar_lines=calendar_lines,
            runtime_entities=exact_entities,
            day_offset=day_offset,
            anchor_date=ui_today,
        )
    except RuntimeError as exc:
        # OCR may corrupt only the small ``(预赛)`` qualifier while the main
        # title, exact date column and Runtime occurrence remain unambiguous.
        # Permit that narrow three-fact alignment instead of weakening the
        # shared scorer globally.
        header = parse_schedule_header(header_lines, anchor_date=ui_today)
        target_x = header.x_for_day_offset(day_offset)
        targets = _resolve_exact_magic_calendar_fallback(
            calendar_lines=calendar_lines,
            runtime_entity=exact_entities[0],
            day_offset=day_offset,
            target_x=target_x,
        )
        if not targets:
            visible = " | ".join(
                str(item.get("text") or "").strip()
                for item in calendar_lines
                if str(item.get("text") or "").strip()
            )
            raise RuntimeError(f"{exc}；当前日历OCR={visible[:1200]}") from exc
    exact = [
        target
        for target in targets
        if occurrence.runtime_id in target.runtime_key.split("|")
    ]
    if len(exact) != 1:
        raise RuntimeError(
            f"{label}：历史日程单元格未唯一对齐 occurrence {occurrence.runtime_id}"
        )
    runtime.click_frame_point(66, exact[0].x, exact[0].y)
    yield from runtime.wait_scene(
        509,
        519,
        520,
        521,
        MAGIC_ENDED_HOME_SCENE,
        timeout=30.0,
        label=f"{label}：等待结束态活动页",
    )
    scene, _score, _frame = runtime.current_scene(
        (509, 519, 520, 521, MAGIC_ENDED_HOME_SCENE),
        update=True,
    )
    if scene != MAGIC_SHOP_SCENE:
        if scene not in {509, 520, 521, MAGIC_ENDED_HOME_SCENE}:
            raise RuntimeError(f"{label}：活动页场景无法对齐：{scene}")
        _open_exchange_tab(runtime, int(scene))
    yield from _wait_exchange_shop_ready(runtime, label=label)

    with Session(engine) as session:
        runtime_period_override = {
            "game_activity_id": int(occurrence.activity_id),
            "cross_count": int(occurrence.cross_count),
            "start_date": occurrence.start_at.date().isoformat(),
            "end_date": occurrence.end_at.date().isoformat(),
            "captured_at": business_now.isoformat(timespec="seconds"),
            "record_id": f"runtime:{occurrence.runtime_id}",
            "packet_id": f"runtime:{occurrence.runtime_id}",
            "world_level": int(occurrence.world_level),
            "runtime_id": occurrence.runtime_id,
        }
        detail = collect_and_store_magic_invasion_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=True,
            runtime_period_override=runtime_period_override,
        )
        session.commit()

    wallet = read_wallet_currency_snapshot(
        int(detail.currency_type),
        allow_discovery=False,
    )
    expected_wallet = int(wallet["exchange_currency"])
    verify_exchange_wallet(
        expected_wallet,
        {"商店": detail.current_currency},
        label=label,
        stage="购买前",
    )
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
    executed: list[dict[str, Any]] = []

    # The shop exposes five rows.  Allocation is already fixed by business
    # priority, so physical execution may use the cheaper source-order walk.
    for action in actions:
        if stop_event.is_set():
            raise InterruptedError()
        for _ in range(action.scroll_rows):
            runtime.drag_frame_point(MAGIC_SHOP_SCENE, 450, 900, 450, 775, duration_ms=800)
            yield from runtime.wait_action_settle(0.25)
        # Reuse the proven exchange-tail alignment: Runtime source order and
        # purchase limits determine ``slot``; completed finite rows disappear
        # from the active prefix, so the next item shifts into that same
        # annotated row.  Product-name OCR is only a post-click dialog guard,
        # never the row locator.
        runtime.click_shape_center(MAGIC_SHOP_SCENE, f"商品行{action.slot}")
        yield from runtime.wait_view(
            COMMON_SHOP_DIALOG_SCENE,
            timeout=15.0,
            label=f"{label}：等待 {action.name} 购买框",
        )
        _verify_dialog(runtime, name=action.name, unit_price=action.unit_price)
        plus_ten, plus_one = exchange_quantity_clicks(
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
        _cost, remaining_wallet = authorize_exchange_purchase(
            current_wallet=expected_wallet,
            quantity=action.quantity,
            unit_price=action.unit_price,
            reserved_tokens=planning["reserved_tokens"],
            name=action.name,
            label=label,
        )
        runtime.click_shape_center(COMMON_SHOP_DIALOG_SCENE, "购买")
        yield from _wait_exchange_shop_ready(
            runtime,
            label=f"{label}：购买 {action.name} 后返回宝阁",
        )
        expected_wallet = remaining_wallet
        executed.append({
            "goods_id": action.goods_id,
            "name": action.name,
            "quantity": action.quantity,
            "unit_price": action.unit_price,
        })

    with Session(engine) as session:
        final_detail = collect_and_store_magic_invasion_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=True,
            runtime_period_override=runtime_period_override,
        )
        session.commit()
    verify_exchange_purchase_counts(
        detail.shop_items,
        final_detail.shop_items,
        purchases,
        label=label,
    )
    final_wallet = read_wallet_currency_snapshot(
        int(final_detail.currency_type),
        allow_discovery=False,
    )
    verify_exchange_wallet(
        expected_wallet,
        {
            "商店": final_detail.current_currency,
            "钱包": final_wallet["exchange_currency"],
            "计划": planning["planned_remaining_tokens"],
        },
        label=label,
    )
    # #519 is deliberately recognized by business OCR because its historical
    # currency label differs from the old required identity anchor. Leave via
    # its annotated return first; asking the global navigator to start from an
    # 82% unknown candidate would correctly fail closed.
    runtime.click_shape_center(MAGIC_SHOP_SCENE, "返回")
    yield from runtime.wait_scene(
        34,
        509,
        MAGIC_ENDED_HOME_SCENE,
        timeout=20.0,
        label=f"{label}：离开兑换宝阁",
    )
    landed, _score, _frame = runtime.current_scene(
        (34, 509, MAGIC_ENDED_HOME_SCENE),
        update=True,
    )
    if landed != 34:
        yield from runtime.goto_view(34)
    return {
        "status": "completed",
        "message": (
            f"魔道 occurrence {occurrence.runtime_id} 兑换收尾完成："
            f"兑换 {len(executed)} 种，保留锁定 {len(retained_locked)} 种"
        ),
        "activity_id": activity_id,
        "purchases": executed,
        "planning": planning,
        "currency_remaining": expected_wallet,
        "retained_locked_goods_ids": sorted(retained_locked),
    }


__all__ = ["execute_magic_invasion_tail_checkpoint"]
