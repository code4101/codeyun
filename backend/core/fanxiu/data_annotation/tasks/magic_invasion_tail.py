from __future__ import annotations

"""Settlement-window exchange tail for one Magic Invasion occurrence."""

from datetime import date, datetime
import re
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.activity.ranking_lifecycle import (
    RankingOccurrence,
    occurrence_exchange_tail_window_contains,
)
from backend.core.fanxiu.data_annotation.ocr_spatial import (
    group_ocr_tokens as _group_ocr_tokens,
)
from backend.core.fanxiu.data_annotation.tasks.exchange_tail_planning import (
    authorize_exchange_purchase,
    plan_exchange_tail_physical_actions,
    plan_exchange_tail_purchases,
    verify_exchange_purchase_counts,
    verify_exchange_wallet,
)
from backend.core.fanxiu.data_annotation.tasks.common_shop_quantity import (
    set_verified_common_shop_quantity,
)
from backend.core.fanxiu.runtime_gui.activity_bottom_tab import (
    resolve_vertical_bottom_tab as resolve_magic_invasion_bottom_tab,
)
from backend.core.fanxiu.runtime_gui.exchange_shop import resolve_exchange_shop_item
from backend.core.fanxiu.runtime_gui import ocr_name_similarity


MAGIC_SHOP_SCENE = 519
MAGIC_ENDED_HOME_SCENE = 522
COMMON_SHOP_DIALOG_SCENE = 566
SHOP_TRAVERSAL_RATIO = 0.65


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or ""))


def open_magic_invasion_exchange_tab(context: Any, scene: int) -> None:
    """Open the shared Magic Invasion exchange tab from an activity subpage."""

    tokens = context.full_frame_ocr_tokens(update=True)
    target = resolve_magic_invasion_bottom_tab(
        _group_ocr_tokens(tokens),
        tab_name="兑换宝阁",
        frame_width=900,
        frame_height=1600,
    )
    context.click_frame_point(scene, target.x, target.y)


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


def wait_magic_invasion_exchange_shop_ready(
    context: Any,
    *,
    label: str,
    attempts: int = 20,
    fail_if_missing: bool = True,
):
    """Wait for the shop by business text when the strict View scores 82%."""

    last_visible = ""
    for _attempt in range(max(1, int(attempts))):
        lines = _group_ocr_tokens(context.full_frame_ocr_tokens(update=True))
        visible = [_compact(line.get("text")) for line in lines]
        last_visible = " | ".join(text for text in visible if text)
        if _exchange_shop_business_ready(lines):
            return True
        yield from context.wait_action_settle(1.0)
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


def _resolve_exact_magic_historical_date_cell(
    *,
    calendar_lines: list[dict[str, Any]],
    runtime_entity: Any,
    day_offset: int,
    target_x: float,
) -> tuple[Any, ...]:
    """Project one unique Magic row onto a historical date column.

    Ended gold cards sometimes omit their stylized qualifier from both the
    shared and cropped OCR.  This fallback is historical-only and retains the
    exact Runtime occurrence key.  The entered shop is still independently
    checked against the occurrence cross count before any purchase.
    """

    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        ScheduleActivityTarget,
    )

    if int(day_offset) >= 0:
        return ()
    activity_name = str(runtime_entity.payload.get("name") or "魔道入侵")
    title_rows = [
        row
        for row in calendar_lines
        if ocr_name_similarity(activity_name, str(row.get("text") or "")) >= 0.90
    ]
    unique_rows: list[dict[str, Any]] = []
    for row in sorted(title_rows, key=lambda item: float(item.get("y") or 0)):
        center_y = float(row.get("y") or 0) + float(row.get("h") or 0) / 2
        if any(
            abs(
                center_y
                - (float(existing.get("y") or 0) + float(existing.get("h") or 0) / 2)
            )
            < 12
            for existing in unique_rows
        ):
            continue
        unique_rows.append(row)
    if len(unique_rows) != 1:
        return ()
    title = unique_rows[0]
    return (
        ScheduleActivityTarget(
            day_offset=int(day_offset),
            x=float(target_x),
            y=float(title.get("y") or 0) + float(title.get("h") or 0) / 2,
            matched_text=str(title.get("text") or "").strip(),
            runtime_key=str(runtime_entity.key),
            alignment_score=1.0,
        ),
    )


