from __future__ import annotations

from typing import Any, Mapping

from backend.core.fanxiu.instrumentation.runtime_memory import (
    LuaJitReader,
    LuaRef,
    as_int,
)


def object_fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    """Return one Lua object with inherited fields flattened."""

    fields = dict(reader.fields(value))
    inherited = fields.get("_super")
    seen: set[int] = set()
    while isinstance(inherited, LuaRef) and inherited.kind == "table":
        if inherited.address in seen:
            break
        seen.add(inherited.address)
        parent = dict(reader.fields(inherited))
        fields = {**parent, **fields}
        inherited = parent.get("_super")
    return fields


def runtime_number(reader: LuaJitReader, value: Any) -> int | float | None:
    long_value = reader.long(value)
    if long_value is not None:
        return long_value
    direct = as_int(value)
    if direct is not None:
        return direct
    return value if isinstance(value, float) else None


_ATTACK_ATTRIBUTE_KEYS = {2001, "2001", "ATTACK", "attack"}
_ATTACK_DIRECT_FIELDS = (
    "attack",
    "attackValue",
    "attack_value",
    "ATTACK",
)
_ATTRIBUTE_MAP_FIELDS = (
    "attributes",
    "attrs",
    "attributeMap",
    "attrMap",
)


def _positive_runtime_number(
    reader: LuaJitReader,
    value: Any,
) -> int | float | None:
    number = runtime_number(reader, value)
    if number is None or number <= 0:
        return None
    return number


def _attribute_entry_number(
    reader: LuaJitReader,
    value: Any,
) -> int | float | None:
    direct = _positive_runtime_number(reader, value)
    if direct is not None:
        return direct
    fields = object_fields(reader, value)
    for field in ("value", "attrValue", "finalValue", "num"):
        number = _positive_runtime_number(reader, fields.get(field))
        if number is not None:
            return number
    return None


def runtime_attack_fact(
    reader: LuaJitReader,
    fields: dict[Any, Any],
) -> tuple[int | float | None, str | None]:
    """Best-effort read of an explicitly identified attack attribute.

    Runtime business models frequently include ``battleScore`` but omit the
    full role attribute map.  This helper deliberately returns ``None`` when
    no exact attack field/key exists; battle score is never converted into an
    estimated attack value.
    """

    for field in _ATTACK_DIRECT_FIELDS:
        number = _positive_runtime_number(reader, fields.get(field))
        if number is not None:
            return number, field

    dictionary_reader = getattr(reader, "dictionary_fields", None)
    if not callable(dictionary_reader):
        return None, None
    for field in _ATTRIBUTE_MAP_FIELDS:
        raw_map = fields.get(field)
        if raw_map is None:
            continue
        attributes = dictionary_reader(raw_map)
        if not isinstance(attributes, dict):
            continue
        for key, value in attributes.items():
            normalized_key = str(key).strip() if isinstance(key, str) else key
            if normalized_key not in _ATTACK_ATTRIBUTE_KEYS:
                continue
            number = _attribute_entry_number(reader, value)
            if number is not None:
                return number, f"{field}[{key}]"
    return None, None


def seat_owner_fact(reader: LuaJitReader, value: Any) -> dict[str, Any] | None:
    fields = object_fields(reader, value)
    if not fields:
        return None
    attack_value, attack_source = runtime_attack_fact(reader, fields)
    return {
        "role_id": reader.long(fields.get("roleId")),
        "name": str(fields.get("name") or ""),
        "level": as_int(fields.get("level")),
        "model": as_int(fields.get("model")),
        "avatar": as_int(fields.get("avatar")),
        "head_frame": as_int(fields.get("headFrame")),
        "sex": as_int(fields.get("sex")),
        "faze": as_int(fields.get("faze")),
        "title_id": as_int(fields.get("titleId")),
        "title_name": str(fields.get("titleName") or ""),
        "alliance_id": reader.long(fields.get("allianceId")),
        "team_uid": reader.long(fields.get("teamUid")),
        "team_name": str(fields.get("teamName") or ""),
        "sit_down_time": reader.long(fields.get("sitDownTime")),
        "protect_end_time": reader.long(fields.get("protectEndTime")),
        "left_listen_time": reader.long(fields.get("leftListenTime")),
        "battle_score": runtime_number(reader, fields.get("battleScore")),
        "attack_value": attack_value,
        "attack_source": attack_source,
        "server_id": as_int(fields.get("serverId")),
        "ganwu_start_time": reader.long(fields.get("ganwuStartTime")),
    }


