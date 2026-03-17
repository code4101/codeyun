from __future__ import annotations

import time
from dataclasses import dataclass

from sqlmodel import Session, select

from backend.models import DeviceFile


MATCH_STATUS_MATCHED = "matched"
MATCH_STATUS_DANGLING = "dangling"


@dataclass(frozen=True)
class DeviceFileSyncSnapshot:
    absolute_path: str
    last_known_path: str | None = None
    content_hash: str | None = None
    hash_algorithm: str = "sha256"
    file_size: int | None = None
    modified_at_ms: int | None = None
    duration_ms: int | None = None
    width_px: int | None = None
    height_px: int | None = None
    media_kind: str | None = None
    mime_type: str | None = None
    weight: int | None = None


@dataclass(frozen=True)
class DeviceFileReconcileResult:
    processed_count: int
    created_count: int
    rebound_count: int
    updated_count: int
    dangling_count: int
    records: list[DeviceFile]


def _normalize_snapshot(snapshot: DeviceFileSyncSnapshot) -> DeviceFileSyncSnapshot:
    absolute_path = (snapshot.absolute_path or "").strip()
    if not absolute_path:
        raise ValueError("absolute_path is required")

    last_known_path = (snapshot.last_known_path or absolute_path).strip() or absolute_path
    content_hash = (snapshot.content_hash or "").strip() or None
    hash_algorithm = (snapshot.hash_algorithm or "sha256").strip().lower() or "sha256"
    media_kind = (snapshot.media_kind or "").strip() or None
    mime_type = (snapshot.mime_type or "").strip() or None

    return DeviceFileSyncSnapshot(
        absolute_path=absolute_path,
        last_known_path=last_known_path,
        content_hash=content_hash,
        hash_algorithm=hash_algorithm,
        file_size=snapshot.file_size,
        modified_at_ms=snapshot.modified_at_ms,
        duration_ms=snapshot.duration_ms,
        width_px=snapshot.width_px,
        height_px=snapshot.height_px,
        media_kind=media_kind,
        mime_type=mime_type,
        weight=snapshot.weight,
    )


def _should_invalidate_auto_cover(record: DeviceFile, snapshot: DeviceFileSyncSnapshot) -> bool:
    if record.cover_source != "auto":
        return False

    if record.absolute_path != snapshot.absolute_path:
        return False

    same_hash = bool(
        record.content_hash
        and snapshot.content_hash
        and record.hash_algorithm == snapshot.hash_algorithm
        and record.content_hash == snapshot.content_hash
    )

    if (
        record.content_hash
        and snapshot.content_hash
        and record.hash_algorithm == snapshot.hash_algorithm
        and record.content_hash != snapshot.content_hash
    ):
        return True

    if (
        record.file_size is not None
        and snapshot.file_size is not None
        and record.file_size != snapshot.file_size
    ):
        return True

    if (
        record.modified_at_ms is not None
        and snapshot.modified_at_ms is not None
        and record.modified_at_ms != snapshot.modified_at_ms
        and not same_hash
    ):
        return True

    return False


def _mark_record_dangling(record: DeviceFile, *, now: float) -> None:
    if record.absolute_path:
        record.last_known_path = record.absolute_path
    record.absolute_path = None
    record.match_status = MATCH_STATUS_DANGLING
    record.updated_at = now


def _apply_snapshot_to_record(
    record: DeviceFile,
    snapshot: DeviceFileSyncSnapshot,
    *,
    now: float,
) -> None:
    previous_modified_at_ms = record.modified_at_ms
    invalidate_auto_cover = _should_invalidate_auto_cover(record, snapshot)

    record.absolute_path = snapshot.absolute_path
    record.last_known_path = snapshot.last_known_path
    record.file_size = snapshot.file_size
    record.modified_at_ms = snapshot.modified_at_ms
    record.duration_ms = snapshot.duration_ms
    record.width_px = snapshot.width_px
    record.height_px = snapshot.height_px
    record.media_kind = snapshot.media_kind
    record.mime_type = snapshot.mime_type
    record.match_status = MATCH_STATUS_MATCHED
    record.last_seen_at = now
    record.updated_at = now

    if snapshot.weight is not None:
        record.weight = snapshot.weight

    if snapshot.content_hash:
        record.content_hash = snapshot.content_hash
        record.hash_algorithm = snapshot.hash_algorithm
        record.hash_updated_at = now
    elif (
        record.content_hash
        and previous_modified_at_ms is not None
        and snapshot.modified_at_ms is not None
        and previous_modified_at_ms != snapshot.modified_at_ms
    ):
        # The file changed, but this sync skipped hashing. Drop the stale hash so a
        # future rematch won't trust outdated content identity.
        record.content_hash = None
        record.hash_updated_at = None

    if invalidate_auto_cover:
        record.cover_path = None
        record.cover_mime_type = None
        record.cover_source = None
        record.cover_updated_at = None


