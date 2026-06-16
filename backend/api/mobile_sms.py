from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from backend.core.access.auth import get_current_active_user
from backend.core.access.service_tokens import (
    SERVICE_SCOPE_MOBILE_SMS_UPLOAD,
    require_service_scope,
)
from backend.db import get_session
from backend.models import MobileSmsMessage, User


router = APIRouter()


class MobileSmsItem(BaseModel):
    sms_id: str = Field(min_length=1)
    thread_id: str = ""
    address: str = ""
    person: str = ""
    body: str = ""
    date_ms: int = 0
    date_sent_ms: int | None = None
    message_type: str = "inbox"
    read: bool | None = None
    seen: bool | None = None
    status: int | None = None
    service_center: str = ""
    subscription_id: int | None = None
    sim_slot_index: int | None = None
    sim_display_name: str = ""
    sim_carrier_name: str = ""
    raw_json: dict[str, Any] = Field(default_factory=dict)


class MobileSmsBatchRequest(BaseModel):
    device_id: str = Field(min_length=1)
    source: str = "android"
    items: list[MobileSmsItem] = Field(default_factory=list, max_length=1000)


def _normalize_message_type(value: str) -> str:
    text = (value or "").strip().lower()
    if text in {"1", "in", "received", "receive"}:
        return "inbox"
    return text or "inbox"


def _serialize_message(row: MobileSmsMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "device_id": row.device_id,
        "sms_id": row.sms_id,
        "thread_id": row.thread_id,
        "address": row.address,
        "person": row.person,
        "body": row.body,
        "date_ms": row.date_ms,
        "date_sent_ms": row.date_sent_ms,
        "message_type": row.message_type,
        "read": row.read,
        "seen": row.seen,
        "status": row.status,
        "service_center": row.service_center,
        "subscription_id": row.subscription_id,
        "sim_slot_index": row.sim_slot_index,
        "sim_display_name": row.sim_display_name,
        "sim_carrier_name": row.sim_carrier_name,
        "raw_json": row.raw_json or {},
        "source": row.source,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get(
    "/ping",
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_MOBILE_SMS_UPLOAD))],
)
def ping_mobile_sms_upload(device_id: str = ""):
    return {
        "ok": True,
        "device_id": device_id.strip(),
        "scope": SERVICE_SCOPE_MOBILE_SMS_UPLOAD,
    }


@router.post(
    "/batch",
    dependencies=[Depends(require_service_scope(SERVICE_SCOPE_MOBILE_SMS_UPLOAD))],
)
def upload_mobile_sms_batch(req: MobileSmsBatchRequest, session: Session = Depends(get_session)):
    now = time.time()
    inserted = 0
    updated = 0
    skipped = 0
    device_id = req.device_id.strip()

    for item in req.items:
        message_type = _normalize_message_type(item.message_type)
        if message_type != "inbox":
            skipped += 1
            continue

        sms_id = item.sms_id.strip()
        existing = session.exec(
            select(MobileSmsMessage).where(
                MobileSmsMessage.device_id == device_id,
                MobileSmsMessage.sms_id == sms_id,
            )
        ).first()
        if existing:
            row = existing
            updated += 1
        else:
            row = MobileSmsMessage(
                device_id=device_id,
                sms_id=sms_id,
                first_seen_at=now,
                created_at=now,
            )
            inserted += 1

        row.thread_id = item.thread_id
        row.address = item.address
        row.person = item.person
        row.body = item.body
        row.date_ms = int(item.date_ms or 0)
        row.date_sent_ms = item.date_sent_ms
        row.message_type = message_type
        row.read = item.read
        row.seen = item.seen
        row.status = item.status
        row.service_center = item.service_center
        row.subscription_id = item.subscription_id
        row.sim_slot_index = item.sim_slot_index
        row.sim_display_name = item.sim_display_name
        row.sim_carrier_name = item.sim_carrier_name
        row.raw_json = item.raw_json
        row.source = req.source.strip() or "android"
        row.last_seen_at = now
        row.updated_at = now
        session.add(row)

    session.commit()
    return {
        "ok": True,
        "device_id": device_id,
        "received": len(req.items),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }


@router.get("/messages")
def list_mobile_sms_messages(
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_active_user),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    device_id: str = "",
    keyword: str = "",
    address: str = "",
):
    offset = (page - 1) * page_size
    statement = select(MobileSmsMessage).where(MobileSmsMessage.message_type == "inbox")
    count_statement = select(func.count()).select_from(MobileSmsMessage).where(MobileSmsMessage.message_type == "inbox")

    if device_id.strip():
        statement = statement.where(MobileSmsMessage.device_id == device_id.strip())
        count_statement = count_statement.where(MobileSmsMessage.device_id == device_id.strip())
    if address.strip():
        like_value = f"%{address.strip()}%"
        statement = statement.where(col(MobileSmsMessage.address).like(like_value))
        count_statement = count_statement.where(col(MobileSmsMessage.address).like(like_value))
    if keyword.strip():
        like_value = f"%{keyword.strip()}%"
        statement = statement.where(
            col(MobileSmsMessage.body).like(like_value)
            | col(MobileSmsMessage.address).like(like_value)
        )
        count_statement = count_statement.where(
            col(MobileSmsMessage.body).like(like_value)
            | col(MobileSmsMessage.address).like(like_value)
        )

    total = session.exec(count_statement).one()
    rows = session.exec(
        statement.order_by(MobileSmsMessage.date_ms.desc(), MobileSmsMessage.created_at.desc())
        .offset(offset)
        .limit(page_size)
    ).all()
    return {
        "ok": True,
        "items": [_serialize_message(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/stats")
def get_mobile_sms_stats(
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_active_user),
):
    total = session.exec(
        select(func.count()).select_from(MobileSmsMessage).where(MobileSmsMessage.message_type == "inbox")
    ).one()
    latest = session.exec(
        select(MobileSmsMessage).where(MobileSmsMessage.message_type == "inbox").order_by(MobileSmsMessage.date_ms.desc())
    ).first()
    devices = session.exec(
        select(MobileSmsMessage.device_id, func.count())
        .where(MobileSmsMessage.message_type == "inbox")
        .group_by(MobileSmsMessage.device_id)
        .order_by(MobileSmsMessage.device_id)
    ).all()
    return {
        "ok": True,
        "total": total,
        "latest": _serialize_message(latest) if latest else None,
        "devices": [
            {"device_id": row[0], "count": row[1]}
            for row in devices
        ],
    }
