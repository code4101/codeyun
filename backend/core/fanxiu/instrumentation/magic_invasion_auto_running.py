from __future__ import annotations

"""Read-only snapshot of the active Magic native auto-exorcism loop."""

import time
from datetime import datetime, timezone
from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    active_ui_component_objects,
    read_ui_object_field,
    read_ui_runtime_snapshot,
)


_MAGIC_MANAGER_METHODS = frozenset(
    {"LuaMagicinvadeMgr", "Inst_get", "GetHadAutoTimes", "GetSetAutoTimes"}
)
_UI_KEYS = frozenset(
    {
        "timeCount",
        "timeCountMax",
        "NeedJoinTeam",
        "CanJoinTeam",
        "isSendTeamList",
        "isSendUseItemMsg",
        "needDelayCloseReward",
        "needAutoUseItem",
        "needAutoTenKill",
        "needAutoHundredKill",
        "TargetTeamList",
        "selectList",
        "txtAlearyCount",
        "autoTimer",
    }
)


def _required_int(value: Any, name: str, *, minimum: int = 0) -> int:
    decoded = as_int(value)
    if decoded is None or decoded < minimum:
        raise FanxiuRuntimeMemoryError(
            f"MapStartAutoTipsView.{name} 无效：{value!r}",
            code="runtime_incomplete",
        )
    return int(decoded)


def _required_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise FanxiuRuntimeMemoryError(
            f"MapStartAutoTipsView.{name} 不是布尔值：{value!r}",
            code="runtime_incomplete",
        )
    return bool(value)


def _list_values(reader: Any, value: Any, name: str) -> list[Any]:
    ref = table_ref(value)
    if ref is None:
        raise FanxiuRuntimeMemoryError(
            f"MapStartAutoTipsView.{name} 未加载", code="not_loaded"
        )
    items, declared = reader.list_items(ref)
    if declared is None or int(declared) != len(items):
        raise FanxiuRuntimeMemoryError(
            f"MapStartAutoTipsView.{name} CList 数量不一致",
            code="snapshot_incoherent",
        )
    return list(items)


def _manager_fields(reader: Any, manager_root: int) -> tuple[dict[Any, Any], dict[Any, Any]]:
    manager = manager_index_fields(reader, manager_root, _MAGIC_MANAGER_METHODS)
    instance_ref = table_ref(manager.get("inst"))
    if instance_ref is None:
        raise FanxiuRuntimeMemoryError("MagicinvadeMgr.inst 未加载", code="not_loaded")
    instance = reader.fields(instance_ref)
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("MagicinvadeData"))
    if not data:
        raise FanxiuRuntimeMemoryError(
            "MagicinvadeMgr.Model.MagicinvadeData 未加载", code="not_loaded"
        )
    return instance, data


def _decode_running_state(
    reader: Any,
    panel_address: int,
    manager_root: int,
    *,
    field,
) -> dict[str, Any]:
    time_count = _required_int(field(panel_address, "timeCount"), "timeCount")
    time_count_max = _required_int(
        field(panel_address, "timeCountMax"), "timeCountMax", minimum=1
    )
    flags = {
        name: _required_bool(field(panel_address, name), name)
        for name in (
            "NeedJoinTeam",
            "CanJoinTeam",
            "isSendTeamList",
            "isSendUseItemMsg",
            "needAutoUseItem",
            "needAutoTenKill",
            "needAutoHundredKill",
        )
    }
    delay = _required_int(
        field(panel_address, "needDelayCloseReward"), "needDelayCloseReward"
    )
    targets = _list_values(
        reader, field(panel_address, "TargetTeamList"), "TargetTeamList"
    )
    selected_raw = _list_values(reader, field(panel_address, "selectList"), "selectList")
    selected_types = {as_int(value) for value in selected_raw}
    if None in selected_types:
        raise FanxiuRuntimeMemoryError(
            "MapStartAutoTipsView.selectList 含非整数类型",
            code="schema_mismatch",
        )

    manager, data = _manager_fields(reader, manager_root)
    had_auto_times = _required_int(manager.get("hadAutoTimes"), "hadAutoTimes")
    set_auto_times = _required_int(manager.get("autoSetTimes"), "autoSetTimes")
    is_in_auto_raw = data.get("isInAuto")
    is_in_auto = bool(is_in_auto_raw) if type(is_in_auto_raw) is bool else False

    cached_events = _list_values(reader, data.get("MagicEventsList"), "MagicEventsList")
    cached_event_types: list[int] = []
    for raw in cached_events:
        row = reader.fields(raw)
        config = reader.fields(row.get("configData"))
        auto_type = as_int(config.get("autoType"))
        if auto_type is not None:
            cached_event_types.append(int(auto_type))
    matching_event_count = sum(
        1 for auto_type in cached_event_types if auto_type in selected_types
    )

    info = reader.fields(data.get("V_MagicInvadeInfo"))
    raw_event_count = (
        len(reader.dictionary_fields(info.get("events"))) if info else 0
    )
    challenge_server_count: int | None = None
    if info:
        counts = reader.dictionary_fields(info.get("counts"))
        challenge = reader.fields(counts.get(2))
        challenge_server_count = as_int(challenge.get("current"))

    blockers: list[str] = []
    if had_auto_times < set_auto_times and challenge_server_count is not None:
        if challenge_server_count > 0 and matching_event_count == 0:
            blockers.append("server_count_positive_but_no_cached_matching_event")
        if flags["NeedJoinTeam"] and flags["isSendTeamList"]:
            blockers.append("join_required_but_team_request_latched")
        if flags["isSendUseItemMsg"]:
            blockers.append("item_use_request_latched")
    return {
        "panel": {
            "time_count": time_count,
            "time_count_max": time_count_max,
            **flags,
            "needDelayCloseReward": delay,
            "target_team_count": len(targets),
            "selected_types": sorted(int(value) for value in selected_types),
        },
        "manager": {
            "had_auto_times": had_auto_times,
            "set_auto_times": set_auto_times,
            "is_in_auto": is_in_auto,
            "challenge_server_count": challenge_server_count,
            "raw_event_count": raw_event_count,
            "cached_available_event_count": len(cached_events),
            "cached_matching_event_count": matching_event_count,
            "cached_event_auto_types": cached_event_types,
        },
        "blocker_candidates": blockers,
    }