def _select_active_path_record(session: Session, device_id: str, absolute_path: str) -> DeviceFile | None:
    return session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == device_id,
            DeviceFile.absolute_path == absolute_path,
        )
    ).first()


def _select_same_path_dangling_record(session: Session, device_id: str, absolute_path: str) -> DeviceFile | None:
    candidates = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == device_id,
            DeviceFile.absolute_path.is_(None),
            DeviceFile.last_known_path == absolute_path,
        )
    ).all()
    if not candidates:
        return None

    candidates.sort(key=lambda item: item.updated_at, reverse=True)
    return candidates[0]


def _select_same_path_dangling_candidate(
    session: Session,
    device_id: str,
    snapshot: DeviceFileSyncSnapshot,
    *,
    exclude_ids: set[int],
) -> DeviceFile | None:
    candidates = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == device_id,
            DeviceFile.absolute_path.is_(None),
            DeviceFile.last_known_path == snapshot.absolute_path,
        )
    ).all()

    filtered = [record for record in candidates if record.id not in exclude_ids]
    if not filtered:
        return None

    if snapshot.content_hash:
        for record in filtered:
            if (
                record.content_hash
                and record.hash_algorithm == snapshot.hash_algorithm
                and record.content_hash == snapshot.content_hash
            ):
                return record

        size_only_candidates = [
            record
            for record in filtered
            if not record.content_hash
            and snapshot.file_size is not None
            and record.file_size == snapshot.file_size
        ]
        if size_only_candidates:
            size_only_candidates.sort(key=lambda item: item.updated_at, reverse=True)
            return size_only_candidates[0]

        return None

    if snapshot.file_size is not None:
        for record in filtered:
            if record.file_size == snapshot.file_size:
                return record

    if snapshot.content_hash is None:
        filtered.sort(key=lambda item: item.updated_at, reverse=True)
        return filtered[0]

    return None


def _select_hash_dangling_candidate(
    session: Session,
    device_id: str,
    snapshot: DeviceFileSyncSnapshot,
    *,
    exclude_ids: set[int],
) -> DeviceFile | None:
    if not snapshot.content_hash:
        return None

    candidates = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == device_id,
            DeviceFile.absolute_path.is_(None),
            DeviceFile.content_hash == snapshot.content_hash,
            DeviceFile.hash_algorithm == snapshot.hash_algorithm,
        )
    ).all()

    filtered = [record for record in candidates if record.id not in exclude_ids]
    if not filtered:
        return None

    filtered.sort(key=lambda item: item.updated_at, reverse=True)
    return filtered[0]


def _select_hash_active_candidate(
    active_candidates: list[DeviceFile],
    snapshot: DeviceFileSyncSnapshot,
    *,
    exclude_ids: set[int],
    seen_paths: set[str],
) -> DeviceFile | None:
    if not snapshot.content_hash:
        return None

    filtered = [
        record
        for record in active_candidates
        if record.id not in exclude_ids
        and record.absolute_path
        and record.absolute_path not in seen_paths
    ]
    if not filtered:
        return None

    matching = [
        record
        for record in filtered
        if record.content_hash
        and record.hash_algorithm == snapshot.hash_algorithm
        and record.content_hash == snapshot.content_hash
    ]
    if not matching:
        return None

    matching.sort(key=lambda item: item.updated_at, reverse=True)
    return matching[0]


def _path_is_within_scope(path: str, scope_prefixes: list[str]) -> bool:
    normalized_path = (path or "").strip()
    if not normalized_path:
        return False

    for raw_prefix in scope_prefixes:
        prefix = (raw_prefix or "").strip().rstrip("/\\")
        if not prefix:
            continue
        if normalized_path == prefix:
            return True
        if normalized_path.startswith(prefix + "/") or normalized_path.startswith(prefix + "\\"):
            return True

    return False


