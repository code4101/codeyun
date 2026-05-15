from __future__ import annotations

import csv
import fnmatch
import hashlib
import io
import json
import math
import mimetypes
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cmp_to_key
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Iterator, List, Literal, Optional
from uuid import uuid4

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from backend.core.device import get_device_id
from backend.core.device_files import (
    DeviceFileSyncSnapshot,
    reconcile_device_file_batch,
    update_device_file_weight,
)
from backend.core.ocr_preview import (
    OcrPreviewError,
    OcrShapeType,
    run_paddle_ocr_preview,
)
from backend.core.settings import ROOT_DIR, get_settings
from backend.db import engine, get_session
from backend.models import DeviceFile

router = APIRouter()

IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}

VIDEO_EXTENSIONS = {
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogv",
    ".webm",
}

PDF_EXTENSIONS = {
    ".pdf",
}

DEVICE_ROOT_SENTINEL = "__device_root__"
MEDIA_LISTING_SNAPSHOT_LIMIT = 32
MEDIA_LISTING_SNAPSHOT_TTL_SECONDS = 30 * 60
DEFAULT_MEDIA_SCAN_LIMIT = 2000
MAX_MEDIA_SCAN_LIMIT = 50000
DEFAULT_DUPLICATE_SCAN_LIMIT = 200000
MAX_DUPLICATE_SCAN_LIMIT = 1000000
DUPLICATE_LISTING_PAGE_SIZE = 10
DUPLICATE_LISTING_SNAPSHOT_LIMIT = 16
DUPLICATE_LISTING_SNAPSHOT_TTL_SECONDS = 30 * 60
DUPLICATE_ANALYSIS_TASK_LIMIT = 16
DUPLICATE_ANALYSIS_TASK_TTL_SECONDS = 30 * 60
DUPLICATE_CANDIDATE_CACHE_LIMIT = 8
DUPLICATE_CANDIDATE_CACHE_TTL_SECONDS = 60 * 60
DUPLICATE_PARTIAL_GROUP_INTERVAL_SECONDS = 0.8
DUPLICATE_PARTIAL_GROUP_CANDIDATE_STEP = 2000
DUPLICATE_CLUSTER_VISUAL_HASH_SIZE = 8
DUPLICATE_CLUSTER_VISUAL_THRESHOLD = 3
VISUAL_HASH_LOOKUP_CHUNK_SIZE = 500
VISUAL_HASH_PREWARM_BATCH_SIZE = 256
FILESYSTEM_DELETE_TASK_NAME = "filesystem_delete"
FILESYSTEM_DELETE_TASKS_STATE_FILE = "filesystem-delete-tasks.json"

_filesystem_delete_task_lock = RLock()
_filesystem_delete_processes: dict[str, subprocess.Popen] = {}


@dataclass(slots=True)
class MediaListingSnapshot:
    snapshot_id: str
    query_signature: str
    root: str | None
    path: str
    absolute_path: str
    sort_mode: str
    sort_program: dict
    entries: list[dict]
    total_bytes: int
    visual_hash_status: dict | None
    created_at: float
    last_accessed_at: float


@dataclass(slots=True)
class DuplicateListingSnapshot:
    snapshot_id: str
    query_signature: str
    root: str | None
    path: str
    absolute_path: str
    groups: list[dict]
    scanned_file_count: int
    candidate_file_count: int
    hash_computed_count: int
    source: str
    source_detail: str
    complete: bool
    created_at: float
    last_accessed_at: float


@dataclass(frozen=True, slots=True)
class DuplicateFileCandidate:
    name: str
    path: str
    absolute_path: str
    size: int
    modified_at: int | None
    file_path: Path | None = None


@dataclass(slots=True)
class DuplicateCandidateCache:
    cache_id: str
    root: str | None
    path: str
    absolute_path: str
    recursive: bool
    source: str
    source_detail: str
    filter_rules: tuple[dict, ...]
    min_size: int
    candidates: list[DuplicateFileCandidate]
    scanned_file_count: int
    complete: bool
    created_at: float
    last_accessed_at: float


@dataclass(slots=True)
class DuplicateAnalysisTask:
    task_id: str
    query_signature: str
    root: str | None
    path: str
    absolute_path: str
    status: str
    stage: str
    message: str
    groups: list[dict]
    scanned_file_count: int
    candidate_file_count: int
    hash_computed_count: int
    source: str
    source_detail: str
    complete: bool
    scan_limit: int
    snapshot_id: str
    error: str | None
    created_at: float
    started_at: float | None
    updated_at: float
    finished_at: float | None


@dataclass(frozen=True, slots=True)
class VisualHashPrewarmCandidate:
    absolute_path: str
    last_known_path: str
    file_size: int | None
    modified_at_ms: int | None
    content_hash: str | None
    hash_algorithm: str
    media_kind: str | None
    mime_type: str | None


_media_listing_snapshots: OrderedDict[str, MediaListingSnapshot] = OrderedDict()
_media_listing_snapshot_lock = RLock()
_duplicate_listing_snapshots: OrderedDict[str, DuplicateListingSnapshot] = OrderedDict()
_duplicate_listing_snapshot_lock = RLock()
_duplicate_candidate_caches: OrderedDict[str, DuplicateCandidateCache] = OrderedDict()
_duplicate_candidate_cache_lock = RLock()
_duplicate_analysis_tasks: OrderedDict[str, DuplicateAnalysisTask] = OrderedDict()
_duplicate_analysis_task_lock = RLock()
_duplicate_analysis_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="duplicate-analysis")
_visual_hash_prewarm_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="device-visual-hash")
_visual_hash_prewarm_lock = RLock()
_visual_hash_prewarm_active_keys: set[str] = set()
_EVERYTHING3_UINT64_MAX = (1 << 64) - 1
_everything3_dll = None
_everything3_dll_path: str | None = None


class LegacyPathRequest(BaseModel):
    path: str


class RootScopedRequest(BaseModel):
    root: Optional[str] = None
    path: str = ""
    absolute_path: str = ""


class TextFileWriteRequest(RootScopedRequest):
    text: str = ""
    encoding: str = "utf-8"


class LabelmeRenameRequest(RootScopedRequest):
    base_root: Optional[str] = None
    base_path: str = ""
    base_absolute_path: str = ""
    target_relative_path: str = ""
    overwrite: bool = False
    encoding: str = "utf-8"


class OcrPreviewRequest(RootScopedRequest):
    shape_type: OcrShapeType = "polygon"


MediaSortMode = Literal["path", "modified-desc", "size-desc", "weight-desc"]
DuplicateRuleField = Literal["size", "name", "extension", "modified_at", "sha256"]
DuplicateSortMode = Literal["file_size", "group_total", "reclaimable"]
DuplicateSourceMode = Literal["auto", "everything", "filesystem"]
DuplicatePathFilterAction = Literal["include", "exclude"]
DuplicatePathFilterMatch = Literal["contains", "prefix", "suffix", "equals", "glob"]
GallerySortField = Literal[
    "random",
    "duplicate_cluster",
    "weight",
    "modified_at",
    "size",
    "duration",
    "relative_path",
    "name",
    "folder_path",
    "kind",
    "width",
    "height",
    "resolution_area",
]
GallerySortDirection = Literal["asc", "desc"]
GallerySortNulls = Literal["first", "last"]


class GallerySortRule(BaseModel):
    field: GallerySortField = "weight"
    direction: GallerySortDirection = "desc"
    nulls: GallerySortNulls = "last"


class GallerySortProgram(BaseModel):
    rules: List[GallerySortRule] = PydanticField(default_factory=list)


DirectorySortField = Literal[
    "name",
    "modified_at",
    "recursive_total_bytes",
    "recursive_file_count",
    "latest_descendant_modified_at",
    "max_weight",
    "weighted_file_count",
]


class DirectorySortRule(BaseModel):
    field: DirectorySortField = "name"
    direction: GallerySortDirection = "asc"
    nulls: GallerySortNulls = "last"


class DirectorySortProgram(BaseModel):
    rules: List[DirectorySortRule] = PydanticField(default_factory=list)


class DirectoryListRequest(RootScopedRequest):
    sort_program: Optional[DirectorySortProgram] = None


class MediaListRequest(RootScopedRequest):
    recursive: bool = False
    sort_mode: MediaSortMode = "path"
    sort_program: Optional[GallerySortProgram] = None
    scan_limit: int = DEFAULT_MEDIA_SCAN_LIMIT
    snapshot_id: str = ""
    offset: int = 0
    limit: int = 0
    layout_mode: Literal["none", "masonry"] = "none"
    layout_columns: int = 0
    layout_column_width: int = 0
    layout_gap: int = 0
    layout_column_heights: List[float] = PydanticField(default_factory=list)


class DuplicatePathFilterRule(BaseModel):
    enabled: bool = True
    action: DuplicatePathFilterAction = "exclude"
    match: DuplicatePathFilterMatch = "contains"
    value: str = ""


class DuplicateListRequest(RootScopedRequest):
    recursive: bool = True
    rules: List[DuplicateRuleField] = PydanticField(default_factory=lambda: ["size"])
    filter_rules: List[DuplicatePathFilterRule] = PydanticField(default_factory=list)
    sort_mode: DuplicateSortMode = "reclaimable"
    source: DuplicateSourceMode = "auto"
    min_size: int = 1024 * 1024
    scan_limit: int = DEFAULT_DUPLICATE_SCAN_LIMIT
    snapshot_id: str = ""
    page: int = 1
    page_size: int = DUPLICATE_LISTING_PAGE_SIZE


class DeleteEntryRequest(BaseModel):
    root: Optional[str] = None
    path: str = ""
    absolute_path: str = ""
    recursive: bool = False


class RevealEntryResponse(BaseModel):
    ok: bool = False
    supported: bool = False
    launched: bool = False
    method: str = ""
    detail: str = ""
    root: Optional[str] = None
    path: str = ""
    absolute_path: str = ""
    target_path: str = ""
    directory_path: str = ""


class DeviceFileWeightUpdateRequest(BaseModel):
    root: Optional[str] = None
    path: str = ""
    absolute_path: str = ""
    weight: int = 0


class DeviceFileSyncItemRequest(BaseModel):
    absolute_path: str
    last_known_path: Optional[str] = None
    content_hash: Optional[str] = None
    hash_algorithm: str = "sha256"
    visual_hash: Optional[str] = None
    visual_hash_algorithm: str = "dhash-8"
    file_size: Optional[int] = None
    modified_at_ms: Optional[int] = None
    duration_ms: Optional[int] = None
    width_px: Optional[int] = None
    height_px: Optional[int] = None
    media_kind: Optional[str] = None
    mime_type: Optional[str] = None
    weight: Optional[int] = None


class DeviceFileSyncRequest(BaseModel):
    items: List[DeviceFileSyncItemRequest]
    mark_missing_as_dangling: bool = False
    scope_prefixes: List[str] = []


class DeviceFileScanRequest(BaseModel):
    root: Optional[str] = None
    path: str = ""
    absolute_path: str = ""
    recursive: bool = True
    hash_mode: Literal["auto", "always", "never"] = "auto"
    mark_missing_as_dangling: bool = True


GALLERY_SORT_FALLBACK_RULES = (
    GallerySortRule(field="relative_path", direction="asc", nulls="last"),
    GallerySortRule(field="name", direction="asc", nulls="last"),
)


