import hashlib
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from backend.db import engine
from backend.models import FanxiuMailRecord

_TABLE_LOCK = threading.Lock()
_TABLE_READY = False


def normalize_fanxiu_mail_title(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", "", text)
    return text.strip()


def normalize_fanxiu_mail_time_text(value: Any) -> str:
    text = str(value or "").replace("：", ":")
    text = re.sub(r"已阅|已读|未阅", "", text)
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", text)
    if not match:
        match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})", text)
    if not match:
        return ""
    year, month, day, hour, minute = (int(part) for part in match.groups())
    return f"{year:04d}年{month:02d}月{day:02d}日{hour:02d}:{minute:02d}"


def format_fanxiu_mail_time_ms(value: Any) -> str:
    try:
        timestamp = int(value) / 1000
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y年%m月%d日%H:%M")


def build_fanxiu_mail_key(
    *,
    mail_id: Any = "",
    title: Any = "",
    create_time_text: Any = "",
    create_time_ms: Any = None,
    mail_type: Any = "",
    source_hint: Any = "",
) -> str:
    mail_id_text = str(mail_id or "").strip()
    if mail_id_text:
        return f"id:{mail_id_text}"
    title_text = normalize_fanxiu_mail_title(title)
    create_text = str(create_time_ms if create_time_ms is not None else normalize_fanxiu_mail_time_text(create_time_text)).strip()
    type_text = str(mail_type or "").strip()
    digest = hashlib.sha1("|".join([title_text, create_text, type_text]).encode("utf-8")).hexdigest()
    return f"weak:{digest}"


def _mail_source_rank(value: Any) -> int:
    source = str(value or "").strip().lower()
    if source == "packet":
        return 3
    if source == "packet_orphan_action":
        return 2
    if source == "gui":
        return 2
    return 1


def _mail_status_rank(value: Any) -> int:
    raw_status = str(value or "").strip()
    if raw_status == "锁定":
        return 3
    if raw_status == "留存":
        return 2
    if raw_status == "可领":
        return 2
    status = raw_status.lower()
    if status == "claimed":
        return 5
    if status == "deleted":
        return 5
    if status == "missing_from_list":
        return 4
    if status in {"claim_requested", "delete_requested"}:
        return 3
    if status == "seen":
        return 2
    return 1


def _is_unknown_fanxiu_mail_title(value: Any) -> bool:
    return bool(re.fullmatch(r"未知邮件(?:类型|动作)\d+", normalize_fanxiu_mail_title(value)))


def _find_existing_mail_record(
    session: Session,
    *,
    mail_key: str,
    mail_id: Any,
    normalized_title: str,
    normalized_create_time_text: str,
) -> FanxiuMailRecord | None:
    existing = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_key == mail_key)).first()
    if existing:
        return existing
    mail_id_text = str(mail_id or "").strip()
    if mail_id_text:
        weak_matches = session.exec(
            select(FanxiuMailRecord).where(
                FanxiuMailRecord.mail_id == "",
                FanxiuMailRecord.normalized_title == normalized_title,
                FanxiuMailRecord.create_time_text == normalized_create_time_text,
            )
        ).all()
        if weak_matches:
            return sorted(weak_matches, key=lambda row: float(row.last_seen_at or row.updated_at or 0), reverse=True)[0]
        return None
    title_time_matches = session.exec(
        select(FanxiuMailRecord).where(
            FanxiuMailRecord.normalized_title == normalized_title,
            FanxiuMailRecord.create_time_text == normalized_create_time_text,
        )
    ).all()
    if not title_time_matches:
        return None
    return sorted(
        title_time_matches,
        key=lambda row: (
            _mail_source_rank(row.source),
            1 if str(row.mail_id or "").strip() else 0,
            _mail_status_rank(row.status),
            float(row.last_seen_at or row.updated_at or 0),
        ),
        reverse=True,
    )[0]


