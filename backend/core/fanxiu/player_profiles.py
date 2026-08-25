from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlmodel import Session, select

from backend.models import FanxiuPlayerProfileRecord


def _finite_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(float(value)):
        return None
    return value


def _captured_date(value: Any) -> str:
    captured_at = str(value or "").strip()
    if not captured_at:
        return ""
    try:
        parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
    return parsed.date().isoformat()


def _profile_attack_attr(row: dict[str, Any]) -> dict[str, Any] | None:
    attrs = row.get("combat_attributes")
    if not isinstance(attrs, list):
        return None
    for attr in attrs:
        if not isinstance(attr, dict) or attr.get("key") != 2001:
            continue
        if _finite_number(attr.get("value")) is not None:
            return attr
    return None


def _observation_id(row: dict[str, Any]) -> str:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    return str(
        row.get("observation_id")
        or row.get("packet_id")
        or evidence.get("observation_id")
        or evidence.get("packet_id")
        or ""
    ).strip()


def serialize_fanxiu_player_profile_record(record: FanxiuPlayerProfileRecord) -> dict[str, Any]:
    """Serialize a battle observation without exposing its legacy packet column."""

    payload = dict(record.payload or {})
    payload.update(
        {
            "id": record.id,
            "observation_id": record.packet_id,
            "observed_at": record.captured_at,
            "observed_date": record.captured_date,
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
            "xianlv_team_fight_score_max": record.xianlv_team_fight_score_max,
            "xianlv_team_fight_score_text": record.xianlv_team_fight_score_text,
            "xianlv_team_observed_at": record.xianlv_team_observed_at,
            "attack_value": record.attack_value,
            "attack_text": record.attack_text,
            "special_attributes": record.special_attributes or [],
            "immortal_attributes": record.immortal_attributes or [],
            "combat_attributes": record.combat_attributes or [],
            "attributes": record.attributes or [],
        }
    )
    payload.pop("packet_id", None)
    payload.pop("captured_at", None)
    payload.pop("evidence", None)
    return payload


