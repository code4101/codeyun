from __future__ import annotations

"""Strictly read the active BeastSpirit quick-synthesis panel selection."""

import json
import time
from datetime import datetime, timezone
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    as_int,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    acquire_ui_runtime_context,
)


_PANEL_KEYS = frozenset({
    "m_panel", "panelRoot", "tabBtnRoot", "tabBtn", "tabPanelGroup",
    "open_index", "curTabIndex", "panelShowComps", "_dt_",
    "dropDownItem_material", "dropDownItem_cost", "confirmBtn",
    "DropDownBox1", "DropDownBox2", "_selectLevelId", "_selectCountId",
    "V_CurrentProbability",
})


def _decode_batch_panel(panel_address: int, *, field) -> dict[str, Any]:
    for name in (
        "dropDownItem_material", "dropDownItem_cost", "confirmBtn",
        "DropDownBox1", "DropDownBox2",
    ):
        if table_ref(field(panel_address, name)) is None:
            raise FanxiuRuntimeMemoryError(
                f"BeastSpiritBatchStrengthPanel 身份字段 {name} 未加载"
            )
    level = as_int(field(panel_address, "_selectLevelId"))
    count = as_int(field(panel_address, "_selectCountId"))
    probability = field(panel_address, "V_CurrentProbability")
    if level is None or level <= 0:
        raise FanxiuRuntimeMemoryError("_selectLevelId 不是正整数")
    if count is None or count <= 0:
        raise FanxiuRuntimeMemoryError("_selectCountId 不是正整数")
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        raise FanxiuRuntimeMemoryError("V_CurrentProbability 不是明确数值")
    probability_value = float(probability)
    if probability_value < 0 or probability_value > 100:
        raise FanxiuRuntimeMemoryError("V_CurrentProbability 超出百分比范围")
    return {
        "source_level": int(level),
        "batch_size": int(count),
        "success_probability_percent": probability_value,
    }


def _select_unique_panel(candidates: dict[int, dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        raise FanxiuRuntimeMemoryError(
            "NotLoaded: active BeastSpiritBatchStrengthPanel 未加载"
        )
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            f"Ambiguous: 同时发现 {len(candidates)} 个 active BeastSpiritBatchStrengthPanel"
        )
    return next(iter(candidates.values()))


def _snapshot(context: UiRuntimeContext) -> dict[str, Any]:
    scan_started = time.perf_counter()
    reader = context.reader
    field = context.field
    table = reader.table(context.binding.component_storage_address)
    raw_windows = [*table["array"], *table["fields"].values()]
    candidates: dict[int, dict[str, Any]] = {}
    seen_windows: set[int] = set()
    for raw_window in raw_windows:
        window = table_ref(raw_window)
        if window is None or window.address in seen_windows:
            continue
        seen_windows.add(window.address)
        windows, count = reader.list_items(window)
        if count is None or count <= 0 or len(windows) != count:
            continue
        component = table_ref(windows[-1])
        outer = table_ref(field(component.address, "m_panel")) if component else None
        if outer is None:
            continue
        if any(
            table_ref(field(outer.address, name)) is None
            for name in ("panelRoot", "tabBtnRoot", "tabBtn", "tabPanelGroup")
        ):
            continue
        tab_group = table_ref(field(outer.address, "tabPanelGroup"))
        current_index = as_int(field(tab_group.address, "curTabIndex")) if tab_group else None
        open_index = as_int(field(outer.address, "open_index"))
        # BeastSpiritStrengthenView adds single first and batch second.
        if current_index != 1 or open_index != 1:
            continue
        components = table_ref(field(tab_group.address, "panelShowComps"))
        storage = table_ref(field(components.address, "_dt_")) if components else None
        if storage is None:
            continue
        storage_table = reader.table(storage.address)
        slot = current_index + 1
        active_raw = (
            storage_table["array"][slot]
            if slot < len(storage_table["array"])
            else storage_table["fields"].get(slot)
        )
        active_component = table_ref(active_raw)
        panel = (
            table_ref(field(active_component.address, "m_panel"))
            if active_component else None
        )
        if panel is None:
            continue
        candidates[panel.address] = {
            "_panel_address_int": panel.address,
            "window_address": f"0x{window.address:x}",
            "window_component_address": f"0x{component.address:x}",
            "outer_panel_address": f"0x{outer.address:x}",
            "active_panel_address": f"0x{panel.address:x}",
            "active_tab_index": current_index,
        }
    context.timings["window_scan"] = time.perf_counter() - scan_started
    selected = _select_unique_panel(candidates)
    # Count active identities before decoding any mutable selection field.
    # This prevents one malformed active panel from being silently skipped and
    # turning a genuinely ambiguous open set into a false singleton.
    panel_address = int(selected.pop("_panel_address_int"))
    values = _decode_batch_panel(panel_address, field=field)
    selected = {**values, **selected}
    memory = context.memory
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "active_beast_spirit_batch_strength_panel",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "captured_at_epoch": time.time(),
        **selected,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "read_only": True,
            "active_membership": "UIShowMgr.V_M_compDic",
        },
        "performance": {
            "cache_mode": context.cache_mode,
            "stages_seconds": dict(context.timings),
        },
    }


def read_beast_spirit_quick_synthesis_snapshot() -> dict[str, Any]:
    started = time.perf_counter()
    context: UiRuntimeContext | None = None
    try:
        context = acquire_ui_runtime_context(_PANEL_KEYS)
        result = _snapshot(context)
        serialization_started = time.perf_counter()
        json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        context.timings["serialization"] = time.perf_counter() - serialization_started
        result["performance"] = {
            "cache_mode": context.cache_mode,
            "stages_seconds": dict(context.timings),
        }
        result["elapsed_seconds"] = time.perf_counter() - started
        return result
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "active_beast_spirit_batch_strength_panel",
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started,
            "evidence": {
                "pid": context.memory.pid if context else None,
                "process_start_ticks": context.memory.process_start_ticks if context else None,
                "read_only": True,
            },
        }


__all__ = [
    "read_beast_spirit_quick_synthesis_snapshot",
]