def _verify_dialog(context: Any, *, name: str, unit_price: int) -> None:
    title = context.ocr_text_in_shapes(
        COMMON_SHOP_DIALOG_SCENE,
        ("商品标题",),
        padding=10,
    )
    if ocr_name_similarity(_compact(name), _compact(title)) < 0.78:
        raise RuntimeError(f"魔道_兑换收尾：购买框商品未对齐 {name}：{title}")
    values, text = context.ocr_numbers_in_shapes(
        COMMON_SHOP_DIALOG_SCENE,
        ("价格",),
        padding=8,
    )
    digits = re.sub(r"\D+", "", str(text or ""))
    if int(unit_price) not in {int(value) for value in values} and str(unit_price) not in digits:
        raise RuntimeError(
            f"魔道_兑换收尾：{name} 单价未对齐 Runtime {unit_price}：{text}"
        )


def _open_verified_shop_product(
    context: Any,
    *,
    name: str,
    unit_price: int,
    max_scrolls: int,
):
    """Find the next planned product by live row identity before clicking it."""

    view = context.view(MAGIC_SHOP_SCENE)
    product_list = view.get_shape("商品列表")
    rows = [view.get_shape(f"商品行{slot}") for slot in range(1, 6)]
    if product_list is None or any(row is None for row in rows):
        raise RuntimeError("魔道_兑换收尾：缺少 #519 商品列表正式几何")

    # A completed purchase may remove or reorder rows.  Re-establish the list
    # origin before resolving every next action, then scan from top to bottom.
    # ``scroll_shape_content`` follows the same direction contract as the
    # auto-configurator: ``up`` returns to the top and ``down`` advances to
    # later rows.
    for _attempt in range(max(0, int(max_scrolls)) + 1):
        changed = yield from context.scroll_shape_content(
            MAGIC_SHOP_SCENE,
            "商品列表",
            direction="up",
            ratio=SHOP_TRAVERSAL_RATIO,
            duration=0.12,
            unchanged_confirmations=2,
        )
        if not changed:
            break
    else:
        raise RuntimeError("魔道_兑换收尾：商品列表在有界次数内未能归顶")

    last_error = ""
    for scroll_index in range(max(0, int(max_scrolls)) + 1):
        # The shop title/wallet become ready before its rows finish rerendering
        # after a purchase.  Re-sample the same viewport before scrolling so a
        # transient empty/old frame cannot skip a target that appears at row 1.
        for recognition_attempt in range(3):
            raw_tokens = tuple(context.full_frame_ocr_tokens(update=True))
            # Product names need their linked Paddle parent line, while a
            # price may share that line with ``所需：``.  Preserve raw pure-
            # numeric word tokens for price disambiguation instead of
            # weakening the resolver to accept arbitrary embedded numbers.
            lines = tuple(_group_ocr_tokens(raw_tokens)) + tuple(
                token
                for token in raw_tokens
                if re.fullmatch(r"\d+", _compact(token.get("text")))
            )
            try:
                target = resolve_exchange_shop_item(
                    lines,
                    product_list_box=product_list.box(),
                    product_row_boxes=[row.box() for row in rows],
                    expected_name=name,
                    expected_unit_price=unit_price,
                )
            except RuntimeError as exc:
                last_error = str(exc)
                if recognition_attempt < 2:
                    # A list drag can leave a persistent item-description
                    # tooltip over row 1.  Clear it through the formal inert
                    # shop title before deciding that the product is absent.
                    context.click_shape_center(MAGIC_SHOP_SCENE, "兑换宝阁标题")
                    yield from context.wait_action_settle(0.35)
                continue
            context.click_frame_point(MAGIC_SHOP_SCENE, target.x, target.y)
            landed = yield from context.wait_scene(
                [COMMON_SHOP_DIALOG_SCENE],
                wait=15.0,
                label=f"魔道_兑换收尾：等待 {name} 购买框",
            )
            _verify_dialog(context, name=name, unit_price=unit_price)
            return landed

        # Full-frame OCR can merge the item title with rotating effect text
        # below it (observed: 咒残页 -> 九转炽阳).  A cropped pass over the
        # formal list restores the product line while retaining global row
        # coordinates.  Keep this as a fallback so the common fast path stays
        # cheap.
        context.click_shape_center(MAGIC_SHOP_SCENE, "兑换宝阁标题")
        yield from context.wait_action_settle(0.35)
        cropped_tokens = tuple(context.ocr_tokens_in_shapes(
            MAGIC_SHOP_SCENE,
            ("商品列表",),
            padding=0,
            crop=True,
        ))
        cropped_lines = tuple(_group_ocr_tokens(cropped_tokens)) + tuple(
            token
            for token in cropped_tokens
            if re.fullmatch(r"\d+", _compact(token.get("text")))
        )
        try:
            target = resolve_exchange_shop_item(
                cropped_lines,
                product_list_box=product_list.box(),
                product_row_boxes=[row.box() for row in rows],
                expected_name=name,
                expected_unit_price=unit_price,
            )
        except RuntimeError as exc:
            last_error = str(exc)
        else:
            context.click_frame_point(MAGIC_SHOP_SCENE, target.x, target.y)
            landed = yield from context.wait_scene(
                [COMMON_SHOP_DIALOG_SCENE],
                wait=15.0,
                label=f"魔道_兑换收尾：等待 {name} 购买框",
            )
            _verify_dialog(context, name=name, unit_price=unit_price)
            return landed

        if scroll_index >= max_scrolls:
            break
        changed = yield from context.scroll_shape_content(
            MAGIC_SHOP_SCENE,
            "商品列表",
            direction="down",
            ratio=SHOP_TRAVERSAL_RATIO,
            duration=0.12,
            unchanged_confirmations=2,
        )
        if not changed:
            break
    raise RuntimeError(
        f"魔道_兑换收尾：有界滚动后未找到商品 {name}({unit_price})：{last_error}"
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
    if not occurrence_exchange_tail_window_contains(occurrence, business_now):
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

    context = runner._behavior_tree_context(ctx, stop_event=stop_event)
    _wait_scene_match = yield from context.wait_scene((COMMON_SHOP_DIALOG_SCENE,), wait=5.0, required=False)
    (dialog_scene, _dialog_score, _dialog_frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if dialog_scene == COMMON_SHOP_DIALOG_SCENE:
        context.click_shape_center(COMMON_SHOP_DIALOG_SCENE, "关闭详情")
        yield from wait_magic_invasion_exchange_shop_ready(
            context,
            label=f"{label}：关闭上次安全拦截的购买框",
        )
    initial_shop_ready = yield from wait_magic_invasion_exchange_shop_ready(
        context,
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
        context.click_shape_center(MAGIC_SHOP_SCENE, "兑换宝阁标题")
        yield from context.wait_action_settle(0.35)
        context.click_shape_center(MAGIC_SHOP_SCENE, "返回")
        yield from context.wait_scene(
            [34,
            509,
            MAGIC_ENDED_HOME_SCENE],
            wait=20.0,
            label=f"{label}：从本期兑换宝阁返回活动主页",
        )
    _wait_scene_match = yield from context.wait_scene((34, 66), wait=5.0, required=False)
    (current_scene, _score, _frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if current_scene != 34:
        # Normalize every retry through the world.  Item-description overlays
        # can survive the shop's return action and corrupt #66 calendar OCR;
        # a fresh #34 -> #66 entry must not inherit that prior-attempt UI.
        yield from context.go_scene(34)
    yield from context.go_scene(66)
    # Item tooltips are global overlays and can survive scene navigation.
    # The annotated #66 title has no jump/action; tapping it dismisses the
    # overlay before any calendar OCR or qualifier comparison.
    context.click_shape_center(66, "日程")
    yield from context.wait_action_settle(0.35)
    yield from context.wait_scene([66], wait=10.0, label=f"{label}：清理日程浮层")
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
        full_lines = _group_ocr_tokens(context.full_frame_ocr_tokens(update=True))
        header_lines = [line for line in full_lines if 200 <= float(line["y"]) < 340]
        calendar_lines = [line for line in full_lines if 295 <= float(line["y"]) < 750]
        if header_lines and calendar_lines:
            break
        yield from context.wait_action_settle(1.0)
        yield from context.wait_scene([66], wait=10.0, label=f"{label}：等待日程稳定")
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
            expected_cross_count=int(occurrence.cross_count),
        )
    except RuntimeError as exc:
        resolution_error = exc
        targets = ()
        # Stylized gold activity cards can be completely absent from shared
        # full-frame Paddle tokens even while they are plainly visible.  Run
        # one targeted OCR pass over the formal #66 header/calendar shapes
        # before falling back to any weaker identity rule.
        cropped_tokens = context.ocr_tokens_in_shapes(
            66,
            ("表头", "日历"),
            padding=0,
            crop=True,
        )
        cropped_lines = _group_ocr_tokens(cropped_tokens)
        cropped_header = [
            line for line in cropped_lines if 200 <= float(line["y"]) < 340
        ]
        cropped_calendar = [
            line for line in cropped_lines if 295 <= float(line["y"]) < 750
        ]
        if cropped_header and cropped_calendar:
            try:
                targets = resolve_schedule_runtime_activity_targets(
                    header_lines=cropped_header,
                    calendar_lines=cropped_calendar,
                    runtime_entities=exact_entities,
                    day_offset=day_offset,
                    anchor_date=ui_today,
                    expected_cross_count=int(occurrence.cross_count),
                )
                header_lines = cropped_header
                calendar_lines = cropped_calendar
            except RuntimeError as cropped_exc:
                resolution_error = cropped_exc
        # OCR may corrupt only the small ``(预赛)`` qualifier while the main
        # title, exact date column and Runtime occurrence remain unambiguous.
        # Permit that narrow three-fact alignment instead of weakening the
        # shared scorer globally.
        if not targets:
            header = parse_schedule_header(header_lines, anchor_date=ui_today)
            target_x = header.x_for_day_offset(day_offset)
            targets = _resolve_exact_magic_calendar_fallback(
                calendar_lines=calendar_lines,
                runtime_entity=exact_entities[0],
                day_offset=day_offset,
                target_x=target_x,
            )
        if not targets:
            targets = _resolve_exact_magic_historical_date_cell(
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
            raise RuntimeError(
                f"{resolution_error}；当前日历OCR={visible[:1200]}"
            ) from resolution_error
    exact = [
        target
        for target in targets
        if occurrence.runtime_id in target.runtime_key.split("|")
    ]
    if len(exact) != 1:
        raise RuntimeError(
            f"{label}：历史日程单元格未唯一对齐 occurrence {occurrence.runtime_id}"
        )
    context.click_frame_point(66, exact[0].x, exact[0].y)
    yield from context.wait_scene(
        [509,
        519,
        520,
        521,
        MAGIC_ENDED_HOME_SCENE],
        wait=30.0,
        label=f"{label}：等待结束态活动页",
    )
    _wait_scene_match = yield from context.wait_scene((509, 519, 520, 521, MAGIC_ENDED_HOME_SCENE), wait=5.0, required=False)
    (scene, _score, _frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if scene != MAGIC_SHOP_SCENE:
        if scene not in {509, 520, 521, MAGIC_ENDED_HOME_SCENE}:
            raise RuntimeError(f"{label}：活动页场景无法对齐：{scene}")
        open_magic_invasion_exchange_tab(context, int(scene))
    yield from wait_magic_invasion_exchange_shop_ready(context, label=label)

    with Session(engine) as session:
        runtime_period_override = {
            "game_activity_id": int(occurrence.activity_id),
            "cross_count": int(occurrence.cross_count),
            "start_date": occurrence.start_at.date().isoformat(),
            "end_date": occurrence.end_at.date().isoformat(),
            "captured_at": business_now.isoformat(timespec="seconds"),
            "record_id": f"context:{occurrence.runtime_id}",
            "packet_id": f"context:{occurrence.runtime_id}",
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

    # Allocation is fixed by Runtime business priority, but completed rows do
    # not have a stable remove/reorder contract across shop variants.  Walk
    # monotonically downward and require live name+unit-price row identity
    # before every click; the dialog check remains an independent second gate.
    remaining_scroll_budget = len(detail.shop_items) + 5
    for action in actions:
        if stop_event.is_set():
            raise InterruptedError()
        yield from _open_verified_shop_product(
            context,
            name=action.name,
            unit_price=action.unit_price,
            max_scrolls=remaining_scroll_budget,
        )
        quantity_proof = yield from set_verified_common_shop_quantity(
            context,
            action.quantity,
            unit_price=action.unit_price,
            label=f"{label}/{action.name}",
        )
        _cost, remaining_wallet = authorize_exchange_purchase(
            current_wallet=expected_wallet,
            quantity=action.quantity,
            unit_price=action.unit_price,
            reserved_tokens=planning["reserved_tokens"],
            name=action.name,
            label=label,
        )
        context.click_shape_center(COMMON_SHOP_DIALOG_SCENE, "购买")
        yield from wait_magic_invasion_exchange_shop_ready(
            context,
            label=f"{label}：购买 {action.name} 后返回宝阁",
        )
        expected_wallet = remaining_wallet
        executed.append({
            "goods_id": action.goods_id,
            "name": action.name,
            "quantity": action.quantity,
            "unit_price": action.unit_price,
            "quantity_proof": quantity_proof,
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
    context.click_shape_center(MAGIC_SHOP_SCENE, "返回")
    yield from context.wait_scene(
        [34,
        509,
        MAGIC_ENDED_HOME_SCENE],
        wait=20.0,
        label=f"{label}：离开兑换宝阁",
    )
    _wait_scene_match = yield from context.wait_scene((34, 509, MAGIC_ENDED_HOME_SCENE), wait=5.0, required=False)
    (landed, _score, _frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if landed != 34:
        yield from context.go_scene(34)
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


__all__ = [
    "execute_magic_invasion_tail_checkpoint",
    "open_magic_invasion_exchange_tab",
    "wait_magic_invasion_exchange_shop_ready",
]
