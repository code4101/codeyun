from __future__ import annotations

"""Strictly read-only snapshots of the game's world-line activity Runtime."""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses


ACTIVITY_MANAGER_MARKER = b"LuaActivityMgr"
ACTIVITY_MANAGER_METHODS = frozenset(
    {"Inst_get", "LuaActivityMgr", "GetOpenServerTime"}
)
# Keep the proven physical cache identity shared with activity_shop.  The key
# predates this generic adapter; it is not a business or public API name.
ACTIVITY_MANAGER_CACHE_KEY = "activity-manager-for-shop"


def _resolve_activity_manager_runtime(
    memory: MumuProcessMemory,
    *,
    allow_discovery: bool,
    force_refresh: bool,
) -> tuple[int, bool, str]:
    """Resolve ActivityMgr cheaply before using the marker compatibility path."""

    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key=ACTIVITY_MANAGER_CACHE_KEY,
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="ActivityMgr",
            required_methods=ACTIVITY_MANAGER_METHODS,
            validate=lambda _reader, _address: None,
            force_refresh=bool(force_refresh),
        )
        return root, cache_hit, "lua_global"
    except FanxiuRuntimeMemoryError:
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key=ACTIVITY_MANAGER_CACHE_KEY,
            marker=ACTIVITY_MANAGER_MARKER,
            required_methods=ACTIVITY_MANAGER_METHODS,
            validate=lambda reader, address: _activity_data_fields(
                reader, address
            ),
            allow_discovery=bool(allow_discovery),
            force_refresh=bool(force_refresh),
        )
        return root, cache_hit, "constructor_marker"


def _long_or_int(reader: LuaJitReader, value: Any) -> int | None:
    if isinstance(value, LuaRef):
        return reader.long(value)
    return as_int(value)


def _activity_data_fields(
    reader: LuaJitReader,
    manager_root: int,
) -> dict[Any, Any]:
    manager = manager_index_fields(
        reader,
        manager_root,
        ACTIVITY_MANAGER_METHODS,
    )
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("ActivityData"))
    if "_WorldLineActiveInfoList" not in data:
        raise FanxiuRuntimeMemoryError(
            "ActivityMgr.ActivityData 世界线活动列表尚未加载",
            code="data_not_loaded",
        )
    return data


def _load_activity_definitions(
    export_root: str | Path | None = None,
) -> dict[int, dict[str, Any]]:
    root = (
        Path(export_root).expanduser().resolve()
        if export_root is not None
        else resolve_fanxiu_export_root()
    )
    path = root / "parsed_configs" / "Activity" / "rows.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise FanxiuRuntimeMemoryError(
            "Activity 静态配置不是列表",
            code="schema_mismatch",
        )
    return {
        activity_id: dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and (activity_id := as_int(row.get("id"))) is not None
    }


