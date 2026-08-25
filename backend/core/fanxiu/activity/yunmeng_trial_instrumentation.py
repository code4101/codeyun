from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
import re
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.instrumentation.activity_shop import (
    collect_activity_shop_runtime,
)


_EQUAL_CROSS_GROUP_RE = re.compile(r"(?:^|[,;])EqualCrossGroup\|(\d+)_(\d+)(?=$|[,;])")


def infer_yunmeng_cross_count(
    conditions: Iterable[object],
    *,
    shop_base_id: int = 210001,
) -> int | None:
    """Read the cross-server count from runtime ``EqualCrossGroup`` conditions."""

    values = {
        int(match.group(2))
        for condition in conditions
        for match in _EQUAL_CROSS_GROUP_RE.finditer(str(condition or ""))
        if int(match.group(1)) == int(shop_base_id)
    }
    if len(values) > 1:
        raise ValueError(f"云梦试剑跨服数条件不一致：{sorted(values)}")
    return next(iter(values), None)


def normalize_yunmeng_shop_runtime_rows(
    rows: Iterable[Sequence[Any]],
    *,
    active_shop_item_ids: Sequence[int],
    display_order: Sequence[int],
    item_names: Mapping[int, str],
    shop_base_id: int = 210001,
) -> list[dict[str, Any]]:
    """Convert Lua ``V_ShopCfg`` arrays into complete DB snapshot rows.

    ``row[1]`` is the config-row id while ``row[2]`` is the shop item id.
    Several config rows may share one shop item id; the game merges their
    purchase limits into one visible product row.
    """

    active_ids = [int(value) for value in active_shop_item_ids]
    order_ids = [int(value) for value in display_order]
    if len(active_ids) != len(set(active_ids)):
        raise ValueError("云梦试剑活动商品 ID 存在重复")
    if set(order_ids) != set(active_ids) or len(order_ids) != len(active_ids):
        raise ValueError("云梦试剑显示顺序与活动商品集合不一致")

    runtime_rows = list(rows)
    cross_count = infer_yunmeng_cross_count(
        (row[12] for row in runtime_rows if len(row) > 12),
        shop_base_id=shop_base_id,
    )
    if cross_count is None:
        raise ValueError("云梦试剑配置中缺少跨服数条件")

    candidates: dict[int, list[Sequence[Any]]] = {
        goods_id: [] for goods_id in active_ids
    }
    for row in runtime_rows:
        if len(row) < 15 or row[2] is None or row[3] != shop_base_id:
            continue
        goods_id = int(row[2])
        if goods_id in candidates:
            signature = tuple(row[:15])
            if not any(tuple(existing[:15]) == signature for existing in candidates[goods_id]):
                candidates[goods_id].append(row)

    missing = [goods_id for goods_id in active_ids if not candidates[goods_id]]
    if missing:
        raise ValueError(f"云梦试剑兑换宝阁动态快照缺少商品：{missing[0]}")

    normalized_by_id: dict[int, dict[str, Any]] = {}
    for goods_id, config_rows in candidates.items():
        product_signatures = {
            (int(row[4]), int(row[5] or 1), int(row[7] or 0), int(row[8] or 0))
            for row in config_rows
        }
        if len(product_signatures) != 1:
            raise ValueError(f"云梦试剑商品 {goods_id} 的多配置内容不一致")
        primary = min(config_rows, key=lambda row: (int(row[14] or 0), int(row[1])))
        limits = [int(row[10] if row[10] is not None else -1) for row in config_rows]
        purchase_limit = -1 if any(limit < 0 for limit in limits) else sum(limits)
        item_id = int(primary[4])
        normalized_by_id[goods_id] = {
            "goods_id": goods_id,
            "item_id": item_id,
            "name": str(item_names.get(item_id) or item_id),
            "goods_num": int(primary[5] or 1),
            "token_cost": int(primary[7] or 0),
            "purchase_limit": purchase_limit,
            "discount": (
                int(primary[18])
                if len(primary) > 18 and isinstance(primary[18], (int, float))
                else None
            ),
            "original_price": (
                int(primary[19])
                if len(primary) > 19 and isinstance(primary[19], (int, float))
                else None
            ),
            "show_limit": str(primary[12] or ""),
            "disappear_limit": str(primary[13] or ""),
            "raw_data": {
                "config_ids": [int(row[1]) for row in config_rows],
                "aggregated_config_count": len(config_rows),
                "shop_item_id": goods_id,
                "cross_count": cross_count,
            },
        }

    return [
        {**normalized_by_id[goods_id], "source_order": source_order}
        for source_order, goods_id in enumerate(order_ids, start=1)
    ]


def collect_yunmeng_trial_shop_snapshot() -> dict[str, Any]:
    """Collect a complete Yunmeng shop snapshot without activity-specific IDs."""

    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
    item_names = {
        int(item_id): str(card.get("name") or "")
        for item_id, card in cards.items()
        if str(item_id).isdigit() and isinstance(card, dict)
    }
    snapshot = collect_activity_shop_runtime(
        shop_base_id=210001,
        item_names=item_names,
        expected_currency_type=19,
    )
    if not snapshot.get("complete") or snapshot.get("cross_count") is None:
        raise ValueError("云梦试剑兑换宝阁运行态快照不完整")
    return snapshot


def collect_and_store_yunmeng_trial_shop(
    session: Session,
    *,
    start_date: str,
    end_date: str | None = None,
) -> str:
    """Agent entry: collect runtime data, validate it, then persist the DB fact."""

    from backend.core.fanxiu.activity.yunmeng_trial import (
        upsert_yunmeng_trial_snapshot,
    )

    snapshot = collect_yunmeng_trial_shop_snapshot()
    return upsert_yunmeng_trial_snapshot(
        session,
        {
            "cross_count": int(snapshot["cross_count"]),
            "start_date": str(start_date),
            "end_date": str(end_date or start_date),
            "game_shop_base_id": int(snapshot["shop_base_id"]),
            "currency_type": int(snapshot["currency_types"][0]),
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "source_kind": "dynamic_instrumentation",
            "shop_items": list(snapshot["items"]),
            "expected_shop_item_count": int(snapshot["active_shop_item_count"]),
            "evidence": dict(snapshot.get("evidence") or {}),
        },
    )
