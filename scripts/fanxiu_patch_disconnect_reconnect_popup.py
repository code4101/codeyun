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
    read_data_annotation_asset_tree_snapshot,
    resolve_data_annotation_scene_node_id,
    save_data_annotation_frame_tree_node,
    update_data_annotation_asset_tree,
)


ENTRY_ID = "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"
FAILURE_FRAME = Path(
    r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu_scene_diagnostics\scene_repair"
    r"\20260907\1788726508452_cbccb6f4_go_scene_34__失败_无法从当前_327找到可达_34的安全路径_"
    r"已失败动作_0_个_.png"
)
EXPECTED_TREE = Path(
    r"C:\home\chenkunze\data\m2603codeyun\codepc_mf\fanxiu\data-annotation\entries"
    rf"\{ENTRY_ID}\asset-tree.json"
)
DISCONNECT_NODE_ID = "image-network-disconnect-reconnect-20260907"


def _shape(
    *,
    shape_id: str,
    title: str,
    description: str,
    x: float,
    y: float,
    w: float,
    h: float,
    identity: bool = False,
    ocr_text: str = "",
    ocr_mode: str = "contains",
    jump_target: str = "",
) -> dict[str, Any]:
    return {
        "id": shape_id,
        "kind": "shape",
        "title": title,
        "description": description,
        "locked": False,
        "floating": False,
        "jitterEnabled": False,
        "jitterRadius": 4,
        "isSceneIdentity": identity,
        "sceneIdentityRole": "required" if identity else "off",
        "sceneJumpTarget": jump_target,
        "loadDirection": "none",
        "imageMatchRole": "off",
        "pixelTolerance": 20,
        "ocrMatchRole": "required" if ocr_text else "off",
        "ocrEnabled": bool(ocr_text),
        "ocrText": ocr_text,
        "ocrMatchMode": ocr_mode,
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
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "children": [],
    }


def _find_image(nodes: list[dict[str, Any]], *, number: int | None = None, node_id: str = "") -> dict[str, Any] | None:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        filename = str(node.get("filename") or "")
        stem = Path(filename).stem
        scene_number = int(stem) if stem.isdigit() else None
        if node.get("type") == "image" and (
            (number is not None and scene_number == number)
            or (node_id and str(node.get("id") or "") == node_id)
        ):
            return node
        children = node.get("children")
        if isinstance(children, list):
            found = _find_image(children, number=number, node_id=node_id)
            if found is not None:
                return found
    return None


def _disconnect_shapes() -> list[dict[str, Any]]:
    evidence = str(FAILURE_FRAME)
    return [
        _shape(
            shape_id="shape-network-disconnect-message-20260907",
            title="当前网络已断开",
            description=f"原失败帧 PaddleOCR bbox=(260,724,226,31)，约 1.1 倍外扩；evidence={evidence}",
            x=0.275,
            y=0.447,
            w=0.275,
            h=0.027,
            identity=True,
            ocr_text="当前网络已断开",
        ),
        _shape(
            shape_id="shape-network-disconnect-login-hint-20260907",
            title="请重新登录",
            description=f"同一正文的第二个专属 OCR 锚点，bbox=(520,724,164,31)；evidence={evidence}",
            x=0.565,
            y=0.447,
            w=0.205,
            h=0.027,
            identity=True,
            ocr_text="请重新登录",
        ),
        _shape(
            shape_id="shape-network-disconnect-reconnect-20260907",
            title="重连",
            description="无资源副作用的原会话网络恢复动作；仅由本弹窗的显式中断映射授权。",
            x=0.62,
            y=0.638,
            w=0.13,
            h=0.055,
            ocr_text="重连",
            jump_target="-1",
        ),
        _shape(
            shape_id="shape-network-disconnect-relogin-20260907",
            title="重新登录",
            description="会替换当前登录流程，不得自动点击；仅标明控件语义并保留现场。",
            x=0.245,
            y=0.638,
            w=0.19,
            h=0.055,
            ocr_text="重新登录",
        ),
    ]


