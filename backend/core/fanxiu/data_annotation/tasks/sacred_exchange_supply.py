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
from backend.core.fanxiu.runtime_gui.common_shop_quantity import (
    SACRED_SHOP_QUANTITY_ASSETS,
    set_verified_common_shop_quantity,
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
    sacred_shop_product_point,
    sacred_exchange_source_point,
)


WORLD_SCENE = 34
ITEM_DETAIL_SCENE = 610
SACRED_ITEM_SCENE = 632
SACRED_SHOP_SCENE = 633
SACRED_BUY_SCENE = 634


@dataclass(frozen=True)
class SacredExchangeSupplySpec:
    """One ranking-mode binding for the shared 神物兑换 transaction.

    The workflow owns navigation, Runtime-GUI alignment, exact quantity control,
    purchase and double-delta verification.  A ranking mode only supplies the
    source sacred item, target stock item and its shortage policy here.
    """

    label: str
    source_item_id: int
    source_item_name: str
    target_item_id: int
    storage_category: str = "日程"
    allow_partial: bool = False
    # Sacred items can have a description-only detail panel, without the
    # ordinary consumable's effect heading. Bind the verified source asset.
    detail_scene: int = ITEM_DETAIL_SCENE


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


def derive_common_shop_quantity_control(
    snapshot: Mapping[str, Any],
    plan: SacredExchangeStockPlan,
) -> dict[str, int]:
    """Derive the exact exchange count and usable bound from Runtime facts."""

    if snapshot.get("complete") is not True:
        raise RuntimeError(
            f"CommonShop 购买框运行态不完整：{snapshot.get('reason') or snapshot!r}"
        )
    initial = int(snapshot.get("showNum") or 0)
    maximum = int(snapshot.get("maxNum") or 0)
    unit_price = int(snapshot.get("Price") or 0)
    owned = int(snapshot.get("HadPrice") or 0)
    target = int(plan.exchange_count)
    if unit_price != int(plan.cost_per_exchange):
        raise RuntimeError(
            f"神物兑换单价与计划不一致：{unit_price} != {plan.cost_per_exchange}"
        )
    if min(initial, maximum, unit_price, target) <= 0:
        raise RuntimeError("神物兑换数量、上限或单价无效")
    affordable_maximum = owned // unit_price
    if maximum > affordable_maximum:
        raise RuntimeError(
            f"CommonShop 上限 {maximum} 超过资源可负担上限 {affordable_maximum}"
        )
    if target > maximum or target * unit_price > owned:
        raise RuntimeError(
            f"神物兑换目标次数 {target} 超出上限 {maximum} 或资源不足"
        )
    return {
        "initial": initial,
        "target": target,
        "maximum": maximum,
        "unit_price": unit_price,
        "target_cost": target * unit_price,
        "affordable_maximum": affordable_maximum,
    }


def require_sacred_scene(landed: Any, expected: tuple[int, ...]) -> int:
    """Fail closed when a global modal wins an expected scene wait."""
    actual = int(getattr(landed, "id", landed))
    if actual not in expected:
        raise RuntimeError(f"Sacred exchange scene mismatch: expected {expected}, actual #{actual}")
    return actual


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
    runtime_items = runtime_backpack_items(snapshot)
    try:
        target_quantity = int(runtime_items[target.runtime_index].get("num") or 0)
    except (IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f"{spec.source_item_name}的 Runtime 数量不可读") from exc
    if target_quantity <= 0:
        raise RuntimeError(f"{spec.source_item_name}的 Runtime 数量无效：{target_quantity}")
    request = StorageBagRandomBoxRequest(
        spec.source_item_id,
        target.instance_id,
        target.name,
        target_quantity,
    )
    click = yield from plan_current_random_box_click(context, snapshot, request)
    if not click.ready or click.point is None:
        raise RuntimeError(f"{spec.source_item_name}无法唯一对齐储物袋：{click.status}")
    context.click_frame_point(STORAGE_BAG_SCENE, *click.point)
    detail = yield from context.wait_scene(
        [spec.detail_scene], wait=10.0, label=f"{spec.label}：等待{spec.source_item_name}详情"
    )
    require_sacred_scene(detail, (spec.detail_scene,))
    yield from context.wait_click(spec.detail_scene, "使用（高风险）", timeout=8.0)
    landed = yield from context.wait_scene(
        [SACRED_ITEM_SCENE, SACRED_SHOP_SCENE],
        wait=10.0,
        label=f"{spec.label}：等待神物兑换",
    )
    return require_sacred_scene(landed, (SACRED_ITEM_SCENE, SACRED_SHOP_SCENE))


