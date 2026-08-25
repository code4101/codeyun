from __future__ import annotations

"""Install the first verified Magic Invasion UI frames into the Fanxiu asset tree.

The source frames are real Runtime screenshots captured during the 2026-08-10
cross-server event.  Dynamic counters and reward rows are deliberately excluded
from scene identity; only stable business text is used.
"""

import json
import argparse
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu.behavior_tree.runtime import (
    DEFAULT_FANXIU_ENTRY_ID,
    data_annotation_asset_tree_path,
)


EVIDENCE_ROOT = Path.home() / "AppData/Local/Temp/codeyun/fanxiu-evidence"
MODAO_CAPTURE_ROOT = Path.home() / "AppData/Local/Temp/codeyun/fanxiu-modao-20260810"
TARGET_FOLDER_PATH = ("日程", "玩法榜", "魔道入侵")
LEGACY_FOLDER_PATH = ("活动", "魔道入侵")
READ_ONLY_SCENE_FILENAMES = {"0519.png", "0520.png", "0521.png"}
PROHIBITED_READ_ONLY_ACTION_WORDS = ("购买", "兑换", "奖励", "领取")


def _shape(
    title: str,
    box: tuple[float, float, float, float],
    *,
    ocr_text: str = "",
    identity: bool = False,
    identity_role: str = "required",
    match_mode: str = "contains",
    ocr_observe: bool = False,
    jump: str = "",
) -> dict[str, Any]:
    x, y, w, h = box
    stamp = int(time.time() * 1000)
    return {
        "id": f"shape-modao-{stamp}-{uuid.uuid4().hex[:12]}",
        "kind": "shape",
        "title": title,
        "description": "",
        "locked": False,
        "floating": False,
        "jitterEnabled": False,
        "jitterRadius": 4,
        "isSceneIdentity": identity,
        "sceneIdentityRole": identity_role if identity else "off",
        "sceneJumpTarget": jump,
        "loadDirection": "none",
        "imageMatchRole": "off",
        "pixelTolerance": 20,
        "ocrMatchRole": identity_role if identity and ocr_text else "off",
        "ocrEnabled": bool((identity or ocr_observe) and ocr_text),
        "ocrText": ocr_text if identity or ocr_observe else "",
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


def _node(
    number: int,
    title: str,
    source: str | Path,
    shapes: list[dict[str, Any]],
) -> dict[str, Any]:
    source_path = Path(source)
    path = source_path if source_path.is_absolute() else EVIDENCE_ROOT / source_path
    if not path.is_file():
        raise FileNotFoundError(path)
    with Image.open(path) as image:
        width, height = image.size
    return {
        "id": f"image-modao-{number}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:10]}",
        "type": "image",
        "title": title,
        "filename": f"{number:04d}.png",
        "width": width,
        "height": height,
        "shapes": shapes,
        "children": [],
        "_source": str(path),
    }


def _direct_folder(items: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("type") == "folder" and item.get("title") == title:
            return item
    return None


def _folder_at_path(
    tree: list[dict[str, Any]], path: tuple[str, ...]
) -> dict[str, Any] | None:
    items = tree
    current: dict[str, Any] | None = None
    for title in path:
        current = _direct_folder(items, title)
        if current is None:
            return None
        children = current.get("children")
        if not isinstance(children, list):
            raise RuntimeError(f"资产目录 {'/'.join(path)} 的 children 非列表")
        items = children
    return current


def _remove_filenames_except(
    items: list[dict[str, Any]],
    filenames: set[str],
    *,
    keep_folder: dict[str, Any],
) -> int:
    removed = 0
    retained: list[dict[str, Any]] = []
    for item in items:
        if item is keep_folder:
            retained.append(item)
            continue
        if (
            item.get("type") == "image"
            and str(item.get("filename") or "") in filenames
        ):
            removed += 1
            continue
        children = item.get("children")
        if isinstance(children, list):
            removed += _remove_filenames_except(
                children, filenames, keep_folder=keep_folder
            )
        retained.append(item)
    items[:] = retained
    return removed


def _merge_key(item: dict[str, Any]) -> tuple[str, str]:
    if item.get("type") == "image" and item.get("filename"):
        return "image", str(item["filename"])
    if item.get("id"):
        return "id", str(item["id"])
    return str(item.get("type") or ""), str(item.get("title") or "")


def _validate_read_only_scene_actions(nodes: list[dict[str, Any]]) -> None:
    """Keep data-only shop/ranking frames free of purchase/reward actions."""

    for node in nodes:
        if str(node.get("filename") or "") not in READ_ONLY_SCENE_FILENAMES:
            continue
        for shape in node.get("shapes") or []:
            if not str(shape.get("sceneJumpTarget") or "").strip():
                continue
            action_text = f"{shape.get('title') or ''} {shape.get('ocrText') or ''}"
            if any(word in action_text for word in PROHIBITED_READ_ONLY_ACTION_WORDS):
                raise RuntimeError(
                    f"{node.get('filename')} 禁止安装购买/兑换/奖励动作："
                    f"{shape.get('title') or shape.get('id')}"
                )


def install_nodes_into_tree(
    tree: list[dict[str, Any]], nodes: list[dict[str, Any]]
) -> dict[str, Any]:
    """Merge verified frames into the exact authoritative directory."""

    _validate_read_only_scene_actions(nodes)

    gameplay = _folder_at_path(tree, TARGET_FOLDER_PATH[:2])
    if gameplay is None:
        raise RuntimeError("资产树缺少日程/玩法榜分组")
    gameplay_children = gameplay.get("children")
    if not isinstance(gameplay_children, list):
        raise RuntimeError("日程/玩法榜 children 非列表")

    target = _direct_folder(gameplay_children, TARGET_FOLDER_PATH[-1])
    changed = False
    if target is None:
        target = {
            "id": f"folder-modao-{int(time.time() * 1000)}-{uuid.uuid4().hex[:10]}",
            "type": "folder",
            "title": TARGET_FOLDER_PATH[-1],
            "children": [],
            "filename": "",
        }
        gameplay_children.append(target)
        changed = True
    target_children = target.get("children")
    if not isinstance(target_children, list):
        raise RuntimeError("日程/玩法榜/魔道入侵 children 非列表")

    legacy = _folder_at_path(tree, LEGACY_FOLDER_PATH)
    migrated_count = 0
    if legacy is not None and legacy is not target:
        legacy_children = legacy.get("children")
        if not isinstance(legacy_children, list):
            raise RuntimeError("活动/魔道入侵 children 非列表")
        known_nodes = {_merge_key(item) for item in target_children}
        for item in legacy_children:
            key = _merge_key(item)
            if key not in known_nodes:
                target_children.append(item)
                known_nodes.add(key)
                migrated_count += 1
        legacy_parent = _folder_at_path(tree, LEGACY_FOLDER_PATH[:1])
        if legacy_parent is not None:
            legacy_parent_children = legacy_parent.get("children")
            if isinstance(legacy_parent_children, list):
                legacy_parent_children[:] = [
                    item for item in legacy_parent_children if item is not legacy
                ]
        changed = True

    existing_by_filename = {
        str(item.get("filename") or ""): item
        for item in target_children
        if item.get("type") == "image" and str(item.get("filename") or "")
    }
    added_nodes: list[dict[str, Any]] = []
    updated_nodes: list[dict[str, Any]] = []
    for node in nodes:
        filename = str(node.get("filename") or "")
        existing = existing_by_filename.get(filename)
        if existing is not None:
            old_shape_ids = {
                str(shape.get("title") or ""): str(shape.get("id") or "")
                for shape in existing.get("shapes") or []
                if isinstance(shape, dict) and str(shape.get("title") or "")
            }
            for shape in node.get("shapes") or []:
                old_id = old_shape_ids.get(str(shape.get("title") or ""))
                if old_id:
                    shape["id"] = old_id
            desired = {
                key: value
                for key, value in node.items()
                if key not in {"id", "_source"}
            }
            if any(existing.get(key) != value for key, value in desired.items()):
                existing.update(desired)
                existing["_source"] = node["_source"]
                updated_nodes.append(existing)
                changed = True
            continue
        target_children.append(node)
        existing_by_filename[filename] = node
        added_nodes.append(node)
        changed = True

    duplicate_count = _remove_filenames_except(
        tree,
        {str(node.get("filename") or "") for node in nodes},
        keep_folder=target,
    )
    return {
        "folder": target,
        "added_nodes": added_nodes,
        "updated_nodes": updated_nodes,
        "migrated_count": migrated_count,
        "removed_duplicate_count": duplicate_count,
        "changed": changed or duplicate_count > 0,
    }


def build_nodes() -> list[dict[str, Any]]:
    return [
        _node(
            509,
            "魔道入侵封面",
            "doctor_20260810_141754.png",
            [
                _shape("活动结束倒计时", (0.25, 0.69, 0.50, 0.045), ocr_text="活动结束倒计时", identity=True),
                _shape("前往大地图", (0.25, 0.742, 0.50, 0.063), ocr_text="前往大地图", identity=True, jump="512"),
                _shape("任务", (0.59, 0.82, 0.09, 0.145), jump=""),
                _shape("返回", (0.025, 0.90, 0.12, 0.065), jump="34"),
            ],
        ),
        _node(
            510,
            "魔道入侵任务·除魔",
            "doctor_20260810_141955.png",
            [
                _shape("魔道入侵标题", (0.32, 0.025, 0.36, 0.065), ocr_text="魔道入侵", identity=True),
                _shape("除魔页签", (0.07, 0.115, 0.18, 0.06), ocr_text="除魔", identity=True),
                _shape("修为页签", (0.25, 0.115, 0.19, 0.06), jump="511"),
                _shape("首条任务领取区", (0.07, 0.184, 0.44, 0.095)),
                _shape("活动主页", (0.33, 0.82, 0.14, 0.17), jump="509"),
                _shape("任务返回", (0.025, 0.90, 0.12, 0.065), jump="34"),
            ],
        ),
        _node(
            511,
            "魔道入侵任务·修为",
            "doctor_20260810_142233.png",
            [
                _shape("魔道入侵标题", (0.32, 0.025, 0.36, 0.065), ocr_text="魔道入侵", identity=True),
                _shape("修为页签", (0.25, 0.115, 0.19, 0.06), ocr_text="修为", identity=True),
                _shape("除魔页签", (0.07, 0.115, 0.18, 0.06), jump="510"),
                _shape("首条任务领取区", (0.07, 0.184, 0.44, 0.095)),
                _shape("活动主页", (0.33, 0.82, 0.14, 0.17), jump="509"),
                _shape("任务返回", (0.025, 0.90, 0.12, 0.065), jump="34"),
            ],
        ),
        _node(
            512,
            "魔道入侵大地图",
            "doctor_20260822_164507.png",
            [
                _shape("挑战事件", (0.07, 0.018, 0.27, 0.055), ocr_text="挑战事件", identity=True),
                _shape("快速探索", (0.31, 0.665, 0.34, 0.045), ocr_text="快速探索", identity=True),
                _shape("探查", (0.39, 0.515, 0.22, 0.12), jump="515"),
                _shape(
                    "可用探查次数",
                    (0.38, 0.635, 0.27, 0.035),
                    ocr_text=r"\d+\s*/\s*\d+",
                    match_mode="regex",
                    ocr_observe=True,
                ),
                _shape("补充探查次数", (0.565, 0.625, 0.07, 0.055), jump="513"),
                _shape("快速探索开启态", (0.602, 0.672, 0.045, 0.026))
                | {"imageMatchRole": "required", "pixelTolerance": 52},
                _shape("快速探索开关", (0.585, 0.665, 0.07, 0.05)),
                _shape("地图返回", (0.015, 0.90, 0.12, 0.065), jump="509"),
            ],
        ),
        _node(
            513,
            "魔道入侵探查道具",
            "doctor_20260810_151228.png",
            [
                _shape("天眼符", (0.30, 0.39, 0.25, 0.08), ocr_text="天眼符", identity=True),
                _shape("帝君敕令符", (0.30, 0.55, 0.31, 0.075), ocr_text="帝君敕令符", identity=True),
                _shape("天眼符条目", (0.29, 0.39, 0.44, 0.09), jump="514"),
                _shape("关闭道具列表", (0.75, 0.72, 0.18, 0.12), jump="512"),
            ],
        ),
        _node(
            514,
            "魔道入侵使用天眼符",
            "doctor_20260822_165805.png",
            [
                _shape("使用标题", (0.16, 0.275, 0.17, 0.06), ocr_text="使用", identity=True),
                _shape("天眼符标题", (0.38, 0.365, 0.20, 0.05), ocr_text="天眼符", identity=True),
                _shape(
                    "持有数量",
                    (0.38, 0.405, 0.34, 0.045),
                    ocr_text=r"持有数量\s*[:：]?\s*\d+",
                    match_mode="regex",
                    ocr_observe=True,
                ),
                _shape(
                    "使用数量",
                    (0.43, 0.50, 0.14, 0.05),
                    ocr_text=r"\d+",
                    match_mode="regex",
                    ocr_observe=True,
                ),
                _shape("使用数量为1", (0.478, 0.527, 0.044, 0.032))
                | {"imageMatchRole": "required", "pixelTolerance": 35},
                _shape("数量减", (0.18, 0.535, 0.08, 0.06)),
                # Track endpoints come from the real 2368-owned / 500-selected
                # frame: knob center ~=335px on a 238..700px track.  The outer
                # dialog ROI includes the +/- buttons and would over-shoot.
                # Reuse the verified Yunmeng/Xutian integer-slider controller.
                # The floating image condition resolves the live thumb after
                # every bound probe instead of assuming its remembered x.
                _shape("数量滑块游标", (0.252, 0.546, 0.041, 0.043))
                | {"floating": True, "imageMatchRole": "required", "pixelTolerance": 40},
                _shape("数量滑轨左端", (0.268, 0.563, 0.010, 0.010)),
                _shape("数量滑轨右端", (0.773, 0.563, 0.010, 0.010)),
                _shape("数量加", (0.77, 0.535, 0.08, 0.06)),
                _shape("使用", (0.35, 0.625, 0.30, 0.065), jump="513"),
                _shape(
                    "返回",
                    (0.05555555555555555, 0.9385416666666667, 0.07222222222222222, 0.038541666666666585),
                    jump="513",
                )
                | {
                    "description": (
                        "2026-08-22 17:47 当前真帧证明纸页右上角仅为折角装饰；"
                        "复用 #424[返回] 的游戏内左下背景落点，位于纸页外且"
                        "避开弹窗内所有控件；2026-08-22 17:52 真实点击后落到 #513。"
                    )
                },
            ],
        ),
        _node(
            515,
            "魔道入侵快速探索结果",
            "doctor_20260810_154206.png",
            [
                _shape("快速探索标题", (0.16, 0.275, 0.25, 0.06), ocr_text="快速探索", identity=True),
                _shape("探索次数结果", (0.22, 0.37, 0.45, 0.045), ocr_text=r"快速探索\d+次", identity=True, match_mode="regex"),
                _shape(
                    "确定",
                    (0.36, 0.64, 0.28, 0.065),
                    ocr_text=r"[确確]定",
                    identity=True,
                    match_mode="regex",
                    jump="512",
                ),
            ],
        ),
        _node(
            516,
            "魔道入侵事件·魔道宗主",
            "doctor_20260810_144718.png",
            [
                _shape("魔道宗主", (0.34, 0.28, 0.33, 0.055), ocr_text="魔道宗主", identity=True),
                _shape("稍后处理", (0.17, 0.735, 0.30, 0.065), ocr_text="稍后处理", identity=True, jump="512"),
                _shape("前往挑战", (0.57, 0.735, 0.30, 0.065)),
            ],
        ),
        _node(
            517,
            "魔道入侵进入大地图确认",
            "doctor_20260822_162735.png",
            [
                _shape(
                    "确认离开提示",
                    (0.18, 0.42, 0.65, 0.10),
                    ocr_text="确认离开当前地图前往沙盘进入魔道入侵玩法",
                    identity=True,
                ),
                _shape(
                    "取消",
                    (0.19, 0.635, 0.30, 0.065),
                    ocr_text="取消",
                    ocr_observe=True,
                    jump="509",
                ) | {"ocrMatchRole": "required"},
                _shape(
                    "确认",
                    (0.53, 0.635, 0.30, 0.065),
                    ocr_text="确认",
                    ocr_observe=True,
                    jump="512",
                ) | {"ocrMatchRole": "required"},
            ],
        ),
        _node(
            518,
            "魔道入侵事件覆盖确认",
            "doctor_20260810_162728.png",
            [
                _shape(
                    "事件上限提示",
                    (0.20, 0.43, 0.61, 0.15),
                    ocr_text="除魔列表最多保存2000个事件",
                    identity=True,
                ),
                _shape("取消", (0.20, 0.64, 0.29, 0.06), ocr_text="取消", identity=True, jump="512"),
                _shape("确认覆盖", (0.54, 0.64, 0.29, 0.06), ocr_text="确认", identity=True, jump="515"),
            ],
        ),
        _node(
            519,
            "魔道入侵·兑换宝阁",
            MODAO_CAPTURE_ROOT / "07_exchange_pavilion_review2.png",
            [
                _shape(
                    "兑换宝阁标题",
                    (0.2703888888888889, 0.0435625, 0.48033333333333333, 0.072875),
                    ocr_text="兑换宝阁",
                    identity=True,
                ),
                _shape(
                    "当前拥有魔晶",
                    (0.2112777777777778, 0.1245625, 0.583, 0.0254375),
                    ocr_text=r"当前拥有(?:位面)?魔晶",
                    identity=True,
                    match_mode="regex",
                ),
                _shape("商品列表", (0.055, 0.205, 0.88, 0.61)),
                _shape("商品行1", (0.06, 0.205, 0.87, 0.10)),
                _shape("商品行2", (0.06, 0.3175, 0.87, 0.10)),
                _shape("商品行3", (0.06, 0.43, 0.87, 0.10)),
                _shape("商品行4", (0.06, 0.5425, 0.87, 0.10)),
                _shape("商品行5", (0.06, 0.655, 0.87, 0.10)),
                _shape("返回", (0.025, 0.90, 0.12, 0.065), jump="509"),
            ],
        ),
        _node(
            520,
            "魔道入侵·除魔榜·个人",
            MODAO_CAPTURE_ROOT / "11_personal_rank_review2.png",
            [
                _shape(
                    "魔道入侵标题",
                    (0.3486666666666667, 0.0338125, 0.2982222222222222, 0.0405625),
                    ocr_text="魔道入侵",
                    identity=True,
                ),
                # Only the lower selected-tab fill is compared.  The OCR text
                # itself is deliberately excluded from the image discriminator.
                _shape(
                    "个人选中态",
                    (0.06111111111111111, 0.1575, 0.22777777777777777, 0.01125),
                    identity=True,
                ) | {"imageMatchRole": "required", "pixelTolerance": 28},
                _shape(
                    "位面",
                    (0.3815, 0.13225, 0.08922222222222222, 0.023375),
                    ocr_text="位面",
                    jump="521",
                ),
                _shape("返回", (0.025, 0.90, 0.12, 0.065)),
            ],
        ),
        _node(
            521,
            "魔道入侵·除魔榜·位面",
            MODAO_CAPTURE_ROOT / "13_plane_rank_review2.png",
            [
                _shape(
                    "魔道入侵标题",
                    (0.3486666666666667, 0.0338125, 0.2982222222222222, 0.0405625),
                    ocr_text="魔道入侵",
                    identity=True,
                ),
                _shape(
                    "位面选中态",
                    (0.30, 0.1575, 0.23333333333333334, 0.01125),
                    identity=True,
                ) | {"imageMatchRole": "required", "pixelTolerance": 28},
                _shape(
                    "个人",
                    (0.1393888888888889, 0.1323125, 0.08677777777777777, 0.0226875),
                    ocr_text="个人",
                ),
                _shape("返回", (0.025, 0.90, 0.12, 0.065)),
            ],
        ),
        _node(
            641,
            "魔道入侵入口情报对话",
            "doctor_20260822_163638.png",
            [
                _shape(
                    "情报角色",
                    (0.33, 0.145, 0.19, 0.045),
                    ocr_text=r"陈巧倩\s*[:：]?",
                    identity=True,
                    match_mode="regex",
                ),
                _shape(
                    "魔道情报文本",
                    (0.39, 0.18, 0.51, 0.065),
                    ocr_text=r"详细的魔道|魔道情报",
                    identity=True,
                    match_mode="regex",
                ),
            ],
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only-scene",
        type=int,
        default=None,
        help="Only install one verified scene without rewriting other live jump frequencies.",
    )
    args = parser.parse_args()
    tree_path = data_annotation_asset_tree_path(DEFAULT_FANXIU_ENTRY_ID)
    entry_root = tree_path.parent
    image_root = entry_root / "images"
    tree = json.loads(tree_path.read_text(encoding="utf-8"))
    if not isinstance(tree, list):
        raise RuntimeError("资产树根节点不是列表")
    nodes = build_nodes()
    if args.only_scene is not None:
        nodes = [node for node in nodes if str(node.get("filename")) == f"{args.only_scene:04d}.png"]
        if not nodes:
            raise RuntimeError(f"未知魔道场景：{args.only_scene}")
    result = install_nodes_into_tree(tree, nodes)
    folder = result["folder"]
    image_updated_count = 0
    for node in nodes:
        source = Path(str(node["_source"]))
        destination = image_root / str(node["filename"])
        if not destination.exists() or source.read_bytes() != destination.read_bytes():
            shutil.copy2(source, destination)
            image_updated_count += 1
    for node in [*result["added_nodes"], *result["updated_nodes"]]:
        node.pop("_source", None)

    backup: Path | None = None
    if result["changed"]:
        backup = tree_path.with_name(
            tree_path.name + f".before-modao-{time.strftime('%Y%m%d-%H%M%S')}.bak"
        )
        shutil.copy2(tree_path, backup)
        tree_path.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "asset_tree": str(tree_path),
                "backup": str(backup) if backup is not None else "",
                "folder": "/".join(TARGET_FOLDER_PATH),
                "frames": [node["filename"] for node in folder["children"]],
                "changed": result["changed"] or image_updated_count > 0,
                "added_count": len(result["added_nodes"]),
                "updated_count": len(result["updated_nodes"]),
                "image_updated_count": image_updated_count,
                "migrated_count": result["migrated_count"],
                "removed_duplicate_count": result["removed_duplicate_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
