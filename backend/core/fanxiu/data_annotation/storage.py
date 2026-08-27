from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from filelock import FileLock
from pyxllib.autogui import image_number as runtime_image_number

from backend.core.settings import get_settings
from backend.core.fanxiu.data_annotation.state import write_data_annotation_json


DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID = "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"
DATA_ANNOTATION_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
DATA_ANNOTATION_FOLDER_ROLES = frozenset({"business-root", "task-family", "task", "shared"})
DATA_ANNOTATION_CONTAINER_FOLDER_ROLES = frozenset({"business-root", "task-family"})

AssetStorageKind = Literal["entry_images", "missing"]


@dataclass(frozen=True)
class FanxiuDataAnnotationImageAsset:
    filename: str
    entry_id: str
    path: Path
    sidecar_path: Path
    exists: bool
    storage_kind: AssetStorageKind


@dataclass(frozen=True)
class FanxiuDataAnnotationAssetTreeSnapshot:
    tree: list[dict[str, Any]]
    revision: str
    updated_at: float
    exists: bool


@dataclass(frozen=True)
class FanxiuDataAnnotationFrameTreeSave:
    asset: FanxiuDataAnnotationImageAsset
    snapshot: FanxiuDataAnnotationAssetTreeSnapshot


class FanxiuDataAnnotationAssetTreeConflict(RuntimeError):
    pass


def validate_data_annotation_task_directories(nodes: list[dict[str, Any]]) -> None:
    """Enforce explicit task encapsulation for opted-in asset directories.

    Legacy folders without ``folderRole`` remain readable and writable. Once a
    business root or task family is marked, scenes must live below a ``task`` or
    ``shared`` directory instead of being flattened into the container.
    """

    def visit(items: list[dict[str, Any]], ancestors: tuple[str, ...]) -> None:
        for node in items:
            if not isinstance(node, dict):
                continue
            title = str(node.get("title") or "").strip() or str(node.get("id") or "未命名")
            node_type = str(node.get("type") or "")
            role = str(node.get("folderRole") or "").strip()
            path = (*ancestors, title)
            path_label = " / ".join(path)

            if role and node_type != "folder":
                raise ValueError(f"scene/frame「{path_label}」不能声明 folderRole")
            if role and role not in DATA_ANNOTATION_FOLDER_ROLES:
                raise ValueError(f"资产目录「{path_label}」的 folderRole 不合法：{role}")

            children = node.get("children")
            child_nodes = children if isinstance(children, list) else []
            if role in DATA_ANNOTATION_CONTAINER_FOLDER_ROLES:
                direct_images = [
                    str(child.get("title") or child.get("id") or "未命名")
                    for child in child_nodes
                    if isinstance(child, dict) and child.get("type") == "image"
                ]
                if direct_images:
                    joined = "、".join(direct_images[:10])
                    raise ValueError(
                        f"资产目录「{path_label}」是容器目录，不能直接包含 scene/frame：{joined}；"
                        "请先创建 folderRole=task 的任务目录，跨 task 共用场景使用 folderRole=shared"
                    )
            visit(child_nodes, path)

    visit(nodes, ())


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


