"""Read the active ActivityRankServerMainView identity, without invoking Lua."""

from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError, as_int
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields, read_ui_runtime_snapshot,
)


def _snapshot(ctx) -> dict[str, Any]:
    required = {"activityId", "containRankId", "cfgTable", "tabPanelGroup", "FirstTabBtnItem"}
    panels = [obj for obj in active_ui_component_objects(ctx)
              if has_ui_object_fields(ctx, obj.address, required)]
    if len(panels) != 1:
        raise FanxiuRuntimeMemoryError("活动榜主面板未唯一加载", code="data_not_loaded")
    panel = ctx.reader.fields(panels[0])
    activity_id = as_int(panel.get("activityId"))
    if not activity_id or activity_id <= 0:
        raise FanxiuRuntimeMemoryError("活动榜面板 ID 不完整", code="runtime_incomplete")
    tabs = ctx.reader.fields(panel.get("tabPanelGroup"))
    tab_index = as_int(tabs.get("curTabIndex"))
    if tab_index is None or tab_index < 0:
        raise FanxiuRuntimeMemoryError("活动榜页签索引不完整", code="runtime_incomplete")
    return {"ok": True, "complete": True, "activity_id": activity_id,
            "tab_index": tab_index,
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "evidence": {"pid": ctx.memory.pid, "process_start_ticks": ctx.memory.process_start_ticks,
                         "source": "active_ui_registry"}}


def read_activity_rank_page_snapshot() -> dict[str, Any]:
    """Call after scene readiness; cached rank data never proves an open page."""
    try:
        return read_ui_runtime_snapshot((), _snapshot, fast=True)
    except FanxiuRuntimeMemoryError as exc:
        return {"ok": False, "complete": False, "activity_id": None,
                "error_code": exc.code, "reason": str(exc)}