def seat_fact(
    reader: LuaJitReader,
    value: Any,
    *,
    source_index: int | None = None,
) -> dict[str, Any] | None:
    fields = object_fields(reader, value)
    seat_id = as_int(fields.get("id"))
    if seat_id is None:
        return None
    return {
        "source_index": source_index,
        "seat_id": seat_id,
        "pre_owner_role_id": reader.long(fields.get("preOwnerRoleId")),
        "owner": seat_owner_fact(reader, fields.get("seatOwner")),
    }


def self_seat_facts(
    reader: LuaJitReader,
    data: dict[Any, Any],
) -> dict[str, Any]:
    room_id = as_int(data.get("myRoomId"))
    seat_id = as_int(data.get("seatId"))
    room_id = None if room_id in {None, 0} else room_id
    seat_id = None if seat_id in {None, 0} else seat_id
    seat = seat_fact(reader, data.get("mySeatVO"))
    seated = room_id is not None or seat_id is not None or seat is not None
    coherent = not seated or (
        seat is not None
        and seat.get("seat_id") is not None
        and isinstance(seat.get("owner"), dict)
    )
    return {
        "ok": coherent,
        "available": coherent,
        "seated": seated,
        "room_id": room_id,
        "seat_id": seat_id or (seat.get("seat_id") if seat else None),
        "seat": seat,
        "source": "runtime_memory",
        "reason": None if coherent else "self_seat_cache_incomplete",
    }


def room_roster_facts(
    reader: LuaJitReader,
    data: dict[Any, Any],
    *,
    room_id: int,
    room_summaries: list[dict[str, Any]],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    observation_evidence = dict(evidence)
    if not observation_evidence.get("order_key"):
        raise ValueError("room roster observation evidence requires order_key")
    summary = next(
        (
            item
            for item in room_summaries
            if as_int(item.get("room_id") if "room_id" in item else item.get("id"))
            == int(room_id)
        ),
        {},
    )
    available_count = as_int(summary.get("available_count"))
    dictionary_reader = getattr(reader, "dictionary_fields", None)
    room_infos = (
        dictionary_reader(data.get("roomInfoDic"))
        if callable(dictionary_reader)
        else {}
    )
    info_value = next(
        (
            value
            for key, value in room_infos.items()
            if as_int(key) == int(room_id)
        ),
        None,
    )
    if info_value is None:
        return {
            "ok": True,
            "available": False,
            "complete": False,
            "room_id": int(room_id),
            "available_count": available_count,
            "declared_count": None,
            "decoded_count": 0,
            "seats": [],
            "source": "runtime_memory",
            "reason": "room_roster_not_loaded",
            "evidence": observation_evidence,
        }

    info = object_fields(reader, info_value)
    room_vo = object_fields(reader, info.get("roomVO"))
    available_count = (
        as_int(room_vo.get("left"))
        if as_int(room_vo.get("left")) is not None
        else available_count
    )
    raw_seats, declared_count = reader.list_items(info.get("seats"))
    seats = [
        seat
        for index, value in enumerate(raw_seats)
        if (seat := seat_fact(reader, value, source_index=index)) is not None
    ]
    decoded_count = len(seats)
    list_complete = (
        declared_count is not None and declared_count == decoded_count
    )
    # The client clears detailed room lists when leaving their page. A full room
    # with zero cached occupants is therefore "not loaded", never an empty fact.
    loaded = bool(decoded_count) or (
        available_count is not None and available_count > 0
    )
    complete = loaded and list_complete
    return {
        "ok": complete,
        "available": loaded,
        "complete": complete,
        "room_id": int(room_id),
        "npc_id": as_int(room_vo.get("npcId")) or as_int(summary.get("npc_id")),
        "theme_id": as_int(room_vo.get("themeId"))
        or as_int(summary.get("theme_id")),
        "available_count": available_count,
        "capacity": (
            available_count + declared_count
            if available_count is not None and declared_count is not None
            else None
        ),
        "declared_count": declared_count,
        "decoded_count": decoded_count,
        "truncated_count": max(0, (declared_count or 0) - decoded_count),
        "seats": seats,
        "source": "runtime_memory",
        "reason": None if complete else "room_roster_not_loaded",
        "evidence": observation_evidence,
    }


def self_profile_from_seat(
    self_seat: dict[str, Any],
) -> dict[str, Any]:
    seat = (
        self_seat.get("seat")
        if isinstance(self_seat.get("seat"), dict)
        else {}
    )
    owner = seat.get("owner") if isinstance(seat.get("owner"), dict) else {}
    role_id = owner.get("role_id")
    battle_score = owner.get("battle_score")
    available = role_id is not None and isinstance(battle_score, (int, float))
    return {
        "ok": available,
        "available": available,
        **owner,
        "source": "runtime_memory",
        "reason": None if available else "self_profile_not_loaded",
    }