def ingest_fanxiu_player_battle_observations(
    session: Session,
    observations: list[dict[str, Any]],
) -> dict[str, int]:
    """Persist reliable battle observations from Runtime, packets, or other producers.

    ``packet_id`` remains the physical unique-key column for backwards compatibility;
    callers use the source-neutral ``observation_id`` contract. A valid observation
    requires an explicit role id, at least one positive power metric, parseable
    observation time, source kind, and stable observation id. Attack is useful
    enrichment, not a gate. A producer may retain a proven Xianlv team slot in
    evidence, but the atlas deliberately does not expose or rank by team slot.
    """

    created = 0
    updated = 0
    skipped_invalid = 0
    skipped_duplicate = 0
    for row in observations:
        if not isinstance(row, dict):
            skipped_invalid += 1
            continue
        role_id = str(row.get("role_id_text") or row.get("role_id") or "").strip()
        battle_score = _finite_number(row.get("battle_score"))
        xianlv_team_score = _finite_number(row.get("xianlv_team_fight_score_max"))
        captured_at = str(row.get("observed_at") or row.get("captured_at") or "").strip()
        captured_date = _captured_date(captured_at)
        xianlv_team_observed_at = str(row.get("xianlv_team_observed_at") or captured_at).strip()
        xianlv_team_observed_date = _captured_date(xianlv_team_observed_at) if xianlv_team_score is not None else ""
        source_kind = str(row.get("source_kind") or "").strip()
        observation_id = _observation_id(row)
        if (
            not role_id
            or role_id == "0"
            or not (
                (battle_score is not None and battle_score > 0)
                or (xianlv_team_score is not None and xianlv_team_score > 0)
            )
            or not captured_date
            or (xianlv_team_score is not None and not xianlv_team_observed_date)
            or not source_kind
            or not observation_id
        ):
            skipped_invalid += 1
            continue

        evidence = dict(row.get("evidence") or {}) if isinstance(row.get("evidence"), dict) else {}
        evidence.setdefault("observation_id", observation_id)
        evidence.setdefault("observed_at", captured_at)
        evidence_team_slot = (
            row.get("xianlv_team_slot")
            if isinstance(row.get("xianlv_team_slot"), int) and row.get("xianlv_team_slot") in (1, 2, 3)
            else None
        )
        incoming_xianlv_evidence = None
        if xianlv_team_score is not None:
            incoming_xianlv_evidence = {
                **evidence,
                "score": xianlv_team_score,
                "team_slot": evidence_team_slot,
                "observed_at": xianlv_team_observed_at,
            }
        attack = _profile_attack_attr(row)
        direct_attack = _finite_number(row.get("attack_value"))
        attack_value = direct_attack if direct_attack is not None else (
            _finite_number(attack.get("value")) if attack else None
        )
        attack_text = str(row.get("attack_text") or (attack.get("text") if attack else "") or "")
        protocol = str(row.get("protocol") or evidence.get("protocol") or "")

        existing = session.exec(
            select(FanxiuPlayerProfileRecord).where(FanxiuPlayerProfileRecord.packet_id == observation_id)
        ).first()
        if existing is not None:
            changed = False
            scalar_updates = {
                "protocol": protocol,
                "source_kind": source_kind,
                "role_id": str(row.get("role_id") or role_id),
                "role_id_text": role_id,
                "name": str(row.get("name") or ""),
                "region_name": str(row.get("region_name") or ""),
                "server_name": str(row.get("server_name") or ""),
                "cultivation_level_text": str(row.get("cultivation_level_text") or ""),
                "captured_at": captured_at,
                "captured_date": captured_date,
                "battle_score": battle_score,
                "battle_score_text": str(row.get("battle_score_text") or ""),
            }
            optional_updates = {
                "server": row.get("server") if isinstance(row.get("server"), int) else None,
                "region_number": row.get("region_number") if isinstance(row.get("region_number"), int) else None,
                "server_order": row.get("server_order") if isinstance(row.get("server_order"), int) else None,
                "cultivation_level": row.get("cultivation_level") if isinstance(row.get("cultivation_level"), int) else None,
                "attack_value": attack_value,
                "attack_text": attack_text or None,
            }
            for field, value in scalar_updates.items():
                if value not in (None, "") and getattr(existing, field) != value:
                    setattr(existing, field, value)
                    changed = True
            for field, value in optional_updates.items():
                if value is not None and getattr(existing, field) != value:
                    setattr(existing, field, value)
                    changed = True
            for field in ("special_attributes", "immortal_attributes", "combat_attributes", "attributes"):
                value = row.get(field)
                if isinstance(value, list) and value and getattr(existing, field) != value:
                    setattr(existing, field, value)
                    changed = True
            replace_xianlv = False
            if xianlv_team_score is not None:
                existing_score = _finite_number(existing.xianlv_team_fight_score_max)
                existing_time = str(existing.xianlv_team_observed_at or "")
                replace_xianlv = (
                    existing_score is None
                    or xianlv_team_score > existing_score
                    or (xianlv_team_score == existing_score and xianlv_team_observed_at > existing_time)
                )
            if replace_xianlv:
                existing.xianlv_team_fight_score_max = xianlv_team_score
                existing.xianlv_team_fight_score_text = str(row.get("xianlv_team_fight_score_text") or "")
                existing.xianlv_team_observed_at = xianlv_team_observed_at
                changed = True
            if incoming_xianlv_evidence is not None:
                if replace_xianlv:
                    evidence["xianlv_team"] = incoming_xianlv_evidence
                else:
                    existing_xianlv_evidence = dict(existing.evidence or {}).get("xianlv_team")
                    if existing_xianlv_evidence is not None:
                        evidence["xianlv_team"] = existing_xianlv_evidence
            merged_evidence = {**dict(existing.evidence or {}), **evidence}
            merged_payload = {**dict(existing.payload or {}), **dict(row), "evidence": merged_evidence}
            if existing.evidence != merged_evidence:
                existing.evidence = merged_evidence
                changed = True
            if existing.payload != merged_payload:
                existing.payload = merged_payload
                changed = True
            if changed:
                existing.updated_at = time.time()
                updated += 1
            else:
                skipped_duplicate += 1
            continue

        now = time.time()
        session.add(
            FanxiuPlayerProfileRecord(
                packet_id=observation_id,
                protocol=protocol,
                source_kind=source_kind,
                role_id=str(row.get("role_id") or role_id),
                role_id_text=role_id,
                name=str(row.get("name") or ""),
                server=row.get("server") if isinstance(row.get("server"), int) else None,
                region_number=row.get("region_number") if isinstance(row.get("region_number"), int) else None,
                region_name=str(row.get("region_name") or ""),
                server_order=row.get("server_order") if isinstance(row.get("server_order"), int) else None,
                server_name=str(row.get("server_name") or ""),
                cultivation_level=row.get("cultivation_level") if isinstance(row.get("cultivation_level"), int) else None,
                cultivation_level_text=str(row.get("cultivation_level_text") or ""),
                attack_value=attack_value,
                attack_text=attack_text,
                captured_at=captured_at,
                captured_date=captured_date,
                battle_score=battle_score,
                battle_score_text=str(row.get("battle_score_text") or ""),
                xianlv_team_fight_score_max=xianlv_team_score,
                xianlv_team_fight_score_text=str(row.get("xianlv_team_fight_score_text") or ""),
                xianlv_team_observed_at=xianlv_team_observed_at if xianlv_team_score is not None else "",
                special_attributes=row.get("special_attributes") if isinstance(row.get("special_attributes"), list) else [],
                immortal_attributes=row.get("immortal_attributes") if isinstance(row.get("immortal_attributes"), list) else [],
                combat_attributes=row.get("combat_attributes") if isinstance(row.get("combat_attributes"), list) else [],
                attributes=row.get("attributes") if isinstance(row.get("attributes"), list) else [],
                payload={
                    **dict(row),
                    "evidence": {
                        **evidence,
                        **({"xianlv_team": incoming_xianlv_evidence} if incoming_xianlv_evidence else {}),
                    },
                },
                evidence={
                    **evidence,
                    **({"xianlv_team": incoming_xianlv_evidence} if incoming_xianlv_evidence else {}),
                },
                created_at=now,
                updated_at=now,
            )
        )
        created += 1
    if created or updated:
        session.commit()
    return {
        "created": created,
        "updated": updated,
        "skipped_invalid": skipped_invalid,
        "skipped_duplicate": skipped_duplicate,
    }


def ingest_fanxiu_player_battle_observation(
    session: Session,
    observation: dict[str, Any],
) -> dict[str, int]:
    return ingest_fanxiu_player_battle_observations(session, [observation])


def _records_for_ranked_query(session: Session, query: str, *, limit: int) -> list[dict[str, Any]]:
    id_rows = session.exec(text(query).bindparams(limit=max(1, min(limit, 5000)))).all()
    ids = [str(row if isinstance(row, str) else row[0]) for row in id_rows]
    if not ids:
        return []
    rows = session.exec(select(FanxiuPlayerProfileRecord).where(FanxiuPlayerProfileRecord.id.in_(ids))).all()
    rows_by_id = {row.id: row for row in rows}
    return [serialize_fanxiu_player_profile_record(rows_by_id[row_id]) for row_id in ids if row_id in rows_by_id]


def list_fanxiu_player_profile_records(session: Session, *, limit: int = 1000) -> list[dict[str, Any]]:
    rows = session.exec(
        select(FanxiuPlayerProfileRecord)
        .order_by(FanxiuPlayerProfileRecord.captured_at.desc(), FanxiuPlayerProfileRecord.created_at.desc())
        .limit(max(1, min(limit, 5000)))
    ).all()
    return [serialize_fanxiu_player_profile_record(row) for row in rows]


