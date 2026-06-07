import json
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from backend.core.fanxiu_tcp_flow import resolve_fanxiu_tcp_store_root
from backend.models import FanxiuPacketDecodedRecord

DECODED_RECORD_BACKLOG_SCHEMA_VERSION = 1


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


def decoded_record_rows_from_decode_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    frames = result.get("frames") if isinstance(result.get("frames"), list) else []
    record_id = _text(result.get("record_id"))
    pcap_name = _text(result.get("pcap_name") or result.get("pcap"))
    if "\\" in pcap_name or "/" in pcap_name:
        pcap_name = pcap_name.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    captured_at = _text(result.get("created_at"))
    if not captured_at:
        captured_at = _text(result.get("decoded_at") or result.get("pcap_modified_at"))
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
            "decoded_at": captured_at,
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
        existing = session.exec(
            select(FanxiuPacketDecodedRecord).where(FanxiuPacketDecodedRecord.packet_id == packet_id)
        ).first()
        if existing:
            if existing.payload == payload and existing.evidence == evidence and existing.captured_at == captured_at:
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
        session.add(
            FanxiuPacketDecodedRecord(
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
        )
        created += 1
    if created or updated:
        session.commit()
    return {"created": created, "updated": updated, "skipped_invalid": skipped_invalid, "skipped_duplicate": skipped_duplicate}


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
