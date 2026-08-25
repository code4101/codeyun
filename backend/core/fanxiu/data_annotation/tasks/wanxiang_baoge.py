from __future__ import annotations

"""Bounded, resumable 万象宝阁 six-yuan refund job."""

import threading
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
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
    load_wanxiang_refund_offer_contract,
    read_wanxiang_baoge_runtime,
)
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
REFRESH_COST = 100
MAX_REFRESHES = 100


def _complete_snapshot() -> dict[str, Any]:
    snapshot = read_wanxiang_baoge_runtime()
    if snapshot.get("complete") is not True:
        raise RuntimeError(str(snapshot.get("reason") or "万象宝阁 Runtime 不完整"))
    return snapshot


def _shape_tokens(runtime: Any, scene_id: int, shape_name: str, frame: str) -> str:
    tokens = runtime.ocr_tokens_in_shapes(
        scene_id,
        (shape_name,),
        frame_data_url=frame,
    )
    ordered = sorted(tokens, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)))
    return "".join(str(item.get("text") or "").strip() for item in ordered)


def _target_slot(runtime: Any, frame: str, goods_ids: list[int]) -> int | None:
    if len(goods_ids) != 5:
        raise RuntimeError("万象宝阁揭晓商品不是五个")
    matches: list[int] = []
    for slot in range(1, 6):
        text = _shape_tokens(runtime, MAIN_REVEALED_SCENE, f"商品{slot}", frame)
        if "0.5折" in text and "120元" in text and "6元" in text:
            matches.append(slot)
    if len(matches) > 1:
        raise RuntimeError(f"万象宝阁出现多个0.5折六元目标槽：{matches}")
    return matches[0] if matches else None


