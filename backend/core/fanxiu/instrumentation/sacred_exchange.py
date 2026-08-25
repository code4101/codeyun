from __future__ import annotations

"""Strictly read the active Sacred Garden exchange scroll view."""

import hashlib
import json
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    as_int,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    active_ui_component_objects,
    read_ui_object_field,
    read_ui_runtime_snapshot,
)


_REQUIRED_KEYS = frozenset({
    "SacredGardenExchangeItem",
    "itemScrollview",
    "ItemInfoList",
    "itemInfo",
    "baseId",
    "num",
    "id",
    "state",
    "startTime",
})


def _nonnegative_int(value: Any, *, label: str) -> int:
    parsed = as_int(value)
    if parsed is None or parsed < 0:
        raise FanxiuRuntimeMemoryError(f"神物兑换 {label} 无效")
    return int(parsed)


def _read_snapshot(context: UiRuntimeContext) -> dict[str, Any]:
    candidates = []
    for component in active_ui_component_objects(context):
        fields = context.reader.fields(component)
        if "SacredGardenExchangeItem" in fields:
            candidates.append(component)
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            f"NotLoaded: active SacredGardenExchangeItem 数量为 {len(candidates)}"
        )
    panel = candidates[0]
    scroll = table_ref(read_ui_object_field(context, panel.address, "itemScrollview"))
    if scroll is None:
        raise FanxiuRuntimeMemoryError("神物兑换 itemScrollview 尚未加载")
    item_list = table_ref(read_ui_object_field(context, scroll.address, "ItemInfoList"))
    if item_list is None:
        raise FanxiuRuntimeMemoryError("神物兑换 ItemInfoList 尚未加载")
    indexed, declared_count = context.reader.indexed_list_items(item_list)
    if declared_count is None or declared_count != len(indexed) or not 1 <= len(indexed) <= 64:
        raise FanxiuRuntimeMemoryError("神物兑换 ItemInfoList 不完整或越界")

    items: list[dict[str, Any]] = []
    seen_base_ids: set[int] = set()
    for expected_index, (lua_index, raw_row) in enumerate(indexed):
        row = table_ref(raw_row)
        if row is None:
            raise FanxiuRuntimeMemoryError("神物兑换 ItemInfoList 含非对象行")
        info = table_ref(read_ui_object_field(context, row.address, "itemInfo"))
        if info is None:
            raise FanxiuRuntimeMemoryError("神物兑换行缺少 itemInfo")
        base_id = _nonnegative_int(
            read_ui_object_field(context, info.address, "baseId"), label="baseId"
        )
        if base_id in seen_base_ids:
            raise FanxiuRuntimeMemoryError("神物兑换 ItemInfoList 含重复 baseId")
        seen_base_ids.add(base_id)
        items.append({
            "ui_index": expected_index,
            "lua_index": int(lua_index),
            "exchange_id": _nonnegative_int(
                read_ui_object_field(context, row.address, "id"), label="exchange id"
            ),
            "base_id": base_id,
            "num": _nonnegative_int(
                read_ui_object_field(context, info.address, "num"), label="num"
            ),
            "state": _nonnegative_int(
                read_ui_object_field(context, row.address, "state"), label="state"
            ),
            "start_time": _nonnegative_int(
                read_ui_object_field(context, row.address, "startTime"), label="startTime"
            ),
        })

    fingerprint = hashlib.sha256(
        json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "ok": True,
        "complete": True,
        "source": "active_sacred_garden_exchange_panel.item_scroll_view",
        "pid": context.binding.pid,
        "process_start_ticks": context.binding.process_start_ticks,
        "panel_address": f"0x{panel.address:x}",
        "scroll_address": f"0x{scroll.address:x}",
        "declared_count": declared_count,
        "items": items,
        "fingerprint": fingerprint,
        "read_only": True,
    }


def read_sacred_exchange_snapshot() -> dict[str, Any]:
    """Return one process-bound, complete ordered exchange projection."""

    try:
        return read_ui_runtime_snapshot(_REQUIRED_KEYS, _read_snapshot, fast=True)
    except (FanxiuRuntimeMemoryError, KeyError, AttributeError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "complete": False,
            "source": "active_sacred_garden_exchange_panel.item_scroll_view",
            "reason": str(exc),
            "items": [],
            "read_only": True,
        }


__all__ = ["read_sacred_exchange_snapshot"]
