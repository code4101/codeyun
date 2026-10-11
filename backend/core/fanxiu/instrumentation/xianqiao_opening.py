"""只读当前开窍弹窗；模型成本与消费预算由各自接口负责。"""

from typing import Any

from .exchange_shop import project_exchange_shop_numeric_row
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    UiRuntimeContext, read_active_ui_window_panels, read_ui_object_field,
    read_ui_runtime_snapshot,
)


def _read_opening_dialog(context: UiRuntimeContext) -> dict[str, Any]:
    panels = read_active_ui_window_panels(context, "CoreUpgradeView")
    if not panels:
        return {"complete": False, "enough": None, "read_only": True,
                "reason": "xianqiao_opening_dialog_not_open"}
    if len(panels) != 1:
        raise FanxiuRuntimeMemoryError("仙窍开窍弹窗实例不唯一", code="runtime_incomplete")
    panel = panels[0]
    cfg = table_ref(read_ui_object_field(context, panel.address, "cfg"))
    data = table_ref(read_ui_object_field(context, panel.address, "data"))
    enough = read_ui_object_field(context, panel.address, "isEnough")
    if cfg is None or data is None or type(enough) is not bool:
        raise FanxiuRuntimeMemoryError("仙窍开窍弹窗尚未就绪", code="data_not_loaded")
    # CoreBase packed row: id=1, type=2, parts=3. Do not mistake native
    # numeric config storage for an empty table of named fields.
    row = project_exchange_shop_numeric_row(context.reader.table(cfg.address))
    if len(row) <= 3:
        raise FanxiuRuntimeMemoryError("仙窍开窍配置身份缺失", code="runtime_incomplete")
    cfg_id, system_type, part_id = (as_int(row[index]) for index in (1, 2, 3))
    level = as_int(read_ui_object_field(context, data.address, "activeLevel"))
    opened = read_ui_object_field(context, data.address, "unLockEquip")
    if (any(value is None or value <= 0 for value in (cfg_id, system_type, part_id))
            or level is None or level < 0 or type(opened) is not bool):
        raise FanxiuRuntimeMemoryError("仙窍开窍弹窗身份或进度无效", code="runtime_incomplete")
    return {"complete": True, "read_only": True, "source": "active_CoreUpgradeView",
            "config_id": cfg_id, "system_type": system_type, "part_id": part_id,
            "active_level": level, "opened": opened, "enough": enough,
            "pid": context.binding.pid, "process_start_ticks": context.binding.process_start_ticks}


def read_xianqiao_opening_dialog_snapshot() -> dict[str, Any]:
    """Read the selected native opening dialog without opening or confirming it.

    A closed dialog returns unknown. ``enough`` is the client's current
    overflow-exp comparison, not authorization or proof of a completed opening.
    Positive GUI acceptance remains required before a caller consumes resources.
    """
    return read_ui_runtime_snapshot([], _read_opening_dialog, fast=True)
