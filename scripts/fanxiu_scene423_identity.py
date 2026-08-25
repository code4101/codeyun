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


POSITIVE_FILENAME = "0423.png"
NEGATIVE_FILENAMES = ("0659.png", "0620.png", "0177.jpg", "0556.png", "0544.png")
IDENTITY_TEXT = "仙宴圆满结束"
IDENTITY_SPEC = {
    "id": "shape-423-xianyan-complete-identity-20260825",
    "title": IDENTITY_TEXT,
    "ocrText": IDENTITY_TEXT,
    "x": 159 / 900,
    "y": 343 / 1600,
    "w": 580 / 900,
    "h": 108 / 1600,
}


def _walk(nodes: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for node in nodes:
        yield node
        children = node.get("children")
        if isinstance(children, list):
            yield from _walk(item for item in children if isinstance(item, dict))


def _scene423(tree: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [node for node in _walk(tree) if node.get("type") == "image" and node.get("filename") == POSITIVE_FILENAME]
    if len(matches) != 1:
        raise RuntimeError(f"#423 节点数量异常：{len(matches)}")
    return matches[0]


def _identity_shape() -> dict[str, Any]:
    spec = IDENTITY_SPEC
    return {
        "id": spec["id"],
        "kind": "shape",
        "title": spec["title"],
        "description": "#423 领奖后的仙宴圆满结果专属标题；真实 #423 正帧及 #659/#620/通用结算负帧离线 OCR 双遍复核。",
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


def refine_scene423_identity(tree: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result = copy.deepcopy(tree)
    scene = _scene423(result)
    scene["title"] = "仙宴圆满结束"
    scene["layer"] = 2
    shapes = [shape for shape in scene.get("shapes") or [] if isinstance(shape, dict)]
    old_identity_ids = {
        str(shape.get("id") or "")
        for shape in shapes
        if bool(shape.get("isSceneIdentity"))
    }
    actions = [shape for shape in shapes if shape.get("title") == "继续"]
    if len(actions) != 1:
        raise RuntimeError(f"#423[继续] 数量异常：{len(actions)}")
    action = actions[0]
    removed_jump_targets = str(action.get("sceneJumpTarget") or "")
    action.update(
        {
            "description": "#423 仙宴圆满结果底部「点击屏幕继续」动作；仅由仙宴局部事务消费，不参与身份。",
            "isSceneIdentity": False,
            "sceneIdentityRole": "off",
            "sceneJumpTarget": "",
            "ocrText": "点击屏幕继续",
            "ocrMatchRole": "required",
            "ocrEnabled": True,
        }
    )
    shapes = [shape for shape in shapes if str(shape.get("id") or "") != IDENTITY_SPEC["id"]]
    shapes.append(_identity_shape())
    scene["shapes"] = shapes
    validate_scene423_identity(result)
    return result, {
        "scene": 423,
        "layer": 2,
        "identity": IDENTITY_TEXT,
        "replaced_identity_ids": sorted(old_identity_ids),
        "removed_contaminated_jump_targets": removed_jump_targets,
    }


def validate_scene423_identity(tree: list[dict[str, Any]]) -> None:
    scene = _scene423(tree)
    if int(scene.get("layer") or 0) != 2:
        raise RuntimeError("#423 必须保持 Layer2 正式场景")
    shapes = [shape for shape in scene.get("shapes") or [] if isinstance(shape, dict)]
    identities = [shape for shape in shapes if bool(shape.get("isSceneIdentity"))]
    if len(identities) != 1 or identities[0].get("id") != IDENTITY_SPEC["id"]:
        raise RuntimeError("#423 必须且只能使用专属标题身份")
    identity = identities[0]
    if identity.get("ocrText") != IDENTITY_TEXT or identity.get("ocrMatchRole") != "required":
        raise RuntimeError("#423 专属 OCR 身份契约错误")
    actions = [shape for shape in shapes if shape.get("title") == "继续"]
    if len(actions) != 1 or actions[0].get("isSceneIdentity"):
        raise RuntimeError("#423 通用继续动作不得作为身份")
    if str(actions[0].get("sceneJumpTarget") or ""):
        raise RuntimeError("#423 通用继续动作仍含跨业务误识别跳频")


def _data_url(path: Path) -> str:
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _ocr_fragments(path: Path) -> list[dict[str, Any]]:
    response = _recognize_data_annotation_ocr_frame(_data_url(path))
    return group_ocr_tokens([token.model_dump() for token in response.tokens])


def audit_saved_frames(image_dir: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = (POSITIVE_FILENAME, *NEGATIVE_FILENAMES)
    fragments = {name: _ocr_fragments(image_dir / name) for name in frames}
    hits = {
        name: any(IDENTITY_TEXT in str(fragment.get("text") or "") for fragment in rows)
        for name, rows in fragments.items()
    }
    if not hits[POSITIVE_FILENAME]:
        raise RuntimeError("#423 正帧未命中专属标题")
    false_positives = [name for name in NEGATIVE_FILENAMES if hits[name]]
    if false_positives:
        raise RuntimeError(f"#423 专属标题误命中负帧：{false_positives}")

    frame = Image.open(image_dir / POSITIVE_FILENAME).convert("RGB")
    pass1 = frame.copy()
    draw1 = ImageDraw.Draw(pass1)
    for fragment in fragments[POSITIVE_FILENAME]:
        x = float(fragment.get("x") or 0)
        y = float(fragment.get("y") or 0)
        w = float(fragment.get("w") or 0)
        h = float(fragment.get("h") or 0)
        draw1.rectangle((x, y, x + w, y + h), outline=(0, 170, 255), width=3)
    pass1_path = output_dir / "0423-identity-pass1-ocr-boxes.png"
    pass1.save(pass1_path)

    pass2 = frame.copy()
    draw2 = ImageDraw.Draw(pass2)
    x = IDENTITY_SPEC["x"] * frame.width
    y = IDENTITY_SPEC["y"] * frame.height
    w = IDENTITY_SPEC["w"] * frame.width
    h = IDENTITY_SPEC["h"] * frame.height
    draw2.rectangle((x, y, x + w, y + h), outline=(0, 220, 80), width=5)
    pass2_path = output_dir / "0423-identity-pass2-final-roi.png"
    pass2.save(pass2_path)
    report = {
        "positive": POSITIVE_FILENAME,
        "negatives": list(NEGATIVE_FILENAMES),
        "hits": hits,
        "fragments": fragments,
        "pass1_overlay": str(pass1_path),
        "pass2_overlay": str(pass2_path),
    }
    (output_dir / "scene423-ocr-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair #423 generic continue identity")
    parser.add_argument("--entry-id", default=DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = data_annotation_asset_tree_path(args.entry_id)
    snapshot = read_data_annotation_asset_tree_snapshot(path)
    evidence_dir = Path(tempfile.gettempdir()) / "codeyun" / "fanxiu-layer1-refactor" / "scene423-identity"
    audit = audit_saved_frames(path.parent / "images", evidence_dir)
    tree, report = refine_scene423_identity(snapshot.tree)
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
        validate_scene423_identity(saved.tree)
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
