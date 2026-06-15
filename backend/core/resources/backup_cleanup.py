from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import delete, or_
from sqlmodel import Session, select

from backend.core.notes.refs import note_public_id, note_ref_aliases
from backend.core.resources.identity import RESOURCE_TYPE_NOTE, RESOURCE_TYPE_SHEET, RESOURCE_TYPE_WORKBOOK
from backend.core.settings import get_settings
from backend.core.resources.sheet_refs import (
    sheet_public_id,
    sheet_ref_aliases,
    workbook_public_id,
    workbook_ref_aliases,
)
from backend.core.resources.storage_usage import collect_directory_usage
from backend.models import (
    AppSetting,
    NoteEdge,
    NoteNode,
    ResourceAccessGrant,
    SheetDocument,
    WorkbookDocument,
    WorkbookSheetLink,
)


MiB = 1024 * 1024
GiB = 1024 * MiB
DEFAULT_RESOURCE_BACKUP_MAX_STORAGE_BYTES = 10 * GiB
RESOURCE_BACKUP_STORAGE_POLICY_SETTING_KEY = "resource_backup.storage_policy"


@dataclass(frozen=True)
class ResourceBackupStoragePolicy:
    cleanup_enabled: bool = True
    max_storage_bytes: int = DEFAULT_RESOURCE_BACKUP_MAX_STORAGE_BYTES
    trash_grace_seconds: int = 24 * 60 * 60
    batch_limit: int = 500
    vacuum_sqlite: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def trash_grace_days(self) -> float:
        return self.trash_grace_seconds / 86400


