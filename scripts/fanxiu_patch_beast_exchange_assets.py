from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from backend.core.fanxiu.data_annotation.storage import (
    data_annotation_asset_tree_path,
    update_data_annotation_asset_tree,
)


ENTRY_ID = "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"


def _copy_shape(source: dict, title: str) -> dict:
    copied = json.loads(json.dumps(source, ensure_ascii=False))
    copied["id"] = f"shape-beast-exchange-{title}"
    copied["title"] = title
    copied["isSceneIdentity"] = False
    copied["sceneJumpTarget"] = ""
    copied["imageMatchRole"] = "off"
    copied["ocrMatchRole"] = "off"
    copied["ocrEnabled"] = False
    copied["ocrText"] = ""
    return copied


def main() -> None:
    path = data_annotation_asset_tree_path(ENTRY_ID)
    before_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path.home() / "AppData" / "Local" / "Temp" / "codeyun" / "fanxiu-asset-backups" / f"beast-exchange-rows-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / "asset-tree.before.json"

    def find_image(nodes: list[dict], number: int) -> dict | None:
        for node in nodes:
            filename = str(node.get("filename") or "")
            if node.get("type") == "image" and filename.split(".", 1)[0].lstrip("0") == str(number):
                return node
            found = find_image(node.get("children") or [], number)
            if found is not None:
                return found
        return None

    changed = {"value": False}

    def update(tree: list[dict]) -> bool:
        beast = find_image(tree, 536)
        source = find_image(tree, 519)
        if beast is None or source is None:
            raise RuntimeError("缺少 #536 或 #519 正式资产")
        existing = {str(shape.get("title") or "") for shape in beast.get("shapes") or []}
        source_shapes = {
            str(shape.get("title") or ""): shape for shape in source.get("shapes") or []
        }
        additions = []
        for title in ("商品列表", "商品行1", "商品行2", "商品行3", "商品行4", "商品行5"):
            if title not in existing:
                additions.append(_copy_shape(source_shapes[title], title))
        if not additions:
            return False
        beast.setdefault("shapes", []).extend(additions)
        changed["value"] = True
        return True

    snapshot = update_data_annotation_asset_tree(
        path,
        update,
        before_write=lambda: shutil.copy2(path, backup),
    )
    print(json.dumps({
        "changed": changed["value"],
        "before_sha256": before_sha,
        "after_sha256": snapshot.revision,
        "backup": str(backup),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
