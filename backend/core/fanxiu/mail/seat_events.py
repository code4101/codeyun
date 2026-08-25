from __future__ import annotations

"""Typed positive events projected from Lundao/Lingmai eviction mails."""

from datetime import datetime
from typing import Any, Literal

from sqlmodel import Session, select

from backend.models import FanxiuMailRecord


SeatDomain = Literal["lundao", "lingmai"]


# The order is authoritative SystemMessage placeholder order, not a guess from
# the repeated NAME/NUM wire keys.  Keeping it per mail type prevents TIME and
# ordinary NUM values from stealing each other's queue entries.
SEAT_EVICTION_MAIL_SCHEMAS: dict[int, dict[str, Any]] = {
    2101: {"domain": "lundao", "content_id": 41001, "fields": (("opponent", "name"), ("location", "name"), ("elapsed_ms", "num"), ("remaining_ms", "num"), ("rewards", "reward"))},
    2105: {"domain": "lundao", "content_id": 41005, "guarantee": True, "fields": (("opponent", "name"), ("location", "name"), ("elapsed_ms", "num"), ("remaining_ms", "num"), ("rewards", "reward"))},
    2107: {"domain": "lundao", "content_id": 41021, "extra": True, "fields": (("opponent", "name"), ("location", "name"), ("elapsed_ms", "num"), ("remaining_ms", "num"), ("buff", "name_number"), ("rewards", "reward"), ("extra_rewards", "reward"), ("leaderboard_points", "num"))},
    2109: {"domain": "lundao", "content_id": 41023, "guarantee": True, "extra": True, "fields": (("opponent", "name"), ("location", "name"), ("elapsed_ms", "num"), ("remaining_ms", "num"), ("buff", "name_number"), ("rewards", "reward"), ("extra_rewards", "reward"), ("leaderboard_points", "num"))},
    2112: {"domain": "lundao", "content_id": 41031, "fields": (("opponent", "name"), ("location", "name"), ("elapsed_ms", "num"), ("remaining_ms", "num"), ("buff", "name_number"), ("rewards", "reward"), ("leaderboard_points", "num"))},
    2114: {"domain": "lundao", "content_id": 41033, "guarantee": True, "fields": (("opponent", "name"), ("location", "name"), ("elapsed_ms", "num"), ("remaining_ms", "num"), ("buff", "name_number"), ("rewards", "reward"), ("leaderboard_points", "num"))},
    2202: {"domain": "lingmai", "content_id": 41011, "fields": (("opponent", "name"), ("buff", "num"), ("location", "name"), ("elapsed_ms", "num"), ("remaining_ms", "num"), ("rewards", "reward"))},
    2206: {"domain": "lingmai", "content_id": 41015, "fields": (("opponent", "name"), ("buff", "num"), ("location", "name"), ("elapsed_ms", "num"), ("remaining_ms", "num"), ("rewards", "reward"))},
    2208: {"domain": "lingmai", "content_id": 41017, "fields": (("opponent", "name"), ("location", "name"), ("elapsed_ms", "num"), ("remaining_ms", "num"), ("rewards", "reward"))},
    2211: {"domain": "lingmai", "content_id": 41120, "extra": True, "fields": (("opponent", "name"), ("buff", "num"), ("location", "name"), ("elapsed_ms", "num"), ("alliance_rewards", "reward"), ("guiyuan_rewards", "reward"), ("remaining_ms", "num"), ("rewards", "reward"))},
    2213: {"domain": "lingmai", "content_id": 41122, "extra": True, "fields": (("opponent", "name"), ("location", "name"), ("elapsed_ms", "num"), ("alliance_rewards", "reward"), ("guiyuan_rewards", "reward"), ("remaining_ms", "num"), ("rewards", "reward"))},
}


def _items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return value["items"]
    return []


