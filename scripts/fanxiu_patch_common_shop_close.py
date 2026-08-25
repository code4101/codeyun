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
        Path.home()
        / "AppData"
        / "Local"
        / "Temp"
        / "codeyun"
        / "fanxiu-asset-backups"
        / f"common-shop-close-{stamp}"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / "asset-tree.before.json"
    changed = {"value": False}

    def update(tree: list[dict]) -> bool:
        dialog = _find_image(tree, 634)
        if dialog is None:
            raise RuntimeError("缺少 #634 通用兑换数量框正式资产")
        if any(str(shape.get("title") or "") == "关闭" for shape in dialog.get("shapes") or []):
            return False
        dialog.setdefault("shapes", []).append({
            "id": "shape-common-shop-buy-close-20260825",
            "kind": "shape",
            "title": "关闭",
            "description": (
                "2026-08-25 真实 #634 帧：弹窗右边界 x=836/900，"
                "点击右侧无遮挡背景安全关闭；用于返回实际来源页后重新识别。"
            ),
            "locked": False,
            "floating": False,
            "jitterEnabled": False,
            "jitterRadius": 4,
            "isSceneIdentity": False,
            "sceneIdentityRole": "off",
            "sceneJumpTarget": "-1",
            "loadDirection": "none",
            "imageMatchRole": "off",
            "pixelTolerance": 8,
            "ocrMatchRole": "off",
            "ocrEnabled": False,
            "ocrText": "",
            "ocrMatchMode": "contains",
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
            "x": 0.94,
            "y": 0.18,
            "w": 0.04,
            "h": 0.06,
            "children": [],
            "source": "visual-model",
            "evidenceFrame": (
                "C:\\Users\\kzche\\AppData\\Local\\Temp\\codeyun\\fanxiu-evidence\\"
                "doctor_20260825_002203.png"
            ),
            "verificationStatus": "verified-current-real-frame-geometry;action-pending-runtime",
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
