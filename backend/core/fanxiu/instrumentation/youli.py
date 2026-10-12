"""修仙传游历区域详情的只读原生身份与快速模式投影。"""

from .runtime_memory import FanxiuRuntimeMemoryError, as_int
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot,
)


def _read_region_dialog(context):
    panels = read_active_ui_window_panels(context, "BookTravelInfoView")
    if not panels:
        return {"complete": False, "read_only": True,
                "reason": "youli_region_dialog_not_open"}
    if len(panels) != 1:
        raise FanxiuRuntimeMemoryError("游历区域详情实例不唯一", code="runtime_incomplete")
    panel = panels[0]
    area_id = as_int(read_ui_object_field(context, panel.address, "areaId"))
    opened = read_ui_object_field(context, panel.address, "isOpen")
    quick_enabled = read_ui_object_field(context, panel.address, "hasVip")
    quick_selected = read_ui_object_field(context, panel.address, "useVip")
    if (area_id is None or area_id <= 0 or type(opened) is not bool
            or type(quick_enabled) is not bool
            or (quick_enabled and type(quick_selected) is not bool)):
        raise FanxiuRuntimeMemoryError("游历区域身份或快速模式尚未就绪", code="data_not_loaded")
    return {"complete": True, "read_only": True,
            "source": "active_BookTravelInfoView", "area_id": area_id,
            "region_open": opened, "quick_enabled": quick_enabled,
            "quick_selected": quick_selected if quick_enabled else False,
            "pid": context.binding.pid,
            "process_start_ticks": context.binding.process_start_ticks}


def read_youli_region_dialog_snapshot():
    """读取当前已打开的区域；不打开页面、不游历、不购买。

    快速模式仅描述界面选择，不等同于资源充足或消费授权。资源读取
    应使用同一真实详情的现有体力与单次消耗；禁止把库存缓存当实时恢复值。
    """
    return read_ui_runtime_snapshot([], _read_region_dialog, fast=True)
