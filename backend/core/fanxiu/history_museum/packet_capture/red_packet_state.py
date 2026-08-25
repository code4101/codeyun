from __future__ import annotations

import time
from copy import deepcopy
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Iterable

from sqlalchemy import func
from sqlmodel import Session, select

from backend.db import engine
from backend.models import FanxiuPacketBusinessRecord, FanxiuPacketDecodedRecord


RED_PACKET_PROTOCOLS = frozenset(
    {
        "SM_NewRedBag",
        "SM_OfflineRedBag",
        "SM_UpdateRedBagList",
        "SM_UpdateRedBag",
        "SM_GrabRedBag",
        "SM_RedBagDetail",
        "SM_RedBagStat",
    }
)
RED_PACKET_EVENT_LOOKBACK_DAYS = 7
RED_PACKET_PROJECTION_DOMAIN = "red_packet_projection"

_projection_cache_lock = Lock()
_projection_cache_signature: tuple[Any, ...] | None = None
_projection_cache_result: dict[str, Any] | None = None
_projection_cache_next_expiry_ms: int | None = None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    return [item for item in value.get("items") or [] if isinstance(item, dict)]


def _parsed(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return {}
    parsed = payload.get("parsed")
    return parsed if isinstance(parsed, dict) else {}


def _event_order(record: dict[str, Any]) -> tuple[str, str, int, int, int, str]:
    return (
        str(record.get("captured_at") or ""),
        str(record.get("pcap_name") or ""),
        _int(record.get("stream")) or 0,
        _int(record.get("frame_index")) or 0,
        _int(record.get("offset")) or 0,
        str(record.get("packet_id") or ""),
    )


def _red_bag_item(vo: dict[str, Any], *, can_receive: bool | None) -> dict[str, Any] | None:
    uid = _int(vo.get("uid"))
    if uid is None:
        return None
    sender = vo.get("senderVO") if isinstance(vo.get("senderVO"), dict) else {}
    return {
        "uid": uid,
        "id": _int(vo.get("id")),
        "channel": _int(vo.get("channel")),
        "sub_channel_id": _int(vo.get("subChannelId")),
        "sender_name": str(sender.get("name") or ""),
        "sender_id": _int(sender.get("id")),
        "end_time_epoch_ms": _int(vo.get("endTimeStamp")),
        # SM_NewRedBag supplies the server's eligibility decision.  False is
        # authoritative; None is retained for offline baselines whose wrapper
        # only carries isReceive.
        "can_receive": can_receive,
    }


def project_red_packet_state(
    records: Iterable[dict[str, Any]],
    *,
    self_role_id: int | None,
    now_epoch_ms: int | None = None,
) -> dict[str, Any]:
    """Replay one captured login session into the game's chat-redbag state.

    The stable identities are protocol name + redbag UID.  PID, module base and
    Lua heap addresses are deliberately absent, so an emulator restart merely
    starts a new SM_Login session and resets this projection.
    """

    ordered = sorted((dict(row) for row in records), key=_event_order)
    bags: dict[int, dict[str, Any]] = {}
    claimed: set[int] = set()
    limited: set[int] = set()
    baseline_seen = False
    last_record: dict[str, Any] | None = None

    for record in ordered:
        name = str(record.get("name") or "")
        if name not in RED_PACKET_PROTOCOLS:
            continue
        data = _parsed(record)
        if not data or _int((data.get("_super") or {}).get("code")) not in (None, 0):
            continue
        last_record = record

        if name == "SM_OfflineRedBag":
            bags.clear()
            claimed.clear()
            limited.clear()
            baseline_seen = True
            for wrapper in _items(data.get("redBagVOList")):
                vo = wrapper.get("redBagVO") if isinstance(wrapper.get("redBagVO"), dict) else wrapper
                item = _red_bag_item(vo, can_receive=None)
                if item is None:
                    continue
                bags[item["uid"]] = item
                if bool(wrapper.get("isReceive")):
                    claimed.add(item["uid"])
            continue

        if name == "SM_NewRedBag":
            can_receive = data.get("canReceive")
            for vo in _items(data.get("redBagVOList")):
                item = _red_bag_item(vo, can_receive=bool(can_receive))
                if item is None:
                    continue
                bags[item["uid"]] = item
                if can_receive is False:
                    limited.add(item["uid"])
            continue

        updates = (
            _items(data.get("updateRedBagList"))
            if name == "SM_UpdateRedBagList"
            else [data]
        )
        if name in {"SM_UpdateRedBag", "SM_UpdateRedBagList"}:
            for update in updates:
                uid = _int(update.get("uid"))
                if uid is None:
                    continue
                total = _int(update.get("num"))
                received = _int(update.get("receiveNum"))
                if uid in bags:
                    bags[uid]["total"] = total
                    bags[uid]["received"] = received
                if total is not None and received is not None and received >= total:
                    claimed.add(uid)
                if self_role_id is not None and _int(update.get("receiveId")) == self_role_id:
                    claimed.add(uid)
            continue

        if name in {"SM_GrabRedBag", "SM_RedBagDetail"}:
            detail = data.get("grabDetailVO")
            if not isinstance(detail, dict):
                continue
            uid = _int(detail.get("uid"))
            if uid is None:
                continue
            total = _int(detail.get("num"))
            received = _int(detail.get("receiveNum"))
            if uid in bags:
                bags[uid]["total"] = total
                bags[uid]["received"] = received
            # These responses are emitted only for this client's detail/grab
            # request.  A reward, exhaustion, or overdue/empty result means it
            # is no longer a claimable patrol candidate.
            if bool(detail.get("isReward")) or (
                total is not None and received is not None and received >= total
            ) or (name == "SM_GrabRedBag" and (_int(data.get("failId")) or 0) != 0):
                claimed.add(uid)

    now_ms = int(now_epoch_ms if now_epoch_ms is not None else time.time() * 1000)
    pending = [
        item
        for uid, item in bags.items()
        if uid not in claimed
        and uid not in limited
        and item.get("can_receive") is not False
        and (
            _int(item.get("end_time_epoch_ms")) in (None, 0)
            or int(item["end_time_epoch_ms"]) > now_ms
        )
        and not (
            _int(item.get("total")) is not None
            and _int(item.get("received")) is not None
            and int(item["received"]) >= int(item["total"])
        )
    ]
    pending.sort(key=lambda item: (_int(item.get("end_time_epoch_ms")) or 0, item["uid"]))
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "packet_projection",
        "protocol": "SM_Login + redbag packet stream",
        "semantics": "server_announced_chat_claimable_candidates",
        "pending": bool(pending),
        "pending_count": len(pending),
        "items": pending,
        "baseline_seen": baseline_seen,
        "baseline_mode": "offline_snapshot" if baseline_seen else "bounded_server_event_history",
        "event_count": len(ordered),
        "evidence": {
            "last_packet_id": str((last_record or {}).get("packet_id") or ""),
            "last_captured_at": str((last_record or {}).get("captured_at") or ""),
            "identity": "redbag_uid",
        },
    }


