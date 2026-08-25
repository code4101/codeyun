from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.data_annotation.storage import (
    DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID,
    data_annotation_asset_tree_path,
    read_data_annotation_asset_tree_snapshot,
    save_data_annotation_asset_tree_snapshot,
)
from scripts.fanxiu_scene423_identity import _ocr_fragments


SCENE_FILENAME = "0672.png"
SOURCE_FRAME = Path(
    tempfile.gettempdir()
) / "codeyun/fanxiu_unknown/20260825/1787657556871_go_scene_34.png"
NEGATIVE_FILENAMES = ("0030.png", "0397.png", "0398.png", "0399.png", "0424.png")


def _walk(nodes: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for node in nodes:
        yield node
        yield from _walk(node.get("children") or [])


def _shape(
    *,
    shape_id: str,
    title: str,
    x: float,
    y: float,
    w: float,
    h: float,
    ocr_text: str = "",
    identity: bool = False,
    ocr_mode: str = "contains",
    description: str = "",
    scene_jump_target: str = "",
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
        "sceneJumpTarget": scene_jump_target,
        "loadDirection": "none",
        "imageMatchRole": "off",
        "pixelTolerance": 5,
        "ocrMatchRole": "required" if identity else "off",
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
        "source": "ocr+visual-model",
        "verificationStatus": "visual-reviewed;action-pending-runtime",
    }


def _scene() -> dict[str, Any]:
    return {
        "id": "image-redpacket-sold-out-20260825",
        "type": "image",
        "title": "首领红包已抢光",
        "description": (
            "点击群聊红包卡片后的业务专属局部结果；只允许日常_红包当前动作事务显式消费，"
            "不得进入通用弹窗、unknown 或跨作业恢复。"
        ),
        "filename": SCENE_FILENAME,
        "width": 900,
        "height": 1600,
        "layer": 0,
        "shapes": [
            _shape(
                shape_id="shape-redpacket-sold-out-title-20260825",
                title="首领累杀奖励",
                x=330 / 900,
                y=684 / 1600,
                w=242 / 900,
                h=67 / 1600,
                ocr_text=r"首领累杀奖[励赏]",
                identity=True,
                ocr_mode="regex",
                description="专属标题身份；兼容真实字体 OCR 的奖励/奖赏差异。",
            ),
            _shape(
                shape_id="shape-redpacket-sold-out-body-20260825",
                title="红包抢光了",
                x=276 / 900,
                y=824 / 1600,
                w=348 / 900,
                h=59 / 1600,
                ocr_text="红包抢光了",
                identity=True,
                description="红包卡片已不可领取的专属正文身份。",
            ),
            _shape(
                shape_id="shape-redpacket-sold-out-details-20260825",
                title="看看大家的手气",
                x=287 / 900,
                y=883 / 1600,
                w=314 / 900,
                h=67 / 1600,
                ocr_text="看看大家的手气",
                description="只读识别；会进入手气详情，当前自动化不得点击。",
            ),
            _shape(
                shape_id="shape-redpacket-sold-out-count-20260825",
                title="当前传音群可领红包",
                x=252 / 900,
                y=1038 / 1600,
                w=420 / 900,
                h=68 / 1600,
                ocr_text=r"当前传音群可领红包[：:]?\s*\d*",
                ocr_mode="regex",
                description="动态数据字段；具体数字不参与 scene 身份。",
            ),
            _shape(
                shape_id="shape-redpacket-sold-out-dismiss-20260825",
                title="弹窗外背景",
                x=28 / 900,
                y=760 / 1600,
                w=140 / 900,
                h=220 / 1600,
                description=(
                    "真实帧中红包卡片外的遮罩背景关闭区；仅日常_红包 sold-out 局部分支使用，"
                    "动作后必须复验 #30。"
                ),
                scene_jump_target="30(1)",
            ),
        ],
        "children": [],
    }


def _redpacket_folder(tree: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [
        node
        for node in _walk(tree)
        if node.get("type") == "folder" and node.get("title") == "红包"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"日常/红包目录数量异常：{len(matches)}")
    return matches[0]


def patch_tree(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = copy.deepcopy(tree)
    folder = _redpacket_folder(result)
    children = [
        node
        for node in folder.get("children") or []
        if node.get("filename") != SCENE_FILENAME
    ]
    children.append(_scene())
    folder["children"] = children
    scene395 = next(
        (node for node in _walk(result) if node.get("filename") == "0395.png"),
        None,
    )
    if scene395 is None:
        raise RuntimeError("缺少 #395 红包世界参考帧")
    chat_shapes = [
        shape for shape in scene395.get("shapes") or [] if shape.get("title") == "聊天"
    ]
    if len(chat_shapes) != 1:
        raise RuntimeError(f"#395[聊天] 数量异常：{len(chat_shapes)}")
    chat = chat_shapes[0]
    chat["imageMatchRole"] = "required"
    chat["pixelTolerance"] = 30
    chat["verificationStatus"] = "source-reference-reviewed;real-action-verified-to-332"
    chat["description"] = (
        "日常_红包进入聊天前的逐帧图像门卫；动作仍只由当前 Job 授权。"
    )
    validate_tree(result)
    return result


def validate_tree(tree: list[dict[str, Any]]) -> None:
    scenes = [node for node in _walk(tree) if node.get("filename") == SCENE_FILENAME]
    if len(scenes) != 1:
        raise RuntimeError(f"#{SCENE_FILENAME[:4]} 唯一路径异常：{len(scenes)}")
    scene = scenes[0]
    if int(scene.get("layer", -1)) != 0:
        raise RuntimeError("红包抢光页必须是业务 Layer0")
    identities = [shape for shape in scene.get("shapes") or [] if shape.get("isSceneIdentity")]
    if len(identities) != 2 or any(shape.get("ocrMatchRole") != "required" for shape in identities):
        raise RuntimeError("红包抢光页必须由两个 required OCR 锚点共同识别")
    count = next(shape for shape in scene["shapes"] if shape["title"] == "当前传音群可领红包")
    if count.get("isSceneIdentity") or count.get("ocrMatchRole") != "off":
        raise RuntimeError("动态可领数量不得参与 scene 身份")
    scene395 = next(node for node in _walk(tree) if node.get("filename") == "0395.png")
    chat = next(shape for shape in scene395.get("shapes") or [] if shape.get("title") == "聊天")
    if chat.get("imageMatchRole") != "required":
        raise RuntimeError("#395[聊天] 必须有逐帧图像门卫")
    popup = next((node for node in tree if node.get("type") == "folder" and node.get("title") == "弹窗"), None)
    if popup and any(node.get("filename") == SCENE_FILENAME for node in _walk(popup.get("children") or [])):
        raise RuntimeError("红包抢光页不得进入全局弹窗目录")


def audit_frames(image_dir: Path, evidence_dir: Path) -> dict[str, Any]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    positive = image_dir / SCENE_FILENAME
    fragments = {name: _ocr_fragments(image_dir / name) for name in (SCENE_FILENAME, *NEGATIVE_FILENAMES)}
    patterns = ("首领累杀奖", "红包抢光了")
    hits = {
        name: {pattern: any(pattern in str(row.get("text") or "") for row in rows) for pattern in patterns}
        for name, rows in fragments.items()
    }
    if not all(hits[SCENE_FILENAME].values()):
        raise RuntimeError(f"#672 正帧双身份未命中：{hits[SCENE_FILENAME]}")
    if any(all(result.values()) for name, result in hits.items() if name != SCENE_FILENAME):
        raise RuntimeError(f"#672 双身份误命中负帧：{hits}")

    frame = Image.open(positive).convert("RGB")
    pass1 = frame.copy()
    draw1 = ImageDraw.Draw(pass1)
    for row in fragments[SCENE_FILENAME]:
        x, y, w, h = (float(row.get(key) or 0) for key in ("x", "y", "w", "h"))
        draw1.rectangle((x, y, x + w, y + h), outline=(0, 170, 255), width=3)
    pass1_path = evidence_dir / "0672-pass1-ocr-boxes.png"
    pass1.save(pass1_path)

    pass2 = frame.copy()
    draw2 = ImageDraw.Draw(pass2)
    for shape in _scene()["shapes"]:
        x, y = shape["x"] * frame.width, shape["y"] * frame.height
        w, h = shape["w"] * frame.width, shape["h"] * frame.height
        color = (0, 220, 80) if shape.get("isSceneIdentity") else (255, 190, 0)
        draw2.rectangle((x, y, x + w, y + h), outline=color, width=4)
    pass2_path = evidence_dir / "0672-pass2-final-roi.png"
    pass2.save(pass2_path)
    report = {"hits": hits, "pass1_overlay": str(pass1_path), "pass2_overlay": str(pass2_path)}
    (evidence_dir / "scene672-ocr-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the red-packet sold-out local result scene")
    parser.add_argument("--entry-id", default=DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID)
    parser.add_argument("--source-frame", type=Path, default=SOURCE_FRAME)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    tree_path = data_annotation_asset_tree_path(args.entry_id)
    snapshot = read_data_annotation_asset_tree_snapshot(tree_path)
    evidence_dir = Path(tempfile.gettempdir()) / "codeyun/redpacket-sold-out"
    image_dir = tree_path.parent / "images"
    candidate_image = evidence_dir / SCENE_FILENAME
    shutil.copy2(args.source_frame, candidate_image)
    if args.apply:
        shutil.copy2(candidate_image, image_dir / SCENE_FILENAME)
    audit_dir = image_dir if args.apply else evidence_dir
    report = audit_frames(audit_dir, evidence_dir)
    patched = patch_tree(snapshot.tree)
    result = {"before_revision": snapshot.revision, "applied": False, **report}
    if args.apply:
        backup = evidence_dir / f"asset-tree.{snapshot.revision[:16]}.before.json"
        saved = save_data_annotation_asset_tree_snapshot(
            tree_path,
            patched,
            entry_id=args.entry_id,
            expected_revision=snapshot.revision,
            before_write=lambda: shutil.copy2(tree_path, backup),
        )
        validate_tree(saved.tree)
        result.update(
            {
                "applied": True,
                "backup": str(backup),
                "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
                "after_revision": saved.revision,
            }
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
