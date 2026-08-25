from __future__ import annotations

"""Install the verified Dongtian friendly-attendant detail scene.

The source is a real 900x1600 Runtime evidence frame.  This installer only
creates visual facts.  In particular, the ``互换采气`` shape deliberately has
no navigation target and must never be treated as action authorization.
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

from backend.core.fanxiu.behavior_tree.runtime import DEFAULT_FANXIU_ENTRY_ID
from backend.core.fanxiu.data_annotation.storage import (
    data_annotation_asset_tree_path,
    update_data_annotation_asset_tree,
)


TARGET_FOLDER_PATH = ("日常", "洞天")
SCENE_TITLE = "友军侍从详情（互换采气）"
SCENE_ID = "image-dongtian-friendly-attendant-swap-20260819"
DEFAULT_SOURCE_FRAME = Path(
    r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu_unknown\20260819"
    r"\1787146329344_洞天_上座研究_等待侍从详情.png"
)


def _shape(
    shape_id: str,
    title: str,
    box: tuple[float, float, float, float],
    *,
    description: str,
    ocr_text: str = "",
    match_mode: str = "contains",
    identity: bool = False,
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
        "sceneJumpTarget": "",
        "loadDirection": "none",
        "imageMatchRole": "off",
        "pixelTolerance": 5,
        "ocrMatchRole": "required" if ocr_text else "off",
        "ocrEnabled": bool(ocr_text),
        "ocrText": ocr_text,
        "ocrMatchMode": match_mode,
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
        raise RuntimeError(f"洞天侍从详情证据帧尺寸异常：{width}x{height}")

    return {
        "id": SCENE_ID,
        "type": "image",
        "title": SCENE_TITLE,
        "filename": f"{number:04d}.png",
        "width": width,
        "height": height,
        "description": (
            "2026-08-19 真实洞天友军侍从详情页：Runtime 对齐 mine=5、quality=2、seat=5、"
            "role_id=24082053427366396、fight_score=65724906006937。地点、玩家和战力均为动态内容，"
            "不写入身份。互换采气会改变双方座位，属于高风险动作；本场景只提供可定位 Shape，"
            "在策略和二次 Runtime 授权完成前禁止点击。"
        ),
        "shapes": [
            _shape(
                "shape-dongtian-friendly-attendant-own-power-20260819",
                "我方当前战力",
                (0.145, 0.292, 0.29, 0.035),
                description=(
                    "真实 OCR bbox 并集约 (147,477)-(373,507)，外扩后避开右侧动态战力值；"
                    "与互换采气共同证明友军侍从详情页。"
                ),
                ocr_text="我方当前战力",
                match_mode="contains",
                identity=True,
            ),
            _shape(
                "shape-dongtian-friendly-attendant-return-20260819",
                "返回",
                (0.258, 0.821, 0.134, 0.03),
                description=(
                    "真实 OCR bbox 并集约 (238,1316)-(347,1358)，中心位于左侧返回按钮安全区；"
                    "尚未真实点击，因此不预填 sceneJumpTarget。"
                ),
                ocr_text="返回",
                match_mode="exact",
            ),
            _shape(
                "shape-dongtian-friendly-attendant-swap-20260819",
                "互换采气",
                (0.61, 0.82, 0.21, 0.035),
                description=(
                    "高风险动作：会与当前友军侍从互换座位。真实 OCR 将末字“气”稳定误识为“无”，"
                    "故仅在本 ROI 使用有界正则。Shape 不代表业务授权；未完成踢友军策略、"
                    "新鲜 Runtime 身份复核和不可逆确认门禁前严禁点击。"
                ),
                ocr_text=r"互换采[气无]",
                match_mode="regex",
                identity=True,
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
            raise RuntimeError(f"既有洞天友军侍从详情场景编号无效：{existing.get('filename')}")
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
    source_frame = Path(os.environ.get("FANXIU_DONGTIAN_FRIEND_SWAP_FRAME") or DEFAULT_SOURCE_FRAME)
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
        backup = tree_path.with_name(tree_path.name + f".before-dongtian-friend-swap-{stamp}.bak")
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
