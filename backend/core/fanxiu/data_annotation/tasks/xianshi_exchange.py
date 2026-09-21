from __future__ import annotations

"""Weekly Xianshi GongFa exchanges backed by saved #467-#470 assets."""

from collections import defaultdict
from datetime import datetime
from pathlib import Path
import re
import time
from typing import Any, Iterable, Mapping

from backend.core.fanxiu.data_annotation.job_times import next_business_time
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import (
    read_common_shop_buy_dialog_snapshot,
)
from backend.core.fanxiu.instrumentation.exchange_shop import read_exchange_shop_runtime
from backend.core.fanxiu.instrumentation.gongfa_atlas import read_gongfa_atlas_runtime
from backend.core.fanxiu.data_annotation.tasks.integer_count_control import (
    IntegerButtonAssets,
    set_verified_integer_button_count,
)


MONDAY = 0
ZHENWUGE_SCENE = 467
ZHENWUGE_DETAIL_SCENE = 468
LANGYAGE_SCENE = 469
LANGYAGE_DETAIL_SCENE = 470
COMMON_SHOP_DETAIL_SCENE = 634
LANGYAGE_FUSION_CAP = 100
_CATEGORY_TABS = frozenset({"剑修", "法修", "魔修", "体修"})


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


def _numbers(context: Any, scene_id: int, shape: str) -> tuple[list[int], str]:
    values, text = context.ocr_numbers_in_shapes(scene_id, (shape,), padding=12)
    return [int(value) for value in values], str(text or "")


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


