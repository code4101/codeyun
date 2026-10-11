"""当前境界突破条件窗的只读判断；不打开窗口、不开始突破。"""

from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError
from .ui_runtime_context import (
    UiRuntimeContext, read_active_ui_window_panels, read_ui_object_field,
    read_ui_runtime_snapshot,
)


def _read_conditions(context: UiRuntimeContext) -> dict[str, Any]:
    panels = read_active_ui_window_panels(context, "LeiJieView")
    if not panels:
        return {"complete": False, "conditions_met": None,
                "reason": "realm_conditions_window_not_open", "read_only": True}
    if len(panels) != 1:
        raise FanxiuRuntimeMemoryError("境界突破条件窗实例不唯一", code="runtime_incomplete")
    ready = read_ui_object_field(context, panels[0].address, "canUp")
    if not isinstance(ready, bool):
        raise FanxiuRuntimeMemoryError("境界突破条件判断尚未加载", code="data_not_loaded")
    return {"complete": True, "conditions_met": ready, "read_only": True,
            "source": "active_LeiJieView.canUp", "pid": context.binding.pid,
            "process_start_ticks": context.binding.process_start_ticks}


def read_realm_breakthrough_conditions_snapshot() -> dict[str, Any]:
    """读取当前打开的原生条件窗，关闭时返回未知而非复用旧判断。

    canUp 是原生窗口检查条件的合取结果，不能替代修为、资源预算、
    挑战胜负或实际等级提升的验收。此接口不调用任何游戏 Lua。
    """
    return read_ui_runtime_snapshot([], _read_conditions, fast=True)
