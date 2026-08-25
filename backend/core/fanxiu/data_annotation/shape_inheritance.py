from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable

from pyxllib.autogui import View, image_number


INHERITANCE_SOURCE_SCENE_ID = "_inheritanceSourceSceneId"
INHERITANCE_HOST_SCENE_ID = "_inheritanceHostSceneId"
INHERITANCE_SOURCE_SHAPE_ID = "_inheritanceSourceShapeId"


class ShapeInheritanceError(ValueError):
    """The declared scene shape inheritance graph cannot be resolved safely."""


@dataclass(frozen=True)
class ShapeInheritanceResolution:
    tree: list[dict[str, Any]]
    images: dict[int, dict[str, Any]]
    raw_images: dict[int, dict[str, Any]]
    parent_ids: dict[int, tuple[int, ...]]


def parse_parent_scene_ids(value: Any) -> tuple[int, ...]:
    """Parse the comma-separated scene ids stored by the annotation page."""

    result: list[int] = []
    for token in re.split(r"[,，]", str(value or "")):
        text = token.strip().lstrip("#").strip()
        if not text.isdecimal():
            continue
        scene_id = int(text)
        if scene_id > 0 and scene_id not in result:
            result.append(scene_id)
    return tuple(result)


def _index_images(nodes: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}

    def visit(items: Iterable[dict[str, Any]]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") == "image":
                scene_id = image_number(item)
                if scene_id is not None:
                    if int(scene_id) in result:
                        raise ShapeInheritanceError(f"Shape 继承解析失败：场景编号 #{scene_id} 重复")
                    result[int(scene_id)] = item
            children = item.get("children")
            if isinstance(children, list):
                visit(child for child in children if isinstance(child, dict))

    visit(nodes)
    return result


def _shape_unit_key(shape: dict[str, Any]) -> tuple[Any, ...]:
    source_scene_id = int(shape.get(INHERITANCE_SOURCE_SCENE_ID) or 0)
    source_shape_id = str(shape.get(INHERITANCE_SOURCE_SHAPE_ID) or shape.get("id") or "").strip()
    if source_shape_id:
        return source_scene_id, "id", source_shape_id
    return (
        source_scene_id,
        "signature",
        str(shape.get("title") or "").strip(),
        round(float(shape.get("x") or 0), 8),
        round(float(shape.get("y") or 0), 8),
        round(float(shape.get("w") or 0), 8),
        round(float(shape.get("h") or 0), 8),
    )


def _annotate_shape_tree(shape: dict[str, Any], *, source_scene_id: int, host_scene_id: int) -> dict[str, Any]:
    resolved = copy.deepcopy(shape)

    def visit(item: dict[str, Any]) -> None:
        original_source = int(item.get(INHERITANCE_SOURCE_SCENE_ID) or source_scene_id)
        item[INHERITANCE_SOURCE_SCENE_ID] = original_source
        item[INHERITANCE_HOST_SCENE_ID] = int(host_scene_id)
        item[INHERITANCE_SOURCE_SHAPE_ID] = str(
            item.get(INHERITANCE_SOURCE_SHAPE_ID) or item.get("id") or ""
        )
        children = item.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    visit(child)

    visit(resolved)
    return resolved


def resolve_shape_inheritance(nodes: list[dict[str, Any]]) -> ShapeInheritanceResolution:
    """Resolve multiple scene parents into host-scene effective Shape trees.

    The input tree remains the raw annotation fact.  The returned tree is a
    derived runtime projection: inherited Shape configuration is copied into
    each child scene, while the child scene's filename, dimensions and image
    context remain the effective host for matching, crops, masks and clicks.
    """

    raw_images = _index_images(nodes)
    parent_ids = {
        scene_id: parse_parent_scene_ids(image.get("parentSceneIds"))
        for scene_id, image in raw_images.items()
    }
    for scene_id, declared_parents in parent_ids.items():
        for parent_id in declared_parents:
            if parent_id == scene_id:
                raise ShapeInheritanceError(f"Shape 继承解析失败：场景 #{scene_id} 不能继承自身")
            if parent_id not in raw_images:
                raise ShapeInheritanceError(
                    f"Shape 继承解析失败：场景 #{scene_id} 引用了不存在的父场景 #{parent_id}"
                )

    resolved_shapes: dict[int, list[dict[str, Any]]] = {}
    resolving: list[int] = []

    def resolve_scene(scene_id: int) -> list[dict[str, Any]]:
        cached = resolved_shapes.get(scene_id)
        if cached is not None:
            return copy.deepcopy(cached)
        if scene_id in resolving:
            start = resolving.index(scene_id)
            cycle = [*resolving[start:], scene_id]
            chain = " -> ".join(f"#{item}" for item in cycle)
            raise ShapeInheritanceError(f"Shape 继承解析失败：检测到循环继承 {chain}")
        resolving.append(scene_id)
        effective: list[dict[str, Any]] = []
        seen_units: set[tuple[Any, ...]] = set()
        for parent_id in parent_ids.get(scene_id, ()):
            for inherited in resolve_scene(parent_id):
                hosted = _annotate_shape_tree(
                    inherited,
                    source_scene_id=int(inherited.get(INHERITANCE_SOURCE_SCENE_ID) or parent_id),
                    host_scene_id=scene_id,
                )
                key = _shape_unit_key(hosted)
                if key in seen_units:
                    continue
                seen_units.add(key)
                effective.append(hosted)
        own_shapes = raw_images[scene_id].get("shapes")
        for raw_shape in own_shapes if isinstance(own_shapes, list) else []:
            if not isinstance(raw_shape, dict):
                continue
            own = _annotate_shape_tree(
                raw_shape,
                source_scene_id=scene_id,
                host_scene_id=scene_id,
            )
            key = _shape_unit_key(own)
            if key in seen_units:
                continue
            seen_units.add(key)
            effective.append(own)
        resolving.pop()
        resolved_shapes[scene_id] = copy.deepcopy(effective)
        return copy.deepcopy(effective)

    resolved_tree = copy.deepcopy(nodes)
    resolved_images = _index_images(resolved_tree)
    for scene_id, image in resolved_images.items():
        image["shapes"] = resolve_scene(scene_id)
        image["_rawShapeCount"] = len(raw_images[scene_id].get("shapes") or [])
        image["_effectiveShapeCount"] = len(image["shapes"])
        image["_shapeParentSceneIds"] = list(parent_ids.get(scene_id, ()))

    return ShapeInheritanceResolution(
        tree=resolved_tree,
        images=resolved_images,
        raw_images=raw_images,
        parent_ids={scene_id: tuple(ids) for scene_id, ids in parent_ids.items()},
    )


def find_raw_shape_for_effective(
    raw_images: dict[int, dict[str, Any]],
    effective_shape: dict[str, Any],
) -> dict[str, Any] | None:
    """Find the source annotation dict behind one derived effective Shape."""

    try:
        source_scene_id = int(effective_shape.get(INHERITANCE_SOURCE_SCENE_ID))
    except (TypeError, ValueError):
        return None
    source_image = raw_images.get(source_scene_id)
    if not isinstance(source_image, dict):
        return None
    source_shape_id = str(
        effective_shape.get(INHERITANCE_SOURCE_SHAPE_ID) or effective_shape.get("id") or ""
    ).strip()
    candidates = View(source_image).get_shapes()
    if source_shape_id:
        return next(
            (
                shape.raw
                for shape in candidates
                if str(shape.raw.get("id") or "").strip() == source_shape_id
            ),
            None,
        )
    title = str(effective_shape.get("title") or "").strip()
    matches = [
        shape.raw
        for shape in candidates
        if shape.title == title
        and all(
            abs(float(shape.raw.get(key) or 0) - float(effective_shape.get(key) or 0)) < 1e-8
            for key in ("x", "y", "w", "h")
        )
    ]
    return matches[0] if len(matches) == 1 else None
