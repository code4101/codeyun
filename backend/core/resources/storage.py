from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.core.settings import BACKEND_DIR, LEGACY_SOURCE_DATA_DIR, get_settings


ATTACHMENTS_DIR_NAME = "attachments"
ATTACHMENTS_URL_PREFIX = "/static/attachments"
LEGACY_UPLOADS_DIR = BACKEND_DIR / "static" / "uploads"
LEGACY_UPLOADS_DATA_DIR_NAME = "uploads"
LEGACY_UPLOADS_URL_PREFIX = "/static/uploads"
ATTACHMENT_URL_PATTERN = re.compile(
    r"/static/(?:attachments|uploads)/([a-zA-Z0-9_-]+\.[a-zA-Z0-9]+)"
)


@dataclass(slots=True)
class LegacyDataDirMigrationResult:
    source: str
    target: str
    backup_dir: str = ""
    moved: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed_source: bool = False


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.resolve(strict=False)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def migrate_legacy_source_data_dir(
    *,
    legacy_data_dir: str | Path | None = None,
    target_data_dir: str | Path | None = None,
    backup_root: str | Path | None = None,
) -> LegacyDataDirMigrationResult:
    """Move old source-tree data out of backend/data without overwriting active data."""

    settings = get_settings()
    legacy_dir = Path(legacy_data_dir) if legacy_data_dir is not None else LEGACY_SOURCE_DATA_DIR
    target_dir = Path(target_data_dir) if target_data_dir is not None else settings.data_dir
    legacy_dir = legacy_dir.resolve(strict=False)
    target_dir = target_dir.resolve(strict=False)

    result = LegacyDataDirMigrationResult(
        source=os.fspath(legacy_dir),
        target=os.fspath(target_dir),
    )

    if _same_path(legacy_dir, target_dir):
        result.skipped.append("legacy_source_is_active_data_dir")
        return result
    if _is_relative_to(target_dir, legacy_dir):
        result.skipped.append("target_is_inside_legacy_source")
        return result
    if not legacy_dir.exists():
        result.skipped.append("legacy_source_missing")
        return result

    target_dir.mkdir(parents=True, exist_ok=True)
    backup_dir: Path | None = None

    def ensure_backup_dir() -> Path:
        nonlocal backup_dir
        if backup_dir is None:
            root = (
                Path(backup_root).resolve(strict=False)
                if backup_root is not None
                else settings.data_workspace_dir / "backups"
            )
            backup_dir = root / f"legacy-source-data-{time.strftime('%Y%m%d-%H%M%S')}"
            backup_dir.mkdir(parents=True, exist_ok=True)
            result.backup_dir = os.fspath(backup_dir)
        return backup_dir

    for entry in list(legacy_dir.iterdir()):
        target = target_dir / entry.name
        if target.exists():
            archive_target = _unique_path(ensure_backup_dir() / entry.name)
            shutil.move(os.fspath(entry), os.fspath(archive_target))
            result.archived.append(os.fspath(archive_target))
            continue

        shutil.move(os.fspath(entry), os.fspath(target))
        result.moved.append(os.fspath(target))

    try:
        legacy_dir.rmdir()
        result.removed_source = True
    except OSError:
        pass

    return result


def get_attachments_dir() -> Path:
    attachments_dir = get_settings().attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)
    return attachments_dir


def build_attachment_url(filename: str) -> str:
    return f"{ATTACHMENTS_URL_PREFIX}/{filename}"


def iter_attachment_urls(content: str) -> list[str]:
    if not content:
        return []
    return ATTACHMENT_URL_PATTERN.findall(content)


def migrate_legacy_attachments() -> int:
    attachments_dir = get_attachments_dir()
    legacy_dirs = [
        LEGACY_UPLOADS_DIR,
        get_settings().data_dir / LEGACY_UPLOADS_DATA_DIR_NAME,
    ]

    moved_count = 0
    for legacy_dir in legacy_dirs:
        if not legacy_dir.exists():
            continue
        for entry in legacy_dir.iterdir():
            target = attachments_dir / entry.name
            if target.exists():
                continue
            shutil.move(os.fspath(entry), os.fspath(target))
            moved_count += 1

        try:
            legacy_dir.rmdir()
        except OSError:
            pass

    return moved_count
