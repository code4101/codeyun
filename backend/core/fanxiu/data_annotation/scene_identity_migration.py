from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pyxllib.autogui import View, normalize_scene_identity_level, normalize_scene_identity_scope


@dataclass(frozen=True)
class SceneIdentityMigrationStats:
    image_count: int = 0
    added_level_count: int = 0
    preserved_level_count: int = 0
    level0_count: int = 0
    level1_count: int = 0
    level2_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _legacy_frame_scene_identity_level(image: dict[str, Any]) -> int:
    level = 0
    for shape in View(image).get_shapes(include_groups=False):
        scope = normalize_scene_identity_scope(shape.raw.get("sceneIdentityScope"), "")
        if not scope and shape.is_scene_identity:
            scope = "local"
        if scope == "global":
            level = max(level, 2)
        elif scope == "local":
            level = max(level, 1)
    return level


def migrate_scene_identity_levels_in_tree(
    tree: list[dict[str, Any]],
    *,
    overwrite: bool = False,
) -> tuple[list[dict[str, Any]], SceneIdentityMigrationStats]:
    """给资产树 image 节点补 `sceneIdentityLevel`，不修改 shape 证据信息。

    迁移规则：
    - 已有 frame.sceneIdentityLevel 默认保留；
    - 缺失时用旧 shape.sceneIdentityScope 推导，global -> 2，local/isSceneIdentity -> 1；
    - 没有场景证据的 image 显式写成 0，便于“非场景帧”投影查看。
    """

    counters = {
        "image_count": 0,
        "added_level_count": 0,
        "preserved_level_count": 0,
        "level0_count": 0,
        "level1_count": 0,
        "level2_count": 0,
    }

    def migrate_node(node: dict[str, Any]) -> dict[str, Any]:
        result = dict(node)
        children = result.get("children")
        if isinstance(children, list):
            result["children"] = [migrate_node(child) if isinstance(child, dict) else child for child in children]
        if result.get("type") != "image":
            return result

        counters["image_count"] += 1
        if not overwrite and "sceneIdentityLevel" in result:
            level = normalize_scene_identity_level(result.get("sceneIdentityLevel"), 0)
            result["sceneIdentityLevel"] = level
            counters["preserved_level_count"] += 1
        else:
            level = _legacy_frame_scene_identity_level(result)
            result["sceneIdentityLevel"] = level
            counters["added_level_count"] += 1

        if level <= 0:
            counters["level0_count"] += 1
        elif level == 1:
            counters["level1_count"] += 1
        else:
            counters["level2_count"] += 1
        return result

    migrated = [migrate_node(node) if isinstance(node, dict) else node for node in tree]
    return migrated, SceneIdentityMigrationStats(**counters)


def migrate_scene_identity_levels_file(
    path: Path,
    *,
    write: bool = False,
    overwrite: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    tree = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tree, list):
        raise ValueError(f"资产树不是 list：{path}")
    migrated, stats = migrate_scene_identity_levels_in_tree(tree, overwrite=overwrite)
    changed = migrated != tree
    backup_path: str | None = None
    if write and changed:
        if backup:
            target = path.with_name(path.name + f".before-scene-identity-level-{time.strftime('%Y%m%d-%H%M%S')}.bak")
            shutil.copy2(path, target)
            backup_path = str(target)
        path.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "path": str(path),
        "write": write,
        "changed": changed,
        "backup_path": backup_path,
        "stats": stats.to_dict(),
    }
