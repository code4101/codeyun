from __future__ import annotations

"""Read the active Magic Invasion auto-banish settings panel."""

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
    active_ui_component_objects,
    read_ui_object_field,
    read_ui_runtime_snapshot,
)


QUALITY_NAMES = {
    1: "堂主",
    2: "长老",
    3: "宗主",
    4: "太上长老",
    5: "天外魔神",
    11: "圣主",
}

_AUTO_USE_DEMON_TOKEN = 6
_TEN_BANISH = 7
_SKIP_ANIMATION = 8
_QUICK_BANISH = 9
_HIDDEN_TARGET_NAMES = {
    10: "武始",
}

_PRIMARY_ALLOWED = frozenset(
    {
        *QUALITY_NAMES,
        _AUTO_USE_DEMON_TOKEN,
        _TEN_BANISH,
        _SKIP_ANIMATION,
        _QUICK_BANISH,
        *_HIDDEN_TARGET_NAMES,
    }
)
_AUGMENT_ALLOWED = frozenset({2, 3, 4, 5, 11})
_UI_KEYS = frozenset(
    {
        "selectList",
        "selectedFourTimesList",
        "selectedSpecialList",
        "autoTimes",
        "useNum",
        "useMax",
        "SliderNum",
        "SureBtn",
        "selectBtn6",
    }
)


def _decode_type_list(
    reader: Any,
    value: Any,
    *,
    field_name: str,
    allowed: frozenset[int],
) -> set[int]:
    ref = table_ref(value)
    if ref is None:
        raise FanxiuRuntimeMemoryError(
            f"MapActAutoTipsView.{field_name} 未加载",
            code="not_loaded",
        )
    items, declared_count = reader.list_items(ref)
    if declared_count is None:
        raise FanxiuRuntimeMemoryError(
            f"MapActAutoTipsView.{field_name} 缺少 CList count",
            code="runtime_incomplete",
        )
    decoded: list[int] = []
    for raw in items:
        item = as_int(raw)
        if item is None or item not in allowed:
            raise FanxiuRuntimeMemoryError(
                f"MapActAutoTipsView.{field_name} 含无效类型 {raw!r}",
                code="schema_mismatch",
            )
        decoded.append(int(item))
    if len(decoded) != len(set(decoded)):
        raise FanxiuRuntimeMemoryError(
            f"MapActAutoTipsView.{field_name} 含重复类型",
            code="runtime_incomplete",
        )
    return set(decoded)


def _decode_settings_panel(
    reader: Any,
    panel_address: int,
    *,
    field,
) -> dict[str, Any]:
    # These two component fields distinguish the editing panel from the
    # similarly named runtime overlay.  No Unity method is invoked.
    if table_ref(field(panel_address, "SliderNum")) is None:
        raise FanxiuRuntimeMemoryError("MapActAutoTipsView.SliderNum 未加载")
    if table_ref(field(panel_address, "SureBtn")) is None:
        raise FanxiuRuntimeMemoryError("MapActAutoTipsView.SureBtn 未加载")
    if table_ref(field(panel_address, "selectBtn6")) is None:
        raise FanxiuRuntimeMemoryError("MapActAutoTipsView.selectBtn6 未加载")

    selected = _decode_type_list(
        reader,
        field(panel_address, "selectList"),
        field_name="selectList",
        allowed=_PRIMARY_ALLOWED,
    )
    fourfold = _decode_type_list(
        reader,
        field(panel_address, "selectedFourTimesList"),
        field_name="selectedFourTimesList",
        allowed=_AUGMENT_ALLOWED,
    )
    pursuit = _decode_type_list(
        reader,
        field(panel_address, "selectedSpecialList"),
        field_name="selectedSpecialList",
        allowed=_AUGMENT_ALLOWED,
    )
    auto_times = as_int(field(panel_address, "autoTimes"))
    use_num = as_int(field(panel_address, "useNum"))
    use_max = as_int(field(panel_address, "useMax"))
    if auto_times is None or auto_times < 0:
        raise FanxiuRuntimeMemoryError(
            f"MapActAutoTipsView.autoTimes 无效：{auto_times!r}",
            code="runtime_incomplete",
        )
    if use_num is None or use_num < 0 or use_max is None or use_max < 0:
        raise FanxiuRuntimeMemoryError(
            "MapActAutoTipsView 次数边界未完整加载",
            code="runtime_incomplete",
        )
    if auto_times != use_num or use_num > use_max:
        raise FanxiuRuntimeMemoryError(
            "MapActAutoTipsView 次数状态不一致",
            code="runtime_incomplete",
        )

    qualities = {
        str(type_id): {
            "type_id": type_id,
            "name": name,
            "selected": type_id in selected,
            "fourfold_merit": type_id in fourfold,
            "pursuit_chain": type_id in pursuit,
            "augment_configurable": type_id in _AUGMENT_ALLOWED,
        }
        for type_id, name in QUALITY_NAMES.items()
    }
    options = {
        "auto_use_demon_token": _AUTO_USE_DEMON_TOKEN in selected,
        "ten_banish": _TEN_BANISH in selected,
        "skip_animation": _SKIP_ANIMATION in selected,
        "quick_banish": _QUICK_BANISH in selected,
    }
    return {
        "qualities": qualities,
        "options": options,
        # Stable semantic projection consumed by the idempotent GUI planner.
        "auto_exorcism_choices": {
            "quality_hall_master": qualities["1"]["selected"],
            "quality_elder": qualities["2"]["selected"],
            "elder_quadruple_merit": qualities["2"]["fourfold_merit"],
            "elder_chase_chain": qualities["2"]["pursuit_chain"],
            "quality_sect_master": qualities["3"]["selected"],
            "sect_master_quadruple_merit": qualities["3"]["fourfold_merit"],
            "sect_master_chase_chain": qualities["3"]["pursuit_chain"],
            "quality_supreme_elder": qualities["4"]["selected"],
            "supreme_elder_quadruple_merit": qualities["4"][
                "fourfold_merit"
            ],
            "supreme_elder_chase_chain": qualities["4"]["pursuit_chain"],
            "quality_extraterrestrial_demon": qualities["5"]["selected"],
            "extraterrestrial_demon_quadruple_merit": qualities["5"][
                "fourfold_merit"
            ],
            "extraterrestrial_demon_chase_chain": qualities["5"][
                "pursuit_chain"
            ],
            "quality_saint_master": qualities["11"]["selected"],
            "saint_master_quadruple_merit": qualities["11"][
                "fourfold_merit"
            ],
            "saint_master_chase_chain": qualities["11"]["pursuit_chain"],
            "auto_use_exorcism_order": options["auto_use_demon_token"],
            "skip_animation": options["skip_animation"],
            "fast_exorcism": options["quick_banish"],
            "tenfold_exorcism": options["ten_banish"],
        },
        "times": {
            "selected": int(auto_times),
            "maximum": int(use_max),
        },
        "hidden_targets": {
            str(type_id): {
                "type_id": type_id,
                "name": name,
                "selected": type_id in selected,
                "fourfold_merit": type_id in fourfold,
                "pursuit_chain": type_id in pursuit,
            }
            for type_id, name in _HIDDEN_TARGET_NAMES.items()
        },
        "raw_type_ids": {
            "selected": sorted(selected),
            "fourfold_merit": sorted(fourfold),
            "pursuit_chain": sorted(pursuit),
        },
    }


