from __future__ import annotations

"""Strict read-only projection of the active Bothdraw 1/10 draw toggle."""

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    as_int,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    active_ui_component_objects,
    read_ui_runtime_snapshot,
    read_ui_object_field,
)


_TOGGLE_KEYS = frozenset(
    {
        "m_panel",
        "tabPanelGroup",
        "curTabIndex",
        "panelShowComps",
        "count",
        "_dt_",
        "V_ActivityId",
        "V_UseTenTimes",
        "toggle",
    }
)
_LABEL_COMPONENT_NAMES = ("costTF", "costTF2", "numTF")
_LABEL_IDENTITY_FIELDS = ("FatherId", "ComponentId", "OrginComponentId")
_TICKET_BINDING_KEYS = _TOGGLE_KEYS | frozenset(
    {"V_CostResourceType", "V_CostNum", "V_CostItemId", "V_CostItemlo", "V_CostItemlo2"}
)


def _direct_active_bothdraw_panels(context: UiRuntimeContext) -> list[tuple[int, int]]:
    """Read the legacy direct tab hierarchy when the panel is directly exposed."""

    reader = context.reader
    field = context.field
    storage = reader.table(context.binding.component_storage_address)
    candidates: list[tuple[int, int]] = []
    for raw_window in [*storage["array"], *storage["fields"].values()]:
        window = table_ref(raw_window)
        if window is None:
            continue
        windows, window_count = reader.list_items(window)
        if window_count is None or window_count <= 0 or len(windows) != window_count:
            continue
        component = table_ref(windows[-1])
        main_panel = table_ref(field(component.address, "m_panel")) if component else None
        tab_group = (
            table_ref(field(main_panel.address, "tabPanelGroup")) if main_panel else None
        )
        current_index = (
            as_int(field(tab_group.address, "curTabIndex")) if tab_group else None
        )
        panels = (
            table_ref(field(tab_group.address, "panelShowComps")) if tab_group else None
        )
        panel_count = as_int(field(panels.address, "count")) if panels else None
        panel_storage = table_ref(field(panels.address, "_dt_")) if panels else None
        if (
            current_index is None
            or panel_count is None
            or not 0 <= current_index < panel_count
            or panel_storage is None
        ):
            continue
        table = reader.table(panel_storage.address)
        slot = current_index + 1
        raw_active = (
            table["array"][slot]
            if slot < len(table["array"])
            else table["fields"].get(slot)
        )
        active_component = table_ref(raw_active)
        panel = (
            table_ref(field(active_component.address, "m_panel"))
            if active_component
            else None
        )
        if panel is None:
            continue
        if (
            as_int(field(panel.address, "V_ActivityId")) is not None
            and table_ref(field(panel.address, "toggle")) is not None
            and isinstance(field(panel.address, "V_UseTenTimes"), bool)
        ):
            candidates.append((panel.address, current_index))

    return candidates


def _is_bothdraw_panel(context: UiRuntimeContext, address: int) -> bool:
    """Recognize the UI panel which owns the live 1/10 toggle.

    ``V_BothDrawPlayVO`` was an old panel-version assumption.  The live
    Lingxiao panel instead exposes its activity identity plus the actual
    toggle state; draw counters belong to BothdrawMgr and are joined by the
    caller's independently read Runtime snapshot.
    """

    activity_id = as_int(read_ui_object_field(context, address, "V_ActivityId"))
    toggle = table_ref(read_ui_object_field(context, address, "toggle"))
    use_ten = read_ui_object_field(context, address, "V_UseTenTimes")
    return (
        activity_id is not None
        and activity_id > 0
        and toggle is not None
        and isinstance(use_ten, bool)
    )


def _current_tab_panel(
    context: UiRuntimeContext, host_address: int
) -> tuple[int, int] | None:
    """Return a host's selected tab panel using its bounded CDictionary slot."""

    tab_group = table_ref(read_ui_object_field(context, host_address, "tabPanelGroup"))
    if tab_group is None:
        return None
    current_index = as_int(read_ui_object_field(context, tab_group.address, "curTabIndex"))
    panels = table_ref(read_ui_object_field(context, tab_group.address, "panelShowComps"))
    panel_count = as_int(read_ui_object_field(context, panels.address, "count")) if panels else None
    storage = table_ref(read_ui_object_field(context, panels.address, "_dt_")) if panels else None
    if (
        current_index is None
        or panel_count is None
        or not 0 <= current_index < panel_count <= 16
        or storage is None
    ):
        return None
    values = context.reader.table(storage.address).get("array") or ()
    slot = current_index + 1
    if slot >= len(values):
        return None
    component = table_ref(values[slot])
    panel = table_ref(read_ui_object_field(context, component.address, "m_panel")) if component else None
    return (panel.address, current_index) if panel is not None else None


