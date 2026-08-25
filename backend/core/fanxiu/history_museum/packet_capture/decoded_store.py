import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete, func, or_
from sqlmodel import Session, select

from backend.core.fanxiu.history_museum.packet_capture.tcp_flow import resolve_fanxiu_tcp_store_root
from backend.models import FanxiuPacketDecodedRecord

DECODED_RECORD_BACKLOG_SCHEMA_VERSION = 1
DEFAULT_DECODED_RECORD_RETENTION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_DECODED_RECORD_RETENTION_MIN_KEEP = 200


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _parse_time_text(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _decoded_record_epoch(record: FanxiuPacketDecodedRecord) -> float:
    parsed = _parse_time_text(record.captured_at)
    if parsed is not None:
        return parsed.timestamp()
    return float(record.updated_at or record.created_at or 0.0)


def fanxiu_packet_decoded_record_to_dict(record: FanxiuPacketDecodedRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "packet_id": record.packet_id,
        "record_id": record.record_id,
        "pcap_name": record.pcap_name,
        "capture_sha256": record.capture_sha256,
        "stream": record.stream,
        "direction": record.direction,
        "frame_index": record.frame_index,
        "offset": record.offset,
        "sn": record.sn,
        "pro_id": record.pro_id,
        "name": record.name,
        "captured_at": record.captured_at,
        "captured_date": record.captured_date,
        "payload_len": record.payload_len,
        "decode_error": record.decode_error,
        "payload": record.payload,
        "evidence": record.evidence,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def decoded_record_rows_from_decode_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    frames = result.get("frames") if isinstance(result.get("frames"), list) else []
    record_id = _text(result.get("record_id"))
    pcap_name = _text(result.get("pcap_name") or result.get("pcap"))
    if "\\" in pcap_name or "/" in pcap_name:
        pcap_name = pcap_name.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    # `captured_at` is the game-event time, not the time an old pcap happened
    # to be decoded.  Keeping those dimensions separate prevents historical
    # maintenance from making stale packets look like fresh live facts.
    captured_at = _text(result.get("pcap_modified_at"))
    if not captured_at:
        captured_at = _text(result.get("captured_at") or result.get("created_at") or result.get("decoded_at"))
    decoded_at = _text(result.get("created_at") or result.get("decoded_at"))
    stream = int(result.get("stream") or 0)
    rows: list[dict[str, Any]] = []
    direction_counts: dict[str, int] = {}
    for fallback_index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            continue
        direction = _text(frame.get("direction"))
        frame_index = direction_counts.get(direction, 0)
        direction_counts[direction] = frame_index + 1
        packet_id = "|".join(
            _text(part)
            for part in (
                record_id,
                direction,
                frame.get("offset"),
                frame.get("pro_id"),
                frame.get("sn"),
            )
        )
        if not record_id:
            packet_id = "|".join(
                _text(part)
                for part in (
                    result.get("capture_sha256"),
                    stream,
                    direction,
                    frame.get("offset"),
                    frame.get("pro_id"),
                    frame.get("sn"),
                    fallback_index,
                )
            )
        payload = dict(frame)
        evidence = {
            "packet_id": packet_id,
            "record_id": record_id,
            "pcap_name": pcap_name,
            "capture_sha256": _text(result.get("capture_sha256")),
            "stream": stream,
            "direction": direction,
            "frame_index": frame_index,
            "offset": frame.get("offset"),
            "sn": frame.get("sn"),
            "pro_id": frame.get("pro_id"),
            "protocol": _text(frame.get("name")),
            "decoded_at": decoded_at,
            "captured_at": captured_at,
            "decoded_path": _text(result.get("stored_decoded_path") or result.get("output_path")),
            "stored_pcap": _text(result.get("stored_pcap")),
            "source_pcap": _text(result.get("pcap") or result.get("source_pcap")),
        }
        rows.append(
            {
                "packet_id": packet_id,
                "record_id": record_id,
                "pcap_name": pcap_name,
                "capture_sha256": _text(result.get("capture_sha256")),
                "stream": stream,
                "direction": direction,
                "frame_index": frame_index,
                "offset": _int_or_none(frame.get("offset")),
                "sn": _int_or_none(frame.get("sn")),
                "pro_id": _int_or_none(frame.get("pro_id")),
                "name": _text(frame.get("name")),
                "captured_at": captured_at,
                "payload_len": _int_or_none(frame.get("payload_len")),
                "decode_error": _text(frame.get("decode_error")),
                "payload": payload,
                "evidence": evidence,
            }
        )
    return rows


def upsert_fanxiu_packet_decoded_records(session: Session, rows: list[dict[str, Any]]) -> dict[str, int]:
    created = 0
    updated = 0
    skipped_invalid = 0
    skipped_duplicate = 0
    packet_ids = [
        packet_id
        for row in rows
        if isinstance(row, dict)
        and (packet_id := _text(row.get("packet_id")).strip())
    ]
    existing_by_packet_id: dict[str, FanxiuPacketDecodedRecord] = {}
    # 先批量读取已有记录，避免逐条 select 触发 SQLAlchemy autoflush。
    # payload/evidence 较大时，旧写法会在每次查询前重复 flush，数百条记录可拖到数分钟。
    for start in range(0, len(packet_ids), 500):
        batch = packet_ids[start : start + 500]
        existing_by_packet_id.update(
            {
                record.packet_id: record
                for record in session.exec(
                    select(FanxiuPacketDecodedRecord).where(
                        FanxiuPacketDecodedRecord.packet_id.in_(batch)
                    )
                ).all()
            }
        )
    for row in rows:
        if not isinstance(row, dict):
            skipped_invalid += 1
            continue
        packet_id = _text(row.get("packet_id")).strip()
        if not packet_id:
            skipped_invalid += 1
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        captured_at = _text(row.get("captured_at"))
        now = time.time()
        existing = existing_by_packet_id.get(packet_id)
        if existing:
            # Decode time is operational provenance, not packet identity. A
            # retry of the same preserved capture must not rewrite every JSON
            # row merely because it happened later; on a multi-gigabyte
            # SQLite store that creates a large WAL/checkpoint burst.
            existing_evidence = dict(existing.evidence or {})
            incoming_evidence = dict(evidence)
            existing_evidence.pop("decoded_at", None)
            incoming_evidence.pop("decoded_at", None)
            if (
                existing.payload == payload
                and existing_evidence == incoming_evidence
                and existing.captured_at == captured_at
            ):
                skipped_duplicate += 1
                continue
            existing.record_id = _text(row.get("record_id"))
            existing.pcap_name = _text(row.get("pcap_name"))
            existing.capture_sha256 = _text(row.get("capture_sha256"))
            existing.stream = int(row.get("stream") or 0)
            existing.direction = _text(row.get("direction"))
            existing.frame_index = int(row.get("frame_index") or 0)
            existing.offset = _int_or_none(row.get("offset"))
            existing.sn = _int_or_none(row.get("sn"))
            existing.pro_id = _int_or_none(row.get("pro_id"))
            existing.name = _text(row.get("name"))
            existing.captured_at = captured_at
            existing.captured_date = captured_at[:10]
            existing.payload_len = _int_or_none(row.get("payload_len"))
            existing.decode_error = _text(row.get("decode_error"))
            existing.payload = payload
            existing.evidence = evidence
            existing.updated_at = now
            updated += 1
            continue
        record = FanxiuPacketDecodedRecord(
            packet_id=packet_id,
            record_id=_text(row.get("record_id")),
            pcap_name=_text(row.get("pcap_name")),
            capture_sha256=_text(row.get("capture_sha256")),
            stream=int(row.get("stream") or 0),
            direction=_text(row.get("direction")),
            frame_index=int(row.get("frame_index") or 0),
            offset=_int_or_none(row.get("offset")),
            sn=_int_or_none(row.get("sn")),
            pro_id=_int_or_none(row.get("pro_id")),
            name=_text(row.get("name")),
            captured_at=captured_at,
            captured_date=captured_at[:10],
            payload_len=_int_or_none(row.get("payload_len")),
            decode_error=_text(row.get("decode_error")),
            payload=payload,
            evidence=evidence,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        existing_by_packet_id[packet_id] = record
        created += 1
    if created or updated:
        session.commit()
    return {"created": created, "updated": updated, "skipped_invalid": skipped_invalid, "skipped_duplicate": skipped_duplicate}


def list_fanxiu_packet_decoded_records(
    session: Session,
    *,
    names: list[str] | None = None,
    pro_ids: list[int] | None = None,
    since_seconds: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query = select(FanxiuPacketDecodedRecord)
    normalized_names = [str(item).strip() for item in (names or []) if str(item).strip()]
    normalized_pro_ids = [int(item) for item in (pro_ids or []) if item is not None]
    if normalized_names:
        query = query.where(FanxiuPacketDecodedRecord.name.in_(normalized_names))
    if normalized_pro_ids:
        query = query.where(FanxiuPacketDecodedRecord.pro_id.in_(normalized_pro_ids))
    if since_seconds and since_seconds > 0:
        threshold = datetime.now() - timedelta(seconds=int(since_seconds))
        query = query.where(FanxiuPacketDecodedRecord.captured_at >= threshold.strftime("%Y-%m-%d %H:%M:%S"))
    rows = session.exec(
        query.order_by(
            FanxiuPacketDecodedRecord.captured_at.desc(),
            FanxiuPacketDecodedRecord.updated_at.desc(),
        ).limit(max(1, min(int(limit), 500)))
    ).all()
    return {
        "ok": True,
        "count": len(rows),
        "records": [fanxiu_packet_decoded_record_to_dict(row) for row in rows],
    }


def prune_fanxiu_packet_decoded_records(
    session: Session,
    *,
    max_age_seconds: int = DEFAULT_DECODED_RECORD_RETENTION_SECONDS,
    min_keep: int = DEFAULT_DECODED_RECORD_RETENTION_MIN_KEEP,
) -> dict[str, Any]:
    # Never hydrate packet payload/evidence JSON merely to decide retention.
    # A mature local store can hold millions of large rows; ORM-loading them
    # turns a tiny DELETE into a multi-gigabyte daemon memory spike.
    scanned = int(
        session.exec(
            select(func.count()).select_from(FanxiuPacketDecodedRecord)
        ).one()
        or 0
    )
    keep_count = max(0, int(min_keep))
    if max_age_seconds <= 0 or scanned <= keep_count:
        return {
            "ok": True,
            "scanned": scanned,
            "deleted": 0,
            "kept": scanned,
            "max_age_seconds": max_age_seconds,
        }
    threshold_epoch = time.time() - int(max_age_seconds)
    threshold_text = datetime.fromtimestamp(threshold_epoch).strftime("%Y-%m-%d %H:%M:%S")
    stale_condition = or_(
        and_(
            FanxiuPacketDecodedRecord.captured_at != "",
            FanxiuPacketDecodedRecord.captured_at < threshold_text,
        ),
        and_(
            FanxiuPacketDecodedRecord.captured_at == "",
            func.coalesce(
                FanxiuPacketDecodedRecord.updated_at,
                FanxiuPacketDecodedRecord.created_at,
                0.0,
            )
            < threshold_epoch,
        ),
    )
    if keep_count:
        keep_ids = (
            select(FanxiuPacketDecodedRecord.id)
            .order_by(
                FanxiuPacketDecodedRecord.captured_at.desc(),
                FanxiuPacketDecodedRecord.updated_at.desc(),
            )
            .limit(keep_count)
        )
        stale_condition = and_(
            stale_condition,
            FanxiuPacketDecodedRecord.id.not_in(keep_ids),
        )
    session.exec(delete(FanxiuPacketDecodedRecord).where(stale_condition))
    kept = int(
        session.exec(
            select(func.count()).select_from(FanxiuPacketDecodedRecord)
        ).one()
        or 0
    )
    deleted = scanned - kept
    if deleted:
        session.commit()
    return {
        "ok": True,
        "scanned": scanned,
        "deleted": deleted,
        "kept": kept,
        "max_age_seconds": max_age_seconds,
        "min_keep": min_keep,
    }


def persist_fanxiu_packet_decoded_result(result: dict[str, Any]) -> dict[str, int]:
    rows = decoded_record_rows_from_decode_result(result)
    if not rows:
        return {"created": 0, "updated": 0, "skipped_invalid": 0, "skipped_duplicate": 0}
    from backend.db import engine

    with Session(engine) as session:
        return upsert_fanxiu_packet_decoded_records(session, rows)


def _load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _decoded_record_backlog_state_path(data_dir: str | Path | None = None) -> Path:
    return resolve_fanxiu_tcp_store_root(data_dir).parent / "packet-insights" / "decoded_record_db_state.json"


def _decode_result_from_meta(meta_path: Path) -> dict[str, Any] | None:
    meta = _load_json(meta_path, {})
    if not isinstance(meta, dict):
        return None
    decoded_path = Path(str(meta.get("decoded_path") or meta_path.parent / "decoded.json"))
    if not decoded_path.is_file():
        return None
    decoded = _load_json(decoded_path, {})
    if not isinstance(decoded, dict):
        return None
    decoded.update(
        {
            "record_id": meta.get("record_id") or meta_path.parent.name,
            "created_at": meta.get("created_at") or "",
            "pcap_name": meta.get("pcap_name") or "",
            "pcap_modified_at": meta.get("pcap_modified_at") or "",
            "capture_sha256": meta.get("capture_sha256") or decoded.get("capture_sha256") or "",
            "stream": int(meta.get("stream") or decoded.get("stream") or 0),
            "stored_pcap": meta.get("stored_pcap") or "",
            "source_pcap": meta.get("source_pcap") or decoded.get("pcap") or "",
            "stored_decoded_path": str(decoded_path),
            "meta_path": str(meta_path),
        }
    )
    return decoded


def sync_fanxiu_decoded_record_backlog(
    *,
    data_dir: str | Path | None = None,
    limit: int = 16,
) -> dict[str, Any]:
    root = resolve_fanxiu_tcp_store_root(data_dir)
    state_path = _decoded_record_backlog_state_path(data_dir)
    state = _load_json(state_path, {})
    if not isinstance(state, dict) or int(state.get("schema_version") or 0) != DECODED_RECORD_BACKLOG_SCHEMA_VERSION:
        state = {}
    processed = {
        str(item)
        for item in state.get("processed_record_ids", [])
        if str(item)
    }
    scanned = 0
    persisted: list[dict[str, Any]] = []
    skipped = 0
    errors: list[dict[str, Any]] = []
    meta_paths = sorted(root.glob("*/meta.json"), key=lambda item: item.stat().st_mtime, reverse=True) if root.is_dir() else []
    for meta_path in meta_paths:
        if scanned >= max(1, int(limit)):
            break
        record_id = meta_path.parent.name
        if record_id in processed:
            skipped += 1
            continue
        scanned += 1
        try:
            result = _decode_result_from_meta(meta_path)
            if result is None:
                processed.add(record_id)
                skipped += 1
                continue
            db_sync = persist_fanxiu_packet_decoded_result(result)
            processed.add(record_id)
            persisted.append(
                {
                    "record_id": record_id,
                    "decoded_path": str(result.get("stored_decoded_path") or ""),
                    "db_sync": db_sync,
                }
            )
        except Exception as exc:
            errors.append({"record_id": record_id, "meta_path": str(meta_path), "error": str(exc)})
    payload = {
        "ok": not errors,
        "schema_version": DECODED_RECORD_BACKLOG_SCHEMA_VERSION,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scanned": scanned,
        "persisted_count": len(persisted),
        "skipped_count": skipped,
        "error_count": len(errors),
        "persisted": persisted[-20:],
        "errors": errors[-20:],
        "processed_record_ids": sorted(processed),
    }
    _write_json(state_path, payload)
    return {key: value for key, value in payload.items() if key != "processed_record_ids"}
