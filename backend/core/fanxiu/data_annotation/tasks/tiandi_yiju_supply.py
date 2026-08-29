from __future__ import annotations

"""Minimal storage-bag supply transaction for 天地弈局 rounds."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.data_annotation.tasks.storage_bag_navigation import (
    select_storage_bag_category,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import (
    STORAGE_BAG_SCENE,
    StorageBagRandomBoxRequest,
    plan_current_random_box_click,
)
from backend.core.fanxiu.data_annotation.tasks.xianshi_exchange import (
    quantity_adjustment_shape,
    validate_common_shop_dialog,
)
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import (
    read_common_shop_buy_dialog_snapshot,
)
from backend.core.fanxiu.instrumentation.sacred_exchange_shop import (
    read_sacred_exchange_shop_snapshot,
)
from backend.core.fanxiu.resources.sacred_exchange_planner import (
    SacredExchangeStockPlan,
    plan_sacred_exchange_stock,
)
from backend.core.fanxiu.runtime_gui.sacred_exchange import (
    plan_sacred_exchange_item_click,
    sacred_exchange_quantity_observations,
    visible_sacred_exchange_rows,
)


WORLD_SCENE = 34
ITEM_DETAIL_SCENE = 610
SACRED_ITEM_SCENE = 632
SACRED_SHOP_SCENE = 633
SACRED_BUY_SCENE = 634
SACRED_TREE_ITEM_ID = 1_300_755
TIANDI_YIJU_BOX_ITEM_ID = 100_000_004


def _items(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if (
        snapshot.get("complete") is not True
        or snapshot.get("source") != "active_backpack_panel_item_info_list"
    ):
        raise RuntimeError("天地弈局补给需要完整的当前储物袋 Runtime 快照")
    return [
        row
        for row in snapshot.get("items") or ()
        if isinstance(row, Mapping) and not row.get("is_padding")
    ]


def _total(snapshot: Mapping[str, Any], base_id: int) -> int:
    return sum(
        max(0, int(row.get("num") or 0))
        for row in _items(snapshot)
        if int(row.get("base_id") or 0) == int(base_id)
    )


def _identity(snapshot: Mapping[str, Any]) -> tuple[int, int]:
    evidence = snapshot.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    identity = (
        int(snapshot.get("pid") or evidence.get("pid") or 0),
        int(
            snapshot.get("process_start_ticks")
            or evidence.get("process_start_ticks")
            or 0
        ),
    )
    if min(identity) <= 0 or not str(snapshot.get("fingerprint") or ""):
        raise RuntimeError("天地弈局补给快照缺少进程身份或指纹")
    return identity


def plan_tiandi_yiju_supply(
    backpack: Mapping[str, Any],
    shop: Mapping[str, Any],
    *,
    required_boxes: int,
) -> SacredExchangeStockPlan:
    """Plan only the missing 弈技·仙弈盒 from the exact shop row."""

    plan = plan_sacred_exchange_stock(
        shop,
        target_item_id=TIANDI_YIJU_BOX_ITEM_ID,
        current_stock=_total(backpack, TIANDI_YIJU_BOX_ITEM_ID),
        target_stock=max(0, int(required_boxes)),
    )
    tree_count = _total(backpack, SACRED_TREE_ITEM_ID)
    if plan.cost_item_id != SACRED_TREE_ITEM_ID:
        raise RuntimeError("弈技·仙弈盒消耗物不是灵眼神树")
    affordable = min(
        int(plan.exchange_count),
        tree_count // max(1, int(plan.cost_per_exchange)),
    )
    if affordable <= 0 and plan.projected_stock < max(0, int(required_boxes)):
        raise RuntimeError(f"天地弈局补给不足：当前灵眼神树 {tree_count}")
    if affordable < int(plan.exchange_count) or not plan.ready:
        projected = int(plan.current_stock) + affordable * int(plan.goods_per_exchange)
        plan = replace(
            plan,
            target_stock=projected,
            exchange_count=affordable,
            total_cost=affordable * int(plan.cost_per_exchange),
            projected_stock=projected,
            ready=True,
            reason=f"按当前灵眼神树与限购能力尽量补给至 {projected}",
        )
    return plan


def verify_tiandi_yiju_supply_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    plan: SacredExchangeStockPlan,
) -> None:
    """Require the same process and exact cost/reward inventory deltas."""

    if _identity(before) != _identity(after):
        raise RuntimeError("天地弈局补给前后不属于同一游戏进程")
    tree_delta = _total(after, SACRED_TREE_ITEM_ID) - _total(
        before, SACRED_TREE_ITEM_ID
    )
    box_delta = _total(after, TIANDI_YIJU_BOX_ITEM_ID) - _total(
        before, TIANDI_YIJU_BOX_ITEM_ID
    )
    expected_boxes = plan.exchange_count * plan.goods_per_exchange
    if tree_delta != -plan.total_cost or box_delta != expected_boxes:
        raise RuntimeError(
            "天地弈局补给 Runtime 双差值不成立："
            f"灵眼神树 {tree_delta}/{-plan.total_cost}，"
            f"仙弈盒 {box_delta}/{expected_boxes}"
        )


def _open_daily_bag(runtime: Any):
    yield from runtime.goto_view(WORLD_SCENE)
    yield from runtime.wait_click(WORLD_SCENE, "右侧菜单/储物袋", timeout=10.0)
    yield from runtime.wait_view(STORAGE_BAG_SCENE, timeout=10.0, label="天地弈局：等待储物袋")
    yield from select_storage_bag_category(runtime, "日程")


def _open_sacred_tree(runtime: Any, snapshot: Mapping[str, Any], cards: Mapping[str, Mapping[str, Any]]):
    matches = [row for row in _items(snapshot) if int(row.get("base_id") or 0) == SACRED_TREE_ITEM_ID]
    if len(matches) != 1:
        raise RuntimeError("储物袋灵眼神树实例不唯一")
    row = matches[0]
    name = str((cards.get(str(SACRED_TREE_ITEM_ID)) or {}).get("name") or "").strip()
    request = StorageBagRandomBoxRequest(
        SACRED_TREE_ITEM_ID,
        str(row.get("instance_id") or ""),
        name,
        int(row.get("num") or 0),
    )
    for attempt in range(3):
        click = plan_current_random_box_click(runtime, snapshot, request)
        if click.ready and click.point is not None:
            runtime.click_frame_point(STORAGE_BAG_SCENE, *click.point)
            break
        if attempt == 2:
            raise RuntimeError(f"灵眼神树无法唯一对齐 #525：{click.status}")
        yield from runtime.wait_action_settle(0.3)
    yield from runtime.wait_view(ITEM_DETAIL_SCENE, timeout=8.0, label="天地弈局：灵眼神树详情")
    yield from runtime.wait_click(ITEM_DETAIL_SCENE, "使用（高风险）", timeout=8.0)
    landed = yield from runtime.wait_view(
        SACRED_ITEM_SCENE,
        SACRED_SHOP_SCENE,
        timeout=10.0,
        label="天地弈局：神物兑换",
    )
    return int(getattr(landed, "id", landed))


def _box(raw: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return tuple(float(raw.get(key) or 0.0) for key in ("x", "y", "w", "h"))  # type: ignore[return-value]


def _open_tree_shop(runtime: Any, backpack: Mapping[str, Any]):
    view = runtime.view(SACRED_ITEM_SCENE)
    width, height = runtime.runner._frame_size(view.raw)
    rows = visible_sacred_exchange_rows(
        _box(runtime.shape(SACRED_ITEM_SCENE, "第1行").raw),
        _box(runtime.shape(SACRED_ITEM_SCENE, "第2行").raw),
        _box(runtime.shape(SACRED_ITEM_SCENE, "滚动窗口").raw),
        frame_width=width,
        frame_height=height,
    )
    frame = runtime.cur_frame(update=True)
    observations = sacred_exchange_quantity_observations(
        rows,
        runtime.full_frame_ocr_tokens(frame_data_url=frame),
        runtime_quantities=(int(row.get("num") or 0) for row in _items(backpack)),
    )
    plan = plan_sacred_exchange_item_click(
        backpack,
        target_base_id=SACRED_TREE_ITEM_ID,
        rows=rows,
        observations=observations,
    )
    if not plan.ready or plan.point is None:
        raise RuntimeError(f"神物兑换灵眼神树行未唯一对齐：{plan.status}")
    runtime.click_frame_point(SACRED_ITEM_SCENE, *plan.point)
    yield from runtime.wait_view(SACRED_SHOP_SCENE, timeout=10.0, label="天地弈局：仙弈盒兑换列表")


def _open_box_product(runtime: Any, plan: SacredExchangeStockPlan):
    match = yield from runtime.wait_ocr_any_text(
        SACRED_SHOP_SCENE,
        (plan.target_item_name,),
        in_shapes=("商品滚动窗口",),
        timeout_seconds=20.0,
        max_scrolls_per_direction=12,
        match_mode="exact",
    )
    if match is None:
        raise TimeoutError("神物兑换未找到弈技·仙弈盒")
    row = runtime.shape_box(SACRED_SHOP_SCENE, "商品滚动窗口")
    x = float(row.get("x") or 0.0) + float(row.get("w") or 0.0) * 0.88
    runtime.click_frame_point(SACRED_SHOP_SCENE, x, match.point(anchor="center")[1])
    yield from runtime.wait_view(SACRED_BUY_SCENE, timeout=10.0, label="天地弈局：仙弈盒兑换数量")


def _exchange_quantity(runtime: Any, plan: SacredExchangeStockPlan, reader: Callable[[], Mapping[str, Any]]):
    target = int(plan.exchange_count)
    snapshot = dict(reader())
    maximum = int(snapshot.get("maxNum") or 0)
    current = int(snapshot.get("showNum") or 0)
    if not 1 <= target <= maximum:
        raise RuntimeError(f"仙弈盒兑换数量 {target} 超出 1..{maximum}")
    if current != target and maximum > 1:
        track = runtime.shape_box(SACRED_BUY_SCENE, "数量滑条")
        left = float(track.get("x") or 0.0)
        right = left + float(track.get("w") or 0.0)
        y = float(track.get("y") or 0.0) + float(track.get("h") or 0.0) / 2
        runtime.drag_frame_point(
            SACRED_BUY_SCENE,
            left + (right - left) * ((current - 1) / (maximum - 1)),
            y,
            left + (right - left) * ((target - 1) / (maximum - 1)),
            y,
            duration_ms=600,
        )
        yield from runtime.wait_action_settle(0.8)
    for _ in range(24):
        snapshot = dict(reader())
        current = int(snapshot.get("showNum") or 0)
        if current == target:
            break
        shape = quantity_adjustment_shape(current, target)
        if shape is None:
            break
        runtime.click_shape_center(SACRED_BUY_SCENE, shape)
        yield from runtime.wait_action_settle(0.3)
    else:
        raise RuntimeError("仙弈盒兑换数量未有界收敛")
    snapshot = dict(reader())
    if int(snapshot.get("goodsNum") or 0) != plan.goods_per_exchange:
        raise RuntimeError("仙弈盒 CommonShop 单次产出与计划不一致")
    validate_common_shop_dialog(
        snapshot,
        quantity=target,
        unit_price=plan.cost_per_exchange,
    )
    yield from runtime.wait_click(SACRED_BUY_SCENE, "兑换（高风险）", timeout=8.0)
    yield from runtime.wait_view(SACRED_SHOP_SCENE, timeout=10.0, label="天地弈局：兑换后返回列表")


def ensure_tiandi_yiju_round_supply(
    runtime: Any,
    *,
    required_boxes: int,
    snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,
    shop_reader=read_sacred_exchange_shop_snapshot,
    buy_reader=read_common_shop_buy_dialog_snapshot,
    catalog_reader=lambda: load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"],
):
    """Ensure the requested box floor, with exact Runtime before/after proof."""

    yield from _open_daily_bag(runtime)
    before = dict(snapshot_reader())
    _identity(before)
    if _total(before, TIANDI_YIJU_BOX_ITEM_ID) >= max(0, int(required_boxes)):
        yield from runtime.goto_view(WORLD_SCENE)
        return {"status": "sufficient", "boxes_after": _total(before, TIANDI_YIJU_BOX_ITEM_ID)}
    cards = dict(catalog_reader())
    sacred_scene = yield from _open_sacred_tree(runtime, before, cards)
    if sacred_scene == SACRED_ITEM_SCENE:
        yield from _open_tree_shop(runtime, before)
    elif sacred_scene != SACRED_SHOP_SCENE:
        raise RuntimeError("灵眼神树使用后未进入神物兑换")
    plan = plan_tiandi_yiju_supply(before, dict(shop_reader()), required_boxes=required_boxes)
    yield from _open_box_product(runtime, plan)
    yield from _exchange_quantity(runtime, plan, buy_reader)
    yield from _open_daily_bag(runtime)
    after = dict(snapshot_reader())
    verify_tiandi_yiju_supply_delta(before, after, plan)
    yield from runtime.goto_view(WORLD_SCENE)
    return {
        "status": "supplied",
        "exchange_count": plan.exchange_count,
        "tree_spent": plan.total_cost,
        "boxes_after": _total(after, TIANDI_YIJU_BOX_ITEM_ID),
    }


__all__ = [
    "SACRED_TREE_ITEM_ID",
    "TIANDI_YIJU_BOX_ITEM_ID",
    "ensure_tiandi_yiju_round_supply",
    "plan_tiandi_yiju_supply",
    "verify_tiandi_yiju_supply_delta",
]