def _param_kind(item: dict[str, Any]) -> str:
    class_name = str(item.get("_class") or "").lower()
    super_value = item.get("_super") if isinstance(item.get("_super"), dict) else {}
    key = str(super_value.get("key") or item.get("key") or "").upper()
    if "REWARD" in class_name or key == "REWARD":
        return "reward"
    if "NAME" in class_name or key == "NAME":
        return "name"
    if "NUM" in class_name or key in {"NUM", "N"}:
        return "num"
    return "unknown"


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value) if float(value).is_integer() else float(value)
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _reward_list(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in _items(value):
        if not isinstance(raw, dict):
            continue
        row = {
            "item_id": raw.get("code"),
            "amount": _number(raw.get("amount")),
            "type": raw.get("type"),
            "class": str(raw.get("_class") or ""),
        }
        result.append({key: value for key, value in row.items() if value not in (None, "")})
    return result


def _event_at(mail_vo: dict[str, Any]) -> tuple[str, int | None]:
    value = _number(mail_vo.get("createTime"))
    if not isinstance(value, int | float) or value <= 0:
        return "", None
    milliseconds = int(value)
    return datetime.fromtimestamp(milliseconds / 1000).astimezone().isoformat(timespec="seconds"), milliseconds


def parse_fanxiu_seat_eviction_event(
    mail_type: Any,
    mail_vo: dict[str, Any],
    *,
    mail_id: str = "",
) -> dict[str, Any] | None:
    """Parse one known eviction mail; absent/ill-typed fields stay absent."""

    try:
        normalized_type = int(mail_type)
    except (TypeError, ValueError):
        return None
    schema = SEAT_EVICTION_MAIL_SCHEMAS.get(normalized_type)
    if schema is None or not isinstance(mail_vo, dict):
        return None
    params = [item for item in _items(mail_vo.get("i18nParams")) if isinstance(item, dict)]
    event_at, event_at_ms = _event_at(mail_vo)
    event: dict[str, Any] = {
        "domain": schema["domain"],
        "event": "seat_eviction",
        "positive": True,
        "mail_type": normalized_type,
        "content_id": int(schema["content_id"]),
        "guarantee": bool(schema.get("guarantee")),
        "extra": bool(schema.get("extra")),
    }
    if mail_id or mail_vo.get("id"):
        event["mail_id"] = str(mail_id or mail_vo.get("id"))
    if event_at:
        event["event_at"] = event_at
        event["event_at_ms"] = event_at_ms

    cursor = 0
    parsed_fields: list[str] = []
    for field, expected_kind in schema["fields"]:
        if cursor >= len(params):
            break
        item = params[cursor]
        actual_kind = _param_kind(item)
        accepted = {expected_kind}
        if expected_kind == "name_number":
            accepted = {"name", "num"}
        if actual_kind not in accepted:
            # Fail closed at the first schema disagreement: shifting remaining
            # values would manufacture a plausible but false eviction event.
            event["incomplete_reason"] = f"parameter_{cursor}_expected_{expected_kind}_got_{actual_kind}"
            break
        cursor += 1
        value = item.get("value")
        if actual_kind == "reward":
            normalized = _reward_list(value)
        elif actual_kind == "num":
            normalized = _number(value)
        else:
            normalized = str(value or "").strip()
            if expected_kind == "name_number":
                normalized = _number(normalized)
        if normalized not in (None, "", []):
            event[field] = normalized
            parsed_fields.append(field)
    event["complete"] = cursor == len(schema["fields"]) and len(params) == cursor
    event["evidence"] = {
        "source": "mail_i18n_params",
        "parameter_count": len(params),
        "parsed_fields": parsed_fields,
    }
    return event


def seat_eviction_event_from_record(record: FanxiuMailRecord) -> dict[str, Any] | None:
    payload = record.payload if isinstance(record.payload, dict) else {}
    existing = payload.get("seat_eviction_event")
    if isinstance(existing, dict):
        return dict(existing)
    mail_vo = payload.get("mailVo") if isinstance(payload.get("mailVo"), dict) else {}
    return parse_fanxiu_seat_eviction_event(record.mail_type, mail_vo, mail_id=record.mail_id)


def list_fanxiu_seat_eviction_events(
    session: Session,
    *,
    domain: SeatDomain | None = None,
    since_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Return durable positive eviction events in chronological order."""

    types = [str(key) for key, value in SEAT_EVICTION_MAIL_SCHEMAS.items() if domain is None or value["domain"] == domain]
    records = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_type.in_(types))).all()
    events = [event for record in records if (event := seat_eviction_event_from_record(record)) is not None]
    if since_ms is not None:
        events = [event for event in events if int(event.get("event_at_ms") or 0) >= int(since_ms)]
    return sorted(events, key=lambda item: (int(item.get("event_at_ms") or 0), str(item.get("mail_id") or "")))


def reconcile_seat_eviction_with_protocol_fact(
    event: dict[str, Any],
    seat_fact: dict[str, Any] | None,
) -> dict[str, Any]:
    """Relate an eviction mail to a seat fact without treating absence as proof."""

    result = {"event": dict(event), "protocol_fact": dict(seat_fact or {}), "relation": "not_loaded"}
    if not seat_fact or not seat_fact.get("available"):
        return result
    evidence = seat_fact.get("evidence") if isinstance(seat_fact.get("evidence"), dict) else {}
    nested_times = [
        str(value.get("captured_at") or "")
        for value in evidence.values()
        if isinstance(value, dict) and value.get("captured_at")
    ]
    captured_at = str(
        seat_fact.get("captured_at")
        or evidence.get("captured_at")
        or (max(nested_times) if nested_times else "")
    )
    try:
        fact_ms = int(datetime.fromisoformat(captured_at.replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        result["relation"] = "loaded_without_comparable_time"
        return result
    event_ms = int(event.get("event_at_ms") or 0)
    seated = seat_fact.get("seated")
    result["protocol_captured_at"] = captured_at
    if fact_ms <= event_ms and seated is True:
        result["relation"] = "prior_seated_fact"
    elif fact_ms > event_ms and seated is False:
        result["relation"] = "post_event_vacated_fact"
    elif fact_ms > event_ms and seated is True:
        result["relation"] = "post_event_reseated_fact"
    else:
        result["relation"] = "loaded_inconclusive"
    return result


def reconcile_latest_seat_eviction(
    session: Session,
    event: dict[str, Any],
    *,
    since_seconds: int | None = None,
) -> dict[str, Any]:
    """Read the live seat Runtime and reconcile one mail event."""

    if event.get("domain") == "lundao":
        from backend.core.fanxiu.instrumentation.lundao import read_lundao_snapshot

        fact = read_lundao_snapshot()
    elif event.get("domain") == "lingmai":
        from backend.core.fanxiu.instrumentation.lingmai import read_lingmai_snapshot

        snapshot = read_lingmai_snapshot()
        fact = (
            snapshot.get("self_seat_facts")
            if isinstance(snapshot.get("self_seat_facts"), dict)
            else snapshot
        )
    else:
        return {"event": dict(event), "protocol_fact": {}, "relation": "unsupported_domain"}
    del session, since_seconds
    return reconcile_seat_eviction_with_protocol_fact(event, fact)


__all__ = [
    "SEAT_EVICTION_MAIL_SCHEMAS",
    "list_fanxiu_seat_eviction_events",
    "parse_fanxiu_seat_eviction_event",
    "reconcile_seat_eviction_with_protocol_fact",
    "reconcile_latest_seat_eviction",
    "seat_eviction_event_from_record",
]
