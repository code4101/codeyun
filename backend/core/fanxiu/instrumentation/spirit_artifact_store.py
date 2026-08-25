from __future__ import annotations

"""Persist the latest equipped spirit-artifact projection as a database fact."""

import time
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from backend.models import FanxiuPacketBusinessRecord


SPIRIT_ARTIFACT_SNAPSHOT_DOMAIN = "spirit_artifact_equipped_snapshot"
SPIRIT_ARTIFACT_SNAPSHOT_KEY = "current"


def load_spirit_artifact_runtime_snapshot(session: Session) -> dict[str, Any] | None:
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == SPIRIT_ARTIFACT_SNAPSHOT_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == SPIRIT_ARTIFACT_SNAPSHOT_KEY,
        )
    ).first()
    return dict(row.payload) if row and isinstance(row.payload, dict) else None


def upsert_spirit_artifact_runtime_snapshot(
    session: Session,
    snapshot: dict[str, Any],
) -> FanxiuPacketBusinessRecord:
    """Replace the current fact only with a complete, non-empty runtime snapshot."""

    payload = dict(snapshot or {})
    if not payload.get("runtime_complete") or int(payload.get("runtime_equipped_count") or 0) <= 0:
        raise ValueError("拒绝用不完整的灵器运行态快照覆盖数据库")
    captured_timestamp = float(payload.get("runtime_updated_at") or time.time())
    captured_at = datetime.fromtimestamp(captured_timestamp).isoformat(timespec="seconds")
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == SPIRIT_ARTIFACT_SNAPSHOT_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == SPIRIT_ARTIFACT_SNAPSHOT_KEY,
        )
    ).first()
    now = time.time()
    if row is None:
        row = FanxiuPacketBusinessRecord(
            domain=SPIRIT_ARTIFACT_SNAPSHOT_DOMAIN,
            record_key=SPIRIT_ARTIFACT_SNAPSHOT_KEY,
            source_kind="dynamic_instrumentation",
            entity_name="当前装配灵器",
            captured_at=captured_at,
            captured_date=captured_at[:10],
            payload=payload,
            evidence=dict(payload.get("runtime_debug") or {}),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.source_kind = "dynamic_instrumentation"
        row.captured_at = captured_at
        row.captured_date = captured_at[:10]
        row.payload = payload
        row.evidence = dict(payload.get("runtime_debug") or {})
        row.updated_at = now
    session.commit()
    session.refresh(row)
    return row