def _image_suffix_from_bytes(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    return None


def _filename_number(filename: str) -> int | None:
    # Scene identity is defined by the Runtime parser, including prefixed
    # assets such as ``lingxiao-preview-580.png``.  Scanning only pure numeric
    # filenames forgets real IDs and can allocate an already-used scene.
    return runtime_image_number({"filename": Path(str(filename or "")).name})


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
    actual_suffix = _image_suffix_from_bytes(data)
    if filename:
        normalized_filename = normalize_data_annotation_image_filename(filename)
        resolved_filename = (
            str(Path(normalized_filename).with_suffix(actual_suffix))
            if actual_suffix
            else normalized_filename
        )
    else:
        resolved_filename = next_data_annotation_image_filename(safe_entry_id, suffix=actual_suffix or ".png")
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


def normalize_data_annotation_shape_load_directions(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize the small, orthogonal window-loading annotation contract."""

    legacy_keys = ("contentDirection", "content_direction", "内容方向")
    direction_aliases = {
        "": "none",
        "none": "none",
        "off": "none",
        "无": "none",
        "up": "up",
        "上": "up",
        "down": "down",
        "下": "down",
        "left": "left",
        "左": "left",
        "right": "right",
        "右": "right",
    }

    def normalize_direction(value: Any) -> Any:
        text = str(value or "").strip().lower()
        return direction_aliases.get(text, value)

    def normalize_shape(shape: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(shape)
        has_direction = "loadDirection" in normalized
        direction = normalized.get("loadDirection")
        if not has_direction:
            for key in ("load_direction", "窗口加载方向", *legacy_keys):
                if key in normalized:
                    direction = normalized.get(key)
                    has_direction = True
                    break
        for key in ("load_direction", "窗口加载方向", *legacy_keys):
            normalized.pop(key, None)
        if has_direction:
            normalized["loadDirection"] = normalize_direction(direction)
        load_mode = normalized.pop("load_mode", normalized.pop("loadMode", None))
        load_boundary = normalized.pop(
            "load_boundary", normalized.pop("loadBoundary", None)
        )
        initial_position = normalized.pop(
            "load_initial_position", normalized.pop("loadInitialPosition", None)
        )
        # Keep the asset contract sparse. Missing values mean the common case:
        # continuous stepping, a bounded control, and an initial cursor at the
        # canonical starting edge. Only exceptional behavior needs annotation.
        if str(load_mode or "").strip().lower() == "paged":
            normalized["loadMode"] = "paged"
        if str(load_boundary or "").strip().lower() == "cyclic":
            normalized["loadBoundary"] = "cyclic"
        if str(initial_position or "").strip().lower() == "unknown":
            normalized["loadInitialPosition"] = "unknown"
        children = normalized.get("children")
        if isinstance(children, list):
            normalized["children"] = [
                normalize_shape(child)
                for child in children
                if isinstance(child, dict)
            ]
        return normalized

    def normalize_node(node: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(node)
        shapes = normalized.get("shapes")
        if isinstance(shapes, list):
            normalized["shapes"] = [
                normalize_shape(shape)
                for shape in shapes
                if isinstance(shape, dict)
            ]
        children = normalized.get("children")
        if isinstance(children, list):
            normalized["children"] = [
                normalize_node(child)
                for child in children
                if isinstance(child, dict)
            ]
        return normalized

    return [normalize_node(node) for node in nodes if isinstance(node, dict)]


def normalize_data_annotation_scene_parent_ids(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize the raw scene-level parent list without resolving inheritance."""

    def scene_number(node: dict[str, Any]) -> int | None:
        filename_number = _filename_number(str(node.get("filename") or ""))
        if filename_number is not None:
            return filename_number
        for key in ("title", "id"):
            match = re.search(r"(?:^|#|[^\d])0*(\d{1,6})(?=[^\d]|$)", str(node.get(key) or ""))
            if match:
                return int(match.group(1))
        return None

    def normalize_parent_ids(value: Any, *, current_scene_id: int | None) -> str:
        raw_items = value if isinstance(value, list) else re.split(r"[,，]", str(value or ""))
        seen: set[int] = set()
        result: list[str] = []
        for item in raw_items:
            text = str(item or "").strip().lstrip("#")
            if not text.isdigit():
                continue
            parent_id = int(text)
            if parent_id <= 0 or parent_id == current_scene_id or parent_id in seen:
                continue
            seen.add(parent_id)
            result.append(str(parent_id))
        return ",".join(result)

    def normalize_node(node: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(node)
        if normalized.get("type") == "image" and "parentSceneIds" in normalized:
            parent_ids = normalize_parent_ids(
                normalized.get("parentSceneIds"),
                current_scene_id=scene_number(normalized),
            )
            if parent_ids:
                normalized["parentSceneIds"] = parent_ids
            else:
                normalized.pop("parentSceneIds", None)
        children = normalized.get("children")
        if isinstance(children, list):
            normalized["children"] = [
                normalize_node(child)
                for child in children
                if isinstance(child, dict)
            ]
        return normalized

    return [normalize_node(node) for node in nodes if isinstance(node, dict)]


def normalize_data_annotation_image_titles(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep image titles as optional human nicknames, never duplicated filenames."""

    def normalize_node(node: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(node)
        if normalized.get("type") == "image":
            title = str(normalized.get("title") or "").strip()
            filename = str(normalized.get("filename") or "").strip()
            normalized["title"] = "" if filename and title.casefold() == filename.casefold() else title
        children = normalized.get("children")
        if isinstance(children, list):
            normalized["children"] = [
                normalize_node(child)
                for child in children
                if isinstance(child, dict)
            ]
        return normalized

    return [normalize_node(node) for node in nodes if isinstance(node, dict)]


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
        # This retired field encoded a single-parent recognition hierarchy.
        # Persisted asset trees are flattened by dropping it on the next save.
        normalized.pop("recognitionParentId", None)
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
            asset = save_data_annotation_image_bytes(decoded, entry_id=safe_entry_id, filename=filename)
            normalized["filename"] = asset.filename
            return normalized

        filename = str(normalized.get("filename") or "").strip()
        if filename:
            asset = resolve_data_annotation_image_asset(filename, entry_id=safe_entry_id)
            normalized["filename"] = asset.filename
            if not asset.exists:
                missing.append(asset.filename)
        return normalized

    normalized_tree = normalize_data_annotation_image_titles(
        [normalize_node(node) for node in nodes if isinstance(node, dict)]
    )
    return normalized_tree, missing


def _asset_tree_revision(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _asset_tree_lock(path: Path) -> FileLock:
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path.with_name(f"{path.name}.lock")), timeout=30)


def _read_asset_tree_unlocked(path: Path) -> FanxiuDataAnnotationAssetTreeSnapshot:
    if not path.is_file():
        return FanxiuDataAnnotationAssetTreeSnapshot(tree=[], revision="", updated_at=0.0, exists=False)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = []
    return FanxiuDataAnnotationAssetTreeSnapshot(
        tree=payload if isinstance(payload, list) else [],
        revision=_asset_tree_revision(path),
        updated_at=path.stat().st_mtime,
        exists=True,
    )


def read_data_annotation_asset_tree_snapshot(path: Path) -> FanxiuDataAnnotationAssetTreeSnapshot:
    with _asset_tree_lock(path):
        return _read_asset_tree_unlocked(path)


def save_data_annotation_asset_tree_snapshot(
    path: Path,
    tree: list[dict[str, Any]],
    *,
    entry_id: str | None = None,
    expected_revision: str | None = None,
    before_write: Callable[[], None] | None = None,
) -> FanxiuDataAnnotationAssetTreeSnapshot:
    with _asset_tree_lock(path):
        current_revision = _asset_tree_revision(path)
        if expected_revision is not None and expected_revision != current_revision:
            raise FanxiuDataAnnotationAssetTreeConflict("资产树版本已变化")
        normalized_tree, missing = _normalize_asset_tree_images(tree, entry_id=entry_id)
        normalized_tree = normalize_data_annotation_shape_load_directions(normalized_tree)
        normalized_tree = normalize_data_annotation_scene_parent_ids(normalized_tree)
        validate_data_annotation_task_directories(normalized_tree)
        if missing:
            joined = "、".join(str(item) for item in missing[:10])
            raise FileNotFoundError(f"资产树引用的图片不存在：{joined}")
        if before_write is not None:
            before_write()
        write_data_annotation_json(path, normalized_tree)
        return _read_asset_tree_unlocked(path)


def save_data_annotation_frame_tree_node(
    path: Path,
    data: bytes,
    node: dict[str, Any],
    *,
    entry_id: str | None = None,
    parent_id: str | None = None,
    after_node_id: str | None = None,
    expected_revision: str | None = None,
    before_write: Callable[[], None] | None = None,
) -> FanxiuDataAnnotationFrameTreeSave:
    """Persist one captured frame and its stable-ID tree insertion as one operation.

    A stale tree revision is safe for a new node: the insertion is replayed on the
    latest tree while holding the same cross-process lock.  Reusing an existing ID,
    or losing the stable parent/anchor, is a real semantic conflict and fails before
    any image is written.
    """

    node_id = str(node.get("id") or "").strip()
    if not node_id:
        raise ValueError("资产节点缺少稳定 ID")
    if node.get("type") != "image":
        raise ValueError("保存帧只能创建 image 节点")

    def find_node(nodes: list[dict[str, Any]], target_id: str) -> dict[str, Any] | None:
        for item in nodes:
            if str(item.get("id") or "") == target_id:
                return item
            children = item.get("children")
            if isinstance(children, list):
                found = find_node(children, target_id)
                if found is not None:
                    return found
        return None

    def find_siblings(nodes: list[dict[str, Any]], target_id: str) -> tuple[list[dict[str, Any]], int] | None:
        for index, item in enumerate(nodes):
            if str(item.get("id") or "") == target_id:
                return nodes, index
            children = item.get("children")
            if isinstance(children, list):
                found = find_siblings(children, target_id)
                if found is not None:
                    return found
        return None

    with _asset_tree_lock(path):
        current = _read_asset_tree_unlocked(path)
        tree = json.loads(json.dumps(current.tree, ensure_ascii=False))
        if find_node(tree, node_id) is not None:
            raise FanxiuDataAnnotationAssetTreeConflict(f"资产节点 {node_id} 已存在")

        if parent_id:
            parent = find_node(tree, parent_id)
            if parent is None or parent.get("type") != "folder":
                raise FanxiuDataAnnotationAssetTreeConflict(f"目标目录 {parent_id} 已变化")
            target = parent.setdefault("children", [])
            if not isinstance(target, list):
                raise FanxiuDataAnnotationAssetTreeConflict(f"目标目录 {parent_id} 已变化")
            insert_at = len(target)
        elif after_node_id:
            location = find_siblings(tree, after_node_id)
            if location is None:
                raise FanxiuDataAnnotationAssetTreeConflict(f"插入锚点 {after_node_id} 已变化")
            target, anchor_index = location
            insert_at = anchor_index + 1
        else:
            target = tree
            insert_at = len(target)

        # A revision mismatch alone is not a conflict: this unique stable-ID
        # insertion does not overwrite any existing node and is applied to latest.
        _ = expected_revision
        asset: FanxiuDataAnnotationImageAsset | None = None
        try:
            requested_filename = str(node.get("filename") or "").strip() or None
            if requested_filename:
                requested_asset = resolve_data_annotation_image_asset(requested_filename, entry_id=entry_id)
                if requested_asset.exists:
                    raise FanxiuDataAnnotationAssetTreeConflict(
                        f"资产图片 {requested_asset.filename} 已存在"
                    )
                requested_number = _filename_number(requested_asset.filename)
                expected_filename = next_data_annotation_image_filename(entry_id)
                expected_number = _filename_number(expected_filename)
                if requested_number is None or requested_number != expected_number:
                    requested_label = (
                        f"#{requested_number}" if requested_number is not None else requested_asset.filename
                    )
                    raise ValueError(
                        f"新增资产编号必须连续：当前应为 #{expected_number}，不能创建 {requested_label}"
                    )
            inserted = dict(node)
            inserted.pop("imageDataUrl", None)
            target.insert(insert_at, inserted)
            validate_data_annotation_task_directories(tree)
            asset = save_data_annotation_image_bytes(
                data,
                entry_id=entry_id,
                filename=requested_filename,
            )
            inserted["filename"] = asset.filename
            normalized_tree, missing = _normalize_asset_tree_images(tree, entry_id=entry_id)
            normalized_tree = normalize_data_annotation_shape_load_directions(normalized_tree)
            normalized_tree = normalize_data_annotation_scene_parent_ids(normalized_tree)
            validate_data_annotation_task_directories(normalized_tree)
            if missing:
                joined = "、".join(str(item) for item in missing[:10])
                raise FileNotFoundError(f"资产树引用的图片不存在：{joined}")
            if before_write is not None:
                before_write()
            write_data_annotation_json(path, normalized_tree)
            return FanxiuDataAnnotationFrameTreeSave(asset=asset, snapshot=_read_asset_tree_unlocked(path))
        except Exception:
            if asset is not None:
                for candidate in (asset.path, asset.sidecar_path):
                    try:
                        candidate.unlink(missing_ok=True)
                    except OSError:
                        pass
            raise


def update_data_annotation_asset_tree(
    path: Path,
    update: Callable[[list[dict[str, Any]]], bool],
    *,
    before_write: Callable[[], None] | None = None,
) -> FanxiuDataAnnotationAssetTreeSnapshot:
    """Apply one semantic mutation to the latest tree under a cross-process lock."""

    with _asset_tree_lock(path):
        current = _read_asset_tree_unlocked(path)
        tree = json.loads(json.dumps(current.tree, ensure_ascii=False))
        if not update(tree):
            return current
        normalized_tree = normalize_data_annotation_shape_load_directions(tree)
        normalized_tree = normalize_data_annotation_scene_parent_ids(normalized_tree)
        validate_data_annotation_task_directories(normalized_tree)
        if before_write is not None:
            before_write()
        write_data_annotation_json(path, normalized_tree)
        return _read_asset_tree_unlocked(path)


def save_data_annotation_asset_tree_bundle(
    path: Path,
    tree: list[dict[str, Any]],
    *,
    entry_id: str | None = None,
    before_write: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    return save_data_annotation_asset_tree_snapshot(
        path,
        tree,
        entry_id=entry_id,
        before_write=before_write,
    ).tree
