from __future__ import annotations

"""Individually runnable Wanxiang shop actions; no Scheduler writes here."""

from typing import Any
import re

from backend.core.fanxiu.data_annotation.tasks.activity_menu_navigation import (
    open_loaded_activity_menu_item,
)
from backend.core.fanxiu.instrumentation.activity_menu import (
    read_activity_menu_snapshot,
)
from backend.core.fanxiu.instrumentation.wanxiang_baoge import (
    WANXIANG_ACTIVITY_BASE_ID,
    ledger_from_snapshot,
    load_wanxiang_refund_offer_contract,
    read_wanxiang_baoge_runtime,
    read_wanxiang_shop_page,
    require_wanxiang_refresh_allowed,
    select_wanxiang_refund_offer,
    verify_wanxiang_purchase_transition,
)
from backend.core.fanxiu.runtime_gui.activity_menu import ActivityMenuGrid

WORLD = 34
FREE = 635
REVEALED = 636
CONFIRM = 640
REFRESH_CONFIRM = 700


def wait_wanxiang_page(context: Any):
    """Prove the active shop page before reading its loaded UI model."""
    match = yield from context.wait_scene(
        [FREE, REVEALED], wait=10, label="万象宝阁：确认页面"
    )
    if match.scene_id not in {FREE, REVEALED}:
        raise RuntimeError(f"万象宝阁页面未就绪，实际为 #{match.scene_id}")
    return match


def enter_wanxiang_shop(context: Any):
    """Enter the unique current activity from world #34; stop on its page."""
    yield from context.go_scene(WORLD)
    menu = read_activity_menu_snapshot("world_left")
    targets = [item for item in menu.items if item.base_id == WANXIANG_ACTIVITY_BASE_ID]
    if not menu.complete or len(targets) != 1 or targets[0].activity_id is None:
        raise RuntimeError("世界左侧菜单没有唯一万象宝阁实例")
    yield from open_loaded_activity_menu_item(
        context,
        targets[0].activity_id,
        kind="world_left",
        source_scene_id=WORLD,
        ocr_shape_names=("左侧菜单",),
        expected_scene_ids=(FREE, REVEALED),
        target_gui_name="万象宝阁",
        grid=ActivityMenuGrid(columns=1, click_offset_heights=0.5),
        timeout_seconds=20,
        max_scrolls=2,
    )
    return (yield from wait_wanxiang_page(context)).scene_id


def reveal_wanxiang_free_goods(context: Any):
    """Click the matched free-first-draw button once, otherwise leave it alone."""
    yield from wait_wanxiang_page(context)
    matched = context.shape_matches(FREE, "第一抽免费")
    if matched:
        before = read_wanxiang_baoge_runtime()
        if not before.get("complete") or before.get("refresh_times") != 0:
            raise RuntimeError("第一抽免费文案与 Runtime 次数不一致")
        yield from context.wait_click(FREE, "第一抽免费", timeout=10)
        for _ in range(20):
            yield from context.wait_action_settle(0.5)
            after = read_wanxiang_baoge_runtime()
            if (
                after.get("complete")
                and after.get("refresh_times") == 1
                and len(after.get("goods_ids") or []) == 5
            ):
                break
        else:
            raise RuntimeError("免费首抽后未取得五个商品")
        yield from wait_wanxiang_page(context)
    return {"free_draw_clicked": bool(matched), "page": read_wanxiang_shop_page()}


def inspect_wanxiang_refund_offer(context: Any):
    """Read the current five slots and return the matching box, or None."""
    yield from wait_wanxiang_page(context)
    page = read_wanxiang_shop_page()
    return {"page": page, "target": select_wanxiang_refund_offer(page)}


def leave_wanxiang_shop(context: Any):
    """Return via the shop's annotated back button and stop on world #34."""
    page = yield from wait_wanxiang_page(context)
    yield from context.wait_click(page.scene_id, "返回", timeout=10)
    landed = yield from context.wait_scene([WORLD], wait=12, label="万象宝阁：返回世界")
    if landed.scene_id != WORLD:
        raise RuntimeError(f"万象宝阁返回未落到世界，实际为 #{landed.scene_id}")
    return {"scene_id": WORLD}