def _active_bothdraw_panel(context: UiRuntimeContext) -> tuple[int, int | None]:
    """Locate the sole active Bothdraw panel without treating a shallow tree as truth.

    Older panels are reachable through the direct tab layout.  Newer activity
    windows can mount the exact same panel deeper under ``m_ChildCompList``;
    the shared UI helper is deliberately bounded to that verified schema.
    A panel appearing via both paths is one object, while two distinct panels
    remain an ambiguity and stop the reader safely.
    """

    candidates: dict[int, int | None] = {
        address: current_index
        for address, current_index in _direct_active_bothdraw_panels(context)
    }
    # The strict shared adapter expands registered roots and their immediate
    # verified UI children only; it does not swallow a malformed active tree
    # or recurse into the permanent world HUD before the activity host is
    # identified.
    for component in active_ui_component_objects(context):
        # A registry component and its ``m_panel`` are both valid closure
        # nodes; inspect each exact object but de-duplicate by panel address.
        for candidate in (
            component,
            table_ref(read_ui_object_field(context, component.address, "m_panel")),
        ):
            if candidate is not None and _is_bothdraw_panel(context, candidate.address):
                candidates.setdefault(candidate.address, None)
            if candidate is not None:
                active_tab = _current_tab_panel(context, candidate.address)
                if active_tab is not None:
                    panel_address, current_index = active_tab
                    if _is_bothdraw_panel(context, panel_address):
                        candidates.setdefault(panel_address, current_index)

    unique = sorted(candidates.items())
    if not unique:
        raise FanxiuRuntimeMemoryError("NotLoaded: active Bothdraw panel 未加载")
    if len(unique) != 1:
        raise FanxiuRuntimeMemoryError(f"Ambiguous: 同时发现 {len(unique)} 个 Bothdraw panel")
    return unique[0]


def _snapshot(
    context: UiRuntimeContext,
    *,
    expected_activity_id: int | None,
    expected_x: int | None = None,
    expected_y: int | None = None,
) -> dict[str, Any]:
    panel_address, current_index = _active_bothdraw_panel(context)
    field = context.field
    activity_id = as_int(field(panel_address, "V_ActivityId"))
    use_ten = field(panel_address, "V_UseTenTimes")
    toggle = table_ref(field(panel_address, "toggle"))
    if activity_id is None or activity_id <= 0:
        raise FanxiuRuntimeMemoryError("当前活动面板缺少 V_ActivityId")
    if expected_activity_id is not None and activity_id != int(expected_activity_id):
        raise FanxiuRuntimeMemoryError(
            f"当前活动面板不属于目标活动：{activity_id} != {int(expected_activity_id)}"
        )
    if toggle is None:
        raise FanxiuRuntimeMemoryError("当前活动面板缺少鉴宝次数 toggle")
    # Current Lingxiao panel builds do not retain ``V_BothDrawPlayVO``.  The
    # caller joins this UI state with the same-activity BothdrawMgr snapshot;
    # retain the counters as provenance only, never pretend they came from
    # this panel.
    if not isinstance(use_ten, bool):
        raise FanxiuRuntimeMemoryError("V_UseTenTimes 不是明确 Lua boolean")
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "active_activity_panel.V_UseTenTimes+bothdraw_runtime_join",
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "activity_id": activity_id,
        "ten_draw_enabled": bool(use_ten),
        "batch_size": 10 if use_ten else 1,
        "current_tab_index": current_index,
        "x": int(expected_x) if expected_x is not None else None,
        "y": int(expected_y) if expected_y is not None else None,
        "panel_address": f"0x{panel_address:x}",
        "toggle_address": f"0x{toggle.address:x}",
        "evidence": {
            "pid": context.memory.pid,
            "process_start_ticks": context.memory.process_start_ticks,
            "read_only": True,
        },
    }


