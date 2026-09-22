from __future__ import annotations

"""列出落在「遮挡」区里的场景身份 shape，供把身份挪到遮挡区之外。

背景：资产树的「遮挡」分组定义了世界公告、通知轮播、道具/活动效果横幅的固定播放位置。
这些区域的内容是**周期性出现**的覆盖层，把场景标识（sceneIdentityRole=required）放在里面，
识别就会时好时坏：2026-09-22 缘定三生入口就是这样被世界公告横幅盖住卡片标题而偶发进不去活动。

本脚本只读资产树，不修改任何资产；输出每个越界身份的坐标、覆盖率与所属场景，供 AI/人工按优先级迁移。
真正稳定的做法是：在遮挡区之外另选锚点（页签、标题、按钮等不会被动效覆盖的位置）。

用法：
    uv run python scripts/fanxiu_occlusion_identity_audit.py
    uv run python scripts/fanxiu_occlusion_identity_audit.py --json
    uv run python scripts/fanxiu_occlusion_identity_audit.py --min-ratio 0.5
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pyxllib.autogui import image_number

from backend.core.fanxiu.data_annotation.storage import (
    data_annotation_asset_tree_path,
    read_data_annotation_asset_tree_snapshot,
)

FRAME_WIDTH = 900.0
FRAME_HEIGHT = 1600.0


def _walk(nodes: Iterable[dict[str, Any]], path: str = ""):
    for node in nodes:
        title = str(node.get("title") or "")
        current = f"{path}/{title}"
        yield current, node
        children = node.get("children")
        if isinstance(children, list):
            yield from _walk(children, current)


def occlusion_boxes(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read the occlusion rectangles exactly as annotated under the 「遮挡」 group."""

    boxes: list[dict[str, Any]] = []
    for path, node in _walk(tree):
        if not path.startswith("/遮挡"):
            continue
        for shape in node.get("shapes") or []:
            boxes.append({
                "source": f"{path}/{shape.get('title')}",
                "x": float(shape.get("x") or 0) * FRAME_WIDTH,
                "y": float(shape.get("y") or 0) * FRAME_HEIGHT,
                "w": float(shape.get("w") or 0) * FRAME_WIDTH,
                "h": float(shape.get("h") or 0) * FRAME_HEIGHT,
            })
    return boxes


def audit_identities(*, min_ratio: float) -> dict[str, Any]:
    path = data_annotation_asset_tree_path()
    snapshot = read_data_annotation_asset_tree_snapshot(path)
    boxes = occlusion_boxes(snapshot.tree)
    offenders: list[dict[str, Any]] = []
    for scene_path, node in _walk(snapshot.tree):
        if node.get("type") != "image":
            continue
        for shape in node.get("shapes") or []:
            if shape.get("sceneIdentityRole") != "required":
                continue
            x = float(shape.get("x") or 0) * FRAME_WIDTH
            y = float(shape.get("y") or 0) * FRAME_HEIGHT
            w = float(shape.get("w") or 0) * FRAME_WIDTH
            h = float(shape.get("h") or 0) * FRAME_HEIGHT
            area = max(1.0, w * h)
            for box in boxes:
                ix = max(0.0, min(x + w, box["x"] + box["w"]) - max(x, box["x"]))
                iy = max(0.0, min(y + h, box["y"] + box["h"]) - max(y, box["y"]))
                ratio = (ix * iy) / area
                if ratio < min_ratio:
                    continue
                offenders.append({
                    "scene_id": image_number(node),
                    "scene_title": str(node.get("title") or ""),
                    "scene_path": scene_path,
                    "shape_title": str(shape.get("title") or ""),
                    "ocr_text": str(shape.get("ocrText") or ""),
                    "box_px": [round(x), round(y), round(w), round(h)],
                    "occlusion": box["source"],
                    "ratio": round(ratio, 3),
                })
    offenders.sort(key=lambda row: (-row["ratio"], int(row["scene_id"] or 0)))
    return {
        "asset_tree": str(path),
        "occlusion_boxes": [{k: round(v, 1) if isinstance(v, float) else v for k, v in box.items()} for box in boxes],
        "min_ratio": min_ratio,
        "offender_count": len(offenders),
        "scene_count": len({row["scene_id"] for row in offenders}),
        "offenders": offenders,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="审计落在遮挡区内的场景身份 shape")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--min-ratio", type=float, default=0.15, help="与遮挡区的重叠占比阈值（默认 0.15）")
    args = parser.parse_args()
    report = audit_identities(min_ratio=max(0.0, float(args.min_ratio)))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    print(f"资产树：{report['asset_tree']}")
    for box in report["occlusion_boxes"]:
        print(f"遮挡区 {box['source']}：x={box['x']:.0f} y={box['y']:.0f} w={box['w']:.0f} h={box['h']:.0f}")
    print(f"越界身份 shape 数：{report['offender_count']}，涉及场景 {report['scene_count']} 个（阈值 {report['min_ratio']}）")
    for row in report["offenders"][:60]:
        print(f"  #{row['scene_id']} {row['scene_title']} | {row['shape_title']} | box={row['box_px']} | {row['occlusion']} | 占比={row['ratio']}")
    if report["offender_count"] > 60:
        print(f"  ... 其余 {report['offender_count'] - 60} 条用 --json 查看")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())