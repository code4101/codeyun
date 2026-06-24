from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, select

from backend.models import FanxiuPlayerProfileRecord

_REAL_PROFILE_PCAP_PREFIXES = (
    "fanxiu_runtime_",
    "fanxiu_live_",
    "fanxiu_windows_rank",
)


def _profile_attack_attr(row: dict[str, Any]) -> dict[str, Any] | None:
    attrs = row.get("combat_attributes")
    if not isinstance(attrs, list):
        return None
    for attr in attrs:
        if isinstance(attr, dict) and attr.get("key") == 2001 and attr.get("value") not in (None, ""):
            return attr
    return None


def _packet_id(row: dict[str, Any]) -> str:
    evidence = row.get("evidence")
    if isinstance(evidence, dict):
        return str(evidence.get("packet_id") or "")
    return ""


def _has_real_capture_evidence(row: dict[str, Any]) -> bool:
    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        return False
    packet_id = str(evidence.get("packet_id") or row.get("packet_id") or "").strip()
    record_id = str(evidence.get("record_id") or "").strip()
    pcap_name = str(evidence.get("pcap_name") or "").strip()
    if not packet_id or not record_id or not pcap_name:
        return False
    return pcap_name.startswith(_REAL_PROFILE_PCAP_PREFIXES)


def _record_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    return payload


def serialize_fanxiu_player_profile_record(record: FanxiuPlayerProfileRecord) -> dict[str, Any]:
    payload = dict(record.payload or {})
    payload.update(
        {
            "id": record.id,
            "packet_id": record.packet_id,
            "captured_at": record.captured_at,
            "source_kind": record.source_kind,
            "role_id": record.role_id,
            "role_id_text": record.role_id_text,
            "name": record.name,
            "server": record.server,
            "region_number": record.region_number,
            "region_name": record.region_name,
            "server_order": record.server_order,
            "server_name": record.server_name,
            "cultivation_level": record.cultivation_level,
            "cultivation_level_text": record.cultivation_level_text,
            "battle_score": record.battle_score,
            "battle_score_text": record.battle_score_text,
            "special_attributes": record.special_attributes or [],
            "immortal_attributes": record.immortal_attributes or [],
            "combat_attributes": record.combat_attributes or [],
            "attributes": record.attributes or [],
            "evidence": record.evidence or {},
        }
    )
    return payload


def upsert_fanxiu_player_profile_rows(session: Session, rows: list[dict[str, Any]]) -> dict[str, Any]:
    created = 0
    updated_existing = 0
    skipped_invalid = 0
    skipped_duplicate = 0
    for row in rows:
        if not isinstance(row, dict):
            skipped_invalid += 1
            continue
        attack = _profile_attack_attr(row)
        if not attack:
            skipped_invalid += 1
            continue
        packet_id = _packet_id(row)
        if not packet_id:
            skipped_invalid += 1
            continue
        if not _has_real_capture_evidence(row):
            skipped_invalid += 1
            continue
        existing = session.exec(
            select(FanxiuPlayerProfileRecord).where(FanxiuPlayerProfileRecord.packet_id == packet_id)
        ).first()
        if existing:
            captured_at = str(row.get("captured_at") or "")
            if captured_at and captured_at != existing.captured_at:
                existing.captured_at = captured_at
                existing.captured_date = captured_at[:10]
                evidence = dict(existing.evidence or {})
                row_evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
                if row_evidence:
                    evidence.update(row_evidence)
                evidence["captured_at"] = captured_at
                existing.evidence = evidence
                payload = dict(existing.payload or {})
                payload["captured_at"] = captured_at
                if row_evidence:
                    payload["evidence"] = evidence
                existing.payload = payload
                existing.updated_at = time.time()
                updated_existing += 1
            skipped_duplicate += 1
            continue
        now = time.time()
        record = FanxiuPlayerProfileRecord(
            packet_id=packet_id,
            protocol=str((row.get("evidence") or {}).get("protocol") or ""),
            source_kind=str(row.get("source_kind") or ""),
            role_id=str(row.get("role_id") or ""),
            role_id_text=str(row.get("role_id_text") or row.get("role_id") or ""),
            name=str(row.get("name") or ""),
            server=row.get("server") if isinstance(row.get("server"), int) else None,
            region_number=row.get("region_number") if isinstance(row.get("region_number"), int) else None,
            region_name=str(row.get("region_name") or ""),
            server_order=row.get("server_order") if isinstance(row.get("server_order"), int) else None,
            server_name=str(row.get("server_name") or ""),
            cultivation_level=row.get("cultivation_level") if isinstance(row.get("cultivation_level"), int) else None,
            cultivation_level_text=str(row.get("cultivation_level_text") or ""),
            attack_value=attack.get("value") if attack and isinstance(attack.get("value"), int | float) else None,
            attack_text=str(attack.get("text") if attack else ""),
            captured_at=str(row.get("captured_at") or ""),
            captured_date=str(row.get("captured_at") or "")[:10],
            battle_score=row.get("battle_score") if isinstance(row.get("battle_score"), int | float) else None,
            battle_score_text=str(row.get("battle_score_text") or ""),
            special_attributes=row.get("special_attributes") if isinstance(row.get("special_attributes"), list) else [],
            immortal_attributes=row.get("immortal_attributes") if isinstance(row.get("immortal_attributes"), list) else [],
            combat_attributes=row.get("combat_attributes") if isinstance(row.get("combat_attributes"), list) else [],
            attributes=row.get("attributes") if isinstance(row.get("attributes"), list) else [],
            payload=_record_payload(row),
            evidence=row.get("evidence") if isinstance(row.get("evidence"), dict) else {},
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        created += 1
    if created or updated_existing:
        session.commit()
    return {"created": created, "skipped_invalid": skipped_invalid, "skipped_duplicate": skipped_duplicate}


def list_fanxiu_player_profile_records(session: Session, *, limit: int = 1000) -> list[dict[str, Any]]:
    rows = session.exec(
        select(FanxiuPlayerProfileRecord)
        .order_by(FanxiuPlayerProfileRecord.captured_at.desc(), FanxiuPlayerProfileRecord.created_at.desc())
        .limit(max(1, min(limit, 5000)))
    ).all()
    return [serialize_fanxiu_player_profile_record(row) for row in rows]


def list_latest_fanxiu_player_profile_records(session: Session, *, limit: int = 1000) -> list[dict[str, Any]]:
    capped_limit = max(1, min(limit, 5000))
    id_rows = session.exec(
        text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    captured_at,
                    created_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(NULLIF(role_id_text, ''), NULLIF(role_id, ''), NULLIF(name, ''), packet_id)
                        ORDER BY captured_at DESC, created_at DESC
                    ) AS rn
                FROM fanxiuplayerprofilerecord
                WHERE attack_value IS NOT NULL OR attack_text != ''
            )
            SELECT id
            FROM ranked
            WHERE rn = 1
            ORDER BY captured_at DESC, created_at DESC
            LIMIT :limit
            """
        ).bindparams(limit=capped_limit)
    ).all()
    ids = [str(row if isinstance(row, str) else row[0]) for row in id_rows]
    if not ids:
        return []
    rows = session.exec(select(FanxiuPlayerProfileRecord).where(FanxiuPlayerProfileRecord.id.in_(ids))).all()
    rows_by_id = {row.id: row for row in rows}
    return [serialize_fanxiu_player_profile_record(rows_by_id[row_id]) for row_id in ids if row_id in rows_by_id]
