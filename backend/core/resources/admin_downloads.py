"""Durable download bundles published by local maintenance, readable by superusers.

Only copied files under the private data directory are served. HTTP callers
select bundle/file names, never filesystem paths. Publishing is atomic and
existing bundles are immutable so a download link keeps identifying its data.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
from datetime import datetime, timezone
from uuid import uuid4

from backend.core.settings import get_settings


def download_root() -> Path:
    return get_settings().data_dir / "admin-downloads"


def bundle_path(bundle_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", bundle_id):
        raise ValueError("Invalid download bundle ID")
    return download_root() / bundle_id


def publish_download_bundle(bundle_id: str, *, title: str, files: list[Path],
                            category: str = "files", description: str = "",
                            version: str = "", platform: str = "") -> dict:
    """Local-owner API: copy, hash and atomically publish an immutable bundle.

    Explicitly provide the files to expose; directory scans and HTTP publication
    are deliberately excluded. Failed staging remains private for inspection.
    """
    target = bundle_path(bundle_id)
    sources = [Path(p).resolve(strict=True) for p in files]
    if not title.strip() or not sources or any(not p.is_file() for p in sources):
        raise ValueError("A title and regular files are required")
    if len({p.name.casefold() for p in sources}) != len(sources):
        raise ValueError("Duplicate download filenames")
    if any(p.name.casefold() == "manifest.json" for p in sources):
        raise ValueError("manifest.json is reserved for bundle metadata")
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / (".staging-" + uuid4().hex)
    staging.mkdir()
    entries = []
    for source in sources:
        copied = staging / source.name
        shutil.copyfile(source, copied)
        digest = hashlib.sha256()
        with copied.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        entries.append({"name": source.name, "bytes": copied.stat().st_size,
                        "sha256": digest.hexdigest()})
    manifest = {"id": bundle_id, "title": title.strip(), "files": entries,
                "category": category, "description": description, "version": version,
                "platform": platform, "created_at": datetime.now(timezone.utc).isoformat()}
    (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    staging.rename(target)
    return manifest


def list_download_bundles(*, category: str) -> list[dict]:
    """List published manifests in one catalogue, excluding staging/other bundles."""
    result = []
    for directory in download_root().glob("*"):
        if directory.name.startswith(".") or not directory.is_dir() or directory.is_symlink():
            continue
        try:
            item = read_download_bundle(directory.name)
        except (OSError, ValueError):
            continue
        if item.get("category") == category:
            result.append(item)
    return sorted(result, key=lambda item: (item.get("created_at", ""), item["id"]), reverse=True)


def read_download_bundle(bundle_id: str) -> dict:
    return json.loads((bundle_path(bundle_id) / "manifest.json").read_text(encoding="utf-8"))


def resolve_download_file(bundle_id: str, filename: str) -> Path:
    manifest = read_download_bundle(bundle_id)
    if filename not in {item["name"] for item in manifest["files"]}:
        raise FileNotFoundError(filename)
    if Path(filename).name != filename or "\\" in filename or "/" in filename:
        raise ValueError("Invalid download filename")
    root = bundle_path(bundle_id).resolve(strict=True)
    path = (root / filename).resolve(strict=True)
    if path.parent != root or not path.is_file():
        raise ValueError("Download file escapes bundle")
    return path
