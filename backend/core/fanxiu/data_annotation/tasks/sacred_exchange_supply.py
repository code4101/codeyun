from __future__ import annotations

"""Shared Runtime-GUI transaction for replenishing stock via 神物兑换."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
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
from backend.core.fanxiu.runtime_gui.storage_bag_alignment import (
    prepare_storage_bag_target_by_name,
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


@dataclass(frozen=True)
class SacredExchangeSupplySpec:
    label: str
    source_item_id: int
    source_item_name: str
    target_item_id: int
    storage_category: str = "日程"
    allow_partial: bool = False


def runtime_backpack_items(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if (
        snapshot.get("complete") is not True
        or snapshot.get("source") != "active_backpack_panel_item_info_list"
    ):
        raise RuntimeError("神物兑换补给需要完整的当前储物袋 Runtime 快照")
    return [
        row
        for row in snapshot.get("items") or ()
        if isinstance(row, Mapping) and not row.get("is_padding")
    ]


def runtime_backpack_total(snapshot: Mapping[str, Any], base_id: int) -> int:
    return sum(
        max(0, int(row.get("num") or 0))
        for row in runtime_backpack_items(snapshot)
        if int(row.get("base_id") or 0) == int(base_id)
    )


def runtime_backpack_identity(snapshot: Mapping[str, Any]) -> tuple[int, int]:
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
        raise RuntimeError("神物兑换补给快照缺少进程身份或指纹")
    return identity


def plan_sacred_exchange_supply(
    backpack: Mapping[str, Any],
    shop: Mapping[str, Any],
    *,
    spec: SacredExchangeSupplySpec,
    required_stock: int,
) -> SacredExchangeStockPlan:
    required = max(0, int(required_stock))
    plan = plan_sacred_exchange_stock(
        shop,
        target_item_id=spec.target_item_id,
        current_stock=runtime_backpack_total(backpack, spec.target_item_id),
        target_stock=required,
    )
    if plan.cost_item_id != spec.source_item_id:
        raise RuntimeError(
            f"{spec.label}消耗物 {plan.cost_item_id} 不是 {spec.source_item_name}"
        )
    source_count = runtime_backpack_total(backpack, spec.source_item_id)
    affordable = min(
        int(plan.exchange_count),
        source_count // max(1, int(plan.cost_per_exchange)),
    )
    projected = int(plan.current_stock) + affordable * int(plan.goods_per_exchange)
    if not spec.allow_partial and (not plan.ready or affordable < int(plan.exchange_count)):
        raise RuntimeError(
            f"{spec.label}不足：当前{spec.source_item_name} {source_count}，"
            f"最多补到 {projected}，目标 {required}"
        )
    if affordable <= 0 and projected < required:
        raise RuntimeError(f"{spec.label}不足：当前{spec.source_item_name} {source_count}")
    if affordable < int(plan.exchange_count) or not plan.ready:
        plan = replace(
            plan,
            target_stock=projected,
            exchange_count=affordable,
            total_cost=affordable * int(plan.cost_per_exchange),
            projected_stock=projected,
            ready=True,
            reason=f"按当前{spec.source_item_name}与限购能力尽量补给至 {projected}",
        )
    return plan


def verify_sacred_exchange_supply_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    plan: SacredExchangeStockPlan,
) -> None:
    if runtime_backpack_identity(before) != runtime_backpack_identity(after):
        raise RuntimeError("神物兑换补给前后不属于同一游戏进程")
    cost_delta = runtime_backpack_total(after, plan.cost_item_id) - runtime_backpack_total(
        before, plan.cost_item_id
    )
    stock_delta = runtime_backpack_total(after, plan.target_item_id) - runtime_backpack_total(
        before, plan.target_item_id
    )
    expected_stock = plan.exchange_count * plan.goods_per_exchange
    if cost_delta != -plan.total_cost or stock_delta != expected_stock:
        raise RuntimeError(
            "神物兑换补给 Runtime 双差值不成立："
            f"消耗物 {cost_delta}/{-plan.total_cost}，目标物 {stock_delta}/{expected_stock}"
        )


def _open_storage_category(
    context: Any,
    spec: SacredExchangeSupplySpec,
    *,
    snapshot_reader: Callable[[], Mapping[str, Any]],
):
    yield from context.go_scene(WORLD_SCENE)
    yield from context.wait_click(WORLD_SCENE, "右侧菜单/储物袋", timeout=10.0)
    yield from context.wait_scene(
        [STORAGE_BAG_SCENE], wait=10.0, label=f"{spec.label}：等待储物袋"
    )
    yield from select_storage_bag_category(context, spec.storage_category)
    snapshot = dict(snapshot_reader())
    runtime_backpack_items(snapshot)
    return snapshot


def _open_source_exchange(
    context: Any,
    spec: SacredExchangeSupplySpec,
    target: Any,
    snapshot: Mapping[str, Any],
):
    request = StorageBagRandomBoxRequest(
        spec.source_item_id,
        target.instance_id,
        target.name,
        target.quantity,
    )
    click = plan_current_random_box_click(context, snapshot, request)
    if not click.ready or click.point is None:
        raise RuntimeError(f"{spec.source_item_name}无法唯一对齐储物袋：{click.status}")
    context.click_frame_point(STORAGE_BAG_SCENE, *click.point)
    yield from context.wait_scene(
        [ITEM_DETAIL_SCENE], wait=10.0, label=f"{spec.label}：等待{spec.source_item_name}详情"
    )
    yield from context.wait_click(ITEM_DETAIL_SCENE, "使用（高风险）", timeout=8.0)
    landed = yield from context.wait_scene(
        [SACRED_ITEM_SCENE, SACRED_SHOP_SCENE],
        wait=10.0,
        label=f"{spec.label}：等待神物兑换",
    )
    return int(getattr(landed, "id", landed))


def _box(raw: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return tuple(float(raw.get(key) or 0.0) for key in ("x", "y", "w", "h"))  # type: ignore[return-value]


def _open_source_shop(
    context: Any,
    backpack: Mapping[str, Any],
    spec: SacredExchangeSupplySpec,
):
    view = context.view(SACRED_ITEM_SCENE)
    width, height = context.runner._frame_size(view.raw)
    rows = visible_sacred_exchange_rows(
        _box(context.shape(SACRED_ITEM_SCENE, "第1行").raw),
        _box(context.shape(SACRED_ITEM_SCENE, "第2行").raw),
        _box(context.shape(SACRED_ITEM_SCENE, "滚动窗口").raw),
        frame_width=width,
        frame_height=height,
    )
    frame = context.cur_frame(update=True)
    observations = sacred_exchange_quantity_observations(
        rows,
        context.full_frame_ocr_tokens(frame_data_url=frame),
        runtime_quantities=(
            int(row.get("num") or 0) for row in runtime_backpack_items(backpack)
        ),
    )
    click = plan_sacred_exchange_item_click(
        backpack,
        target_base_id=spec.source_item_id,
        rows=rows,
        observations=observations,
    )
    if not click.ready or click.point is None:
        raise RuntimeError(
            f"神物兑换{spec.source_item_name}行未唯一对齐：{click.status}"
        )
    context.click_frame_point(SACRED_ITEM_SCENE, *click.point)
    yield from context.wait_scene(
        [SACRED_SHOP_SCENE], wait=10.0, label=f"{spec.label}：等待目标兑换列表"
    )


def _open_target_product(context: Any, plan: SacredExchangeStockPlan, *, label: str):
    match = yield from context.wait_ocr_any_text(
        SACRED_SHOP_SCENE,
        (plan.target_item_name,),
        in_shapes=("商品滚动窗口",),
        timeout_seconds=20.0,
        max_scrolls_per_direction=12,
        match_mode="exact",
    )
    if match is None:
        raise TimeoutError(f"神物兑换未找到{plan.target_item_name}")
    window = context.shape_box(SACRED_SHOP_SCENE, "商品滚动窗口")
    x = float(window.get("x") or 0.0) + float(window.get("w") or 0.0) * 0.88
    context.click_frame_point(SACRED_SHOP_SCENE, x, match.point(anchor="center")[1])
    yield from context.wait_scene(
        [SACRED_BUY_SCENE], wait=10.0, label=f"{label}：等待兑换数量"
    )


def _exchange_quantity(
    context: Any,
    plan: SacredExchangeStockPlan,
    reader: Callable[[], Mapping[str, Any]],
    *,
    label: str,
):
    target = int(plan.exchange_count)
    snapshot = dict(reader())
    maximum = int(snapshot.get("maxNum") or 0)
    current = int(snapshot.get("showNum") or 0)
    if not 1 <= target <= maximum:
        raise RuntimeError(f"{label}兑换数量 {target} 超出 1..{maximum}")
    if current != target and maximum > 1:
        track = context.shape_box(SACRED_BUY_SCENE, "数量滑条")
        left = float(track.get("x") or 0.0)
        right = left + float(track.get("w") or 0.0)
        y = float(track.get("y") or 0.0) + float(track.get("h") or 0.0) / 2
        context.drag_frame_point(
            SACRED_BUY_SCENE,
            left + (right - left) * ((current - 1) / (maximum - 1)),
            y,
            left + (right - left) * ((target - 1) / (maximum - 1)),
            y,
            duration_ms=600,
        )
        yield from context.wait_action_settle(0.8)
    for _ in range(24):
        snapshot = dict(reader())
        current = int(snapshot.get("showNum") or 0)
        if current == target:
            break
        shape = quantity_adjustment_shape(current, target)
        if shape is None:
            break
        context.click_shape_center(SACRED_BUY_SCENE, shape)
        yield from context.wait_action_settle(0.3)
    else:
        raise RuntimeError(f"{label}兑换数量未有界收敛")
    snapshot = dict(reader())
    if int(snapshot.get("goodsNum") or 0) != plan.goods_per_exchange:
        raise RuntimeError(f"{label}单次产出与计划不一致")
    validate_common_shop_dialog(
        snapshot,
        quantity=target,
        unit_price=plan.cost_per_exchange,
    )
    yield from context.wait_click(SACRED_BUY_SCENE, "兑换（高风险）", timeout=8.0)
    yield from context.wait_scene(
        [SACRED_SHOP_SCENE], wait=10.0, label=f"{label}：兑换后返回列表"
    )


def ensure_sacred_exchange_stock(
    context: Any,
    *,
    spec: SacredExchangeSupplySpec,
    required_stock: int,
    snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,
    shop_reader=read_sacred_exchange_shop_snapshot,
    buy_reader=read_common_shop_buy_dialog_snapshot,
    catalog_reader=lambda: load_fanxiu_item_runtime_index(rebuild_missing=False)[
        "cards_by_id"
    ],
):
    cards = dict(catalog_reader())
    before = yield from _open_storage_category(
        context,
        spec,
        snapshot_reader=snapshot_reader,
    )
    runtime_backpack_identity(before)
    required = max(0, int(required_stock))
    current = runtime_backpack_total(before, spec.target_item_id)
    if current >= required:
        yield from context.go_scene(WORLD_SCENE)
        return {"status": "sufficient", "stock_after": current}
    target = prepare_storage_bag_target_by_name(
        before,
        name=spec.source_item_name,
        catalog_cards_by_id=cards,
    )
    landed = yield from _open_source_exchange(context, spec, target, before)
    if landed == SACRED_ITEM_SCENE:
        yield from _open_source_shop(context, before, spec)
    elif landed != SACRED_SHOP_SCENE:
        raise RuntimeError(f"{spec.source_item_name}使用后未进入神物兑换商品列表")
    plan = plan_sacred_exchange_supply(
        before,
        dict(shop_reader()),
        spec=spec,
        required_stock=required,
    )
    yield from _open_target_product(context, plan, label=spec.label)
    yield from _exchange_quantity(context, plan, buy_reader, label=spec.label)
    after = yield from _open_storage_category(
        context,
        spec,
        snapshot_reader=snapshot_reader,
    )
    verify_sacred_exchange_supply_delta(before, after, plan)
    stock_after = runtime_backpack_total(after, spec.target_item_id)
    if not spec.allow_partial and stock_after < required:
        raise RuntimeError(f"{spec.label}后库存 {stock_after} 未达到 {required}")
    yield from context.go_scene(WORLD_SCENE)
    return {
        "status": "supplied",
        "exchange_count": plan.exchange_count,
        "cost_spent": plan.total_cost,
        "stock_after": stock_after,
    }


__all__ = [
    "SacredExchangeSupplySpec",
    "ensure_sacred_exchange_stock",
    "plan_sacred_exchange_supply",
    "runtime_backpack_identity",
    "runtime_backpack_items",
    "runtime_backpack_total",
    "verify_sacred_exchange_supply_delta",
]
