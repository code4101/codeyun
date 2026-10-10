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
    lua_jit_intern_state,
    resolve_interned_lua_string,
    table_ref,
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
    *, state_address: int | None = None,
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

    # Fresh addresses/bytes only, scoped to this reader and this observation.
    # Batch headers and the stored-hash pages for exactly the fields projected
    # below, avoiding full materialization of unrelated ActivityVO fields.
    names = ('id', 'activityId', 'activityType', 'state', 'startTime', 'endTime',
             'prepareEndTime', 'closePanelTime', 'scheduleId', 'loopDay', 'avgWorldLevel', 'crossGroup')
    keys = None
    if state_address is not None:
        refs = [table_ref(value) for value in values]
        if any(ref is None for ref in refs):
            raise FanxiuRuntimeMemoryError('世界线活动列表包含非表项', code='snapshot_incoherent')
        _global, strings, mask, seed = lua_jit_intern_state(reader.memory, state_address)
        keys = {name: resolve_interned_lua_string(reader.memory, string_table_address=strings,
                string_mask=mask, string_seed=seed, name=name) for name in names}
        reader.prefetch_hashed_string_fields((ref.address for ref in refs), key_addresses=keys.values())
    projected = [({name: reader.hashed_string_field(value.address, key_address=key, expected_name=name)
                   for name, key in keys.items()} if keys is not None else reader.fields(value)) for value in values]
    if keys is not None:
        # The 64-bit time/ID values are nested Lua Long tables. Batch their fresh
        # headers/hash pages too before long() validates flag/high/low semantics.
        long_refs = [table_ref(row.get(name)) for row in projected
                     for name in ('id', 'startTime', 'endTime', 'prepareEndTime', 'closePanelTime')]
        addresses = [ref.address for ref in long_refs if ref is not None]
        long_keys = [resolve_interned_lua_string(reader.memory, string_table_address=strings,
                    string_mask=mask, string_seed=seed, name=name)
                    for name in ('_IS_LUSUOLONG', '_low', '_internalHigh')]
        reader.prefetch_hashed_string_fields(addresses, key_addresses=long_keys)
    items: list[dict[str, Any]] = []
    for index, fields in enumerate(projected):
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
        process_done = time.perf_counter()
        root, cache_hit, manager_resolver = _resolve_activity_manager_runtime(
            memory,
            allow_discovery=bool(allow_discovery),
            force_refresh=bool(force_refresh),
        )
        root_done = time.perf_counter()
        reader = LuaJitReader(memory)
        activity_data = _activity_data_fields(reader, root)
        model_done = time.perf_counter()
        items, declared_count = _decode_worldline_activity_items(
            reader,
            activity_data,
            _load_activity_definitions(export_root),
            state_address=int(_lua_addresses(memory)["state"], 16),
        )
        decode_done = time.perf_counter()
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
            "performance": {"stages_seconds": {"process_identity": process_done-started_at,
                            "manager_root": root_done-process_done, "activity_model": model_done-root_done,
                            "fresh_item_projection": decode_done-model_done,
                            "finalization": time.perf_counter()-decode_done},
                            "memory": memory.diagnostics(), "reader": reader.diagnostics()},
            "evidence": {
                "adb_serial": memory.adb_serial,
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


def read_activity_period_runtime_snapshot(
    activity_id: int, *, runtime_id: int | None = None,
) -> dict[str, Any]:
    """Read one exact ActivityVO period without loading or operating the game.

    Raises on missing or ambiguous instances; presence alone never proves an
    opening date. Times are the server's epoch milliseconds. When a caller has
    an authoritative occurrence ID, runtime_id selects that exact instance;
    coexisting server/cross instances are never resolved by list order/state.
    """
    # Long-running purchases and quiet monitors can outlive the PID/maps cache.
    # Rebind the process read-only; ActivityMgr discovery remains disabled below.
    memory = MumuProcessMemory.discover_cached(fallback_to_discovery=True)
    root, _, _ = _resolve_activity_manager_runtime(
        memory, allow_discovery=False, force_refresh=False
    )
    reader = LuaJitReader(memory)
    data = _activity_data_fields(reader, root)
    matches = []
    for value in reader.dictionary_fields(data.get("_ActivityInfo")).values():
        if not isinstance(value, LuaRef) or value.kind != 'table':
            continue
        # Finding one period needs identity only. Decoding every field of all
        # loaded ActivityVOs repeats unrelated work at each pre/post guard.
        # Keep fresh parent membership and reject ambiguity as before; fetch
        # the time fields only for a matching instance, without address cache.
        row = reader.string_fields(value.address, frozenset({'activityId', 'id'}))
        if (as_int(row.get("activityId")) == int(activity_id)
                and (runtime_id is None or _long_or_int(reader, row.get("id")) == int(runtime_id))):
            row.update(reader.string_fields(value.address, frozenset({'startTime', 'endTime'})))
            matches.append(row)
    if not matches:
        raise FanxiuRuntimeMemoryError(
            f"活动 {activity_id} 的指定周期实例尚未加载", code="not_loaded")
    if len(matches) != 1:
        raise FanxiuRuntimeMemoryError(f"活动 {activity_id} 的周期实例不唯一", code="runtime_incomplete")
    row = matches[0]
    start = _long_or_int(reader, row.get("startTime"))
    end = _long_or_int(reader, row.get("endTime"))
    if start is None or end is None or start <= 0 or end < start:
        raise FanxiuRuntimeMemoryError(f"活动 {activity_id} 的开启时间不完整")
    return {
        "complete": True,
        "activity_id": int(activity_id),
        "runtime_id": _long_or_int(reader, row.get("id")),
        "start_time_ms": start,
        "end_time_ms": end,
        "evidence": {"pid": memory.pid, "process_start_ticks": memory.process_start_ticks,
                     "read_only": True},
    }


def read_activity_instances_runtime_snapshot() -> dict[str, Any]:
    """Read all instances currently materialized in ActivityData._ActivityInfo.

    This is broader than the world-line list, but is still only loaded client
    state: absence does not prove that an event can never open. No Lua call,
    UI action, config load or persistent mutation occurs. Static names and
    base IDs are deliberately omitted; hot-updated instances may not exist in
    the exported configuration. A period with zero times remains raw evidence,
    rather than being interpreted as an active or expired timed event.
    """
    memory = MumuProcessMemory.discover_cached(fallback_to_discovery=True)
    root, _, _ = _resolve_activity_manager_runtime(
        memory, allow_discovery=False, force_refresh=False
    )
    reader = LuaJitReader(memory)
    data = _activity_data_fields(reader, root)
    if not isinstance(data.get("_ActivityInfo"), LuaRef):
        raise FanxiuRuntimeMemoryError(
            "ActivityData._ActivityInfo 尚未加载", code="data_not_loaded"
        )
    values = reader.dictionary_fields(data["_ActivityInfo"])
    if len(values) > 4096:
        raise FanxiuRuntimeMemoryError(
            "已加载活动实例超过有界解析范围", code="snapshot_incoherent"
        )
    items = []
    identities = set()
    for value in values.values():
        row = reader.fields(value)
        identity = _long_or_int(reader, row.get("id"))
        activity_id = as_int(row.get("activityId"))
        activity_type = as_int(row.get("activityType"))
        state = as_int(row.get("state"))
        if (identity is None or identity <= 0 or activity_id is None
                or activity_id <= 0 or activity_type is None or state is None
                or identity in identities):
            raise FanxiuRuntimeMemoryError(
                "已加载活动实例身份缺失或重复", code="snapshot_incoherent"
            )
        identities.add(identity)
        items.append({
            "id": identity, "activityId": activity_id,
            "activityType": activity_type, "state": state,
            "startTime": _long_or_int(reader, row.get("startTime")),
            "endTime": _long_or_int(reader, row.get("endTime")),
            "closePanelTime": _long_or_int(reader, row.get("closePanelTime")),
        })
    return {
        "complete": True, "scope": "currently_loaded_instances",
        "count": len(items), "items": items,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "evidence": {
            "pid": memory.pid, "process_start_ticks": memory.process_start_ticks,
            "read_only": True,
            "protocol": "LuaActivityMgr.Model.ActivityData._ActivityInfo",
        },
    }