def _record_dict(row: FanxiuPacketDecodedRecord) -> dict[str, Any]:
    return {
        "packet_id": row.packet_id,
        "pcap_name": row.pcap_name,
        "stream": row.stream,
        "frame_index": row.frame_index,
        "offset": row.offset,
        "name": row.name,
        "captured_at": row.captured_at,
        "payload": dict(row.payload or {}),
    }


def read_packet_red_packet_state() -> dict[str, Any]:
    """Read current server state from restart-independent packet history."""

    global _projection_cache_signature
    global _projection_cache_result
    global _projection_cache_next_expiry_ms

    try:
        with _projection_cache_lock:
            with Session(engine) as session:
                login = session.exec(
                    select(
                        FanxiuPacketDecodedRecord.packet_id,
                        FanxiuPacketDecodedRecord.captured_at,
                    )
                    .where(FanxiuPacketDecodedRecord.name == "SM_Login")
                    .order_by(FanxiuPacketDecodedRecord.captured_at.desc())
                    .limit(1)
                ).first()
                if login is None:
                    return {
                        "ok": False,
                        "available": False,
                        "complete": False,
                        "source": "packet_projection",
                        "pending": False,
                        "pending_count": 0,
                        "reason": "尚未捕捉到当前登录会话的 SM_Login 基线",
                    }
                # A simulator/login restart does not clear server-side redbags.
                # Login is identity evidence only, never a state-reset boundary.
                history_start = (
                    datetime.now() - timedelta(days=RED_PACKET_EVENT_LOOKBACK_DAYS)
                ).strftime("%Y-%m-%d %H:%M:%S")
                login_packet_id, login_captured_at = login
                identity_key = session.exec(
                    select(FanxiuPacketBusinessRecord.record_key)
                    .where(FanxiuPacketBusinessRecord.domain == "account_identity")
                    .order_by(FanxiuPacketBusinessRecord.captured_at.desc())
                    .limit(1)
                ).first()
                self_role_id = _int(identity_key)
                event_count = int(session.exec(
                    select(func.count(FanxiuPacketDecodedRecord.id))
                    .where(FanxiuPacketDecodedRecord.name.in_(RED_PACKET_PROTOCOLS))
                    .where(FanxiuPacketDecodedRecord.captured_at >= history_start)
                ).one())
                latest_event = session.exec(
                    select(
                        FanxiuPacketDecodedRecord.packet_id,
                        FanxiuPacketDecodedRecord.captured_at,
                    )
                    .where(FanxiuPacketDecodedRecord.name.in_(RED_PACKET_PROTOCOLS))
                    .where(FanxiuPacketDecodedRecord.captured_at >= history_start)
                    .order_by(
                        FanxiuPacketDecodedRecord.captured_at.desc(),
                        FanxiuPacketDecodedRecord.pcap_name.desc(),
                        FanxiuPacketDecodedRecord.stream.desc(),
                        FanxiuPacketDecodedRecord.frame_index.desc(),
                        FanxiuPacketDecodedRecord.offset.desc(),
                        FanxiuPacketDecodedRecord.packet_id.desc(),
                    )
                    .limit(1)
                ).first()
                latest_packet_id, latest_captured_at = latest_event or ("", "")
                signature = (
                    login_packet_id,
                    self_role_id,
                    history_start[:10],
                    event_count,
                    latest_packet_id,
                )
                now_ms = int(time.time() * 1000)
                if (
                    _projection_cache_signature == signature
                    and _projection_cache_result is not None
                    and (
                        _projection_cache_next_expiry_ms is None
                        or now_ms < _projection_cache_next_expiry_ms
                    )
                ):
                    cached = deepcopy(_projection_cache_result)
                    cached.setdefault("evidence", {})["cache_hit"] = True
                    cached["evidence"]["cache_source"] = "memory"
                    return cached

                projection_key = str(self_role_id or "current")
                persisted = session.exec(
                    select(FanxiuPacketBusinessRecord).where(
                        FanxiuPacketBusinessRecord.domain == RED_PACKET_PROJECTION_DOMAIN,
                        FanxiuPacketBusinessRecord.record_key == projection_key,
                    )
                ).first()
                persisted_payload = persisted.payload if persisted and isinstance(persisted.payload, dict) else {}
                persisted_result = persisted_payload.get("result")
                persisted_expiry = _int(persisted_payload.get("next_expiry_ms"))
                if (
                    tuple(persisted_payload.get("signature") or ()) == signature
                    and isinstance(persisted_result, dict)
                    and (persisted_expiry is None or now_ms < persisted_expiry)
                ):
                    cached = deepcopy(persisted_result)
                    cached.setdefault("evidence", {})["cache_hit"] = True
                    cached["evidence"]["cache_source"] = "database"
                    _projection_cache_signature = signature
                    _projection_cache_result = deepcopy(cached)
                    _projection_cache_next_expiry_ms = persisted_expiry
                    return cached

                rows = session.exec(
                    select(FanxiuPacketDecodedRecord)
                    .where(FanxiuPacketDecodedRecord.name.in_(RED_PACKET_PROTOCOLS))
                    .where(FanxiuPacketDecodedRecord.captured_at >= history_start)
                    .order_by(FanxiuPacketDecodedRecord.captured_at)
                ).all()
                result = project_red_packet_state(
                    (_record_dict(row) for row in rows),
                    self_role_id=self_role_id,
                    now_epoch_ms=now_ms,
                )
                result["session"] = {
                    "login_packet_id": login_packet_id,
                    "login_captured_at": login_captured_at,
                    "self_role_id": self_role_id,
                    "history_start": history_start,
                    "login_is_state_boundary": False,
                }
                result.setdefault("evidence", {})["cache_hit"] = False
                result["evidence"]["cache_source"] = "rebuild"
                expiries = [
                    value
                    for item in result.get("items") or []
                    if (value := _int(item.get("end_time_epoch_ms"))) is not None
                    and value > now_ms
                ]
                _projection_cache_signature = signature
                _projection_cache_result = deepcopy(result)
                _projection_cache_next_expiry_ms = min(expiries) if expiries else None
                persisted_payload = {
                    "signature": list(signature),
                    "next_expiry_ms": _projection_cache_next_expiry_ms,
                    "result": result,
                }
                if persisted is None:
                    session.add(FanxiuPacketBusinessRecord(
                        domain=RED_PACKET_PROJECTION_DOMAIN,
                        record_key=projection_key,
                        protocol="SM_Login + redbag packet stream",
                        packet_id=latest_packet_id,
                        source_kind="packet_projection",
                        entity_id=projection_key,
                        captured_at=latest_captured_at or login_captured_at,
                        captured_date=(latest_captured_at or login_captured_at)[:10],
                        payload=persisted_payload,
                        evidence=dict(result.get("evidence") or {}),
                    ))
                else:
                    persisted.protocol = "SM_Login + redbag packet stream"
                    persisted.packet_id = latest_packet_id
                    persisted.captured_at = latest_captured_at or login_captured_at
                    persisted.captured_date = persisted.captured_at[:10]
                    persisted.payload = persisted_payload
                    persisted.evidence = dict(result.get("evidence") or {})
                    persisted.updated_at = time.time()
                session.commit()
                return result
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "packet_projection",
            "pending": False,
            "pending_count": 0,
            "reason": f"读取红包协议投影失败：{exc}",
        }