def _scene_327_shapes(scene: dict[str, Any]) -> list[dict[str, Any]]:
    shapes = [shape for shape in scene.get("shapes") or [] if isinstance(shape, dict)]
    challenge = next(
        (shape for shape in shapes if str(shape.get("id") or "") == "shape-1782832404673-c89ce9a0f4cba"),
        None,
    )
    if challenge is None:
        raise RuntimeError("#327 缺少原始‘挑战’ Shape，拒绝在未知资产上覆盖")
    challenge.update(
        _shape(
            shape_id="shape-1782832404673-c89ce9a0f4cba",
            title="挑战",
            description="‘仍要挑战’动作；原单字小图身份会把断线弹窗的‘重新登录’误判为 #327，现仅作 OCR 动作。",
            x=0.20,
            y=0.638,
            w=0.28,
            h=0.055,
            ocr_text="仍要挑战",
        )
    )
    retained = [
        shape
        for shape in shapes
        if str(shape.get("id") or "")
        not in {
            "shape-scene-327-round-reward-20260907",
            "shape-scene-327-team-question-20260907",
        }
    ]
    retained.extend(
        [
            _shape(
                shape_id="shape-scene-327-round-reward-20260907",
                title="挑战轮次奖励说明",
                description="#327 参考帧稳定正文，PaddleOCR bbox=(227,683,470,27)，避开动态属性飘字。",
                x=0.245,
                y=0.422,
                w=0.54,
                h=0.029,
                identity=True,
                ocr_text="挑战轮次越多",
            ),
            _shape(
                shape_id="shape-scene-327-team-question-20260907",
                title="是否组队提示",
                description="#327 专属问题正文，PaddleOCR bbox=(342,768,243,31)，与奖励说明共同构成身份。",
                x=0.365,
                y=0.474,
                w=0.30,
                h=0.027,
                identity=True,
                ocr_text="是否要进行组队",
            ),
        ]
    )
    return retained


def main() -> None:
    tree_path = data_annotation_asset_tree_path(ENTRY_ID).resolve()
    if tree_path != EXPECTED_TREE.resolve():
        raise RuntimeError(f"资产树路径与第一现场不一致：{tree_path}")
    if not FAILURE_FRAME.is_file():
        raise FileNotFoundError(f"第一现场不存在：{FAILURE_FRAME}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = (
        Path.home()
        / "AppData"
        / "Local"
        / "Temp"
        / "codeyun"
        / "fanxiu-asset-backups"
        / f"disconnect-reconnect-{stamp}"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)
    before_sha = hashlib.sha256(tree_path.read_bytes()).hexdigest()

    snapshot = read_data_annotation_asset_tree_snapshot(tree_path)
    created = _find_image(snapshot.tree, node_id=DISCONNECT_NODE_ID) is None
    if created:
        anchor_id = resolve_data_annotation_scene_node_id(snapshot.tree, 47)
        save_data_annotation_frame_tree_node(
            tree_path,
            FAILURE_FRAME.read_bytes(),
            {
                "id": DISCONNECT_NODE_ID,
                "type": "image",
                "title": "断线重连",
                "description": "全局网络断线中断；优先重连原会话，不自动执行重新登录。",
                "width": 900,
                "height": 1600,
                "behaviorTreeInterruptionAction": "重连",
                "shapes": _disconnect_shapes(),
                "children": [],
                "evidenceFrame": str(FAILURE_FRAME),
            },
            entry_id=ENTRY_ID,
            after_node_id=anchor_id,
            expected_revision=snapshot.revision,
            before_write=lambda: shutil.copy2(tree_path, backup_dir / "asset-tree.before-create.json"),
        )

    changed = {"value": False}

    def update(tree: list[dict[str, Any]]) -> bool:
        scene_327 = _find_image(tree, number=327)
        disconnect = _find_image(tree, node_id=DISCONNECT_NODE_ID)
        if scene_327 is None or disconnect is None:
            raise RuntimeError("缺少 #327 或断线重连节点，拒绝部分更新")

        desired_327 = _scene_327_shapes(scene_327)
        desired_disconnect = _disconnect_shapes()
        node_changes = {
            "title": "断线重连",
            "description": "全局网络断线中断；优先重连原会话，不自动执行重新登录。",
            "behaviorTreeInterruptionAction": "重连",
            "shapes": desired_disconnect,
            "evidenceFrame": str(FAILURE_FRAME),
        }
        if scene_327.get("title") != "悟道试炼组队提示":
            scene_327["title"] = "悟道试炼组队提示"
            changed["value"] = True
        if scene_327.get("shapes") != desired_327:
            scene_327["shapes"] = desired_327
            changed["value"] = True
        for key, value in node_changes.items():
            if disconnect.get(key) != value:
                disconnect[key] = value
                changed["value"] = True
        return changed["value"]

    final = update_data_annotation_asset_tree(
        tree_path,
        update,
        before_write=lambda: shutil.copy2(tree_path, backup_dir / "asset-tree.before-update.json"),
    )
    disconnect = _find_image(final.tree, node_id=DISCONNECT_NODE_ID)
    assert disconnect is not None
    print(
        json.dumps(
            {
                "created": created,
                "changed": changed["value"],
                "disconnect_scene": int(Path(str(disconnect["filename"])).stem),
                "before_sha256": before_sha,
                "after_sha256": final.revision,
                "backup_dir": str(backup_dir),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
