from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from backend.core.settings import get_settings


DOCUMENT_ASSETS_DIR_NAME = "reduction-documents"
DOCUMENT_CACHE_DIR_NAME = "reduction-cache"
DOCUMENT_SOURCE_FILENAME = "source.txt"
DOCUMENT_MANIFEST_FILENAME = "manifest.json"
DOCUMENT_RUN_RESULT_FILENAME = "result.json"
DOCUMENT_RUN_TREE_FILENAME = "tree.jsonl"
DOCUMENT_RUN_SOURCE_UNITS_FILENAME = "source_units.jsonl"


class DocumentReductionStorageError(RuntimeError):
    """Raised when reduction document assets cannot be stored or loaded safely."""


def get_document_assets_dir() -> Path:
    path = get_settings().data_dir / DOCUMENT_ASSETS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_document_cache_dir() -> Path:
    path = get_settings().data_dir / DOCUMENT_CACHE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_document_asset_dir(*, user_id: int, document_id: str) -> Path:
    path = get_document_assets_dir() / f"user-{user_id}" / document_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def migrate_document_asset_dir_id(*, user_id: int, old_document_id: str, new_document_id: str) -> bool:
    old_id = str(old_document_id or "").strip()
    new_id = str(new_document_id or "").strip()
    if not old_id or not new_id or old_id == new_id:
        return False

    user_dir = get_document_assets_dir() / f"user-{user_id}"
    old_path = user_dir / old_id
    new_path = user_dir / new_id
    if not old_path.exists():
        return False

    user_dir.mkdir(parents=True, exist_ok=True)
    if not new_path.exists():
        old_path.rename(new_path)
    else:
        for child in old_path.iterdir():
            target = new_path / child.name
            if target.exists():
                continue
            shutil.move(str(child), str(target))
        try:
            old_path.rmdir()
        except OSError:
            pass

    manifest_path = new_path / DOCUMENT_MANIFEST_FILENAME
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = None
        if isinstance(manifest, dict) and manifest.get("document_id") != new_id:
            manifest["document_id"] = new_id
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def get_document_runs_dir(*, user_id: int, document_id: str) -> Path:
    path = get_document_asset_dir(user_id=user_id, document_id=document_id) / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_document_run_dir(*, user_id: int, document_id: str, run_id: str) -> Path:
    path = get_document_runs_dir(user_id=user_id, document_id=document_id) / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def delete_document_asset_dir(*, user_id: int, document_id: str) -> None:
    path = get_document_assets_dir() / f"user-{user_id}" / document_id
    if path.exists():
        shutil.rmtree(path)


def sha256_hexdigest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_document_source_text(
    *,
    user_id: int,
    document_id: str,
    original_filename: str,
    media_type: str,
    raw_bytes: bytes,
    text_content: str,
) -> dict[str, Any]:
    asset_dir = get_document_asset_dir(user_id=user_id, document_id=document_id)
    source_path = asset_dir / DOCUMENT_SOURCE_FILENAME
    source_path.write_text(text_content, encoding="utf-8")

    manifest = {
        "document_id": document_id,
        "user_id": user_id,
        "original_filename": original_filename,
        "media_type": media_type,
        "size_bytes": len(raw_bytes),
        "sha256": sha256_hexdigest(raw_bytes),
        "source_char_count": len(text_content),
    }
    (asset_dir / DOCUMENT_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def load_document_source_text(*, user_id: int, document_id: str) -> str:
    path = get_document_asset_dir(user_id=user_id, document_id=document_id) / DOCUMENT_SOURCE_FILENAME
    if not path.exists():
        raise DocumentReductionStorageError("文档原文不存在")
    return path.read_text(encoding="utf-8")


def save_document_run_artifacts(
    *,
    user_id: int,
    document_id: str,
    run_id: str,
    source_units: list[dict[str, Any]],
    levels: list[dict[str, Any]],
    final_result: dict[str, Any],
) -> None:
    run_dir = get_document_run_dir(user_id=user_id, document_id=document_id, run_id=run_id)

    source_units_path = run_dir / DOCUMENT_RUN_SOURCE_UNITS_FILENAME
    with source_units_path.open("w", encoding="utf-8") as handle:
        for item in source_units:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    tree_path = run_dir / DOCUMENT_RUN_TREE_FILENAME
    with tree_path.open("w", encoding="utf-8") as handle:
        for level in levels:
            for node in level.get("nodes") or []:
                payload = {
                    "level": level.get("level"),
                    "input_kind": level.get("input_kind"),
                    **node,
                }
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    (run_dir / DOCUMENT_RUN_RESULT_FILENAME).write_text(
        json.dumps(final_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_document_run_result(*, user_id: int, document_id: str, run_id: str) -> dict[str, Any]:
    path = get_document_run_dir(user_id=user_id, document_id=document_id, run_id=run_id) / DOCUMENT_RUN_RESULT_FILENAME
    if not path.exists():
        raise DocumentReductionStorageError("归纳结果不存在")
    return json.loads(path.read_text(encoding="utf-8"))


def load_document_run_source_units(
    *,
    user_id: int,
    document_id: str,
    run_id: str,
) -> dict[str, dict[str, Any]]:
    path = get_document_run_dir(user_id=user_id, document_id=document_id, run_id=run_id) / DOCUMENT_RUN_SOURCE_UNITS_FILENAME
    if not path.exists():
        raise DocumentReductionStorageError("归纳 source units 不存在")

    items: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            unit_id = str(payload.get("unit_id") or "").strip()
            if unit_id:
                items[unit_id] = payload
    return items


def load_document_run_tree_nodes(
    *,
    user_id: int,
    document_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    path = get_document_run_dir(user_id=user_id, document_id=document_id, run_id=run_id) / DOCUMENT_RUN_TREE_FILENAME
    if not path.exists():
        raise DocumentReductionStorageError("归纳节点树不存在")

    nodes: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                nodes.append(payload)
    return nodes