def refresh_wanxiang_goods(context: Any):
    """Refresh only while no target and no prior purchase exists.

    Owns the entire click/confirmation transaction. There is no separate
    confirmation entry point or caller-supplied authorization snapshot.
    """
    yield from wait_wanxiang_page(context)
    page = read_wanxiang_shop_page()
    before = read_wanxiang_baoge_runtime()
    require_wanxiang_refresh_allowed(page, before)
    yield from context.wait_click(REVEALED, "试试手气", timeout=10)
    yield from context.wait_action_settle(1)
    confirmed = False
    extra_clicks = 0
    for attempt in range(20):
        landed = yield from context.wait_scene(
            [REFRESH_CONFIRM, REVEALED], wait=3, label="万象宝阁：刷新结果或确认"
        )
        if landed.scene_id == REFRESH_CONFIRM:
            if confirmed:
                raise RuntimeError("确认刷新后仍停留在确认页，停止重复确认")
            current = read_wanxiang_baoge_runtime()
            if not current.get("complete") or any(
                current.get(key) != before.get(key)
                for key in (
                    "activity_id",
                    "refresh_times",
                    "purchase_counts",
                    "goods_ids",
                    "spirit_stone",
                )
            ):
                raise RuntimeError("刷新确认前活动或账本发生变化")
            require_wanxiang_refresh_allowed(page, current)
            yield from context.wait_click(REFRESH_CONFIRM, "确认刷新", timeout=8)
            confirmed = True
            yield from context.wait_action_settle(2)
            continue
        if landed.scene_id != REVEALED:
            raise RuntimeError(f"刷新后出现未支持的页面 #{landed.scene_id}")
        after = read_wanxiang_baoge_runtime()
        if not after.get("complete"):
            yield from context.wait_action_settle(0.5)
            continue
        if (
            after["activity_id"] != before["activity_id"]
            or after["evidence"]["pid"] != before["evidence"]["pid"]
            or after["evidence"]["process_start_ticks"]
            != before["evidence"]["process_start_ticks"]
        ):
            raise RuntimeError("刷新前后活动或进程变化")
        if after["refresh_times"] == before["refresh_times"] + 1:
            if (
                after["spirit_stone"] != before["spirit_stone"] - 100
                or after["purchase_counts"] != before["purchase_counts"]
                or after["voucher"] != before["voucher"]
                or after["bound_voucher"] != before["bound_voucher"]
            ):
                raise RuntimeError("刷新消耗或购买账本与预期不符")
            page = read_wanxiang_shop_page()
            _ledger_for_page(page, after, 0)
            return {"refreshes": 1, "spirit_stone_spent": 100, "page": page}
        if after["refresh_times"] != before["refresh_times"]:
            raise RuntimeError("刷新次数并非精确增加 1")
        # The client can ignore a click while its previous-click latch clears.
        # Retry only with unchanged accounting and a proven clear UI latch;
        # once confirmation is submitted, never replay it.
        page = read_wanxiang_shop_page()
        _ledger_for_page(page, after, 0)
        if (
            not confirmed
            and not page["refresh_click_pending"]
            and extra_clicks < 2
            and attempt >= 1
        ):
            if any(
                after[key] != before[key]
                for key in ("goods_ids", "spirit_stone", "purchase_counts")
            ):
                raise RuntimeError("刷新重试前账本已变化")
            require_wanxiang_refresh_allowed(page, after)
            yield from context.wait_click(REVEALED, "试试手气", timeout=10)
            extra_clicks += 1
        yield from context.wait_action_settle(1)
    raise RuntimeError("刷新后未取得新的五个商品")


def find_wanxiang_refund_offer(context: Any):
    """Refresh until the box is among the five goods; stop as soon as it is."""
    refreshes = 0
    while True:
        observed = yield from inspect_wanxiang_refund_offer(context)
        if observed["target"] is not None:
            return {**observed, "refreshes": refreshes}
        yield from refresh_wanxiang_goods(context)
        refreshes += 1


