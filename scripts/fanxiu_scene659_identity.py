from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

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
from scripts.fanxiu_scene423_identity import _ocr_fragments, _walk


POSITIVE_FILENAME = "0659.png"
NEGATIVE_FILENAMES = ("0423.png", "0620.png", "0177.jpg", "0556.png", "0544.png")
IDENTITY_SPECS = (
    {
        "id": "shape-659-trigger-effect-identity-20260825",
        "title": "触发效果",
        "ocrText": "触发",
        "x": 390 / 900,
        "y": 388 / 1600,
        "w": 500 / 900,
        "h": 58 / 1600,
    },
    {
        "id": "shape-659-guest-list-identity-20260825",
        "title": "宾客名单",
        "ocrText": "宾客名单",
        "x": 354 / 900,
        "y": 917 / 1600,
        "w": 202 / 900,
        "h": 74 / 1600,
    },
)


def _scene659(tree: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [node for node in _walk(tree) if node.get("type") == "image" and node.get("filename") == POSITIVE_FILENAME]
    if len(matches) != 1:
        raise RuntimeError(f"#659 节点数量异常：{len(matches)}")
    return matches[0]


def _identity_shape(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": spec["id"],
        "kind": "shape",
        "title": spec["title"],
        "description": "#659 仙宴特效结算专属锚点；真实 #659 正帧与 #423/#620/通用结算负帧离线 OCR 双遍复核。",
        "locked": False,
        "floating": False,
        "jitterEnabled": False,
        "jitterRadius": 4,
        "isSceneIdentity": True,
        "sceneIdentityRole": "required",
        "sceneJumpTarget": "",
        "loadDirection": "none",
        "imageMatchRole": "off",
        "pixelTolerance": 5,
        "ocrMatchRole": "required",
        "ocrEnabled": True,
        "ocrText": spec["ocrText"],
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
        "x": spec["x"],
        "y": spec["y"],
        "w": spec["w"],
        "h": spec["h"],
        "children": [],
    }


def refine_scene659_identity(tree: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result = copy.deepcopy(tree)
    scene = _scene659(result)
    scene["layer"] = 2
    shapes = [shape for shape in scene.get("shapes") or [] if isinstance(shape, dict)]
    old_identity_ids = [str(shape.get("id") or "") for shape in shapes if bool(shape.get("isSceneIdentity"))]
    new_ids = {spec["id"] for spec in IDENTITY_SPECS}
    shapes = [shape for shape in shapes if not bool(shape.get("isSceneIdentity")) and str(shape.get("id") or "") not in new_ids]
    shapes.extend(_identity_shape(spec) for spec in IDENTITY_SPECS)
    scene["shapes"] = shapes
    validate_scene659_identity(result)
    return result, {"scene": 659, "layer": 2, "identity": [spec["ocrText"] for spec in IDENTITY_SPECS], "replaced_identity_ids": old_identity_ids}


def validate_scene659_identity(tree: list[dict[str, Any]]) -> None:
    scene = _scene659(tree)
    identities = [shape for shape in scene.get("shapes") or [] if bool(shape.get("isSceneIdentity"))]
    if int(scene.get("layer") or 0) != 2 or {shape.get("id") for shape in identities} != {spec["id"] for spec in IDENTITY_SPECS}:
        raise RuntimeError("#659 Layer2 双身份契约错误")
    if any(shape.get("sceneIdentityRole") != "required" or shape.get("ocrMatchRole") != "required" for shape in identities):
        raise RuntimeError("#659 身份锚点必须为 required OCR")


def audit_saved_frames(image_dir: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = (POSITIVE_FILENAME, *NEGATIVE_FILENAMES)
    fragments = {name: _ocr_fragments(image_dir / name) for name in frames}
    hits = {}
    for name, rows in fragments.items():
        texts = [str(row.get("text") or "") for row in rows]
        hits[name] = {spec["ocrText"]: any(spec["ocrText"] in text for text in texts) for spec in IDENTITY_SPECS}
    if not all(hits[POSITIVE_FILENAME].values()):
        raise RuntimeError("#659 正帧未同时命中双身份锚点")
    false_positives = [name for name in NEGATIVE_FILENAMES if all(hits[name].values())]
    if false_positives:
        raise RuntimeError(f"#659 双身份误命中负帧：{false_positives}")

    frame = Image.open(image_dir / POSITIVE_FILENAME).convert("RGB")
    pass1 = frame.copy()
    draw1 = ImageDraw.Draw(pass1)
    for row in fragments[POSITIVE_FILENAME]:
        x, y, w, h = (float(row.get(key) or 0) for key in ("x", "y", "w", "h"))
        draw1.rectangle((x, y, x + w, y + h), outline=(0, 170, 255), width=3)
    pass1_path = output_dir / "0659-identity-pass1-ocr-boxes.png"
    pass1.save(pass1_path)
    pass2 = frame.copy()
    draw2 = ImageDraw.Draw(pass2)
    for spec in IDENTITY_SPECS:
        x, y = spec["x"] * frame.width, spec["y"] * frame.height
        w, h = spec["w"] * frame.width, spec["h"] * frame.height
        draw2.rectangle((x, y, x + w, y + h), outline=(0, 220, 80), width=5)
    pass2_path = output_dir / "0659-identity-pass2-final-roi.png"
    pass2.save(pass2_path)
    report = {"hits": hits, "pass1_overlay": str(pass1_path), "pass2_overlay": str(pass2_path)}
    (output_dir / "scene659-ocr-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair #659 identity against #423 and generic result pages")
    parser.add_argument("--entry-id", default=DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = data_annotation_asset_tree_path(args.entry_id)
    snapshot = read_data_annotation_asset_tree_snapshot(path)
    evidence_dir = Path(tempfile.gettempdir()) / "codeyun" / "fanxiu-layer1-refactor" / "scene659-identity"
    audit = audit_saved_frames(path.parent / "images", evidence_dir)
    tree, report = refine_scene659_identity(snapshot.tree)
    report.update({"before_revision": snapshot.revision, "applied": False, **{key: audit[key] for key in ("pass1_overlay", "pass2_overlay")}})
    if args.apply:
        backup = evidence_dir / f"asset-tree.{snapshot.revision[:16]}.before.json"
        saved = save_data_annotation_asset_tree_snapshot(
            path,
            tree,
            entry_id=args.entry_id,
            expected_revision=snapshot.revision,
            before_write=lambda: shutil.copy2(path, backup),
        )
        validate_scene659_identity(saved.tree)
        report.update({"applied": True, "backup": str(backup), "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(), "after_revision": saved.revision})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
