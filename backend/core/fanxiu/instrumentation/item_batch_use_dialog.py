from __future__ import annotations

"""Read the active common item-batch-use dialog without executing Lua."""

from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    as_int,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    active_ui_component_objects,
    read_ui_object_field,
    read_ui_runtime_snapshot,
)


# ``ItemBatchUseView`` and its activity-specific subclasses all implement this
# exact sibling-field contract.  The reverse source assigns these controls in
# InitView and the mutable values in DataInfo/UpdateBatchUseShow.
_ITEM_BATCH_USE_FIELDS = frozenset(
    {
        "useNum",
        "useMax",
        "itemvo",
        "itemlo",
        "SliderNum",
        "NumTF",
        "ItemNumTF",
        "NameTF",
        "RemoveBtn",
        "AddBtn",
        "SynthesisBtn",
        "V_SliderNumInfo",
    }
)


def _positive_integer(context: Any, value: Any) -> int | None:
    result = as_int(value)
    if result is None:
        if value is None:
            return None
        try:
            result = context.reader.long(value)
        except (AttributeError, TypeError, ValueError, FanxiuRuntimeMemoryError):
            return None
    if result is None:
        return None
    return int(result) if int(result) > 0 else None


def _item_id(context: Any, itemlo: Any, itemvo: Any) -> int | None:
    itemlo_id = _positive_integer(
        context,
        read_ui_object_field(context, itemlo.address, "id"),
    )
    itemvo_ids = [
        _positive_integer(
            context,
            read_ui_object_field(context, itemvo.address, field),
        )
        for field in ("baseId", "code")
    ]
    identities = {value for value in (itemlo_id, *itemvo_ids) if value is not None}
    if len(identities) > 1:
        raise FanxiuRuntimeMemoryError(
            f"批量使用弹窗道具身份冲突：{sorted(identities)}"
        )
    return next(iter(identities), None)


def _read_snapshot(context: Any, *, expected_item_id: int | None) -> dict[str, Any]:
    candidates: list[tuple[Any, Any, Any, int | None]] = []
    for component in active_ui_component_objects(context):
        fields = context.reader.fields(component)
        if not _ITEM_BATCH_USE_FIELDS.issubset(fields):
            continue
        itemlo = table_ref(fields.get("itemlo"))
        itemvo = table_ref(fields.get("itemvo"))
        if itemlo is None or itemvo is None:
            continue
        item_id = _item_id(context, itemlo, itemvo)
        if expected_item_id is None or item_id == int(expected_item_id):
            candidates.append((component, itemlo, itemvo, item_id))
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            "active 批量使用弹窗数量不唯一："
            f"expected_item_id={expected_item_id}, count={len(candidates)}"
        )

    panel, _itemlo, itemvo, item_id = candidates[0]
    current = _positive_integer(
        context, read_ui_object_field(context, panel.address, "useNum")
    )
    single_use_maximum = _positive_integer(
        context, read_ui_object_field(context, panel.address, "useMax")
    )
    owned_count = _positive_integer(
        context, read_ui_object_field(context, itemvo.address, "num")
    )
    if current is None or single_use_maximum is None or owned_count is None:
        raise FanxiuRuntimeMemoryError(
            "批量使用弹窗 current/useMax/itemvo.num 运行态不完整"
        )
    if current > single_use_maximum or single_use_maximum > owned_count:
        raise FanxiuRuntimeMemoryError(
            "批量使用弹窗数量关系无效："
            f"current={current}, single_use_maximum={single_use_maximum}, "
            f"owned_count={owned_count}"
        )

    use_parameter_max = bool(
        read_ui_object_field(context, panel.address, "V_UseParamMax")
    )
    # ItemBatchUseView.UpdateBatchUseShow sets SliderNum.max to inventory by
    # default, or to min(useMax, inventory) when V_UseParamMax is enabled.
    slider_maximum = (
        min(single_use_maximum, owned_count)
        if use_parameter_max
        else owned_count
    )
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "active_item_batch_use_dialog",
        "read_only": True,
        "item_id": int(item_id or 0),
        "current": current,
        "minimum": 1,
        "single_use_maximum": single_use_maximum,
        "owned_count": owned_count,
        "slider_maximum": slider_maximum,
        "use_parameter_max": use_parameter_max,
        "evidence": {
            "pid": context.binding.pid,
            "process_start_ticks": context.binding.process_start_ticks,
            "panel_address": f"0x{panel.address:x}",
            "current_field": "useNum",
            "single_use_maximum_field": "useMax",
            "owned_count_field": "itemvo.num",
        },
    }


def read_item_batch_use_dialog_snapshot(
    *, expected_item_id: int | None = None
) -> dict[str, Any]:
    """Read current selection, native use cap, inventory and slider range."""

    expected = int(expected_item_id) if expected_item_id is not None else None
    if expected is not None and expected <= 0:
        raise ValueError("批量使用弹窗期望道具 ID 必须为正整数")
    return read_ui_runtime_snapshot(
        _ITEM_BATCH_USE_FIELDS | {"id", "baseId", "code", "num", "V_UseParamMax"},
        lambda context: _read_snapshot(context, expected_item_id=expected),
        fast=True,
    )


__all__ = ["read_item_batch_use_dialog_snapshot"]