def _merge_mail_record_fields(
    record: FanxiuMailRecord,
    *,
    mail_key: str,
    title: str,
    normalized_title: str,
    mail_id: Any,
    mail_type: Any,
    normalized_create_time_text: str,
    create_time_ms: int | None,
    source: str,
    action_policy: str,
    status: str,
    locked: bool | None,
    payload: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    seen_capture_at: str,
    now: float,
) -> None:
    source_text = str(source or "").strip()
    current_source = str(record.source or "").strip()
    incoming_source_wins = _mail_source_rank(source_text) >= _mail_source_rank(current_source)
    if record.mail_key.startswith("weak:") and mail_key.startswith("id:"):
        record.mail_key = mail_key
    incoming_title = str(title or "").strip()
    if incoming_title and not (
        _is_unknown_fanxiu_mail_title(incoming_title) and record.title and not _is_unknown_fanxiu_mail_title(record.title)
    ):
        record.title = incoming_title
        record.normalized_title = normalized_title or record.normalized_title
    record.mail_id = str(mail_id or record.mail_id or "")
    record.mail_type = str(mail_type or record.mail_type or "")
    record.create_time_text = str(normalized_create_time_text or record.create_time_text or "")
    record.create_time_ms = create_time_ms if create_time_ms is not None else record.create_time_ms
    record.source = source_text if incoming_source_wins else current_source
    if source_text == "packet":
        record.action_policy = str(action_policy or "")
    elif action_policy:
        record.action_policy = action_policy
    incoming_status = str(status or "").strip()
    current_status = str(record.status or "").strip()
    if incoming_status in {"锁定", "留存", "可领"}:
        if current_status not in {"锁定", "留存", "可领"}:
            record.status = incoming_status
    elif incoming_status and current_status not in {"锁定", "留存", "可领"} and _mail_status_rank(incoming_status) >= _mail_status_rank(record.status):
        record.status = status
    if locked is not None:
        record.locked = bool(locked)
    record.seen_count = int(record.seen_count or 0) + 1
    record.last_seen_at = now
    record.last_seen_capture_at = str(seen_capture_at or record.last_seen_capture_at or "")
    if payload:
        existing_payload = dict(record.payload or {})
        if source_text == "packet":
            for key in ("mailVo", "mail_rewards", "mail_rewards_summary", "mail_content_text"):
                if key in payload:
                    existing_payload[key] = payload[key]
        existing_payload[source_text or "unknown"] = payload
        record.payload = existing_payload
    if evidence:
        merged = dict(record.evidence or {})
        source_key = source_text or "unknown"
        evidence_items = list(merged.get("sources") or [])
        evidence_items.append({source_key: evidence})
        merged["sources"] = evidence_items[-12:]
        merged.update(evidence)
        record.evidence = merged
    record.updated_at = now


def ensure_fanxiu_mail_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    with _TABLE_LOCK:
        if _TABLE_READY:
            return
        SQLModel.metadata.create_all(engine, tables=[FanxiuMailRecord.__table__])
        with Session(engine) as session:
            columns = {str(row[1]) for row in session.exec(text("PRAGMA table_info(fanxiumailrecord)")).all()}
            if "locked" not in columns:
                session.exec(text("ALTER TABLE fanxiumailrecord ADD COLUMN locked BOOLEAN NOT NULL DEFAULT 0"))
                session.exec(text("CREATE INDEX IF NOT EXISTS ix_fanxiumailrecord_locked ON fanxiumailrecord (locked)"))
                session.commit()
        _TABLE_READY = True


def upsert_fanxiu_mail_fact(
    session: Session,
    *,
    title: str,
    mail_id: Any = "",
    mail_type: Any = "",
    create_time_text: str = "",
    create_time_ms: int | None = None,
    source: str = "gui",
    action_policy: str = "",
    status: str = "seen",
    locked: bool | None = None,
    payload: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    seen_capture_at: str = "",
) -> tuple[FanxiuMailRecord, bool]:
    ensure_fanxiu_mail_table()
    normalized_title = normalize_fanxiu_mail_title(title)
    normalized_create_time_text = normalize_fanxiu_mail_time_text(create_time_text)
    mail_key = build_fanxiu_mail_key(
        mail_id=mail_id,
        title=normalized_title,
        create_time_text=normalized_create_time_text,
        create_time_ms=create_time_ms,
        mail_type=mail_type,
        source_hint=source,
    )
    now = time.time()
    existing = _find_existing_mail_record(
        session,
        mail_key=mail_key,
        mail_id=mail_id,
        normalized_title=normalized_title,
        normalized_create_time_text=normalized_create_time_text,
    )
    if existing:
        _merge_mail_record_fields(
            existing,
            mail_key=mail_key,
            title=title,
            normalized_title=normalized_title,
            mail_id=mail_id,
            mail_type=mail_type,
            normalized_create_time_text=normalized_create_time_text,
            create_time_ms=create_time_ms,
            source=source,
            action_policy=action_policy,
            status=status,
            locked=locked,
            payload=payload,
            evidence=evidence,
            seen_capture_at=seen_capture_at,
            now=now,
        )
        session.add(existing)
        return existing, False
    record = FanxiuMailRecord(
        mail_key=mail_key,
        mail_id=str(mail_id or ""),
        title=str(title or ""),
        normalized_title=normalized_title,
        mail_type=str(mail_type or ""),
        create_time_text=str(normalized_create_time_text or ""),
        create_time_ms=create_time_ms,
        source=str(source or ""),
        status=str(status or "留存"),
        locked=bool(locked) if locked is not None else False,
        action_policy=str(action_policy or ""),
        seen_count=1,
        first_seen_at=now,
        last_seen_at=now,
        last_seen_capture_at=str(seen_capture_at or ""),
        payload=payload or {},
        evidence=evidence or {},
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    return record, True


def mark_fanxiu_mail_action(
    session: Session,
    mail_key: str,
    *,
    status: str,
    error: str = "",
    evidence: dict[str, Any] | None = None,
) -> bool:
    ensure_fanxiu_mail_table()
    record = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_key == mail_key)).first()
    if not record:
        return False
    if status and _mail_status_rank(status) >= _mail_status_rank(record.status):
        record.status = status
    record.last_action_error = error
    if evidence:
        merged = dict(record.evidence or {})
        merged.update(evidence)
        record.evidence = merged
    record.updated_at = time.time()
    session.add(record)
    return True


