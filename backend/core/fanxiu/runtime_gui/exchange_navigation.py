"""兑换宝阁公共导航：在已有商店中定位商品并打开经核对的详情。

调用者提供商店/购买框资产和商品名称、单价；本模块负责滚动、瞬态等待、
OCR 行对齐和详情核对，不分配预算、不设置数量、不点击购买。确认消费仍
由上层购买器根据 Runtime 身份与资源授权完成。错误保留商品和换页证据。
"""
from __future__ import annotations

import re
from typing import Any

from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens as _group_ocr_tokens
from backend.core.fanxiu.runtime_gui import ocr_name_similarity
from backend.core.fanxiu.runtime_gui.exchange_shop import resolve_exchange_shop_item

COMMON_SHOP_DIALOG_SCENE = 566
# #519 实测：快速短滑不能移动此滚动容器；换页沿用已验证的慢滑参数。
SHOP_TRAVERSAL_RATIO = 0.9
SHOP_TRAVERSAL_DURATION_SECONDS = 0.8


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or ""))


def _verify_dialog(
    context: Any,
    *,
    name: str,
    unit_price: int,
    dialog_scene: int = COMMON_SHOP_DIALOG_SCENE,
    label: str = "兑换宝阁",
) -> None:
    title = context.ocr_text_in_shapes(
        dialog_scene,
        ("商品标题",),
        padding=10,
    )
    if ocr_name_similarity(_compact(name), _compact(title)) < 0.78:
        raise RuntimeError(f"{label}：购买框商品未对齐 {name}：{title}")
    values, text = context.ocr_numbers_in_shapes(
        dialog_scene,
        ("价格",),
        padding=8,
    )
    digits = re.sub(r"\D+", "", str(text or ""))
    if int(unit_price) not in {int(value) for value in values} and str(unit_price) not in digits:
        raise RuntimeError(
            f"{label}：{name} 单价未对齐 Runtime {unit_price}：{text}"
        )

def open_exchange_shop_product(
    context: Any,
    *,
    name: str,
    unit_price: int,
    max_scrolls: int,
    shop_scene: int,
    product_list_shape: str = "商品列表",
    product_row_shapes: tuple[str, ...] = (
        "商品行1",
        "商品行2",
        "商品行3",
        "商品行4",
        "商品行5",
    ),
    dialog_scene: int = COMMON_SHOP_DIALOG_SCENE,
    label: str = "兑换宝阁",
):
    """Find the next planned product by live row identity before clicking it.

    The row remove/reorder contract differs across activity shop variants, so
    callers must pass the shop geometry instead of assuming Runtime order maps
    onto GUI rows.  Every click is gated by live name+unit-price alignment.
    """

    view = context.view(shop_scene)
    product_list = view.get_shape(product_list_shape)
    rows = [view.get_shape(shape) for shape in product_row_shapes]
    if product_list is None or any(row is None for row in rows):
        raise RuntimeError(f"{label}：缺少 #{shop_scene} 商品列表正式几何")

    # A completed purchase may remove or reorder rows.  Re-establish the list
    # origin before resolving every next action, then scan from top to bottom.
    # ``scroll_shape_content`` follows the same direction contract as the
    # auto-configurator: ``up`` returns to the top and ``down`` advances to
    # later rows.
    for _attempt in range(max(0, int(max_scrolls)) + 1):
        changed = yield from context.scroll_shape_content(
            shop_scene,
            product_list_shape,
            direction="up",
            ratio=SHOP_TRAVERSAL_RATIO,
            duration=SHOP_TRAVERSAL_DURATION_SECONDS,
            unchanged_confirmations=2,
        )
        if not changed:
            break
    else:
        raise RuntimeError(f"{label}：商品列表在有界次数内未能归顶")

    last_error = ""
    page_changes = 0
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
                    context.click_shape_center(shop_scene, "兑换宝阁标题")
                    yield from context.wait_action_settle(0.35)
                continue
            context.click_frame_point(shop_scene, target.x, target.y)
            landed = yield from context.wait_scene(
                [dialog_scene],
                wait=15.0,
                label=f"{label}：等待 {name} 购买框",
            )
            _verify_dialog(
                context, name=name, unit_price=unit_price, dialog_scene=dialog_scene, label=label
            )
            return landed

        # Full-frame OCR can merge the item title with rotating effect text
        # below it (observed: 咒残页 -> 九转炽阳).  A cropped pass over the
        # formal list restores the product line while retaining global row
        # coordinates.  Keep this as a fallback so the common fast path stays
        # cheap.
        context.click_shape_center(shop_scene, "兑换宝阁标题")
        yield from context.wait_action_settle(0.35)
        cropped_tokens = tuple(context.ocr_tokens_in_shapes(
            shop_scene,
            (product_list_shape,),
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
            context.click_frame_point(shop_scene, target.x, target.y)
            landed = yield from context.wait_scene(
                [dialog_scene],
                wait=15.0,
                label=f"{label}：等待 {name} 购买框",
            )
            _verify_dialog(
                context, name=name, unit_price=unit_price, dialog_scene=dialog_scene, label=label
            )
            return landed

        if scroll_index >= max_scrolls:
            break
        changed = yield from context.scroll_shape_content(
            shop_scene,
            product_list_shape,
            direction="down",
            ratio=SHOP_TRAVERSAL_RATIO,
            duration=SHOP_TRAVERSAL_DURATION_SECONDS,
            unchanged_confirmations=2,
        )
        if not changed:
            break
        page_changes += 1
    # 换页次数是本轮遍历的实际证据：0 次说明列表没有位移，
    # 此时"未找到"不能当作"商品不存在"，两者必须能被区分。
    raise RuntimeError(
        f"{label}：有界滚动后未找到商品 {name}({unit_price})："
        f"换页 {page_changes} 次，{last_error}"
    )
