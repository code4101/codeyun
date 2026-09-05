from __future__ import annotations

"""Bounded, resumable 万象宝阁 six-yuan refund job."""

import threading
from datetime import timedelta
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.tasks.activity_menu_navigation import (
    open_loaded_activity_menu_item,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import (
    StorageBagFixedBoxGuiAdapter,
    StorageBagRandomBoxRequest,
    record_box_execution,
)
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.instrumentation.activity_menu import (
    read_activity_menu_snapshot,
)
from backend.core.fanxiu.instrumentation.storage_bag_catalog import (
    sync_storage_bag_atlas,
)
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
from backend.core.fanxiu.instrumentation.wanxiang_baoge import (
    WANXIANG_ACTIVITY_BASE_ID,
    WANXIANG_REFUND_BOX_ITEM_ID,
    WANXIANG_REFUND_GOODS_ID,
    decide_wanxiang_refund_action,
    load_wanxiang_refund_offer_contract,
    read_wanxiang_baoge_runtime,
)
from backend.core.fanxiu.runtime_gui.activity_menu import ActivityMenuGrid
from backend.core.fanxiu.storage_bag_usage import ensure_storage_bag_atlas_analysis
from backend.db import engine


STANDARD_JOB_ID = "wanxiang-baoge-six-yuan"
TASK_TYPE = "wanxiang_baoge_six_yuan"
WORLD_SCENE = 34
MAIN_FREE_SCENE = 635
MAIN_REVEALED_SCENE = 636
BOX_DETAIL_SCENE = 639
PURCHASE_CONFIRM_SCENE = 640
STORAGE_BAG_SCENE = 525
RETRY_HOUR = 0
RETRY_MINUTE = 30


def _complete_snapshot() -> dict[str, Any]:
    snapshot = read_wanxiang_baoge_runtime()
    if snapshot.get("complete") is not True:
        raise RuntimeError(str(snapshot.get("reason") or "万象宝阁 Runtime 不完整"))
    return snapshot


def _shape_tokens(context: Any, scene_id: int, shape_name: str, frame: str) -> str:
    tokens = context.ocr_tokens_in_shapes(
        scene_id,
        (shape_name,),
        frame_data_url=frame,
    )
    ordered = sorted(tokens, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)))
    return "".join(str(item.get("text") or "").strip() for item in ordered)


def _authorized_goods_slot(goods_ids: list[int]) -> int | None:
    if len(goods_ids) != 5:
        raise RuntimeError("万象宝阁揭晓商品不是五个")
    matches = [
        slot
        for slot, goods_id in enumerate(goods_ids, start=1)
        if int(goods_id) == WANXIANG_REFUND_GOODS_ID
    ]
    if len(matches) > 1:
        raise RuntimeError(f"万象宝阁出现多个 goods_id=99001 白名单商品槽：{matches}")
    return matches[0] if matches else None


def _verify_target_slot(context: Any, frame: str, goods_ids: list[int]) -> int:
    slot = _authorized_goods_slot(goods_ids)
    if slot is None:
        raise RuntimeError("万象宝阁当前商品不含 goods_id=99001")
    text = _shape_tokens(context, MAIN_REVEALED_SCENE, f"商品{slot}", frame)
    if not all(token in text for token in ("0.5折", "120元", "6元")):
        raise RuntimeError("goods_id=99001 槽位未显示已授权的0.5折六元契约")
    return slot


def next_wanxiang_retry_time() -> str:
    current = job_now()
    return (current + timedelta(days=1)).replace(
        hour=RETRY_HOUR,
        minute=RETRY_MINUTE,
        second=0,
        microsecond=0,
    ).strftime("%Y-%m-%d %H:%M:%S")


