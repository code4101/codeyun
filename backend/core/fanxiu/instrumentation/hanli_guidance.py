from __future__ import annotations

"""韩立指导课程列表的只读投影；不打开窗口、不同步、不开始挑战。"""

from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from backend.core.fanxiu.instrumentation.exchange_shop import project_exchange_shop_numeric_row
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext, read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot,
)


def _read_list(context: UiRuntimeContext) -> dict[str, Any]:
    panels = read_active_ui_window_panels(context, "CapacityTowerListView")
    if not panels:
        return {"complete": False, "reason": "hanli_guidance_list_not_open", "read_only": True}
    if len(panels) != 1:
        raise FanxiuRuntimeMemoryError("韩立指导课程列表实例不唯一", code="runtime_incomplete")
    panel = panels[0]
    index = as_int(read_ui_object_field(context, panel.address, "curIndex"))
    course_list = table_ref(read_ui_object_field(context, panel.address, "capacityTowerList"))
    if index is None or index < 0 or course_list is None:
        raise FanxiuRuntimeMemoryError("韩立指导当前课程未加载", code="data_not_loaded")
    indexed, count = context.reader.indexed_list_items(course_list)
    if count is None or not 0 < count == len(indexed) <= 4096 or index >= count:
        raise FanxiuRuntimeMemoryError("韩立指导课程列表数量或当前索引不完整", code="runtime_incomplete")
    # Cipairs/列表UI索引从0开始；reader返回_dt_的1..count存储键。
    selected = [value for key, value in indexed if key == index + 1]
    course = table_ref(selected[0]) if len(selected) == 1 else None
    if course is None:
        raise FanxiuRuntimeMemoryError("韩立指导当前课程未唯一定位", code="runtime_incomplete")
    # CapacityTower.id为生成配置第1列；兼容数字键位于array或hash。
    row = project_exchange_shop_numeric_row(context.reader.table(course.address))
    course_id = as_int(row[1]) if len(row) > 1 else None
    if course_id is None or course_id <= 0:
        raise FanxiuRuntimeMemoryError("韩立指导当前课程身份不完整", code="runtime_incomplete")
    return {"complete": True, "source": "active_CapacityTowerListView", "read_only": True,
            "current_course_id": course_id, "current_course_index": index, "course_count": count,
            "pid": context.binding.pid, "process_start_ticks": context.binding.process_start_ticks}


def read_hanli_guidance_list_snapshot() -> dict[str, Any]:
    """读取当前打开的课程列表，不能将混沌灵塔或缓存管理器当作该窗口。

    由Window枚举及UIShowMgr注册表证明当前窗口成员身份。关闭时返回
    complete=False；加载不完整或与当前管理器不同课时失败关闭。
    当前课程投影不代表详情页已打开，也不授权开始挑战。
    已真实验收末课1426及窗口关闭；课程切换中的投影仍待验收。
    """
    result = read_ui_runtime_snapshot({"Window", "CapacityTowerListView", "curIndex", "capacityTowerList"},
                                      _read_list, fast=True)
    if result.get("complete") is True:
        from backend.core.fanxiu.instrumentation.capacity_tower import read_hanli_guidance_course_snapshot
        current = read_hanli_guidance_course_snapshot()
        evidence = current.get("evidence") or {}
        if (current.get("complete") is not True
                or evidence.get("pid") != result["pid"]
                or evidence.get("process_start_ticks") != result["process_start_ticks"]
                or current.get("current_course_id") != result["current_course_id"]):
            raise FanxiuRuntimeMemoryError("韩立指导列表与当前管理器课程或进程身份不一致", code="runtime_incomplete")
        result["current_course_identity_verified"] = True
    return result