def _label_component_locator(
    context: UiRuntimeContext,
    *,
    panel_address: int,
    component_name: str,
) -> dict[str, int | str]:
    """Read a text wrapper's stable Lua identity without invoking Unity bridges.

    ``LuaUIText.text`` and rect access call game-side Unity bridges, which are
    intentionally outside Fanxiu's external read-only policy.  The wrapper's
    component identity is nevertheless valuable provenance for a later OCR
    alignment pass: it proves that a particular OCR label was observed while
    these exact three UI objects were mounted on the same activity panel.
    """

    wrapper = table_ref(read_ui_object_field(context, panel_address, component_name))
    if wrapper is None:
        raise FanxiuRuntimeMemoryError(f"当前活动面板缺少 {component_name} 文本组件")
    def identity_int(value: Any) -> int | None:
        decoded = as_int(value)
        if decoded is not None:
            return decoded
        # ComponentId is a Lua Long wrapper in the live client, while the
        # neighboring identity fields are plain numbers.  Decode only this
        # known numeric wrapper; this is not a generic object traversal.
        try:
            return context.reader.long(value) if table_ref(value) is not None else None
        except (FanxiuRuntimeMemoryError, AttributeError):
            return None

    values = {
        name: identity_int(read_ui_object_field(context, wrapper.address, name))
        for name in _LABEL_IDENTITY_FIELDS
    }
    if any(value is None or value < 0 for value in values.values()):
        raise FanxiuRuntimeMemoryError(
            f"{component_name} 文本组件身份不完整：{values}"
        )
    return {
        "wrapper_address": f"0x{wrapper.address:x}",
        **{name: int(value) for name, value in values.items() if value is not None},
    }


def _label_locator_snapshot(
    context: UiRuntimeContext,
    *,
    expected_activity_id: int | None,
) -> dict[str, Any]:
    panel_address, current_index = _active_bothdraw_panel(context)
    activity_id = as_int(read_ui_object_field(context, panel_address, "V_ActivityId"))
    if activity_id is None or activity_id <= 0:
        raise FanxiuRuntimeMemoryError("当前活动面板缺少 V_ActivityId")
    if expected_activity_id is not None and activity_id != int(expected_activity_id):
        raise FanxiuRuntimeMemoryError(
            f"当前活动面板不属于目标活动：{activity_id} != {int(expected_activity_id)}"
        )
    locators = {
        name: _label_component_locator(
            context, panel_address=panel_address, component_name=name
        )
        for name in _LABEL_COMPONENT_NAMES
    }
    identity_tuples = {
        tuple(locator[field] for field in _LABEL_IDENTITY_FIELDS)
        for locator in locators.values()
    }
    if len(identity_tuples) != len(locators):
        raise FanxiuRuntimeMemoryError("抽奖文本组件身份重复，拒绝建立 OCR 对齐")
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "active_activity_panel.text_component_locators",
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "activity_id": int(activity_id),
        "current_tab_index": current_index,
        "panel_address": f"0x{panel_address:x}",
        "components": locators,
        "evidence": {
            "pid": context.memory.pid,
            "process_start_ticks": context.memory.process_start_ticks,
            "read_only": True,
            "unity_text_or_rect_read": False,
        },
    }


def _item_config_row_id(context: UiRuntimeContext, config: Any, *, label: str) -> int:
    """Read an Item config row's id from its generated-array slot.

    The active FestivalTreasure panel receives these exact Item rows from the
    game's config database.  In the current generated Item schema, Lua array
    slot 1 is the row id (slot 0 is a nil sentinel).  We never guess a paired
    id: callers additionally cross-check the primary row against the panel's
    explicit ``V_CostItemId`` before accepting the replacement row.
    """

    ref = table_ref(config)
    if ref is None:
        raise FanxiuRuntimeMemoryError(f"当前活动面板缺少{label}配置行")
    values = context.reader.table(ref.address).get("array") or ()
    if len(values) <= 1:
        raise FanxiuRuntimeMemoryError(f"{label}配置行缺少 Item id 槽位")
    item_id = as_int(values[1])
    if item_id is None or item_id <= 0:
        raise FanxiuRuntimeMemoryError(f"{label}配置行 Item id 无效")
    return int(item_id)


