from __future__ import annotations

import base64
import json
import mimetypes
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from pyxllib.autogui import MatchRole, Shape, View, image_number

from backend.core.fanxiu.data_annotation.runner import create_fanxiu_runtime_runner
from backend.core.fanxiu.data_annotation.storage import resolve_data_annotation_image_asset


ShapeScoreFunc = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], float]


@dataclass(frozen=True)
class FrameStructureAdoption:
    parent_id: int
    child_id: int
    shared_shape_titles: list[str]
    average_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FrameStructureDiagnostic:
    kind: str
    level: str
    image_id: int
    message: str
    parent_id: int | None = None
    child_id: int | None = None
    score: float | None = None
    shape_titles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FrameStructureOrganizerStats:
    image_count: int = 0
    sibling_group_count: int = 0
    scored_pair_count: int = 0
    adoption_count: int = 0
    demoted_identity_count: int = 0
    layer_update_count: int = 0
    diagnostic_count: int = 0
    adoptions: list[FrameStructureAdoption] = field(default_factory=list)
    demoted_identities: list[dict[str, Any]] = field(default_factory=list)
    layer_updates: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[FrameStructureDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["adoptions"] = [item.to_dict() for item in self.adoptions]
        result["diagnostics"] = [item.to_dict() for item in self.diagnostics]
        return result


def _scene_identity_shapes(image: dict[str, Any]) -> list[Shape]:
    return [
        shape
        for shape in View(image).get_shapes(include_groups=False, include_descendants=False)
        if shape.scene_identity_role is not MatchRole.off
    ]


def _shape_key(shape: dict[str, Any]) -> str:
    raw_id = str(shape.get("id") or "").strip()
    if raw_id:
        return f"id:{raw_id}"
    title = str(shape.get("title") or "").strip()
    box = (
        round(float(shape.get("x") or 0), 4),
        round(float(shape.get("y") or 0), 4),
        round(float(shape.get("w") or 0), 4),
        round(float(shape.get("h") or 0), 4),
    )
    return f"title:{title}|box:{box}"


def _image_layer(image: dict[str, Any]) -> int:
    return int(View(image).layer)


def _is_explicit_layer1(image: dict[str, Any]) -> bool:
    try:
        return int(image.get("layer") or 0) == 1
    except (TypeError, ValueError):
        return False


def _image_title(image: dict[str, Any]) -> str:
    number = image_number(image)
    title = str(image.get("title") or "").strip()
    return f"#{number} {title}" if number is not None and title else (f"#{number}" if number is not None else title)


def _image_sort_key(image: dict[str, Any], order: int) -> tuple[int, int]:
    number = image_number(image)
    return (number if number is not None else 10**9, order)


def _same_structure_scope(parent: dict[str, Any], child: dict[str, Any], *, require_same_layer: bool) -> bool:
    if image_number(parent) is None or image_number(child) is None:
        return False
    if parent is child:
        return False
    if _is_explicit_layer1(child):
        return False
    if require_same_layer and _image_layer(parent) != _image_layer(child):
        return False
    return True


def _plan_sibling_adoptions(
    siblings: list[dict[str, Any]],
    *,
    score_shape: ShapeScoreFunc,
    threshold: float,
    min_shared_anchors: int,
    require_same_layer: bool,
) -> tuple[list[FrameStructureAdoption], dict[int, set[str]], int]:
    ordered = sorted(
        [item for item in siblings if isinstance(item, dict) and item.get("type") == "image" and image_number(item) is not None],
        key=lambda item: _image_sort_key(item, siblings.index(item)),
    )
    best_by_child: dict[int, tuple[FrameStructureAdoption, tuple[int, int, int]]] = {}
    shared_keys_by_adoption: dict[tuple[int, int], set[str]] = {}
    scored_pair_count = 0
    adoption_threshold = max(float(threshold), 95.0)

    for child_index, child in enumerate(ordered):
        child_id = image_number(child)
        if child_id is None:
            continue
        for parent_index, parent in enumerate(ordered[:child_index]):
            parent_id = image_number(parent)
            if parent_id is None or not _same_structure_scope(parent, child, require_same_layer=require_same_layer):
                continue
            parent_shapes = _scene_identity_shapes(parent)
            if not parent_shapes:
                continue
            scored_pair_count += 1
            shared: list[tuple[Shape, float]] = []
            for shape in parent_shapes:
                score = float(score_shape(parent, shape.raw, child) or 0)
                if score >= adoption_threshold:
                    shared.append((shape, score))
            if len(shared) < min_shared_anchors:
                continue
            average = sum(score for _shape, score in shared) / len(shared)
            adoption = FrameStructureAdoption(
                parent_id=int(parent_id),
                child_id=int(child_id),
                shared_shape_titles=[shape.title for shape, _score in shared],
                average_score=average,
            )
            rank = (len(shared), int(round(average * 100)), -parent_index)
            current = best_by_child.get(int(child_id))
            if current is None or rank > current[1]:
                best_by_child[int(child_id)] = (adoption, rank)
                shared_keys_by_adoption[(int(parent_id), int(child_id))] = {_shape_key(shape.raw) for shape, _score in shared}

    adoptions = [item[0] for item in best_by_child.values()]
    shared_parent_shape_keys: dict[int, set[str]] = {}
    for adoption in adoptions:
        keys = shared_keys_by_adoption.get((adoption.parent_id, adoption.child_id), set())
        if adoption.parent_id in shared_parent_shape_keys:
            shared_parent_shape_keys[adoption.parent_id] &= keys
        else:
            shared_parent_shape_keys[adoption.parent_id] = set(keys)
    return adoptions, shared_parent_shape_keys, scored_pair_count


def _score_parent_identity_on_child(
    parent: dict[str, Any],
    child: dict[str, Any],
    *,
    score_shape: ShapeScoreFunc,
    threshold: float,
) -> tuple[bool, float | None, list[str]]:
    parent_shapes = _scene_identity_shapes(parent)
    if not parent_shapes:
        return False, None, []
    scores: list[tuple[str, float]] = []
    for shape in parent_shapes:
        score = float(score_shape(parent, shape.raw, child) or 0.0)
        scores.append((shape.title, score))
    min_score = min(score for _title, score in scores) if scores else 0.0
    return min_score >= float(threshold), min_score, [title for title, _score in scores]


def _diagnose_scene_domain_group(
    roots: list[dict[str, Any]],
    *,
    score_shape: ShapeScoreFunc,
    threshold: float,
) -> list[FrameStructureDiagnostic]:
    diagnostics: list[FrameStructureDiagnostic] = []

    def image_children(image: dict[str, Any]) -> list[dict[str, Any]]:
        children = image.get("children")
        return [child for child in children if isinstance(child, dict)] if isinstance(children, list) else []

    for parent in roots:
        parent_id = image_number(parent)
        if parent_id is None:
            continue
        parent_shapes = _scene_identity_shapes(parent)
        if not parent_shapes:
            for child in image_children(parent):
                child_id = image_number(child)
                if child_id is None:
                    continue
                diagnostics.append(FrameStructureDiagnostic(
                    kind="unverifiable_parent",
                    level="warning",
                    image_id=int(parent_id),
                    parent_id=int(parent_id),
                    child_id=int(child_id),
                    message=f"{_image_title(parent)} 缺少 required 场景标识，无法校验 sub-scene {_image_title(child)}",
                ))
            continue
        child_ids = {
            int(image_number(child))
            for child in image_children(parent)
            if image_number(child) is not None
        }
        for child in image_children(parent):
            child_id = image_number(child)
            if child_id is None:
                continue
            ok, score, shape_titles = _score_parent_identity_on_child(
                parent,
                child,
                score_shape=score_shape,
                threshold=threshold,
            )
            if not ok:
                diagnostics.append(FrameStructureDiagnostic(
                    kind="invalid_child",
                    level="error",
                    image_id=int(child_id),
                    parent_id=int(parent_id),
                    child_id=int(child_id),
                    score=score,
                    shape_titles=shape_titles,
                    message=f"{_image_title(child)} 已挂在 {_image_title(parent)} 下，但不满足父 scene 场景标识",
                ))
        for candidate in roots:
            candidate_id = image_number(candidate)
            if candidate_id is None or int(candidate_id) == int(parent_id) or int(candidate_id) in child_ids:
                continue
            if _is_explicit_layer1(candidate):
                continue
            ok, score, shape_titles = _score_parent_identity_on_child(
                parent,
                candidate,
                score_shape=score_shape,
                threshold=threshold,
            )
            if ok:
                diagnostics.append(FrameStructureDiagnostic(
                    kind="missing_child_candidate",
                    level="suggestion",
                    image_id=int(candidate_id),
                    parent_id=int(parent_id),
                    child_id=int(candidate_id),
                    score=score,
                    shape_titles=shape_titles,
                    message=f"{_image_title(candidate)} 满足 {_image_title(parent)} 的场景标识，但尚未挂为 sub-scene",
                ))
    return diagnostics


def _demote_unshared_parent_identities(
    image: dict[str, Any],
    *,
    shared_shape_keys: set[str],
) -> list[dict[str, Any]]:
    demoted: list[dict[str, Any]] = []
    for shape in View(image).get_shapes(include_groups=False, include_descendants=False):
        if shape.scene_identity_role is MatchRole.off:
            continue
        if _shape_key(shape.raw) in shared_shape_keys:
            continue
        shape.raw["isSceneIdentity"] = False
        shape.raw["sceneIdentityRole"] = "off"
        shape.raw["sceneIdentityScope"] = "none"
        demoted.append({"image_id": image_number(image), "shape_title": shape.title})
    return demoted


def organize_frame_structure_in_tree(
    tree: list[dict[str, Any]],
    *,
    score_shape: ShapeScoreFunc,
    scope: str = "sibling",
    threshold: float = 80.0,
    min_shared_anchors: int = 1,
    require_same_layer: bool = True,
    demote_unshared_parent_identities: bool = True,
) -> tuple[list[dict[str, Any]], FrameStructureOrganizerStats]:
    """按场景身份公共锚点整理 frame/subframe 结构。

    `sibling` 和 `layer` 都按资产树局部 sibling group 做结构归纳。
    `layer` 是调用侧语义：结果面向识别层展示，但不做全局 root 两两匹配。
    """
    if scope not in {"sibling", "layer"}:
        raise ValueError(f"未知 frame structure scope：{scope}")

    counters = {
        "image_count": 0,
        "sibling_group_count": 0,
        "scored_pair_count": 0,
        "adoption_count": 0,
        "demoted_identity_count": 0,
        "layer_update_count": 0,
        "diagnostic_count": 0,
    }
    adoptions: list[FrameStructureAdoption] = []
    demoted_identities: list[dict[str, Any]] = []
    diagnostics: list[FrameStructureDiagnostic] = []

    def clone_node(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        result = dict(node)
        children = result.get("children")
        if isinstance(children, list):
            result["children"] = [clone_node(child) for child in children]
        return result

    migrated = [clone_node(node) for node in tree]

    def count_images(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("type") == "image":
                counters["image_count"] += 1
            children = node.get("children")
            if isinstance(children, list):
                count_images([child for child in children if isinstance(child, dict)])

    def apply_planned_adoptions(
        planned: list[FrameStructureAdoption],
        shared_keys: dict[int, set[str]],
        *,
        by_id: dict[int, dict[str, Any]],
        source_children_by_id: dict[int, list[Any]],
    ) -> None:
        if not planned:
            return
        child_to_parent = {item.child_id: item.parent_id for item in planned}
        for adoption in planned:
            parent = by_id.get(adoption.parent_id)
            child = by_id.get(adoption.child_id)
            source_children = source_children_by_id.get(adoption.child_id)
            if parent is None or child is None or source_children is None:
                continue
            parent_children = parent.get("children")
            if not isinstance(parent_children, list):
                parent_children = []
                parent["children"] = parent_children
            if child not in parent_children:
                parent_children.append(child)
            adoptions.append(adoption)
        if demote_unshared_parent_identities:
            for parent_id, keys in shared_keys.items():
                parent = by_id.get(parent_id)
                if parent is None:
                    continue
                demoted = _demote_unshared_parent_identities(parent, shared_shape_keys=keys)
                demoted_identities.extend(demoted)
        for child_id in child_to_parent:
            source_children = source_children_by_id.get(child_id)
            if source_children is None:
                continue
            source_children[:] = [
                child
                for child in source_children
                if not (isinstance(child, dict) and image_number(child) == child_id)
            ]

    def organize_children(children: list[Any], *, allow_adoption: bool) -> list[Any]:
        for child in children:
            if not isinstance(child, dict):
                continue
            grand_children = child.get("children")
            if isinstance(grand_children, list):
                child["children"] = organize_children(grand_children, allow_adoption=child.get("type") != "image")

        dict_children = [child for child in children if isinstance(child, dict)]
        direct_images = [child for child in dict_children if child.get("type") == "image" and image_number(child) is not None]
        if allow_adoption and len(direct_images) >= 2:
            counters["sibling_group_count"] += 1
            planned, shared_keys, scored_count = _plan_sibling_adoptions(
                direct_images,
                score_shape=score_shape,
                threshold=threshold,
                min_shared_anchors=min_shared_anchors,
                require_same_layer=require_same_layer,
            )
            counters["scored_pair_count"] += scored_count
            if planned:
                by_id = {image_number(item): item for item in direct_images}
                source_children_by_id = {
                    int(image_number(item)): children
                    for item in direct_images
                    if image_number(item) is not None
                }
                apply_planned_adoptions(planned, shared_keys, by_id=by_id, source_children_by_id=source_children_by_id)
        return children

    def diagnose_children(children: list[Any]) -> None:
        direct_images = [
            child
            for child in children
            if isinstance(child, dict) and child.get("type") == "image" and image_number(child) is not None
        ]
        if direct_images:
            diagnostics.extend(_diagnose_scene_domain_group(direct_images, score_shape=score_shape, threshold=threshold))
        for child in children:
            if not isinstance(child, dict):
                continue
            grand_children = child.get("children")
            if isinstance(grand_children, list):
                diagnose_children(grand_children)

    count_images([node for node in migrated if isinstance(node, dict)])
    if scope == "layer":
        organize_children(migrated, allow_adoption=True)
        diagnose_children(migrated)
    else:
        organize_children(migrated, allow_adoption=True)
        diagnose_children(migrated)
    counters["adoption_count"] = len(adoptions)
    counters["demoted_identity_count"] = len(demoted_identities)
    counters["diagnostic_count"] = len(diagnostics)
    return migrated, FrameStructureOrganizerStats(
        **counters,
        adoptions=adoptions,
        demoted_identities=demoted_identities,
        layer_updates=[],
        diagnostics=diagnostics,
    )


def _image_data_url(filename: str, *, entry_id: str | None = None) -> str:
    asset = resolve_data_annotation_image_asset(filename, entry_id=entry_id)
    path = asset.path
    if not asset.exists:
        raise FileNotFoundError(f"截图不存在：{path}")
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _runtime_score_shape_factory(tree: list[dict[str, Any]], asset_tree_path: Path, entry_id: str) -> ShapeScoreFunc:
    runner = create_fanxiu_runtime_runner()
    images = runner._index_images(tree)
    frame_cache: dict[int, str] = {}
    score_cache: dict[tuple[int | None, str, int | None], float] = {}
    entry = SimpleNamespace(mode="local", window="mumu", id=entry_id)
    ctx = {"entry": entry, "asset_tree": tree, "asset_tree_path": asset_tree_path, "images": images}

    def frame_for(image: dict[str, Any]) -> str:
        number = image_number(image)
        if number is None:
            raise ValueError(f"无法识别 frame 编号：{image.get('filename')}")
        if number not in frame_cache:
            frame_cache[number] = _image_data_url(str(image.get("filename") or ""), entry_id=entry_id)
        return frame_cache[number]

    def score(parent: dict[str, Any], shape: dict[str, Any], child: dict[str, Any]) -> float:
        cache_key = (image_number(parent), _shape_key(shape), image_number(child))
        if cache_key in score_cache:
            return score_cache[cache_key]
        try:
            frame = frame_for(child)
        except (FileNotFoundError, ValueError):
            score_cache[cache_key] = 0.0
            return 0.0
        value = float(runner._scene_identity_shape_score(ctx, parent, shape, frame) or 0)
        score_cache[cache_key] = value
        return value

    return score


def organize_frame_structure_file(
    path: Path,
    *,
    entry_id: str,
    write: bool = False,
    backup: bool = True,
    scope: str = "layer",
    threshold: float = 80.0,
    min_shared_anchors: int = 1,
    require_same_layer: bool = True,
    demote_unshared_parent_identities: bool = True,
) -> dict[str, Any]:
    tree = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tree, list):
        raise ValueError(f"资产树不是 list：{path}")
    score_shape = _runtime_score_shape_factory(tree, path, entry_id)
    organized, stats = organize_frame_structure_in_tree(
        tree,
        score_shape=score_shape,
        scope=scope,
        threshold=threshold,
        min_shared_anchors=min_shared_anchors,
        require_same_layer=require_same_layer,
        demote_unshared_parent_identities=demote_unshared_parent_identities,
    )
    changed = organized != tree
    backup_path: str | None = None
    if write and changed:
        if backup:
            target = path.with_name(path.name + f".before-frame-structure-{time.strftime('%Y%m%d-%H%M%S')}.bak")
            shutil.copy2(path, target)
            backup_path = str(target)
        path.write_text(json.dumps(organized, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "path": str(path),
        "write": write,
        "changed": changed,
        "backup_path": backup_path,
        "stats": stats.to_dict(),
    }
