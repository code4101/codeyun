from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.role_progression import (
    read_role_profile_from_memory,
)
from backend.core.fanxiu.instrumentation.seat_runtime import (
    object_fields,
    room_roster_facts,
    runtime_number,
    self_profile_from_seat,
    self_seat_facts,
)


_UNION_VENIS_MARKER = b"LuaUnionVenisMgr"
_UNION_VENIS_METHODS = frozenset(
    {
        "LuaUnionVenisMgr",
        "Inst_get",
        "ReqMsg",
    }
)


def _data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(
        reader,
        root_address,
        _UNION_VENIS_METHODS,
    )
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("data"))
    required = {
        "myRoomId",
        "leftListenTime",
        "strength",
        "roomList",
        "UnionVeinsGroup",
    }
    if not required.issubset(data):
        raise FanxiuRuntimeMemoryError(
            "联盟灵脉 Runtime 模型尚未初始化",
            code="data_not_loaded",
        )
    return data


def _resolve_union_venis_root(
    memory: MumuProcessMemory,
) -> tuple[int, bool, str]:
    """Resolve the loaded Manager before using constructor-marker discovery."""

    from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
        _lua_addresses,
    )

    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="lingmai-union-venis",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="UnionVenisMgr",
            required_methods=_UNION_VENIS_METHODS,
            validate=lambda reader, root: _data_fields(reader, root),
        )
        return root, cache_hit, "lua_global"
    except FanxiuRuntimeMemoryError as exc:
        # Once the exact Manager is proven, missing Model.data fields mean the
        # page has not naturally loaded yet. A constructor-marker heap scan
        # cannot load them and only turns a precise result into a slow failure.
        if exc.code == "data_not_loaded":
            raise
    root, cache_hit = resolve_manager_root(
        memory,
        manager_key="lingmai-union-venis",
        marker=_UNION_VENIS_MARKER,
        required_methods=_UNION_VENIS_METHODS,
        validate=lambda reader, root: _data_fields(reader, root),
    )
    return root, cache_hit, "constructor_marker"


def _room(reader: LuaJitReader, value: Any) -> dict[str, Any]:
    fields = object_fields(reader, value)
    return {
        "id": as_int(fields.get("id")),
        "available_count": as_int(fields.get("left")),
        "theme_id": as_int(fields.get("themeId")),
        "npc_id": as_int(fields.get("npcId")),
    }


def _role_info(reader: LuaJitReader, value: Any) -> dict[str, Any]:
    fields = object_fields(reader, value)
    return {
        "battle_id": as_int(fields.get("battleId")),
        "room_id": as_int(fields.get("roomId")),
        "seat_id": as_int(fields.get("seatId")),
        "local_server": as_int(fields.get("localServer")),
        "remaining_milliseconds": reader.long(fields.get("leftListenTime")),
        "sit_down_time": reader.long(fields.get("sitDownTime")),
        "skill_level": as_int(fields.get("skillLv")),
    }


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
    manager_resolver: str = "constructor_marker",
) -> dict[str, Any]:
    captured_at_epoch = time.time()
    roster_evidence = {
        "pid": memory.pid,
        "process_start_ticks": memory.process_start_ticks,
        "captured_at_epoch": captured_at_epoch,
        "order_key": [captured_at_epoch],
    }
    reader = LuaJitReader(memory)
    data = _data_fields(reader, root_address)
    raw_rooms, declared_room_count = reader.list_items(data.get("roomList"))
    rooms = [
        room
        for value in raw_rooms
        if (room := _room(reader, value))["id"] is not None
    ]
    remaining_milliseconds = reader.long(data.get("leftListenTime"))
    strength = runtime_number(reader, data.get("strength"))
    union_group = reader.long(data.get("UnionVeinsGroup"))
    own_room_id = as_int(data.get("myRoomId"))
    own_seat_id = as_int(data.get("seatId"))
    self_seat = self_seat_facts(reader, data)
    self_profile = self_profile_from_seat(self_seat)
    if not self_profile.get("available"):
        # Account identity is role state, not seat state.  An unseated player
        # has no self seat by definition, but still needs a role id and battle
        # score to choose an empty seat or a safe opponent.
        self_profile = read_role_profile_from_memory(memory)
    shenmai_roster = room_roster_facts(
        reader,
        data,
        room_id=17,
        room_summaries=rooms,
        evidence=roster_evidence,
    )
    shengmai_roster = room_roster_facts(
        reader,
        data,
        room_id=18,
        room_summaries=rooms,
        evidence=roster_evidence,
    )
    complete = (
        remaining_milliseconds is not None
        and strength is not None
        and union_group is not None
        and bool(rooms)
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "UnionVenisMgr.Model.data",
        "mode": "union",
        "remaining_milliseconds": remaining_milliseconds,
        "completed": (
            remaining_milliseconds <= 0
            if remaining_milliseconds is not None
            else None
        ),
        "strength": strength,
        "fight_consume": as_int(data.get("fightConsume")),
        "sit_consume": as_int(data.get("sitConsume")),
        "initial_strength": as_int(data.get("initialStrength")),
        "maximum_milliseconds": (
            (as_int(data.get("maxVenisTime")) or 0) * 1000
            or None
        ),
        "union_group": union_group,
        "own_room_id": own_room_id,
        "own_seat_id": own_seat_id,
        "battle_id": as_int(data.get("battleId")),
        "local_server": as_int(data.get("localServer")),
        "rooms": rooms,
        "declared_room_count": declared_room_count,
        "decoded_room_count": len(rooms),
        "role_info": _role_info(reader, data.get("roleInfo")),
        "self_seat_facts": self_seat,
        "self_profile": self_profile,
        "union_group_facts": {
            "ok": union_group is not None,
            "available": union_group is not None,
            "veins_group": union_group,
            "source": "runtime_memory",
        },
        "shenmai_roster": shenmai_roster,
        "shengmai_roster": shengmai_roster,
        "room_rosters": {
            "17": shenmai_roster,
            "18": shengmai_roster,
        },
        "captured_at": datetime.fromtimestamp(captured_at_epoch).strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": captured_at_epoch,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
            "manager_resolver": manager_resolver,
            "order_key": [captured_at_epoch],
        },
    }


