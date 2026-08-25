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
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.role_progression import (
    read_role_profile_from_memory,
)
from backend.core.fanxiu.instrumentation.seat_runtime import (
    object_fields,
    room_roster_facts,
    self_profile_from_seat,
    self_seat_facts,
)


_LUNDAO_MARKER = b"LuaLundaoMgr"
_LUNDAO_METHODS = frozenset(
    {
        "LuaLundaoMgr",
        "GetCurLeftListenTime",
        "Inst_get",
    }
)


def _data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _LUNDAO_METHODS)
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("data"))
    required = {
        "leftListenTime",
        "maxLunDaoTime",
        "strength",
        "myRoomId",
        "seatId",
        "roomList",
        "roleInfo",
    }
    if not required.issubset(data):
        raise FanxiuRuntimeMemoryError("论道 Runtime 模型尚未初始化")
    return data


def _room(reader: LuaJitReader, value: Any) -> dict[str, Any]:
    fields = object_fields(reader, value)
    return {
        "room_id": as_int(fields.get("id")),
        "available_count": as_int(fields.get("left")),
        "theme_id": as_int(fields.get("themeId")),
        "npc_id": as_int(fields.get("npcId")),
    }


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
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
    role = object_fields(reader, data.get("roleInfo"))
    raw_rooms, declared_room_count = reader.list_items(data.get("roomList"))
    rooms = [
        room
        for value in raw_rooms
        if (room := _room(reader, value))["room_id"] is not None
    ]
    room_id = as_int(role.get("roomId"))
    if room_id in {None, 0}:
        room_id = as_int(data.get("myRoomId"))
    room_id = None if room_id == 0 else room_id
    seat_id = as_int(role.get("seatId"))
    if seat_id in {None, 0}:
        seat_id = as_int(data.get("seatId"))
    seat_id = None if seat_id == 0 else seat_id
    sit_down_time = reader.long(role.get("sitDownTime"))
    seated = room_id is not None and (
        seat_id is not None or bool(sit_down_time)
    )
    remaining_milliseconds = reader.long(role.get("leftListenTime"))
    if remaining_milliseconds is None:
        remaining_milliseconds = reader.long(data.get("leftListenTime"))
    current_remaining_milliseconds = remaining_milliseconds
    if (
        current_remaining_milliseconds is not None
        and current_remaining_milliseconds > 0
        and seated
        and sit_down_time is not None
        and sit_down_time > 0
    ):
        elapsed_milliseconds = max(0, int(captured_at_epoch * 1000) - sit_down_time)
        current_remaining_milliseconds = max(
            0,
            current_remaining_milliseconds - elapsed_milliseconds,
        )
    strength = as_int(data.get("strength"))
    maximum_seconds = as_int(data.get("maxLunDaoTime"))
    self_seat = self_seat_facts(reader, data)
    self_profile = self_profile_from_seat(self_seat)
    if not self_profile.get("available"):
        self_profile = read_role_profile_from_memory(memory)
    daluo_roster = room_roster_facts(
        reader,
        data,
        room_id=15,
        room_summaries=rooms,
        evidence=roster_evidence,
    )
    sanqing_roster = room_roster_facts(
        reader,
        data,
        room_id=14,
        room_summaries=rooms,
        evidence=roster_evidence,
    )
    complete = (
        remaining_milliseconds is not None
        and strength is not None
        and maximum_seconds is not None
        and bool(rooms)
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "LundaoMgr.Model.data",
        "remaining_milliseconds": remaining_milliseconds,
        "left_listen_time": remaining_milliseconds,
        "current_left_listen_time": current_remaining_milliseconds,
        "completed": (
            current_remaining_milliseconds <= 0
            if current_remaining_milliseconds is not None
            else None
        ),
        "maximum_milliseconds": (
            maximum_seconds * 1000
            if maximum_seconds is not None
            else None
        ),
        "strength": strength,
        "room_id": room_id,
        "seat_id": seat_id,
        "seated": seated,
        "sit_down_time": sit_down_time,
        "rooms": rooms,
        "room_available_counts": {
            str(room["room_id"]): room["available_count"]
            for room in rooms
        },
        "declared_room_count": declared_room_count,
        "decoded_room_count": len(rooms),
        "self_seat_facts": self_seat,
        "self_profile": self_profile,
        "daluo_roster": daluo_roster,
        "sanqing_roster": sanqing_roster,
        "captured_at": datetime.fromtimestamp(captured_at_epoch).strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": captured_at_epoch,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
            "order_key": [captured_at_epoch],
        },
    }


def read_lundao_snapshot() -> dict[str, Any]:
    """Read the current Lundao state without packets or GUI evidence."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover()
        root, root_cache_hit = resolve_manager_root(
            memory,
            manager_key="lundao",
            marker=_LUNDAO_MARKER,
            required_methods=_LUNDAO_METHODS,
            validate=lambda reader, address: _data_fields(reader, address),
        )
        result = _snapshot(
            memory,
            root,
            root_cache_hit=root_cache_hit,
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
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
            },
        }
