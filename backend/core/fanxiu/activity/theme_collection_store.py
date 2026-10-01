from __future__ import annotations

"""Durable stage completion for the Theme Collection job.

Completion is isolated by ``instance_key + member_id + stage_kind +
business_date`` so that a new activity instance or a new business day can never
reuse an earlier success.  The durable table is the shared
``FanxiuPacketBusinessRecord``; this module does not add a second business
table or a second lifecycle engine.
"""

from datetime import datetime
from typing import Any, Iterable, Mapping

from sqlmodel import Session, select

from backend.models import FanxiuPacketBusinessRecord


THEME_COLLECTION_COMPLETION_DOMAIN = "theme_collection_stage_completion"
THEME_COLLECTION_COMPLETION_PROTOCOL = "theme_collection_stage_v1"


def theme_stage_record_key(
    *,
    instance_key: str,
    member_id: str,
    stage_kind: str,
    business_date: str,
) -> str:
    parts = [
        str(instance_key or "").strip(),
        str(member_id or "").strip(),
        str(stage_kind or "").strip(),
        str(business_date or "").strip(),
    ]
    if not all(parts):
        raise ValueError("主题集阶段完成身份不完整")
    return "|".join(parts)


def _parse_completed_at(value: Any) -> datetime:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("主题集阶段完成时间无效") from exc
    if parsed.tzinfo is None:
        raise ValueError("主题集阶段完成时间必须带时区")
    return parsed


def _normalized_stage_state(state: Mapping[str, Any]) -> dict[str, Any]:
    record_key = str(state.get("record_key") or "").strip()
    if not record_key:
        raise ValueError("主题集阶段完成缺少 record_key")
    status = str(state.get("status") or "completed").strip()
    if status not in {"completed", "retained", "unavailable"}:
        raise ValueError(f"主题集阶段完成状态无效：{status}")
    completed_at = _parse_completed_at(state.get("completed_at"))
    return {
        "version": 1,
        "record_key": record_key,
        "member_id": str(state.get("member_id") or "").strip(),
        "instance_key": str(state.get("instance_key") or "").strip(),
        "stage": str(state.get("stage") or "").strip(),
        "stage_kind": str(state.get("stage_kind") or "").strip(),
        "business_date": str(state.get("business_date") or "").strip(),
        "status": status,
        "message": str(state.get("message") or ""),
        "completed_at": completed_at.isoformat(timespec="seconds"),
    }


def list_theme_stage_completions(
    session: Session, *, business_date: str | None = None,
) -> list[dict[str, Any]]:
    """Read validated completion evidence; never probe Runtime or write records."""
    statement = select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == THEME_COLLECTION_COMPLETION_DOMAIN,
        FanxiuPacketBusinessRecord.protocol == THEME_COLLECTION_COMPLETION_PROTOCOL,
    )
    if business_date is not None:
        statement = statement.where(FanxiuPacketBusinessRecord.captured_date == business_date)
    return [_normalized_stage_state(dict(row.payload or {})) for row in session.exec(statement).all()]


def completed_theme_stage_keys(
    session: Session,
    *,
    instance_keys: Iterable[str] | None = None,
) -> set[tuple[str, str, str, str]]:
    """Return ``(instance_key, member_id, kind, business_date)`` completions."""

    statement = select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == THEME_COLLECTION_COMPLETION_DOMAIN,
        FanxiuPacketBusinessRecord.protocol
        == THEME_COLLECTION_COMPLETION_PROTOCOL,
    )
    selected = tuple(
        str(value) for value in (instance_keys or ()) if str(value or "").strip()
    )
    if selected:
        statement = statement.where(
            FanxiuPacketBusinessRecord.entity_id.in_(selected)
        )
    result: set[tuple[str, str, str, str]] = set()
    for row in session.exec(statement).all():
        payload = dict(row.payload or {})
        instance_key = str(payload.get("instance_key") or "").strip()
        member_id = str(payload.get("member_id") or "").strip()
        stage_kind = str(payload.get("stage_kind") or "").strip()
        business_date = str(payload.get("business_date") or "").strip()
        if instance_key and member_id and stage_kind and business_date:
            result.add((instance_key, member_id, stage_kind, business_date))
    return result


def persist_theme_stage_completion(
    session: Session,
    *,
    instance_key: str,
    member_id: str,
    stage: str,
    stage_kind: str,
    business_date: str,
    completed_at: datetime,
    status: str = "completed",
    message: str = "",
) -> dict[str, Any]:
    """Upsert one isolated stage completion after a successful member call."""

    if completed_at.tzinfo is None:
        raise ValueError("主题集阶段完成时间必须带时区")
    record_key = theme_stage_record_key(
        instance_key=instance_key,
        member_id=member_id,
        stage_kind=stage_kind,
        business_date=business_date,
    )
    normalized = _normalized_stage_state(
        {
            "record_key": record_key,
            "member_id": member_id,
            "instance_key": instance_key,
            "stage": stage,
            "stage_kind": stage_kind,
            "business_date": business_date,
            "status": status,
            "message": message,
            "completed_at": completed_at,
        }
    )
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain
            == THEME_COLLECTION_COMPLETION_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == record_key,
        )
    ).first()
    if row is not None:
        if row.protocol != THEME_COLLECTION_COMPLETION_PROTOCOL:
            raise ValueError(
                f"主题集 completion protocol 不兼容：{row.protocol}"
            )
        previous = _normalized_stage_state(dict(row.payload or {}))
        if _parse_completed_at(normalized["completed_at"]) < _parse_completed_at(
            previous["completed_at"]
        ):
            raise ValueError("主题集阶段完成时间发生倒退")
        row.entity_id = instance_key
        row.entity_name = f"{member_id}:{stage_kind}"
        row.captured_at = normalized["completed_at"]
        row.captured_date = business_date
        row.payload = normalized
        row.evidence = {
            "write_boundary": "after_successful_theme_member_call",
            "scheduler_trigger_fields": ["next_time"],
        }
        row.updated_at = datetime.now().timestamp()
    else:
        row = FanxiuPacketBusinessRecord(
            domain=THEME_COLLECTION_COMPLETION_DOMAIN,
            record_key=record_key,
            protocol=THEME_COLLECTION_COMPLETION_PROTOCOL,
            source_kind="theme_collection_member_success_completion",
            entity_id=instance_key,
            entity_name=f"{member_id}:{stage_kind}",
            captured_at=normalized["completed_at"],
            captured_date=business_date,
            payload=normalized,
            evidence={
                "write_boundary": "after_successful_theme_member_call",
                "scheduler_trigger_fields": ["next_time"],
            },
        )
        session.add(row)
    session.commit()
    return normalized


__all__ = [
    "THEME_COLLECTION_COMPLETION_DOMAIN",
    "THEME_COLLECTION_COMPLETION_PROTOCOL",
    "completed_theme_stage_keys",
    "list_theme_stage_completions",
    "persist_theme_stage_completion",
    "theme_stage_record_key",
]
