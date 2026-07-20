from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.packet.decoded_store import list_fanxiu_packet_decoded_records
from backend.core.fanxiu.packet.service_runtime import (
    get_fanxiu_packet_worker_status,
    request_fanxiu_packet_service_catch_up,
    start_fanxiu_packet_service,
)

LUNDAO_SCENE_PROTOCOL_NAME = "SM_SeatsNoInScene"
LUNDAO_SCENE_PROTOCOL_ID = 59518
LUNDAO_SCENE_PROTOCOL_NAMES = ("SM_SeatsNoInScene", "SM_SyncLundaoSceneInfo")
LUNDAO_SCENE_PROTOCOL_IDS = (59518, 59504)
LUNDAO_STATUS_PROTOCOL_NAMES = (
    "SM_RoomList",
    "SM_UpdateLundaoStrength",
    "SM_SyncLundaoRoleInfo",
    "SM_SelfSeat",
)
LUNDAO_STATUS_PROTOCOL_IDS = (59502, 59508, 59514, 59530)
LINGMAI_SCENE_PROTOCOL_NAME = "SM_VeinsSeatsNoInScene"
LINGMAI_SCENE_PROTOCOL_ID = 87216
LINGMAI_SCENE_PROTOCOL_NAMES = (
    LINGMAI_SCENE_PROTOCOL_NAME,
    "SM_UnionVeinsSeatsNoInScene",
)
LINGMAI_SCENE_PROTOCOL_IDS = (LINGMAI_SCENE_PROTOCOL_ID, 93517)
LINGMAI_SELF_SEAT_PROTOCOL_NAME = "SM_VeinsSelfSeat"
LINGMAI_SELF_SEAT_PROTOCOL_ID = 87227
LINGMAI_SELF_SEAT_PROTOCOL_NAMES = (
    LINGMAI_SELF_SEAT_PROTOCOL_NAME,
    "SM_UnionVeinsSelfSeat",
)
LINGMAI_SELF_SEAT_PROTOCOL_IDS = (LINGMAI_SELF_SEAT_PROTOCOL_ID, 93528)
FAZE_SHOW_PROTOCOL_NAMES = ("SM_FazeShow", "SM_ShowFazePanel")
FAZE_SHOW_PROTOCOL_IDS = (34001, 34003)
_CAPTURE_NAME_TIME_RE = re.compile(r"(?P<date>\d{8})_(?P<time>\d{6})")


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _seat_owner_fact(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    # Only expose fields confirmed by SeatOwnerVO.  In particular, keep the
    # nested HangPointVO out of this current-fact API: it is appearance data,
    # may itself be truncated by the generic decoded cache, and is unrelated
    # to choosing a lundao seat.
    return {
        "role_id": _int_or_none(value.get("roleId")),
        "name": str(value.get("name") or ""),
        "level": _int_or_none(value.get("level")),
        "model": _int_or_none(value.get("model")),
        "avatar": _int_or_none(value.get("avatar")),
        "head_frame": _int_or_none(value.get("headFrame")),
        "sex": _int_or_none(value.get("sex")),
        "faze": _int_or_none(value.get("faze")),
        "title_id": _int_or_none(value.get("titleId")),
        "title_name": str(value.get("titleName") or ""),
        "alliance_id": _int_or_none(value.get("allianceId")),
        "sit_down_time": _int_or_none(value.get("sitDownTime")),
        "protect_end_time": _int_or_none(value.get("protectEndTime")),
        "left_listen_time": _int_or_none(value.get("leftListenTime")),
        "battle_score": value.get("battleScore") if isinstance(value.get("battleScore"), (int, float)) else None,
        "server_id": _int_or_none(value.get("serverId")),
        "ganwu_start_time": _int_or_none(value.get("ganwuStartTime")),
    }


def _decoded_record_capture_sort_key(record: dict[str, Any]) -> tuple[float, int, int, int, str, float]:
    """Prefer capture-segment time over the time an old backlog row was inserted."""
    match = _CAPTURE_NAME_TIME_RE.search(str(record.get("pcap_name") or ""))
    capture_epoch = 0.0
    if match:
        try:
            capture_epoch = datetime.strptime(
                f"{match.group('date')}_{match.group('time')}",
                "%Y%m%d_%H%M%S",
            ).timestamp()
        except ValueError:
            pass
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    frame_index = _int_or_none(
        record.get("frame_index") if record.get("frame_index") is not None else evidence.get("frame_index")
    ) or 0
    offset = _int_or_none(record.get("offset") if record.get("offset") is not None else evidence.get("offset")) or 0
    sn = _int_or_none(record.get("sn") if record.get("sn") is not None else evidence.get("sn")) or 0
    return (
        capture_epoch,
        frame_index,
        offset,
        sn,
        str(record.get("captured_at") or ""),
        float(record.get("updated_at") or 0.0),
    )


def fanxiu_packet_record_order_key(record: dict[str, Any]) -> tuple[float, int, int, int, str, float]:
    """Public comparable order key for freshness checks on decoded game events."""

    return _decoded_record_capture_sort_key(record)


def _record_evidence(record: dict[str, Any]) -> dict[str, Any]:
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    return {
        "packet_id": str(record.get("packet_id") or evidence.get("packet_id") or ""),
        "record_id": str(record.get("record_id") or evidence.get("record_id") or ""),
        "pcap_name": str(record.get("pcap_name") or evidence.get("pcap_name") or ""),
        "frame_index": _int_or_none(
            record.get("frame_index") if record.get("frame_index") is not None else evidence.get("frame_index")
        ),
        "offset": _int_or_none(record.get("offset") if record.get("offset") is not None else evidence.get("offset")),
        "sn": _int_or_none(record.get("sn") if record.get("sn") is not None else evidence.get("sn")),
        "captured_at": str(record.get("captured_at") or ""),
        "order_key": list(_decoded_record_capture_sort_key(record)),
    }


def _normalize_fanxiu_scene_seat_facts(
    record: dict[str, Any],
    *,
    default_protocol_name: str,
    default_protocol_id: int,
) -> dict[str, Any]:
    """Normalize one decoded seat packet into a traceable snapshot.

    The generic decoded cache deliberately trims large lists.  Therefore this
    function always reports declared_count and decoded_count independently and
    never presents a truncated sample as the complete room roster.
    """
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    parsed = payload.get("parsed") if isinstance(payload.get("parsed"), dict) else {}
    room_vo = parsed.get("roomVO") if isinstance(parsed.get("roomVO"), dict) else {}
    seats_value = parsed.get("seats")
    seats_container = seats_value if isinstance(seats_value, dict) else {}
    raw_items = seats_container.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    seats: list[dict[str, Any]] = []
    for source_index, value in enumerate(items):
        if not isinstance(value, dict):
            continue
        seats.append(
            {
                "source_index": source_index,
                "seat_id": _int_or_none(value.get("id")),
                "pre_owner_role_id": _int_or_none(value.get("preOwnerRoleId")),
                "owner": _seat_owner_fact(value.get("seatOwner")),
            }
        )

    declared_count = _int_or_none(seats_container.get("_count"))
    decoded_count = len(seats)
    explicit_truncated_count = _int_or_none(seats_container.get("_truncated_items")) or 0
    complete = (
        isinstance(seats_value, (dict, list))
        and explicit_truncated_count == 0
        and (declared_count is None or declared_count == decoded_count)
    )
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    return {
        "ok": bool(parsed and not payload.get("parse_error") and not record.get("decode_error")),
        "available": bool(parsed),
        "protocol": str(record.get("name") or payload.get("name") or default_protocol_name),
        "pro_id": _int_or_none(record.get("pro_id") or payload.get("pro_id")) or default_protocol_id,
        "captured_at": str(record.get("captured_at") or ""),
        "room_id": _int_or_none(parsed.get("roomId") if parsed.get("roomId") is not None else room_vo.get("id")),
        "npc_id": _int_or_none(parsed.get("npcId") if parsed.get("npcId") is not None else room_vo.get("npcId")),
        "theme_id": _int_or_none(parsed.get("themeId") if parsed.get("themeId") is not None else room_vo.get("themeId")),
        "available_count": _int_or_none(room_vo.get("left")),
        "capacity": (
            _int_or_none(room_vo.get("left")) + declared_count
            if _int_or_none(room_vo.get("left")) is not None and declared_count is not None
            else None
        ),
        "seat_type_id": _int_or_none(seats_container.get("_type_id")),
        "seat_type": str(seats_container.get("_type") or ""),
        "declared_count": declared_count,
        "decoded_count": decoded_count,
        "truncated_count": max(explicit_truncated_count, (declared_count or 0) - decoded_count),
        "complete": complete,
        "seats": seats,
        "decode": {
            "decode_error": str(record.get("decode_error") or ""),
            "parse_error": str(payload.get("parse_error") or ""),
            "parsed_bytes": _int_or_none(payload.get("parsed_bytes")),
            "remain": _int_or_none(payload.get("remain")),
        },
        "evidence": {
            "packet_id": str(record.get("packet_id") or evidence.get("packet_id") or ""),
            "record_id": str(record.get("record_id") or evidence.get("record_id") or ""),
            "pcap_name": str(record.get("pcap_name") or evidence.get("pcap_name") or ""),
            "capture_sha256": str(record.get("capture_sha256") or evidence.get("capture_sha256") or ""),
            "stream": _int_or_none(record.get("stream") if record.get("stream") is not None else evidence.get("stream")),
            "direction": str(record.get("direction") or evidence.get("direction") or ""),
            "frame_index": _int_or_none(record.get("frame_index") if record.get("frame_index") is not None else evidence.get("frame_index")),
            "offset": _int_or_none(record.get("offset") if record.get("offset") is not None else evidence.get("offset")),
            "sn": _int_or_none(record.get("sn") if record.get("sn") is not None else evidence.get("sn")),
            "decoded_path": str(evidence.get("decoded_path") or ""),
            "stored_pcap": str(evidence.get("stored_pcap") or ""),
            "order_key": list(_decoded_record_capture_sort_key(record)),
        },
    }


def normalize_fanxiu_lundao_scene_seat_facts(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize one decoded lundao seat packet into a traceable snapshot."""

    return _normalize_fanxiu_scene_seat_facts(
        record,
        default_protocol_name=LUNDAO_SCENE_PROTOCOL_NAME,
        default_protocol_id=LUNDAO_SCENE_PROTOCOL_ID,
    )


def normalize_fanxiu_lingmai_scene_seat_facts(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize one decoded lingmai room roster into the shared seat snapshot."""

    return _normalize_fanxiu_scene_seat_facts(
        record,
        default_protocol_name=LINGMAI_SCENE_PROTOCOL_NAME,
        default_protocol_id=LINGMAI_SCENE_PROTOCOL_ID,
    )


def normalize_fanxiu_lingmai_self_seat_facts(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize the dedicated response that says whether this role has a seat."""

    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    parsed = payload.get("parsed") if isinstance(payload.get("parsed"), dict) else {}
    seat_vo = parsed.get("seatVO") if isinstance(parsed.get("seatVO"), dict) else None
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    seat = None
    if seat_vo is not None:
        seat = {
            "seat_id": _int_or_none(seat_vo.get("id")),
            "pre_owner_role_id": _int_or_none(seat_vo.get("preOwnerRoleId")),
            "owner": _seat_owner_fact(seat_vo.get("seatOwner")),
        }
    return {
        "ok": bool(parsed and not payload.get("parse_error") and not record.get("decode_error")),
        "available": bool(parsed),
        "seated": bool(seat and seat.get("seat_id") is not None and seat.get("owner")),
        "protocol": str(record.get("name") or payload.get("name") or LINGMAI_SELF_SEAT_PROTOCOL_NAME),
        "pro_id": _int_or_none(record.get("pro_id") or payload.get("pro_id")) or LINGMAI_SELF_SEAT_PROTOCOL_ID,
        "captured_at": str(record.get("captured_at") or ""),
        "seat": seat,
        "evidence": {
            "packet_id": str(record.get("packet_id") or evidence.get("packet_id") or ""),
            "pcap_name": str(record.get("pcap_name") or evidence.get("pcap_name") or ""),
            "decoded_path": str(evidence.get("decoded_path") or ""),
        },
    }


def get_latest_fanxiu_lundao_scene_seat_facts(
    session: Session,
    *,
    since_seconds: int | None = None,
) -> dict[str, Any]:
    """Read the latest already-decoded lundao room snapshot without side effects."""
    result = list_fanxiu_packet_decoded_records(
        session,
        names=list(LUNDAO_SCENE_PROTOCOL_NAMES),
        pro_ids=list(LUNDAO_SCENE_PROTOCOL_IDS),
        since_seconds=since_seconds,
        # Backlog maintenance can insert an old pcap today, so SQL order by
        # captured_at/updated_at is not sufficient to find the newest game
        # event.  Fetch the bounded per-protocol cache and sort by pcap name.
        limit=500,
    )
    records = result.get("records") if isinstance(result.get("records"), list) else []
    if not records:
        return {
            "ok": True,
            "available": False,
            "protocol": LUNDAO_SCENE_PROTOCOL_NAME,
            "pro_id": LUNDAO_SCENE_PROTOCOL_ID,
            "reason": "no_decoded_record",
            "declared_count": None,
            "decoded_count": 0,
            "truncated_count": 0,
            "complete": False,
            "seats": [],
            "evidence": {},
        }
    return normalize_fanxiu_lundao_scene_seat_facts(max(records, key=_decoded_record_capture_sort_key))


def _decoded_record_parsed(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    return payload.get("parsed") if isinstance(payload.get("parsed"), dict) else {}


def _lundao_rooms_fact(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    value = parsed.get("rooms")
    if isinstance(value, dict):
        items = value.get("items") if isinstance(value.get("items"), list) else []
    elif isinstance(value, list):
        items = value
    else:
        items = []
    rooms: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        room_id = _int_or_none(item.get("id"))
        if room_id is None:
            continue
        rooms.append(
            {
                "room_id": room_id,
                "available_count": _int_or_none(item.get("left")),
                "npc_id": _int_or_none(item.get("npcId")),
                "theme_id": _int_or_none(item.get("themeId")),
            }
        )
    return rooms


def get_latest_fanxiu_lundao_status_facts(
    session: Session,
    *,
    since_seconds: int | None = None,
) -> dict[str, Any]:
    """Aggregate the newest authoritative lundao status fields without side effects.

    Different status fields arrive in separate protocols.  They are selected by
    capture segment and frame order, not by database insertion time, so a late
    backlog insert cannot roll the live state backwards.
    """

    result = list_fanxiu_packet_decoded_records(
        session,
        names=list(LUNDAO_STATUS_PROTOCOL_NAMES),
        pro_ids=list(LUNDAO_STATUS_PROTOCOL_IDS),
        since_seconds=since_seconds,
        limit=500,
    )
    records = result.get("records") if isinstance(result.get("records"), list) else []
    usable = [record for record in records if _decoded_record_parsed(record)]
    if not usable:
        return {
            "ok": True,
            "available": False,
            "reason": "no_decoded_record",
            "strength": None,
            "room_id": None,
            "seat_id": None,
            "seated": False,
            "left_listen_time": None,
            "sit_down_time": None,
            "ganwu_start_time": None,
            "rooms": [],
            "room_available_counts": {},
            "evidence": {},
        }

    def latest(names: set[str]) -> dict[str, Any] | None:
        candidates = [record for record in usable if str(record.get("name") or "") in names]
        return max(candidates, key=_decoded_record_capture_sort_key) if candidates else None

    room_list_record = latest({"SM_RoomList"})
    strength_record = latest({"SM_RoomList", "SM_UpdateLundaoStrength"})
    role_record = latest({"SM_RoomList", "SM_SyncLundaoRoleInfo"})
    self_seat_record = latest({"SM_SelfSeat"})

    room_list_parsed = _decoded_record_parsed(room_list_record or {})
    strength_parsed = _decoded_record_parsed(strength_record or {})
    role_parsed = _decoded_record_parsed(role_record or {})
    self_seat_parsed = _decoded_record_parsed(self_seat_record or {})
    rooms = _lundao_rooms_fact(room_list_parsed)

    room_id = _int_or_none(role_parsed.get("roomId"))
    seat_id = _int_or_none(role_parsed.get("seatId"))
    seat_vo = self_seat_parsed.get("seatVO") if isinstance(self_seat_parsed.get("seatVO"), dict) else None
    if seat_id is None and seat_vo is not None:
        seat_id = _int_or_none(seat_vo.get("id"))
    if room_id == 0:
        room_id = None
    if seat_id == 0:
        seat_id = None

    evidence: dict[str, Any] = {}
    for key, record in (
        ("room_list", room_list_record),
        ("strength", strength_record),
        ("role", role_record),
        ("self_seat", self_seat_record),
    ):
        if record is not None:
            evidence[key] = _record_evidence(record)

    sit_down_time = _int_or_none(role_parsed.get("sitDownTime"))
    return {
        "ok": True,
        "available": True,
        "strength": _int_or_none(strength_parsed.get("strength")),
        "room_id": room_id,
        "seat_id": seat_id,
        # SM_RoomList is authoritative for the current room but does not carry
        # seatId.  A positive roomId plus sitDownTime still means the player is
        # seated; retain seat_id=None instead of misclassifying the state.
        "seated": room_id is not None and (seat_id is not None or bool(sit_down_time)),
        "left_listen_time": _int_or_none(role_parsed.get("leftListenTime")),
        "sit_down_time": sit_down_time,
        "ganwu_start_time": _int_or_none(role_parsed.get("ganwuStartTime")),
        "rooms": rooms,
        "room_available_counts": {
            str(room["room_id"]): room.get("available_count")
            for room in rooms
        },
        "evidence": evidence,
    }


def get_latest_fanxiu_lundao_kick_transition_facts(
    session: Session,
    *,
    since_seconds: int = 86400,
) -> dict[str, Any]:
    """Detect the newest seated -> unseated server transition from packet history."""

    result = list_fanxiu_packet_decoded_records(
        session,
        names=["SM_SyncLundaoRoleInfo"],
        pro_ids=[59514],
        since_seconds=max(60, int(since_seconds)),
        limit=500,
    )
    records = [record for record in result.get("records") or [] if _decoded_record_parsed(record)]
    if not records:
        return {"ok": True, "available": False, "kicked": False, "reason": "no_role_record"}
    records.sort(key=_decoded_record_capture_sort_key, reverse=True)
    latest = records[0]
    latest_parsed = _decoded_record_parsed(latest)
    latest_state = (
        _int_or_none(latest_parsed.get("roomId")) or 0,
        _int_or_none(latest_parsed.get("seatId")) or 0,
    )
    previous = next(
        (
            record
            for record in records[1:]
            if (
                _int_or_none(_decoded_record_parsed(record).get("roomId")) or 0,
                _int_or_none(_decoded_record_parsed(record).get("seatId")) or 0,
            )
            != latest_state
        ),
        None,
    )
    previous_parsed = _decoded_record_parsed(previous or {})
    kicked = bool(
        latest_state == (0, 0)
        and (_int_or_none(previous_parsed.get("roomId")) or 0) > 0
        and (_int_or_none(latest_parsed.get("leftListenTime")) or 0) > 0
    )
    order_key = _decoded_record_capture_sort_key(latest)
    event_at = datetime.fromtimestamp(order_key[0]).strftime("%Y-%m-%d %H:%M:%S") if order_key[0] else ""
    return {
        "ok": True,
        "available": True,
        "kicked": kicked,
        "room_id": latest_state[0] or None,
        "seat_id": latest_state[1] or None,
        "left_listen_time": _int_or_none(latest_parsed.get("leftListenTime")),
        "event_at": event_at,
        "evidence": _record_evidence(latest),
        "previous_evidence": _record_evidence(previous) if previous is not None else {},
    }


def get_latest_fanxiu_lingmai_scene_seat_facts(
    session: Session,
    *,
    since_seconds: int | None = None,
) -> dict[str, Any]:
    """Read the latest already-decoded lingmai room roster without side effects."""

    result = list_fanxiu_packet_decoded_records(
        session,
        names=list(LINGMAI_SCENE_PROTOCOL_NAMES),
        pro_ids=list(LINGMAI_SCENE_PROTOCOL_IDS),
        since_seconds=since_seconds,
        limit=500,
    )
    records = result.get("records") if isinstance(result.get("records"), list) else []
    if not records:
        return {
            "ok": True,
            "available": False,
            "protocol": LINGMAI_SCENE_PROTOCOL_NAME,
            "pro_id": LINGMAI_SCENE_PROTOCOL_ID,
            "reason": "no_decoded_record",
            "room_id": None,
            "available_count": None,
            "capacity": None,
            "declared_count": None,
            "decoded_count": 0,
            "truncated_count": 0,
            "complete": False,
            "seats": [],
            "evidence": {},
        }
    return normalize_fanxiu_lingmai_scene_seat_facts(max(records, key=_decoded_record_capture_sort_key))


def get_latest_fanxiu_lingmai_self_seat_facts(
    session: Session,
    *,
    since_seconds: int | None = None,
) -> dict[str, Any]:
    """Read the latest dedicated self-seat response without side effects."""

    result = list_fanxiu_packet_decoded_records(
        session,
        names=list(LINGMAI_SELF_SEAT_PROTOCOL_NAMES),
        pro_ids=list(LINGMAI_SELF_SEAT_PROTOCOL_IDS),
        since_seconds=since_seconds,
        limit=500,
    )
    records = result.get("records") if isinstance(result.get("records"), list) else []
    if not records:
        return {
            "ok": True,
            "available": False,
            "seated": False,
            "protocol": LINGMAI_SELF_SEAT_PROTOCOL_NAME,
            "pro_id": LINGMAI_SELF_SEAT_PROTOCOL_ID,
            "reason": "no_decoded_record",
            "seat": None,
            "evidence": {},
        }
    return normalize_fanxiu_lingmai_self_seat_facts(max(records, key=_decoded_record_capture_sort_key))


def get_latest_fanxiu_faze_show_facts(session: Session) -> dict[str, Any]:
    """Read the latest equipped/displayed law id known to the game client."""

    result = list_fanxiu_packet_decoded_records(
        session,
        names=list(FAZE_SHOW_PROTOCOL_NAMES),
        pro_ids=list(FAZE_SHOW_PROTOCOL_IDS),
        limit=500,
    )
    records = result.get("records") if isinstance(result.get("records"), list) else []
    if not records:
        return {
            "ok": True,
            "available": False,
            "reason": "no_decoded_record",
            "faze_id": None,
            "evidence": {},
        }
    record = max(records, key=_decoded_record_capture_sort_key)
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    parsed = payload.get("parsed") if isinstance(payload.get("parsed"), dict) else {}
    name = str(record.get("name") or payload.get("name") or "")
    faze_id = _int_or_none(parsed.get("fazeResId") if name == "SM_FazeShow" else parsed.get("showId"))
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    return {
        "ok": faze_id is not None and not record.get("decode_error") and not payload.get("parse_error"),
        "available": faze_id is not None,
        "protocol": name,
        "pro_id": _int_or_none(record.get("pro_id") or payload.get("pro_id")),
        "captured_at": str(record.get("captured_at") or ""),
        "faze_id": faze_id,
        "evidence": {
            "packet_id": str(record.get("packet_id") or evidence.get("packet_id") or ""),
            "pcap_name": str(record.get("pcap_name") or evidence.get("pcap_name") or ""),
            "decoded_path": str(evidence.get("decoded_path") or ""),
        },
    }


def catch_up_and_list_fanxiu_packet_decoded_records(
    session: Session,
    *,
    names: list[str] | None = None,
    pro_ids: list[int] | None = None,
    since_seconds: int | None = None,
    limit: int = 50,
    reason: str = "decoded-records-api",
    wait_seconds: float = 30.0,
) -> dict[str, Any]:
    start_result = start_fanxiu_packet_service()
    catch_up_result = request_fanxiu_packet_service_catch_up(
        reason=reason,
        wait_seconds=max(120.0, float(wait_seconds)),
    )
    decoded_records = list_fanxiu_packet_decoded_records(
        session,
        names=names,
        pro_ids=pro_ids,
        since_seconds=since_seconds,
        limit=limit,
    )
    return {
        "ok": bool(catch_up_result.get("ok", True) and decoded_records.get("ok", True)),
        "status": catch_up_result.get("status") or "pending",
        "action": "packet-facts-catch-up-and-query-decoded-records",
        "start_result": start_result,
        "catch_up": catch_up_result,
        "decoded_records": decoded_records,
        "worker": get_fanxiu_packet_worker_status(),
    }