def _snapshot(context: UiRuntimeContext) -> dict[str, Any]:
    def field(address: int, name: str) -> Any:
        return read_ui_object_field(context, address, name)

    panels = [
        component.address
        for component in active_ui_component_objects(context)
        if as_int(field(component.address, "timeCountMax")) in {1, 10}
        and table_ref(field(component.address, "TargetTeamList")) is not None
        and table_ref(field(component.address, "txtAlearyCount")) is not None
    ]
    if not panels:
        raise FanxiuRuntimeMemoryError(
            "NotLoaded: 当前未打开魔道自动除魔运行浮层", code="not_loaded"
        )
    if len(panels) != 1:
        raise FanxiuRuntimeMemoryError(
            f"Ambiguous: 同时发现 {len(panels)} 个魔道自动除魔运行浮层",
            code="runtime_incomplete",
        )
    manager_root, cache_hit, _environment = resolve_lua_global_manager_root(
        context.memory,
        manager_key="magic-invasion-auto-running",
        state_address=int(_lua_addresses(context.memory)["state"], 16),
        global_name="MagicinvadeMgr",
        required_methods=_MAGIC_MANAGER_METHODS,
        validate=lambda reader, root: _manager_fields(reader, root),
    )
    decoded = _decode_running_state(
        context.reader,
        panels[0],
        manager_root,
        field=field,
    )
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "active_map_start_auto_tips_view",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        **decoded,
        "evidence": {
            "pid": context.memory.pid,
            "process_start_ticks": context.memory.process_start_ticks,
            "panel_address": int(panels[0]),
            "manager_root_cache_hit": bool(cache_hit),
            "active_membership": "UIShowMgr.V_M_compDic",
            "read_only": True,
            "lua_methods_invoked": False,
        },
    }


def read_magic_invasion_auto_running_snapshot() -> dict[str, Any]:
    """Read the active loop's latches, counters, and cached event supply."""

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
            "source": "active_map_start_auto_tips_view",
            "reason": str(exc),
            "error_code": exc.code,
            "elapsed_seconds": time.perf_counter() - started,
            "evidence": {"read_only": True, "lua_methods_invoked": False},
        }


def read_magic_invasion_counters() -> dict[str, Any]:
    """Read manager counters independently of the auto-running overlay.

    Counts are authoritative even when their map labels are covered by icons.
    No manager method is invoked and no UI is opened to obtain this snapshot.
    """
    def read(context: UiRuntimeContext) -> dict[str, Any]:
        root, _, _ = resolve_lua_global_manager_root(
            context.memory, manager_key="magic-invasion-auto-running",
            state_address=int(_lua_addresses(context.memory)["state"], 16),
            global_name="MagicinvadeMgr", required_methods=_MAGIC_MANAGER_METHODS,
            validate=lambda reader, root: _manager_fields(reader, root))
        manager, data = _manager_fields(context.reader, root)
        info = context.reader.fields(data.get("V_MagicInvadeInfo"))
        counts = context.reader.dictionary_fields(info.get("counts"))
        decoded = {int(k): _required_int(context.reader.fields(v).get("current"), f"counts[{k}]")
                   for k, v in counts.items()}
        if 1 not in decoded or 2 not in decoded:
            raise FanxiuRuntimeMemoryError("魔道缺少探查/挑战次数", code="runtime_incomplete")
        return {"explore_count": decoded[1], "challenge_count": decoded[2],
                "had_auto_times": as_int(manager.get("hadAutoTimes")),
                "set_auto_times": as_int(manager.get("autoSetTimes")),
                "is_in_auto": data.get("isInAuto") is True,
                "info": {str(k): v for k, v in info.items() if isinstance(v, (int, float, str, bool))},
                "pid": context.memory.pid, "process_start_ticks": context.memory.process_start_ticks}
    return read_ui_runtime_snapshot(frozenset(), read, fast=True)


__all__ = ["read_magic_invasion_auto_running_snapshot", "read_magic_invasion_counters"]