def _snapshot(context: UiRuntimeContext) -> dict[str, Any]:
    def field(address: int, name: str) -> Any:
        return read_ui_object_field(context, address, name)

    panel_addresses = [
        component.address
        for component in active_ui_component_objects(context)
        if table_ref(field(component.address, "SliderNum")) is not None
        and table_ref(field(component.address, "SureBtn")) is not None
        and table_ref(field(component.address, "selectBtn6")) is not None
    ]
    if not panel_addresses:
        raise FanxiuRuntimeMemoryError(
            "NotLoaded: 当前未打开魔道入侵自动除魔设置弹层",
            code="not_loaded",
        )
    if len(panel_addresses) != 1:
        raise FanxiuRuntimeMemoryError(
            f"Ambiguous: 同时发现 {len(panel_addresses)} 个自动除魔设置弹层",
            code="runtime_incomplete",
        )
    decoded = _decode_settings_panel(
        context.reader,
        panel_addresses[0],
        field=field,
    )
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "active_map_act_auto_tips_view",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "captured_at_epoch": time.time(),
        **decoded,
        "performance": {
            "cache_mode": context.cache_mode,
            "stages_seconds": dict(context.timings),
        },
        "evidence": {
            "pid": context.memory.pid,
            "process_start_ticks": context.memory.process_start_ticks,
            # The active component address is the read-only identity of this
            # exact panel instance.  Callers use it only to reject a
            # before/after pair that crossed a close/reopen boundary.
            "panel_address": int(panel_addresses[0]),
            "active_membership": "UIShowMgr.V_M_compDic",
            "read_only": True,
            "lua_methods_invoked": False,
        },
    }


def read_magic_invasion_auto_settings_snapshot() -> dict[str, Any]:
    """Read unsaved values shown by the active auto-banish settings panel."""

    started = time.perf_counter()
    try:
        result = read_ui_runtime_snapshot(_UI_KEYS, _snapshot, fast=True)
        result["elapsed_seconds"] = time.perf_counter() - started
        return result
    except FanxiuRuntimeMemoryError as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "active_map_act_auto_tips_view",
            "reason": str(exc),
            "error_code": exc.code,
            "qualities": {},
            "options": {},
            "times": {},
            "elapsed_seconds": time.perf_counter() - started,
            "evidence": {
                "active_membership": "UIShowMgr.V_M_compDic",
                "read_only": True,
                "lua_methods_invoked": False,
            },
        }


__all__ = [
    "QUALITY_NAMES",
    "read_magic_invasion_auto_settings_snapshot",
]
