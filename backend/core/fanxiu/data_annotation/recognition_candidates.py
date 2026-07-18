from __future__ import annotations

from typing import Any

from pyxllib.autogui import View, image_number


def recognition_candidate_ids_by_layer(
    asset_tree: list[dict[str, Any]],
    images: dict[int, dict[str, Any]],
    layer: int,
    *,
    include_popups: bool | None = None,
) -> list[int]:
    """Return scene ids from exactly one recognition layer."""

    target_layer = int(layer)
    result: list[int] = []

    def visit(items: list[dict[str, Any]], path: tuple[str, ...]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            current_path = (*path, title) if title else path
            if str(item.get("type") or "") == "image":
                scene_id = image_number(item)
                image = images.get(int(scene_id)) if scene_id is not None else None
                in_popup_path = any("弹窗" in part for part in current_path)
                if (
                    scene_id is not None
                    and isinstance(image, dict)
                    and int(View(image).layer) == target_layer
                    and (include_popups is None or in_popup_path == include_popups)
                    and int(scene_id) not in result
                ):
                    result.append(int(scene_id))
            children = item.get("children")
            if isinstance(children, list):
                visit([child for child in children if isinstance(child, dict)], current_path)

    visit(asset_tree, ())
    return result


def default_recognition_candidate_layers(
    asset_tree: list[dict[str, Any]],
    images: dict[int, dict[str, Any]],
    *,
    include_popups: bool | None = None,
) -> list[tuple[int, list[int]]]:
    """Return Layer 1 and Layer 2 as separate, ordered recognition passes."""

    return [
        (
            layer,
            recognition_candidate_ids_by_layer(
                asset_tree,
                images,
                layer,
                include_popups=include_popups,
            ),
        )
        for layer in (1, 2)
    ]


def default_recognition_candidate_ids(
    asset_tree: list[dict[str, Any]],
    images: dict[int, dict[str, Any]],
    *,
    include_popups: bool | None = None,
) -> list[int]:
    """Return Layer 1/2 scene ids in stable asset order.

    Asset nesting is only storage metadata.  Candidate enumeration deliberately
    does not derive recognition relations from folders or image children.
    """

    layer_ids = {
        layer: set(ids)
        for layer, ids in default_recognition_candidate_layers(
            asset_tree,
            images,
            include_popups=include_popups,
        )
    }
    result: list[int] = []

    def visit(items: list[dict[str, Any]], path: tuple[str, ...]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            current_path = (*path, title) if title else path
            if str(item.get("type") or "") == "image":
                scene_id = image_number(item)
                image = images.get(int(scene_id)) if scene_id is not None else None
                in_popup_path = any("弹窗" in part for part in current_path)
                if (
                    scene_id is not None
                    and isinstance(image, dict)
                    and int(scene_id) in layer_ids.get(int(View(image).layer), set())
                    and (include_popups is None or in_popup_path == include_popups)
                    and int(scene_id) not in result
                ):
                    result.append(int(scene_id))
            children = item.get("children")
            if isinstance(children, list):
                visit([child for child in children if isinstance(child, dict)], current_path)

    visit(asset_tree, ())
    return result