def _open_refund_box(context: Any):
    yield from context.go_scene(WORLD_SCENE)
    yield from context.wait_click(WORLD_SCENE, "右侧菜单/储物袋", timeout=12)
    yield from context.wait_scene(
        [STORAGE_BAG_SCENE],
        wait=12,
        label="万象宝阁：等待储物袋",
    )
    snapshot = dict(fanxiu_instrumentation_service.backpack_ui_snapshot())
    matches = [
        item
        for item in snapshot.get("items") or []
        if int(item.get("base_id") or 0) == WANXIANG_REFUND_BOX_ITEM_ID
        and not item.get("is_padding")
        and int(item.get("num") or 0) > 0
    ]
    if len(matches) != 1 or int(matches[0].get("num") or 0) != 1:
        raise RuntimeError(f"代币宝匣 Runtime 实例不唯一或数量不是1：{matches}")
    target = matches[0]
    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
    box_card = cards.get(str(WANXIANG_REFUND_BOX_ITEM_ID)) or {}
    if box_card.get("name") != "代币宝匣":
        raise RuntimeError("Item 1201 不再是代币宝匣")

    # The generic recorder is intentionally strict: establish the immutable
    # fixed-box classification before the irreversible click, never afterward.
    atlas = sync_storage_bag_atlas(snapshot, cards)
    with Session(engine) as session:
        ensure_storage_bag_atlas_analysis(session, atlas)
        session.commit()

    def recorder(execution: Any) -> None:
        with Session(engine) as session:
            record_box_execution(session, execution)
            session.commit()

    adapter = StorageBagFixedBoxGuiAdapter(
        context=context,
        snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,
        catalog_cards_by_id=cards,
        recorder=recorder,
        wallet_snapshot_reader=read_wallet_currency_snapshot,
    )
    result = yield from adapter.execute(
        StorageBagRandomBoxRequest(
            base_id=WANXIANG_REFUND_BOX_ITEM_ID,
            instance_id=str(target.get("instance_id") or ""),
            name="代币宝匣",
            quantity=1,
        )
    )
    rewards = list(result.delta.rewards)
    backpack_spirit = [
        item
        for item in rewards
        if not item.get("reward_key") and int(item.get("item_id") or 0) == 1001
    ]
    voucher_rewards = [
        item for item in rewards if item.get("reward_key") == "wallet:1001"
    ]
    if len(backpack_spirit) != 1 or int(backpack_spirit[0].get("quantity") or 0) != 1140:
        raise RuntimeError(f"代币宝匣灵石奖励不是1140：{rewards}")
    if len(voucher_rewards) != 1 or int(voucher_rewards[0].get("quantity") or 0) != 6:
        raise RuntimeError(f"代币宝匣充值代币奖励不是6：{rewards}")
    if dict(result.wallet_after).get(1001, 0) - dict(result.wallet_before).get(1001, 0) != 6:
        raise RuntimeError("代币宝匣未精确回补6元充值代币")
    yield from context.wait_click(STORAGE_BAG_SCENE, "返回", timeout=8)
    yield from context.wait_scene([WORLD_SCENE], wait=12, label="万象宝阁：返回世界")
    return {
        "opened": 1,
        "rewards": rewards,
        "voucher_before": dict(result.wallet_before).get(1001),
        "voucher_after": dict(result.wallet_after).get(1001),
    }


