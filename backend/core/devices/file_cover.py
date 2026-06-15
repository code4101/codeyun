from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from sqlmodel import Session, select

from backend.core.devices.files import DeviceFileSyncSnapshot, ensure_device_file_resource_identity, reconcile_device_file_batch
from backend.core.settings import get_settings
from backend.models import DeviceFile


DEVICE_COVER_DIR_NAME = "device_covers"
DEVICE_COVER_MAX_EDGE = 960
DEVICE_COVER_JPEG_QUALITY = 86


@dataclass(frozen=True)
class DeviceFileMetadataSnapshot:
    absolute_path: str
    last_known_path: str
    file_size: int | None
    modified_at_ms: int | None
    content_hash: str | None
    hash_algorithm: str
    visual_hash: str | None
    visual_hash_algorithm: str
    duration_ms: int | None
    width_px: int | None
    height_px: int | None
    media_kind: str | None
    mime_type: str | None


def get_device_cover_dir() -> Path:
    cover_dir = get_settings().data_dir / DEVICE_COVER_DIR_NAME
    cover_dir.mkdir(parents=True, exist_ok=True)
    return cover_dir


def build_device_cover_relative_path(device_id: str, absolute_path: str) -> str:
    digest = hashlib.sha256(f"{device_id}\0{absolute_path}".encode("utf-8")).hexdigest()
    return f"{DEVICE_COVER_DIR_NAME}/{digest}.jpg"


def resolve_device_cover_path(relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    return get_settings().data_dir / relative_path


def ensure_device_file_record(session: Session, device_id: str, absolute_path: str) -> DeviceFile:
    record = session.exec(
        select(DeviceFile).where(
            DeviceFile.device_id == device_id,
            DeviceFile.absolute_path == absolute_path,
        )
    ).first()
    if record:
        return record

    now = time.time()
    record = DeviceFile(
        device_id=device_id,
        absolute_path=absolute_path,
        last_known_path=absolute_path,
        match_status="matched",
        created_at=now,
        updated_at=now,
        last_seen_at=now,
    )
    session.add(record)
    session.flush()
    ensure_device_file_resource_identity(session, record)
    return record


def upsert_device_file_metadata_batch(
    session: Session,
    device_id: str,
    snapshots: list[DeviceFileMetadataSnapshot],
) -> list[DeviceFile]:
    sync_snapshots = []
    for snapshot in snapshots:
        normalized_absolute = (snapshot.absolute_path or "").strip()
        if not normalized_absolute:
            continue
        sync_snapshots.append(
            DeviceFileSyncSnapshot(
                absolute_path=normalized_absolute,
                last_known_path=(snapshot.last_known_path or normalized_absolute).strip() or normalized_absolute,
                content_hash=(snapshot.content_hash or "").strip() or None,
                hash_algorithm=(snapshot.hash_algorithm or "sha256").strip() or "sha256",
                visual_hash=(snapshot.visual_hash or "").strip() or None,
                visual_hash_algorithm=(snapshot.visual_hash_algorithm or "dhash-8").strip() or "dhash-8",
                file_size=snapshot.file_size,
                modified_at_ms=snapshot.modified_at_ms,
                duration_ms=snapshot.duration_ms,
                width_px=snapshot.width_px,
                height_px=snapshot.height_px,
                media_kind=(snapshot.media_kind or "").strip() or None,
                mime_type=(snapshot.mime_type or "").strip() or None,
            )
        )

    if not sync_snapshots:
        return []

    result = reconcile_device_file_batch(
        session,
        device_id,
        sync_snapshots,
        mark_missing_as_dangling=False,
    )
    return result.records


def normalize_cover_image_bytes(
    image_bytes: bytes,
    *,
    max_edge: int = DEVICE_COVER_MAX_EDGE,
    quality: int = DEVICE_COVER_JPEG_QUALITY,
) -> tuple[bytes, str]:
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    with Image.open(io.BytesIO(image_bytes)) as source_image:
        normalized_image = ImageOps.exif_transpose(source_image).convert("RGB")
        normalized_image.thumbnail((max_edge, max_edge), resampling)
        output = io.BytesIO()
        normalized_image.save(
            output,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )
    return output.getvalue(), "image/jpeg"


def save_device_cover(
    session: Session,
    device_id: str,
    absolute_path: str,
    image_bytes: bytes,
    *,
    source: str,
) -> DeviceFile:
    record = ensure_device_file_record(session, device_id, absolute_path)
    normalized_bytes, mime_type = normalize_cover_image_bytes(image_bytes)

    relative_path = build_device_cover_relative_path(device_id, absolute_path)
    cover_path = resolve_device_cover_path(relative_path)
    assert cover_path is not None
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    cover_path.write_bytes(normalized_bytes)

    now = time.time()
    record.last_known_path = absolute_path
    record.last_seen_at = now
    record.updated_at = now
    record.cover_path = relative_path
    record.cover_mime_type = mime_type
    record.cover_source = source
    record.cover_updated_at = now
    session.add(record)
    session.commit()
    session.refresh(record)
    return record
