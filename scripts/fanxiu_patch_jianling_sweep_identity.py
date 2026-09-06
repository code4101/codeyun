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
SCENE_ID = 191
SCENE_NODE_ID = "image-jianling-191-20260611-154700"
IDENTITY_SHAPE_ID = "shape-jianling-191-id"
CONFIRM_SHAPE_ID = "shape-jianling-191-confirm"
FAILURE_FRAME = Path(
    r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu_scene_diagnostics\scene_repair"
    r"\20260907\1788729503980_22b3fa28_场景识别连续处理弹窗超过上限__191_____191_____191_____191_____.png"
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


def _find_shape(scene: dict[str, Any], shape_id: str) -> dict[str, Any] | None:
    pending = list(scene.get("shapes") or [])
    while pending:
        shape = pending.pop()
        if not isinstance(shape, dict):
            continue
        if str(shape.get("id") or "") == shape_id:
            return shape
        pending.extend(shape.get("children") or [])
    return None


def main() -> None:
    tree_path = data_annotation_asset_tree_path(ENTRY_ID).resolve()
    if tree_path != EXPECTED_TREE.resolve():
        raise RuntimeError(f"资产树路径与第一现场不一致：{tree_path}")
    if not FAILURE_FRAME.is_file():
        raise FileNotFoundError(f"第一现场不存在：{FAILURE_FRAME}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = codeyun_temp_root("fanxiu-asset-backups", f"jianling-sweep-identity-{stamp}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    before_sha = hashlib.sha256(tree_path.read_bytes()).hexdigest()
    changed = {"value": False}

    def update(tree: list[dict[str, Any]]) -> bool:
        scene = _find_node(tree, SCENE_NODE_ID)
        if scene is None or str(scene.get("filename") or "") != f"{SCENE_ID:04d}.png":
            raise RuntimeError(f"#{SCENE_ID} 资产身份已变化，拒绝覆盖")
        identity = _find_shape(scene, IDENTITY_SHAPE_ID)
        confirm = _find_shape(scene, CONFIRM_SHAPE_ID)
        if identity is None or confirm is None:
            raise RuntimeError(f"#{SCENE_ID} 缺少既有身份或确认 Shape，拒绝部分更新")

        desired = {
            IDENTITY_SHAPE_ID: {
                "title": "扫荡奖励说明",
                "description": (
                    "#191 弹窗专属正文身份。原规则‘剑阁|淬剑|剑灵|1200层’会把日常页"
                    "‘挑战或扫荡淬剑试炼’误判为本弹窗；2026-09-07 原失败帧回放："
                    "旧规则 100%，本规则 0%，#191 参考帧 100%。"
                ),
                "ocrText": "昨日最高挑战|灵塔奖励",
                "ocrMatchMode": "regex",
                "ocrEnabled": True,
                "ocrMatchRole": "required",
                "isSceneIdentity": True,
                "sceneIdentityRole": "required",
            },
            CONFIRM_SHAPE_ID: {
                "description": (
                    "#191 第二个独立身份锚点兼动作入口；与扫荡奖励正文共同成立后才允许识别和点击。"
                    "2026-09-07 原失败帧回放 0%，#191 参考帧 100%。"
                ),
                "ocrText": "进行扫荡",
                "ocrMatchMode": "contains",
                "ocrEnabled": True,
                "ocrMatchRole": "required",
                "isSceneIdentity": True,
                "sceneIdentityRole": "required",
            },
        }
        for shape, fields in ((identity, desired[IDENTITY_SHAPE_ID]), (confirm, desired[CONFIRM_SHAPE_ID])):
            for key, value in fields.items():
                if shape.get(key) != value:
                    shape[key] = value
                    changed["value"] = True
        return changed["value"]

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
