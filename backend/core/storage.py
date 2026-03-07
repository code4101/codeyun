from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from backend.core.settings import BACKEND_DIR, get_settings


ATTACHMENTS_DIR_NAME = "attachments"
ATTACHMENTS_URL_PREFIX = "/static/attachments"
LEGACY_UPLOADS_DIR = BACKEND_DIR / "static" / "uploads"
LEGACY_UPLOADS_DATA_DIR_NAME = "uploads"
LEGACY_UPLOADS_URL_PREFIX = "/static/uploads"
ATTACHMENT_URL_PATTERN = re.compile(
    r"/static/(?:attachments|uploads)/([a-zA-Z0-9_-]+\.[a-zA-Z0-9]+)"
)


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
