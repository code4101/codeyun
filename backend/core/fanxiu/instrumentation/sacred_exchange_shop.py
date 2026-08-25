from __future__ import annotations

"""Strictly read the active common shop opened from one divine item."""

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from backend.core.fanxiu.catalog.item import load_fanxiu_item_catalog
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
    as_int,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    active_ui_component_objects,
    read_ui_object_field,
    read_ui_runtime_snapshot,
)


_COST_RE = re.compile(r"^Item\|(\d+)_(\d+)$")
_REQUIRED_KEYS = frozenset({
    "CommonShopTab", "type", "itemScrollview", "ItemInfoList", "configData",
    "goodsId", "hasBuyTime", "_dt_", "count",
})


def decode_sacred_exchange_shop_config(
    values: Sequence[Any],
    *,
    cost_values: Sequence[Any],
) -> dict[str, int | bool | None]:
    """Decode the active type=3 CommonShop row used by divine exchange."""

    if len(values) <= 18:
        raise FanxiuRuntimeMemoryError("神物兑换商品配置行长度不足")
    cost_text = next((value for value in cost_values[1:] if isinstance(value, str)), None)
    match = _COST_RE.fullmatch(str(cost_text or ""))
    if match is None:
        raise FanxiuRuntimeMemoryError("神物兑换商品消耗不是 Item|id_num")

    def integer(index: int, label: str) -> int:
        value = as_int(values[index])
        if value is None:
            raise FanxiuRuntimeMemoryError(f"神物兑换商品 {label} 不是整数")
        return int(value)

    goods_id = integer(1, "goodsId")
    group_id = integer(2, "groupId")
    item_id = integer(6, "itemId")
    shop_type = integer(3, "type")
    goods_num = integer(7, "goodsNum")
    limit_times = integer(12, "limitTimes")
    position = integer(17, "position")
    cost_item_id, cost_num = int(match.group(1)), int(match.group(2))
    if shop_type != 3 or min(goods_id, group_id, item_id, goods_num, cost_item_id, cost_num) <= 0:
        raise FanxiuRuntimeMemoryError("神物兑换商品配置身份或数量无效")
    return {
        "goods_id": goods_id,
        "group_id": group_id,
        "item_id": item_id,
        "goods_num": goods_num,
        "cost_item_id": cost_item_id,
        "cost_num": cost_num,
        "position": position,
        "limit_times": limit_times,
        "unlimited": limit_times < 0,
    }


def _catalog_names() -> Mapping[int, str]:
    catalog = load_fanxiu_item_catalog(rebuild_missing=False)
    return {
        int(card.get("id") or 0): str(card.get("name") or "").strip()
        for card in catalog.get("cards") or []
        if int(card.get("id") or 0) > 0
    }


def _read_snapshot(context: UiRuntimeContext) -> dict[str, Any]:
    panels = [
        component
        for component in active_ui_component_objects(context)
        if "CommonShopTab" in context.reader.fields(component)
        and as_int(read_ui_object_field(context, component.address, "type")) == 3
    ]
    if len(panels) != 1:
        raise FanxiuRuntimeMemoryError(f"active type=3 CommonShopTab 数量为 {len(panels)}")
    panel = panels[0]
    scroll = table_ref(read_ui_object_field(context, panel.address, "itemScrollview"))
    item_list = table_ref(read_ui_object_field(context, scroll.address, "ItemInfoList")) if scroll else None
    if scroll is None or item_list is None:
        raise FanxiuRuntimeMemoryError("神物兑换商品列表尚未加载")
    groups, declared_count = context.reader.indexed_list_items(item_list)
    if declared_count is None or declared_count != len(groups) or not 1 <= len(groups) <= 64:
        raise FanxiuRuntimeMemoryError("神物兑换商品分组列表不完整")

    names = _catalog_names()
    rows: list[dict[str, Any]] = []
    for expected_group, (lua_group_index, raw_group) in enumerate(groups):
        group = table_ref(raw_group)
        if group is None:
            raise FanxiuRuntimeMemoryError("神物兑换商品分组不是 CList")
        entries, group_count = context.reader.indexed_list_items(group)
        if group_count is None or group_count != len(entries) or not entries:
            raise FanxiuRuntimeMemoryError("神物兑换商品分组内容不完整")
        decoded_entries: list[dict[str, Any]] = []
        for _lua_item_index, raw_item in entries:
            item = table_ref(raw_item)
            if item is None:
                raise FanxiuRuntimeMemoryError("神物兑换商品 VO 缺失")
            fields = context.reader.fields(item)
            config = table_ref(fields.get("configData"))
            if config is None:
                raise FanxiuRuntimeMemoryError("神物兑换商品 configData 缺失")
            values = context.reader.table(config.address)["array"]
            cost = table_ref(values[8]) if len(values) > 8 else None
            if cost is None:
                # Fashion rows have no item cost and are not exchange targets.
                continue
            decoded = decode_sacred_exchange_shop_config(
                values,
                cost_values=context.reader.table(cost.address)["array"],
            )
            if decoded["goods_id"] != as_int(fields.get("goodsId")):
                raise FanxiuRuntimeMemoryError("神物兑换商品 VO/config goodsId 不一致")
            decoded_entries.append({
                **decoded,
                "name": names.get(int(decoded["item_id"])) or f"物品 {decoded['item_id']}",
                "bought": max(0, as_int(fields.get("hasBuyTime")) or 0),
            })
        if decoded_entries:
            rows.append({
                "ui_index": expected_group,
                "lua_group_index": int(lua_group_index),
                "entries": decoded_entries,
            })
    if not rows:
        raise FanxiuRuntimeMemoryError("神物兑换当前页没有可读商品")
    fingerprint = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "ok": True,
        "complete": True,
        "source": "active_type3_common_shop.item_scroll_view",
        "pid": context.binding.pid,
        "process_start_ticks": context.binding.process_start_ticks,
        "panel_address": f"0x{panel.address:x}",
        "scroll_address": f"0x{scroll.address:x}",
        "declared_group_count": declared_count,
        "rows": rows,
        "fingerprint": fingerprint,
        "read_only": True,
    }


def read_sacred_exchange_shop_snapshot() -> dict[str, Any]:
    try:
        return read_ui_runtime_snapshot(_REQUIRED_KEYS, _read_snapshot, fast=True)
    except (FanxiuRuntimeMemoryError, KeyError, AttributeError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "complete": False,
            "source": "active_type3_common_shop.item_scroll_view",
            "reason": str(exc),
            "rows": [],
            "read_only": True,
        }


__all__ = ["decode_sacred_exchange_shop_config", "read_sacred_exchange_shop_snapshot"]
