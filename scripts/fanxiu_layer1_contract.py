from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pyxllib.autogui import image_number

from backend.core.fanxiu.data_annotation.storage import (
    DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID,
    data_annotation_asset_tree_path,
    read_data_annotation_asset_tree_snapshot,
    save_data_annotation_asset_tree_snapshot,
)


LAYER1_SCENE_IDS = frozenset({20, 34, 66, 69, 171})
SCENE_DESTINATIONS: dict[tuple[str, ...], tuple[int, ...]] = {
    ("日常", "仙府", "仙侣居"): (619, 621, 622, 623),
    ("日常", "仙侣历练"): (620,),
    ("日程", "资源榜", "炼丹"): (624, 625, 626, 627, 628, 629),
    ("日程", "仙宴"): (630, 631, 642, 643, 649, 650, 659, 660),
    ("活动", "圣木祈愿"): (644, 645, 646, 647, 648),
}
NEW_FOLDER_IDS = {
    ("日常", "仙府", "仙侣居"): "folder-xianlvju-layer2-20260825",
    ("日程", "资源榜", "炼丹"): "folder-resource-rank-alchemy-20260825",
    ("活动", "圣木祈愿"): "folder-holy-wood-prayer-20260825",
}
FOLLOW_UP_SCENES: dict[int, str] = {}


