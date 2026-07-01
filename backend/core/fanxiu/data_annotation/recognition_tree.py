from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyxllib.autogui import View, image_number


@dataclass(frozen=True)
class RecognitionTreeNode:
    """Runtime projection of an asset-tree image into recognition candidates."""

    scene_id: int
    image: dict[str, Any]
    parent_scene_ids: tuple[int, ...]
    asset_path: tuple[str, ...]
    depth: int
    layer: int
    order: int
    in_popup_path: bool

    @property
    def is_root(self) -> bool:
        return not self.parent_scene_ids


def effective_recognition_layer(image: dict[str, Any]) -> int:
    """Return the derived recognition layer for an asset-tree scene.

    Source facts live on the asset tree: explicit layer1 marker, scene identity
    shapes, and traversal order. Layer2 and layer3 are derived from whether the
    scene has identity shapes.
    """

    return int(View(image).layer)


def build_recognition_tree_nodes(
    asset_tree: list[dict[str, Any]],
    images: dict[int, dict[str, Any]],
) -> list[RecognitionTreeNode]:
    """Project asset-tree facts into a runtime recognition-tree node list.

    Folder nesting stays in ``asset_path`` as classification metadata. Only
    ``image.children`` contributes to ``parent_scene_ids`` and therefore to
    scene/sub-scene candidate refinement.
    """

    nodes: list[RecognitionTreeNode] = []

    def visit(
        items: list[dict[str, Any]],
        *,
        asset_path: tuple[str, ...],
        parent_scene_ids: tuple[int, ...],
        depth: int,
    ) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            node_type = str(item.get("type") or "")
            title = str(item.get("title") or "").strip()
            current_asset_path = (*asset_path, title) if title else asset_path
            current_parent_scene_ids = parent_scene_ids
            current_depth = depth
            if node_type == "image":
                scene_id = image_number(item)
                if scene_id is not None and int(scene_id) in images and isinstance(images.get(int(scene_id)), dict):
                    nodes.append(
                        RecognitionTreeNode(
                            scene_id=int(scene_id),
                            image=images[int(scene_id)],
                            parent_scene_ids=parent_scene_ids,
                            asset_path=current_asset_path,
                            depth=depth,
                            layer=effective_recognition_layer(images[int(scene_id)]),
                            order=len(nodes),
                            in_popup_path=any("弹窗" in part for part in current_asset_path),
                        )
                    )
                    current_parent_scene_ids = (*parent_scene_ids, int(scene_id))
                    current_depth = depth + 1
            children = item.get("children")
            if isinstance(children, list):
                visit(
                    [child for child in children if isinstance(child, dict)],
                    asset_path=current_asset_path,
                    parent_scene_ids=current_parent_scene_ids,
                    depth=current_depth,
                )

    visit(asset_tree, asset_path=(), parent_scene_ids=(), depth=0)
    return nodes


def runtime_root_scene_candidate_ids(
    asset_tree: list[dict[str, Any]],
    images: dict[int, dict[str, Any]],
    *,
    include_popups: bool | None = None,
) -> list[int]:
    """Return root scene candidates for Runtime global detection.

    This preserves the existing behavior: root frames are ordered by layer
    1 -> 2 -> 3, popup-only scans can ask for popup roots, and sub-scenes do
    not enter the global root queue by directory position alone.
    """

    candidates: list[int] = []
    layer_buckets: dict[int, list[int]] = {1: [], 2: [], 3: []}

    def add_candidate(scene_id: int) -> None:
        if scene_id not in candidates:
            candidates.append(scene_id)

    def add_layer_candidate(scene_id: int) -> None:
        image = images.get(int(scene_id))
        if not isinstance(image, dict):
            return
        bucket = layer_buckets.get(effective_recognition_layer(image))
        if bucket is not None and scene_id not in bucket:
            bucket.append(scene_id)

    for node in build_recognition_tree_nodes(asset_tree, images):
        if not node.is_root:
            continue
        if include_popups is not None and node.in_popup_path != include_popups:
            continue
        if include_popups is True:
            add_candidate(node.scene_id)
        else:
            add_layer_candidate(node.scene_id)
    if include_popups is not True:
        for layer in (1, 2, 3):
            for scene_id in layer_buckets[layer]:
                add_candidate(scene_id)
    return candidates


def layer0_recognition_candidate_ids(
    asset_tree: list[dict[str, Any]],
    images: dict[int, dict[str, Any]],
    preferred_scene_ids: list[int],
) -> list[int]:
    """Expand dynamic layer0 targets to their required recognition context."""

    nodes = build_recognition_tree_nodes(asset_tree, images)
    by_id = {node.scene_id: node for node in nodes}
    preferred = [int(scene_id) for scene_id in preferred_scene_ids if int(scene_id) in by_id]
    preferred_set = set(preferred)
    result: list[int] = []
    for scene_id in preferred:
        node = by_id[scene_id]
        for parent_id in node.parent_scene_ids:
            if parent_id in by_id and parent_id not in result:
                result.append(parent_id)
        if scene_id not in result:
            result.append(scene_id)
        for child in nodes:
            if child.scene_id in result:
                continue
            if any(parent_id in preferred_set for parent_id in child.parent_scene_ids):
                result.append(child.scene_id)
    return result
