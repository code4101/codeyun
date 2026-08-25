from __future__ import annotations

"""Strictly read the already-loaded ordinary backpack panel projection."""

import hashlib
import json
import threading
import time
from typing import Any, Mapping

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    as_int,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    acquire_ui_runtime_context,
)


_BACKPACK_KEYS = frozenset(
    {
        "m_panel",
        "tabPanelGroup",
        "curTabIndex",
        "panelShowComps",
        "isShow",
        "tablo",
        "tabNum",
        "ItemListScroll",
        "ItemInfoList",
        "ItemClassDic",
        "_dt_",
        "itemvo",
        "V_Data",
        "root",
    }
)
_BACKPACK_CACHE_LOCK = threading.RLock()
_backpack_view_cache: tuple[int, int, int, int, int] | None = None


def _integer(reader: LuaJitReader, value: Any) -> int | None:
    return reader.long(value) if isinstance(value, LuaRef) else as_int(value)


def _json_value(reader: LuaJitReader, value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    integer = _integer(reader, value)
    if integer is not None:
        return integer
    ref = table_ref(value)
    if ref is None:
        return None
    fields = reader.fields(ref)
    result: dict[str, Any] = {}
    for key, raw in fields.items():
        if not isinstance(key, (str, int)) or len(result) >= 32:
            continue
        if raw is None or isinstance(raw, (str, int, float, bool)):
            result[str(key)] = raw
        else:
            nested_integer = _integer(reader, raw)
            if nested_integer is not None:
                result[str(key)] = nested_integer
    return result


def _decode_item(reader: LuaJitReader, value: Any, ui_index: int) -> dict[str, Any]:
    if value is False:
        return {
            "ui_index": ui_index,
            "is_padding": True,
            "instance_id": None,
            "base_id": None,
            "num": None,
            "end_time": None,
            "ext": None,
        }
    if value is None:
        raise FanxiuRuntimeMemoryError(
            f"储物袋 UI 第 {ui_index} 项为缺失 nil 键，不能冒充 padding"
        )
    if table_ref(value) is None:
        raise FanxiuRuntimeMemoryError(
            f"储物袋 UI 第 {ui_index} 项为未知标量，不能冒充 padding"
        )
    fields = reader.fields(value)
    instance_id = _integer(reader, fields.get("id"))
    base_id = as_int(fields.get("baseId"))
    num = as_int(fields.get("num"))
    if instance_id is None or base_id is None or num is None:
        raise FanxiuRuntimeMemoryError(
            f"储物袋 UI 第 {ui_index} 项缺少 id/baseId/num"
        )
    return {
        "ui_index": ui_index,
        "is_padding": False,
        "instance_id": str(instance_id),
        "base_id": base_id,
        "num": num,
        "end_time": _integer(reader, fields.get("endTime")),
        "ext": _json_value(reader, fields.get("ext")),
    }


def _backpack_ui_list_items(
    reader: LuaJitReader,
    value: Any,
) -> tuple[list[tuple[int, Any]], int | None, tuple[int, ...]]:
    """Read ``ItemInfoList`` without renumbering its sparse tail.

    This particular UI list reports its allocated CList capacity in ``count``.
    The game can leave a contiguous unmaterialized suffix after the last item.
    That suffix is not an item and must neither make the panel unavailable nor
    be compressed into a different UI order.  A hole *before* a later value is
    still a structural contradiction and fails closed.
    """

    wrapper = reader.fields(value)
    storage = table_ref(wrapper.get("_dt_"))
    count = as_int(wrapper.get("count"))
    if storage is None:
        return [], None, ()
    if count is not None and count < 0:
        raise FanxiuRuntimeMemoryError("ItemInfoList CList count 不能为负数")
    table = reader.table(storage.address)
    indexed: list[tuple[int, Any]] = []
    missing: list[int] = []
    limit = count if count is not None else max(len(table["array"]) - 1, 0)
    for key in range(1, limit + 1):
        array_value = table["array"][key] if key < len(table["array"]) else None
        hash_value = table["fields"].get(key)
        if array_value is not None and hash_value is not None and array_value != hash_value:
            raise FanxiuRuntimeMemoryError(
                f"ItemInfoList 数字键 {key} 在 array/hash 区内容冲突"
            )
        item = array_value if array_value is not None else hash_value
        if item is None:
            missing.append(key)
        else:
            indexed.append((key, item))
    if missing and any(key > missing[0] for key, _item in indexed):
        raise FanxiuRuntimeMemoryError(
            f"ItemInfoList 出现非尾部缺失槽位：{missing[0]}"
        )
    return indexed, count, tuple(missing)


def _decode_panel(
    reader: LuaJitReader,
    panel: LuaRef,
    *,
    field,
    include_materialized: bool = True,
) -> dict[str, Any]:
    if field(panel.address, "isShow") is not True:
        raise FanxiuRuntimeMemoryError("BackPackPanel 不是 isShow=true 的活动面板")
    tablo = table_ref(field(panel.address, "tablo"))
    scroll = table_ref(field(panel.address, "ItemListScroll"))
    if tablo is None or scroll is None:
        raise FanxiuRuntimeMemoryError("BackPackPanel tablo/ItemListScroll 尚未加载")
    tab_fields = reader.fields(tablo)
    tab_param = _integer(reader, tab_fields.get("param"))
    tab_num = _integer(reader, field(panel.address, "tabNum"))
    tab_id = _json_value(reader, tab_fields.get("id"))
    tab_label = str(tab_fields.get("name") or tab_fields.get("label") or "")
    # ``tablo.param`` is the zero-based category parameter (全部=0), while
    # ``tabNum`` is the one-based visible tab ordinal (全部=1).  They describe
    # different coordinate systems and must not be forced equal.
    if (tab_param is not None and tab_param < 0) or tab_num is None or tab_num < 1:
        raise FanxiuRuntimeMemoryError(
            "BackPackPanel 当前 tab 参数不完整或非法"
            f"（param={tab_param!r}, tabNum={tab_num!r}）"
        )
    item_info_list = table_ref(field(scroll.address, "ItemInfoList"))
    if item_info_list is None:
        raise FanxiuRuntimeMemoryError("LuaUIScrollView.ItemInfoList 尚未加载")
    indexed_values, count, trailing_missing_indices = _backpack_ui_list_items(reader, item_info_list)
    if count is None:
        raise FanxiuRuntimeMemoryError("ItemInfoList CList count 尚未加载")
    items = [
        _decode_item(reader, value, source_index - 1)
        for source_index, value in indexed_values
    ]
    instance_ids = [item["instance_id"] for item in items if not item["is_padding"]]
    if len(instance_ids) != len(set(instance_ids)):
        raise FanxiuRuntimeMemoryError("ItemInfoList 含重复实例 id")

    bindings: list[dict[str, Any]] = []
    item_class_dic = table_ref(field(scroll.address, "ItemClassDic")) if include_materialized else None
    storage = table_ref(field(item_class_dic.address, "_dt_")) if item_class_dic else None
    materialized_read_error: str | None = None
    if storage:
        try:
            table = reader.table(storage.address)
            seen: set[int] = set()
            for raw in [*table["array"], *table["fields"].values()]:
                item_instance = table_ref(raw)
                if item_instance is None or item_instance.address in seen:
                    continue
                seen.add(item_instance.address)
                instance_fields = reader.string_fields(
                    item_instance.address, frozenset({"itemvo", "V_Data", "root"})
                )
                item_vo = instance_fields.get("itemvo") or instance_fields.get("V_Data")
                item_fields = reader.fields(item_vo)
                instance_id = _integer(reader, item_fields.get("id"))
                root = table_ref(instance_fields.get("root"))
                bindings.append({
                    "instance_id": str(instance_id) if instance_id is not None else None,
                    "root_address": f"0x{root.address:x}" if root else None,
                    "item_instance_address": f"0x{item_instance.address:x}",
                })
        except FanxiuRuntimeMemoryError as exc:
            # ItemClassDic is an optional materialized Unity projection.  Its
            # lifecycle is shorter than ItemInfoList and it is not needed for
            # ordered Runtime-GUI registration, so do not discard the latter.
            bindings = []
            materialized_read_error = str(exc)
    valid_bindings = [
        binding
        for binding in bindings
        if binding["instance_id"] is not None and binding["root_address"] is not None
    ]
    binding_ids = [binding["instance_id"] for binding in valid_bindings]
    materialized_available = item_class_dic is not None and storage is not None
    materialized_complete = materialized_available and (
        len(valid_bindings) == len(bindings)
        and len(binding_ids) == len(set(binding_ids))
        and (not instance_ids or bool(binding_ids))
        and set(binding_ids).issubset(set(instance_ids))
    )
    return {
        "tab": {
            "id": tab_id,
            "param": tab_param,
            "number": tab_num,
            "label": tab_label,
        },
        "items": items,
        "item_count": len(instance_ids),
        "slot_count": len(items),
        "declared_slot_count": count,
        "trailing_missing_indices": list(trailing_missing_indices),
        "padding_count": sum(item["is_padding"] for item in items),
        "materialized_bindings": valid_bindings if materialized_complete else [],
        "materialized_available": materialized_available,
        "materialized_complete": materialized_complete,
        "materialized_state": (
            "complete"
            if materialized_complete
            else "incomplete"
            if materialized_available
            else "not_loaded"
        ),
        "materialized_reason": materialized_read_error,
        "panel_address": f"0x{panel.address:x}",
        "scroll_address": f"0x{scroll.address:x}",
    }


def _select_unique_panel(candidates: dict[int, dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        raise FanxiuRuntimeMemoryError("NotLoaded: 当前没有完整的 active BackPackPanel")
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            f"Incomplete: 同时发现 {len(candidates)} 个 active BackPackPanel"
        )
    return next(iter(candidates.values()))


def _snapshot(context: UiRuntimeContext) -> dict[str, Any]:
    global _backpack_view_cache
    reader = context.reader
    field = context.field
    memory = context.memory
    started = time.perf_counter()
    table = reader.table(context.binding.component_storage_address)
    candidates: dict[int, dict[str, Any]] = {}
    decode_errors: list[str] = []
    decode_seconds = 0.0
    raw_windows = [*table["array"], *table["fields"].values()]

    def decode_window(raw_window: Any) -> None:
        global _backpack_view_cache
        nonlocal decode_seconds
        window = table_ref(raw_window)
        if window is None:
            return
        windows, count = reader.list_items(window)
        if count is None or count <= 0 or len(windows) != count:
            return
        component = table_ref(windows[-1])
        view = table_ref(field(component.address, "m_panel")) if component else None
        group = table_ref(field(view.address, "tabPanelGroup")) if view else None
        current_index = as_int(field(group.address, "curTabIndex")) if group else None
        panels = table_ref(field(group.address, "panelShowComps")) if group else None
        panel_storage = table_ref(field(panels.address, "_dt_")) if panels else None
        if current_index is None or panel_storage is None:
            return
        panel_table = reader.table(panel_storage.address)
        slot = current_index + 1
        raw_panel_component = (
            panel_table["array"][slot]
            if slot < len(panel_table["array"])
            else panel_table["fields"].get(slot)
        )
        panel_component = table_ref(raw_panel_component)
        panel = table_ref(field(panel_component.address, "m_panel")) if panel_component else None
        if panel is None:
            return
        try:
            decode_started = time.perf_counter()
            decoded = _decode_panel(
                reader,
                panel,
                field=field,
                include_materialized=False,
            )
            decode_seconds += time.perf_counter() - decode_started
        except FanxiuRuntimeMemoryError as exc:
            if len(decode_errors) < 3:
                decode_errors.append(str(exc))
            return
        decoded["window_address"] = f"0x{window.address:x}"
        decoded["window_component_address"] = (
            f"0x{component.address:x}" if component else None
        )
        candidates[panel.address] = decoded
        with _BACKPACK_CACHE_LOCK:
            _backpack_view_cache = (
                memory.pid,
                memory.process_start_ticks,
                window.address,
                component.address,
                view.address,
            )

    with _BACKPACK_CACHE_LOCK:
        cached_view = _backpack_view_cache
    used_cached_view = False
    if cached_view is not None and cached_view[:2] == (
        memory.pid,
        memory.process_start_ticks,
    ):
        raw_cached = next(
            (
                raw
                for raw in raw_windows
                if (ref := table_ref(raw)) is not None
                and ref.address == cached_view[2]
            ),
            None,
        )
        if raw_cached is not None:
            decode_window(raw_cached)
            used_cached_view = bool(candidates)
        if not used_cached_view:
            with _BACKPACK_CACHE_LOCK:
                _backpack_view_cache = None
    if not used_cached_view:
        for raw_window in raw_windows:
            decode_window(raw_window)
    context.timings["window_scan"] = time.perf_counter() - started - decode_seconds
    context.timings["panel_fields_and_clist_decode"] = decode_seconds
    context.timings["view_cache_hit"] = float(used_cached_view)
    try:
        decoded = _select_unique_panel(candidates)
    except FanxiuRuntimeMemoryError as exc:
        if decode_errors:
            raise FanxiuRuntimeMemoryError(f"{exc}；候选面板解码失败：{' | '.join(decode_errors)}") from exc
        raise
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        **decoded,
        "performance": {
            "cache_mode": context.cache_mode,
            "stages_seconds": dict(context.timings),
        },
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "read_only": True,
        },
    }


