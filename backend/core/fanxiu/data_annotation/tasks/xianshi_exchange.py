from __future__ import annotations

"""Weekly Xianshi GongFa exchanges backed by saved #467-#470 assets."""

from collections import defaultdict
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from backend.core.fanxiu.data_annotation.job_times import next_business_time
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import (
    read_common_shop_buy_dialog_snapshot,
)
from backend.core.fanxiu.instrumentation.exchange_shop import read_exchange_shop_runtime
from backend.core.fanxiu.instrumentation.gongfa_atlas import read_gongfa_atlas_runtime


TUESDAY = 1
ZHENWUGE_SCENE = 467
ZHENWUGE_DETAIL_SCENE = 468
LANGYAGE_SCENE = 469
LANGYAGE_DETAIL_SCENE = 470
COMMON_SHOP_DETAIL_SCENE = 634
LANGYAGE_FUSION_CAP = 100
_CATEGORY_TABS = frozenset({"剑修", "法修", "魔修", "体修"})


def quantity_clicks(quantity: int) -> tuple[int, int]:
    """Return (+10 clicks, +1 clicks) for a dialog whose initial value is one."""

    value = int(quantity)
    if value < 1:
        raise ValueError("兑换数量至少为 1")
    return divmod(value - 1, 10)


def _book_index(books: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    return {
        int(book.get("book_id") or 0): dict(book)
        for book in books
        if int(book.get("book_id") or 0) > 0
    }


def _priority(book: Mapping[str, Any]) -> tuple[int, int, int]:
    return (
        int(book.get("upgrade_index") or 10**9),
        -int(book.get("quality_grade_order") or 0),
        int(book.get("book_id") or 0),
    )


def plan_zhenwuge_candidates(
    books: Iterable[Mapping[str, Any]],
    shop_items: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Rank finite 悟境 books; 满甲元 candidates lead their currency pool."""

    by_id = _book_index(books)
    candidates: list[dict[str, Any]] = []
    for raw_item in shop_items:
        item = dict(raw_item)
        book = by_id.get(int(item.get("linked_gongfa_id") or 0))
        if book is None or int(item.get("item_type") or 0) != 999 or int(item.get("item_sub_type") or 0) != 33:
            continue
        max_wujing = int(book.get("max_wujing") or 0)
        wujing = int(book.get("wujing") or 0)
        remaining_stock = int(item.get("remaining") or 0)
        desired = min(max(0, max_wujing - wujing), remaining_stock)
        if desired <= 0 or bool(item.get("unlimited")):
            continue
        candidates.append({**item, "book": book, "desired": desired})
    candidates.sort(key=lambda row: (
        int(row.get("cost_item_id") or 0),
        0 if bool(row["book"].get("full")) else 1,
        _priority(row["book"]),
    ))
    return candidates


def plan_langyage_candidates(
    books: Iterable[Mapping[str, Any]],
    shop_items: Iterable[Mapping[str, Any]],
    backpack_counts: Mapping[int, int],
    *,
    fusion_cap: int = LANGYAGE_FUSION_CAP,
) -> list[dict[str, Any]]:
    """Rank unlimited GongFa books and cap fused + unconsumed copies at 100."""

    by_id = _book_index(books)
    candidates: list[dict[str, Any]] = []
    for raw_item in shop_items:
        item = dict(raw_item)
        book = by_id.get(int(item.get("linked_gongfa_id") or 0))
        if book is None or not bool(item.get("unlimited")):
            continue
        # The shop also contains an unlimited 仙术 tab.  Only the 神通/心法
        # atlas participates in the user's declared priority programme.
        if str(book.get("skill_type_name") or "") not in {"神通", "心法"}:
            continue
        if str(book.get("filter_category") or "") not in _CATEGORY_TABS:
            continue
        owned_books = max(0, int(backpack_counts.get(int(item.get("item_id") or 0), 0)))
        effective_fusion = int(book.get("jie") or 0) + owned_books
        desired = max(0, int(fusion_cap) - effective_fusion)
        if desired <= 0:
            continue
        candidates.append({
            **item,
            "book": book,
            "backpack_count": owned_books,
            "effective_fusion": effective_fusion,
            "desired": desired,
        })
    candidates.sort(key=lambda row: (
        int(row.get("cost_item_id") or 0),
        _priority(row["book"]),
    ))
    return candidates


def _numbers(runtime: Any, scene_id: int, shape: str) -> tuple[list[int], str]:
    values, text = runtime.ocr_numbers_in_shapes(scene_id, (shape,), padding=12)
    return [int(value) for value in values], str(text or "")


def quantity_adjustment_shape(current: int, target: int) -> str | None:
    """Choose one idempotent quantity adjustment from the observed value."""

    delta = int(target) - int(current)
    if delta >= 10:
        return "+10"
    if delta > 0:
        return "+"
    if delta <= -10:
        return "-10"
    if delta < 0:
        return "-"
    return None


def validate_common_shop_dialog(
    snapshot: Mapping[str, Any],
    *,
    quantity: int,
    unit_price: int,
) -> int:
    """Validate the active buy dialog and return its authoritative currency."""

    if snapshot.get("complete") is not True:
        raise RuntimeError(f"CommonShop 购买框运行态不完整：{snapshot.get('reason') or snapshot!r}")
    show_num = int(snapshot.get("showNum") or 0)
    price = int(snapshot.get("Price") or 0)
    owned = int(snapshot.get("HadPrice") or 0)
    if show_num != int(quantity):
        raise RuntimeError(f"CommonShop 数量未闭环：期望 {quantity}，实际 {show_num}")
    if price != int(unit_price):
        raise RuntimeError(f"CommonShop 单价未闭环：期望 {unit_price}，实际 {price}")
    total = int(quantity) * int(unit_price)
    if owned < total:
        raise RuntimeError(f"CommonShop 拥有资源 {owned} 小于总成本 {total}")
    if snapshot.get("CanBuy") is not True or snapshot.get("isEnough") is not True:
        raise RuntimeError(
            "CommonShop 购买资格未闭环："
            f"CanBuy={snapshot.get('CanBuy')!r}, isEnough={snapshot.get('isEnough')!r}"
        )
    return owned


def is_langyage_detail_text(text: str) -> bool:
    """Recognize the saved #470 detail layout when its image identity is stale.

    The 2026-08-11 failure frame contains all three independent business
    anchors while the formal scene matcher misses only the bounded
    ``参悟效果`` identity Shape.  Requiring the full trio keeps this fallback
    specific to the already-open exchange detail page.
    """

    normalized = str(text or "").replace(" ", "")
    return all(anchor in normalized for anchor in ("融合层数", "参悟效果", "兑换"))


def is_langyage_product_detail_text(text: str, expected_name: str) -> bool:
    """Recognize the current detail through Runtime identity plus GUI action.

    The current client may project the exchange dialog as generic scene #316
    and a floating notice can cover the old ``融合层数/参悟效果`` anchors.
    Runtime already selected the product; GUI only has to prove that the same
    product's exchange dialog (not the list's ``兑换所需`` row) is open.
    """

    def normalize_name(value: str) -> str:
        compact = re.sub(r"[\s·・:：._-]+", "", str(value or ""))
        return re.sub(r"^(?:悟|心法)", "", compact)

    normalized = re.sub(r"\s+", "", str(text or ""))
    normalized_name = normalize_name(normalized.replace("兑换", ""))
    target = normalize_name(expected_name)
    return bool(
        target
        and target in normalized_name
        and "兑换" in normalized
        and "兑换所需" not in normalized
    )


def exchange_row_action_x(list_box: Mapping[str, Any]) -> float:
    """Choose the row's action surface instead of its item/title tooltip area."""

    return float(list_box.get("x") or 0) + float(list_box.get("w") or 0) * 0.88


class XianshiExchangeTaskMixin:
    """Execute both exchanges with strict scene and OCR closed-loop checks."""

    def _record_xianshi_exchange_done(
        self,
        payload: dict[str, Any],
        *,
        default_task_id: str,
        now: datetime | None = None,
    ) -> str:
        next_time = next_business_time(("00:10",), now=now, weekdays=(TUESDAY,))
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or default_task_id),
            next_time,
        )
        return next_time

    def _open_xianshi_exchange_home(self, runtime: Any, home_scene: int, menu_shape: str, *, label: str):
        # goto_view and wait_view both use the framework's inner recognition
        # polling.  A transient unknown is never treated as permission to click.
        yield from runtime.goto_view(34)
        yield from runtime.wait_view(34, timeout=10.0, label=f"{label}：稳定确认世界 #34")
        yield from runtime.click_shape_center_then_view(
            34, "仙市", 247, timeout=15.0, label=f"{label}：进入仙市 #247"
        )
        yield from runtime.click_shape_center_then_view(
            247, menu_shape, home_scene, timeout=15.0, label=f"{label}：进入{menu_shape}"
        )

    def _select_exchange_candidate(self, runtime: Any, home_scene: int, detail_scene: int, row: Mapping[str, Any], *, label: str):
        book = dict(row["book"])
        category = str(book.get("filter_category") or "")
        if category not in _CATEGORY_TABS:
            raise RuntimeError(f"{label}：功法 {book.get('name')} 的分类 {category!r} 不可导航")
        runtime.click_shape_center(home_scene, category)
        yield from runtime.wait_action_settle(0.8)

        raw_name = str(row.get("name") or book.get("name") or "").strip()
        clean_name = re.sub(r"^(?:悟|心法)[·・]?", "", raw_name).strip()
        targets = tuple(dict.fromkeys(filter(None, (raw_name, raw_name.replace("·", ""), clean_name))))
        match = yield from runtime.wait_ocr_any_text(
            home_scene,
            targets,
            in_shapes=("商品列表",),
            timeout_seconds=25.0,
            poll_seconds=0.8,
            max_scrolls_per_direction=12,
            direction_cycles=2,
            cycle_pause_seconds=0.8,
            search_direction="down",
            match_mode="exact",
        )
        if match is None:
            raise TimeoutError(f"{label}：商品列表未找到 {raw_name}")
        x, y = match.point(anchor="center")
        if detail_scene == LANGYAGE_DETAIL_SCENE:
            list_shape = runtime.view(home_scene).get_shape("商品列表")
            if list_shape is None:
                raise RuntimeError(f"{label}：缺少 #{home_scene}「商品列表」Shape")
            # The title/icon area opens generic item information (#316).  The
            # right side of the same formal row is the exchange action surface.
            # OCR selects only the row y; the asset container supplies x.
            x = exchange_row_action_x(list_shape.box())
        runtime.click_frame_point(home_scene, x, y)
        yield from runtime.wait_action_settle(1.0)
        if detail_scene == LANGYAGE_DETAIL_SCENE:
            predicate = lambda text: (
                    is_langyage_detail_text(text)
                    or is_langyage_product_detail_text(text, raw_name)
                )
            matched = yield from runtime.wait_any(
                {
                    "legacy_detail": runtime.view_visible(detail_scene),
                    "common_shop_detail": runtime.view_visible(COMMON_SHOP_DETAIL_SCENE),
                    "legacy_detail_text": runtime.ocr_matches(
                        predicate,
                        label=f"{label}：等待商品详情 #{detail_scene} OCR",
                    ),
                },
                timeout=15.0,
                label=f"{label}：等待商品详情 #{detail_scene}",
            )
            if matched == "common_shop_detail":
                return COMMON_SHOP_DETAIL_SCENE
            return detail_scene
        else:
            yield from runtime.wait_view(
                detail_scene,
                timeout=15.0,
                label=f"{label}：等待商品详情 #{detail_scene}",
            )
            return detail_scene

    def _buy_exchange_quantity(
        self,
        runtime: Any,
        *,
        home_scene: int,
        detail_scene: int,
        quantity: int,
        unit_price: int,
        label: str,
    ):
        # The red quantity glyph is not a reliable OCR source.  The active
        # CommonShop dialog exposes showNum directly; GUI clicks only move that
        # value, and every batch is followed by a fresh authoritative read.
        snapshot: dict[str, Any] = {}
        for _attempt in range(12):
            snapshot = read_common_shop_buy_dialog_snapshot()
            if snapshot.get("complete") is not True:
                raise RuntimeError(
                    f"{label}：CommonShop 购买框运行态不完整：{snapshot.get('reason') or snapshot!r}"
                )
            current = int(snapshot.get("showNum") or 0)
            if current == int(quantity):
                break
            delta = int(quantity) - current
            coarse, fine = divmod(abs(delta), 10)
            coarse_shape, fine_shape = (("+10", "+") if delta > 0 else ("-10", "-"))
            for _ in range(coarse):
                runtime.click_shape_center(detail_scene, coarse_shape)
                yield from runtime.wait_action_settle(0.5)
            for _ in range(fine):
                runtime.click_shape_center(detail_scene, fine_shape)
                yield from runtime.wait_action_settle(0.5)
        else:
            raise RuntimeError(f"{label}：数量配置未在动作上限内收敛到 {quantity}")

        snapshot = read_common_shop_buy_dialog_snapshot()
        owned = validate_common_shop_dialog(snapshot, quantity=quantity, unit_price=unit_price)
        expected_price = quantity * unit_price
        exchange_shape = "兑换（高风险）" if int(detail_scene) == COMMON_SHOP_DETAIL_SCENE else "兑换"

        yield from runtime.click_shape_center_then_view(
            detail_scene,
            exchange_shape,
            home_scene,
            settle_seconds=1.0,
            timeout=15.0,
            label=f"{label}：兑换后返回商品页",
        )
        return owned - expected_price

    def _return_xianshi_exchange_to_world(self, runtime: Any, home_scene: int, *, label: str):
        yield from runtime.wait_click_then_view(home_scene, "仙市", 247, timeout=15.0, label=f"{label}：返回仙市")
        yield from runtime.wait_click_then_view(247, "返回", 34, timeout=15.0, label=f"{label}：返回世界")
        yield from runtime.wait_view(34, timeout=10.0, label=f"{label}：稳定确认完成场景 #34")

    def _execute_xianshi_exchange_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None,
        *,
        mode: str,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        if not isinstance(ctx.get("asset_tree_path"), Path):
            raise RuntimeError("缺少周常/仙市资产树路径，无法执行兑换作业")
        if mode not in {"zhenwuge", "langyage"}:
            raise ValueError(f"未知仙市兑换模式：{mode}")

        label = "仙市_真悟阁" if mode == "zhenwuge" else "仙市_琅琊榜"
        home_scene = ZHENWUGE_SCENE if mode == "zhenwuge" else LANGYAGE_SCENE
        detail_scene = ZHENWUGE_DETAIL_SCENE if mode == "zhenwuge" else LANGYAGE_DETAIL_SCENE
        menu_shape = "真悟阁" if mode == "zhenwuge" else "琅琊阁"
        task_id = "xianshi-zhenwuge" if mode == "zhenwuge" else "xianshi-langya-rankings"
        runtime = self._fanxiu_runtime(ctx, ctx["asset_tree_path"], stop_event=stop_event)

        atlas = read_gongfa_atlas_runtime()
        shop = read_exchange_shop_runtime()
        if atlas.get("runtime_complete") is not True or shop.get("runtime_complete") is not True:
            raise RuntimeError(f"{label}：只读运行态不完整，拒绝兑换")
        items = list(shop.get("items") or [])
        books = list(atlas.get("books") or [])
        currency_ids = {
            int(item.get("cost_item_id") or 0)
            for item in items
            if int(item.get("cost_item_id") or 0) > 0
        }
        if mode == "langyage":
            item_ids = [int(item.get("item_id") or 0) for item in items if item.get("unlimited")]
            backpack_counts, backpack_debug = read_backpack_item_counts(
                [*item_ids, *currency_ids], manager_key="xianshi-langyage-books"
            )
            candidates = plan_langyage_candidates(books, items, backpack_counts)
        else:
            backpack_counts, backpack_debug = read_backpack_item_counts(
                currency_ids, manager_key="xianshi-zhenwuge-currencies"
            )
            candidates = plan_zhenwuge_candidates(books, items)

        yield from self._open_xianshi_exchange_home(runtime, home_scene, menu_shape, label=label)
        currency_remaining: dict[int, int] = {
            item_id: max(0, int(backpack_counts.get(item_id, 0)))
            for item_id in currency_ids
        }
        purchases: list[dict[str, Any]] = []
        for row in candidates:
            cost_item_id = int(row["cost_item_id"])
            unit_price = int(row["cost_num"])
            if currency_remaining.get(cost_item_id, 0) < unit_price:
                continue
            active_detail_scene = yield from self._select_exchange_candidate(
                runtime, home_scene, detail_scene, row, label=label
            )
            dialog = read_common_shop_buy_dialog_snapshot()
            if dialog.get("complete") is not True:
                raise RuntimeError(
                    f"{label}：CommonShop 购买框运行态不完整：{dialog.get('reason') or dialog!r}"
                )
            if int(dialog.get("Price") or 0) != unit_price:
                raise RuntimeError(
                    f"{label}：运行态单价与商品计划不一致，"
                    f"计划 {unit_price}，实际 {dialog.get('Price')!r}"
                )
            owned = int(dialog.get("HadPrice") or 0)
            currency_remaining[cost_item_id] = owned
            quantity = min(int(row["desired"]), owned // unit_price)
            if quantity <= 0:
                raise RuntimeError(f"{label}：进入详情后资源不足，前置背包读数与弹窗不一致")
            remaining = yield from self._buy_exchange_quantity(
                runtime,
                home_scene=home_scene,
                detail_scene=active_detail_scene,
                quantity=quantity,
                unit_price=unit_price,
                label=f"{label}/{row.get('name')}",
            )
            currency_remaining[cost_item_id] = remaining
            purchases.append({
                "item_id": int(row["item_id"]),
                "name": str(row["name"]),
                "quantity": quantity,
                "unit_price": unit_price,
                "cost_item_id": cost_item_id,
            })

        yield from self._return_xianshi_exchange_to_world(runtime, home_scene, label=label)
        next_time = self._record_xianshi_exchange_done(payload, default_task_id=task_id)
        self._log("success", f"{label}：兑换 {len(purchases)} 种并返回 #34，下次 {next_time}")
        return {
            "result": "success",
            "message": f"兑换 {len(purchases)} 种并返回世界",
            "current_scene": 34,
            "purchases": purchases,
            "currency_remaining": currency_remaining,
            "backpack_debug": backpack_debug,
        }

    def _execute_xianshi_zhenwuge_task(self, ctx: dict[str, Any], stop_event: Any, payload: dict[str, Any] | None = None):
        return (yield from self._execute_xianshi_exchange_task(ctx, stop_event, payload, mode="zhenwuge"))

    def _execute_xianshi_langya_rankings_task(self, ctx: dict[str, Any], stop_event: Any, payload: dict[str, Any] | None = None):
        return (yield from self._execute_xianshi_exchange_task(ctx, stop_event, payload, mode="langyage"))