def _compute_image_dhash(path: Path, *, hash_size: int = DUPLICATE_CLUSTER_VISUAL_HASH_SIZE) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        with Image.open(path) as image_obj:
            image = ImageOps.exif_transpose(image_obj).convert("L")
            resampling = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            image = image.resize((hash_size + 1, hash_size), resampling)
            pixels = list(image.get_flattened_data())
    except Exception:
        return None

    bits: list[int] = []
    row_width = hash_size + 1
    for row in range(hash_size):
        offset = row * row_width
        for col in range(hash_size):
            bits.append(1 if pixels[offset + col] > pixels[offset + col + 1] else 0)

    value = 0
    for bit in bits:
        value = (value << 1) | bit
    width = max(1, (hash_size * hash_size) // 4)
    return f"{value:0{width}x}"


def _visual_hash_distance(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except Exception:
        return None


def _parse_visual_hash_int(value: str | None) -> int | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    try:
        return int(normalized, 16)
    except Exception:
        return None


def _normalize_hash_algorithm(value: str | None, default: str) -> str:
    return (str(value or default).strip().lower() or default)


def _normalize_optional_hash(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _make_content_hash_key(
    content_hash: str | None,
    hash_algorithm: str | None = "sha256",
) -> tuple[str, str] | None:
    normalized_content_hash = _normalize_optional_hash(content_hash)
    if not normalized_content_hash:
        return None
    return (_normalize_hash_algorithm(hash_algorithm, "sha256"), normalized_content_hash)


def _iter_list_chunks(values: list[str], size: int = VISUAL_HASH_LOOKUP_CHUNK_SIZE) -> Iterator[list[str]]:
    normalized_size = max(1, int(size or VISUAL_HASH_LOOKUP_CHUNK_SIZE))
    for start in range(0, len(values), normalized_size):
        yield values[start:start + normalized_size]


def _load_cached_device_records_by_path(
    session: Session | None,
    device_id: str,
    absolute_paths: list[str],
) -> dict[str, DeviceFile]:
    if session is None or not device_id or not absolute_paths:
        return {}

    deduplicated_paths = list(dict.fromkeys(path for path in absolute_paths if path))
    records: dict[str, DeviceFile] = {}
    for path_chunk in _iter_list_chunks(deduplicated_paths):
        for record in session.exec(
            select(DeviceFile).where(
                DeviceFile.device_id == device_id,
                DeviceFile.absolute_path.in_(path_chunk),
            )
        ).all():
            if record.absolute_path:
                records[record.absolute_path] = record
    return records


def _load_visual_hashes_by_content_hash(
    session: Session | None,
    device_id: str,
    hash_keys: set[tuple[str, str]],
) -> dict[tuple[str, str], tuple[str, str]]:
    if session is None or not device_id or not hash_keys:
        return {}

    requested_hashes = sorted({content_hash for _, content_hash in hash_keys if content_hash})
    visual_hashes: dict[tuple[str, str], tuple[str, str]] = {}
    for hash_chunk in _iter_list_chunks(requested_hashes):
        for record in session.exec(
            select(DeviceFile).where(
                DeviceFile.device_id == device_id,
                DeviceFile.content_hash.in_(hash_chunk),
                DeviceFile.visual_hash.is_not(None),
            )
        ).all():
            hash_key = _make_content_hash_key(record.content_hash, record.hash_algorithm)
            visual_hash = _normalize_optional_hash(record.visual_hash)
            if hash_key is None or hash_key not in hash_keys or not visual_hash or hash_key in visual_hashes:
                continue
            visual_hashes[hash_key] = (
                visual_hash,
                _normalize_hash_algorithm(record.visual_hash_algorithm, "dhash-8"),
            )
    return visual_hashes


def _resolve_pending_visual_hash_items(
    pending_items: list[dict],
    session: Session | None,
    device_id: str,
) -> tuple[int, int]:
    if not pending_items:
        return 0, 0

    requested_hash_keys = {
        hash_key
        for hash_key in (
            _make_content_hash_key(
                item.get("entry", {}).get("content_hash"),
                item.get("entry", {}).get("hash_algorithm"),
            )
            for item in pending_items
        )
        if hash_key is not None
    }
    available_visual_hashes = _load_visual_hashes_by_content_hash(session, device_id, requested_hash_keys)
    reused_content_hash_count = 0
    computed_count = 0

    for item in pending_items:
        entry = item.get("entry")
        file_path = item.get("file_path")
        if not isinstance(entry, dict):
            continue

        entry["hash_algorithm"] = _normalize_hash_algorithm(entry.get("hash_algorithm"), "sha256")
        entry["visual_hash_algorithm"] = _normalize_hash_algorithm(entry.get("visual_hash_algorithm"), "dhash-8")

        existing_visual_hash = _normalize_optional_hash(entry.get("visual_hash"))
        if existing_visual_hash:
            entry["visual_hash"] = existing_visual_hash
            hash_key = _make_content_hash_key(entry.get("content_hash"), entry.get("hash_algorithm"))
            if hash_key is not None:
                available_visual_hashes.setdefault(
                    hash_key,
                    (existing_visual_hash, entry["visual_hash_algorithm"]),
                )
            continue

        hash_key = _make_content_hash_key(entry.get("content_hash"), entry.get("hash_algorithm"))
        if hash_key is not None and hash_key in available_visual_hashes:
            cached_visual_hash, cached_visual_hash_algorithm = available_visual_hashes[hash_key]
            entry["visual_hash"] = cached_visual_hash
            entry["visual_hash_algorithm"] = cached_visual_hash_algorithm
            reused_content_hash_count += 1
            continue

        if not isinstance(file_path, Path):
            continue

        computed_visual_hash = _compute_image_dhash(file_path)
        if not computed_visual_hash:
            continue

        entry["visual_hash"] = computed_visual_hash
        entry["visual_hash_algorithm"] = "dhash-8"
        computed_count += 1
        if hash_key is not None:
            available_visual_hashes[hash_key] = (computed_visual_hash, "dhash-8")
    return reused_content_hash_count, computed_count


def _run_visual_hash_prewarm(
    prewarm_key: str,
    device_id: str,
    candidates: list[VisualHashPrewarmCandidate],
) -> None:
    try:
        from backend.core.device_file_cover import (
            DeviceFileMetadataSnapshot,
            upsert_device_file_metadata_batch,
        )

        if not device_id or not candidates:
            return

        with Session(engine) as session:
            current_records = _load_cached_device_records_by_path(
                session,
                device_id,
                [candidate.absolute_path for candidate in candidates],
            )
            pending_items: list[dict] = []
            snapshot_specs: list[tuple[VisualHashPrewarmCandidate, dict, int | None, int | None, int | None, str | None, str | None]] = []

            for candidate in candidates:
                current_record = current_records.get(candidate.absolute_path)
                if current_record is not None:
                    if (
                        current_record.file_size != candidate.file_size
                        or current_record.modified_at_ms != candidate.modified_at_ms
                    ):
                        continue
                    if _normalize_optional_hash(current_record.visual_hash):
                        continue

                file_path = Path(candidate.absolute_path)
                if not file_path.exists() or not file_path.is_file():
                    continue

                entry = {
                    "content_hash": (
                        _normalize_optional_hash(current_record.content_hash)
                        if current_record is not None
                        else _normalize_optional_hash(candidate.content_hash)
                    ),
                    "hash_algorithm": (
                        _normalize_hash_algorithm(current_record.hash_algorithm, "sha256")
                        if current_record is not None
                        else _normalize_hash_algorithm(candidate.hash_algorithm, "sha256")
                    ),
                    "visual_hash": None,
                    "visual_hash_algorithm": "dhash-8",
                }
                pending_items.append({
                    "entry": entry,
                    "file_path": file_path,
                })
                snapshot_specs.append((
                    candidate,
                    entry,
                    current_record.duration_ms if current_record is not None else None,
                    current_record.width_px if current_record is not None else None,
                    current_record.height_px if current_record is not None else None,
                    candidate.media_kind or (current_record.media_kind if current_record is not None else None),
                    candidate.mime_type or (current_record.mime_type if current_record is not None else None),
                ))

            _resolve_pending_visual_hash_items(pending_items, session, device_id)

            snapshots = []
            for candidate, entry, duration_ms, width_px, height_px, media_kind, mime_type in snapshot_specs:
                visual_hash = _normalize_optional_hash(entry.get("visual_hash"))
                if not visual_hash:
                    continue
                snapshots.append(
                    DeviceFileMetadataSnapshot(
                        absolute_path=candidate.absolute_path,
                        last_known_path=candidate.last_known_path,
                        file_size=candidate.file_size,
                        modified_at_ms=candidate.modified_at_ms,
                        content_hash=_normalize_optional_hash(entry.get("content_hash")),
                        hash_algorithm=_normalize_hash_algorithm(entry.get("hash_algorithm"), "sha256"),
                        visual_hash=visual_hash,
                        visual_hash_algorithm=_normalize_hash_algorithm(entry.get("visual_hash_algorithm"), "dhash-8"),
                        duration_ms=duration_ms,
                        width_px=width_px,
                        height_px=height_px,
                        media_kind=media_kind,
                        mime_type=mime_type,
                    )
                )

            if snapshots:
                upsert_device_file_metadata_batch(session, device_id, snapshots)
    except Exception:
        return
    finally:
        with _visual_hash_prewarm_lock:
            _visual_hash_prewarm_active_keys.discard(prewarm_key)


def _schedule_visual_hash_prewarm(
    prewarm_key: str | None,
    device_id: str,
    candidates: list[VisualHashPrewarmCandidate],
) -> None:
    normalized_key = str(prewarm_key or "").strip()
    if not normalized_key or not device_id or not candidates:
        return

    batch = candidates[:VISUAL_HASH_PREWARM_BATCH_SIZE]
    if not batch:
        return

    with _visual_hash_prewarm_lock:
        if normalized_key in _visual_hash_prewarm_active_keys:
            return
        _visual_hash_prewarm_active_keys.add(normalized_key)

    try:
        _visual_hash_prewarm_executor.submit(_run_visual_hash_prewarm, normalized_key, device_id, batch)
    except Exception:
        with _visual_hash_prewarm_lock:
            _visual_hash_prewarm_active_keys.discard(normalized_key)


def _build_visual_hash_status(
    *,
    include_visual_hash: bool,
    total_image_count: int,
    indexed_count: int,
    computed_count: int = 0,
    reused_content_hash_count: int = 0,
    prewarm_scheduled_count: int = 0,
) -> dict:
    normalized_total = max(0, int(total_image_count or 0))
    normalized_indexed = min(normalized_total, max(0, int(indexed_count or 0)))
    normalized_computed = max(0, int(computed_count or 0))
    normalized_reused = max(0, int(reused_content_hash_count or 0))
    normalized_prewarm = max(0, int(prewarm_scheduled_count or 0))
    missing_count = max(0, normalized_total - normalized_indexed)
    return {
        "requested": bool(include_visual_hash),
        "total_image_count": normalized_total,
        "indexed_count": normalized_indexed,
        "missing_count": missing_count,
        "computed_count": normalized_computed,
        "reused_content_hash_count": normalized_reused,
        "prewarm_scheduled_count": normalized_prewarm,
        "complete": missing_count == 0,
    }


def _normalize_rel_path(raw_path: str) -> str:
    normalized = (raw_path or "").strip().replace("\\", "/")
    normalized = normalized.lstrip("/")
    if normalized in {"", "."}:
        return ""
    return "/".join(part for part in normalized.split("/") if part not in {"", "."})


def _normalize_strict_relative_file_path(raw_path: str) -> str:
    normalized_input = (raw_path or "").strip()
    if not normalized_input:
        raise HTTPException(status_code=400, detail="Target relative path is required")
    if _is_absolute_input(normalized_input):
        raise HTTPException(status_code=400, detail="Target path must be relative")
    if normalized_input.endswith(("/", "\\")):
        raise HTTPException(status_code=400, detail="Target path must include a file name")

    normalized = _normalize_rel_path(normalized_input)
    if not normalized:
        raise HTTPException(status_code=400, detail="Target relative path is required")
    if any(part == ".." for part in normalized.split("/")):
        raise HTTPException(status_code=400, detail="Target path escapes the base directory")
    return normalized


def _normalize_input_path(raw_path: str) -> str:
    return (raw_path or "").strip()


def _is_absolute_input(raw_path: str) -> bool:
    normalized = _normalize_input_path(raw_path)
    if not normalized:
        return False
    if normalized.startswith(("~", "/", "\\")):
        return True
    drive, _ = os.path.splitdrive(normalized)
    return bool(drive)


def _resolve_absolute_path(raw_path: str) -> Path:
    candidate = Path(_normalize_input_path(raw_path)).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(status_code=400, detail="absolute_path must be absolute")
    return candidate.resolve(strict=False)


def _ensure_within_root(root_path: Path, candidate: Path) -> Path:
    resolved_root = root_path.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        raise HTTPException(status_code=400, detail="Path escapes the allowed root")
    return resolved_candidate


def _iter_candidate_roots() -> Iterable[dict]:
    settings = get_settings()
    home_dir = Path.home()
    pictures_dir = home_dir / "Pictures"
    candidates = [
        {
            "key": "attachments",
            "label": "附件目录",
            "path": settings.attachments_dir,
            "preferred": True,
        },
        {
            "key": "data",
            "label": "数据目录",
            "path": settings.data_dir,
            "preferred": False,
        },
        {
            "key": "project",
            "label": "项目根目录",
            "path": ROOT_DIR,
            "preferred": False,
        },
        {
            "key": "home",
            "label": "用户目录",
            "path": home_dir,
            "preferred": False,
        },
        {
            "key": "pictures",
            "label": "图片目录",
            "path": pictures_dir,
            "preferred": False,
        },
    ]

    seen_paths = set()
    for item in candidates:
        path = item["path"]
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            continue
        if resolved in seen_paths:
            continue
        if not resolved.exists():
            continue
        seen_paths.add(resolved)
        yield {
            "key": item["key"],
            "label": item["label"],
            "path": os.fspath(resolved),
            "preferred": item["preferred"],
            "writable": os.access(resolved, os.W_OK),
        }


def list_available_roots() -> List[dict]:
    return list(_iter_candidate_roots())


def resolve_root_path(root_key: str) -> Path:
    for root in list_available_roots():
        if root["key"] == root_key:
            return Path(root["path"])
    raise HTTPException(status_code=404, detail="Unknown filesystem root")


def resolve_scoped_path(root_key: str, rel_path: str) -> tuple[Path, str]:
    root_path = resolve_root_path(root_key)
    normalized_rel = _normalize_rel_path(rel_path)
    candidate = root_path / normalized_rel if normalized_rel else root_path
    return _ensure_within_root(root_path, candidate), normalized_rel


def resolve_request_path(
    root_key: Optional[str],
    rel_path: str = "",
    absolute_path: str = "",
) -> tuple[Path, dict]:
    normalized_absolute = _normalize_input_path(absolute_path)
    if not normalized_absolute and not root_key and _is_absolute_input(rel_path):
        normalized_absolute = _normalize_input_path(rel_path)

    if normalized_absolute:
        target_path = _resolve_absolute_path(normalized_absolute)
        absolute_string = os.fspath(target_path)
        return target_path, {
            "root": None,
            "path": absolute_string,
            "absolute_path": absolute_string,
            "is_absolute": True,
        }

    if not root_key:
        raise HTTPException(status_code=400, detail="Either root or absolute_path is required")

    target_path, normalized_rel = resolve_scoped_path(root_key, rel_path)
    return target_path, {
        "root": root_key,
        "path": normalized_rel,
        "absolute_path": "",
        "is_absolute": False,
    }


def _paths_equal(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(left.resolve(strict=False))) == os.path.normcase(os.fspath(right.resolve(strict=False)))


def _is_filesystem_root_path(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except Exception:
        resolved = path
    return resolved.parent == resolved


def _iter_scan_files(target_path: Path, *, recursive: bool) -> list[Path]:
    if target_path.is_file():
        return [target_path]
    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail="Path is neither file nor directory")

    files: list[Path] = []
    if recursive:
        def _raise_walk_error(exc: OSError) -> None:
            raise exc

        try:
            for current_root, _, filenames in os.walk(target_path, onerror=_raise_walk_error):
                current_root_path = Path(current_root)
                for filename in filenames:
                    files.append(current_root_path / filename)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Permission denied") from exc
    else:
        try:
            with os.scandir(target_path) as entries:
                for entry in entries:
                    if entry.is_file():
                        files.append(Path(entry.path))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Permission denied") from exc

    files.sort(key=lambda item: os.fspath(item).lower())
    return files


def _guess_scan_file_metadata(path: Path) -> tuple[str | None, str | None]:
    media_info = _resolve_media_kind(path)
    if media_info:
        return media_info
    mime_type, _ = mimetypes.guess_type(os.fspath(path))
    return None, mime_type


def _compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_duplicate_rules(rules: Iterable[str] | None) -> tuple[str, ...]:
    allowed = {"size", "name", "extension", "modified_at", "sha256"}
    normalized: list[str] = ["size"]
    for rule in rules or []:
        value = str(rule or "").strip()
        if value in allowed and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _normalize_duplicate_filter_rules(rules: Iterable[DuplicatePathFilterRule | dict | object] | None) -> tuple[dict, ...]:
    normalized: list[dict] = []
    for raw_rule in rules or []:
        if isinstance(raw_rule, DuplicatePathFilterRule):
            raw = raw_rule.model_dump()
        elif isinstance(raw_rule, dict):
            raw = raw_rule
        else:
            continue

        value = str(raw.get("value") or "").strip()
        if not value:
            continue
        action = raw.get("action") if raw.get("action") in {"include", "exclude"} else "exclude"
        match = raw.get("match") if raw.get("match") in {"contains", "prefix", "suffix", "equals", "glob"} else "contains"
        normalized.append(
            {
                "enabled": bool(raw.get("enabled", True)),
                "action": action,
                "match": match,
                "value": value,
            }
        )
        if len(normalized) >= 50:
            break
    return tuple(normalized)


def _normalize_duplicate_filter_path(value: str) -> str:
    return str(value or "").replace("\\", "/").lower()


def _duplicate_path_filter_rule_matches(rule: dict, absolute_path: str) -> bool:
    path_value = _normalize_duplicate_filter_path(absolute_path)
    rule_value = _normalize_duplicate_filter_path(str(rule.get("value") or ""))
    if not rule_value:
        return False

    match = str(rule.get("match") or "contains")
    if match == "prefix":
        return path_value.startswith(rule_value)
    if match == "suffix":
        return path_value.endswith(rule_value)
    if match == "equals":
        return path_value == rule_value
    if match == "glob":
        return fnmatch.fnmatch(path_value, rule_value)
    return rule_value in path_value


def _duplicate_path_allowed(absolute_path: str, filter_rules: tuple[dict, ...]) -> bool:
    allowed = True
    for rule in filter_rules:
        if not rule.get("enabled", True):
            continue
        if _duplicate_path_filter_rule_matches(rule, absolute_path):
            allowed = rule.get("action") != "exclude"
    return allowed


def _normalize_duplicate_scan_limit(value: int) -> int:
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        numeric_value = DEFAULT_DUPLICATE_SCAN_LIMIT
    return max(1, min(MAX_DUPLICATE_SCAN_LIMIT, numeric_value))


def _normalize_duplicate_page(value: int) -> int:
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        numeric_value = 1
    return max(1, numeric_value)


def _normalize_duplicate_page_size(value: int) -> int:
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        numeric_value = DUPLICATE_LISTING_PAGE_SIZE
    return max(1, min(50, numeric_value))


def _normalize_duplicate_min_size(value: int) -> int:
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        numeric_value = 0
    return max(0, numeric_value)


def _build_duplicate_query_signature(
    *,
    root: str | None,
    path: str,
    absolute_path: str,
    recursive: bool,
    rules: tuple[str, ...],
    filter_rules: tuple[dict, ...],
    sort_mode: str,
    source: str,
    min_size: int,
    scan_limit: int,
) -> str:
    return json.dumps(
        {
            "root": root,
            "path": path,
            "absolute_path": absolute_path,
            "recursive": bool(recursive),
            "rules": list(rules),
            "filter_rules": list(filter_rules),
            "sort_mode": sort_mode,
            "source": source,
            "min_size": min_size,
            "scan_limit": scan_limit,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _get_duplicate_listing_snapshot(snapshot_id: str, query_signature: str) -> DuplicateListingSnapshot | None:
    normalized_snapshot_id = str(snapshot_id or "").strip()
    if not normalized_snapshot_id:
        return None

    now = time.time()
    with _duplicate_listing_snapshot_lock:
        snapshot = _duplicate_listing_snapshots.get(normalized_snapshot_id)
        if snapshot is None or snapshot.query_signature != query_signature:
            return None
        if now - snapshot.created_at > DUPLICATE_LISTING_SNAPSHOT_TTL_SECONDS:
            _duplicate_listing_snapshots.pop(normalized_snapshot_id, None)
            return None
        snapshot.last_accessed_at = now
        _duplicate_listing_snapshots.move_to_end(normalized_snapshot_id)
        return snapshot


def _find_duplicate_listing_snapshot_by_signature(query_signature: str) -> DuplicateListingSnapshot | None:
    now = time.time()
    with _duplicate_listing_snapshot_lock:
        for snapshot_id, snapshot in list(_duplicate_listing_snapshots.items()):
            if now - snapshot.created_at > DUPLICATE_LISTING_SNAPSHOT_TTL_SECONDS:
                _duplicate_listing_snapshots.pop(snapshot_id, None)
                continue
            if snapshot.query_signature == query_signature:
                snapshot.last_accessed_at = now
                _duplicate_listing_snapshots.move_to_end(snapshot_id)
                return snapshot
    return None


def _store_duplicate_listing_snapshot(
    *,
    query_signature: str,
    root: str | None,
    path: str,
    absolute_path: str,
    groups: list[dict],
    scanned_file_count: int,
    candidate_file_count: int,
    hash_computed_count: int,
    source: str,
    source_detail: str,
    complete: bool,
) -> DuplicateListingSnapshot:
    now = time.time()
    snapshot = DuplicateListingSnapshot(
        snapshot_id=uuid4().hex,
        query_signature=query_signature,
        root=root,
        path=path,
        absolute_path=absolute_path,
        groups=groups,
        scanned_file_count=scanned_file_count,
        candidate_file_count=candidate_file_count,
        hash_computed_count=hash_computed_count,
        source=source,
        source_detail=source_detail,
        complete=complete,
        created_at=now,
        last_accessed_at=now,
    )
    with _duplicate_listing_snapshot_lock:
        _duplicate_listing_snapshots[snapshot.snapshot_id] = snapshot
        _duplicate_listing_snapshots.move_to_end(snapshot.snapshot_id)
        while len(_duplicate_listing_snapshots) > DUPLICATE_LISTING_SNAPSHOT_LIMIT:
            _duplicate_listing_snapshots.popitem(last=False)
    return snapshot


def _normalize_duplicate_scope_path(value: str) -> str:
    return str(value or "").replace("\\", "/").rstrip("/").lower()


def _is_duplicate_path_within_scope(parent_path: str, child_path: str) -> bool:
    parent = _normalize_duplicate_scope_path(parent_path)
    child = _normalize_duplicate_scope_path(child_path)
    return bool(parent and (child == parent or child.startswith(f"{parent}/")))


def _filter_duplicate_candidates_for_scope(
    candidates: Iterable[DuplicateFileCandidate],
    target_path: Path,
    *,
    min_size: int,
) -> list[DuplicateFileCandidate]:
    target = os.fspath(target_path.resolve(strict=False))
    if target_path.is_file():
        target_normalized = _normalize_duplicate_scope_path(target)
        return [
            candidate
            for candidate in candidates
            if candidate.size >= min_size and _normalize_duplicate_scope_path(candidate.absolute_path) == target_normalized
        ]
    return [
        candidate
        for candidate in candidates
        if candidate.size >= min_size and _is_duplicate_path_within_scope(target, candidate.absolute_path)
    ]


def _find_duplicate_candidate_cache(
    target_path: Path,
    *,
    source: str,
    recursive: bool,
    filter_rules: tuple[dict, ...],
    min_size: int,
) -> tuple[DuplicateCandidateCache, list[DuplicateFileCandidate]] | None:
    now = time.time()
    target_absolute = os.fspath(target_path.resolve(strict=False))
    with _duplicate_candidate_cache_lock:
        for cache_id, cache in list(_duplicate_candidate_caches.items()):
            if now - cache.created_at > DUPLICATE_CANDIDATE_CACHE_TTL_SECONDS:
                _duplicate_candidate_caches.pop(cache_id, None)
                continue
            if not cache.recursive or not recursive:
                continue
            if cache.filter_rules != filter_rules:
                continue
            if cache.min_size > min_size:
                continue
            if source != "auto" and cache.source != source:
                continue
            if not _is_duplicate_path_within_scope(cache.absolute_path, target_absolute):
                continue
            cache.last_accessed_at = now
            _duplicate_candidate_caches.move_to_end(cache_id)
            return cache, _filter_duplicate_candidates_for_scope(cache.candidates, target_path, min_size=min_size)
    return None


def _store_duplicate_candidate_cache(
    *,
    root: str | None,
    path: str,
    absolute_path: str,
    recursive: bool,
    source: str,
    source_detail: str,
    filter_rules: tuple[dict, ...],
    min_size: int,
    candidates: list[DuplicateFileCandidate],
    scanned_file_count: int,
    complete: bool,
) -> None:
    if not recursive or not candidates:
        return
    now = time.time()
    cache = DuplicateCandidateCache(
        cache_id=uuid4().hex,
        root=root,
        path=path,
        absolute_path=absolute_path,
        recursive=recursive,
        source=source,
        source_detail=source_detail,
        filter_rules=filter_rules,
        min_size=min_size,
        candidates=list(candidates),
        scanned_file_count=scanned_file_count,
        complete=complete,
        created_at=now,
        last_accessed_at=now,
    )
    with _duplicate_candidate_cache_lock:
        _duplicate_candidate_caches[cache.cache_id] = cache
        _duplicate_candidate_caches.move_to_end(cache.cache_id)
        while len(_duplicate_candidate_caches) > DUPLICATE_CANDIDATE_CACHE_LIMIT:
            _duplicate_candidate_caches.popitem(last=False)


def _iter_duplicate_candidate_paths(target_path: Path, *, recursive: bool) -> Iterator[Path]:
    if target_path.is_file():
        yield target_path
        return
    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail="Path is neither file nor directory")

    if recursive:
        for current_root, _, filenames in os.walk(target_path):
            current_root_path = Path(current_root)
            for filename in filenames:
                yield current_root_path / filename
        return

    try:
        with os.scandir(target_path) as entries:
            for entry in entries:
                try:
                    if entry.is_file():
                        yield Path(entry.path)
                except OSError:
                    continue
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied") from exc


def _build_duplicate_candidate_from_path(
    file_path: Path,
    *,
    resolved: dict,
    root_path: Path | None,
) -> DuplicateFileCandidate | None:
    try:
        stat_result = file_path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(stat_result.st_mode):
        return None

    identity_path = os.fspath(file_path.resolve(strict=False))
    if resolved["is_absolute"] or root_path is None:
        request_path = identity_path
    else:
        try:
            request_path = os.fspath(file_path.relative_to(root_path)).replace("\\", "/")
        except ValueError:
            request_path = identity_path

    return DuplicateFileCandidate(
        name=file_path.name,
        path=request_path,
        absolute_path=identity_path,
        size=int(stat_result.st_size),
        modified_at=int(stat_result.st_mtime * 1000),
        file_path=file_path,
    )


def _find_everything_es_path() -> Path | None:
    env_paths = [
        os.environ.get("CODEYUN_EVERYTHING_ES", ""),
        os.environ.get("EVERYTHING_ES", ""),
    ]
    for raw_path in env_paths:
        if raw_path:
            candidate = Path(raw_path)
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue

    for executable_name in ("es.exe", "es"):
        resolved = shutil.which(executable_name)
        if resolved:
            return Path(resolved)

    if os.name != "nt":
        return None

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    for candidate in [
        Path(program_files) / "Everything 1.5a" / "es.exe",
        Path(program_files) / "Everything" / "es.exe",
        Path(program_files_x86) / "Everything 1.5a" / "es.exe",
        Path(program_files_x86) / "Everything" / "es.exe",
        Path(local_app_data) / "Everything" / "es.exe" if local_app_data else None,
    ]:
        if candidate is None:
            continue
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _everything_dupe_query_for_rules(rules: tuple[str, ...]) -> str:
    if "sha256" in rules:
        return "dupe:size"

    fields = ["size"]
    if "name" in rules:
        fields.insert(0, "name")
    if "modified_at" in rules:
        fields.append("dm")
    return f"dupe:{';'.join(dict.fromkeys(fields))}"


def _parse_everything_filetime_ms(value: str) -> int | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        filetime = int(normalized)
    except ValueError:
        return None
    unix_ms = (filetime - 116444736000000000) // 10000
    return unix_ms if unix_ms > 0 else None


def _load_duplicate_candidates_from_everything(
    target_path: Path,
    *,
    resolved: dict,
    rules: tuple[str, ...],
    filter_rules: tuple[dict, ...],
    min_size: int,
    scan_limit: int,
) -> tuple[list[DuplicateFileCandidate], str] | None:
    if os.name != "nt" or target_path.is_file():
        return None

    es_path = _find_everything_es_path()
    if es_path is None:
        return None

    with tempfile.NamedTemporaryFile(prefix="codeyun-duplicates-", suffix=".csv", delete=False) as temp_file:
        export_path = Path(temp_file.name)

    args = [
        os.fspath(es_path),
        "-n",
        str(scan_limit),
        "-sort",
        "size",
        "-sort-descending",
        "-export-csv",
        os.fspath(export_path),
        "-no-header",
        "-full-path-and-name",
        "-size",
        "-dm",
        "-size-format",
        "1",
        "-date-format",
        "2",
        "-no-digit-grouping",
        "-path",
        os.fspath(target_path),
        "/a-d",
        _everything_dupe_query_for_rules(rules),
    ]
    if min_size > 0:
        args.append(f"size:>={min_size}")

    instance_name = os.environ.get("CODEYUN_EVERYTHING_INSTANCE") or os.environ.get("EVERYTHING_INSTANCE") or ""
    if instance_name:
        args[1:1] = ["-instance", instance_name]

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode not in {0, 1}:
            return None

        candidates: list[DuplicateFileCandidate] = []
        try:
            with export_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
                reader = csv.reader(file_obj)
                for row in reader:
                    if len(row) < 3:
                        continue
                    file_path = Path(row[0])
                    try:
                        stat_result = file_path.stat()
                    except OSError:
                        continue
                    if not stat.S_ISREG(stat_result.st_mode):
                        continue
                    size = int(stat_result.st_size)
                    if size < min_size:
                        continue
                    identity_path = os.fspath(file_path.resolve(strict=False))
                    if not _duplicate_path_allowed(identity_path, filter_rules):
                        continue
                    candidates.append(
                        DuplicateFileCandidate(
                            name=file_path.name,
                            path=identity_path if resolved["is_absolute"] else os.fspath(file_path),
                            absolute_path=identity_path,
                            size=size,
                            modified_at=_parse_everything_filetime_ms(row[2]) or int(stat_result.st_mtime * 1000),
                            file_path=file_path,
                        )
                    )
        except OSError:
            return None
        return candidates, f"Everything ES: {es_path}"
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        try:
            export_path.unlink(missing_ok=True)
        except OSError:
            pass


def _load_duplicate_candidates_from_filesystem(
    target_path: Path,
    *,
    resolved: dict,
    filter_rules: tuple[dict, ...],
    min_size: int,
    scan_limit: int,
    recursive: bool,
) -> tuple[list[DuplicateFileCandidate], bool]:
    root_path = None if resolved["is_absolute"] else resolve_root_path(resolved["root"])
    candidates: list[DuplicateFileCandidate] = []
    scanned_file_count = 0
    complete = True
    for file_path in _iter_duplicate_candidate_paths(target_path, recursive=recursive):
        candidate = _build_duplicate_candidate_from_path(file_path, resolved=resolved, root_path=root_path)
        if candidate is None:
            continue
        scanned_file_count += 1
        if not _duplicate_path_allowed(candidate.absolute_path, filter_rules):
            continue
        if candidate.size >= min_size:
            candidates.append(candidate)
        if scanned_file_count >= scan_limit:
            complete = False
            break
    return candidates, complete


def _load_duplicate_candidates(
    target_path: Path,
    *,
    resolved: dict,
    source: str,
    rules: tuple[str, ...],
    filter_rules: tuple[dict, ...],
    min_size: int,
    scan_limit: int,
    recursive: bool,
) -> tuple[list[DuplicateFileCandidate], int, str, str, bool]:
    if source in {"auto", "everything"}:
        everything_result = _load_duplicate_candidates_from_everything(
            target_path,
            resolved=resolved,
            rules=rules,
            filter_rules=filter_rules,
            min_size=min_size,
            scan_limit=scan_limit,
        )
        if everything_result is not None:
            candidates, detail = everything_result
            complete = len(candidates) < scan_limit
            return candidates, len(candidates), "everything", detail, complete
        if source == "everything":
            raise HTTPException(status_code=501, detail="Everything ES is not available on this device")

    candidates, complete = _load_duplicate_candidates_from_filesystem(
        target_path,
        resolved=resolved,
        filter_rules=filter_rules,
        min_size=min_size,
        scan_limit=scan_limit,
        recursive=recursive,
    )
    return candidates, len(candidates), "filesystem", "filesystem traversal", complete


def _duplicate_key_for_candidate(
    candidate: DuplicateFileCandidate,
    rules: tuple[str, ...],
    *,
    content_hash: str | None = None,
) -> tuple:
    key_parts: list[object] = [candidate.size]
    if "name" in rules:
        key_parts.append(candidate.name.lower())
    if "extension" in rules:
        key_parts.append(Path(candidate.name).suffix.lower())
    if "modified_at" in rules:
        key_parts.append(candidate.modified_at)
    if "sha256" in rules:
        key_parts.append(content_hash or "")
    return tuple(key_parts)


def _rule_label_for_group_key(rules: tuple[str, ...], key: tuple) -> str:
    labels = []
    key_index = 0
    labels.append(f"大小={key[key_index]}")
    key_index += 1
    if "name" in rules:
        labels.append(f"名称={key[key_index]}")
        key_index += 1
    if "extension" in rules:
        labels.append(f"扩展名={key[key_index] or '(无)'}")
        key_index += 1
    if "modified_at" in rules:
        labels.append(f"修改时间={key[key_index]}")
        key_index += 1
    if "sha256" in rules:
        digest = str(key[key_index] or "")
        labels.append(f"SHA256={digest[:12]}..." if digest else "SHA256=未计算")
    return " / ".join(labels)


def _sort_duplicate_groups(groups: list[dict], sort_mode: str) -> list[dict]:
    if sort_mode == "file_size":
        primary = "file_size"
    elif sort_mode == "group_total":
        primary = "group_total_bytes"
    else:
        primary = "reclaimable_bytes"
    return sorted(
        groups,
        key=lambda group: (
            -int(group.get(primary) or 0),
            -int(group.get("file_size") or 0),
            str(group.get("key_label") or ""),
        ),
    )


def _build_duplicate_groups(
    candidates: list[DuplicateFileCandidate],
    *,
    rules: tuple[str, ...],
    sort_mode: str,
) -> tuple[list[dict], int]:
    grouped: dict[tuple, list[DuplicateFileCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(_duplicate_key_for_candidate(candidate, tuple(rule for rule in rules if rule != "sha256")), []).append(candidate)

    candidate_groups = [items for items in grouped.values() if len(items) > 1]
    hash_computed_count = 0
    if "sha256" in rules:
        hashed_grouped: dict[tuple, list[DuplicateFileCandidate]] = {}
        for items in candidate_groups:
            for candidate in items:
                if candidate.file_path is None:
                    continue
                try:
                    content_hash = _compute_file_sha256(candidate.file_path)
                except OSError:
                    continue
                hash_computed_count += 1
                key = _duplicate_key_for_candidate(candidate, rules, content_hash=content_hash)
                hashed_grouped.setdefault(key, []).append(candidate)
        grouped_items = [(key, items) for key, items in hashed_grouped.items() if len(items) > 1]
    else:
        grouped_items = [(key, items) for key, items in grouped.items() if len(items) > 1]

    groups: list[dict] = []
    for key, items in grouped_items:
        sorted_items = sorted(items, key=lambda item: item.absolute_path.lower())
        total_bytes = sum(item.size for item in sorted_items)
        max_size = max(item.size for item in sorted_items)
        key_source = json.dumps([str(part) for part in key], ensure_ascii=False, sort_keys=True)
        group_id = hashlib.sha1(key_source.encode("utf-8", "ignore")).hexdigest()
        groups.append(
            {
                "id": group_id,
                "key_label": _rule_label_for_group_key(rules, key),
                "rules": list(rules),
                "file_count": len(sorted_items),
                "file_size": max_size,
                "group_total_bytes": total_bytes,
                "reclaimable_bytes": max(0, total_bytes - max_size),
                "files": [
                    {
                        "name": item.name,
                        "path": item.path,
                        "absolute_path": item.absolute_path,
                        "size": item.size,
                        "modified_at": item.modified_at,
                    }
                    for item in sorted_items
                ],
            }
        )

    return _sort_duplicate_groups(groups, sort_mode), hash_computed_count


def _build_duplicate_listing_response(
    snapshot: DuplicateListingSnapshot,
    *,
    page: int,
    page_size: int,
) -> dict:
    normalized_page = _normalize_duplicate_page(page)
    normalized_page_size = _normalize_duplicate_page_size(page_size)
    start = (normalized_page - 1) * normalized_page_size
    end = start + normalized_page_size
    groups = snapshot.groups[start:end]
    total_groups = len(snapshot.groups)
    total_reclaimable_bytes = sum(int(group.get("reclaimable_bytes") or 0) for group in snapshot.groups)
    duplicate_file_count = sum(int(group.get("file_count") or 0) for group in snapshot.groups)
    return {
        "ok": True,
        "root": snapshot.root,
        "path": snapshot.path,
        "absolute_path": snapshot.absolute_path,
        "snapshot_id": snapshot.snapshot_id,
        "page": normalized_page,
        "page_size": normalized_page_size,
        "has_previous": normalized_page > 1,
        "has_next": end < total_groups,
        "total_groups": total_groups,
        "total_reclaimable_bytes": total_reclaimable_bytes,
        "duplicate_file_count": duplicate_file_count,
        "scanned_file_count": snapshot.scanned_file_count,
        "candidate_file_count": snapshot.candidate_file_count,
        "hash_computed_count": snapshot.hash_computed_count,
        "source": snapshot.source,
        "source_detail": snapshot.source_detail,
        "complete": snapshot.complete,
        "groups": groups,
    }


def _duplicate_task_snapshot(task: DuplicateAnalysisTask) -> DuplicateListingSnapshot:
    return DuplicateListingSnapshot(
        snapshot_id=task.snapshot_id,
        query_signature=task.query_signature,
        root=task.root,
        path=task.path,
        absolute_path=task.absolute_path,
        groups=list(task.groups),
        scanned_file_count=task.scanned_file_count,
        candidate_file_count=task.candidate_file_count,
        hash_computed_count=task.hash_computed_count,
        source=task.source,
        source_detail=task.source_detail,
        complete=task.complete,
        created_at=task.created_at,
        last_accessed_at=task.updated_at,
    )


def _build_duplicate_task_response(
    task: DuplicateAnalysisTask,
    *,
    page: int,
    page_size: int,
) -> dict:
    listing = _build_duplicate_listing_response(
        _duplicate_task_snapshot(task),
        page=page,
        page_size=page_size,
    )
    now = time.time()
    elapsed_start = task.started_at or task.created_at
    listing.update(
        {
            "task_id": task.task_id,
            "status": task.status,
            "stage": task.stage,
            "message": task.message,
            "running": task.status in {"queued", "running"},
            "error": task.error,
            "scan_limit": task.scan_limit,
            "hit_scan_limit": task.scanned_file_count >= task.scan_limit and not task.complete,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "updated_at": task.updated_at,
            "finished_at": task.finished_at,
            "elapsed_ms": int(max(0, (task.finished_at or now) - elapsed_start) * 1000),
        }
    )
    return listing


def _store_duplicate_analysis_task(task: DuplicateAnalysisTask) -> None:
    with _duplicate_analysis_task_lock:
        _duplicate_analysis_tasks[task.task_id] = task
        _duplicate_analysis_tasks.move_to_end(task.task_id)
        now = time.time()
        for task_id, existing in list(_duplicate_analysis_tasks.items()):
            if (
                len(_duplicate_analysis_tasks) <= DUPLICATE_ANALYSIS_TASK_LIMIT
                and now - existing.created_at <= DUPLICATE_ANALYSIS_TASK_TTL_SECONDS
            ):
                continue
            if existing.status in {"queued", "running"}:
                continue
            _duplicate_analysis_tasks.pop(task_id, None)


def _update_duplicate_analysis_task(task_id: str, **changes: object) -> DuplicateAnalysisTask | None:
    with _duplicate_analysis_task_lock:
        task = _duplicate_analysis_tasks.get(task_id)
        if task is None:
            return None
        for key, value in changes.items():
            if hasattr(task, key):
                setattr(task, key, value)
        task.updated_at = time.time()
        _duplicate_analysis_tasks.move_to_end(task_id)
        return task


def get_duplicate_analysis_task_snapshot(
    task_id: str,
    *,
    page: int = 1,
    page_size: int = DUPLICATE_LISTING_PAGE_SIZE,
) -> dict:
    normalized_task_id = str(task_id or "").strip()
    with _duplicate_analysis_task_lock:
        task = _duplicate_analysis_tasks.get(normalized_task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Duplicate analysis task not found")
        task.updated_at = time.time()
        _duplicate_analysis_tasks.move_to_end(normalized_task_id)
        snapshot = _build_duplicate_task_response(
            task,
            page=page,
            page_size=page_size,
        )
    return snapshot


def _should_hash_scanned_file(
    *,
    hash_mode: str,
    active_record,
    file_size: int | None,
    modified_at_ms: int | None,
    has_dangling_same_path: bool,
    has_dangling_same_size: bool,
    has_unseen_active_same_size: bool,
) -> bool:
    if hash_mode == "always":
        return True
    if hash_mode == "never":
        return False

    if active_record is not None:
        same_size = active_record.file_size == file_size
        same_modified_at = active_record.modified_at_ms == modified_at_ms
        if same_size and same_modified_at:
            if (
                not active_record.content_hash
                and (has_dangling_same_path or has_dangling_same_size or has_unseen_active_same_size)
            ):
                return True
            return False
        return True

    if has_dangling_same_path:
        return True

    if file_size is not None and (has_dangling_same_size or has_unseen_active_same_size):
        return True

    return False


def _path_matches_scope_prefix(path: str, prefix: str) -> bool:
    normalized_path = (path or "").strip()
    normalized_prefix = (prefix or "").strip().rstrip("/\\")
    if not normalized_path or not normalized_prefix:
        return False
    if normalized_path == normalized_prefix:
        return True
    return normalized_path.startswith(normalized_prefix + "/") or normalized_path.startswith(normalized_prefix + "\\")


def _entry_payload(entry: os.DirEntry[str], base_path: Path, *, use_absolute_path: bool) -> dict:
    try:
        stat_result = entry.stat()
    except OSError:
        stat_result = None

    full_path = Path(entry.path)
    entry_path = os.fspath(full_path) if use_absolute_path else os.fspath(full_path.relative_to(base_path)).replace("\\", "/")
    is_dir = entry.is_dir()
    return {
        "name": entry.name,
        "path": entry_path,
        "is_dir": is_dir,
        "size": None if is_dir or stat_result is None else stat_result.st_size,
        "modified_at": None if stat_result is None else int(stat_result.st_mtime * 1000),
    }


def _build_empty_directory_stats() -> dict[str, int | None]:
    return {
        "direct_file_bytes": None,
        "direct_file_count": None,
        "recursive_total_bytes": None,
        "recursive_file_count": None,
        "latest_descendant_modified_at": None,
        "max_weight": None,
        "weighted_file_count": None,
    }


def _build_directory_stats_by_name(
    session: Session | None,
    *,
    target_path: Path,
    directory_items: list[dict],
) -> dict[str, dict[str, int | None]]:
    if session is None or not directory_items:
        return {}

    try:
        device_id = get_device_id()
    except Exception:
        return {}

    scope_prefix = os.fspath(target_path.resolve(strict=False))
    normalized_prefix = (scope_prefix or "").strip().rstrip("/\\")
    if not normalized_prefix:
        return {}

    directory_names = {
        str(item.get("name") or "").lower(): str(item.get("name") or "")
        for item in directory_items
        if item.get("is_dir") and item.get("name")
    }
    if not directory_names:
        return {}

    rows = session.exec(
        select(
            DeviceFile.absolute_path,
            DeviceFile.file_size,
            DeviceFile.modified_at_ms,
            DeviceFile.weight,
        ).where(
            DeviceFile.device_id == device_id,
            DeviceFile.absolute_path.is_not(None),
            DeviceFile.match_status == "matched",
            DeviceFile.absolute_path.startswith(normalized_prefix),
        )
    ).all()

    stats_by_name: dict[str, dict[str, int | None]] = {}
    for absolute_path, file_size, modified_at_ms, weight in rows:
        if not absolute_path or not _path_matches_scope_prefix(absolute_path, normalized_prefix):
            continue

        relative_path = absolute_path[len(normalized_prefix):].lstrip("/\\")
        if not relative_path:
            continue

        normalized_relative = relative_path.replace("/", "\\")
        relative_segments = normalized_relative.split("\\", 1)
        first_segment = relative_segments[0].strip()
        child_relative_path = relative_segments[1] if len(relative_segments) > 1 else ""
        if not first_segment:
            continue

        directory_name = directory_names.get(first_segment.lower())
        if not directory_name:
            continue

        stats = stats_by_name.setdefault(directory_name, _build_empty_directory_stats())
        size_value = int(file_size) if isinstance(file_size, (int, float)) else 0
        modified_value = int(modified_at_ms) if isinstance(modified_at_ms, (int, float)) else None
        weight_value = int(weight) if isinstance(weight, (int, float)) else 0

        stats["recursive_total_bytes"] = int(stats["recursive_total_bytes"] or 0) + size_value
        stats["recursive_file_count"] = int(stats["recursive_file_count"] or 0) + 1
        if child_relative_path and "\\" not in child_relative_path:
            stats["direct_file_bytes"] = int(stats["direct_file_bytes"] or 0) + size_value
            stats["direct_file_count"] = int(stats["direct_file_count"] or 0) + 1
        if modified_value is not None:
            stats["latest_descendant_modified_at"] = max(
                int(stats["latest_descendant_modified_at"] or 0),
                modified_value,
            )
        stats["max_weight"] = max(int(stats["max_weight"] or 0), weight_value)
        if weight_value != 0:
            stats["weighted_file_count"] = int(stats["weighted_file_count"] or 0) + 1

    return stats_by_name


def _build_direct_file_stats(path: Path) -> dict[str, int | None]:
    direct_file_bytes = 0
    direct_file_count = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if not entry.is_file():
                        continue
                    stat_result = entry.stat()
                except OSError:
                    continue
                direct_file_bytes += int(stat_result.st_size)
                direct_file_count += 1
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return {}

    return {
        "direct_file_bytes": direct_file_bytes,
        "direct_file_count": direct_file_count,
    }


def _build_direct_directory_stats_by_name(
    *,
    target_path: Path,
    directory_items: list[dict],
    existing_stats_by_name: dict[str, dict[str, int | None]] | None = None,
) -> dict[str, dict[str, int | None]]:
    if not directory_items:
        return {}

    stats_by_name: dict[str, dict[str, int | None]] = {}
    for item in directory_items:
        name = str(item.get("name") or "")
        if not name:
            continue
        existing_stats = (existing_stats_by_name or {}).get(name)
        if existing_stats and existing_stats.get("direct_file_bytes") is not None:
            continue

        direct_stats = _build_direct_file_stats(Path(target_path) / name)
        if direct_stats:
            stats_by_name[name] = direct_stats

    return stats_by_name


def _iter_everything3_dll_candidates() -> Iterator[Path]:
    env_paths = [
        os.environ.get("CODEYUN_EVERYTHING3_DLL", ""),
        os.environ.get("EVERYTHING3_DLL", ""),
    ]
    for raw_path in env_paths:
        if raw_path:
            yield Path(raw_path)

    if os.name != "nt":
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    machine = platform.machine().lower()
    dll_name = "Everything3_x64.dll" if "64" in machine else "Everything3_x86.dll"
    for base_path in [
        Path(local_app_data) / "CodeYun" / "EverythingSDK3" / "dll" / dll_name if local_app_data else None,
        Path(program_files) / "Everything 1.5a" / dll_name,
        Path(program_files) / "Everything" / dll_name,
        Path(program_files_x86) / "Everything 1.5a" / dll_name,
        Path(program_files_x86) / "Everything" / dll_name,
    ]:
        if base_path is not None:
            yield base_path


def _find_everything3_dll_path() -> Path | None:
    for candidate in _iter_everything3_dll_candidates():
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _load_everything3_dll():
    global _everything3_dll, _everything3_dll_path

    if os.name != "nt":
        return None

    dll_path = _find_everything3_dll_path()
    if dll_path is None:
        return None

    resolved_path = os.fspath(dll_path)
    if _everything3_dll is not None and _everything3_dll_path == resolved_path:
        return _everything3_dll

    try:
        import ctypes

        dll = ctypes.WinDLL(resolved_path)
        dll.Everything3_ConnectW.argtypes = [ctypes.c_wchar_p]
        dll.Everything3_ConnectW.restype = ctypes.c_void_p
        dll.Everything3_DestroyClient.argtypes = [ctypes.c_void_p]
        dll.Everything3_DestroyClient.restype = ctypes.c_bool
        dll.Everything3_GetFolderSizeFromFilenameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        dll.Everything3_GetFolderSizeFromFilenameW.restype = ctypes.c_ulonglong
    except Exception:
        return None

    _everything3_dll = dll
    _everything3_dll_path = resolved_path
    return dll


def _iter_everything_instance_names() -> Iterator[str]:
    configured = [
        os.environ.get("CODEYUN_EVERYTHING_INSTANCE", ""),
        os.environ.get("EVERYTHING_INSTANCE", ""),
    ]
    yielded: set[str] = set()
    for name in configured:
        if name and name not in yielded:
            yielded.add(name)
            yield name

    for name in ("1.5a", ""):
        if name not in yielded:
            yielded.add(name)
            yield name


def _connect_everything3_client(dll):
    for instance_name in _iter_everything_instance_names():
        try:
            client = dll.Everything3_ConnectW(instance_name)
        except Exception:
            continue
        if client:
            return client
    return None


def _build_everything_directory_stats_by_name(
    *,
    target_path: Path,
    directory_items: list[dict],
    existing_stats_by_name: dict[str, dict[str, int | None]] | None = None,
) -> dict[str, dict[str, int | None]]:
    if os.name != "nt" or not directory_items:
        return {}

    dll = _load_everything3_dll()
    if dll is None:
        return {}

    pending_items = []
    for item in directory_items:
        name = str(item.get("name") or "")
        if not name:
            continue
        pending_items.append((name, Path(target_path) / name))

    if not pending_items:
        return {}

    client = _connect_everything3_client(dll)
    if not client:
        return {}

    stats_by_name: dict[str, dict[str, int | None]] = {}
    try:
        for name, directory_path in pending_items:
            try:
                size_value = int(dll.Everything3_GetFolderSizeFromFilenameW(client, os.fspath(directory_path)))
            except Exception:
                continue
            if size_value < 0 or size_value == _EVERYTHING3_UINT64_MAX:
                continue
            stats = _build_empty_directory_stats()
            stats["recursive_total_bytes"] = size_value
            stats_by_name[name] = stats
    finally:
        try:
            dll.Everything3_DestroyClient(client)
        except Exception:
            pass

    return stats_by_name


def _merge_directory_stats(
    primary_stats: dict[str, dict[str, int | None]],
    fallback_stats: dict[str, dict[str, int | None]],
    *,
    override_keys: set[str] | None = None,
) -> dict[str, dict[str, int | None]]:
    if not fallback_stats:
        return primary_stats

    normalized_override_keys = override_keys or set()
    merged_stats = {name: dict(stats) for name, stats in primary_stats.items()}
    for name, stats in fallback_stats.items():
        target_stats = merged_stats.setdefault(name, _build_empty_directory_stats())
        for key, value in stats.items():
            if value is None:
                continue
            if key in normalized_override_keys or target_stats.get(key) is None:
                target_stats[key] = value
    return merged_stats


DIRECTORY_SORT_FALLBACK_RULES = [DirectorySortRule(field="name", direction="asc", nulls="last")]


def _create_directory_sort_program() -> DirectorySortProgram:
    return DirectorySortProgram(rules=[DirectorySortRule(field="recursive_total_bytes", direction="desc", nulls="last")])


def _normalize_directory_sort_program(sort_program: DirectorySortProgram | None) -> list[DirectorySortRule]:
    base_program = sort_program if sort_program and sort_program.rules else _create_directory_sort_program()
    return [*base_program.rules, *DIRECTORY_SORT_FALLBACK_RULES]


def _get_directory_sort_value(item: dict, field: DirectorySortField) -> str | int | None:
    if field == "name":
        return str(item.get("name") or "").lower()
    if field == "modified_at":
        value = item.get("modified_at")
        return int(value) if isinstance(value, (int, float)) else None
    if field == "recursive_total_bytes":
        value = item.get("recursive_total_bytes")
        return int(value) if isinstance(value, (int, float)) else None
    if field == "recursive_file_count":
        value = item.get("recursive_file_count")
        return int(value) if isinstance(value, (int, float)) else None
    if field == "latest_descendant_modified_at":
        value = item.get("latest_descendant_modified_at")
        return int(value) if isinstance(value, (int, float)) else None
    if field == "max_weight":
        value = item.get("max_weight")
        return int(value) if isinstance(value, (int, float)) else None
    if field == "weighted_file_count":
        value = item.get("weighted_file_count")
        return int(value) if isinstance(value, (int, float)) else None
    return None


def _compare_directory_sort_rule(left: dict, right: dict, rule: DirectorySortRule) -> int:
    left_value = _get_directory_sort_value(left, rule.field)
    right_value = _get_directory_sort_value(right, rule.field)
    left_missing = left_value is None
    right_missing = right_value is None

    if left_missing or right_missing:
        if left_missing and right_missing:
            return 0
        if rule.nulls == "first":
            return -1 if left_missing else 1
        return 1 if left_missing else -1

    if isinstance(left_value, str) and isinstance(right_value, str):
        if left_value < right_value:
            result = -1
        elif left_value > right_value:
            result = 1
        else:
            result = 0
    else:
        left_number = int(left_value)
        right_number = int(right_value)
        result = (left_number > right_number) - (left_number < right_number)

    if result == 0:
        return 0
    return -result if rule.direction == "desc" else result


def _compare_directory_entries(left: dict, right: dict, rules: list[DirectorySortRule]) -> int:
    for rule in rules:
        result = _compare_directory_sort_rule(left, right, rule)
        if result != 0:
            return result
    left_name = str(left.get("name") or "").lower()
    right_name = str(right.get("name") or "").lower()
    if left_name < right_name:
        return -1
    if left_name > right_name:
        return 1
    return 0


def _sort_directory_entries(entries: list[dict], sort_program: DirectorySortProgram | None) -> None:
    normalized_rules = _normalize_directory_sort_program(sort_program)
    entries.sort(key=cmp_to_key(lambda left, right: _compare_directory_entries(left, right, normalized_rules)))


def _list_system_root_entries() -> list[dict]:
    def build_entry(candidate: str, name: str) -> dict:
        disk_total_bytes: int | None = None
        disk_free_bytes: int | None = None
        disk_used_bytes: int | None = None
        try:
            usage = shutil.disk_usage(candidate)
            disk_total_bytes = int(usage.total)
            disk_free_bytes = int(usage.free)
            disk_used_bytes = int(usage.used)
        except (OSError, ValueError):
            pass

        direct_file_stats = _build_direct_file_stats(Path(candidate))
        return {
            "name": name,
            "path": candidate,
            "is_dir": True,
            "size": None,
            "modified_at": None,
            "direct_file_bytes": direct_file_stats.get("direct_file_bytes"),
            "direct_file_count": direct_file_stats.get("direct_file_count"),
            "recursive_total_bytes": disk_used_bytes,
            "recursive_file_count": None,
            "latest_descendant_modified_at": None,
            "disk_total_bytes": disk_total_bytes,
            "disk_free_bytes": disk_free_bytes,
            "disk_used_bytes": disk_used_bytes,
        }

    if os.name == "nt":
        candidates: list[str] = []
        try:
            import ctypes

            drive_mask = ctypes.windll.kernel32.GetLogicalDrives()
        except Exception:
            drive_mask = 0

        if drive_mask:
            for index in range(26):
                if drive_mask & (1 << index):
                    drive_letter = chr(ord("A") + index)
                    candidates.append(f"{drive_letter}:\\")
        else:
            for drive_letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                candidate = f"{drive_letter}:\\"
                if Path(candidate).exists():
                    candidates.append(candidate)

        return [
            build_entry(candidate, candidate.rstrip("\\/"))
            for candidate in candidates
        ]

    return [
        build_entry("/", "/")
    ]


def list_directory_items(
    root_key: Optional[str] = None,
    rel_path: str = "",
    absolute_path: str = "",
    *,
    sort_program: DirectorySortProgram | None = None,
    session: Session | None = None,
) -> dict:
    if _normalize_input_path(absolute_path) == DEVICE_ROOT_SENTINEL:
        return {
            "root": None,
            "current_path": DEVICE_ROOT_SENTINEL,
            "absolute_path": DEVICE_ROOT_SENTINEL,
            "items": _list_system_root_entries(),
        }

    target_path, resolved = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    base_path = target_path if resolved["is_absolute"] else resolve_root_path(resolved["root"])
    items = []
    try:
        with os.scandir(target_path) as entries:
            for entry in entries:
                items.append(_entry_payload(entry, base_path, use_absolute_path=resolved["is_absolute"]))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied") from exc

    directory_items = [item for item in items if item["is_dir"]]
    file_items = [item for item in items if not item["is_dir"]]
    stats_by_name = _build_directory_stats_by_name(session, target_path=target_path, directory_items=directory_items)
    everything_stats_by_name = _build_everything_directory_stats_by_name(
        target_path=target_path,
        directory_items=directory_items,
        existing_stats_by_name=stats_by_name,
    )
    stats_by_name = _merge_directory_stats(
        stats_by_name,
        everything_stats_by_name,
        override_keys={"recursive_total_bytes"},
    )
    direct_stats_by_name = _build_direct_directory_stats_by_name(
        target_path=target_path,
        directory_items=directory_items,
        existing_stats_by_name=stats_by_name,
    )
    stats_by_name = _merge_directory_stats(stats_by_name, direct_stats_by_name)
    for item in directory_items:
        item.update(stats_by_name.get(str(item.get("name") or ""), _build_empty_directory_stats()))
    _sort_directory_entries(directory_items, sort_program)
    file_items.sort(key=lambda item: str(item.get("name") or "").lower())
    return {
        "root": resolved["root"],
        "current_path": resolved["path"],
        "absolute_path": resolved["absolute_path"],
        "items": [*directory_items, *file_items],
    }


def list_duplicate_file_groups(
    root_key: Optional[str] = None,
    rel_path: str = "",
    absolute_path: str = "",
    *,
    recursive: bool = True,
    rules: Iterable[str] | None = None,
    filter_rules: Iterable[DuplicatePathFilterRule | dict | object] | None = None,
    sort_mode: DuplicateSortMode = "reclaimable",
    source: DuplicateSourceMode = "auto",
    min_size: int = 1024 * 1024,
    scan_limit: int = DEFAULT_DUPLICATE_SCAN_LIMIT,
    snapshot_id: str = "",
    page: int = 1,
    page_size: int = DUPLICATE_LISTING_PAGE_SIZE,
) -> dict:
    if _normalize_input_path(absolute_path) == DEVICE_ROOT_SENTINEL:
        raise HTTPException(status_code=400, detail="Please choose a concrete disk or directory")

    target_path, resolved = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target_path.is_dir() and not target_path.is_file():
        raise HTTPException(status_code=400, detail="Path is neither file nor directory")

    normalized_rules = _normalize_duplicate_rules(rules)
    normalized_filter_rules = _normalize_duplicate_filter_rules(filter_rules)
    normalized_min_size = _normalize_duplicate_min_size(min_size)
    normalized_scan_limit = _normalize_duplicate_scan_limit(scan_limit)
    normalized_page = _normalize_duplicate_page(page)
    normalized_page_size = _normalize_duplicate_page_size(page_size)
    normalized_source = source if source in {"auto", "everything", "filesystem"} else "auto"
    normalized_sort_mode = sort_mode if sort_mode in {"file_size", "group_total", "reclaimable"} else "reclaimable"

    query_signature = _build_duplicate_query_signature(
        root=resolved["root"],
        path=resolved["path"],
        absolute_path=resolved["absolute_path"],
        recursive=recursive,
        rules=normalized_rules,
        filter_rules=normalized_filter_rules,
        sort_mode=normalized_sort_mode,
        source=normalized_source,
        min_size=normalized_min_size,
        scan_limit=normalized_scan_limit,
    )
    cached_snapshot = _get_duplicate_listing_snapshot(snapshot_id, query_signature)
    if cached_snapshot is not None:
        return _build_duplicate_listing_response(
            cached_snapshot,
            page=normalized_page,
            page_size=normalized_page_size,
        )

    candidates, scanned_file_count, actual_source, source_detail, complete = _load_duplicate_candidates(
        target_path,
        resolved=resolved,
        source=normalized_source,
        rules=normalized_rules,
        filter_rules=normalized_filter_rules,
        min_size=normalized_min_size,
        scan_limit=normalized_scan_limit,
        recursive=recursive,
    )
    groups, hash_computed_count = _build_duplicate_groups(
        candidates,
        rules=normalized_rules,
        sort_mode=normalized_sort_mode,
    )
    snapshot = _store_duplicate_listing_snapshot(
        query_signature=query_signature,
        root=resolved["root"],
        path=resolved["path"],
        absolute_path=resolved["absolute_path"],
        groups=groups,
        scanned_file_count=scanned_file_count,
        candidate_file_count=len(candidates),
        hash_computed_count=hash_computed_count,
        source=actual_source,
        source_detail=source_detail,
        complete=complete,
    )
    return _build_duplicate_listing_response(
        snapshot,
        page=normalized_page,
        page_size=normalized_page_size,
    )


def _complete_duplicate_analysis_task_from_snapshot(
    *,
    query_signature: str,
    snapshot: DuplicateListingSnapshot,
    scan_limit: int,
    message: str,
) -> DuplicateAnalysisTask:
    now = time.time()
    task = DuplicateAnalysisTask(
        task_id=uuid4().hex,
        query_signature=query_signature,
        root=snapshot.root,
        path=snapshot.path,
        absolute_path=snapshot.absolute_path,
        status="completed",
        stage="cached",
        message=message,
        groups=list(snapshot.groups),
        scanned_file_count=snapshot.scanned_file_count,
        candidate_file_count=snapshot.candidate_file_count,
        hash_computed_count=snapshot.hash_computed_count,
        source=snapshot.source,
        source_detail=snapshot.source_detail,
        complete=snapshot.complete,
        scan_limit=scan_limit,
        snapshot_id=snapshot.snapshot_id,
        error=None,
        created_at=now,
        started_at=now,
        updated_at=now,
        finished_at=now,
    )
    _store_duplicate_analysis_task(task)
    return task


def _finish_duplicate_analysis_task(
    task_id: str,
    *,
    query_signature: str,
    root: str | None,
    path: str,
    absolute_path: str,
    groups: list[dict],
    scanned_file_count: int,
    candidate_file_count: int,
    hash_computed_count: int,
    source: str,
    source_detail: str,
    complete: bool,
) -> None:
    snapshot = _store_duplicate_listing_snapshot(
        query_signature=query_signature,
        root=root,
        path=path,
        absolute_path=absolute_path,
        groups=groups,
        scanned_file_count=scanned_file_count,
        candidate_file_count=candidate_file_count,
        hash_computed_count=hash_computed_count,
        source=source,
        source_detail=source_detail,
        complete=complete,
    )
    now = time.time()
    _update_duplicate_analysis_task(
        task_id,
        status="completed",
        stage="completed",
        message="分析完成",
        groups=groups,
        scanned_file_count=scanned_file_count,
        candidate_file_count=candidate_file_count,
        hash_computed_count=hash_computed_count,
        source=source,
        source_detail=source_detail,
        complete=complete,
        snapshot_id=snapshot.snapshot_id,
        error=None,
        finished_at=now,
    )


def _run_duplicate_analysis_task(
    task_id: str,
    target_path: Path,
    resolved: dict,
    *,
    query_signature: str,
    recursive: bool,
    rules: tuple[str, ...],
    filter_rules: tuple[dict, ...],
    sort_mode: str,
    source: str,
    min_size: int,
    scan_limit: int,
) -> None:
    now = time.time()
    _update_duplicate_analysis_task(
        task_id,
        status="running",
        stage="starting",
        message="准备分析",
        started_at=now,
    )

    def publish_partial(
        *,
        candidates: list[DuplicateFileCandidate],
        scanned_file_count: int,
        source_name: str,
        source_detail: str,
        message: str,
        force_groups: bool = False,
    ) -> None:
        groups: list[dict] = []
        hash_computed_count = 0
        if candidates and "sha256" not in rules and force_groups:
            groups, hash_computed_count = _build_duplicate_groups(
                list(candidates),
                rules=rules,
                sort_mode=sort_mode,
            )
        _update_duplicate_analysis_task(
            task_id,
            groups=groups,
            scanned_file_count=scanned_file_count,
            candidate_file_count=len(candidates),
            hash_computed_count=hash_computed_count,
            source=source_name,
            source_detail=source_detail,
            message=message,
        )

    try:
        cached_candidates = _find_duplicate_candidate_cache(
            target_path,
            source=source,
            recursive=recursive,
            filter_rules=filter_rules,
            min_size=min_size,
        )
        if cached_candidates is not None:
            cache, candidates = cached_candidates
            _update_duplicate_analysis_task(
                task_id,
                stage="cached",
                message="复用已扫描缓存",
                source=cache.source,
                source_detail=f"cache: {cache.source_detail}",
                scanned_file_count=len(candidates),
                candidate_file_count=len(candidates),
            )
            groups, hash_computed_count = _build_duplicate_groups(
                candidates,
                rules=rules,
                sort_mode=sort_mode,
            )
            _finish_duplicate_analysis_task(
                task_id,
                query_signature=query_signature,
                root=resolved["root"],
                path=resolved["path"],
                absolute_path=resolved["absolute_path"],
                groups=groups,
                scanned_file_count=len(candidates),
                candidate_file_count=len(candidates),
                hash_computed_count=hash_computed_count,
                source=cache.source,
                source_detail=f"cache: {cache.source_detail}",
                complete=cache.complete,
            )
            return

        if source in {"auto", "everything"}:
            _update_duplicate_analysis_task(
                task_id,
                stage="everything",
                message="正在调用 Everything",
                source="everything",
                source_detail="Everything ES",
            )
            everything_result = _load_duplicate_candidates_from_everything(
                target_path,
                resolved=resolved,
                rules=rules,
                filter_rules=filter_rules,
                min_size=min_size,
                scan_limit=scan_limit,
            )
            if everything_result is not None:
                candidates, source_detail = everything_result
                complete = len(candidates) < scan_limit
                _update_duplicate_analysis_task(
                    task_id,
                    stage="grouping",
                    message="正在整理重复组",
                    scanned_file_count=len(candidates),
                    candidate_file_count=len(candidates),
                    source="everything",
                    source_detail=source_detail,
                    complete=complete,
                )
                groups, hash_computed_count = _build_duplicate_groups(
                    candidates,
                    rules=rules,
                    sort_mode=sort_mode,
                )
                _store_duplicate_candidate_cache(
                    root=resolved["root"],
                    path=resolved["path"],
                    absolute_path=resolved["absolute_path"],
                    recursive=recursive,
                    source="everything",
                    source_detail=source_detail,
                    filter_rules=filter_rules,
                    min_size=min_size,
                    candidates=candidates,
                    scanned_file_count=len(candidates),
                    complete=complete,
                )
                _finish_duplicate_analysis_task(
                    task_id,
                    query_signature=query_signature,
                    root=resolved["root"],
                    path=resolved["path"],
                    absolute_path=resolved["absolute_path"],
                    groups=groups,
                    scanned_file_count=len(candidates),
                    candidate_file_count=len(candidates),
                    hash_computed_count=hash_computed_count,
                    source="everything",
                    source_detail=source_detail,
                    complete=complete,
                )
                return
            if source == "everything":
                raise HTTPException(status_code=501, detail="Everything ES is not available on this device")

        candidates: list[DuplicateFileCandidate] = []
        scanned_file_count = 0
        complete = True
        root_path = None if resolved["is_absolute"] else resolve_root_path(resolved["root"])
        last_publish_time = time.monotonic()
        last_publish_candidate_count = 0
        _update_duplicate_analysis_task(
            task_id,
            stage="scanning",
            message="正在遍历文件",
            source="filesystem",
            source_detail="filesystem traversal",
        )
        for file_path in _iter_duplicate_candidate_paths(target_path, recursive=recursive):
            candidate = _build_duplicate_candidate_from_path(file_path, resolved=resolved, root_path=root_path)
            if candidate is None:
                continue
            scanned_file_count += 1
            if _duplicate_path_allowed(candidate.absolute_path, filter_rules) and candidate.size >= min_size:
                candidates.append(candidate)
            if scanned_file_count >= scan_limit:
                complete = False
                break

            now_monotonic = time.monotonic()
            candidate_delta = len(candidates) - last_publish_candidate_count
            if (
                now_monotonic - last_publish_time >= DUPLICATE_PARTIAL_GROUP_INTERVAL_SECONDS
                or candidate_delta >= DUPLICATE_PARTIAL_GROUP_CANDIDATE_STEP
            ):
                last_publish_time = now_monotonic
                last_publish_candidate_count = len(candidates)
                publish_partial(
                    candidates=candidates,
                    scanned_file_count=scanned_file_count,
                    source_name="filesystem",
                    source_detail="filesystem traversal",
                    message="正在遍历文件",
                    force_groups="sha256" not in rules,
                )

        _update_duplicate_analysis_task(
            task_id,
            stage="hashing" if "sha256" in rules else "grouping",
            message="正在计算哈希" if "sha256" in rules else "正在整理重复组",
            scanned_file_count=scanned_file_count,
            candidate_file_count=len(candidates),
            source="filesystem",
            source_detail="filesystem traversal",
            complete=complete,
        )
        groups, hash_computed_count = _build_duplicate_groups(
            candidates,
            rules=rules,
            sort_mode=sort_mode,
        )
        _store_duplicate_candidate_cache(
            root=resolved["root"],
            path=resolved["path"],
            absolute_path=resolved["absolute_path"],
            recursive=recursive,
            source="filesystem",
            source_detail="filesystem traversal",
            filter_rules=filter_rules,
            min_size=min_size,
            candidates=candidates,
            scanned_file_count=scanned_file_count,
            complete=complete,
        )
        _finish_duplicate_analysis_task(
            task_id,
            query_signature=query_signature,
            root=resolved["root"],
            path=resolved["path"],
            absolute_path=resolved["absolute_path"],
            groups=groups,
            scanned_file_count=scanned_file_count,
            candidate_file_count=len(candidates),
            hash_computed_count=hash_computed_count,
            source="filesystem",
            source_detail="filesystem traversal",
            complete=complete,
        )
    except Exception as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        now = time.time()
        _update_duplicate_analysis_task(
            task_id,
            status="failed",
            stage="failed",
            message=str(detail),
            error=str(detail),
            finished_at=now,
            complete=False,
        )


def start_duplicate_file_analysis(
    root_key: Optional[str] = None,
    rel_path: str = "",
    absolute_path: str = "",
    *,
    recursive: bool = True,
    rules: Iterable[str] | None = None,
    filter_rules: Iterable[DuplicatePathFilterRule | dict | object] | None = None,
    sort_mode: DuplicateSortMode = "reclaimable",
    source: DuplicateSourceMode = "auto",
    min_size: int = 1024 * 1024,
    scan_limit: int = DEFAULT_DUPLICATE_SCAN_LIMIT,
    page: int = 1,
    page_size: int = DUPLICATE_LISTING_PAGE_SIZE,
) -> dict:
    if _normalize_input_path(absolute_path) == DEVICE_ROOT_SENTINEL:
        raise HTTPException(status_code=400, detail="Please choose a concrete disk or directory")

    target_path, resolved = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target_path.is_dir() and not target_path.is_file():
        raise HTTPException(status_code=400, detail="Path is neither file nor directory")

    normalized_rules = _normalize_duplicate_rules(rules)
    normalized_filter_rules = _normalize_duplicate_filter_rules(filter_rules)
    normalized_min_size = _normalize_duplicate_min_size(min_size)
    normalized_scan_limit = _normalize_duplicate_scan_limit(scan_limit)
    normalized_page = _normalize_duplicate_page(page)
    normalized_page_size = _normalize_duplicate_page_size(page_size)
    normalized_source = source if source in {"auto", "everything", "filesystem"} else "auto"
    normalized_sort_mode = sort_mode if sort_mode in {"file_size", "group_total", "reclaimable"} else "reclaimable"
    query_signature = _build_duplicate_query_signature(
        root=resolved["root"],
        path=resolved["path"],
        absolute_path=resolved["absolute_path"],
        recursive=recursive,
        rules=normalized_rules,
        filter_rules=normalized_filter_rules,
        sort_mode=normalized_sort_mode,
        source=normalized_source,
        min_size=normalized_min_size,
        scan_limit=normalized_scan_limit,
    )

    with _duplicate_analysis_task_lock:
        for task in _duplicate_analysis_tasks.values():
            if task.query_signature == query_signature and task.status in {"queued", "running"}:
                return _build_duplicate_task_response(
                    task,
                    page=normalized_page,
                    page_size=normalized_page_size,
                )

    cached_snapshot = _find_duplicate_listing_snapshot_by_signature(query_signature)
    if cached_snapshot is not None:
        task = _complete_duplicate_analysis_task_from_snapshot(
            query_signature=query_signature,
            snapshot=cached_snapshot,
            scan_limit=normalized_scan_limit,
            message="复用查询缓存",
        )
        return _build_duplicate_task_response(
            task,
            page=normalized_page,
            page_size=normalized_page_size,
        )

    now = time.time()
    task = DuplicateAnalysisTask(
        task_id=uuid4().hex,
        query_signature=query_signature,
        root=resolved["root"],
        path=resolved["path"],
        absolute_path=resolved["absolute_path"],
        status="queued",
        stage="queued",
        message="已加入分析队列",
        groups=[],
        scanned_file_count=0,
        candidate_file_count=0,
        hash_computed_count=0,
        source=normalized_source,
        source_detail="",
        complete=False,
        scan_limit=normalized_scan_limit,
        snapshot_id="",
        error=None,
        created_at=now,
        started_at=None,
        updated_at=now,
        finished_at=None,
    )
    _store_duplicate_analysis_task(task)
    _duplicate_analysis_executor.submit(
        _run_duplicate_analysis_task,
        task.task_id,
        target_path,
        resolved,
        query_signature=query_signature,
        recursive=recursive,
        rules=normalized_rules,
        filter_rules=normalized_filter_rules,
        sort_mode=normalized_sort_mode,
        source=normalized_source,
        min_size=normalized_min_size,
        scan_limit=normalized_scan_limit,
    )
    return _build_duplicate_task_response(
        task,
        page=normalized_page,
        page_size=normalized_page_size,
    )


def _is_supported_image(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return True
    guessed_type, _ = mimetypes.guess_type(os.fspath(path))
    return bool(guessed_type and guessed_type.startswith("image/"))


def _resolve_media_kind(path: Path) -> tuple[str, str | None] | None:
    suffix = path.suffix.lower()
    guessed_type, _ = mimetypes.guess_type(os.fspath(path))

    if suffix in IMAGE_EXTENSIONS or (guessed_type and guessed_type.startswith("image/")):
        return "image", guessed_type

    if suffix in VIDEO_EXTENSIONS or (guessed_type and guessed_type.startswith("video/")):
        return "video", guessed_type

    if suffix in PDF_EXTENSIONS or guessed_type == "application/pdf":
        return "pdf", guessed_type or "application/pdf"

    return None


def _normalize_positive_dimension(value: object) -> int | None:
    try:
        numeric = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def _resolve_created_at_ms(stat_result: os.stat_result) -> int | None:
    raw_value = getattr(stat_result, "st_birthtime", None)
    if raw_value in {None, 0}:
        raw_value = getattr(stat_result, "st_ctime", None)

    try:
        created_at_seconds = float(raw_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None

    if not math.isfinite(created_at_seconds) or created_at_seconds <= 0:
        return None
    return int(created_at_seconds * 1000)


def _probe_image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as source_image:
            normalized_image = ImageOps.exif_transpose(source_image)
            width = _normalize_positive_dimension(normalized_image.width)
            height = _normalize_positive_dimension(normalized_image.height)
            return width, height
    except (OSError, UnidentifiedImageError):
        return None, None


def _probe_video_metadata(path: Path, ffprobe_bin: str) -> tuple[int | None, int | None, int | None]:
    try:
        result = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                os.fspath(path),
            ],
            capture_output=True,
            check=True,
            timeout=8,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return None, None, None

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None, None, None

    raw_duration = ((payload.get("format") or {}).get("duration"))
    duration_ms = None
    if raw_duration not in {None, ""}:
        try:
            duration_seconds = float(raw_duration)
        except (TypeError, ValueError):
            duration_seconds = math.nan
        if math.isfinite(duration_seconds) and duration_seconds >= 0:
            duration_ms = int(round(duration_seconds * 1000))

    width_px = None
    height_px = None
    for stream in payload.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        width_px = _normalize_positive_dimension(stream.get("width"))
        height_px = _normalize_positive_dimension(stream.get("height"))
        if width_px and height_px:
            break

    return duration_ms, width_px, height_px


def _attach_cached_media_metadata(
    entries: list[dict],
    session: Session | None = None,
    *,
    include_visual_hash: bool = False,
    prewarm_visual_hash_key: str | None = None,
) -> dict:
    if not entries:
        return _build_visual_hash_status(include_visual_hash=include_visual_hash, total_image_count=0, indexed_count=0)

    prewarm_requested = bool(not include_visual_hash and str(prewarm_visual_hash_key or "").strip())

    try:
        from backend.core.device import get_device_id
        from backend.core.device_file_cover import (
            DeviceFileMetadataSnapshot,
            upsert_device_file_metadata_batch,
        )
        from backend.models import DeviceFile
    except Exception:
        for entry in entries:
            entry.pop("_absolute_identity_path", None)
            entry.pop("_file_path", None)
        return _build_visual_hash_status(include_visual_hash=include_visual_hash, total_image_count=0, indexed_count=0)

    try:
        device_id = get_device_id()
    except Exception:
        device_id = ""

    absolute_paths = [
        str(entry["_absolute_identity_path"])
        for entry in entries
        if entry.get("_absolute_identity_path")
    ]
    cached_records = _load_cached_device_records_by_path(session, device_id, absolute_paths)

    ffprobe_bin = shutil.which("ffprobe")
    pending_visual_hash_items: list[dict] = []
    prewarm_candidates: list[VisualHashPrewarmCandidate] = []
    snapshot_refs: list[tuple[dict, str]] = []
    total_image_count = 0
    browse_indexed_count = 0
    for entry in entries:
        absolute_identity_path = str(entry.get("_absolute_identity_path", "") or "")
        file_path = entry.pop("_file_path", None)
        if file_path is None and absolute_identity_path:
            file_path = Path(absolute_identity_path)
        cached_record = cached_records.get(absolute_identity_path) if absolute_identity_path else None

        duration_ms = None
        width_px = None
        height_px = None
        content_hash = None
        hash_algorithm = "sha256"
        visual_hash = None
        visual_hash_algorithm = "dhash-8"
        matches_cached_file = (
            cached_record is not None
            and cached_record.file_size == entry.get("size")
            and cached_record.modified_at_ms == entry.get("modified_at")
        )
        cached_visual_hash = (
            _normalize_optional_hash(cached_record.visual_hash)
            if matches_cached_file and cached_record is not None
            else None
        )

        if entry.get("kind") == "image":
            total_image_count += 1
            if matches_cached_file and cached_record:
                width_px = cached_record.width_px
                height_px = cached_record.height_px
                content_hash = cached_record.content_hash
                hash_algorithm = cached_record.hash_algorithm or hash_algorithm
                if include_visual_hash:
                    visual_hash = cached_visual_hash
                    visual_hash_algorithm = cached_record.visual_hash_algorithm or visual_hash_algorithm
                elif cached_visual_hash:
                    browse_indexed_count += 1
            if (width_px is None or height_px is None) and isinstance(file_path, Path):
                probed_width, probed_height = _probe_image_dimensions(file_path)
                width_px = probed_width if probed_width is not None else width_px
                height_px = probed_height if probed_height is not None else height_px

        if entry.get("kind") == "video":
            if matches_cached_file and cached_record:
                duration_ms = cached_record.duration_ms
                width_px = cached_record.width_px
                height_px = cached_record.height_px
                content_hash = cached_record.content_hash
                hash_algorithm = cached_record.hash_algorithm or hash_algorithm

            if ffprobe_bin and isinstance(file_path, Path) and (
                duration_ms is None or width_px is None or height_px is None
            ):
                probed_duration_ms, probed_width, probed_height = _probe_video_metadata(file_path, ffprobe_bin)
                duration_ms = probed_duration_ms if probed_duration_ms is not None else duration_ms
                width_px = probed_width if probed_width is not None else width_px
                height_px = probed_height if probed_height is not None else height_px

        entry["duration_ms"] = duration_ms
        entry["width"] = width_px
        entry["height"] = height_px
        entry["aspect_ratio"] = (width_px / height_px) if width_px and height_px else None
        entry["weight"] = cached_record.weight if cached_record else 0
        entry["content_hash"] = content_hash
        entry["hash_algorithm"] = hash_algorithm
        entry["visual_hash"] = visual_hash if include_visual_hash else None
        entry["visual_hash_algorithm"] = visual_hash_algorithm if include_visual_hash else None
        if (
            entry.get("kind") == "image"
            and include_visual_hash
            and entry["visual_hash"] is None
            and isinstance(file_path, Path)
        ):
            pending_visual_hash_items.append({
                "entry": entry,
                "file_path": file_path,
            })
        if (
            entry.get("kind") == "image"
            and not include_visual_hash
            and isinstance(file_path, Path)
            and absolute_identity_path
        ):
            if not cached_visual_hash:
                prewarm_candidates.append(
                    VisualHashPrewarmCandidate(
                        absolute_path=absolute_identity_path,
                        last_known_path=absolute_identity_path,
                        file_size=entry.get("size"),
                        modified_at_ms=entry.get("modified_at"),
                        content_hash=_normalize_optional_hash(content_hash),
                        hash_algorithm=_normalize_hash_algorithm(hash_algorithm, "sha256"),
                        media_kind=entry.get("kind"),
                        mime_type=entry.get("mime_type"),
                    )
                )
        if device_id and absolute_identity_path:
            snapshot_refs.append((entry, absolute_identity_path))

    reused_content_hash_count = 0
    computed_count = 0
    if include_visual_hash and pending_visual_hash_items:
        reused_content_hash_count, computed_count = _resolve_pending_visual_hash_items(
            pending_visual_hash_items,
            session,
            device_id,
        )

    indexed_count = (
        sum(
            1
            for entry in entries
            if entry.get("kind") == "image"
            and _normalize_optional_hash(entry.get("visual_hash"))
        )
        if include_visual_hash
        else browse_indexed_count
    )

    if session is not None and device_id and snapshot_refs:
        try:
            snapshots = [
                DeviceFileMetadataSnapshot(
                    absolute_path=absolute_identity_path,
                    last_known_path=absolute_identity_path,
                    file_size=entry.get("size"),
                    modified_at_ms=entry.get("modified_at"),
                    content_hash=_normalize_optional_hash(entry.get("content_hash")),
                    hash_algorithm=_normalize_hash_algorithm(entry.get("hash_algorithm"), "sha256"),
                    visual_hash=_normalize_optional_hash(entry.get("visual_hash")) if include_visual_hash else None,
                    visual_hash_algorithm=_normalize_hash_algorithm(entry.get("visual_hash_algorithm"), "dhash-8"),
                    duration_ms=entry.get("duration_ms"),
                    width_px=entry.get("width"),
                    height_px=entry.get("height"),
                    media_kind=entry.get("kind"),
                    mime_type=entry.get("mime_type"),
                )
                for entry, absolute_identity_path in snapshot_refs
            ]
            upsert_device_file_metadata_batch(session, device_id, snapshots)
        except Exception:
            pass
        else:
            if prewarm_requested and prewarm_candidates:
                _schedule_visual_hash_prewarm(prewarm_visual_hash_key, device_id, prewarm_candidates)
    return _build_visual_hash_status(
        include_visual_hash=include_visual_hash,
        total_image_count=total_image_count,
        indexed_count=indexed_count,
        computed_count=computed_count,
        reused_content_hash_count=reused_content_hash_count,
        prewarm_scheduled_count=(
            min(len(prewarm_candidates), VISUAL_HASH_PREWARM_BATCH_SIZE)
            if (prewarm_requested and prewarm_candidates)
            else 0
        ),
    )


def _create_media_sort_program_from_mode(sort_mode: MediaSortMode) -> GallerySortProgram:
    if sort_mode == "modified-desc":
        return GallerySortProgram(rules=[GallerySortRule(field="modified_at", direction="desc", nulls="last")])
    if sort_mode == "size-desc":
        return GallerySortProgram(rules=[GallerySortRule(field="size", direction="desc", nulls="last")])
    if sort_mode == "weight-desc":
        return GallerySortProgram(rules=[GallerySortRule(field="weight", direction="desc", nulls="last")])
    return GallerySortProgram(rules=[GallerySortRule(field="relative_path", direction="asc", nulls="last")])


def _normalize_media_sort_program(
    sort_mode: MediaSortMode,
    sort_program: GallerySortProgram | None,
) -> list[GallerySortRule]:
    base_program = sort_program if sort_program and sort_program.rules else _create_media_sort_program_from_mode(sort_mode)
    return [*base_program.rules, *GALLERY_SORT_FALLBACK_RULES]


def _compute_media_random_order(seed: str, item: dict) -> int:
    basis = str(
        item.get("_absolute_identity_path")
        or item.get("absolute_path")
        or item.get("path")
        or item.get("id")
        or ""
    )
    digest = hashlib.blake2b(f"{seed}:{basis}".encode("utf-8", "ignore"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False)


def _populate_media_random_sort_values(entries: list[dict], rules: list[GallerySortRule]) -> None:
    if not any(rule.field == "random" for rule in rules):
        return

    seed = uuid4().hex
    for entry in entries:
        entry["_random_order"] = _compute_media_random_order(seed, entry)


def _media_sort_rules_use_duplicate_cluster(rules: list[GallerySortRule]) -> bool:
    return any(rule.field == "duplicate_cluster" for rule in rules)


def _get_media_duplicate_cluster_baseline_rules(rules: list[GallerySortRule]) -> list[GallerySortRule]:
    comparison_rules = [rule for rule in rules if rule.field != "duplicate_cluster"]
    if comparison_rules:
        return comparison_rules
    return list(GALLERY_SORT_FALLBACK_RULES)


def _populate_media_duplicate_cluster_values(entries: list[dict], rules: list[GallerySortRule]) -> None:
    duplicate_rule_active = _media_sort_rules_use_duplicate_cluster(rules)
    for entry in entries:
        entry.pop("duplicate_cluster_order", None)
        entry.pop("duplicate_cluster_distance", None)
        entry.pop("duplicate_cluster_member_order", None)
        entry.pop("duplicate_cluster_size", None)

    if not duplicate_rule_active or not entries:
        return

    comparison_rules = _get_media_duplicate_cluster_baseline_rules(rules)
    baseline_entries = sorted(
        entries,
        key=cmp_to_key(lambda left, right: _compare_media_entries(left, right, comparison_rules)),
    )

    cluster_visual_anchors: dict[int, int] = {}
    cluster_by_content_hash: dict[str, int] = {}
    cluster_members: list[list[tuple[dict, int, int]]] = []

    for baseline_rank, entry in enumerate(baseline_entries):
        normalized_content_hash = str(entry.get("content_hash") or "").strip().lower() or None
        visual_hash_int = _parse_visual_hash_int(str(entry.get("visual_hash") or "").strip().lower() or None)

        cluster_index = cluster_by_content_hash.get(normalized_content_hash) if normalized_content_hash else None
        cluster_distance = 0
        if cluster_index is None and visual_hash_int is not None:
            best_cluster_index = None
            best_distance = None
            for candidate_cluster_index, anchor_hash_int in cluster_visual_anchors.items():
                distance = (visual_hash_int ^ anchor_hash_int).bit_count()
                if distance > DUPLICATE_CLUSTER_VISUAL_THRESHOLD:
                    continue
                if (
                    best_distance is None
                    or distance < best_distance
                    or (distance == best_distance and candidate_cluster_index < (best_cluster_index or 0))
                ):
                    best_cluster_index = candidate_cluster_index
                    best_distance = distance
            if best_cluster_index is not None:
                cluster_index = best_cluster_index
                cluster_distance = int(best_distance or 0)

        if cluster_index is None:
            cluster_index = len(cluster_members)
            cluster_members.append([])

        cluster_members[cluster_index].append((entry, cluster_distance, baseline_rank))

        if normalized_content_hash:
            cluster_by_content_hash.setdefault(normalized_content_hash, cluster_index)
        if visual_hash_int is not None and cluster_index not in cluster_visual_anchors:
            cluster_visual_anchors[cluster_index] = visual_hash_int

    for cluster_index, members in enumerate(cluster_members):
        ordered_members = sorted(
            members,
            key=lambda item: (
                int(item[1]),
                int(item[2]),
                str(item[0].get("id") or ""),
            ),
        )
        cluster_size = len(ordered_members)
        for member_order, (entry, distance, _) in enumerate(ordered_members):
            entry["duplicate_cluster_order"] = cluster_index
            entry["duplicate_cluster_distance"] = int(distance)
            entry["duplicate_cluster_member_order"] = member_order
            entry["duplicate_cluster_size"] = cluster_size


def _get_media_sort_value(item: dict, field: GallerySortField) -> str | int | None:
    if field == "random":
        random_order = item.get("_random_order")
        if isinstance(random_order, int):
            return random_order
        return _compute_media_random_order("stable-random", item)
    if field == "duplicate_cluster":
        cluster_order = item.get("duplicate_cluster_order")
        return int(cluster_order) if isinstance(cluster_order, (int, float)) else None
    if field == "weight":
        return int(item.get("weight") or 0)
    if field == "modified_at":
        return int(item.get("modified_at") or 0)
    if field == "size":
        return int(item.get("size") or 0)
    if field == "duration":
        duration_ms = item.get("duration_ms")
        return int(duration_ms) if isinstance(duration_ms, (int, float)) else None
    if field == "relative_path":
        return str(item.get("relative_path") or "").lower()
    if field == "name":
        return str(item.get("name") or "").lower()
    if field == "folder_path":
        return str(item.get("folder_path") or "").lower()
    if field == "kind":
        return str(item.get("kind") or "image").lower()
    if field == "width":
        width = item.get("width")
        return int(width) if isinstance(width, (int, float)) else None
    if field == "height":
        height = item.get("height")
        return int(height) if isinstance(height, (int, float)) else None
    if field == "resolution_area":
        width = item.get("width")
        height = item.get("height")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            return int(width) * int(height)
        return None
    return None


def _compare_media_sort_values(
    left_value: str | int | None,
    right_value: str | int | None,
    *,
    direction: GallerySortDirection,
    nulls: GallerySortNulls,
) -> int:
    left_missing = left_value is None
    right_missing = right_value is None
    if left_missing or right_missing:
        if left_missing and right_missing:
            return 0
        if nulls == "first":
            return -1 if left_missing else 1
        return 1 if left_missing else -1

    if isinstance(left_value, str) and isinstance(right_value, str):
        if left_value < right_value:
            result = -1
        elif left_value > right_value:
            result = 1
        else:
            result = 0
    else:
        left_number = int(left_value)
        right_number = int(right_value)
        result = (left_number > right_number) - (left_number < right_number)

    if result == 0:
        return 0
    return -result if direction == "desc" else result


def _compare_duplicate_cluster_rule(left: dict, right: dict, rule: GallerySortRule) -> int:
    result = _compare_media_sort_values(
        _get_media_sort_value(left, "duplicate_cluster"),
        _get_media_sort_value(right, "duplicate_cluster"),
        direction=rule.direction,
        nulls=rule.nulls,
    )
    if result != 0:
        return result
    result = _compare_media_sort_values(
        int(left.get("duplicate_cluster_distance")) if isinstance(left.get("duplicate_cluster_distance"), (int, float)) else None,
        int(right.get("duplicate_cluster_distance")) if isinstance(right.get("duplicate_cluster_distance"), (int, float)) else None,
        direction="asc",
        nulls="last",
    )
    if result != 0:
        return result
    return _compare_media_sort_values(
        int(left.get("duplicate_cluster_member_order")) if isinstance(left.get("duplicate_cluster_member_order"), (int, float)) else None,
        int(right.get("duplicate_cluster_member_order")) if isinstance(right.get("duplicate_cluster_member_order"), (int, float)) else None,
        direction="asc",
        nulls="last",
    )


def _compare_media_sort_rule(left: dict, right: dict, rule: GallerySortRule) -> int:
    if rule.field == "duplicate_cluster":
        return _compare_duplicate_cluster_rule(left, right, rule)
    left_value = _get_media_sort_value(left, rule.field)
    right_value = _get_media_sort_value(right, rule.field)
    return _compare_media_sort_values(left_value, right_value, direction=rule.direction, nulls=rule.nulls)


def _compare_media_entries(left: dict, right: dict, rules: list[GallerySortRule]) -> int:
    for rule in rules:
        result = _compare_media_sort_rule(left, right, rule)
        if result != 0:
            return result

    left_id = str(left.get("id") or "")
    right_id = str(right.get("id") or "")
    if left_id < right_id:
        return -1
    if left_id > right_id:
        return 1
    return 0


def _sort_media_entries_by_rules(entries: list[dict], rules: list[GallerySortRule]) -> None:
    entries.sort(key=cmp_to_key(lambda left, right: _compare_media_entries(left, right, rules)))


def _apply_duplicate_cluster_sort_to_entries(entries: list[dict], rules: list[GallerySortRule]) -> None:
    if not entries or not _media_sort_rules_use_duplicate_cluster(rules):
        return
    _populate_media_duplicate_cluster_values(entries, rules)
    _sort_media_entries_by_rules(entries, rules)


def _sort_supported_media_entries(
    entries: list[dict],
    sort_mode: MediaSortMode,
    sort_program: GallerySortProgram | None,
) -> list[GallerySortRule]:
    normalized_rules = _normalize_media_sort_program(sort_mode, sort_program)
    _populate_media_random_sort_values(entries, normalized_rules)
    _apply_duplicate_cluster_sort_to_entries(entries, normalized_rules)
    return normalized_rules


def _prepare_media_listing_snapshot_entries(
    entries: list[dict],
    sort_mode: MediaSortMode,
    sort_program: GallerySortProgram | None,
) -> list[GallerySortRule]:
    normalized_rules = _normalize_media_sort_program(sort_mode, sort_program)
    _populate_media_random_sort_values(entries, normalized_rules)
    snapshot_rules = (
        _get_media_duplicate_cluster_baseline_rules(normalized_rules)
        if _media_sort_rules_use_duplicate_cluster(normalized_rules)
        else normalized_rules
    )
    _sort_media_entries_by_rules(entries, snapshot_rules)
    return normalized_rules


def _serialize_media_entries(entries: list[dict]) -> list[dict]:
    return [
        {
            key: value
            for key, value in entry.items()
            if not (isinstance(key, str) and key.startswith("_"))
        }
        for entry in entries
    ]


def _estimate_masonry_item_height(item: dict, column_width: int) -> float:
    width_px = _normalize_positive_dimension(item.get("width"))
    height_px = _normalize_positive_dimension(item.get("height"))
    if width_px and height_px:
        return max(1.0, (column_width * height_px) / width_px)
    return float(max(1, column_width))


def _build_media_layout(
    entries: list[dict],
    *,
    layout_mode: str,
    layout_columns: int,
    layout_column_width: int,
    layout_gap: int,
    layout_column_heights: list[float],
) -> dict | None:
    if layout_mode != "masonry":
        return None

    column_count = max(1, min(int(layout_columns or 0), 12))
    column_width = max(48, int(layout_column_width or 0))
    gap = max(0, int(layout_gap or 0))
    initial_heights = [
        max(0.0, float(value))
        for value in layout_column_heights[:column_count]
        if isinstance(value, (int, float))
    ]
    while len(initial_heights) < column_count:
        initial_heights.append(0.0)

    columns = [[] for _ in range(column_count)]
    next_heights = initial_heights[:]
    for item in entries:
        target_column = min(range(column_count), key=lambda index: (next_heights[index], index))
        columns[target_column].append(str(item.get("id") or ""))
        if next_heights[target_column] > 0 and gap:
            next_heights[target_column] += gap
        next_heights[target_column] += _estimate_masonry_item_height(item, column_width)

    return {
        "mode": "masonry",
        "column_count": column_count,
        "column_width": column_width,
        "gap": gap,
        "columns": columns,
        "column_heights": next_heights,
    }


def _prune_media_listing_snapshots(now: float | None = None) -> None:
    current_time = now if now is not None else time.time()
    expired_ids = [
        snapshot_id
        for snapshot_id, snapshot in _media_listing_snapshots.items()
        if current_time - snapshot.last_accessed_at > MEDIA_LISTING_SNAPSHOT_TTL_SECONDS
    ]
    for snapshot_id in expired_ids:
        _media_listing_snapshots.pop(snapshot_id, None)

    while len(_media_listing_snapshots) > MEDIA_LISTING_SNAPSHOT_LIMIT:
        _media_listing_snapshots.popitem(last=False)


def _build_media_listing_query_signature(
    *,
    device_id: str,
    root: str | None,
    path: str,
    absolute_path: str,
    recursive: bool,
    scan_limit: int,
    allowed_kinds: set[str],
    normalized_rules: list[GallerySortRule],
) -> str:
    payload = {
        "device_id": device_id,
        "root": root,
        "path": path,
        "absolute_path": absolute_path,
        "recursive": recursive,
        "scan_limit": scan_limit,
        "allowed_kinds": sorted(allowed_kinds),
        "sort_program": {"rules": [rule.model_dump() for rule in normalized_rules]},
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _normalize_media_scan_limit(scan_limit: int) -> int:
    normalized_limit = int(scan_limit or DEFAULT_MEDIA_SCAN_LIMIT)
    if normalized_limit <= 0:
        return DEFAULT_MEDIA_SCAN_LIMIT
    return min(normalized_limit, MAX_MEDIA_SCAN_LIMIT)


def _iter_scanned_media_files(
    target_path: Path,
    *,
    recursive: bool,
    scan_limit: int,
) -> Iterator[Path]:
    normalized_scan_limit = _normalize_media_scan_limit(scan_limit)
    scanned_files = 0
    pending_dirs = [target_path]

    while pending_dirs and scanned_files < normalized_scan_limit:
        current_dir = pending_dirs.pop()
        child_dirs: list[Path] = []
        try:
            with os.scandir(current_dir) as current_entries:
                for entry in current_entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if recursive:
                                child_dirs.append(Path(entry.path))
                            continue
                    except OSError:
                        continue

                    try:
                        if entry.is_file():
                            if scanned_files >= normalized_scan_limit:
                                break
                            scanned_files += 1
                            yield Path(entry.path)
                    except OSError:
                        continue
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Permission denied") from exc

        if recursive and child_dirs and scanned_files < normalized_scan_limit:
            pending_dirs.extend(reversed(child_dirs))


def _get_media_listing_snapshot(snapshot_id: str, query_signature: str) -> MediaListingSnapshot | None:
    normalized_snapshot_id = (snapshot_id or "").strip()
    if not normalized_snapshot_id:
        return None

    with _media_listing_snapshot_lock:
        now = time.time()
        _prune_media_listing_snapshots(now)
        snapshot = _media_listing_snapshots.get(normalized_snapshot_id)
        if snapshot is None or snapshot.query_signature != query_signature:
            return None
        snapshot.last_accessed_at = now
        _media_listing_snapshots.move_to_end(normalized_snapshot_id)
        return snapshot


def _store_media_listing_snapshot(
    *,
    query_signature: str,
    root: str | None,
    path: str,
    absolute_path: str,
    sort_mode: str,
    sort_program: dict,
    entries: list[dict],
    total_bytes: int,
    visual_hash_status: dict | None,
) -> MediaListingSnapshot:
    now = time.time()
    snapshot = MediaListingSnapshot(
        snapshot_id=uuid4().hex,
        query_signature=query_signature,
        root=root,
        path=path,
        absolute_path=absolute_path,
        sort_mode=sort_mode,
        sort_program=sort_program,
        entries=entries,
        total_bytes=total_bytes,
        visual_hash_status=visual_hash_status,
        created_at=now,
        last_accessed_at=now,
    )
    with _media_listing_snapshot_lock:
        _prune_media_listing_snapshots(now)
        _media_listing_snapshots[snapshot.snapshot_id] = snapshot
        _media_listing_snapshots.move_to_end(snapshot.snapshot_id)
        _prune_media_listing_snapshots(now)
    return snapshot


def _slice_media_listing_entries(
    entries: list[dict],
    *,
    offset: int,
    limit: int,
) -> tuple[list[dict], int, int | None]:
    total_count = len(entries)
    normalized_offset = max(0, int(offset or 0))
    normalized_limit = max(0, int(limit or 0))
    if normalized_limit > 0:
        sliced_entries = entries[normalized_offset: normalized_offset + normalized_limit]
        next_offset = normalized_offset + normalized_limit if normalized_offset + normalized_limit < total_count else None
    else:
        sliced_entries = entries[normalized_offset:]
        next_offset = None
    return sliced_entries, total_count, next_offset


def _build_media_listing_response(
    *,
    root: str | None,
    path: str,
    absolute_path: str,
    sort_mode: str,
    sort_program: dict,
    snapshot_id: str,
    entries: list[dict],
    total_bytes: int,
    visual_hash_status: dict | None,
    normalized_rules: list[GallerySortRule],
    session: Session | None,
    offset: int,
    limit: int,
    response_key: str,
    layout_mode: str,
    layout_columns: int,
    layout_column_width: int,
    layout_gap: int,
    layout_column_heights: list[float],
) -> dict:
    sliced_entries, total_count, next_offset = _slice_media_listing_entries(entries, offset=offset, limit=limit)
    response_entries = list(sliced_entries)
    response_visual_hash_status = visual_hash_status
    if _media_sort_rules_use_duplicate_cluster(normalized_rules) and response_entries:
        response_visual_hash_status = _attach_cached_media_metadata(
            response_entries,
            session,
            include_visual_hash=True,
        )
        _apply_duplicate_cluster_sort_to_entries(response_entries, normalized_rules)
    normalized_offset = max(0, int(offset or 0))
    normalized_limit = max(0, int(limit or 0))
    has_more = next_offset is not None
    layout = _build_media_layout(
        response_entries,
        layout_mode=layout_mode,
        layout_columns=layout_columns,
        layout_column_width=layout_column_width,
        layout_gap=layout_gap,
        layout_column_heights=layout_column_heights,
    )
    return {
        "root": root,
        "path": path,
        "absolute_path": absolute_path,
        "sort_mode": sort_mode,
        "sort_program": sort_program,
        "snapshot_id": snapshot_id,
        "total_count": total_count,
        "total_bytes": total_bytes,
        "visual_hash_status": response_visual_hash_status,
        "offset": normalized_offset,
        "limit": normalized_limit,
        "has_more": has_more,
        "next_offset": next_offset,
        "layout": layout,
        response_key: _serialize_media_entries(response_entries),
    }


def _list_supported_entries(
    root_key: Optional[str] = None,
    rel_path: str = "",
    absolute_path: str = "",
    *,
    recursive: bool = False,
    scan_limit: int = DEFAULT_MEDIA_SCAN_LIMIT,
    allowed_kinds: set[str],
    response_key: str,
    session: Session | None = None,
    sort_mode: MediaSortMode = "path",
    sort_program: GallerySortProgram | None = None,
    snapshot_id: str = "",
    offset: int = 0,
    limit: int = 0,
    layout_mode: str = "none",
    layout_columns: int = 0,
    layout_column_width: int = 0,
    layout_gap: int = 0,
    layout_column_heights: list[float] | None = None,
) -> dict:
    normalized_rules = _normalize_media_sort_program(sort_mode, sort_program)
    include_visual_hash = _media_sort_rules_use_duplicate_cluster(normalized_rules)
    normalized_scan_limit = _normalize_media_scan_limit(scan_limit)
    if _normalize_input_path(absolute_path) == DEVICE_ROOT_SENTINEL:
        return {
            "root": None,
            "path": DEVICE_ROOT_SENTINEL,
            "absolute_path": DEVICE_ROOT_SENTINEL,
            "sort_mode": sort_mode,
            "sort_program": {"rules": [rule.model_dump() for rule in normalized_rules]},
            "total_count": 0,
            "total_bytes": 0,
            "visual_hash_status": _build_visual_hash_status(
                include_visual_hash=include_visual_hash,
                total_image_count=0,
                indexed_count=0,
            ),
            "offset": 0,
            "limit": max(0, int(limit or 0)),
            "has_more": False,
            "next_offset": None,
            "layout": None,
            response_key: [],
        }

    target_path, resolved = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    try:
        device_id = get_device_id()
    except Exception:
        device_id = ""

    query_signature = _build_media_listing_query_signature(
        device_id=device_id,
        root=resolved["root"],
        path=resolved["path"],
        absolute_path=resolved["absolute_path"],
        recursive=recursive,
        scan_limit=normalized_scan_limit,
        allowed_kinds=allowed_kinds,
        normalized_rules=normalized_rules,
    )
    cached_snapshot = _get_media_listing_snapshot(snapshot_id, query_signature)
    if cached_snapshot is not None:
        return _build_media_listing_response(
            root=cached_snapshot.root,
            path=cached_snapshot.path,
            absolute_path=cached_snapshot.absolute_path,
            sort_mode=cached_snapshot.sort_mode,
            sort_program=cached_snapshot.sort_program,
            snapshot_id=cached_snapshot.snapshot_id,
            entries=cached_snapshot.entries,
            total_bytes=cached_snapshot.total_bytes,
            visual_hash_status=cached_snapshot.visual_hash_status,
            normalized_rules=normalized_rules,
            session=session,
            offset=offset,
            limit=limit,
            response_key=response_key,
            layout_mode=layout_mode,
            layout_columns=layout_columns,
            layout_column_width=layout_column_width,
            layout_gap=layout_gap,
            layout_column_heights=layout_column_heights or [],
        )

    root_path = None if resolved["is_absolute"] else resolve_root_path(resolved["root"])
    entries = []

    def _append_supported_file(file_path: Path) -> None:
        media_info = _resolve_media_kind(file_path)
        if not media_info:
            return

        kind, mime_type = media_info
        if kind not in allowed_kinds:
            return

        try:
            stat_result = file_path.stat()
        except OSError:
            return

        display_relative = os.fspath(file_path.relative_to(target_path)).replace("\\", "/")
        folder_path = os.path.dirname(display_relative).replace("\\", "/")
        request_path = (
            os.fspath(file_path)
            if resolved["is_absolute"]
            else os.fspath(file_path.relative_to(root_path)).replace("\\", "/")
        )
        entries.append(
            {
                "id": f"{request_path}:{stat_result.st_size}:{stat_result.st_mtime_ns}",
                "name": file_path.name,
                "path": request_path,
                "absolute_path": os.fspath(file_path) if resolved["is_absolute"] else "",
                "relative_path": display_relative,
                "folder_path": "" if folder_path == "." else folder_path,
                "size": stat_result.st_size,
                "created_at": _resolve_created_at_ms(stat_result),
                "modified_at": int(stat_result.st_mtime * 1000),
                "kind": kind,
                "mime_type": mime_type,
                "_absolute_identity_path": os.fspath(file_path.resolve(strict=False)),
                "_file_path": file_path,
            }
        )

    for file_path in _iter_scanned_media_files(
        target_path,
        recursive=recursive,
        scan_limit=normalized_scan_limit,
    ):
        _append_supported_file(file_path)

    visual_hash_status = _attach_cached_media_metadata(
        entries,
        session,
        include_visual_hash=False,
        prewarm_visual_hash_key=query_signature if include_visual_hash else None,
    )
    normalized_rules = _prepare_media_listing_snapshot_entries(entries, sort_mode, sort_program)
    sort_program_payload = {"rules": [rule.model_dump() for rule in normalized_rules]}
    total_bytes = sum(int(entry.get("size") or 0) for entry in entries)
    snapshot = _store_media_listing_snapshot(
        query_signature=query_signature,
        root=resolved["root"],
        path=resolved["path"],
        absolute_path=resolved["absolute_path"],
        sort_mode=sort_mode,
        sort_program=sort_program_payload,
        entries=entries,
        total_bytes=total_bytes,
        visual_hash_status=visual_hash_status,
    )
    return _build_media_listing_response(
        root=resolved["root"],
        path=resolved["path"],
        absolute_path=resolved["absolute_path"],
        sort_mode=sort_mode,
        sort_program=sort_program_payload,
        snapshot_id=snapshot.snapshot_id,
        entries=snapshot.entries,
        total_bytes=snapshot.total_bytes,
        visual_hash_status=snapshot.visual_hash_status,
        normalized_rules=normalized_rules,
        session=session,
        offset=offset,
        limit=limit,
        response_key=response_key,
        layout_mode=layout_mode,
        layout_columns=layout_columns,
        layout_column_width=layout_column_width,
        layout_gap=layout_gap,
        layout_column_heights=layout_column_heights or [],
    )


def list_image_entries(
    root_key: Optional[str] = None,
    rel_path: str = "",
    absolute_path: str = "",
    *,
    session: Session | None = None,
) -> dict:
    return _list_supported_entries(
        root_key,
        rel_path,
        absolute_path,
        recursive=False,
        allowed_kinds={"image"},
        response_key="images",
        session=session,
    )


def list_media_entries(
    root_key: Optional[str] = None,
    rel_path: str = "",
    absolute_path: str = "",
    *,
    recursive: bool = False,
    scan_limit: int = DEFAULT_MEDIA_SCAN_LIMIT,
    session: Session | None = None,
    sort_mode: MediaSortMode = "path",
    sort_program: GallerySortProgram | None = None,
    snapshot_id: str = "",
    offset: int = 0,
    limit: int = 0,
    layout_mode: str = "none",
    layout_columns: int = 0,
    layout_column_width: int = 0,
    layout_gap: int = 0,
    layout_column_heights: list[float] | None = None,
) -> dict:
    return _list_supported_entries(
        root_key,
        rel_path,
        absolute_path,
        recursive=recursive,
        scan_limit=scan_limit,
        allowed_kinds={"image", "video", "pdf"},
        response_key="media",
        session=session,
        sort_mode=sort_mode,
        sort_program=sort_program,
        snapshot_id=snapshot_id,
        offset=offset,
        limit=limit,
        layout_mode=layout_mode,
        layout_columns=layout_columns,
        layout_column_width=layout_column_width,
        layout_gap=layout_gap,
        layout_column_heights=layout_column_heights,
    )


def delete_scoped_entry(
    root_key: Optional[str] = None,
    rel_path: str = "",
    *,
    absolute_path: str = "",
    recursive: bool = False,
) -> dict:
    target_path, resolved = _resolve_deletable_entry(
        root_key,
        rel_path,
        absolute_path=absolute_path,
        recursive=recursive,
    )

    try:
        _delete_path_with_permission_retry(target_path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=_format_delete_permission_error(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "root": resolved["root"],
        "path": resolved["path"],
        "absolute_path": resolved["absolute_path"],
    }


def _resolve_deletable_entry(
    root_key: Optional[str] = None,
    rel_path: str = "",
    *,
    absolute_path: str = "",
    recursive: bool = False,
) -> tuple[Path, dict]:
    target_path, resolved = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if _is_filesystem_root_path(target_path):
        raise HTTPException(status_code=400, detail="Filesystem root cannot be deleted")
    if not resolved["is_absolute"] and target_path == resolve_root_path(resolved["root"]):
        raise HTTPException(status_code=400, detail="Root path cannot be deleted")
    if target_path.is_dir() and not recursive:
        raise HTTPException(status_code=400, detail="Directory deletion requires recursive=true")

    return target_path, resolved


def _make_path_writable(path: str | Path) -> None:
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass


def _delete_path_with_permission_retry(target_path: Path) -> None:
    if target_path.is_dir():
        def retry_after_chmod(function, path, excinfo):
            _make_path_writable(path)
            function(path)

        shutil.rmtree(target_path, onexc=retry_after_chmod)
        return

    try:
        target_path.unlink()
    except PermissionError:
        _make_path_writable(target_path)
        target_path.unlink()


def _format_delete_permission_error(exc: BaseException) -> str:
    return f"Permission denied. Close apps using this file or check ACL permissions: {exc}"


def _compact_task_error(error_message: Any) -> str | None:
    if not isinstance(error_message, str):
        return None
    first_line = error_message.strip().splitlines()[0] if error_message.strip() else ""
    return first_line or None


_DELETE_SCOPED_ENTRY_PROCESS_CODE = r"""
import json
import os
import pathlib
import shutil
import stat
import sys
import time
import traceback


def read_records(state_path):
    path = pathlib.Path(state_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_records(state_path, records):
    path = pathlib.Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def update_record(state_path, task_id, updates):
    records = read_records(state_path)
    record = dict(records.get(task_id) or {})
    record.update(updates)
    records[task_id] = record
    write_records(state_path, records)


def make_path_writable(path):
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass


def format_delete_error(exc):
    if isinstance(exc, PermissionError):
        return f"Permission denied. Close apps using this file or check ACL permissions: {exc}"
    return str(exc)


def append_skipped(skipped, path, exc):
    skipped.append({"path": str(path), "error": format_delete_error(exc)})


def delete_path_with_permission_retry(target):
    skipped = []
    if target.is_dir():
        def retry_after_chmod(function, path, excinfo):
            make_path_writable(path)
            try:
                function(path)
            except Exception as retry_exc:
                append_skipped(skipped, path, retry_exc)

        try:
            shutil.rmtree(target, onexc=retry_after_chmod)
        except Exception as exc:
            if not skipped:
                raise
            append_skipped(skipped, target, exc)
        return skipped

    try:
        target.unlink()
    except PermissionError:
        make_path_writable(target)
        try:
            target.unlink()
        except Exception as retry_exc:
            append_skipped(skipped, target, retry_exc)
    except Exception as exc:
        append_skipped(skipped, target, exc)
    return skipped


def main():
    task_id, target_path, recursive_raw, state_path = sys.argv[1:5]
    try:
        target = pathlib.Path(target_path)
        if target.is_dir():
            if recursive_raw != "1":
                raise RuntimeError("Directory deletion requires recursive=true")
            skipped = delete_path_with_permission_retry(target)
        else:
            skipped = delete_path_with_permission_retry(target)
        if skipped:
            first_skipped = skipped[0]
            update_record(
                state_path,
                task_id,
                {
                    "status": "partial_failed",
                    "finished_at": time.time(),
                    "return_code": 2,
                    "error_message": f"Skipped {len(skipped)} entries; first: {first_skipped['path']}: {first_skipped['error']}",
                    "skipped_count": len(skipped),
                    "skipped_paths": skipped[:50],
                },
            )
            return
        update_record(
            state_path,
            task_id,
            {
                "status": "completed",
                "finished_at": time.time(),
                "return_code": 0,
                "error_message": None,
                "skipped_count": 0,
                "skipped_paths": [],
            },
        )
    except Exception as exc:
        update_record(
            state_path,
            task_id,
            {
                "status": "failed",
                "finished_at": time.time(),
                "return_code": 1,
                "error_message": f"{format_delete_error(exc)}\n{traceback.format_exc()}",
            },
        )
        raise


if __name__ == "__main__":
    main()
"""


def _delete_tasks_state_path() -> Path:
    settings = get_settings()
    return settings.data_dir / FILESYSTEM_DELETE_TASKS_STATE_FILE


def _read_delete_task_records_unlocked() -> dict[str, dict[str, Any]]:
    state_path = _delete_tasks_state_path()
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def _write_delete_task_records_unlocked(records: dict[str, dict[str, Any]]) -> None:
    state_path = _delete_tasks_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = state_path.with_name(f"{state_path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, state_path)


def _read_delete_task_records() -> dict[str, dict[str, Any]]:
    with _filesystem_delete_task_lock:
        return _read_delete_task_records_unlocked()


def _update_delete_task_record(task_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    with _filesystem_delete_task_lock:
        records = _read_delete_task_records_unlocked()
        record = dict(records.get(task_id) or {})
        record.update(updates)
        records[task_id] = record
        _write_delete_task_records_unlocked(records)
        return record


def _process_create_time(pid: int) -> float | None:
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return None


def _delete_process_is_alive(pid: Any, pid_started_at: Any = None) -> bool:
    try:
        normalized_pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        process = psutil.Process(normalized_pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False
        if pid_started_at is not None:
            try:
                expected_started_at = float(pid_started_at)
            except (TypeError, ValueError):
                expected_started_at = 0.0
            if expected_started_at and abs(float(process.create_time()) - expected_started_at) > 2:
                return False
        return True
    except psutil.AccessDenied:
        return True
    except psutil.NoSuchProcess:
        return False


def _poll_known_delete_process(task_id: str) -> int | None:
    with _filesystem_delete_task_lock:
        process = _filesystem_delete_processes.get(task_id)
    if process is None:
        return None

    return_code = process.poll()
    if return_code is not None:
        with _filesystem_delete_task_lock:
            _filesystem_delete_processes.pop(task_id, None)
    return return_code


def _target_path_exists(record: dict[str, Any]) -> bool:
    metadata = dict(record.get("metadata") or {})
    target_path = str(record.get("target_path") or metadata.get("target_path") or "")
    if not target_path:
        return False
    try:
        return Path(target_path).exists()
    except OSError:
        return True


def _refresh_delete_task_record(record: dict[str, Any]) -> dict[str, Any]:
    task_id = str(record.get("id") or record.get("task_id") or "")
    if not task_id:
        return record

    latest = dict(_read_delete_task_records().get(task_id) or record)
    status = str(latest.get("status") or "unknown")
    if status not in {"pending", "running"}:
        return latest

    return_code = _poll_known_delete_process(task_id)
    refreshed = dict(_read_delete_task_records().get(task_id) or latest)
    if str(refreshed.get("status") or "unknown") not in {"pending", "running"}:
        return refreshed
    latest = refreshed
    pid = latest.get("pid")
    if _delete_process_is_alive(pid, latest.get("pid_started_at")):
        if status == "running":
            return latest
        return _update_delete_task_record(
            task_id,
            {
                "status": "running",
                "started_at": latest.get("started_at") or time.time(),
            },
        )

    finished_at = latest.get("finished_at") or time.time()
    if _target_path_exists(latest):
        return _update_delete_task_record(
            task_id,
            {
                "status": "failed",
                "finished_at": finished_at,
                "return_code": return_code,
                "error_message": latest.get("error_message") or "Delete process exited before reporting completion",
            },
        )

    return _update_delete_task_record(
        task_id,
        {
            "status": "completed",
            "finished_at": finished_at,
            "return_code": 0 if return_code is None else return_code,
            "error_message": None,
        },
    )


def _iter_delete_task_snapshots() -> Iterator[dict[str, Any]]:
    records = _read_delete_task_records()
    snapshots = sorted(
        records.values(),
        key=lambda item: float(item.get("queued_at") or 0),
        reverse=True,
    )
    for snapshot in snapshots:
        yield _refresh_delete_task_record(snapshot)


def _serialize_delete_task_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(snapshot.get("metadata") or {})
    return {
        "id": snapshot.get("id") or "",
        "task_id": snapshot.get("task_id") or snapshot.get("id") or "",
        "name": snapshot.get("name") or "",
        "status": snapshot.get("status") or "unknown",
        "queued_at": snapshot.get("queued_at"),
        "started_at": snapshot.get("started_at"),
        "finished_at": snapshot.get("finished_at"),
        "pid": snapshot.get("pid"),
        "pid_started_at": snapshot.get("pid_started_at"),
        "return_code": snapshot.get("return_code"),
        "skipped_count": snapshot.get("skipped_count") or 0,
        "skipped_paths": snapshot.get("skipped_paths") or [],
        "error_message": _compact_task_error(snapshot.get("error_message")),
        "metadata": metadata,
        "target_path": metadata.get("target_path") or "",
        "entry_name": metadata.get("entry_name") or "",
    }


def list_delete_task_snapshots(*, entry_id: str | None = None) -> dict:
    normalized_entry_id = str(entry_id or "").strip()
    tasks: list[dict[str, Any]] = []
    for snapshot in _iter_delete_task_snapshots():
        if snapshot.get("name") != FILESYSTEM_DELETE_TASK_NAME:
            continue
        metadata = dict(snapshot.get("metadata") or {})
        if normalized_entry_id and metadata.get("entry_id") not in {normalized_entry_id, None, ""}:
            continue
        tasks.append(_serialize_delete_task_snapshot(snapshot))
    return {"tasks": tasks}


def get_delete_task_snapshot(task_id: str, *, entry_id: str | None = None) -> dict:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        raise HTTPException(status_code=404, detail="Delete task not found")

    for task in list_delete_task_snapshots(entry_id=entry_id)["tasks"]:
        if task["id"] == normalized_task_id:
            return task
    raise HTTPException(status_code=404, detail="Delete task not found")


def enqueue_delete_scoped_entry(
    root_key: Optional[str] = None,
    rel_path: str = "",
    *,
    absolute_path: str = "",
    recursive: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict:
    target_path, resolved = _resolve_deletable_entry(
        root_key,
        rel_path,
        absolute_path=absolute_path,
        recursive=recursive,
    )
    task_metadata = {
        "root": resolved["root"],
        "path": resolved["path"],
        "absolute_path": resolved["absolute_path"],
        "target_path": os.fspath(target_path),
        "entry_name": target_path.name or os.fspath(target_path),
        "recursive": bool(recursive),
        "is_directory": target_path.is_dir(),
    }
    task_metadata.update(dict(metadata or {}))
    task_id = uuid4().hex
    now = time.time()
    _update_delete_task_record(
        task_id,
        {
            "id": task_id,
            "task_id": task_id,
            "name": FILESYSTEM_DELETE_TASK_NAME,
            "status": "pending",
            "queued_at": now,
            "started_at": None,
            "finished_at": None,
            "pid": None,
            "pid_started_at": None,
            "return_code": None,
            "skipped_count": 0,
            "skipped_paths": [],
            "error_message": None,
            "metadata": task_metadata,
        },
    )
    state_path = _delete_tasks_state_path()
    command = [
        sys.executable,
        "-c",
        _DELETE_SCOPED_ENTRY_PROCESS_CODE,
        task_id,
        os.fspath(target_path),
        "1" if recursive else "0",
        os.fspath(state_path),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        process = subprocess.Popen(  # noqa: S603 - arguments are explicit, no shell is used.
            command,
            cwd=os.fspath(ROOT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError as exc:
        _update_delete_task_record(
            task_id,
            {
                "status": "failed",
                "finished_at": time.time(),
                "error_message": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail=f"Failed to start delete process: {exc}") from exc

    with _filesystem_delete_task_lock:
        _filesystem_delete_processes[task_id] = process
    pid_started_at = _process_create_time(process.pid)
    _update_delete_task_record(
        task_id,
        {
            "status": "running",
            "started_at": time.time(),
            "pid": process.pid,
            "pid_started_at": pid_started_at,
        },
    )
    return {
        "ok": True,
        "queued": True,
        "task_id": task_id,
        "pid": process.pid,
        "task": get_delete_task_snapshot(task_id, entry_id=metadata.get("entry_id") if metadata else None),
    }


def _has_desktop_session() -> bool:
    if sys.platform in {"win32", "darwin"}:
        return True
    return bool(
        os.environ.get("DISPLAY")
        or os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("DESKTOP_SESSION")
        or os.environ.get("XDG_CURRENT_DESKTOP")
    )


def _launch_path_in_file_manager(target_path: Path) -> tuple[bool, bool, str, str]:
    normalized_target_path = target_path.resolve(strict=False)

    try:
        if sys.platform == "win32":
            command = (
                ["explorer", os.fspath(normalized_target_path)]
                if normalized_target_path.is_dir()
                else ["explorer", "/select,", os.fspath(normalized_target_path)]
            )
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, True, "explorer", ""

        if sys.platform == "darwin":
            command = (
                ["open", os.fspath(normalized_target_path)]
                if normalized_target_path.is_dir()
                else ["open", "-R", os.fspath(normalized_target_path)]
            )
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, True, "open", ""

        if not _has_desktop_session():
            return False, False, "", "当前设备没有可用的桌面文件管理器"

        opener = shutil.which("xdg-open")
        if not opener:
            return False, False, "", "当前设备没有可用的桌面文件管理器"

        open_path = normalized_target_path if normalized_target_path.is_dir() else normalized_target_path.parent
        subprocess.Popen([opener, os.fspath(open_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, True, "xdg-open", ""
    except OSError as exc:
        return False, False, "", str(exc)


def reveal_scoped_entry(
    root_key: Optional[str] = None,
    rel_path: str = "",
    *,
    absolute_path: str = "",
) -> dict:
    target_path, resolved = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    directory_path = target_path if target_path.is_dir() else target_path.parent
    launched, supported, method, detail = _launch_path_in_file_manager(target_path)
    return RevealEntryResponse(
        ok=launched,
        supported=supported,
        launched=launched,
        method=method,
        detail=detail,
        root=resolved["root"],
        path=resolved["path"],
        absolute_path=resolved["absolute_path"],
        target_path=os.fspath(target_path.resolve(strict=False)),
        directory_path=os.fspath(directory_path.resolve(strict=False)),
    ).model_dump()


def build_file_response(root_key: Optional[str] = None, rel_path: str = "", *, absolute_path: str = "") -> FileResponse:
    target_path, _ = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    media_type, _ = mimetypes.guess_type(os.fspath(target_path))
    return FileResponse(
        path=os.fspath(target_path),
        media_type=media_type or "application/octet-stream",
        filename=target_path.name,
    )


def read_text_file(
    root_key: Optional[str] = None,
    rel_path: str = "",
    *,
    absolute_path: str = "",
    encoding: str = "utf-8",
) -> dict:
    target_path, resolved = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    try:
        stat_result = target_path.stat()
        text = target_path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to decode file as {encoding}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {exc}") from exc

    return {
        "ok": True,
        "root": resolved["root"],
        "path": resolved["path"],
        "absolute_path": os.fspath(target_path.resolve(strict=False)),
        "encoding": encoding,
        "size": stat_result.st_size,
        "modified_at": int(stat_result.st_mtime * 1000),
        "text": text,
    }


def write_text_file(
    root_key: Optional[str] = None,
    rel_path: str = "",
    *,
    absolute_path: str = "",
    text: str = "",
    encoding: str = "utf-8",
) -> dict:
    target_path, resolved = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if target_path.exists() and not target_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    parent_path = target_path.parent
    if not parent_path.exists():
        raise HTTPException(status_code=404, detail="Parent directory not found")
    if not parent_path.is_dir():
        raise HTTPException(status_code=400, detail="Parent path is not a directory")

    try:
        target_path.write_text(text, encoding=encoding)
        stat_result = target_path.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {exc}") from exc

    absolute_identity_path = os.fspath(target_path.resolve(strict=False))
    return {
        "ok": True,
        "root": resolved["root"],
        "path": resolved["path"] or target_path.name,
        "absolute_path": absolute_identity_path,
        "encoding": encoding,
        "size": stat_result.st_size,
        "modified_at": int(stat_result.st_mtime * 1000),
    }


def rename_labelme_annotation_pair(req: LabelmeRenameRequest) -> dict:
    source_image_path, source_resolved = resolve_request_path(req.root, req.path, absolute_path=req.absolute_path)
    if not source_image_path.exists():
        raise HTTPException(status_code=404, detail="Source image not found")
    if not source_image_path.is_file():
        raise HTTPException(status_code=400, detail="Source path is not a file")
    if source_image_path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Source path is not a supported image file")

    base_root = req.base_root if req.base_root is not None else req.root
    base_path = req.base_path or ""
    base_absolute_path = req.base_absolute_path or ""
    if not base_absolute_path and not base_root:
        base_absolute_path = os.fspath(source_image_path.parent)

    base_dir_path, _ = resolve_request_path(base_root, base_path, absolute_path=base_absolute_path)
    if not base_dir_path.exists():
        raise HTTPException(status_code=404, detail="Base directory not found")
    if not base_dir_path.is_dir():
        raise HTTPException(status_code=400, detail="Base path is not a directory")

    target_relative_path = _normalize_strict_relative_file_path(req.target_relative_path)
    raw_target_path = _ensure_within_root(base_dir_path, base_dir_path / target_relative_path)
    target_image_path = raw_target_path if raw_target_path.suffix else raw_target_path.with_suffix(source_image_path.suffix)
    if target_image_path.suffix.lower() != source_image_path.suffix.lower():
        raise HTTPException(status_code=400, detail="Target image extension must match the source image extension")
    if target_image_path.name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Target path must include a file name")

    target_image_path = _ensure_within_root(base_dir_path, target_image_path)
    target_relative_path = os.fspath(target_image_path.relative_to(base_dir_path)).replace("\\", "/")
    source_json_path = source_image_path.with_suffix(".json")
    target_json_path = target_image_path.with_suffix(".json")
    source_json_exists = source_json_path.exists()
    target_image_same = _paths_equal(source_image_path, target_image_path)
    target_json_same = _paths_equal(source_json_path, target_json_path)
    target_image_exists = target_image_path.exists() and not target_image_same
    target_json_exists = target_json_path.exists() and not target_json_same

    if target_image_path.exists() and target_image_path.is_dir():
        raise HTTPException(status_code=400, detail="Target image path is a directory")
    if target_json_path.exists() and target_json_path.is_dir():
        raise HTTPException(status_code=400, detail="Target JSON path is a directory")
    if (target_image_exists or target_json_exists) and not req.overwrite:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Target image or annotation JSON already exists",
                "target_image_exists": target_image_exists,
                "target_json_exists": target_json_exists,
                "target_relative_path": target_relative_path,
            },
        )

    next_json_text = ""
    if source_json_exists:
        if not source_json_path.is_file():
            raise HTTPException(status_code=400, detail="Source annotation JSON path is not a file")
        try:
            document = json.loads(source_json_path.read_text(encoding=req.encoding))
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Failed to decode annotation JSON as {req.encoding}") from exc
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Failed to parse annotation JSON") from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read annotation JSON: {exc}") from exc

        if isinstance(document, dict):
            document["imagePath"] = target_image_path.name
        else:
            raise HTTPException(status_code=400, detail="Annotation JSON root must be an object")
        next_json_text = f"{json.dumps(document, ensure_ascii=False, indent=2)}\n"

    target_image_path.parent.mkdir(parents=True, exist_ok=True)
    if source_json_exists:
        target_json_path.parent.mkdir(parents=True, exist_ok=True)

    overwritten = bool(target_image_exists or target_json_exists)
    try:
        if not target_image_same:
            os.replace(source_image_path, target_image_path)

        json_moved = False
        json_updated = False
        if source_json_exists:
            if target_json_same:
                source_json_path.write_text(next_json_text, encoding=req.encoding)
            else:
                target_json_path.write_text(next_json_text, encoding=req.encoding)
                try:
                    source_json_path.unlink()
                except FileNotFoundError:
                    pass
                json_moved = True
            json_updated = True
        elif target_json_exists and req.overwrite:
            target_json_path.unlink()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to rename annotation files: {exc}") from exc

    return {
        "ok": True,
        "root": source_resolved["root"],
        "path": target_relative_path if source_resolved["root"] else os.fspath(target_image_path),
        "absolute_path": os.fspath(target_image_path.resolve(strict=False)),
        "source_image_absolute_path": os.fspath(source_image_path.resolve(strict=False)),
        "target_image_absolute_path": os.fspath(target_image_path.resolve(strict=False)),
        "source_json_absolute_path": os.fspath(source_json_path.resolve(strict=False)) if source_json_exists else "",
        "target_json_absolute_path": os.fspath(target_json_path.resolve(strict=False)) if source_json_exists else "",
        "target_relative_path": target_relative_path,
        "target_name": target_image_path.name,
        "json_moved": json_moved,
        "json_updated": json_updated,
        "overwritten": overwritten,
    }


def build_ocr_preview_response(
    root_key: Optional[str] = None,
    rel_path: str = "",
    *,
    absolute_path: str = "",
    shape_type: OcrShapeType = "polygon",
) -> dict:
    target_path, resolved = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    try:
        preview = run_paddle_ocr_preview(target_path, shape_type=shape_type)
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "ok": True,
        "root": resolved["root"],
        "path": resolved["path"],
        "absolute_path": os.fspath(target_path.resolve(strict=False)),
        "engine": preview["engine"],
        "shape_type": preview["shape_type"],
        "shape_count": preview["shape_count"],
        "document": preview["document"],
    }


def build_thumbnail_response(
    root_key: Optional[str] = None,
    rel_path: str = "",
    *,
    absolute_path: str = "",
    max_edge: int = 360,
    quality: int = 82,
) -> Response:
    target_path, _ = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    media_info = _resolve_media_kind(target_path)
    if not media_info:
        raise HTTPException(status_code=400, detail="Path is not a supported media file")

    clamped_edge = max(64, min(max_edge, 2048))
    clamped_quality = max(40, min(quality, 95))
    kind, _ = media_info
    if kind == "video":
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise HTTPException(status_code=501, detail="Video thumbnail generation requires ffmpeg")

        try:
            result = subprocess.run(
                [
                    ffmpeg_bin,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    "0.2",
                    "-i",
                    os.fspath(target_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale=w={clamped_edge}:h={clamped_edge}:force_original_aspect_ratio=decrease",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "mjpeg",
                    "pipe:1",
                ],
                capture_output=True,
                check=True,
                timeout=8,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="Timed out generating video thumbnail") from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode("utf-8", errors="ignore").strip() or "Failed to generate video thumbnail"
            raise HTTPException(status_code=500, detail=detail) from exc

        return Response(
            content=result.stdout,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=300"},
        )

    resampling = getattr(Image, "Resampling", Image).LANCZOS

    try:
        with Image.open(target_path) as source_image:
            preview_image = ImageOps.exif_transpose(source_image)
            preview_image.thumbnail((clamped_edge, clamped_edge), resampling)

            has_alpha = (
                preview_image.mode in {"RGBA", "LA"}
                or (preview_image.mode == "P" and "transparency" in preview_image.info)
            )
            buffer = io.BytesIO()

            if has_alpha:
                if preview_image.mode not in {"RGBA", "LA"}:
                    preview_image = preview_image.convert("RGBA")
                preview_image.save(buffer, format="PNG", optimize=True)
                media_type = "image/png"
            else:
                preview_image = preview_image.convert("RGB")
                preview_image.save(
                    buffer,
                    format="JPEG",
                    quality=clamped_quality,
                    optimize=True,
                    progressive=True,
                )
                media_type = "image/jpeg"
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="Failed to decode image") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(
        content=buffer.getvalue(),
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


def sync_device_file_records(
    req: DeviceFileSyncRequest,
    session: Session,
    *,
    device_id: str,
) -> dict:
    try:
        result = reconcile_device_file_batch(
            session,
            device_id,
            [
                DeviceFileSyncSnapshot(
                    absolute_path=item.absolute_path,
                    last_known_path=item.last_known_path,
                    content_hash=item.content_hash,
                    hash_algorithm=item.hash_algorithm,
                    visual_hash=item.visual_hash,
                    visual_hash_algorithm=item.visual_hash_algorithm,
                    file_size=item.file_size,
                    modified_at_ms=item.modified_at_ms,
                    duration_ms=item.duration_ms,
                    width_px=item.width_px,
                    height_px=item.height_px,
                    media_kind=item.media_kind,
                    mime_type=item.mime_type,
                    weight=item.weight,
                )
                for item in req.items
            ],
            mark_missing_as_dangling=req.mark_missing_as_dangling,
            scope_prefixes=req.scope_prefixes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "device_id": device_id,
        "processed_count": result.processed_count,
        "created_count": result.created_count,
        "rebound_count": result.rebound_count,
        "updated_count": result.updated_count,
        "dangling_count": result.dangling_count,
        "records": [
            {
                "id": record.id,
                "absolute_path": record.absolute_path,
                "last_known_path": record.last_known_path,
                "content_hash": record.content_hash,
                "hash_algorithm": record.hash_algorithm,
                "visual_hash": record.visual_hash,
                "visual_hash_algorithm": record.visual_hash_algorithm,
                "file_size": record.file_size,
                "modified_at_ms": record.modified_at_ms,
                "duration_ms": record.duration_ms,
                "width_px": record.width_px,
                "height_px": record.height_px,
                "media_kind": record.media_kind,
                "mime_type": record.mime_type,
                "match_status": record.match_status,
                "weight": record.weight,
            }
            for record in result.records
        ],
    }


def update_device_file_weight_for_request(
    req: DeviceFileWeightUpdateRequest,
    session: Session,
    *,
    device_id: str,
) -> dict:
    target_path, resolved = resolve_request_path(req.root, req.path, absolute_path=req.absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    absolute_identity_path = os.fspath(target_path.resolve(strict=False))
    try:
        record = update_device_file_weight(
            session,
            device_id,
            absolute_identity_path,
            weight=req.weight,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "device_id": device_id,
        "root": resolved["root"],
        "path": resolved["path"],
        "absolute_path": absolute_identity_path,
        "weight": record.weight,
    }


def scan_device_file_records(
    req: DeviceFileScanRequest,
    session: Session,
    *,
    device_id: str,
) -> dict:
    target_path, resolved = resolve_request_path(req.root, req.path, absolute_path=req.absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    scanned_paths = _iter_scan_files(target_path, recursive=req.recursive)
    root_path = None if resolved["is_absolute"] else resolve_root_path(resolved["root"])
    is_directory = target_path.is_dir()
    scope_prefix = os.fspath(target_path.resolve(strict=False))

    items: list[dict] = []
    identity_paths: list[str] = []
    file_sizes: set[int] = set()
    for file_path in scanned_paths:
        try:
            stat_result = file_path.stat()
        except OSError:
            continue

        identity_path = os.fspath(file_path.resolve(strict=False))
        request_path = (
            os.fspath(file_path)
            if resolved["is_absolute"]
            else os.fspath(file_path.relative_to(root_path)).replace("\\", "/")
        )
        if is_directory:
            relative_path = os.fspath(file_path.relative_to(target_path)).replace("\\", "/")
        else:
            relative_path = file_path.name
        folder_path = os.path.dirname(relative_path).replace("\\", "/")
        media_kind, mime_type = _guess_scan_file_metadata(file_path)

        items.append(
            {
                "name": file_path.name,
                "path": request_path,
                "absolute_path": os.fspath(file_path) if resolved["is_absolute"] else "",
                "relative_path": relative_path,
                "folder_path": "" if folder_path == "." else folder_path,
                "size": stat_result.st_size,
                "modified_at": int(stat_result.st_mtime * 1000),
                "media_kind": media_kind,
                "mime_type": mime_type,
                "_identity_path": identity_path,
            }
        )
        identity_paths.append(identity_path)
        file_sizes.add(stat_result.st_size)

    active_by_path = {}
    if identity_paths:
        active_by_path = {
            record.absolute_path: record
            for record in session.exec(
                select(DeviceFile).where(
                    DeviceFile.device_id == device_id,
                    DeviceFile.absolute_path.in_(identity_paths),
                )
            ).all()
            if record.absolute_path
        }

    dangling_paths = set()
    dangling_sizes = set()
    if identity_paths:
        dangling_paths = {
            record.last_known_path
            for record in session.exec(
                select(DeviceFile).where(
                    DeviceFile.device_id == device_id,
                    DeviceFile.absolute_path.is_(None),
                    DeviceFile.last_known_path.in_(identity_paths),
                )
            ).all()
            if record.last_known_path
        }
    if file_sizes:
        dangling_sizes = {
            record.file_size
            for record in session.exec(
                select(DeviceFile).where(
                    DeviceFile.device_id == device_id,
                    DeviceFile.absolute_path.is_(None),
                    DeviceFile.file_size.in_(list(file_sizes)),
                )
            ).all()
            if record.file_size is not None
        }

    unseen_active_sizes = {
        record.file_size
        for record in session.exec(
            select(DeviceFile).where(
                DeviceFile.device_id == device_id,
                DeviceFile.absolute_path.is_not(None),
            )
        ).all()
        if record.absolute_path
        and record.absolute_path not in identity_paths
        and _path_matches_scope_prefix(record.absolute_path, scope_prefix)
        and record.file_size is not None
    }

    hashed_count = 0
    snapshots: list[DeviceFileSyncSnapshot] = []
    for item in items:
        identity_path = item["_identity_path"]
        active_record = active_by_path.get(identity_path)
        should_hash = _should_hash_scanned_file(
            hash_mode=req.hash_mode,
            active_record=active_record,
            file_size=item["size"],
            modified_at_ms=item["modified_at"],
            has_dangling_same_path=identity_path in dangling_paths,
            has_dangling_same_size=item["size"] in dangling_sizes,
            has_unseen_active_same_size=item["size"] in unseen_active_sizes,
        )

        content_hash = None
        hash_algorithm = "sha256"
        if should_hash:
            try:
                content_hash = _compute_file_sha256(Path(identity_path))
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Failed to hash file: {exc}") from exc
            hashed_count += 1
        elif (
            active_record
            and active_record.file_size == item["size"]
            and active_record.modified_at_ms == item["modified_at"]
            and active_record.content_hash
        ):
            content_hash = active_record.content_hash
            hash_algorithm = active_record.hash_algorithm or "sha256"

        item["content_hash"] = content_hash
        item["hash_algorithm"] = hash_algorithm
        item["_hashed"] = should_hash
        snapshots.append(
            DeviceFileSyncSnapshot(
                absolute_path=identity_path,
                last_known_path=identity_path,
                content_hash=content_hash,
                hash_algorithm=hash_algorithm,
                file_size=item["size"],
                modified_at_ms=item["modified_at"],
                media_kind=item["media_kind"],
                mime_type=item["mime_type"],
            )
        )

    try:
        result = reconcile_device_file_batch(
            session,
            device_id,
            snapshots,
            mark_missing_as_dangling=req.mark_missing_as_dangling,
            scope_prefixes=[scope_prefix],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    records_by_path = {
        record.absolute_path: record
        for record in result.records
        if record.absolute_path
    }
    response_items = []
    for item in items:
        identity_path = item.pop("_identity_path")
        hashed = item.pop("_hashed")
        record = records_by_path.get(identity_path)
        response_items.append(
            {
                **item,
                "hashed": hashed,
                "match_status": record.match_status if record else "matched",
                "weight": record.weight if record else 0,
            }
        )

    return {
        "ok": True,
        "device_id": device_id,
        "root": resolved["root"],
        "path": resolved["path"],
        "absolute_path": resolved["absolute_path"],
        "is_directory": is_directory,
        "recursive": req.recursive,
        "hash_mode": req.hash_mode,
        "processed_count": result.processed_count,
        "hashed_count": hashed_count,
        "created_count": result.created_count,
        "rebound_count": result.rebound_count,
        "updated_count": result.updated_count,
        "dangling_count": result.dangling_count,
        "items": response_items,
    }


@router.get("/roots")
def get_roots():
    return {"roots": list_available_roots()}


@router.post("/list_dir")
def list_directory(req: LegacyPathRequest):
    """Legacy path-based directory listing."""
    path = req.path
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Path not found")

    items = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                items.append(
                    {
                        "name": entry.name,
                        "is_dir": entry.is_dir(),
                        "path": entry.path,
                    }
                )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied") from exc

    return {"items": items, "current_path": path}


@router.post("/scoped/list_dir")
def list_scoped_directory(req: DirectoryListRequest, session: Session = Depends(get_session)):
    return list_directory_items(
        req.root,
        req.path,
        absolute_path=req.absolute_path,
        sort_program=req.sort_program,
        session=session,
    )


@router.post("/images/list")
def list_images(req: RootScopedRequest, session: Session = Depends(get_session)):
    return list_image_entries(req.root, req.path, absolute_path=req.absolute_path, session=session)


@router.post("/media/list")
def list_media(req: MediaListRequest, session: Session = Depends(get_session)):
    return list_media_entries(
        req.root,
        req.path,
        absolute_path=req.absolute_path,
        recursive=req.recursive,
        scan_limit=req.scan_limit,
        session=session,
        sort_mode=req.sort_mode,
        sort_program=req.sort_program,
        snapshot_id=req.snapshot_id,
        offset=req.offset,
        limit=req.limit,
        layout_mode=req.layout_mode,
        layout_columns=req.layout_columns,
        layout_column_width=req.layout_column_width,
        layout_gap=req.layout_gap,
        layout_column_heights=req.layout_column_heights,
    )


@router.post("/duplicates")
def list_duplicates(req: DuplicateListRequest):
    return list_duplicate_file_groups(
        req.root,
        req.path,
        absolute_path=req.absolute_path,
        recursive=req.recursive,
        rules=req.rules,
        filter_rules=req.filter_rules,
        sort_mode=req.sort_mode,
        source=req.source,
        min_size=req.min_size,
        scan_limit=req.scan_limit,
        snapshot_id=req.snapshot_id,
        page=req.page,
        page_size=req.page_size,
    )


@router.post("/duplicates/tasks")
def start_duplicates_task(req: DuplicateListRequest):
    return start_duplicate_file_analysis(
        req.root,
        req.path,
        absolute_path=req.absolute_path,
        recursive=req.recursive,
        rules=req.rules,
        filter_rules=req.filter_rules,
        sort_mode=req.sort_mode,
        source=req.source,
        min_size=req.min_size,
        scan_limit=req.scan_limit,
        page=req.page,
        page_size=req.page_size,
    )


@router.get("/duplicates/tasks/{task_id}")
def get_duplicates_task(task_id: str, page: int = Query(1, ge=1), page_size: int = Query(DUPLICATE_LISTING_PAGE_SIZE, ge=1, le=50)):
    return get_duplicate_analysis_task_snapshot(task_id, page=page, page_size=page_size)


@router.post("/delete")
def delete_entry(req: DeleteEntryRequest):
    return delete_scoped_entry(
        req.root,
        req.path,
        absolute_path=req.absolute_path,
        recursive=req.recursive,
    )


@router.post("/delete/async")
def start_delete_entry(req: DeleteEntryRequest):
    return enqueue_delete_scoped_entry(
        req.root,
        req.path,
        absolute_path=req.absolute_path,
        recursive=req.recursive,
    )


@router.get("/delete-tasks")
def list_delete_tasks():
    return list_delete_task_snapshots()


@router.get("/delete-tasks/{task_id}")
def get_delete_task(task_id: str):
    return get_delete_task_snapshot(task_id)


@router.post("/reveal")
def reveal_entry(req: RootScopedRequest):
    return reveal_scoped_entry(
        req.root,
        req.path,
        absolute_path=req.absolute_path,
    )


@router.post("/device-files/sync")
def sync_device_files(req: DeviceFileSyncRequest, session: Session = Depends(get_session)):
    return sync_device_file_records(req, session, device_id=get_device_id())


@router.post("/device-files/scan")
def scan_device_files(req: DeviceFileScanRequest, session: Session = Depends(get_session)):
    return scan_device_file_records(req, session, device_id=get_device_id())


@router.post("/weight")
def update_file_weight(req: DeviceFileWeightUpdateRequest, session: Session = Depends(get_session)):
    return update_device_file_weight_for_request(req, session, device_id=get_device_id())


@router.get("/content")
def get_content(
    root: Optional[str] = Query(None),
    path: str = Query(""),
    absolute_path: str = Query(""),
):
    return build_file_response(root, path, absolute_path=absolute_path)


@router.get("/text")
def get_text(
    root: Optional[str] = Query(None),
    path: str = Query(""),
    absolute_path: str = Query(""),
    encoding: str = Query("utf-8"),
):
    return read_text_file(root, path, absolute_path=absolute_path, encoding=encoding)


@router.post("/text")
def save_text(req: TextFileWriteRequest):
    return write_text_file(
        req.root,
        req.path,
        absolute_path=req.absolute_path,
        text=req.text,
        encoding=req.encoding,
    )


@router.post("/labelme/rename")
def rename_labelme_annotation(req: LabelmeRenameRequest):
    return rename_labelme_annotation_pair(req)


@router.post("/ocr")
def preview_ocr(req: OcrPreviewRequest):
    return build_ocr_preview_response(
        req.root,
        req.path,
        absolute_path=req.absolute_path,
        shape_type=req.shape_type,
    )


@router.get("/thumbnail")
def get_thumbnail(
    root: Optional[str] = Query(None),
    path: str = Query(""),
    absolute_path: str = Query(""),
    max_edge: int = Query(360, ge=64, le=2048),
    quality: int = Query(82, ge=40, le=95),
):
    return build_thumbnail_response(
        root,
        path,
        absolute_path=absolute_path,
        max_edge=max_edge,
        quality=quality,
    )


@router.get("/info")
def get_system_info():
    return {
        "platform": sys.platform,
        "python_version": sys.version,
        "cwd": os.getcwd(),
    }