def backpack_ui_snapshot_fingerprint(snapshot: Mapping[str, Any]) -> str:
    """Hash the ordered semantic Runtime projection, excluding timings and OCR evidence."""

    if snapshot.get("complete") is not True:
        return ""
    evidence = snapshot.get("evidence") if isinstance(snapshot.get("evidence"), Mapping) else {}
    payload = {
        "source": snapshot.get("source"),
        "tab": snapshot.get("tab"),
        "panel_address": snapshot.get("panel_address"),
        "scroll_address": snapshot.get("scroll_address"),
        "window_address": snapshot.get("window_address"),
        "pid": evidence.get("pid"),
        "process_start_ticks": evidence.get("process_start_ticks"),
        "items": [
            {
                "ui_index": item.get("ui_index"),
                "slot_index": item.get("slot_index"),
                "instance_id": item.get("instance_id"),
                "base_id": item.get("base_id"),
                "num": item.get("num"),
                "need_num": item.get("need_num"),
                "is_padding": bool(item.get("is_padding")),
            }
            for item in snapshot.get("items") or []
            if isinstance(item, Mapping)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read_backpack_ui_snapshot() -> dict[str, Any]:
    started = time.perf_counter()
    context: UiRuntimeContext | None = None
    try:
        context = acquire_ui_runtime_context(_BACKPACK_KEYS)
        result = _snapshot(context)
        result["fingerprint"] = backpack_ui_snapshot_fingerprint(result)
        serialization_started = time.perf_counter()
        json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        context.timings["serialization"] = time.perf_counter() - serialization_started
        result["performance"]["stages_seconds"] = dict(context.timings)
        result["elapsed_seconds"] = time.perf_counter() - started
        return result
    except Exception as exc:
        reason = str(exc) if isinstance(exc, FanxiuRuntimeMemoryError) else f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "active_backpack_panel_item_info_list",
            "state": "NotLoaded" if "NotLoaded" in reason else "Incomplete",
            "reason": reason,
            "items": [],
            "elapsed_seconds": time.perf_counter() - started,
            "evidence": {
                "pid": context.memory.pid if context else None,
                "process_start_ticks": (
                    context.memory.process_start_ticks if context else None
                ),
                "read_only": True,
            },
        }


def locate_backpack_ui_items(
    snapshot: dict[str, Any],
    *,
    instance_id: str | int | None = None,
    base_id: int | None = None,
) -> list[dict[str, Any]]:
    """Filter the authoritative UI order without aggregating or reordering it."""

    if snapshot.get("complete") is not True:
        raise FanxiuRuntimeMemoryError("储物袋 UI 快照未完整加载")
    if instance_id is None and base_id is None:
        raise ValueError("instance_id/base_id 至少提供一个")
    wanted_instance = str(instance_id) if instance_id is not None else None
    return [
        item
        for item in snapshot.get("items") or []
        if not item.get("is_padding")
        and (wanted_instance is None or item.get("instance_id") == wanted_instance)
        and (base_id is None or item.get("base_id") == int(base_id))
    ]


def clear_backpack_ui_view_cache() -> None:
    global _backpack_view_cache
    with _BACKPACK_CACHE_LOCK:
        _backpack_view_cache = None


__all__ = [
    "backpack_ui_snapshot_fingerprint",
    "clear_backpack_ui_view_cache",
    "locate_backpack_ui_items",
    "read_backpack_ui_snapshot",
]