def update_fanxiu_mail_desired_status(
    session: Session,
    mail_key: str,
    *,
    desired_status: str,
) -> FanxiuMailRecord | None:
    ensure_fanxiu_mail_table()
    status_text = str(desired_status or "").strip()
    if status_text not in {"锁定", "留存", "可领"}:
        raise ValueError("invalid_mail_desired_status")
    record = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_key == mail_key)).first()
    if not record:
        return None
    record.status = status_text
    record.locked = status_text == "锁定"
    record.action_policy = "claim" if status_text == "可领" else ""
    record.last_action_error = ""
    record.updated_at = time.time()
    session.add(record)
    return record


def _fanxiu_mail_record_time_ms(record: FanxiuMailRecord) -> int | None:
    if record.create_time_ms is not None:
        try:
            return int(record.create_time_ms)
        except (TypeError, ValueError):
            pass
    normalized = normalize_fanxiu_mail_time_text(record.create_time_text)
    if not normalized:
        return None
    try:
        return int(datetime.strptime(normalized, "%Y年%m月%d日%H:%M").timestamp() * 1000)
    except ValueError:
        return None


def parse_fanxiu_mail_time_text_ms(value: Any) -> int | None:
    normalized = normalize_fanxiu_mail_time_text(value)
    if not normalized:
        return None
    try:
        return int(datetime.strptime(normalized, "%Y年%m月%d日%H:%M").timestamp() * 1000)
    except ValueError:
        return None


