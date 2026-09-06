from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

XLPROJECT_SRC = ROOT.parent / "xlproject" / "src"
if str(XLPROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(XLPROJECT_SRC))
import xlproject.loadenv  # noqa: F401,E402  # shared deployment environment contract

from backend.core.fanxiu.data_annotation.storage import (  # noqa: E402
    data_annotation_asset_tree_path,
    update_data_annotation_asset_tree,
)
from backend.core.temp_paths import codeyun_temp_root  # noqa: E402


ENTRY_ID = "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"
SCENE_ID = 336
SCENE_NODE_ID = "image-1783053043337-b17af86b3eaaa"
RETURN_SHAPE_ID = "shape-daily-boss-336-return-20260907"
FAILURE_FRAME = Path(
    r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu_scene_diagnostics\scene_repair"
    r"\20260907\1788731136775_395e227a_go_scene_34__失败_无法从当前_336找到可达_34的安全路径_"
    r"已失败动作_0_个_.png"
)
EXPECTED_TREE = Path(
    r"C:\home\chenkunze\data\m2603codeyun\codepc_mf\fanxiu\data-annotation\entries"
    rf"\{ENTRY_ID}\asset-tree.json"
)


def _find_node(nodes: list[dict[str, Any]], node_id: str) -> dict[str, Any] | None:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("id") or "") == node_id:
            return node
        children = node.get("children")
        if isinstance(children, list):
            found = _find_node(children, node_id)
            if found is not None:
                return found
    return None


def _return_shape() -> dict[str, Any]:
    return {
        "id": RETURN_SHAPE_ID,
        "kind": "shape",
        "title": "返回",
        "description": (
            "#336 首领列表左下明确返回控件；2026-09-07 原失败帧与 #336 参考帧布局一致，"
            "几何复用同构 #178 首领列表已验证的返回 Shape，落点为世界 #34。"
        ),
        "locked": False,
        "floating": False,
        "jitterEnabled": False,
        "jitterRadius": 4,
        "isSceneIdentity": False,
        "sceneIdentityRole": "off",
        "sceneJumpTarget": "34",
        "loadDirection": "none",
        "imageMatchRole": "off",
        "pixelTolerance": 5,
        "ocrMatchRole": "off",
        "ocrEnabled": False,
        "ocrText": "",
        "ocrMatchMode": "contains",
        "ocrMinConfidence": 0,
        "ocrMaskMode": "inherit-envelope",
        "ocrMask": None,
        "maskEnabled": False,
        "alphaMask": None,
        "toleranceEnabled": False,
        "toleranceRange": None,
        "discriminatorEnabled": False,
        "discriminator": None,
        "discriminatorGroupId": None,
        "discriminatorValue": "",
        "x": 0.04425925925925926,
        "y": 0.904375,
        "w": 0.08203703703703703,
        "h": 0.0425,
        "children": [],
    }


def main() -> None:
    tree_path = data_annotation_asset_tree_path(ENTRY_ID).resolve()
    if tree_path != EXPECTED_TREE.resolve():
        raise RuntimeError(f"资产树路径与第一现场不一致：{tree_path}")
    if not FAILURE_FRAME.is_file():
        raise FileNotFoundError(f"第一现场不存在：{FAILURE_FRAME}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = codeyun_temp_root("fanxiu-asset-backups", f"boss-list-return-{stamp}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    before_sha = hashlib.sha256(tree_path.read_bytes()).hexdigest()
    changed = {"value": False}

    def update(tree: list[dict[str, Any]]) -> bool:
        scene = _find_node(tree, SCENE_NODE_ID)
        if scene is None or str(scene.get("filename") or "") != f"{SCENE_ID:04d}.png":
            raise RuntimeError(f"#{SCENE_ID} 资产身份已变化，拒绝覆盖")
        shapes = scene.get("shapes")
        if not isinstance(shapes, list):
            raise RuntimeError(f"#{SCENE_ID} shapes 结构无效")
        desired = _return_shape()
        existing = next(
            (shape for shape in shapes if isinstance(shape, dict) and str(shape.get("id") or "") == RETURN_SHAPE_ID),
            None,
        )
        if existing == desired:
            return False
        if existing is None:
            shapes.append(desired)
        else:
            existing.clear()
            existing.update(desired)
        changed["value"] = True
        return True

    final = update_data_annotation_asset_tree(
        tree_path,
        update,
        before_write=lambda: shutil.copy2(tree_path, backup_dir / "asset-tree.before.json"),
    )
    print(
        json.dumps(
            {
                "changed": changed["value"],
                "scene_id": SCENE_ID,
                "before_sha256": before_sha,
                "after_sha256": final.revision,
                "backup_dir": str(backup_dir),
                "failure_frame": str(FAILURE_FRAME),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