def reconcile_device_file_batch(
    session: Session,
    device_id: str,
    snapshots: list[DeviceFileSyncSnapshot],
    *,
    mark_missing_as_dangling: bool = False,
    scope_prefixes: list[str] | None = None,
) -> DeviceFileReconcileResult:
    normalized_snapshots = [_normalize_snapshot(snapshot) for snapshot in snapshots]
    seen_paths: set[str] = {snapshot.absolute_path for snapshot in normalized_snapshots}
    normalized_scope_prefixes = [
        (prefix or "").strip().rstrip("/\\")
        for prefix in (scope_prefixes or [])
        if (prefix or "").strip().rstrip("/\\")
    ]

    if mark_missing_as_dangling and not normalized_scope_prefixes:
        raise ValueError("scope_prefixes is required when mark_missing_as_dangling is true")

    active_scope_candidates: list[DeviceFile] = []
    if normalized_scope_prefixes:
        active_scope_candidates = [
            record
            for record in session.exec(
                select(DeviceFile).where(
                    DeviceFile.device_id == device_id,
                    DeviceFile.absolute_path.is_not(None),
                )
            ).all()
            if record.absolute_path and _path_is_within_scope(record.absolute_path, normalized_scope_prefixes)
        ]

    processed_records: list[DeviceFile] = []
    created_count = 0
    rebound_count = 0
    updated_count = 0

    for snapshot in normalized_snapshots:
        now = time.time()
        target = _select_active_path_record(session, device_id, snapshot.absolute_path)

        if target is None:
            target = _select_same_path_dangling_candidate(
                session,
                device_id,
                snapshot,
                exclude_ids=set(),
            )

        if target is None:
            target = _select_hash_dangling_candidate(
                session,
                device_id,
                snapshot,
                exclude_ids=set(),
            )

        if target is None and active_scope_candidates:
            target = _select_hash_active_candidate(
                active_scope_candidates,
                snapshot,
                exclude_ids=set(),
                seen_paths=seen_paths,
            )

        previous_path = target.absolute_path if target else None
        if target is None:
            target = DeviceFile(
                device_id=device_id,
                created_at=now,
            )
            created_count += 1
        elif previous_path != snapshot.absolute_path:
            rebound_count += 1
        else:
            updated_count += 1

        _apply_snapshot_to_record(target, snapshot, now=now)
        session.add(target)
        session.flush()
        processed_records.append(target)

    dangling_count = 0
    if mark_missing_as_dangling:
        active_records = session.exec(
            select(DeviceFile).where(DeviceFile.device_id == device_id)
        ).all()
        now = time.time()
        for record in active_records:
            active_path = (record.absolute_path or "").strip()
            if not active_path or active_path in seen_paths:
                continue
            if not _path_is_within_scope(active_path, normalized_scope_prefixes):
                continue
            _mark_record_dangling(record, now=now)
            session.add(record)
            dangling_count += 1

    session.commit()
    return DeviceFileReconcileResult(
        processed_count=len(normalized_snapshots),
        created_count=created_count,
        rebound_count=rebound_count,
        updated_count=updated_count,
        dangling_count=dangling_count,
        records=processed_records,
    )


def update_device_file_weight(
    session: Session,
    device_id: str,
    absolute_path: str,
    *,
    weight: int,
) -> DeviceFile:
    normalized_absolute_path = (absolute_path or "").strip()
    if not normalized_absolute_path:
        raise ValueError("absolute_path is required")

    now = time.time()
    record = _select_active_path_record(session, device_id, normalized_absolute_path)
    if record is None:
        record = _select_same_path_dangling_record(session, device_id, normalized_absolute_path)

    if record is None:
        record = DeviceFile(
            device_id=device_id,
            created_at=now,
        )

    record.absolute_path = normalized_absolute_path
    record.last_known_path = normalized_absolute_path
    record.match_status = MATCH_STATUS_MATCHED
    record.weight = int(weight)
    record.last_seen_at = now
    record.updated_at = now

    session.add(record)
    session.commit()
    session.refresh(record)
    return record