def align_fanxiu_mail_records_claimable_between_times(
    session: Session,
    *,
    newer_time_text: str,
    older_time_text: str,
    source: str = "visible_mail_adjacency",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Mark user-managed records in an observed empty open interval as claimable.

    The interval is open: records exactly at either visible boundary are not touched.
    """

    ensure_fanxiu_mail_table()
    newer_ms = parse_fanxiu_mail_time_text_ms(newer_time_text)
    older_ms = parse_fanxiu_mail_time_text_ms(older_time_text)
    if newer_ms is None or older_ms is None or newer_ms <= older_ms:
        return {
            "ok": False,
            "reason": "invalid_or_non_descending_interval",
            "newer_time_text": normalize_fanxiu_mail_time_text(newer_time_text),
            "older_time_text": normalize_fanxiu_mail_time_text(older_time_text),
            "matched": 0,
            "updated": 0,
            "records": [],
        }
    rows = session.exec(
        select(FanxiuMailRecord).where(
            FanxiuMailRecord.source.in_(("packet", "packet_orphan_action")),
            FanxiuMailRecord.status.in_(("锁定", "留存", "seen")),
        )
    ).all()
    matched: list[FanxiuMailRecord] = []
    newer_normalized = normalize_fanxiu_mail_time_text(newer_time_text)
    older_normalized = normalize_fanxiu_mail_time_text(older_time_text)
    for row in rows:
        row_time_text = normalize_fanxiu_mail_time_text(row.create_time_text)
        if row_time_text in {newer_normalized, older_normalized}:
            continue
        row_ms = _fanxiu_mail_record_time_ms(row)
        if row_ms is None:
            continue
        if older_ms < row_ms < newer_ms:
            matched.append(row)
    now = time.time()
    source_text = str(source or "visible_mail_adjacency").strip() or "visible_mail_adjacency"
    records: list[dict[str, Any]] = []
    updated = 0
    for row in sorted(matched, key=lambda item: (_fanxiu_mail_record_time_ms(item) or 0, item.mail_key), reverse=True):
        previous_status = str(row.status or "")
        records.append(
            {
                "mail_key": row.mail_key,
                "mail_id": row.mail_id,
                "title": row.title,
                "create_time_text": row.create_time_text,
                "previous_status": previous_status,
            }
        )
        if dry_run:
            continue
        evidence = dict(row.evidence or {})
        history = [item for item in evidence.get("visible_adjacency_claimable_alignment_history") or [] if isinstance(item, dict)]
        history.append(
            {
                "source": source_text,
                "newer_time_text": normalize_fanxiu_mail_time_text(newer_time_text),
                "older_time_text": normalize_fanxiu_mail_time_text(older_time_text),
                "previous_status": previous_status,
                "aligned_at": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        evidence["visible_adjacency_claimable_alignment"] = history[-1]
        evidence["visible_adjacency_claimable_alignment_history"] = history[-12:]
        row.status = "可领"
        row.locked = False
        row.action_policy = "claim"
        row.last_action_error = ""
        row.evidence = evidence
        row.updated_at = now
        session.add(row)
        updated += 1
    return {
        "ok": True,
        "newer_time_text": normalize_fanxiu_mail_time_text(newer_time_text),
        "older_time_text": normalize_fanxiu_mail_time_text(older_time_text),
        "matched": len(matched),
        "updated": updated,
        "records": records,
    }


def mark_fanxiu_mail_locked(
    session: Session,
    mail_id: Any,
    *,
    locked: bool,
    evidence: dict[str, Any] | None = None,
) -> bool:
    ensure_fanxiu_mail_table()
    mail_id_text = str(mail_id or "").strip()
    if not mail_id_text:
        return False
    record = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == mail_id_text)).first()
    if not record:
        return False
    record.locked = bool(locked)
    if evidence:
        merged = dict(record.evidence or {})
        merged["mail_lock"] = evidence
        record.evidence = merged
    record.updated_at = time.time()
    session.add(record)
    return True


def clear_fanxiu_mail_records(session: Session) -> int:
    ensure_fanxiu_mail_table()
    records = session.exec(select(FanxiuMailRecord)).all()
    for record in records:
        session.delete(record)
    return len(records)


def backup_fanxiu_mail_records(session: Session, path: str | Path) -> dict[str, Any]:
    ensure_fanxiu_mail_table()
    records = session.exec(select(FanxiuMailRecord)).all()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": len(records),
        "records": [record.model_dump() for record in records],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(target), "count": len(records)}


def merge_duplicate_fanxiu_mail_records(session: Session) -> dict[str, Any]:
    ensure_fanxiu_mail_table()
    rows = session.exec(select(FanxiuMailRecord)).all()
    groups: dict[tuple[str, str], list[FanxiuMailRecord]] = {}
    for row in rows:
        key = (str(row.normalized_title or "").strip(), str(row.create_time_text or "").strip())
        if not key[0] or not key[1]:
            continue
        groups.setdefault(key, []).append(row)
    merged = 0
    deleted = 0
    for group_rows in groups.values():
        weak_rows = [row for row in group_rows if not str(row.mail_id or "").strip()]
        packet_rows = [row for row in group_rows if str(row.mail_id or "").strip()]
        if not weak_rows or not packet_rows:
            continue
        target = sorted(
            packet_rows,
            key=lambda row: (
                _mail_source_rank(row.source),
                _mail_status_rank(row.status),
                float(row.last_seen_at or row.updated_at or 0),
            ),
            reverse=True,
        )[0]
        for weak in weak_rows:
            evidence = dict(target.evidence or {})
            duplicate_evidence = list(evidence.get("merged_duplicates") or [])
            duplicate_evidence.append(weak.model_dump())
            evidence["merged_duplicates"] = duplicate_evidence[-12:]
            target.evidence = evidence
            target.seen_count = int(target.seen_count or 0) + int(weak.seen_count or 0)
            target.first_seen_at = min(float(target.first_seen_at or weak.first_seen_at or time.time()), float(weak.first_seen_at or time.time()))
            target.last_seen_at = max(float(target.last_seen_at or 0), float(weak.last_seen_at or 0))
            target.updated_at = max(float(target.updated_at or 0), float(weak.updated_at or 0), time.time())
            session.delete(weak)
            deleted += 1
            merged += 1
        session.add(target)
    return {"ok": True, "merged": merged, "deleted": deleted}
