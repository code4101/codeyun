from __future__ import annotations

"""Read the active BackPackQuickView's four persisted option projections."""

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    acquire_ui_runtime_context,
)


QUICK_SETTING_FIELDS = {
    1: "isSelectedOpenBox",
    2: "isSelectedFenJie",
    3: "isSelectedMerge",
    4: "isSelectedUse",
}
_QUICK_KEYS = frozenset(
    {
        "m_panel",
        "configBtn",
        "ItemContent",
        "BackPackQuickItem",
        "isShow",
        *QUICK_SETTING_FIELDS.values(),
    }
)
_QUICK_CACHE_LOCK = threading.RLock()
_quick_view_cache: tuple[int, int, int, int, int] | None = None


def _decode_quick_view(panel_address: int, *, field) -> dict[int, int]:
    # Unlike BackPackPanel, BackPackQuickView never defines or inherits an
    # ``isShow`` state field.  Its authoritative open-state boundary is
    # UIShowMgr.V_M_compDic: P_CloseOneWin removes the component from its CList
    # and F_RemoveWinSn removes the dictionary key when the list becomes empty.
    for identity_field in ("configBtn", "ItemContent", "BackPackQuickItem"):
        if table_ref(field(panel_address, identity_field)) is None:
            raise FanxiuRuntimeMemoryError(
                f"BackPackQuickView 身份字段 {identity_field} 未加载"
            )
    values: dict[int, int] = {}
    for quick_type, name in QUICK_SETTING_FIELDS.items():
        value = field(panel_address, name)
        if not isinstance(value, bool):
            raise FanxiuRuntimeMemoryError(
                f"BackPackQuickView.{name} 不是明确 Lua boolean"
            )
        values[quick_type] = int(value)
    return values


def _select_unique_quick_view(
    candidates: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    if not candidates:
        raise FanxiuRuntimeMemoryError("NotLoaded: active BackPackQuickView 未加载")
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            f"Ambiguous: 同时发现 {len(candidates)} 个 BackPackQuickView"
        )
    return next(iter(candidates.values()))


def _snapshot(context: UiRuntimeContext) -> dict[str, Any]:
    global _quick_view_cache
    reader = context.reader
    field = context.field
    memory = context.memory
    started = time.perf_counter()
    table = reader.table(context.binding.component_storage_address)
    candidates: dict[int, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    panel_field_seconds = 0.0
    raw_windows = [*table["array"], *table["fields"].values()]

    def decode_window(raw_window: Any, *, diagnostics_enabled: bool) -> None:
        global _quick_view_cache
        nonlocal panel_field_seconds
        window = table_ref(raw_window)
        if window is None:
            return
        windows, count = reader.list_items(window)
        if count is None or count <= 0 or len(windows) != count:
            return
        component = table_ref(windows[-1])
        panel = table_ref(field(component.address, "m_panel")) if component else None
        if panel is None:
            return
        panel_started = time.perf_counter()
        raw_fields = {
            name: field(panel.address, name)
            for name in (
                "isShow",
                "configBtn",
                "ItemContent",
                "BackPackQuickItem",
                *QUICK_SETTING_FIELDS.values(),
            )
        }

        def summarize(value: Any) -> Any:
            ref = table_ref(value)
            if ref:
                return {"type": ref.kind, "address": f"0x{ref.address:x}"}
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            return type(value).__name__

        if diagnostics_enabled:
            diagnostics.append({
                "window_address": f"0x{window.address:x}",
                "panel_address": f"0x{panel.address:x}",
                "fields": {name: summarize(value) for name, value in raw_fields.items()},
            })
        try:
            values = _decode_quick_view(panel.address, field=field)
        except FanxiuRuntimeMemoryError:
            panel_field_seconds += time.perf_counter() - panel_started
            return
        panel_field_seconds += time.perf_counter() - panel_started
        candidates[panel.address] = {
            "values": {str(key): value for key, value in values.items()},
            "window_address": f"0x{window.address:x}",
            "window_component_address": f"0x{component.address:x}",
            "panel_address": f"0x{panel.address:x}",
            "active_membership": "UIShowMgr.V_M_compDic",
        }
        with _QUICK_CACHE_LOCK:
            _quick_view_cache = (
                memory.pid,
                memory.process_start_ticks,
                window.address,
                component.address,
                panel.address,
            )

    with _QUICK_CACHE_LOCK:
        cached_view = _quick_view_cache
    used_cached_view = False
    if cached_view is not None and cached_view[:2] == (
        memory.pid,
        memory.process_start_ticks,
    ):
        cached_window_address = cached_view[2]
        raw_cached = next(
            (
                raw
                for raw in raw_windows
                if (ref := table_ref(raw)) is not None
                and ref.address == cached_window_address
            ),
            None,
        )
        if raw_cached is not None:
            decode_window(raw_cached, diagnostics_enabled=False)
            used_cached_view = bool(candidates)
        if not used_cached_view:
            with _QUICK_CACHE_LOCK:
                _quick_view_cache = None
    if not used_cached_view:
        for raw_window in raw_windows:
            decode_window(raw_window, diagnostics_enabled=True)
    context.timings["window_scan"] = (
        time.perf_counter() - started - panel_field_seconds
    )
    context.timings["panel_field_reads"] = panel_field_seconds
    context.timings["view_cache_hit"] = float(used_cached_view)
    try:
        selected = _select_unique_quick_view(candidates)
    except FanxiuRuntimeMemoryError as exc:
        raise FanxiuRuntimeMemoryError(
            f"{exc}; candidate_diagnostics="
            f"{json.dumps(diagnostics, ensure_ascii=False, separators=(',', ':'))}"
        ) from exc
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "active_backpack_quick_view",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "captured_at_epoch": time.time(),
        **selected,
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


def read_backpack_quick_settings_snapshot() -> dict[str, Any]:
    started = time.perf_counter()
    context: UiRuntimeContext | None = None
    try:
        context = acquire_ui_runtime_context(_QUICK_KEYS)
        result = _snapshot(context)
        serialization_started = time.perf_counter()
        json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        context.timings["serialization"] = time.perf_counter() - serialization_started
        result["performance"]["stages_seconds"] = dict(context.timings)
        result["elapsed_seconds"] = time.perf_counter() - started
        return result
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}"
        )
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "active_backpack_quick_view",
            "reason": reason,
            "values": {},
            "elapsed_seconds": time.perf_counter() - started,
            "evidence": {
                "pid": context.memory.pid if context else None,
                "process_start_ticks": (
                    context.memory.process_start_ticks if context else None
                ),
                "read_only": True,
            },
        }


def clear_backpack_quick_view_cache() -> None:
    global _quick_view_cache
    with _QUICK_CACHE_LOCK:
        _quick_view_cache = None


__all__ = [
    "QUICK_SETTING_FIELDS",
    "clear_backpack_quick_view_cache",
    "read_backpack_quick_settings_snapshot",
]