def _walk(nodes: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for node in nodes:
        yield node
        children = node.get("children")
        if isinstance(children, list):
            yield from _walk(item for item in children if isinstance(item, dict))


def _scene_id(node: dict[str, Any]) -> int | None:
    if node.get("type") != "image":
        return None
    value = image_number(node)
    return int(value) if value is not None else None


def _find_folder(tree: list[dict[str, Any]], path: tuple[str, ...]) -> dict[str, Any] | None:
    nodes = tree
    current: dict[str, Any] | None = None
    for title in path:
        matches = [
            node
            for node in nodes
            if node.get("type") == "folder" and str(node.get("title") or "") == title
        ]
        if len(matches) > 1:
            raise RuntimeError(f"资产目录路径不唯一：{' / '.join(path)}")
        if not matches:
            return None
        current = matches[0]
        children = current.setdefault("children", [])
        if not isinstance(children, list):
            raise RuntimeError(f"资产目录 children 非列表：{' / '.join(path)}")
        nodes = children
    return current


def _ensure_folder(tree: list[dict[str, Any]], path: tuple[str, ...]) -> dict[str, Any]:
    existing = _find_folder(tree, path)
    if existing is not None:
        return existing
    parent = _find_folder(tree, path[:-1])
    if parent is None:
        raise RuntimeError(f"缺少既有父目录：{' / '.join(path[:-1])}")
    folder_id = NEW_FOLDER_IDS.get(path)
    if not folder_id:
        raise RuntimeError(f"未声明新目录稳定 ID：{' / '.join(path)}")
    folder = {
        "id": folder_id,
        "type": "folder",
        "title": path[-1],
        "children": [],
        "filename": "",
    }
    parent["children"].append(folder)
    return folder


def _remove_scene(nodes: list[dict[str, Any]], target: int) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []

    def remove_from(container: list[dict[str, Any]]) -> None:
        kept: list[dict[str, Any]] = []
        for node in container:
            if _scene_id(node) == target:
                matches.append(node)
                continue
            children = node.get("children")
            if isinstance(children, list):
                remove_from(children)
            kept.append(node)
        container[:] = kept

    remove_from(nodes)
    if len(matches) != 1:
        raise RuntimeError(f"场景 #{target} 资产节点数量异常：{len(matches)}")
    return matches[0]


def _shape(node: dict[str, Any], title: str) -> dict[str, Any]:
    matches = [shape for shape in node.get("shapes") or [] if shape.get("title") == title]
    if len(matches) != 1:
        raise RuntimeError(f"#{_scene_id(node)}[{title}] 数量异常：{len(matches)}")
    return matches[0]


def _without_jump_target(value: Any, target: int) -> str:
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    kept = [part for part in parts if int(re.match(r"\d+", part).group()) != int(target)]
    return ",".join(kept)


def validate_layer1_contract(tree: list[dict[str, Any]]) -> dict[str, Any]:
    nodes = list(_walk(tree))
    stable_ids = [str(node.get("id") or "") for node in nodes]
    duplicate_node_ids = sorted({value for value in stable_ids if value and stable_ids.count(value) > 1})
    if duplicate_node_ids:
        raise RuntimeError(f"资产节点 ID 重复：{duplicate_node_ids[:10]}")

    scene_ids = [value for node in nodes if (value := _scene_id(node)) is not None]
    duplicate_scene_ids = sorted({value for value in scene_ids if scene_ids.count(value) > 1})
    if duplicate_scene_ids:
        raise RuntimeError(f"场景 ID 重复：{duplicate_scene_ids[:10]}")

    layer1 = {
        int(scene_id)
        for node in nodes
        if (scene_id := _scene_id(node)) is not None and int(node.get("layer") or 2) == 1
    }
    if layer1 != LAYER1_SCENE_IDS:
        raise RuntimeError(
            f"Layer1 必须精确为 {sorted(LAYER1_SCENE_IDS)}，当前={sorted(layer1)}"
        )

    paths: dict[int, str] = {}

    def collect_paths(items: list[dict[str, Any]], prefix: tuple[str, ...]) -> None:
        for item in items:
            title = str(item.get("title") or "")
            current = (*prefix, title) if title else prefix
            scene_id = _scene_id(item)
            if scene_id is not None:
                paths[scene_id] = " / ".join(current)
            children = item.get("children")
            if isinstance(children, list):
                collect_paths(children, current)

    collect_paths(tree, ())
    for path, expected_ids in SCENE_DESTINATIONS.items():
        folder = _find_folder(tree, path)
        if folder is None:
            raise RuntimeError(f"迁移后缺少目录：{' / '.join(path)}")
        direct_ids = {_scene_id(node) for node in folder.get("children") or []}
        if not set(expected_ids).issubset(direct_ids):
            raise RuntimeError(
                f"目录 {' / '.join(path)} 缺场景：{sorted(set(expected_ids) - direct_ids)}"
            )
        for scene_id in expected_ids:
            node = next(node for node in nodes if _scene_id(node) == scene_id)
            if int(node.get("layer") or 0) != 2:
                raise RuntimeError(f"业务场景 #{scene_id} 未显式设置 Layer2")

    return {
        "layer1": sorted(layer1),
        "migrated_paths": {str(scene_id): paths[scene_id] for ids in SCENE_DESTINATIONS.values() for scene_id in ids},
        "follow_up": FOLLOW_UP_SCENES,
    }


def refactor_layer1_tree(tree: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result = copy.deepcopy(tree)
    before_nodes = list(_walk(result))
    before_by_scene = {_scene_id(node): copy.deepcopy(node) for node in before_nodes if _scene_id(node) is not None}
    xianyan_landing_before = _shape(before_by_scene[20], "仙园游宴").get("sceneJumpTarget")

    for path, scene_ids in SCENE_DESTINATIONS.items():
        target = _ensure_folder(result, path)
        children = target.setdefault("children", [])
        for scene_id in scene_ids:
            node = _remove_scene(result, scene_id)
            node["layer"] = 2
            children.append(node)

    current = {_scene_id(node): node for node in _walk(result) if _scene_id(node) is not None}
    _shape(current[340], "返回")["sceneJumpTarget"] = _without_jump_target(
        _shape(current[340], "返回").get("sceneJumpTarget"), 631
    )
    _shape(current[269], "返回")["sceneJumpTarget"] = _without_jump_target(
        _shape(current[269], "返回").get("sceneJumpTarget"), 645
    )
    if _shape(current[20], "仙园游宴").get("sceneJumpTarget") != xianyan_landing_before:
        raise RuntimeError("#20[仙园游宴]动态真实落点不得随目录迁移改变")

    for scene_ids in SCENE_DESTINATIONS.values():
        for scene_id in scene_ids:
            before = before_by_scene[scene_id]
            after = current[scene_id]
            for key in ("id", "filename", "shapes", "parentSceneIds"):
                if before.get(key) != after.get(key):
                    raise RuntimeError(f"目录迁移意外改变 #{scene_id}.{key}")

    report = validate_layer1_contract(result)
    report["removed_dirty_jump_targets"] = ["#340[返回]->#631", "#269[返回]->#645"]
    return result, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Refactor the Fanxiu Layer1 asset contract")
    parser.add_argument("--entry-id", default=DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    path = data_annotation_asset_tree_path(args.entry_id)
    snapshot = read_data_annotation_asset_tree_snapshot(path)
    tree, report = refactor_layer1_tree(snapshot.tree)
    report.update({"path": str(path), "before_revision": snapshot.revision, "applied": False})
    if args.apply:
        backup_dir = Path(tempfile.gettempdir()) / "codeyun" / "fanxiu-layer1-refactor"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"asset-tree.{snapshot.revision[:16]}.before.json"

        def backup_before_write() -> None:
            shutil.copy2(path, backup)

        saved = save_data_annotation_asset_tree_snapshot(
            path,
            tree,
            entry_id=args.entry_id,
            expected_revision=snapshot.revision,
            before_write=backup_before_write,
        )
        report.update(
            {
                "applied": True,
                "backup": str(backup),
                "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
                "after_revision": saved.revision,
            }
        )
        validate_layer1_contract(saved.tree)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
