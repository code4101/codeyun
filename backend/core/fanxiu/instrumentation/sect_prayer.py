"""宗门祈福详情只读投影；不打开页面、不祈福、不修改助手配置。"""

import re

from .exchange_shop import project_exchange_shop_numeric_row
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot,
)


def _read_prayer_dialog(context):
    panels = read_active_ui_window_panels(context, "AllianceDonateInfoTipsView")
    if not panels:
        return {"complete": False, "read_only": True,
                "reason": "sect_prayer_dialog_not_open"}
    if len(panels) != 1:
        raise FanxiuRuntimeMemoryError("宗门祈福详情实例不唯一", code="runtime_incomplete")
    panel = panels[0]
    cfg = table_ref(read_ui_object_field(context, panel.address, "infoData"))
    free = read_ui_object_field(context, panel.address, "isFree")
    maximum = read_ui_object_field(context, panel.address, "isMax")
    if cfg is None or type(free) is not bool or type(maximum) is not bool:
        raise FanxiuRuntimeMemoryError("宗门祈福详情尚未就绪", code="data_not_loaded")
    row = project_exchange_shop_numeric_row(context.reader.table(cfg.address))
    if len(row) <= 4:
        raise FanxiuRuntimeMemoryError("宗门祈福配置身份缺失", code="runtime_incomplete")
    config_id, position_id, ordinal = [as_int(row[i]) for i in (1, 2, 3)]
    # AllianceDonate packed schema: id, grade (职位), number, cost.
    # All current ordinal rows use Item|1_N. Unsupported formats fail closed.
    cost = re.fullmatch(r"Item\|(\d+)_(\d+)", str(row[4]))
    if (any(v is None or v <= 0 for v in (config_id, position_id, ordinal))
            or cost is None or any(int(v) <= 0 for v in cost.groups())):
        raise FanxiuRuntimeMemoryError("宗门祈福配置或消费格式未验证", code="runtime_incomplete")
    enough = read_ui_object_field(context, panel.address, "isEnough")
    if not maximum and not free and type(enough) is not bool:
        raise FanxiuRuntimeMemoryError("宗门祈福余额判断未就绪", code="data_not_loaded")
    return {"complete": True, "read_only": True,
            "source": "active_AllianceDonateInfoTipsView",
            "config_id": config_id, "position_id": position_id,
            "displayed_ordinal": ordinal,
            "next_ordinal": None if maximum else ordinal,
            "donated_count": ordinal if maximum else ordinal - 1,
            "is_max": maximum, "is_free": free,
            "currency_item_id": int(cost[1]), "nominal_cost": int(cost[2]),
            "next_cost": None if maximum else (0 if free else int(cost[2])),
            "enough": None if maximum else (True if free else enough),
            "pid": context.binding.pid,
            "process_start_ticks": context.binding.process_start_ticks}


def read_sect_prayer_dialog_snapshot():
    """读取当前祈福弹窗；关闭时返回未知，不将缺失状态当作免费。

    enough 仅为客户端余额比较，is_free 仅适用于显示的下一次。
    本接口不授权付费动作，不证明后续次数仍免费。已在真实收费详情页
    核对第2次150灵石、已完成1次，以及关闭页面后的未知状态。
    """
    return read_ui_runtime_snapshot([], _read_prayer_dialog, fast=True)