_TRANSIENT_LINGMAI_RUNTIME_MESSAGES = (
    "Runtime 内存地址越界",
    "读取凡修 Runtime 内存失败",
    "读取凡修 Runtime 内存不完整",
    "Lua table node 数量越界",
    "Lua table array 数量越界",
)


def _is_transient_lingmai_runtime_error(exc: BaseException) -> bool:
    """Whether one same-process full snapshot reread can repair the failure."""

    return isinstance(exc, FanxiuRuntimeMemoryError) and any(
        marker in str(exc) for marker in _TRANSIENT_LINGMAI_RUNTIME_MESSAGES
    )


def _read_lingmai_with_generation_retry(read_once):
    """Reread one volatile Lua generation without crossing process identity."""

    first_identity: tuple[int, int] | None = None
    first_error: str | None = None
    for attempt in range(2):
        # ``discover_cached`` reuses the verified process maps but returns a
        # new reader with an empty read cache.  That is the lightweight fresh
        # generation boundary; forcing a new global/heap scan is unnecessary
        # and can take minutes.
        memory = MumuProcessMemory.discover_cached()
        identity = (int(memory.pid), int(memory.process_start_ticks))
        if first_identity is None:
            first_identity = identity
        elif identity != first_identity:
            raise FanxiuRuntimeMemoryError(
                "灵脉 Runtime 复读期间凡修进程身份已变化，拒绝拼接跨进程快照"
            )
        try:
            result = read_once(memory)
            return result, attempt + 1, first_error
        except FanxiuRuntimeMemoryError as exc:
            if attempt or not _is_transient_lingmai_runtime_error(exc):
                setattr(exc, "lingmai_decode_attempt_count", attempt + 1)
                if first_error is not None:
                    setattr(exc, "lingmai_retried_generation_error", first_error)
                raise
            first_error = str(exc)
    raise AssertionError("unreachable")


def read_lingmai_snapshot() -> dict[str, Any]:
    """Read the current alliance Lingmai model without packets or GUI OCR."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    def read_once(attempt_memory: MumuProcessMemory) -> dict[str, Any]:
        nonlocal memory
        memory = attempt_memory
        root_address, root_cache_hit, manager_resolver = (
            _resolve_union_venis_root(memory)
        )
        return _snapshot(
            memory,
            root_address,
            root_cache_hit=root_cache_hit,
            manager_resolver=manager_resolver,
        )

    try:
        result, attempt_count, retried_error = (
            _read_lingmai_with_generation_retry(read_once)
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        evidence = result.setdefault("evidence", {})
        evidence["decode_attempt_count"] = attempt_count
        if retried_error is not None:
            evidence["retried_generation_error"] = retried_error
        return result
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}"
        )
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "decode_attempt_count": getattr(
                    exc, "lingmai_decode_attempt_count", 1
                ),
                "retried_generation_error": getattr(
                    exc, "lingmai_retried_generation_error", None
                ),
            },
        }
