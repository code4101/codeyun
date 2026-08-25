from __future__ import annotations

"""Observe currently visible Revenue activities without inventing schedules.

``RevenueMgr.V_ActivityDic`` contains both visible and dormant activities, while
the #34 left menu proves which activities are currently exposed to the player.
Only their exact ``activityId`` intersection is projected here.  The result is
an observation fact, deliberately not a #66 occurrence and not job authority.
"""

import time
from datetime import datetime
from typing import Any, Iterable, Mapping

from backend.core.fanxiu.instrumentation.activity_menu import (
    ActivityMenuSnapshot,
    read_activity_menu_snapshot,
)
from backend.core.fanxiu.instrumentation.bothdraw import (
    _REVENUE_METHODS,
    _dictionary_items,
    _fields,
    _revenue_data_fields,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    resolve_lua_global_manager_root,
)


SOURCE_KIND = "revenue_activity_observation_runtime_memory"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _positive_int(value: Any) -> int | None:
    result = as_int(value)
    return result if result is not None and result > 0 else None


def build_revenue_activity_observation_snapshot(
    revenue_items: Iterable[Mapping[str, Any]],
    menu_snapshot: ActivityMenuSnapshot,
    *,
    captured_at: str,
    pid: int,
    process_start_ticks: int,
    revenue_cache_hit: bool = False,
) -> dict[str, Any]:
    """Join two already-decoded read-only facts and fail closed on ambiguity."""

    if not menu_snapshot.complete:
        raise FanxiuRuntimeMemoryError(menu_snapshot.reason, code="data_not_loaded")
    if (
        menu_snapshot.pid != int(pid)
        or menu_snapshot.process_start_ticks != int(process_start_ticks)
    ):
        raise FanxiuRuntimeMemoryError("RevenueMgr 与 #34 左侧菜单进程身份不一致")

    revenue_by_id: dict[int, dict[str, Any]] = {}
    for source in revenue_items:
        item = dict(source)
        activity_id = _positive_int(item.get("activity_id"))
        if activity_id is None:
            raise FanxiuRuntimeMemoryError("RevenueMgr 活动缺少正整数 activityId")
        if activity_id in revenue_by_id:
            raise FanxiuRuntimeMemoryError(
                f"RevenueMgr 活动身份重复：{activity_id}"
            )
        revenue_by_id[activity_id] = item

    observations: list[dict[str, Any]] = []
    for menu_item in menu_snapshot.items:
        activity_id = _positive_int(menu_item.activity_id)
        if activity_id is None or activity_id not in revenue_by_id:
            continue
        revenue = revenue_by_id[activity_id]
        template_id = _positive_int(revenue.get("template_id"))
        menu_base_id = _positive_int(menu_item.base_id)
        if template_id and menu_base_id and template_id != menu_base_id:
            raise FanxiuRuntimeMemoryError(
                f"活动 {activity_id} 的 Revenue templateId 与菜单 baseId 不一致"
            )
        revenue_name = _text(revenue.get("name"))
        menu_name = _text(menu_item.name)
        menu_fallback = f"活动{activity_id}"
        if (
            revenue_name
            and menu_name
            and menu_name != menu_fallback
            and revenue_name != menu_name
        ):
            raise FanxiuRuntimeMemoryError(
                f"活动 {activity_id} 的 Revenue 名称与菜单名称不一致"
            )
        observations.append(
            {
                "observation_id": f"revenue:{activity_id}",
                "activity_id": activity_id,
                "template_id": template_id,
                "base_id": menu_base_id or template_id,
                "name": revenue_name or menu_name,
                "menu_index": int(menu_item.index),
                "menu_key": str(menu_item.key),
                "display": as_int(revenue.get("display")),
                "icon": _text(revenue.get("icon")) or menu_item.icon,
                "is_schedule_occurrence": False,
                "source_roles": ["RevenueMgr.V_ActivityDic", "#34.world_left"],
            }
        )

    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source_kind": SOURCE_KIND,
        "captured_at": str(captured_at),
        "count": len(observations),
        "items": observations,
        "evidence": {
            "pid": int(pid),
            "process_start_ticks": int(process_start_ticks),
            "revenue_cache_hit": bool(revenue_cache_hit),
            "menu_fingerprint": menu_snapshot.fingerprint,
            "menu_item_count": len(menu_snapshot.items),
            "revenue_item_count": len(revenue_by_id),
            "join": "exact_activity_id_intersection",
            "read_only": True,
        },
    }


def _decode_revenue_items(
    reader: LuaJitReader, revenue_data: Mapping[Any, Any]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw_id, raw_value in _dictionary_items(
        reader, revenue_data.get("V_ActivityDic")
    ).items():
        activity_id = _positive_int(raw_id)
        activity = _fields(reader, raw_value)
        base = _fields(reader, activity.get("revenueBaseVO"))
        if activity_id is None or not activity:
            raise FanxiuRuntimeMemoryError("RevenueMgr 活动字典存在无效条目")
        result.append(
            {
                "activity_id": activity_id,
                "template_id": _positive_int(base.get("templateId")),
                "name": _text(base.get("name")) or _text(activity.get("name")),
                "display": as_int(base.get("display")),
                "icon": _text(base.get("icon")),
            }
        )
    if not result:
        raise FanxiuRuntimeMemoryError(
            "RevenueMgr 活动字典尚未加载", code="data_not_loaded"
        )
    return result


def read_revenue_activity_observation_snapshot(
    *, force_refresh: bool = False
) -> dict[str, Any]:
    """Read the natural Revenue/menu intersection without executing Lua code."""

    started_at = time.perf_counter()
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    memory: MumuProcessMemory | None = None
    try:
        menu = read_activity_menu_snapshot("world_left")
        if not menu.complete:
            raise FanxiuRuntimeMemoryError(menu.reason, code="data_not_loaded")
        memory = MumuProcessMemory.discover_cached(fallback_to_discovery=True)
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="revenue-activity-observation",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="RevenueMgr",
            required_methods=_REVENUE_METHODS,
            validate=_revenue_data_fields,
            force_refresh=bool(force_refresh),
        )
        reader = LuaJitReader(memory)
        result = build_revenue_activity_observation_snapshot(
            _decode_revenue_items(reader, _revenue_data_fields(reader, root)),
            menu,
            captured_at=captured_at,
            pid=memory.pid,
            process_start_ticks=memory.process_start_ticks,
            revenue_cache_hit=cache_hit,
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result["evidence"]["manager_root"] = f"0x{root:x}"
        return result
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, (FanxiuRuntimeMemoryError, OSError, ValueError))
            else f"{type(exc).__name__}: {exc}"
        )
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source_kind": SOURCE_KIND,
            "captured_at": captured_at,
            "reason": reason,
            "count": 0,
            "items": [],
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "read_only": True,
            },
        }


__all__ = [
    "SOURCE_KIND",
    "build_revenue_activity_observation_snapshot",
    "read_revenue_activity_observation_snapshot",
]
