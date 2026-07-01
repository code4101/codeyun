from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pyxllib.autogui import View, normalize_frame_layer, normalize_scene_identity_scope


@dataclass(frozen=True)
class FrameLayerMigrationStats:
    image_count: int = 0
    added_layer_count: int = 0
    preserved_layer_count: int = 0
    removed_legacy_level_count: int = 0
    layer1_count: int = 0
    layer2_count: int = 0
    layer3_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _legacy_primary_scene_marker(image: dict[str, Any]) -> bool:
    legacy_level = image.get("sceneIdentityLevel")
    if legacy_level == 2 or str(legacy_level).strip() == "2":
        return True

    for shape in View(image).get_shapes(include_groups=False):
        scope = normalize_scene_identity_scope(shape.raw.get("sceneIdentityScope"), "")
        if not scope and shape.is_scene_identity:
            scope = "local"
        if scope == "global":
            return True
    return False


def migrate_frame_layers_in_tree(
    tree: list[dict[str, Any]],
    *,
    overwrite: bool = False,
) -> tuple[list[dict[str, Any]], FrameLayerMigrationStats]:
    """清理旧识别层字段，仅保留显式 layer1 主场景标记。"""

    counters = {
        "image_count": 0,
        "added_layer_count": 0,
        "preserved_layer_count": 0,
        "removed_legacy_level_count": 0,
        "layer1_count": 0,
        "layer2_count": 0,
        "layer3_count": 0,
    }

    def migrate_node(node: dict[str, Any]) -> dict[str, Any]:
        result = dict(node)
        children = result.get("children")
        if isinstance(children, list):
            result["children"] = [migrate_node(child) if isinstance(child, dict) else child for child in children]
        if result.get("type") != "image":
            return result

        counters["image_count"] += 1
        if "sceneIdentityLevel" in result:
            counters["removed_legacy_level_count"] += 1
        if not overwrite and normalize_frame_layer(result.get("layer"), 3) == 1:
            layer = 1
            counters["preserved_layer_count"] += 1
            result["layer"] = 1
        else:
            layer = 1 if _legacy_primary_scene_marker(result) else 3
            if layer == 1:
                result["layer"] = 1
                counters["added_layer_count"] += 1
            else:
                result.pop("layer", None)
        result.pop("sceneIdentityLevel", None)
        counters[f"layer{layer}_count"] += 1
        return result

    migrated = [migrate_node(node) if isinstance(node, dict) else node for node in tree]
    return migrated, FrameLayerMigrationStats(**counters)


def migrate_frame_layers_file(
    path: Path,
    *,
    write: bool = False,
    overwrite: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    tree = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tree, list):
        raise ValueError(f"资产树不是 list：{path}")
    migrated, stats = migrate_frame_layers_in_tree(tree, overwrite=overwrite)
    changed = migrated != tree
    backup_path: str | None = None
    if write and changed:
        if backup:
            target = path.with_name(path.name + f".before-frame-layer-{time.strftime('%Y%m%d-%H%M%S')}.bak")
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
