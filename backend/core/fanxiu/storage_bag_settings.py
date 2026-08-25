"""Database-backed user settings for cumulative storage-bag atlas items."""

from __future__ import annotations

import time
from typing import Any, Mapping

from sqlmodel import Session, select

from backend.models import FanxiuStorageBagItemSetting, FanxiuStorageBagYieldAggregate


def apply_storage_bag_item_settings(
    session: Session,
    atlas: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge database flags into an atlas projection without mutating Runtime facts."""

    result = dict(atlas)
    items = [dict(row) for row in atlas.get("items") or [] if isinstance(row, Mapping)]
    base_ids = [int(row["base_id"]) for row in items if str(row.get("base_id") or "").isdigit()]
    settings_by_id: dict[int, FanxiuStorageBagItemSetting] = {}
    aggregates_by_id: dict[int, FanxiuStorageBagYieldAggregate] = {}
    if base_ids:
        records = session.exec(
            select(FanxiuStorageBagItemSetting).where(FanxiuStorageBagItemSetting.base_id.in_(base_ids))
        ).all()
        settings_by_id = {int(record.base_id): record for record in records}
        aggregates = session.exec(
            select(FanxiuStorageBagYieldAggregate).where(
                FanxiuStorageBagYieldAggregate.base_id.in_(base_ids)
            )
        ).all()
        aggregates_by_id = {int(record.base_id): record for record in aggregates}
    for row in items:
        base_id = int(row.get("base_id") or 0)
        record = settings_by_id.get(base_id)
        aggregate = aggregates_by_id.get(base_id)
        row["auto_claim"] = bool(record.auto_claim) if record is not None else False
        row["note"] = str(record.note or "") if record is not None else ""
        row["operation_template"] = str(record.operation_template or "") if record is not None else ""
        row["yield_mode"] = str(record.yield_mode or "") if record is not None else ""
        row["analysis_status"] = str(record.analysis_status or "pending") if record is not None else "pending"
        row["analysis_fingerprint"] = str(record.analysis_fingerprint or "") if record is not None else ""
        row["analysis_reason"] = str(record.analysis_reason or "") if record is not None else ""
        row["average_yield"] = str(aggregate.average_yield or "") if aggregate is not None else ""
        row["yield_sample_count"] = int(aggregate.opened_count or 0) if aggregate is not None else 0
    result["items"] = items
    return result


def set_storage_bag_auto_claim(
    session: Session,
    *,
    base_id: int,
    auto_claim: bool,
) -> FanxiuStorageBagItemSetting:
    """Idempotently set the automatic-claim flag for one stable item ID."""

    now = time.time()
    record = session.get(FanxiuStorageBagItemSetting, base_id)
    if record is None:
        record = FanxiuStorageBagItemSetting(
            base_id=base_id,
            auto_claim=bool(auto_claim),
            created_at=now,
            updated_at=now,
        )
    else:
        record.auto_claim = bool(auto_claim)
        record.updated_at = now
    session.add(record)
    session.flush()
    return record


def set_storage_bag_note(
    session: Session,
    *,
    base_id: int,
    note: str,
) -> FanxiuStorageBagItemSetting:
    """Idempotently set the user's usage note for one stable item ID."""

    normalized_note = str(note or "").strip()
    now = time.time()
    record = session.get(FanxiuStorageBagItemSetting, base_id)
    if record is None:
        record = FanxiuStorageBagItemSetting(
            base_id=base_id,
            auto_claim=False,
            note=normalized_note,
            created_at=now,
            updated_at=now,
        )
    else:
        record.note = normalized_note
        record.updated_at = now
    session.add(record)
    session.flush()
    return record


def delete_storage_bag_item_setting(session: Session, *, base_id: int) -> bool:
    record = session.get(FanxiuStorageBagItemSetting, base_id)
    if record is None:
        return False
    session.delete(record)
    session.flush()
    return True
