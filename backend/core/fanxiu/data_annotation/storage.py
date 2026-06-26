from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backend.core.settings import get_settings


DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID = "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"
DATA_ANNOTATION_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

AssetStorageKind = Literal["entry_images", "missing"]


@dataclass(frozen=True)
class FanxiuDataAnnotationImageAsset:
    filename: str
    entry_id: str
    path: Path
    sidecar_path: Path
    exists: bool
    storage_kind: AssetStorageKind


def sanitize_data_annotation_entry_id(entry_id: str | None = None) -> str:
    raw = str(entry_id or DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID)
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", raw).strip("._") or "default"


def normalize_data_annotation_image_filename(filename: str) -> str:
    name = Path(str(filename or "")).name
    if not name or name != str(filename) or "\x00" in name:
        raise ValueError("data-annotation 图片文件名不合法")
    if Path(name).suffix.lower() not in DATA_ANNOTATION_IMAGE_SUFFIXES:
        raise ValueError("data-annotation 图片只支持 jpg/jpeg/png")
    return name


def fanxiu_data_annotation_dir() -> Path:
    return get_settings().data_dir / "fanxiu" / "data-annotation"


def data_annotation_asset_tree_path(entry_id: str | None = None) -> Path:
    return data_annotation_entry_dir(entry_id) / "asset-tree.json"


def data_annotation_entry_dir(entry_id: str | None = None) -> Path:
    return fanxiu_data_annotation_dir() / "entries" / sanitize_data_annotation_entry_id(entry_id)


def data_annotation_entry_image_dir(entry_id: str | None = None) -> Path:
    return data_annotation_entry_dir(entry_id) / "images"


def _resolve_child_path(directory: Path, filename: str) -> Path:
    root = directory.resolve(strict=False)
    path = (root / filename).resolve(strict=False)
    if path.parent != root:
        raise ValueError("data-annotation 图片路径越界")
    return path


def _sidecar_path(image_path: Path) -> Path:
    return image_path.with_suffix(".json")


def resolve_data_annotation_image_asset(
    filename: str,
    *,
    entry_id: str | None = None,
) -> FanxiuDataAnnotationImageAsset:
    name = normalize_data_annotation_image_filename(filename)
    safe_entry_id = sanitize_data_annotation_entry_id(entry_id)
    image_path = _resolve_child_path(data_annotation_entry_image_dir(safe_entry_id), name)
    return FanxiuDataAnnotationImageAsset(
        filename=name,
        entry_id=safe_entry_id,
        path=image_path,
        sidecar_path=_sidecar_path(image_path),
        exists=image_path.is_file(),
        storage_kind="entry_images" if image_path.is_file() else "missing",
    )
