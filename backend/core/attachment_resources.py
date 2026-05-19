from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path

from sqlmodel import Session, select

from backend.core.device_files import ensure_device_file_resource_identity
from backend.core.storage import get_attachments_dir
from backend.models import DeviceFile


def resolve_attachment_media_kind(mime_type: str | None) -> str:
    normalized = str(mime_type or "").strip().lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("video/"):
        return "video"
    if normalized == "application/pdf":
        return "pdf"
    return "attachment"


def resolve_attachment_device_id() -> str:
    from backend.core.device import get_device_id

    return get_device_id()


def index_attachment_file_resource(
    session: Session,
    file_path: str | Path,
    *,
    device_id: str | None = None,
    mime_type: str | None = None,
) -> DeviceFile:
    path = Path(file_path).resolve(strict=False)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(os.fspath(path))

    if device_id is None:
        device_id = resolve_attachment_device_id()
    normalized_device_id = str(device_id or "").strip()
    absolute_path = os.fspath(path)
    stat_result = path.stat()
    resolved_mime_type = mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    now = time.time()

    record = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == normalized_device_id,
            DeviceFile.absolute_path == absolute_path,
        )
    ).first()
    if record is None:
        record = DeviceFile(
            device_id=normalized_device_id,
            absolute_path=absolute_path,
            created_at=now,
        )

    record.last_known_path = absolute_path
    record.file_size = stat_result.st_size
    record.modified_at_ms = int(stat_result.st_mtime * 1000)
    record.media_kind = resolve_attachment_media_kind(resolved_mime_type)
    record.mime_type = resolved_mime_type
    record.match_status = "matched"
    record.updated_at = now
    record.last_seen_at = now

    session.add(record)
    session.flush()
    ensure_device_file_resource_identity(session, record)
    return record


def index_existing_attachment_resources(session: Session) -> int:
    attachments_dir = get_attachments_dir()
    count = 0
    for path in sorted(attachments_dir.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        index_attachment_file_resource(session, path)
        count += 1
    session.commit()
    return count