def _decode_worldline_activity_items(
    reader: LuaJitReader,
    activity_data: Mapping[Any, Any],
    definitions: Mapping[int, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    values, declared_count = reader.list_items(
        activity_data.get("_WorldLineActiveInfoList")
    )
    if declared_count is None:
        raise FanxiuRuntimeMemoryError(
            "世界线活动列表缺少声明数量",
            code="snapshot_incoherent",
        )
    if declared_count != len(values):
        raise FanxiuRuntimeMemoryError(
            "世界线活动列表声明数量与可读项不一致",
            code="snapshot_incoherent",
        )

    items: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        fields = reader.fields(value)
        activity_id = as_int(fields.get("activityId"))
        runtime_id = _long_or_int(reader, fields.get("id"))
        activity_type = as_int(fields.get("activityType"))
        state = as_int(fields.get("state"))
        start_time = _long_or_int(reader, fields.get("startTime"))
        end_time = _long_or_int(reader, fields.get("endTime"))
        if (
            activity_id is None
            or runtime_id is None
            or activity_type is None
            or state is None
            or start_time is None
            or end_time is None
            or activity_id <= 0
            or runtime_id <= 0
            or start_time <= 0
            or end_time < start_time
        ):
            raise FanxiuRuntimeMemoryError(
                f"世界线活动第 {index + 1} 项身份或周期不完整",
                code="snapshot_incoherent",
            )
        definition = definitions.get(activity_id)
        configured_type = (
            as_int(definition.get("activityId"))
            if definition is not None
            else None
        )
        if definition is not None and configured_type != activity_type:
            raise FanxiuRuntimeMemoryError(
                f"世界线活动 {activity_id} 的 Runtime 类型与静态配置不一致",
                code="snapshot_incoherent",
            )
        name = str(
            (definition or {}).get("name_plain")
            or (definition or {}).get("name")
            or ""
        ).strip()
        if definition is not None and not name:
            raise FanxiuRuntimeMemoryError(
                f"世界线活动 {activity_id} 缺少权威名称",
                code="schema_mismatch",
            )
        server_count = as_int((definition or {}).get("crossGroup"))
        items.append(
            {
                "id": runtime_id,
                "activityId": activity_id,
                "activityType": activity_type,
                "state": state,
                "startTime": start_time,
                "endTime": end_time,
                "prepareEndTime": _long_or_int(
                    reader, fields.get("prepareEndTime")
                ),
                "closePanelTime": _long_or_int(
                    reader, fields.get("closePanelTime")
                ),
                "scheduleId": as_int(fields.get("scheduleId")),
                "loopDay": as_int(fields.get("loopDay")),
                "avgWorldLevel": as_int(fields.get("avgWorldLevel")),
                "runtimeCrossGroup": as_int(fields.get("crossGroup")),
                "serverCount": server_count,
                "name": name,
                "identityComplete": bool(definition is not None and name),
                "littleName": str(
                    (definition or {}).get("littleName_plain")
                    or (definition or {}).get("littleName")
                    or ""
                ).strip(),
                "baseId": as_int((definition or {}).get("baseId")),
                "source": "runtime_memory+activity_config",
            }
        )
    return items, declared_count


def _open_server_time_ms(
    reader: LuaJitReader,
    activity_data: Mapping[Any, Any],
) -> int | None:
    """Read the already-materialized open-server time without calling Lua."""

    for key in (
        "openServerTime",
        "_openServerTime",
        "OpenServerTime",
        "_OpenServerTime",
    ):
        value = _long_or_int(reader, activity_data.get(key))
        if value is not None and value >= 1_000_000_000_000:
            return value
    return None


def read_worldline_activity_runtime_snapshot(
    *,
    allow_discovery: bool = False,
    force_refresh: bool = False,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Read the already-loaded #66 activity model without calling game code."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        # Process identity discovery is a cheap, strictly read-only target
        # binding step; it is not ActivityMgr cold discovery.  In particular,
        # opening #66 can naturally load ActivityData but cannot populate this
        # Python process' module-local ``_process_cache``.  Therefore a cold or
        # expired process cache must still fall back to normal PID/maps
        # discovery, while ``allow_discovery`` continues to gate only the
        # expensive constructor-marker Manager fallback below.
        memory = MumuProcessMemory.discover_cached(fallback_to_discovery=True)
        root, cache_hit, manager_resolver = _resolve_activity_manager_runtime(
            memory,
            allow_discovery=bool(allow_discovery),
            force_refresh=bool(force_refresh),
        )
        reader = LuaJitReader(memory)
        activity_data = _activity_data_fields(reader, root)
        items, declared_count = _decode_worldline_activity_items(
            reader,
            activity_data,
            _load_activity_definitions(export_root),
        )
        open_server_time = _open_server_time_ms(reader, activity_data)
        captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source_kind": "worldline_activity_runtime_memory",
            "captured_at": captured_at,
            "count": len(items),
            "declared_count": declared_count,
            "resolved_identity_count": sum(
                1 for item in items if item.get("identityComplete")
            ),
            "unresolved_identity_count": sum(
                1 for item in items if not item.get("identityComplete")
            ),
            "items": items,
            "openServerTime": open_server_time,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "manager_root": f"0x{root:x}",
                "manager_cache_hit": cache_hit,
                "manager_resolver": manager_resolver,
                "protocol": (
                    "LuaActivityMgr.Model.ActivityData."
                    "_WorldLineActiveInfoList"
                ),
            },
        }
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
            "source_kind": "worldline_activity_runtime_memory",
            "reason": reason,
            "captured_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "elapsed_seconds": time.perf_counter() - started_at,
            "items": [],
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }
