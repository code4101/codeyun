"""Historical packet adapter for the active player battle-observation store."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.player_profiles import (
    ingest_fanxiu_player_battle_observations,
    list_daily_fanxiu_player_profile_records,
    list_fanxiu_player_profile_records,
    list_latest_fanxiu_player_profile_records,
    serialize_fanxiu_player_profile_record,
)

_REAL_PROFILE_PCAP_PREFIXES = (
    "fanxiu_runtime_",
    "fanxiu_live_",
    "fanxiu_windows_rank",
)


def _has_real_capture_evidence(row: dict[str, Any]) -> bool:
    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        return False
    packet_id = str(evidence.get("packet_id") or row.get("packet_id") or "").strip()
    record_id = str(evidence.get("record_id") or "").strip()
    pcap_name = str(evidence.get("pcap_name") or "").strip()
    return bool(
        packet_id
        and record_id
        and pcap_name
        and pcap_name.startswith(_REAL_PROFILE_PCAP_PREFIXES)
    )


def upsert_fanxiu_player_profile_rows(session: Session, rows: list[dict[str, Any]]) -> dict[str, int]:
    """Validate retired packet evidence, then use the source-neutral active sink."""

    accepted: list[dict[str, Any]] = []
    rejected = 0
    for row in rows:
        if not isinstance(row, dict) or not _has_real_capture_evidence(row):
            rejected += 1
            continue
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        accepted.append(
            {
                **row,
                "observation_id": str(evidence.get("packet_id") or row.get("packet_id") or ""),
                "source_kind": str(row.get("source_kind") or "packet_player_profile"),
            }
        )
    result = ingest_fanxiu_player_battle_observations(session, accepted)
    result["skipped_invalid"] += rejected
    return result


__all__ = [
    "list_daily_fanxiu_player_profile_records",
    "list_fanxiu_player_profile_records",
    "list_latest_fanxiu_player_profile_records",
    "serialize_fanxiu_player_profile_record",
    "upsert_fanxiu_player_profile_rows",
]