def _open_refund_box(runtime: Any):
    yield from runtime.goto_view(WORLD_SCENE)
    yield from runtime.wait_click(WORLD_SCENE, "右侧菜单/储物袋", timeout=12)
    yield from runtime.wait_scene(
        STORAGE_BAG_SCENE,
        timeout=12,
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
        runtime=runtime,
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
    yield from runtime.wait_click(STORAGE_BAG_SCENE, "返回", timeout=8)
    yield from runtime.wait_scene(WORLD_SCENE, timeout=12, label="万象宝阁：返回世界")
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
    runtime = runner._fanxiu_runtime(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    max_refreshes = max(0, min(MAX_REFRESHES, int(payload.get("max_refreshes") or MAX_REFRESHES)))

    snapshot = read_wanxiang_baoge_runtime()
    if snapshot.get("complete") is not True:
        yield from runtime.goto_view(WORLD_SCENE)
        menu = read_activity_menu_snapshot("world_left")
        targets = [item for item in menu.items if int(item.base_id or 0) == WANXIANG_ACTIVITY_BASE_ID]
        if len(targets) != 1 or targets[0].activity_id is None:
            raise RuntimeError("世界左侧菜单没有唯一万象宝阁实例")
        yield from open_loaded_activity_menu_item(
            runtime,
            int(targets[0].activity_id),
            kind="world_left",
            source_scene_id=WORLD_SCENE,
            ocr_shape_names=("左侧菜单",),
            expected_scene_ids=(MAIN_FREE_SCENE, MAIN_REVEALED_SCENE),
            timeout_seconds=20,
        )
        snapshot = _complete_snapshot()

    if int(snapshot.get("buy_times") or 0) >= 1:
        if int(snapshot.get("refund_box_count") or 0) > 0:
            box = yield from _open_refund_box(runtime)
            return {"ok": True, "outcome": "refund_complete", "cash_paid_fen": 0, "box": box}
        yield from runtime.goto_view(WORLD_SCENE)
        return {"ok": True, "outcome": "already_completed", "cash_paid_fen": 0}

    if int(snapshot.get("refresh_times") or 0) == 0:
        if snapshot.get("goods_ids") != []:
            raise RuntimeError("首次免费状态与商品列表矛盾")
        yield from runtime.wait_click(MAIN_FREE_SCENE, "第一抽免费", timeout=10)
        for _attempt in range(20):
            yield from runtime.wait_action_settle(0.4)
            snapshot = read_wanxiang_baoge_runtime()
            if snapshot.get("complete") and int(snapshot.get("refresh_times") or 0) == 1 and len(snapshot.get("goods_ids") or []) == 5:
                break
        else:
            raise RuntimeError("免费首抽后未取得五个 Runtime 商品")

    purchase_before: dict[str, Any] | None = None
    active_goods_id = 0
    for _round in range(max_refreshes + 1):
        snapshot = _complete_snapshot()
        if int(snapshot.get("buy_times") or 0) >= 1:
            break
        frame = runtime.cur_frame(update=True)
        slot = _target_slot(runtime, frame, [int(value) for value in snapshot.get("goods_ids") or []])
        if slot is not None:
            active_goods_id = int(snapshot["goods_ids"][slot - 1])
            counts = {int(key): int(value) for key, value in (snapshot.get("purchase_counts") or {}).items()}
            if counts.get(active_goods_id, 0) != 0:
                raise RuntimeError("目标商品已购账本与总购买次数矛盾")
            # Read-only detail click proves the icon is the documented box.
            runtime.click_shape(MAIN_REVEALED_SCENE, f"查看商品{slot}", frame_data_url=frame)
            yield from runtime.wait_scene(BOX_DETAIL_SCENE, timeout=10, label="万象宝阁：核对代币宝匣详情")
            yield from runtime.wait_click(BOX_DETAIL_SCENE, "关闭详情", timeout=8)
            yield from runtime.wait_scene(MAIN_REVEALED_SCENE, timeout=10, label="万象宝阁：详情返回")

            purchase_before = _complete_snapshot()
            frame = runtime.cur_frame(update=True)
            runtime.click_shape(MAIN_REVEALED_SCENE, f"购买商品{slot}", frame_data_url=frame)
            yield from runtime.wait_scene(PURCHASE_CONFIRM_SCENE, timeout=10, label="万象宝阁：等待六元确认")
            confirm_frame = runtime.cur_frame(update=True)
            confirm = "".join(str(item.get("text") or "") for item in runtime.full_frame_ocr_tokens(frame_data_url=confirm_frame))
            if "购买商品：代币宝匣" not in confirm or "购买所需：6" not in confirm or "代币购买" not in confirm:
                raise RuntimeError("代币确认页商品或六元金额不完整")
            runtime.click_shape(PURCHASE_CONFIRM_SCENE, "确认代币购买", frame_data_url=confirm_frame)
            for _attempt in range(20):
                yield from runtime.wait_action_settle(0.5)
                after = read_wanxiang_baoge_runtime()
                if after.get("complete") and int(after.get("buy_times") or 0) == int(purchase_before.get("buy_times") or 0) + 1:
                    break
            else:
                raise RuntimeError("六元商品确认后购买账本未变化")
            before_voucher = int(purchase_before.get("voucher") or 0) + int(purchase_before.get("bound_voucher") or 0)
            after_voucher = int(after.get("voucher") or 0) + int(after.get("bound_voucher") or 0)
            after_counts = {int(key): int(value) for key, value in (after.get("purchase_counts") or {}).items()}
            if after_voucher != before_voucher - 6 or after_counts.get(active_goods_id) != 1 or int(after.get("refund_box_count") or 0) != 1:
                raise RuntimeError("六元购买后的代币、商品次数或宝匣账本不精确")
            snapshot = after
            break

        if _round >= max_refreshes:
            raise RuntimeError("有界刷新结束仍未出现0.5折代币宝匣")
        if int(snapshot.get("spirit_stone") or 0) < REFRESH_COST:
            raise RuntimeError("灵石不足100，无法继续刷新")
        before_refresh_times = int(snapshot.get("refresh_times") or 0)
        before_stones = int(snapshot.get("spirit_stone") or 0)
        before_goods = list(snapshot.get("goods_ids") or [])
        yield from runtime.wait_click(MAIN_REVEALED_SCENE, "试试手气", timeout=10)
        for _attempt in range(20):
            yield from runtime.wait_action_settle(0.4)
            refreshed = read_wanxiang_baoge_runtime()
            if refreshed.get("complete") and int(refreshed.get("refresh_times") or 0) == before_refresh_times + 1:
                break
        else:
            raise RuntimeError("灵石刷新后 Runtime 次数未增加")
        if int(refreshed.get("spirit_stone") or 0) != before_stones - REFRESH_COST or list(refreshed.get("goods_ids") or []) == before_goods:
            raise RuntimeError("灵石刷新成本或商品列表变化不精确")

    final_purchase = _complete_snapshot()
    if int(final_purchase.get("refund_box_count") or 0) != 1:
        raise RuntimeError("购买完成后没有唯一代币宝匣")
    box = yield from _open_refund_box(runtime)
    return {
        "ok": True,
        "outcome": "refund_complete",
        "cash_paid_fen": 0,
        "active_goods_id": active_goods_id,
        "voucher_purchase_before": (
            int(purchase_before.get("voucher") or 0) + int(purchase_before.get("bound_voucher") or 0)
            if purchase_before is not None
            else None
        ),
        "box": box,
    }


__all__ = ["STANDARD_JOB_ID", "TASK_TYPE", "execute_wanxiang_baoge_task"]
