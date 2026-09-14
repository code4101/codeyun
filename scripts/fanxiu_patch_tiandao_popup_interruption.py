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
SCENE_ID = 74
SCENE_NODE_ID = "image-1780259600000-tiandao-lead"
CLOSE_SHAPE_ID = "shape-1780259600000-tiandao-blank-close"
CLOSE_SHAPE_TITLE = "空白"
FAILURE_FRAME = Path(
    r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu_scene_diagnostics\scene_repair"
    r"\20260914\1789333210154_c2aa6611_场景识别命中弹窗__74_但该节点没有可执行的中断处理动作.png"
)
COMPARISON_FRAME = Path(
    r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu_scene_diagnostics\comparison"
    r"\20260914\1789333507659_f1ecbb23_scene_74.png"
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


def main() -> None:
    tree_path = data_annotation_asset_tree_path(ENTRY_ID).resolve()
    if tree_path != EXPECTED_TREE.resolve():
        raise RuntimeError(f"资产树路径与第一现场不一致：{tree_path}")
    if not FAILURE_FRAME.is_file() or not COMPARISON_FRAME.is_file():
        raise FileNotFoundError("缺少 #74 第一现场或正式标准对比图")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = codeyun_temp_root("fanxiu-asset-backups", f"tiandao-popup-{stamp}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    before_sha = hashlib.sha256(tree_path.read_bytes()).hexdigest()
    changed = {"value": False}

    def update(tree: list[dict[str, Any]]) -> bool:
        scene = _find_node(tree, SCENE_NODE_ID)
        if scene is None or str(scene.get("filename") or "") != f"{SCENE_ID:04d}.png":
            raise RuntimeError(f"#{SCENE_ID} 资产身份已变化，拒绝覆盖")
        close_shape = _find_node(scene.get("shapes") or [], CLOSE_SHAPE_ID)
        if close_shape is None or str(close_shape.get("title") or "") != CLOSE_SHAPE_TITLE:
            raise RuntimeError(f"#{SCENE_ID} 缺少已验证的‘空白’关闭 Shape，拒绝猜测动作")

        fields = {
            "description": (
                "全局天道魁首活动引导弹窗。2026-09-14 第一现场与 #74 标准双图确认身份一致；"
                "非业务主动进入时点击已验证的左下空白关闭区，不点击‘立即前往’。"
            ),
            "behaviorTreeInterruptionAction": CLOSE_SHAPE_TITLE,
        }
        for key, value in fields.items():
            if scene.get(key) != value:
                scene[key] = value
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
                "interruption_action": CLOSE_SHAPE_TITLE,
                "before_sha256": before_sha,
                "after_sha256": final.revision,
                "backup_dir": str(backup_dir),
                "failure_frame": str(FAILURE_FRAME),
                "comparison_frame": str(COMPARISON_FRAME),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
