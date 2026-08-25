from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.data_annotation.frame_structure_organizer import organize_frame_structure_file
from backend.core.fanxiu.behavior_tree.runtime import DEFAULT_FANXIU_ENTRY_ID, data_annotation_asset_tree_path
from pyxllib.autogui import View, image_number


def _frame_info_by_id(tree: list[dict]) -> dict[int, dict[str, object]]:
    result: dict[int, dict[str, object]] = {}

    def visit(nodes: list, parts: list[str], parent_id: int | None = None) -> None:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            title = str(node.get("title") or node.get("filename") or "").strip()
            next_parts = [*parts, title] if title else parts
            if node.get("type") == "image":
                scene_id = image_number(node)
                if scene_id is not None:
                    result[int(scene_id)] = {
                        "title": title,
                        "filename": str(node.get("filename") or ""),
                        "path": " > ".join(next_parts),
                        "layer": int(View(node).layer),
                        "parent_id": parent_id,
                    }
                    visit(node.get("children") or [], next_parts, int(scene_id))
                    continue
            visit(node.get("children") or [], next_parts, parent_id)

    visit(tree, [])
    return result


def _enrich_adoptions(result: dict[str, object], tree: list[dict]) -> None:
    by_id = _frame_info_by_id(tree)
    stats = result.get("stats")
    if not isinstance(stats, dict):
        return
    adoptions = stats.get("adoptions")
    if not isinstance(adoptions, list):
        return
    for adoption in adoptions:
        if not isinstance(adoption, dict):
            continue
        parent_id = adoption.get("parent_id")
        child_id = adoption.get("child_id")
        if isinstance(parent_id, int):
            adoption["parent"] = by_id.get(parent_id, {})
        if isinstance(child_id, int):
            adoption["child"] = by_id.get(child_id, {})


def main() -> int:
    parser = argparse.ArgumentParser(description="按场景身份公共锚点整理凡修 frame/subframe structure。")
    parser.add_argument("--entry-id", default=DEFAULT_FANXIU_ENTRY_ID, help="资产树 entry_id，默认使用当前凡修入口。")
    parser.add_argument("--asset-tree", type=Path, default=None, help="显式指定资产树 JSON 路径。")
    parser.add_argument("--write", action="store_true", help="实际写回资产树；默认只 dry-run 输出统计。")
    parser.add_argument("--no-backup", action="store_true", help="写回时不创建 .bak 备份。")
    parser.add_argument(
        "--scope",
        choices=["layer", "sibling"],
        default="layer",
        help="归纳范围：layer=按 Layer 视图跨业务目录整理 root frame；sibling=只整理同目录同级图片。",
    )
    parser.add_argument("--threshold", type=float, default=80.0, help="身份锚点命中阈值。")
    parser.add_argument("--min-shared-anchors", type=int, default=1, help="归档所需的最少公共身份锚点数。")
    parser.add_argument("--allow-cross-layer", action="store_true", help="允许跨 layer 归档；默认只整理同 layer sibling。")
    parser.add_argument(
        "--keep-unshared-parent-identities",
        action="store_true",
        help="不降级父 frame 中未被子 frame 共享的身份锚点。",
    )
    args = parser.parse_args()

    path = args.asset_tree or data_annotation_asset_tree_path(args.entry_id)
    result = organize_frame_structure_file(
        path,
        entry_id=args.entry_id,
        write=bool(args.write),
        backup=not bool(args.no_backup),
        scope=str(args.scope),
        threshold=float(args.threshold),
        min_shared_anchors=max(1, int(args.min_shared_anchors)),
        require_same_layer=not bool(args.allow_cross_layer),
        demote_unshared_parent_identities=not bool(args.keep_unshared_parent_identities),
    )
    tree = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(tree, list):
        _enrich_adoptions(result, tree)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
