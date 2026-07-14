from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyxllib.autogui import View, image_number


@dataclass(frozen=True)
class RecognitionGraphNode:
    """One scene node projected from asset metadata into the recognition graph."""

    scene_id: int
    image: dict[str, Any]
    parent_scene_ids: tuple[int, ...]
    asset_path: tuple[str, ...]
    layer: int
    order: int
    in_popup_path: bool


def effective_recognition_layer(image: dict[str, Any]) -> int:
    return int(View(image).layer)


def _flatten_shapes(shapes: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(shapes, list):
        return result
    for shape in shapes:
        if not isinstance(shape, dict):
            continue
        result.append(shape)
        result.extend(_flatten_shapes(shape.get("children")))
        result.extend(_flatten_shapes(shape.get("shapes")))
    return result


def is_explicit_local_identity_only_scene(image: dict[str, Any]) -> bool:
    identity_shapes = []
    for shape in _flatten_shapes(image.get("shapes")):
        role = str(shape.get("sceneIdentityRole") or "").strip().lower()
        if bool(shape.get("isSceneIdentity")) or role not in {"", "off", "无"}:
            identity_shapes.append(shape)
    if not identity_shapes:
        return False
    return all(str(shape.get("sceneIdentityScope") or "").strip().lower() == "local" for shape in identity_shapes)


def recognition_parent_id(image: dict[str, Any]) -> int | None:
    try:
        value = int(image.get("recognitionParentId"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def build_recognition_graph_nodes(
    asset_tree: list[dict[str, Any]],
    images: dict[int, dict[str, Any]],
) -> list[RecognitionGraphNode]:
    """Project asset scenes and ``recognitionParentId`` relations into graph nodes."""

    records: list[dict[str, Any]] = []

    def visit(items: list[dict[str, Any]], asset_path: tuple[str, ...]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            current_path = (*asset_path, title) if title else asset_path
            if str(item.get("type") or "") == "image":
                scene_id = image_number(item)
                if scene_id is not None and isinstance(images.get(int(scene_id)), dict):
                    records.append({
                        "scene_id": int(scene_id),
                        "image": images[int(scene_id)],
                        "asset_path": current_path,
                        "order": len(records),
                        "in_popup_path": any("弹窗" in part for part in current_path),
                    })
            children = item.get("children")
            if isinstance(children, list):
                visit([child for child in children if isinstance(child, dict)], current_path)

    visit(asset_tree, ())
    record_by_id = {int(record["scene_id"]): record for record in records}
    direct_parent_by_id: dict[int, int | None] = {}
    for scene_id, record in record_by_id.items():
        parent_id = recognition_parent_id(record["image"])
        direct_parent_by_id[scene_id] = (
            parent_id
            if parent_id is not None and parent_id in record_by_id and parent_id != scene_id
            else None
        )

    def parent_chain(scene_id: int) -> tuple[int, ...]:
        chain: list[int] = []
        seen = {scene_id}
        current = direct_parent_by_id.get(scene_id)
        while current is not None and current not in seen:
            chain.append(current)
            seen.add(current)
            current = direct_parent_by_id.get(current)
        chain.reverse()
        return tuple(chain)

    return [
        RecognitionGraphNode(
            scene_id=scene_id,
            image=record["image"],
            parent_scene_ids=parent_chain(scene_id),
            asset_path=record["asset_path"],
            layer=effective_recognition_layer(record["image"]),
            order=int(record["order"]),
            in_popup_path=bool(record["in_popup_path"]),
        )
        for scene_id, record in record_by_id.items()
    ]


def global_recognition_candidate_ids(
    asset_tree: list[dict[str, Any]],
    images: dict[int, dict[str, Any]],
    *,
    include_popups: bool | None = None,
) -> list[int]:
    """Return globally valid graph candidates in stable asset order.

    Material-only layer-3 frames are not scenes. OCR-only local helpers without
    a reference image or graph parent are context-bound and must be requested
    explicitly by their business flow.
    """

    result: list[int] = []
    for node in build_recognition_graph_nodes(asset_tree, images):
        if node.layer > 2:
            continue
        if include_popups is not None and node.in_popup_path != include_popups:
            continue
        if (
            not str(node.image.get("filename") or "").strip()
            and not node.parent_scene_ids
            and is_explicit_local_identity_only_scene(node.image)
        ):
            continue
        result.append(node.scene_id)
    return result


def expand_graph_candidate_ids(
    asset_tree: list[dict[str, Any]],
    images: dict[int, dict[str, Any]],
    preferred_scene_ids: list[int],
) -> list[int]:
    """Expand requested scenes to their graph ancestors and descendants."""

    nodes = build_recognition_graph_nodes(asset_tree, images)
    by_id = {node.scene_id: node for node in nodes}
    preferred = [int(scene_id) for scene_id in preferred_scene_ids if int(scene_id) in by_id]
    preferred_set = set(preferred)
    result: list[int] = []
    for scene_id in preferred:
        node = by_id[scene_id]
        for parent_id in node.parent_scene_ids:
            if parent_id not in result:
                result.append(parent_id)
        if scene_id not in result:
            result.append(scene_id)
        for child in nodes:
            if child.scene_id not in result and any(parent_id in preferred_set for parent_id in child.parent_scene_ids):
                result.append(child.scene_id)
    return result