def verify_xianshi_currency_balances(
    expected: Mapping[int, int],
    actual: Mapping[int, int],
    *,
    spent_currency_ids: Iterable[int],
) -> None:
    """Require the final Runtime wallet to match every currency we spent."""

    for item_id in sorted({int(value) for value in spent_currency_ids}):
        expected_value = int(expected.get(item_id, 0))
        actual_value = int(actual.get(item_id, 0))
        if actual_value != expected_value:
            raise RuntimeError(
                f"仙市兑换后资源未闭环：item_id={item_id}，"
                f"期望 {expected_value}，实际 {actual_value}"
            )


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
    def _read_common_shop_dialog(self, *, label: str, stage: str) -> dict[str, Any]:
        """Read one authoritative dialog snapshot and expose its real latency.

        The long-lived Kernel owns the process-local UI binding cache, so this
        call-site timing is the representative measurement.  A standalone
        Python probe would cold-discover memory and materially overstate the
        latency seen by the actual Job.
        """

        started = time.perf_counter()
        snapshot = read_common_shop_buy_dialog_snapshot()
        elapsed = time.perf_counter() - started
        logger = getattr(self, "_log", None)
        if callable(logger):
            logger(
                "detail",
                f"{label}：CommonShop Runtime {stage} 耗时 {elapsed:.3f}s，"
                f"complete={snapshot.get('complete') is True}",
            )
        return snapshot

    """Execute both exchanges with strict scene and OCR closed-loop checks."""

    def _record_xianshi_exchange_done(
        self,
        payload: dict[str, Any],
        *,
        default_task_id: str,
        now: datetime | None = None,
    ) -> str:
        next_time = next_business_time(("00:05",), now=now, weekdays=(MONDAY,))
        if bool(payload.get("schedule", True)):
            self._persist_scheduler_task_next_time(
                str(payload.get("__scheduler_task_id") or default_task_id),
                next_time,
            )
        # Under aggregation the parent Job owns the single schedule write; the
        # exchange still returns its business next_time without touching the
        # retired first-level id.
        return next_time

    def _open_xianshi_exchange_home(self, context: Any, home_scene: int, menu_shape: str, *, label: str):
        # go_scene and wait_scene both use the framework's inner recognition
        # polling.  A transient unknown is never treated as permission to click.
        yield from context.go_scene(34)
        yield from context.wait_scene([34], wait=10.0, label=f"{label}：稳定确认世界 #34")
        yield from context.click_shape_center_then_scene(
            34, "仙市", 247, timeout=15.0, label=f"{label}：进入仙市 #247"
        )
        yield from context.click_shape_center_then_scene(
            247, menu_shape, home_scene, timeout=15.0, label=f"{label}：进入{menu_shape}"
        )

    def _select_exchange_candidate(self, context: Any, home_scene: int, detail_scene: int, row: Mapping[str, Any], *, label: str):
        book = dict(row["book"])
        category = str(book.get("filter_category") or "")
        if category not in _CATEGORY_TABS:
            raise RuntimeError(f"{label}：功法 {book.get('name')} 的分类 {category!r} 不可导航")
        context.click_shape_center(home_scene, category)
        yield from context.wait_action_settle(0.8)

        raw_name = str(row.get("name") or book.get("name") or "").strip()
        clean_name = re.sub(r"^(?:悟|心法)[·・]?", "", raw_name).strip()
        targets = tuple(dict.fromkeys(filter(None, (raw_name, raw_name.replace("·", ""), clean_name))))
        match = yield from context.wait_ocr_any_text(
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
            list_shape = context.view(home_scene).get_shape("商品列表")
            if list_shape is None:
                raise RuntimeError(f"{label}：缺少 #{home_scene}「商品列表」Shape")
            # The title/icon area opens generic item information (#316).  The
            # right side of the same formal row is the exchange action surface.
            # OCR selects only the row y; the asset container supplies x.
            x = exchange_row_action_x(list_shape.box())
        context.click_frame_point(home_scene, x, y)
        yield from context.wait_action_settle(1.0)
        if detail_scene == LANGYAGE_DETAIL_SCENE:
            predicate = lambda text: (
                    is_langyage_detail_text(text)
                    or is_langyage_product_detail_text(text, raw_name)
                )
            matched = yield from context.wait_any(
                {
                    "legacy_detail": context.scene_visible(detail_scene),
                    "common_shop_detail": context.scene_visible(COMMON_SHOP_DETAIL_SCENE),
                    "legacy_detail_text": context.ocr_matches(
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
            yield from context.wait_scene(
                [detail_scene],
                wait=15.0,
                label=f"{label}：等待商品详情 #{detail_scene}",
            )
            return detail_scene

    def _buy_exchange_quantity(
        self,
        context: Any,
        *,
        home_scene: int,
        detail_scene: int,
        quantity: int,
        unit_price: int,
        label: str,
        initial_snapshot: Mapping[str, Any] | None = None,
    ):
        # The red quantity glyph is not a reliable OCR source.  The active
        # CommonShop dialog exposes showNum directly; GUI clicks only move that
        # value, and every batch is followed by a fresh authoritative read.
        # The caller has just read the active dialog to verify product price
        # and currency.  Reuse that same immutable observation for the first
        # quantity decision; no GUI action occurs between the two functions.
        # Every click batch and the final high-risk exchange still receive a
        # fresh Runtime read, so safety evidence is not weakened.
        snapshot: dict[str, Any] = dict(initial_snapshot or {})
        if not snapshot:
            snapshot = self._read_common_shop_dialog(label=label, stage="数量初始值")
        if snapshot.get("complete") is not True:
            raise RuntimeError(
                f"{label}：CommonShop 购买框运行态不完整：{snapshot.get('reason') or snapshot!r}"
            )

        def runtime_count() -> Mapping[str, int]:
            current_snapshot = self._read_common_shop_dialog(label=label, stage="数量稳定读回")
            if current_snapshot.get("complete") is not True:
                raise RuntimeError(
                    f"{label}：CommonShop 购买框运行态不完整："
                    f"{current_snapshot.get('reason') or current_snapshot!r}"
                )
            return {"current": int(current_snapshot.get("showNum") or 0)}

        assets = IntegerButtonAssets(
            settings_scene_id=int(detail_scene),
            count_region="数量",
            count_decrease="-",
            count_increase="+",
            count_decrease_large="-10",
            count_increase_large="+10",
            count_large_step=10,
        )
        adjustment = yield from set_verified_integer_button_count(
            context,
            assets,
            int(quantity),
            count_label=f"{label}购买数量",
            runtime_count_reader=runtime_count,
            initial_count=int(snapshot.get("showNum") or 0),
        )
        if int(adjustment.get("after") or 0) != int(quantity):
            raise RuntimeError(f"{label}：购买数量未精确回读为 {quantity}")

        snapshot = self._read_common_shop_dialog(label=label, stage="兑换前最终复核")
        owned = validate_common_shop_dialog(snapshot, quantity=quantity, unit_price=unit_price)
        expected_price = quantity * unit_price
        exchange_shape = "兑换（高风险）" if int(detail_scene) == COMMON_SHOP_DETAIL_SCENE else "兑换"

        yield from context.click_shape_center_then_scene(
            detail_scene,
            exchange_shape,
            home_scene,
            settle_seconds=1.0,
            timeout=15.0,
            label=f"{label}：兑换后返回商品页",
        )
        return owned - expected_price

    def _return_xianshi_exchange_to_world(self, context: Any, home_scene: int, *, label: str):
        yield from context.wait_click_then_scene(home_scene, "仙市", 247, timeout=15.0, label=f"{label}：返回仙市")
        yield from context.wait_click_then_scene(247, "返回", 34, timeout=15.0, label=f"{label}：返回世界")
        yield from context.wait_scene([34], wait=10.0, label=f"{label}：稳定确认完成场景 #34")

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
        context = self._behavior_tree_context(ctx, ctx["asset_tree_path"], stop_event=stop_event)

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

        yield from self._open_xianshi_exchange_home(context, home_scene, menu_shape, label=label)
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
                context, home_scene, detail_scene, row, label=label
            )
            dialog = self._read_common_shop_dialog(
                label=f"{label}/{row.get('name')}",
                stage="商品详情初读",
            )
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
                context,
                home_scene=home_scene,
                detail_scene=active_detail_scene,
                quantity=quantity,
                unit_price=unit_price,
                label=f"{label}/{row.get('name')}",
                initial_snapshot=dialog,
            )
            currency_remaining[cost_item_id] = remaining
            purchases.append({
                "item_id": int(row["item_id"]),
                "name": str(row["name"]),
                "quantity": quantity,
                "unit_price": unit_price,
                "cost_item_id": cost_item_id,
            })

        backpack_debug_after: dict[str, Any] | None = None
        if purchases:
            spent_currency_ids = {int(row["cost_item_id"]) for row in purchases}
            actual_remaining, backpack_debug_after = read_backpack_item_counts(
                spent_currency_ids,
                manager_key=f"{task_id}-currencies-after",
            )
            verify_xianshi_currency_balances(
                currency_remaining,
                actual_remaining,
                spent_currency_ids=spent_currency_ids,
            )

        yield from self._return_xianshi_exchange_to_world(context, home_scene, label=label)
        next_time = self._record_xianshi_exchange_done(payload, default_task_id=task_id)
        self._log("success", f"{label}：兑换 {len(purchases)} 种并返回 #34，下次 {next_time}")
        return {
            "result": "success",
            "message": f"兑换 {len(purchases)} 种并返回世界",
            "current_scene": 34,
            "purchases": purchases,
            "currency_remaining": currency_remaining,
            "backpack_debug": backpack_debug,
            "backpack_debug_after": backpack_debug_after,
        }

    def _execute_xianshi_zhenwuge_task(self, ctx: dict[str, Any], stop_event: Any, payload: dict[str, Any] | None = None):
        return (yield from self._execute_xianshi_exchange_task(ctx, stop_event, payload, mode="zhenwuge"))

    def _execute_xianshi_langya_rankings_task(self, ctx: dict[str, Any], stop_event: Any, payload: dict[str, Any] | None = None):
        return (yield from self._execute_xianshi_exchange_task(ctx, stop_event, payload, mode="langyage"))
