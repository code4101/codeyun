from __future__ import annotations

"""Install the verified Beast-Abyss team detail scene.

The game auto-opens this panel right after ``#862 创建`` succeeds, on top of
``#860``.  The panel has no close control; a dim background tap below the
panel returns to ``#860``.  Source frame is the real 900x1600 failure frame of
ranking-lifecycle attempt 5d07fed8 (2026-10-20).
"""

import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu.behavior_tree.kernel_scheduler import DEFAULT_FANXIU_ENTRY_ID
from backend.core.fanxiu.data_annotation.storage import (
    data_annotation_asset_tree_path,
    update_data_annotation_asset_tree,
)


TARGET_FOLDER_PATH = ("日程", "玩法榜", "兽渊探秘")
SCENE_TITLE = "兽渊团队详情"
SCENE_ID = "image-beast-abyss-team-detail-20261020"
DEFAULT_SOURCE_FRAME = Path(
    r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu_scene_diagnostics\unmatched\20261020"
    r"\1792465016155_816df2fc_wait_click__860__返回__点击前守护.png"
)


def _shape(
    shape_id: str,
    title: str,
    box: tuple[float, float, float, float],
    *,
    description: str,
    ocr_text: str = "",
    identity: bool = False,
    jump_target: str = "",
) -> dict[str, Any]:
    x, y, w, h = box
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
        "pixelTolerance": 5,
        "ocrMatchRole": "required" if ocr_text else "off",
        "ocrEnabled": bool(ocr_text),
        "ocrText": ocr_text,
        "ocrMatchMode": "contains",
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


def build_scene_node(number: int, source_frame: Path) -> dict[str, Any]:
    with Image.open(source_frame) as image:
        width, height = image.size
    if (width, height) != (900, 1600):
        raise RuntimeError(f"兽渊团队详情证据帧尺寸异常：{width}x{height}")

    return {
        "id": SCENE_ID,
        "type": "image",
        "title": SCENE_TITLE,
        "filename": f"{number:04d}.png",
        "width": width,
        "height": height,
        "description": (
            "2026-10-20 真实操作：#862 点击创建成功后，#860 短暂显示已组队提示，随后游戏自动弹出本团队详情。"
            "队名、成员、功勋与排名均为动态内容，不写入身份；面板无关闭按钮，点击面板下方背景返回 #860。"
        ),
        "shapes": [
            _shape(
                "shape-beast-abyss-team-detail-merit-20261020",
                "团队总功勋",
                (0.176, 0.25, 0.2, 0.024),
                description="OCR bbox (164,406)-(374,433)，只框标签文字，避开右侧动态数值。",
                ocr_text="团队总功勋",
                identity=True,
            ),
            _shape(
                "shape-beast-abyss-team-detail-members-20261020",
                "队伍成员",
                (0.15, 0.33, 0.15, 0.024),
                description="OCR bbox (140,533)-(349,561)，只框标签文字，避开右侧动态人数。",
                ocr_text="队伍成员",
                identity=True,
            ),
            _shape(
                "shape-beast-abyss-team-detail-disband-20261020",
                "解散",
                (0.79, 0.836, 0.085, 0.03),
                description="OCR bbox (718,1345)-(783,1380)，队长专属按钮；仅作身份证据，业务禁止点击。",
                ocr_text="解散",
                identity=True,
            ),
            _shape(
                "shape-beast-abyss-team-detail-return-20261020",
                "返回",
                (0.2, 0.92, 0.15, 0.04),
                description=(
                    "面板下方左侧暗色背景，避开底层 #860 左下角返回按钮 (31,1440)-(121,1528)；"
                    "真实点击后回到 #860。"
                ),
                jump_target="860",
            ),
        ],
        "children": [],
    }


def _walk_images(nodes: list[dict[str, Any]]):
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("type") == "image":
            yield node
        yield from _walk_images(node.get("children") or [])


def authoritative_next_scene_number(tree: list[dict[str, Any]]) -> int:
    numbers: list[int] = []
    for node in _walk_images(tree):
        stem = Path(str(node.get("filename") or "")).stem
        if re.fullmatch(r"\d{4}", stem):
            numbers.append(int(stem))
    if not numbers:
        raise RuntimeError("资产树中没有可用于分配场景编号的四位数字图片")
    return max(numbers) + 1


def _folder_at_path(tree: list[dict[str, Any]], path: tuple[str, ...]) -> dict[str, Any]:
    items = tree
    current: dict[str, Any] | None = None
    for title in path:
        current = next(
            (
                item
                for item in items
                if isinstance(item, dict)
                and item.get("type") == "folder"
                and item.get("title") == title
            ),
            None,
        )
        if current is None:
            raise RuntimeError(f"资产树缺少目标目录：{' / '.join(path)}")
        items = current.setdefault("children", [])
    assert current is not None
    return current


def install_scene_into_tree(
    tree: list[dict[str, Any]], source_frame: Path
) -> tuple[bool, dict[str, Any]]:
    existing = next((node for node in _walk_images(tree) if node.get("id") == SCENE_ID), None)
    if existing is not None:
        stem = Path(str(existing.get("filename") or "")).stem
        if not stem.isdigit():
            raise RuntimeError(f"既有兽渊团队详情场景编号无效：{existing.get('filename')}")
        desired = build_scene_node(int(stem), source_frame)
        if existing == desired:
            return False, existing
        existing.clear()
        existing.update(desired)
        return True, existing
    number = authoritative_next_scene_number(tree)
    node = build_scene_node(number, source_frame)
    folder = _folder_at_path(tree, TARGET_FOLDER_PATH)
    folder.setdefault("children", []).append(node)
    return True, node


def main() -> None:
    source_frame = Path(os.environ.get("FANXIU_BEAST_ABYSS_TEAM_DETAIL_FRAME") or DEFAULT_SOURCE_FRAME)
    if not source_frame.is_file():
        raise FileNotFoundError(source_frame)

    tree_path = data_annotation_asset_tree_path(DEFAULT_FANXIU_ENTRY_ID)
    image_root = tree_path.parent / "images"
    result: dict[str, Any] = {}

    def update(tree: list[dict[str, Any]]) -> bool:
        before_bytes = tree_path.read_bytes()
        changed, node = install_scene_into_tree(tree, source_frame)
        result.update(
            changed=changed,
            node=node,
            before_sha256=hashlib.sha256(before_bytes).hexdigest(),
        )
        return changed

    def before_write() -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = tree_path.with_name(tree_path.name + f".before-beast-abyss-team-detail-{stamp}.bak")
        shutil.copy2(tree_path, backup)
        destination = image_root / str(result["node"]["filename"])
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(source_frame.read_bytes()).digest():
                raise FileExistsError(destination)
        else:
            shutil.copy2(source_frame, destination)
        result.update(backup=backup, destination=destination)

    update_data_annotation_asset_tree(tree_path, update, before_write=before_write)
    result["after_sha256"] = hashlib.sha256(tree_path.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "changed": bool(result.get("changed")),
                "scene_id": Path(str(result["node"]["filename"])).stem,
                "title": result["node"]["title"],
                "asset_tree": str(tree_path),
                "source_frame": str(source_frame),
                "installed_image": str(result.get("destination") or ""),
                "backup": str(result.get("backup") or ""),
                "before_sha256": result["before_sha256"],
                "after_sha256": result["after_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
