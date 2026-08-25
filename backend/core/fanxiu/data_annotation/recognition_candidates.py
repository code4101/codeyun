from __future__ import annotations

from typing import Any

from pyxllib.autogui import View, image_number


def scene_asset_directory_path(
    asset_tree: list[dict[str, Any]],
    scene_id: int | None,
) -> str:
    """Return the recognized scene's authoritative parent-directory path."""

    if scene_id is None:
        return ""
    target = int(scene_id)

    def visit(items: list[dict[str, Any]], directories: tuple[str, ...]) -> tuple[str, ...] | None:
        for item in items:
            if not isinstance(item, dict):
                continue
            is_image = str(item.get("type") or "") == "image"
            title = str(item.get("title") or "").strip()
            current_directories = directories if is_image or not title else (*directories, title)
            if is_image and image_number(item) == target:
                return current_directories
            children = item.get("children")
            if isinstance(children, list):
                found = visit(
                    [child for child in children if isinstance(child, dict)],
                    current_directories,
                )
                if found is not None:
                    return found
        return None

    return "/".join(visit(asset_tree, ()) or ())


def _is_floating_overlay_image(image: dict[str, Any]) -> bool:
    """Return whether an image describes a movable overlay, not a base scene."""

    identity_shapes = [
        shape
        for shape in View(image).get_shapes(include_groups=False)
        if shape.is_scene_identity
    ]
    return bool(identity_shapes) and all(bool(shape.raw.get("floating")) for shape in identity_shapes)


def has_scene_identity(image: dict[str, Any]) -> bool:
    """Return whether an asset is eligible to be a recognition graph node."""

    return any(
        shape.is_scene_identity
        for shape in View(image).get_shapes(include_groups=False)
    )


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
                    and has_scene_identity(image)
                    and not _is_floating_overlay_image(image)
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


def layer3_recognition_candidate_ids(
    asset_tree: list[dict[str, Any]],
    images: dict[int, dict[str, Any]],
    *,
    include_popups: bool | None = None,
) -> list[int]:
    """Return identity-free Layer 3 scene frames in stable asset order.

    Layer 3 is intentionally separate from the scene-identity graph.  Its
    reference frames are evaluated by full-frame similarity only after the
    identity-bearing layers fail to produce a result.
    """

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
                    and not has_scene_identity(image)
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
