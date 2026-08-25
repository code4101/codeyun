from __future__ import annotations

"""Database-backed current snapshots for Fanxiu inventory halls.

The store deliberately knows nothing about a particular hall or collector.  A
hall page reads its latest durable snapshot from here; a runtime collector (or
an explicit manual editor during migration) is the only writer.
"""

import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from backend.models import FanxiuPacketBusinessRecord


INVENTORY_HALL_SNAPSHOT_DOMAIN = "inventory_hall_snapshot"


def load_inventory_hall_snapshot(
    session: Session,
    hall_key: str,
) -> dict[str, Any] | None:
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == INVENTORY_HALL_SNAPSHOT_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == str(hall_key),
        )
    ).first()
    return deepcopy(row.payload) if row and isinstance(row.payload, dict) else None


def upsert_inventory_hall_snapshot(
    session: Session,
    hall_key: str,
    snapshot: dict[str, Any],
    *,
    source_kind: str,
    entity_name: str,
    require_complete_runtime: bool = False,
) -> FanxiuPacketBusinessRecord:
    payload = dict(snapshot or {})
    if require_complete_runtime:
        item_count = sum(
            len(value)
            for value in payload.values()
            if isinstance(value, list)
        )
        if not payload.get("runtime_complete") or item_count <= 0:
            raise ValueError("拒绝用不完整的运行态仓库快照覆盖数据库")

    captured_timestamp = float(payload.get("runtime_updated_at") or time.time())
    captured_at = datetime.fromtimestamp(captured_timestamp).isoformat(timespec="seconds")
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == INVENTORY_HALL_SNAPSHOT_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == str(hall_key),
        )
    ).first()
    now = time.time()
    if row is None:
        row = FanxiuPacketBusinessRecord(
            domain=INVENTORY_HALL_SNAPSHOT_DOMAIN,
            record_key=str(hall_key),
            source_kind=str(source_kind),
            entity_name=str(entity_name),
            captured_at=captured_at,
            captured_date=captured_at[:10],
            payload=payload,
            evidence=dict(payload.get("runtime_debug") or {}),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.source_kind = str(source_kind)
        row.entity_name = str(entity_name)
        row.captured_at = captured_at
        row.captured_date = captured_at[:10]
        row.payload = payload
        row.evidence = dict(payload.get("runtime_debug") or {})
        row.updated_at = now
    session.commit()
    session.refresh(row)
    return row
