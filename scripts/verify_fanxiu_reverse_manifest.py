from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu.catalog.resources import (  # noqa: E402
    DEFAULT_FANXIU_REVERSE_ROOT,
    resolve_fanxiu_export_root,
    resolve_fanxiu_resource_root,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


RESOURCE_KEY_FILES = (
    "filelist.csv",
    "filelistVersion",
)

RESOURCE_KEY_DIRS = (
    "lscripts",
    "atlasnew",
    "ui",
    "uieffect",
    "Audio",
)

EXPORT_KEY_FILES = (
    "parsed_configs/Item/rows.json",
    "parsed_configs/Envelope/rows.json",
    "parsed_configs/item_catalog/item_catalog.json",
    "parsed_configs/activity_catalog/activity_catalog.json",
    "parsed_configs/gongfa_catalog/gongfa_catalog.json",
    "parsed_configs/digitdoor_catalog/digitdoor_catalog.json",
    "parsed_configs/doupotd_catalog/doupotd_catalog.json",
    "parsed_configs/visual_catalog/static_visual_catalog.json",
    "parsed_configs/visual_catalog/atlas_sprite_catalog.tsv",
    "parsed_configs/asset_catalog/static_asset_catalog.json",
    "parsed_configs/audio_catalog/wwise_audio_catalog.json",
)

EXPORT_KEY_DIRS = (
    "icons",
    "by_source/lscripts",
    "apk_static_index",
)


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _file_entry(path: Path, *, root: Path, kind: str) -> dict[str, Any]:
    stat = path.stat()
    return {
        "kind": kind,
        "relative_path": path.relative_to(root).as_posix(),
        "path": str(path),
        "exists": True,
        "is_file": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256_file(path),
    }


def _missing_entry(path: Path, *, root: Path, kind: str) -> dict[str, Any]:
    try:
        relative_path = path.relative_to(root).as_posix()
    except ValueError:
        relative_path = path.as_posix()
    return {
        "kind": kind,
        "relative_path": relative_path,
        "path": str(path),
        "exists": False,
        "is_file": False,
        "size": 0,
        "mtime_ns": 0,
        "sha256": "",
    }


def _dir_summary(path: Path, *, root: Path, kind: str) -> dict[str, Any]:
    if not path.exists() or not path.is_dir():
        return _missing_entry(path, root=root, kind=kind)
    file_count = 0
    total_bytes = 0
    max_mtime_ns = 0
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        rel = item.relative_to(path).as_posix()
        file_count += 1
        total_bytes += stat.st_size
        max_mtime_ns = max(max_mtime_ns, stat.st_mtime_ns)
        digest.update(rel.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\n")
    return {
        "kind": kind,
        "relative_path": path.relative_to(root).as_posix(),
        "path": str(path),
        "exists": True,
        "is_file": False,
        "file_count": file_count,
        "size": total_bytes,
        "mtime_ns": max_mtime_ns,
        "sha256": f"dir-meta:{digest.hexdigest()}",
    }


def _raw_input_entries(raw_inputs_root: Path, *, reverse_root: Path) -> list[dict[str, Any]]:
    if not raw_inputs_root.exists() or not raw_inputs_root.is_dir():
        return [_missing_entry(raw_inputs_root, root=reverse_root, kind="raw_inputs_dir")]
    entries: list[dict[str, Any]] = []
    for item in sorted(raw_inputs_root.iterdir()):
        if item.is_file():
            entries.append(_file_entry(item, root=reverse_root, kind="raw_input"))
        elif item.is_dir():
            entries.append(_dir_summary(item, root=reverse_root, kind="raw_input_dir"))
    return entries


def _manifest_digest(entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda row: (str(row.get("kind")), str(row.get("relative_path")))):
        digest.update(str(entry.get("kind", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry.get("relative_path", "")).encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(entry.get("size", 0)).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry.get("sha256", "")).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _write_outputs(summary: dict[str, Any], entries: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_json = output_dir / "reverse_manifest_latest.json"
    latest_tsv = output_dir / "reverse_manifest_latest.tsv"
    latest_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with latest_tsv.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["kind", "relative_path", "exists", "is_file", "size", "file_count", "mtime_ns", "sha256", "path"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            writer.writerow(entry)


def build_fanxiu_reverse_manifest(
    *,
    reverse_root: str | os.PathLike[str] | None = None,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    write_outputs: bool = True,
) -> dict[str, Any]:
    resolved_reverse_root = Path(reverse_root).expanduser().resolve() if reverse_root else DEFAULT_FANXIU_REVERSE_ROOT.resolve()
    resolved_resource_root = resolve_fanxiu_resource_root(resource_root)
    resolved_export_root = resolve_fanxiu_export_root(export_root)
    raw_inputs_root = resolved_reverse_root / "raw_inputs"
    entries: list[dict[str, Any]] = []

    entries.extend(_raw_input_entries(raw_inputs_root, reverse_root=resolved_reverse_root))
    for relative in RESOURCE_KEY_FILES:
        path = resolved_resource_root / relative
        entries.append(_file_entry(path, root=resolved_reverse_root, kind="resource_file") if path.is_file() else _missing_entry(path, root=resolved_reverse_root, kind="resource_file"))
    for relative in RESOURCE_KEY_DIRS:
        entries.append(_dir_summary(resolved_resource_root / relative, root=resolved_reverse_root, kind="resource_dir"))
    for relative in EXPORT_KEY_FILES:
        path = resolved_export_root / relative
        entries.append(_file_entry(path, root=resolved_reverse_root, kind="export_file") if path.is_file() else _missing_entry(path, root=resolved_reverse_root, kind="export_file"))
    for relative in EXPORT_KEY_DIRS:
        entries.append(_dir_summary(resolved_export_root / relative, root=resolved_reverse_root, kind="export_dir"))

    missing_entries = [entry for entry in entries if not entry.get("exists")]
    raw_input_files = [entry for entry in entries if entry.get("kind") == "raw_input" and entry.get("exists")]
    hashed_file_entries = [entry for entry in entries if entry.get("is_file") and entry.get("sha256")]
    directory_entries = [entry for entry in entries if not entry.get("is_file") and entry.get("exists")]
    output_dir = resolved_export_root / "parsed_configs" / "reverse_manifest_audit"
    summary = {
        "ok": not missing_entries and bool(raw_input_files) and bool(hashed_file_entries),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reverse_root": str(resolved_reverse_root),
        "resource_root": str(resolved_resource_root),
        "export_root": str(resolved_export_root),
        "raw_inputs_root": str(raw_inputs_root),
        "entry_count": len(entries),
        "raw_input_count": len(raw_input_files),
        "hashed_file_count": len(hashed_file_entries),
        "directory_summary_count": len(directory_entries),
        "missing_count": len(missing_entries),
        "missing_entries": [
            {"kind": entry.get("kind"), "relative_path": entry.get("relative_path"), "path": entry.get("path")}
            for entry in missing_entries
        ],
        "manifest_digest": _manifest_digest(entries),
        "output_dir": str(output_dir),
        "entries": entries,
    }
    if write_outputs:
        _write_outputs(summary, entries, output_dir)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and verify a traceable Fanxiu reverse-resource manifest.")
    parser.add_argument("--reverse-root", default=None)
    parser.add_argument("--resource-root", default=None)
    parser.add_argument("--export-root", default=None)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    summary = build_fanxiu_reverse_manifest(
        reverse_root=args.reverse_root,
        resource_root=args.resource_root,
        export_root=args.export_root,
        write_outputs=not args.no_write,
    )
    public_summary = {key: value for key, value in summary.items() if key != "entries"}
    print(json.dumps(public_summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
