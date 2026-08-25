from __future__ import annotations

import argparse
import base64
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

from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
from backend.core.fanxiu.data_annotation.storage import (
    DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID,
    data_annotation_asset_tree_path,
    read_data_annotation_asset_tree_snapshot,
    save_data_annotation_asset_tree_snapshot,
)
from backend.core.fanxiu.game.macro_annotation import _recognize_data_annotation_ocr_frame


POSITIVE_FILENAME = "0620.png"
NEGATIVE_FILENAMES = ("0659.png", "0177.jpg", "0556.png", "0544.png")
IDENTITY_SPECS = (
    {
        "id": "shape-620-xianlv-identity-20260825",
        "title": "仙侣",
        "ocrText": "仙侣",
        "x": 248 / 900,
        "y": 309 / 1600,
        "w": 301 / 900,
        "h": 151 / 1600,
    },
    {
        "id": "shape-620-level-up-identity-20260825",
        "title": "升级",
        "ocrText": "升级",
        "x": 398 / 900,
        "y": 432 / 1600,
        "w": 222 / 900,
        "h": 136 / 1600,
    },
)


def _walk(nodes: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for node in nodes:
        yield node
        children = node.get("children")
        if isinstance(children, list):
            yield from _walk(item for item in children if isinstance(item, dict))


def _scene620(tree: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [node for node in _walk(tree) if node.get("type") == "image" and node.get("filename") == POSITIVE_FILENAME]
    if len(matches) != 1:
        raise RuntimeError(f"#620 节点数量异常：{len(matches)}")
    return matches[0]


def _identity_shape(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": spec["id"],
        "kind": "shape",
        "title": spec["title"],
        "description": "#620 仙侣升级结算专属标题锚点；2026-08-25 由真实 0620.png 正帧与 #659/通用结算负帧离线 OCR 双遍复核。",
        "floating": False,
        "jitterEnabled": False,
        "jitterRadius": 4,
        "isSceneIdentity": True,
        "sceneIdentityRole": "required",
        "sceneJumpTarget": "",
        "imageMatchRole": "off",
        "pixelTolerance": 5,
        "ocrMatchRole": "required",
        "ocrEnabled": True,
        "ocrText": spec["ocrText"],
        "ocrMatchMode": "contains",
        "ocrMinConfidence": 0,
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
        "locked": False,
        "ocrMaskMode": "inherit-envelope",
        "ocrMask": None,
        "loadDirection": "none",
    }


def refine_scene620_identity(tree: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result = copy.deepcopy(tree)
    scene = _scene620(result)
    shapes = [shape for shape in scene.get("shapes") or [] if isinstance(shape, dict)]
    action_matches = [shape for shape in shapes if shape.get("id") == "shape-620-continue"]
    if len(action_matches) != 1:
        raise RuntimeError(f"#620[继续] 数量异常：{len(action_matches)}")
    action = action_matches[0]
    removed_jump_targets = str(action.get("sceneJumpTarget") or "")
    action.update(
        {
            "description": "仙侣升级结算页原生「点击屏幕继续」动作；不参与场景身份识别。",
            "isSceneIdentity": False,
            "sceneIdentityRole": "off",
            "sceneJumpTarget": "",
        }
    )
    identity_ids = {spec["id"] for spec in IDENTITY_SPECS}
    shapes = [shape for shape in shapes if shape.get("id") not in identity_ids]
    shapes.extend(_identity_shape(spec) for spec in IDENTITY_SPECS)
    scene["shapes"] = shapes
    validate_scene620_identity(result)
    return result, {
        "scene": 620,
        "identity": [spec["ocrText"] for spec in IDENTITY_SPECS],
        "action_identity_disabled": True,
        "removed_contaminated_jump_targets": removed_jump_targets,
    }


def validate_scene620_identity(tree: list[dict[str, Any]]) -> None:
    scene = _scene620(tree)
    shapes = [shape for shape in scene.get("shapes") or [] if isinstance(shape, dict)]
    by_id = {str(shape.get("id") or ""): shape for shape in shapes}
    action = by_id.get("shape-620-continue")
    if not action or action.get("isSceneIdentity") or str(action.get("sceneIdentityRole") or "") != "off":
        raise RuntimeError("#620 通用继续动作不得作为场景身份")
    if str(action.get("sceneJumpTarget") or ""):
        raise RuntimeError("#620 通用继续动作仍含仙宴抢认污染跳频")
    for spec in IDENTITY_SPECS:
        shape = by_id.get(spec["id"])
        if not shape:
            raise RuntimeError(f"#620 缺少身份 shape：{spec['id']}")
        if not shape.get("isSceneIdentity") or shape.get("sceneIdentityRole") != "required":
            raise RuntimeError(f"#620 身份 shape 未设为 required：{spec['id']}")
        if shape.get("ocrMatchRole") != "required" or shape.get("ocrText") != spec["ocrText"]:
            raise RuntimeError(f"#620 身份 OCR 契约错误：{spec['id']}")


def _data_url(path: Path) -> str:
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _ocr_fragments(path: Path) -> list[dict[str, Any]]:
    response = _recognize_data_annotation_ocr_frame(_data_url(path))
    return group_ocr_tokens([token.model_dump() for token in response.tokens])


def audit_saved_frames(image_dir: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = (POSITIVE_FILENAME, *NEGATIVE_FILENAMES)
    fragments_by_frame = {name: _ocr_fragments(image_dir / name) for name in frames}
    evidence: dict[str, Any] = {}
    for name, fragments in fragments_by_frame.items():
        texts = [str(fragment.get("text") or "") for fragment in fragments]
        hits = {spec["ocrText"]: any(spec["ocrText"] in text for text in texts) for spec in IDENTITY_SPECS}
        evidence[name] = {"hits": hits, "fragments": fragments}
    if not all(evidence[POSITIVE_FILENAME]["hits"].values()):
        raise RuntimeError("真实 #620 正帧未同时命中「仙侣」「升级」")
    for name in NEGATIVE_FILENAMES:
        if all(evidence[name]["hits"].values()):
            raise RuntimeError(f"负帧 {name} 同时命中 #620 双身份锚点")

    frame = Image.open(image_dir / POSITIVE_FILENAME).convert("RGB")
    pass1 = frame.copy()
    draw1 = ImageDraw.Draw(pass1)
    for fragment in fragments_by_frame[POSITIVE_FILENAME]:
        box = tuple(float(fragment.get(key) or 0) for key in ("x", "y", "w", "h"))
        x, y, w, h = box
        draw1.rectangle((x, y, x + w, y + h), outline=(0, 170, 255), width=3)
    pass1_path = output_dir / "0620-identity-pass1-ocr-boxes.png"
    pass1.save(pass1_path)

    pass2 = frame.copy()
    draw2 = ImageDraw.Draw(pass2)
    for spec in IDENTITY_SPECS:
        x = spec["x"] * frame.width
        y = spec["y"] * frame.height
        w = spec["w"] * frame.width
        h = spec["h"] * frame.height
        draw2.rectangle((x, y, x + w, y + h), outline=(0, 220, 80), width=5)
    pass2_path = output_dir / "0620-identity-pass2-final-roi.png"
    pass2.save(pass2_path)
    report = {
        "positive": POSITIVE_FILENAME,
        "negatives": list(NEGATIVE_FILENAMES),
        "evidence": evidence,
        "pass1_overlay": str(pass1_path),
        "pass2_overlay": str(pass2_path),
    }
    (output_dir / "scene620-ocr-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair #620 identity using saved positive/negative frames")
    parser.add_argument("--entry-id", default=DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = data_annotation_asset_tree_path(args.entry_id)
    snapshot = read_data_annotation_asset_tree_snapshot(path)
    evidence_dir = Path(tempfile.gettempdir()) / "codeyun" / "fanxiu-layer1-refactor" / "scene620-identity"
    audit = audit_saved_frames(path.parent / "images", evidence_dir)
    tree, report = refine_scene620_identity(snapshot.tree)
    report.update(
        {
            "path": str(path),
            "before_revision": snapshot.revision,
            "applied": False,
            "pass1_overlay": audit["pass1_overlay"],
            "pass2_overlay": audit["pass2_overlay"],
        }
    )
    if args.apply:
        backup = evidence_dir / f"asset-tree.{snapshot.revision[:16]}.before.json"

        def backup_before_write() -> None:
            shutil.copy2(path, backup)

        saved = save_data_annotation_asset_tree_snapshot(
            path,
            tree,
            entry_id=args.entry_id,
            expected_revision=snapshot.revision,
            before_write=backup_before_write,
        )
        validate_scene620_identity(saved.tree)
        report.update(
            {
                "applied": True,
                "backup": str(backup),
                "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
                "after_revision": saved.revision,
            }
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
