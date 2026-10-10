from __future__ import annotations

import time
import inspect
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from backend.models import FanxiuPacketBusinessRecord

def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _evidence_from_row(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence")
    if isinstance(evidence, dict):
        return evidence
    return {}


def _source_id_from_row(row: dict[str, Any]) -> str:
    evidence = _evidence_from_row(row)
    return _text(evidence.get("packet_id") or row.get("packet_id"))


def _captured_at_from_row(row: dict[str, Any]) -> str:
    return _text(row.get("captured_at") or _evidence_from_row(row).get("captured_at"))


def upsert_fanxiu_business_records(session: Session, rows: list[dict[str, Any]], *,
                                 diagnostics_path: Path | None = None) -> dict[str, int]:
    """Persist current facts with bounded write diagnostics, without lock retries.

    Query timing includes ORM autoflush. A supplied diagnostics_path isolates
    owned test databases; read_business_write_diagnostics is the public reader.
    """
    from backend.core.fanxiu.business_write_diagnostics import observe_business_write
    frame = inspect.currentframe()
    caller = frame.f_back
    source = {'file': Path(caller.f_code.co_filename).name,
              'function': caller.f_code.co_name, 'line': caller.f_lineno}
    del frame, caller
    with observe_business_write(session, rows, source=source, path=diagnostics_path) as diagnostic:
        result = _upsert_fanxiu_business_records(session, rows, diagnostic)
        diagnostic['result'] = dict(result)
        return result


def _upsert_fanxiu_business_records(session, rows, diagnostic):
    created = 0
    updated = 0
    skipped_invalid = 0
    skipped_duplicate = 0
    for row in rows:
        if not isinstance(row, dict):
            skipped_invalid += 1
            continue
        domain = _text(row.get("domain")).strip()
        record_key = _text(row.get("record_key")).strip()
        if not domain or not record_key:
            skipped_invalid += 1
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else dict(row)
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else _evidence_from_row(payload)
        captured_at = _captured_at_from_row(row) or _captured_at_from_row(payload)
        packet_id = _text(row.get("packet_id") or _source_id_from_row(row) or _source_id_from_row(payload))
        protocol = _text(row.get("protocol") or evidence.get("protocol") or payload.get("protocol"))
        source_kind = _text(row.get("source_kind") or evidence.get("source_kind") or payload.get("source_kind"))
        now = time.time()
        diagnostic['phase'] = 'lookup_may_autoflush'
        lookup_started = time.perf_counter()
        try:
            existing = session.exec(
                select(FanxiuPacketBusinessRecord).where(
                    FanxiuPacketBusinessRecord.domain == domain,
                    FanxiuPacketBusinessRecord.record_key == record_key,
                )
            ).first()
        finally:
            phases = diagnostic['phase_seconds']
            phases['lookup_may_autoflush'] = phases.get('lookup_may_autoflush', 0) + time.perf_counter() - lookup_started
        diagnostic['phase'] = 'stage_changes'
        if existing:
            # Business records are current-state projections.  Applying an old
            # observation must never roll a newer absolute game fact backwards.
            if existing.captured_at and captured_at and captured_at < existing.captured_at:
                skipped_duplicate += 1
                continue
            if (
                existing.payload == payload
                and existing.evidence == evidence
                and existing.captured_at == captured_at
                and existing.protocol == protocol
                and existing.packet_id == packet_id
                and existing.source_kind == source_kind
            ):
                skipped_duplicate += 1
                continue
            existing.protocol = protocol
            existing.packet_id = packet_id
            existing.source_kind = source_kind
            existing.entity_id = _text(row.get("entity_id") or payload.get("entity_id") or payload.get("id"))
            existing.entity_name = _text(row.get("entity_name") or payload.get("entity_name") or payload.get("name"))
            existing.captured_at = captured_at
            existing.captured_date = captured_at[:10]
            existing.payload = payload
            existing.evidence = evidence
            existing.updated_at = now
            updated += 1
            continue
        session.add(
            FanxiuPacketBusinessRecord(
                domain=domain,
                record_key=record_key,
                protocol=protocol,
                packet_id=packet_id,
                source_kind=source_kind,
                entity_id=_text(row.get("entity_id") or payload.get("entity_id") or payload.get("id")),
                entity_name=_text(row.get("entity_name") or payload.get("entity_name") or payload.get("name")),
                captured_at=captured_at,
                captured_date=captured_at[:10],
                payload=payload,
                evidence=evidence,
                created_at=now,
                updated_at=now,
            )
        )
        created += 1
    if created or updated:
        diagnostic['phase'] = 'commit_flush'
        commit_started = time.perf_counter()
        try:
            session.commit()
        finally:
            diagnostic['phase_seconds']['commit_flush'] = time.perf_counter() - commit_started
    diagnostic['phase'] = 'complete'
    return {"created": created, "updated": updated, "skipped_invalid": skipped_invalid, "skipped_duplicate": skipped_duplicate}
