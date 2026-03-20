from __future__ import annotations

import hashlib
import io
import json
import math
import mimetypes
import os
import shutil
import subprocess
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import cmp_to_key
from pathlib import Path
from threading import RLock
from typing import Iterable, Iterator, List, Literal, Optional
from uuid import uuid4

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
from backend.core.settings import ROOT_DIR, get_settings
from backend.db import get_session

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

DEVICE_ROOT_SENTINEL = "__device_root__"
MEDIA_LISTING_SNAPSHOT_LIMIT = 32
MEDIA_LISTING_SNAPSHOT_TTL_SECONDS = 30 * 60
DEFAULT_MEDIA_SCAN_LIMIT = 2000
MAX_MEDIA_SCAN_LIMIT = 50000


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
    created_at: float
    last_accessed_at: float


_media_listing_snapshots: OrderedDict[str, MediaListingSnapshot] = OrderedDict()
_media_listing_snapshot_lock = RLock()


class LegacyPathRequest(BaseModel):
    path: str


class RootScopedRequest(BaseModel):
    root: Optional[str] = None
    path: str = ""
    absolute_path: str = ""


MediaSortMode = Literal["path", "modified-desc", "size-desc", "weight-desc"]
GallerySortField = Literal[
    "random",
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


def _normalize_rel_path(raw_path: str) -> str:
    normalized = (raw_path or "").strip().replace("\\", "/")
    normalized = normalized.lstrip("/")
    if normalized in {"", "."}:
        return ""
    return "/".join(part for part in normalized.split("/") if part not in {"", "."})


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


def _list_system_root_entries() -> list[dict]:
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
            {
                "name": candidate.rstrip("\\/"),
                "path": candidate,
                "is_dir": True,
                "size": None,
                "modified_at": None,
            }
            for candidate in candidates
        ]

    return [
        {
            "name": "/",
            "path": "/",
            "is_dir": True,
            "size": None,
            "modified_at": None,
        }
    ]


def list_directory_items(root_key: Optional[str] = None, rel_path: str = "", absolute_path: str = "") -> dict:
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

    items.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    return {
        "root": resolved["root"],
        "current_path": resolved["path"],
        "absolute_path": resolved["absolute_path"],
        "items": items,
    }


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


def _attach_cached_media_metadata(entries: list[dict], session: Session | None = None) -> None:
    if not entries:
        return

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
        return

    try:
        device_id = get_device_id()
    except Exception:
        device_id = ""

    absolute_paths = [
        str(entry["_absolute_identity_path"])
        for entry in entries
        if entry.get("_absolute_identity_path")
    ]
    cached_records: dict[str, DeviceFile] = {}
    if session is not None and device_id and absolute_paths:
        cached_records = {
            record.absolute_path: record
            for record in session.exec(
                select(DeviceFile).where(
                    DeviceFile.device_id == device_id,
                    DeviceFile.absolute_path.in_(absolute_paths),
                )
            ).all()
            if record.absolute_path
        }

    ffprobe_bin = shutil.which("ffprobe")
    snapshots: list[DeviceFileMetadataSnapshot] = []
    for entry in entries:
        absolute_identity_path = str(entry.pop("_absolute_identity_path", "") or "")
        file_path = entry.pop("_file_path", None)
        cached_record = cached_records.get(absolute_identity_path) if absolute_identity_path else None

        duration_ms = None
        width_px = None
        height_px = None
        matches_cached_file = (
            cached_record is not None
            and cached_record.file_size == entry.get("size")
            and cached_record.modified_at_ms == entry.get("modified_at")
        )

        if entry.get("kind") == "image":
            if matches_cached_file and cached_record:
                width_px = cached_record.width_px
                height_px = cached_record.height_px
            if (width_px is None or height_px is None) and isinstance(file_path, Path):
                probed_width, probed_height = _probe_image_dimensions(file_path)
                width_px = probed_width if probed_width is not None else width_px
                height_px = probed_height if probed_height is not None else height_px

        if entry.get("kind") == "video":
            if matches_cached_file and cached_record:
                duration_ms = cached_record.duration_ms
                width_px = cached_record.width_px
                height_px = cached_record.height_px

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
        if device_id and absolute_identity_path:
            snapshots.append(
                DeviceFileMetadataSnapshot(
                    absolute_path=absolute_identity_path,
                    last_known_path=absolute_identity_path,
                    file_size=entry.get("size"),
                    modified_at_ms=entry.get("modified_at"),
                    duration_ms=duration_ms,
                    width_px=width_px,
                    height_px=height_px,
                    media_kind=entry.get("kind"),
                    mime_type=entry.get("mime_type"),
                )
            )

    if session is not None and device_id and snapshots:
        try:
            upsert_device_file_metadata_batch(session, device_id, snapshots)
        except Exception:
            pass


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


def _get_media_sort_value(item: dict, field: GallerySortField) -> str | int | None:
    if field == "random":
        random_order = item.get("_random_order")
        if isinstance(random_order, int):
            return random_order
        return _compute_media_random_order("stable-random", item)
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


def _compare_media_sort_rule(left: dict, right: dict, rule: GallerySortRule) -> int:
    left_value = _get_media_sort_value(left, rule.field)
    right_value = _get_media_sort_value(right, rule.field)
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


def _sort_supported_media_entries(
    entries: list[dict],
    sort_mode: MediaSortMode,
    sort_program: GallerySortProgram | None,
) -> list[GallerySortRule]:
    normalized_rules = _normalize_media_sort_program(sort_mode, sort_program)
    _populate_media_random_sort_values(entries, normalized_rules)
    entries.sort(key=cmp_to_key(lambda left, right: _compare_media_entries(left, right, normalized_rules)))
    for entry in entries:
        entry.pop("_random_order", None)
    return normalized_rules


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
    normalized_offset = max(0, int(offset or 0))
    normalized_limit = max(0, int(limit or 0))
    has_more = next_offset is not None
    layout = _build_media_layout(
        sliced_entries,
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
        "offset": normalized_offset,
        "limit": normalized_limit,
        "has_more": has_more,
        "next_offset": next_offset,
        "layout": layout,
        response_key: sliced_entries,
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

    _attach_cached_media_metadata(entries, session)
    normalized_rules = _sort_supported_media_entries(entries, sort_mode, sort_program)
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
        allowed_kinds={"image", "video"},
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
    target_path, resolved = resolve_request_path(root_key, rel_path, absolute_path=absolute_path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not resolved["is_absolute"] and target_path == resolve_root_path(resolved["root"]):
        raise HTTPException(status_code=400, detail="Root path cannot be deleted")

    try:
        if target_path.is_dir():
            if not recursive:
                raise HTTPException(status_code=400, detail="Directory deletion requires recursive=true")
            shutil.rmtree(target_path)
        else:
            target_path.unlink()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "root": resolved["root"],
        "path": resolved["path"],
        "absolute_path": resolved["absolute_path"],
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
    from backend.models import DeviceFile

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
def list_scoped_directory(req: RootScopedRequest):
    return list_directory_items(req.root, req.path, absolute_path=req.absolute_path)


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


@router.post("/delete")
def delete_entry(req: DeleteEntryRequest):
    return delete_scoped_entry(
        req.root,
        req.path,
        absolute_path=req.absolute_path,
        recursive=req.recursive,
    )


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
