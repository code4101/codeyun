"""当前境界突破条件窗的只读判断；不打开窗口、不开始突破。"""

from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError, as_int
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
    # The live panel uses V_CurDungeonId; the exported Lua's _CurDungeonId
    # spelling is not its current runtime field. Zero is observed while the
    # current realm conditions are unmet and must not become a guessed ID.
    raw_dungeon = read_ui_object_field(context, panels[0].address, "V_CurDungeonId")
    dungeon_id = as_int(raw_dungeon)
    challenge_identity_complete = (
        not isinstance(raw_dungeon, bool) and dungeon_id is not None and dungeon_id > 0
    )
    return {"complete": True, "conditions_met": ready, "read_only": True,
            "dungeon_id": dungeon_id if challenge_identity_complete else None,
            "challenge_identity_complete": challenge_identity_complete,
            "challenge_identity_reason": "" if challenge_identity_complete else "dungeon_identity_unavailable",
            "dungeon_id_observed": raw_dungeon if isinstance(raw_dungeon, (str, int, float, bool)) else None,
            "source": "active_LeiJieView.canUp", "pid": context.binding.pid,
            "process_start_ticks": context.binding.process_start_ticks}


def read_realm_breakthrough_conditions_snapshot() -> dict[str, Any]:
    """读取当前打开的原生条件窗，关闭时返回未知而非复用旧判断。

    canUp 是原生窗口检查条件的合取结果，不能替代修为、资源预算、
    挑战胜负或实际等级提升的验收。challenge_identity_complete 独立
    表示是否读到有效副本身份；未知身份不使已加载的条件判断失效。
    只有身份完整时 dungeon_id 才可供进入后的副本核对，不授权挑战。
    此接口不调用任何游戏 Lua。
    """
    return read_ui_runtime_snapshot([], _read_conditions, fast=True)


def read_realm_breakthrough_identity_sources_snapshot() -> dict[str, Any]:
    """诊断当前窗口的副本字段；候选字段不构成挑战身份或执行授权。

    用于核验静态客户端与当前运行时的字段差异，只读取已打开且
    身份唯一的窗口。调用方不得按候选名称自行选择挑战。
    """
    def read(context):
        snapshot = _read_conditions(context)
        if not snapshot['complete']:
            return snapshot
        panels = read_active_ui_window_panels(context, 'LeiJieView')
        fields = context.reader.fields(panels[0])
        candidates = {
            key: value for key, value in fields.items()
            if isinstance(key, str) and 'dungeon' in key.lower()
            and isinstance(value, (str, int, float, bool, type(None)))
        }
        return {**snapshot, 'dungeon_field_candidates': candidates,
                'candidate_semantics_verified': False}
    return read_ui_runtime_snapshot([], read, fast=True)
