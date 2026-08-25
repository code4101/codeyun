from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.data_annotation.storage import (
    data_annotation_asset_tree_path,
    update_data_annotation_asset_tree,
)


ENTRY_ID = "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"


def _find_image(nodes: list[dict], number: int) -> dict | None:
    for node in nodes:
        filename = str(node.get("filename") or "")
        if node.get("type") == "image" and filename.split(".", 1)[0].lstrip("0") == str(number):
            return node
        found = _find_image(node.get("children") or [], number)
        if found is not None:
            return found
    return None


def main() -> None:
    path = data_annotation_asset_tree_path(ENTRY_ID)
    before_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = (
        Path.home() / "AppData" / "Local" / "Temp" / "codeyun"
        / "fanxiu-asset-backups" / f"prayer-new-round-confirm-{stamp}"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / "asset-tree.before.json"
    changed = {"value": False}

    def update(tree: list[dict]) -> bool:
        prayer = _find_image(tree, 455)
        if prayer is None:
            raise RuntimeError("缺少 #455 祈愿正式资产")
        if any(str(shape.get("title") or "") == "新一轮确认" for shape in prayer.get("shapes") or []):
            return False
        prayer.setdefault("shapes", []).append({
            "id": "shape-prayer-new-round-confirm",
            "kind": "shape",
            "title": "新一轮确认",
            "description": "真实帧 OCR：确认 bbox=(419,1038,84,45)，仅在业务代码确认完整弹窗文案后使用",
            "locked": False,
            "floating": False,
            "jitterEnabled": False,
            "jitterRadius": 4,
            "isSceneIdentity": False,
            "sceneIdentityRole": "off",
            "sceneJumpTarget": "455",
            "loadDirection": "none",
            "imageMatchRole": "off",
            "pixelTolerance": 20,
            "ocrMatchRole": "required",
            "ocrEnabled": True,
            "ocrText": "确认",
            "ocrMatchMode": "exact",
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
            "x": 0.40,
            "y": 0.625,
            "w": 0.20,
            "h": 0.09,
            "children": [],
        })
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