def _ledger_for_page(page, snapshot, goods_id):
    ledger = ledger_from_snapshot(snapshot, goods_id=goods_id)
    if (
        page["activity_id"] != ledger.activity_id
        or page["evidence"]["process_start_ticks"] != ledger.process_start_ticks
        or page["evidence"]["pid"] != snapshot["evidence"]["pid"]
    ):
        raise RuntimeError("万象宝阁页面与账本不属于同一活动和进程")
    if [row["goods_id"] for row in page["goods"]] != snapshot["goods_ids"]:
        raise RuntimeError("万象宝阁画面商品与账本不同步")
    return ledger


def _shape_text(context, scene, shape):
    tokens = context.ocr_tokens_in_shapes(
        scene, (shape,), frame_data_url=context.cur_frame(update=True)
    )
    return "".join(
        str(t.get("text") or "")
        for t in sorted(tokens, key=lambda t: (t.get("y", 0), t.get("x", 0)))
    )


def purchase_wanxiang_refund_offer(context: Any):
    """Buy the visible, unpurchased refund box once; stop back on the shop.

    Re-observes its own target and wallet. The caller cannot inject a stale
    slot or waive the item/payment whitelist. Does not open the acquired box.
    """
    observed = yield from inspect_wanxiang_refund_offer(context)
    page, target = observed["page"], observed["target"]
    if target is None:
        raise RuntimeError("当前商品中没有代币宝匣")
    goods_id = target["goods_id"]
    if target["purchased"]:
        return {"ok": True, "outcome": "already_purchased", "goods_id": goods_id}
    before = _ledger_for_page(page, read_wanxiang_baoge_runtime(), goods_id)
    if before.target_purchase_count >= 1:
        raise RuntimeError("代币宝匣界面已购状态与账本不一致")
    if target["sold_out"] is not False or target["remaining"] != 1:
        raise RuntimeError("代币宝匣当前库存不是 1")
    if before.voucher_total < 6:
        raise RuntimeError("代币余额不足 6，停止购买")
    # This stable payment/item join also proves what the six vouchers buy.
    load_wanxiang_refund_offer_contract()
    slot = target["slot"]
    yield from context.wait_click(REVEALED, f"购买商品{slot}", timeout=10)
    match = yield from context.wait_scene(
        [CONFIRM], wait=10, label="万象宝阁：代币确认"
    )
    if match.scene_id != CONFIRM:
        raise RuntimeError("未进入代币购买确认页")
    name = re.sub(r"\s", "", _shape_text(context, CONFIRM, "商品代币宝匣"))
    cost = re.sub(r"\s", "", _shape_text(context, CONFIRM, "购买所需6"))
    if "代币宝匣" not in name or re.search(r"购买所需[:：]?6(?:元)?$", cost) is None:
        raise RuntimeError(f"代币宝匣确认页商品或金额不符：{name}；{cost}")
    # Read again at the irreversible boundary; the confirmation must still
    # refer to an unpurchased offer with enough existing vouchers.
    latest = ledger_from_snapshot(read_wanxiang_baoge_runtime(), goods_id=goods_id)
    if latest != before:
        raise RuntimeError("确认代币购买前账本发生变化")
    yield from context.wait_click(CONFIRM, "确认代币购买", timeout=8)
    for _ in range(20):
        yield from context.wait_action_settle(0.5)
        snapshot = read_wanxiang_baoge_runtime()
        if snapshot.get("complete"):
            after = ledger_from_snapshot(snapshot, goods_id=goods_id)
            if after.target_purchase_count == before.target_purchase_count + 1:
                verify_wanxiang_purchase_transition(before, after)
                break
    else:
        raise RuntimeError("代币宝匣购买后账本未变化")
    yield from wait_wanxiang_page(context)
    page = read_wanxiang_shop_page()
    target_after = select_wanxiang_refund_offer(page)
    if (
        target_after is None
        or target_after["goods_id"] != goods_id
        or not target_after["purchased"]
    ):
        raise RuntimeError("购买账本已变化，但商品页面尚未标记已购")
    return {
        "ok": True,
        "outcome": "purchased",
        "goods_id": goods_id,
        "voucher_before": before.voucher_total,
        "voucher_after": after.voucher_total,
        "box_before": before.refund_box_count,
        "box_after": after.refund_box_count,
    }