def execute_wanxiang_baoge_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
):
    """Observe → decide → act → verify one exact six-yuan refund offer."""

    contract = load_wanxiang_refund_offer_contract()
    if contract.get("complete") is not True or int(contract.get("price_cny_fen") or 0) != 600:
        raise RuntimeError("万象宝阁静态六元契约不完整")
    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)

    scene_id, _score, _frame = yield from context.current_scene(
        (MAIN_FREE_SCENE, MAIN_REVEALED_SCENE, WORLD_SCENE),
        update=True,
        label="万象宝阁：确认操作页",
    )
    if scene_id not in {MAIN_FREE_SCENE, MAIN_REVEALED_SCENE}:
        yield from context.go_scene(WORLD_SCENE)
        menu = read_activity_menu_snapshot("world_left")
        targets = [item for item in menu.items if int(item.base_id or 0) == WANXIANG_ACTIVITY_BASE_ID]
        if len(targets) != 1 or targets[0].activity_id is None:
            raise RuntimeError("世界左侧菜单没有唯一万象宝阁实例")
        yield from open_loaded_activity_menu_item(
            context,
            int(targets[0].activity_id),
            kind="world_left",
            source_scene_id=WORLD_SCENE,
            ocr_shape_names=("左侧菜单",),
            expected_scene_ids=(MAIN_FREE_SCENE, MAIN_REVEALED_SCENE),
            target_gui_name="万象宝阁",
            # Derive the icon centre from the exact OCR label span.  Live
            # layouts vary the text height with combat-power overlays; moving
            # half a text height above the label top remains in the same row,
            # while larger fixed multiples can hit the preceding activity.
            grid=ActivityMenuGrid(
                columns=1,
                click_offset_heights=0.5,
            ),
            timeout_seconds=20,
            max_scrolls=2,
        )
    # The activity model is globally resident and can be complete on #34.
    # Therefore Runtime completeness cannot substitute for the UI-page
    # precondition above; read it only after the page landing is proven.
    snapshot = _complete_snapshot()

    if int(snapshot.get("refresh_times") or 0) == 0:
        if snapshot.get("goods_ids") != []:
            raise RuntimeError("首次免费状态与商品列表矛盾")
        yield from context.wait_click(MAIN_FREE_SCENE, "第一抽免费", timeout=10)
        for _attempt in range(20):
            yield from context.wait_action_settle(0.4)
            snapshot = read_wanxiang_baoge_runtime()
            if snapshot.get("complete") and int(snapshot.get("refresh_times") or 0) == 1 and len(snapshot.get("goods_ids") or []) == 5:
                break
        else:
            raise RuntimeError("免费首抽后未取得五个 Runtime 商品")

    decision = decide_wanxiang_refund_action(snapshot)
    if decision["action"] == "open_refund_box":
        context.set_next_time(next_wanxiang_retry_time())
        box = yield from _open_refund_box(context)
        context.set_next_time(None)
        runner._log("success", "万象宝阁：既有代币宝匣已开启并精确回补6代币")
        return {"ok": True, "outcome": "refund_complete", "cash_paid_fen": 0, "box": box}
    if decision["action"] == "stop":
        outcome = str(decision.get("outcome") or "stopped")
        if outcome == "already_completed":
            context.set_next_time(None)
            yield from context.go_scene(WORLD_SCENE)
            runner._log("success", "万象宝阁：本期6代币白名单商品已完成，幂等跳过")
            return {"ok": True, "outcome": outcome, "cash_paid_fen": 0}
        if outcome == "target_not_visible":
            next_time = next_wanxiang_retry_time()
            context.set_next_time(next_time)
            yield from context.go_scene(WORLD_SCENE)
            runner._log(
                "success",
                f"万象宝阁：免费五卡无6代币白名单商品，零付费刷新；下次 {next_time}",
            )
            return {
                "ok": True,
                "outcome": "pending_retry",
                "reason": decision["reason"],
                "next_time": next_time,
                "cash_paid_fen": 0,
                "refreshes_paid": 0,
            }
        context.set_next_time(None)
        raise RuntimeError(str(decision.get("reason") or outcome))
    if decision["action"] != "purchase_with_voucher":
        context.set_next_time(None)
        raise RuntimeError(f"万象宝阁返回未知授权动作：{decision}")

    context.set_next_time(next_wanxiang_retry_time())
    snapshot = _complete_snapshot()
    frame = context.cur_frame(update=True)
    goods_ids = [int(value) for value in snapshot.get("goods_ids") or []]
    slot = _verify_target_slot(context, frame, goods_ids)
    active_goods_id = int(goods_ids[slot - 1])
    if active_goods_id != WANXIANG_REFUND_GOODS_ID:
        raise RuntimeError("万象宝阁拒绝购买非白名单商品")
    counts = {int(key): int(value) for key, value in (snapshot.get("purchase_counts") or {}).items()}
    if counts.get(WANXIANG_REFUND_GOODS_ID, 0) != 0:
        raise RuntimeError("目标商品已购账本与授权动作矛盾")
    context.click_shape(MAIN_REVEALED_SCENE, f"查看商品{slot}", frame_data_url=frame)
    yield from context.wait_scene([BOX_DETAIL_SCENE], wait=10, label="万象宝阁：核对代币宝匣详情")
    yield from context.wait_click(BOX_DETAIL_SCENE, "关闭详情", timeout=8)
    yield from context.wait_scene([MAIN_REVEALED_SCENE], wait=10, label="万象宝阁：详情返回")

    purchase_before = _complete_snapshot()
    frame = context.cur_frame(update=True)
    context.click_shape(MAIN_REVEALED_SCENE, f"购买商品{slot}", frame_data_url=frame)
    yield from context.wait_scene([PURCHASE_CONFIRM_SCENE], wait=10, label="万象宝阁：等待六元确认")
    confirm_frame = context.cur_frame(update=True)
    confirm = "".join(str(item.get("text") or "") for item in context.full_frame_ocr_tokens(frame_data_url=confirm_frame))
    if "购买商品：代币宝匣" not in confirm or "购买所需：6" not in confirm or "代币购买" not in confirm:
        raise RuntimeError("代币确认页商品或六元金额不完整")
    context.click_shape(PURCHASE_CONFIRM_SCENE, "确认代币购买", frame_data_url=confirm_frame)
    for _attempt in range(20):
        yield from context.wait_action_settle(0.5)
        after = read_wanxiang_baoge_runtime()
        if after.get("complete") and int(after.get("buy_times") or 0) == int(purchase_before.get("buy_times") or 0) + 1:
            break
    else:
        raise RuntimeError("六元商品确认后购买账本未变化")
    before_voucher = int(purchase_before.get("voucher") or 0) + int(purchase_before.get("bound_voucher") or 0)
    after_voucher = int(after.get("voucher") or 0) + int(after.get("bound_voucher") or 0)
    after_counts = {int(key): int(value) for key, value in (after.get("purchase_counts") or {}).items()}
    if after_voucher != before_voucher - 6 or after_counts.get(WANXIANG_REFUND_GOODS_ID) != 1 or int(after.get("refund_box_count") or 0) != 1:
        raise RuntimeError("六元购买后的代币、白名单商品次数或宝匣账本不精确")

    box = yield from _open_refund_box(context)
    context.set_next_time(None)
    runner._log("success", "万象宝阁：6代币白名单商品购买及宝匣回补已闭环")
    return {
        "ok": True,
        "outcome": "refund_complete",
        "cash_paid_fen": 0,
        "active_goods_id": active_goods_id,
        "voucher_purchase_before": before_voucher,
        "box": box,
    }


__all__ = [
    "STANDARD_JOB_ID",
    "TASK_TYPE",
    "execute_wanxiang_baoge_task",
    "next_wanxiang_retry_time",
]