@dataclass(frozen=True)
class TrashCleanupCandidate:
    kind: str
    id: str
    public_id: str
    title: str
    deleted_at: float
    estimated_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _coerce_int(value: Any, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        result = int(float(str(value).strip()))
    except (TypeError, ValueError):
        result = default
    result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _coerce_seconds_from_days(value: Any, default_seconds: int) -> int:
    try:
        days = float(str(value).strip())
    except (TypeError, ValueError):
        return default_seconds
    return max(0, int(days * 86400))


def _parse_size_bytes(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return max(0, int(value))

    text_value = str(value).strip()
    if not text_value:
        return default

    match = re.fullmatch(r"(?i)\s*(\d+(?:\.\d+)?)\s*([kmgtp]?i?b?|bytes?)?\s*", text_value)
    if not match:
        return default

    number = float(match.group(1))
    unit = (match.group(2) or "b").lower()
    multipliers = {
        "": 1,
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": MiB,
        "mb": MiB,
        "mib": MiB,
        "g": GiB,
        "gb": GiB,
        "gib": GiB,
        "t": 1024 * GiB,
        "tb": 1024 * GiB,
        "tib": 1024 * GiB,
        "p": 1024 * 1024 * GiB,
        "pb": 1024 * 1024 * GiB,
        "pib": 1024 * 1024 * GiB,
    }
    return max(0, int(number * multipliers.get(unit, 1)))


def _default_resource_backup_storage_policy() -> ResourceBackupStoragePolicy:
    return ResourceBackupStoragePolicy(
        cleanup_enabled=_coerce_bool(os.getenv("CODEYUN_RESOURCE_BACKUP_CLEANUP_ENABLED"), True),
        max_storage_bytes=_parse_size_bytes(
            os.getenv("CODEYUN_RESOURCE_BACKUP_MAX_STORAGE_SIZE")
            or os.getenv("CODEYUN_RESOURCE_BACKUP_MAX_STORAGE_BYTES"),
            DEFAULT_RESOURCE_BACKUP_MAX_STORAGE_BYTES,
        ),
        trash_grace_seconds=_coerce_seconds_from_days(
            os.getenv("CODEYUN_RESOURCE_BACKUP_TRASH_GRACE_DAYS"),
            24 * 60 * 60,
        ),
        batch_limit=_coerce_int(os.getenv("CODEYUN_RESOURCE_BACKUP_CLEANUP_BATCH_LIMIT"), 500, minimum=1),
        vacuum_sqlite=_coerce_bool(os.getenv("CODEYUN_RESOURCE_BACKUP_VACUUM_SQLITE"), True),
    )


def _normalize_resource_backup_storage_policy(value: dict[str, Any] | None = None) -> ResourceBackupStoragePolicy:
    default_policy = _default_resource_backup_storage_policy()
    payload = dict(value or {})
    return ResourceBackupStoragePolicy(
        cleanup_enabled=_coerce_bool(payload.get("cleanup_enabled"), default_policy.cleanup_enabled),
        max_storage_bytes=_parse_size_bytes(payload.get("max_storage_bytes"), default_policy.max_storage_bytes),
        trash_grace_seconds=_coerce_int(
            payload.get("trash_grace_seconds"),
            default_policy.trash_grace_seconds,
            minimum=0,
        ),
        batch_limit=_coerce_int(payload.get("batch_limit"), default_policy.batch_limit, minimum=1, maximum=10000),
        vacuum_sqlite=_coerce_bool(payload.get("vacuum_sqlite"), default_policy.vacuum_sqlite),
    )


def load_resource_backup_storage_policy(session: Session | None = None) -> ResourceBackupStoragePolicy:
    def _read(current_session: Session) -> ResourceBackupStoragePolicy:
        row = current_session.get(AppSetting, RESOURCE_BACKUP_STORAGE_POLICY_SETTING_KEY)
        if row and isinstance(row.value, dict):
            return _normalize_resource_backup_storage_policy(row.value)
        return _default_resource_backup_storage_policy()

    if session is not None:
        return _read(session)

    from backend.db import engine

    with Session(engine) as current_session:
        return _read(current_session)


def save_resource_backup_storage_policy(
    session: Session,
    updates: dict[str, Any],
) -> ResourceBackupStoragePolicy:
    current = load_resource_backup_storage_policy(session).to_dict()
    if "trash_grace_days" in updates and "trash_grace_seconds" not in updates:
        updates = {
            **updates,
            "trash_grace_seconds": _coerce_seconds_from_days(updates.get("trash_grace_days"), current["trash_grace_seconds"]),
        }
    next_policy = _normalize_resource_backup_storage_policy({**current, **updates})

    row = session.get(AppSetting, RESOURCE_BACKUP_STORAGE_POLICY_SETTING_KEY)
    if row is None:
        row = AppSetting(key=RESOURCE_BACKUP_STORAGE_POLICY_SETTING_KEY)
    row.value = next_policy.to_dict()
    row.updated_at = time.time()
    session.add(row)
    session.commit()
    return next_policy


def _json_size(value: Any) -> int:
    try:
        text_value = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        text_value = str(value)
    return len(text_value.encode("utf-8"))


def _note_estimated_bytes(note: NoteNode) -> int:
    return max(
        512,
        len(str(note.title or "").encode("utf-8"))
        + len(str(note.content or "").encode("utf-8"))
        + _json_size(note.note_types)
        + _json_size(note.note_categories)
        + _json_size(note.history)
        + _json_size(note.custom_fields)
        + 512,
    )


def _sheet_estimated_bytes(sheet: SheetDocument) -> int:
    return max(
        512,
        len(str(sheet.title or "").encode("utf-8"))
        + _json_size(sheet.document_json)
        + 512,
    )


def _workbook_estimated_bytes(workbook: WorkbookDocument) -> int:
    return max(512, len(str(workbook.title or "").encode("utf-8")) + 512)


def _collect_trash_cleanup_candidates(
    session: Session,
    *,
    older_than: float,
    limit: int,
) -> list[TrashCleanupCandidate]:
    candidates: list[TrashCleanupCandidate] = []

    notes = session.exec(
        select(NoteNode)
        .where(NoteNode.deleted_at > 0)
        .where(NoteNode.deleted_at <= older_than)
        .order_by(NoteNode.deleted_at.asc(), NoteNode.updated_at.asc())
        .limit(limit)
    ).all()
    for note in notes:
        candidates.append(
            TrashCleanupCandidate(
                kind="note",
                id=str(note.id or ""),
                public_id=note_public_id(note),
                title=note.title or "Untitled",
                deleted_at=float(note.deleted_at or 0),
                estimated_bytes=_note_estimated_bytes(note),
            )
        )

    workbooks = session.exec(
        select(WorkbookDocument)
        .where(WorkbookDocument.deleted_at > 0)
        .where(WorkbookDocument.deleted_at <= older_than)
        .order_by(WorkbookDocument.deleted_at.asc(), WorkbookDocument.updated_at.asc())
        .limit(limit)
    ).all()
    for workbook in workbooks:
        candidates.append(
            TrashCleanupCandidate(
                kind="workbook",
                id=str(workbook.id or ""),
                public_id=workbook_public_id(workbook),
                title=workbook.title or "未命名工作簿",
                deleted_at=float(workbook.deleted_at or 0),
                estimated_bytes=_workbook_estimated_bytes(workbook),
            )
        )

    sheets = session.exec(
        select(SheetDocument)
        .where(SheetDocument.deleted_at > 0)
        .where(SheetDocument.deleted_at <= older_than)
        .order_by(SheetDocument.deleted_at.asc(), SheetDocument.updated_at.asc())
        .limit(limit)
    ).all()
    for sheet in sheets:
        candidates.append(
            TrashCleanupCandidate(
                kind="sheet",
                id=str(sheet.id or ""),
                public_id=sheet_public_id(sheet),
                title=sheet.title or "未命名表格",
                deleted_at=float(sheet.deleted_at or 0),
                estimated_bytes=_sheet_estimated_bytes(sheet),
            )
        )

    candidates.sort(key=lambda item: (item.deleted_at, item.kind, item.public_id))
    return candidates[:limit]


def _delete_resource_access_grants(session: Session, *, resource_type: str, resource_ids: list[str]) -> None:
    normalized_ids = [str(resource_id or "").strip() for resource_id in resource_ids]
    normalized_ids = [resource_id for resource_id in normalized_ids if resource_id]
    if not normalized_ids:
        return
    session.exec(
        delete(ResourceAccessGrant)
        .where(ResourceAccessGrant.resource_type == resource_type)
        .where(ResourceAccessGrant.resource_id.in_(normalized_ids))
    )


def _purge_note(session: Session, note_id: str) -> bool:
    note = session.get(NoteNode, note_id)
    if note is None or not note.deleted_at:
        return False

    refs = sorted(note_ref_aliases(note))
    if refs:
        session.exec(
            delete(NoteEdge).where(
                NoteEdge.user_id == note.user_id,
                or_(NoteEdge.source_id.in_(refs), NoteEdge.target_id.in_(refs)),
            )
        )
    _delete_resource_access_grants(
        session,
        resource_type=RESOURCE_TYPE_NOTE,
        resource_ids=[note_public_id(note), str(note.id or ""), str(note.legacy_id or "")],
    )
    session.delete(note)
    return True


def _purge_sheet(session: Session, sheet_id: str) -> bool:
    sheet = session.get(SheetDocument, sheet_id)
    if sheet is None or not sheet.deleted_at:
        return False

    refs = sorted(sheet_ref_aliases(sheet))
    if refs:
        session.exec(delete(WorkbookSheetLink).where(WorkbookSheetLink.sheet_id.in_(refs)))
    _delete_resource_access_grants(
        session,
        resource_type=RESOURCE_TYPE_SHEET,
        resource_ids=[sheet_public_id(sheet), str(sheet.id or ""), str(sheet.legacy_id or "")],
    )
    session.delete(sheet)
    return True


def _purge_workbook(session: Session, workbook_id: str) -> bool:
    workbook = session.get(WorkbookDocument, workbook_id)
    if workbook is None or not workbook.deleted_at:
        return False

    refs = sorted(workbook_ref_aliases(workbook))
    if refs:
        session.exec(delete(WorkbookSheetLink).where(WorkbookSheetLink.workbook_id.in_(refs)))
    _delete_resource_access_grants(
        session,
        resource_type=RESOURCE_TYPE_WORKBOOK,
        resource_ids=[workbook_public_id(workbook), str(workbook.id or ""), str(workbook.legacy_id or "")],
    )
    session.delete(workbook)
    return True


def _purge_candidate(session: Session, candidate: TrashCleanupCandidate) -> bool:
    if candidate.kind == "note":
        return _purge_note(session, candidate.id)
    if candidate.kind == "sheet":
        return _purge_sheet(session, candidate.id)
    if candidate.kind == "workbook":
        return _purge_workbook(session, candidate.id)
    return False


def _collect_workspace_usage_bytes() -> int:
    usage = collect_directory_usage(get_settings().data_workspace_dir, top_limit=0)
    return int(usage.allocated_size_bytes or usage.logical_size_bytes or 0)


def _is_sqlite_database() -> bool:
    return get_settings().database_url.startswith("sqlite")


def _run_sqlite_vacuum() -> None:
    if not _is_sqlite_database():
        return

    from backend.db import engine

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").close()
        connection.exec_driver_sql("VACUUM")


def run_resource_backup_storage_cleanup(
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    from backend.db import engine

    now = time.time()
    with Session(engine) as session:
        policy = load_resource_backup_storage_policy(session)
        usage_before = _collect_workspace_usage_bytes()
        max_storage_bytes = int(policy.max_storage_bytes or 0)
        bytes_over_limit = max(0, usage_before - max_storage_bytes) if max_storage_bytes > 0 else 0
        should_cleanup = (
            (force or (policy.cleanup_enabled and bytes_over_limit > 0))
            and max_storage_bytes > 0
        )
        older_than = now - max(0, int(policy.trash_grace_seconds or 0))
        candidates = _collect_trash_cleanup_candidates(
            session,
            older_than=older_than,
            limit=max(1, int(policy.batch_limit or 1)),
        )

        result: dict[str, Any] = {
            "cleanup_enabled": policy.cleanup_enabled,
            "dry_run": bool(dry_run),
            "force": bool(force),
            "max_storage_bytes": max_storage_bytes,
            "trash_grace_seconds": policy.trash_grace_seconds,
            "usage_before_bytes": usage_before,
            "usage_after_bytes": usage_before,
            "bytes_over_limit_before": bytes_over_limit,
            "candidate_count": len(candidates),
            "estimated_candidate_bytes": sum(candidate.estimated_bytes for candidate in candidates),
            "purged_count": 0,
            "purged_estimated_bytes": 0,
            "purged_by_kind": {"note": 0, "sheet": 0, "workbook": 0},
            "purged_items": [],
            "vacuum_ran": False,
            "skipped_reason": "",
        }

        if not policy.cleanup_enabled and not force:
            result["skipped_reason"] = "cleanup_disabled"
            return result
        if max_storage_bytes <= 0:
            result["skipped_reason"] = "max_storage_disabled"
            return result
        if bytes_over_limit <= 0 and not force:
            result["skipped_reason"] = "under_limit"
            return result
        if not candidates:
            result["skipped_reason"] = "no_eligible_trash"
            return result

        target_reclaim = bytes_over_limit if not force else sum(candidate.estimated_bytes for candidate in candidates)
        selected: list[TrashCleanupCandidate] = []
        selected_estimated_bytes = 0
        for candidate in candidates:
            selected.append(candidate)
            selected_estimated_bytes += max(1, int(candidate.estimated_bytes or 1))
            if not force and selected_estimated_bytes >= target_reclaim:
                break

        result["purged_estimated_bytes"] = selected_estimated_bytes
        result["purged_items"] = [candidate.to_dict() for candidate in selected[:50]]

        if dry_run:
            result["skipped_reason"] = "dry_run"
            return result

        for candidate in selected:
            if _purge_candidate(session, candidate):
                result["purged_count"] += 1
                result["purged_by_kind"][candidate.kind] += 1
        session.commit()

    if result["purged_count"] > 0 and policy.vacuum_sqlite and _is_sqlite_database():
        _run_sqlite_vacuum()
        result["vacuum_ran"] = True

    result["usage_after_bytes"] = _collect_workspace_usage_bytes()
    return result