def _box(raw: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return tuple(float(raw.get(key) or 0.0) for key in ("x", "y", "w", "h"))  # type: ignore[return-value]


def _open_source_shop(
    context: Any,
    backpack: Mapping[str, Any],
    spec: SacredExchangeSupplySpec,
):
    # #632 is independently ordered; storage-bag sequence is not its identity.
    quantity = runtime_backpack_total(backpack, spec.source_item_id)
    for direction in ('up', 'down'):
        for scan in range(7):
            frame = context.cur_frame(update=True)
            point = sacred_exchange_source_point(
                context.full_frame_ocr_tokens(frame_data_url=frame),
                window=context.shape_box(SACRED_ITEM_SCENE, "滚动窗口"),
                name=spec.source_item_name, quantity=quantity,
            )
            if point is not None:
                context.click_frame_point(SACRED_ITEM_SCENE, *point)
                landed = yield from context.wait_scene(
                    [SACRED_SHOP_SCENE], wait=10.0,
                    label=f"{spec.label}：等待目标兑换列表",
                )
                require_sacred_scene(landed, (SACRED_SHOP_SCENE,))
                return
            if scan < 6:
                context.drag_shape_content(SACRED_ITEM_SCENE, "滚动窗口", direction=direction)
                yield from context.wait_action_settle(0.5)
    raise RuntimeError(f"神物兑换{spec.source_item_name}未找到唯一名称与实时数量 {quantity}")



def _open_target_product(context: Any, plan: SacredExchangeStockPlan, *, label: str):
    # Bounded overlapping scans; name alone cannot distinguish the regular
    # offer from exhausted discounted/free copies of the same item.
    for direction in ("up", "down"):
        for scan in range(13):
            frame = context.cur_frame(update=True)
            point = sacred_shop_product_point(
                context.full_frame_ocr_tokens(frame_data_url=frame),
                window=context.shape_box(SACRED_SHOP_SCENE, "商品滚动窗口"),
                name=plan.target_item_name, unit_price=plan.cost_per_exchange,
            )
            if point is not None:
                context.click_frame_point(SACRED_SHOP_SCENE, *point)
                landed = yield from context.wait_scene(
                    [SACRED_BUY_SCENE], wait=10.0, label=f"{label}：等待兑换数量"
                )
                require_sacred_scene(landed, (SACRED_BUY_SCENE,))
                return
            if scan < 12:
                context.drag_shape_content(
                    SACRED_SHOP_SCENE, "商品滚动窗口", direction=direction,
                    cross_axis_ratio=0.9,
                )
                yield from context.wait_action_settle(0.7)
    raise TimeoutError(f"神物兑换未找到唯一的{plan.target_item_name}/单价{plan.cost_per_exchange}")


def verify_sacred_buy_identity(snapshot: Mapping[str, Any], plan: SacredExchangeStockPlan) -> None:
    """Reject wrong, stale or unreadable SKUs before touching purchase."""
    if snapshot.get("identity_complete") is not True:
        raise RuntimeError("神物购买框商品身份不完整")
    expected = {"item_id": plan.target_item_id, "cost_item_id": plan.cost_item_id}
    if plan.goods_id > 0:
        expected["goods_id"] = plan.goods_id
    if any(int(snapshot.get(k) or 0) != value for k, value in expected.items()):
        raise RuntimeError("神物购买框商品/消耗物身份与计划不一致")


def _exchange_quantity(
    context: Any,
    plan: SacredExchangeStockPlan,
    reader: Callable[[], Mapping[str, Any]],
    *,
    label: str,
):
    initial = dict(reader())
    verify_sacred_buy_identity(initial, plan)
    control = derive_common_shop_quantity_control(initial, plan)

    quantity_proof = yield from set_verified_common_shop_quantity(
        context,
        control["target"],
        unit_price=plan.cost_per_exchange,
        label=label,
        assets=SACRED_SHOP_QUANTITY_ASSETS,
        initial_snapshot=initial,
        snapshot_reader=reader,
    )
    adjustment = dict(quantity_proof.get("adjustment") or {})
    if int(adjustment.get("after") or 0) != control["target"]:
        raise RuntimeError(f"{label}兑换数量未精确回读为 {control['target']}")
    snapshot = dict(quantity_proof.get("snapshot") or {})
    verify_sacred_buy_identity(snapshot, plan)
    if (initial.get("pid"), initial.get("process_start_ticks")) != (snapshot.get("pid"), snapshot.get("process_start_ticks")):
        raise RuntimeError("神物购买框数量调整前后进程身份变化")
    if int(snapshot.get("goodsNum") or 0) != plan.goods_per_exchange:
        raise RuntimeError(f"{label}单次产出与计划不一致")
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
    """Raise ``spec.target_item_id`` to ``required_stock`` through 神物兑换.

    This is the public business API for ranking-mode sacred-item exchange.  It
    derives price, output ratio, limits and affordable quantity from live
    Runtime state; callers must not reproduce the GUI sequence or assume that
    requested item count equals slider count or source-item cost.
    """

    before = yield from _open_storage_category(
        context,
        spec,
        snapshot_reader=snapshot_reader,
    )
    runtime_backpack_identity(before)
    current = runtime_backpack_total(before, spec.target_item_id)
    required = max(0, int(required_stock))
    if current >= required:
        yield from context.go_scene(WORLD_SCENE)
        return {"status": "sufficient", "stock_after": current}
    cards = dict(catalog_reader())
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
    "verify_sacred_buy_identity",
    "SacredExchangeSupplySpec",
    "derive_common_shop_quantity_control",
    "ensure_sacred_exchange_stock",
    "plan_sacred_exchange_supply",
    "runtime_backpack_identity",
    "runtime_backpack_items",
    "runtime_backpack_total",
    "verify_sacred_exchange_supply_delta",
]
