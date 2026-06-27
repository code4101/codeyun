from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.core.settings import get_settings
from backend.core.fanxiu.data_annotation.state import write_data_annotation_json


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


def _decode_data_url(data_url: str) -> bytes | None:
    text = str(data_url or "").strip()
    if not text.startswith("data:image/") or "," not in text:
        return None
    header, payload = text.split(",", 1)
    if ";base64" not in header.lower():
        return None
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None


def decode_data_annotation_image_data_url(data_url: str) -> bytes:
    decoded = _decode_data_url(data_url)
    if not decoded:
        raise ValueError("data-annotation 图片数据不合法")
    return decoded


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{hashlib.sha1(data).hexdigest()[:12]}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _filename_number(filename: str) -> int | None:
    match = re.fullmatch(r"0*(\d+)\.[^.]+", Path(str(filename or "")).name)
    if not match:
        return None
    return int(match.group(1))


def _asset_tree_image_numbers(entry_id: str | None = None) -> list[int]:
    path = data_annotation_asset_tree_path(entry_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    numbers: list[int] = []

    def walk(nodes: Any) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("type") == "image":
                number = _filename_number(str(node.get("filename") or ""))
                if number is not None:
                    numbers.append(number)
            walk(node.get("children"))

    walk(payload)
    return numbers


def next_data_annotation_image_filename(entry_id: str | None = None, suffix: str = ".png") -> str:
    safe_entry_id = sanitize_data_annotation_entry_id(entry_id)
    image_dir = data_annotation_entry_image_dir(safe_entry_id)
    numbers = _asset_tree_image_numbers(safe_entry_id)
    if image_dir.exists():
        for path in image_dir.iterdir():
            if not path.is_file():
                continue
            number = _filename_number(path.name)
            if number is not None:
                numbers.append(number)
    return f"{(max(numbers) if numbers else 0) + 1:04d}{suffix}"


def save_data_annotation_image_bytes(
    data: bytes,
    *,
    entry_id: str | None = None,
    filename: str | None = None,
) -> FanxiuDataAnnotationImageAsset:
    safe_entry_id = sanitize_data_annotation_entry_id(entry_id)
    resolved_filename = filename or next_data_annotation_image_filename(safe_entry_id)
    asset = resolve_data_annotation_image_asset(resolved_filename, entry_id=safe_entry_id)
    _atomic_write_bytes(asset.path, data)
    return FanxiuDataAnnotationImageAsset(
        filename=asset.filename,
        entry_id=safe_entry_id,
        path=asset.path,
        sidecar_path=asset.sidecar_path,
        exists=True,
        storage_kind="entry_images",
    )


def _image_filename_from_node(node: dict[str, Any], index: int) -> str:
    raw = str(node.get("filename") or "").strip()
    if raw:
        return normalize_data_annotation_image_filename(raw)
    for key in ("title", "id"):
        text = str(node.get(key) or "")
        match = re.search(r"(?:^|#|[^\d])0*(\d{1,6})(?=\.[^.]+$|[^\d]|$)", text)
        if match:
            return f"{int(match.group(1)):04d}.png"
    return f"frame-{index:04d}.png"


def _normalize_asset_tree_images(
    nodes: list[dict[str, Any]],
    *,
    entry_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    safe_entry_id = sanitize_data_annotation_entry_id(entry_id)
    image_count = 0
    missing: list[str] = []

    def normalize_node(node: dict[str, Any]) -> dict[str, Any]:
        nonlocal image_count
        normalized = dict(node)
        children = normalized.get("children")
        if isinstance(children, list):
            normalized["children"] = [
                normalize_node(child)
                for child in children
                if isinstance(child, dict)
            ]
        elif "children" in normalized:
            normalized["children"] = []
        if normalized.get("type") != "image":
            return normalized

        image_count += 1
        data_url = normalized.pop("imageDataUrl", None)
        decoded = _decode_data_url(str(data_url or "")) if data_url else None
        if decoded:
            filename = _image_filename_from_node(normalized, image_count)
            asset = resolve_data_annotation_image_asset(filename, entry_id=safe_entry_id)
            _atomic_write_bytes(asset.path, decoded)
            normalized["filename"] = asset.filename
            return normalized

        filename = str(normalized.get("filename") or "").strip()
        if filename:
            asset = resolve_data_annotation_image_asset(filename, entry_id=safe_entry_id)
            normalized["filename"] = asset.filename
            if not asset.exists:
                missing.append(asset.filename)
        return normalized

    normalized_tree = [normalize_node(node) for node in nodes if isinstance(node, dict)]
    return normalized_tree, missing


def save_data_annotation_asset_tree_bundle(
    path: Path,
    tree: list[dict[str, Any]],
    *,
    entry_id: str | None = None,
) -> list[dict[str, Any]]:
    normalized_tree, missing = _normalize_asset_tree_images(tree, entry_id=entry_id)
    if missing:
        joined = "、".join(str(item) for item in missing[:10])
        raise FileNotFoundError(f"资产树引用的图片不存在：{joined}")
    write_data_annotation_json(path, normalized_tree)
    return normalized_tree