def _ticket_binding_snapshot(
    context: UiRuntimeContext, *, expected_activity_id: int | None
) -> dict[str, Any]:
    """Read the two Runtime-owned ticket identities, never their OCR labels."""

    panel_address, current_index = _active_bothdraw_panel(context)
    activity_id = as_int(read_ui_object_field(context, panel_address, "V_ActivityId"))
    primary_resource_id = as_int(
        read_ui_object_field(context, panel_address, "V_CostResourceType")
    )
    cost_per_draw = as_int(read_ui_object_field(context, panel_address, "V_CostNum"))
    primary_item_id = as_int(read_ui_object_field(context, panel_address, "V_CostItemId"))
    if activity_id is None or primary_resource_id is None or cost_per_draw is None:
        raise FanxiuRuntimeMemoryError("当前活动面板缺少寻宝资源身份")
    if expected_activity_id is not None and activity_id != int(expected_activity_id):
        raise FanxiuRuntimeMemoryError("当前活动面板不属于目标活动")
    if primary_resource_id <= 0 or cost_per_draw <= 0 or primary_item_id is None:
        raise FanxiuRuntimeMemoryError("当前活动面板寻宝资源配置无效")
    primary_config_id = _item_config_row_id(
        context,
        read_ui_object_field(context, panel_address, "V_CostItemlo"),
        label="主寻宝券",
    )
    if primary_config_id != int(primary_item_id):
        raise FanxiuRuntimeMemoryError("主寻宝券配置与面板 item id 不一致")
    replacement_item_id = _item_config_row_id(
        context,
        read_ui_object_field(context, panel_address, "V_CostItemlo2"),
        label="绑定替代寻宝券",
    )
    if replacement_item_id == primary_config_id:
        raise FanxiuRuntimeMemoryError("绑定替代寻宝券不得与主券同 id")
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "active_festival_treasure_panel.ticket_bindings",
        "activity_id": int(activity_id),
        "primary_resource_id": int(primary_resource_id),
        "primary_item_id": int(primary_config_id),
        "bound_replacement_item_id": int(replacement_item_id),
        "cost_per_draw": int(cost_per_draw),
        "current_tab_index": current_index,
        "panel_address": f"0x{panel_address:x}",
        "evidence": {"pid": context.memory.pid, "process_start_ticks": context.memory.process_start_ticks, "read_only": True},
    }
def read_bothdraw_ten_draw_runtime(
    *,
    expected_activity_id: int | None = None,
    expected_x: int | None = None,
    expected_y: int | None = None,
) -> dict[str, Any]:
    """Read the same boolean mirrored by the active toggle and draw request."""

    started = time.perf_counter()
    try:
        return {
            **read_ui_runtime_snapshot(
                _TOGGLE_KEYS,
                lambda context: _snapshot(
                    context,
                    expected_activity_id=expected_activity_id,
                    expected_x=expected_x,
                    expected_y=expected_y,
                ),
            ),
            "elapsed_seconds": time.perf_counter() - started,
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "active_activity_panel.V_UseTenTimes",
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started,
            "evidence": {
                "pid": None,
                "process_start_ticks": None,
                "read_only": True,
            },
        }


def read_bothdraw_text_component_locators_runtime(
    *, expected_activity_id: int | None = None
) -> dict[str, Any]:
    """Expose strict identity evidence for later OCR-to-UI text alignment."""

    started = time.perf_counter()
    try:
        return {
            **read_ui_runtime_snapshot(
                _TOGGLE_KEYS,
                lambda context: _label_locator_snapshot(
                    context, expected_activity_id=expected_activity_id
                ),
            ),
            "elapsed_seconds": time.perf_counter() - started,
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "active_activity_panel.text_component_locators",
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started,
            "evidence": {"read_only": True, "unity_text_or_rect_read": False},
        }


def read_bothdraw_ticket_bindings_runtime(
    *, expected_activity_id: int | None = None
) -> dict[str, Any]:
    """Read the primary-wallet and bound-item ticket identities for a panel."""

    started = time.perf_counter()
    try:
        return {
            **read_ui_runtime_snapshot(
                _TICKET_BINDING_KEYS,
                lambda context: _ticket_binding_snapshot(
                    context, expected_activity_id=expected_activity_id
                ),
            ),
            "elapsed_seconds": time.perf_counter() - started,
        }
    except Exception as exc:
        return {
            "ok": False, "available": False, "complete": False,
            "source": "active_festival_treasure_panel.ticket_bindings",
            "reason": str(exc), "elapsed_seconds": time.perf_counter() - started,
            "evidence": {"read_only": True},
        }
__all__ = [
    "read_bothdraw_ticket_bindings_runtime",
    "read_bothdraw_ten_draw_runtime",
    "read_bothdraw_text_component_locators_runtime",
]
