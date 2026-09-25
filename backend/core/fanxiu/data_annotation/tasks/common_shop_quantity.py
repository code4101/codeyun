from __future__ import annotations

"""Verified quantity control for the shared CommonShop purchase dialog."""

from collections.abc import Callable, Iterator, Mapping
from typing import Any

from backend.core.fanxiu.data_annotation.tasks.integer_count_control import (
    IntegerSliderAssets,
    set_verified_integer_slider_count,
)
from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import (
    read_common_shop_buy_dialog_snapshot,
)


COMMON_SHOP_DIALOG_SCENE = 566
COMMON_SHOP_QUANTITY_ASSETS = IntegerSliderAssets(
    settings_scene_id=COMMON_SHOP_DIALOG_SCENE,
    count_region="数量",
    count_decrease="-",
    count_increase="+",
    count_decrease_large="-10",
    count_increase_large="+10",
    count_large_step=10,
    count_slider_thumb="兑换数量_滑块游标",
    count_slider_left_anchor="兑换数量_滑轨左端",
    count_slider_right_anchor="兑换数量_滑轨右端",
    # #566 at maxNum=270: the live thumb centre is x=646.7. The
    # reference anchor is x=655.2 and the template width is 34.2,
    # so the generic half-template inset is 8.6px
    # too large. Bind the correction to this dialog, not every slider.
    count_slider_right_center_offset=8.6,
)
SACRED_SHOP_DIALOG_SCENE = 634
SACRED_SHOP_QUANTITY_ASSETS = IntegerSliderAssets(
    settings_scene_id=SACRED_SHOP_DIALOG_SCENE,
    count_region="数量滑条",
    count_decrease="-",
    count_increase="+",
    count_decrease_large="-10",
    count_increase_large="+10",
    count_large_step=10,
    count_slider_thumb=None,
    count_slider_track="数量滑条",
)


def _require_snapshot(
    raw: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    snapshot = dict(raw)
    if snapshot.get("complete") is not True:
        raise RuntimeError(
            f"{label}：购买框运行态不完整：{snapshot.get('reason') or snapshot!r}"
        )
    return snapshot


def set_verified_common_shop_quantity(
    context: Any,
    desired: int,
    *,
    unit_price: int,
    label: str,
    assets: IntegerSliderAssets = COMMON_SHOP_QUANTITY_ASSETS,
    initial_snapshot: Mapping[str, Any] | None = None,
    snapshot_reader: Callable[[], Mapping[str, Any]] = (
        read_common_shop_buy_dialog_snapshot
    ),
) -> Iterator[Any]:
    """Set an exact CommonShop quantity and return pre-purchase Runtime proof."""

    target = int(desired)
    price = int(unit_price)
    initial = _require_snapshot(
        initial_snapshot if initial_snapshot is not None else snapshot_reader(),
        label=label,
    )
    maximum = int(initial.get("maxNum") or 0)
    if not 1 <= target <= maximum:
        raise RuntimeError(f"{label}：购买数量 {target} 超出 1..{maximum}")
    if price <= 0 or int(initial.get("Price") or 0) != price:
        raise RuntimeError(
            f"{label}：购买框单价 {initial.get('Price')!r} != {price}"
        )

    # Full redemption needs Runtime only for range and final purchase proof.
    # Live #566 acceptance covers the Beast Abyss quantity dialog.
    if target == maximum and assets.count_slider_right_anchor:
        # Full redemption is the usual shop operation. The right-end Shape
        # names the track endpoint; +10 uses the game's natural cap. There
        # is no proportional probe or unit-by-unit tuning on this path.
        context.click_shape_center(
            assets.settings_scene_id, assets.count_slider_right_anchor,
        )
        yield from context.wait_action_settle(0.5)
        # The game clamps +10 at maxNum. One saturating action completes the
        # small endpoint residual without measuring it or issuing unit clicks.
        if assets.count_increase_large:
            context.click_shape_center(
                assets.settings_scene_id, assets.count_increase_large,
            )
        yield from context.wait_action_settle(0.5)
        adjustment = {"phase": "maximum_endpoint", "before": int(initial["showNum"])}
    elif target == maximum and assets.count_increase_large and assets.count_large_step:
        # The dialog clamps +10 at maxNum: 1 -> 20 needs two taps, not +10
        # followed by nine unit taps. Verify once after the bounded burst.
        import math
        clicks=math.ceil((target-int(initial['showNum']))/assets.count_large_step)
        if clicks>100:
            raise RuntimeError(f'{label}：拉满点击数超过预算，需滑条定位')
        for _ in range(clicks):
            context.click_shape_center_fast(assets.settings_scene_id,assets.count_increase_large)
            yield from context.wait_action_settle(0.18)
        yield from context.wait_action_settle(0.75)
        adjustment={'phase':'maximum_endpoint','before':int(initial['showNum'])}
    else:
        # Preserve the exact partial-quantity path. In particular, #566's
        # single-digit label can be missed by OCR; it must not become zero.
        def runtime_count() -> dict[str, int]:
            current = _require_snapshot(snapshot_reader(), label=label)
            return {
                "current": int(current.get("showNum") or 0),
                "maximum": int(current.get("maxNum") or 0),
            }

        adjustment = yield from set_verified_integer_slider_count(
            context,
            assets,
            target,
            maximum=maximum,
            # A +/-10 dialog can finish a <100 residual in at most 18
            # button actions; pixel calibration is wasteful at this scale.
            max_adjustments=10 * int(assets.count_large_step or 1),
            count_label=f"{label}购买数量",
            initial_count=int(initial["showNum"]),
            runtime_count_reader=runtime_count,
        )
    if adjustment.get("phase") != "maximum_endpoint" and int(adjustment.get("after") or 0) != target:
        raise RuntimeError(f"{label}：数量控制器未精确收敛到 {target}")

    final = _require_snapshot(snapshot_reader(), label=label)
    actual = int(final.get("showNum") or 0)
    adjustment["after"] = actual
    actual_price = int(final.get("Price") or 0)
    owned = int(final.get("HadPrice") or 0)
    expected_total = target * price
    if actual != target:
        raise RuntimeError(f"{label}：购买前数量 {actual} != {target}")
    if actual_price != price:
        raise RuntimeError(f"{label}：购买前单价 {actual_price} != {price}")
    if owned < expected_total:
        raise RuntimeError(f"{label}：购买前余额 {owned} 小于总价 {expected_total}")
    if final.get("CanBuy") is not True or final.get("isEnough") is not True:
        raise RuntimeError(
            f"{label}：购买资格未闭环："
            f"CanBuy={final.get('CanBuy')!r}, isEnough={final.get('isEnough')!r}"
        )
    return {
        "quantity": target,
        "maximum": maximum,
        "unit_price": price,
        "expected_total": expected_total,
        "owned_currency": owned,
        "adjustment": adjustment,
        "snapshot": final,
    }


__all__ = [
    "COMMON_SHOP_DIALOG_SCENE",
    "COMMON_SHOP_QUANTITY_ASSETS",
    "SACRED_SHOP_DIALOG_SCENE",
    "SACRED_SHOP_QUANTITY_ASSETS",
    "set_verified_common_shop_quantity",
]