_DAILY_RANK_CTE = """
WITH normalized AS (
    SELECT
        id,
        COALESCE(NULLIF(role_id_text, ''), NULLIF(role_id, ''), NULLIF(name, ''), packet_id) AS identity_key,
        COALESCE(NULLIF(captured_date, ''), SUBSTR(captured_at, 1, 10)) AS observation_date,
        captured_at,
        created_at,
        updated_at,
        battle_score
    FROM fanxiuplayerprofilerecord
    WHERE battle_score IS NOT NULL AND battle_score > 0
), daily_ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY identity_key, observation_date
            ORDER BY battle_score DESC, captured_at DESC, updated_at DESC, created_at DESC, id DESC
        ) AS daily_rank
    FROM normalized
)
"""


def list_daily_fanxiu_player_profile_records(session: Session, *, limit: int = 5000) -> list[dict[str, Any]]:
    return _records_for_ranked_query(
        session,
        _DAILY_RANK_CTE
        + """
        SELECT id
        FROM daily_ranked
        WHERE daily_rank = 1
        ORDER BY observation_date DESC, battle_score DESC, captured_at DESC, id DESC
        LIMIT :limit
        """,
        limit=limit,
    )


def list_latest_fanxiu_player_profile_records(session: Session, *, limit: int = 1000) -> list[dict[str, Any]]:
    return _records_for_ranked_query(
        session,
        _DAILY_RANK_CTE
        + """
        , daily AS (
            SELECT *
            FROM daily_ranked
            WHERE daily_rank = 1
        ), latest_ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY identity_key
                    ORDER BY observation_date DESC, battle_score DESC, captured_at DESC, updated_at DESC, created_at DESC, id DESC
                ) AS latest_rank
            FROM daily
        )
        SELECT id
        FROM latest_ranked
        WHERE latest_rank = 1
        ORDER BY battle_score DESC, captured_at DESC, identity_key ASC, id ASC
        LIMIT :limit
        """,
        limit=limit,
    )


_XIANLV_DAILY_RANK_CTE = """
WITH normalized AS (
    SELECT
        id,
        COALESCE(NULLIF(role_id_text, ''), NULLIF(role_id, ''), NULLIF(name, ''), packet_id) AS identity_key,
        SUBSTR(COALESCE(NULLIF(xianlv_team_observed_at, ''), captured_at), 1, 10) AS observation_date,
        COALESCE(NULLIF(xianlv_team_observed_at, ''), captured_at) AS metric_observed_at,
        created_at,
        updated_at,
        xianlv_team_fight_score_max
    FROM fanxiuplayerprofilerecord
    WHERE xianlv_team_fight_score_max IS NOT NULL AND xianlv_team_fight_score_max > 0
), daily_ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY identity_key, observation_date
            ORDER BY xianlv_team_fight_score_max DESC, metric_observed_at DESC,
                     updated_at DESC, created_at DESC, id DESC
        ) AS daily_rank
    FROM normalized
)
"""


def list_daily_fanxiu_player_xianlv_team_records(
    session: Session,
    *,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    return _records_for_ranked_query(
        session,
        _XIANLV_DAILY_RANK_CTE
        + """
        SELECT id
        FROM daily_ranked
        WHERE daily_rank = 1
        ORDER BY observation_date DESC, xianlv_team_fight_score_max DESC,
                 metric_observed_at DESC, id DESC
        LIMIT :limit
        """,
        limit=limit,
    )


def list_latest_fanxiu_player_xianlv_team_records(
    session: Session,
    *,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    return _records_for_ranked_query(
        session,
        _XIANLV_DAILY_RANK_CTE
        + """
        , daily AS (
            SELECT *
            FROM daily_ranked
            WHERE daily_rank = 1
        ), latest_ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY identity_key
                    ORDER BY observation_date DESC, xianlv_team_fight_score_max DESC,
                             metric_observed_at DESC,
                             updated_at DESC, created_at DESC, id DESC
                ) AS latest_rank
            FROM daily
        )
        SELECT id
        FROM latest_ranked
        WHERE latest_rank = 1
        ORDER BY metric_observed_at DESC, xianlv_team_fight_score_max DESC, id DESC
        LIMIT :limit
        """,
        limit=limit,
    )
