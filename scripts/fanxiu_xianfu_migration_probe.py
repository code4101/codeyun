from __future__ import annotations

import argparse
import base64
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
import sys
import re
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.game.macro_annotation import _recognize_data_annotation_ocr_frame
from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
from backend.core.fanxiu.runtime.mumu_control import screencap_mumu_adb_png
from backend.core.temp_paths import codeyun_temp_root


DEFAULT_OLD_XIANFU_ROOT = Path(r"D:\home\chenkunze\data\m2508凡修\mainwin\仙府")
DEFAULT_SCREENSHOT_DIR = Path(r"D:\home\chenkunze\data\m2508凡修\mainwin\截图")
DEFAULT_OLD_CROP = (0.0, 42.0, 476.0, 1037.0)

SNAP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "寻仙台": ("寻仙台",),
    "离开": ("离开",),
    "世界": ("世界",),
    "仙侣居": ("仙侣居",),
    "本命金身": ("本命金身",),
    "寻访": ("寻访",),
    "寻仙台标题": ("寻仙台",),
    "领悟绝技": ("领悟绝技",),
    "状态": ("免费", "倒计时", "重新", "抽取后"),
    "价格": ("免费",),
    "免费提示": ("免费",),
    "大奖记录": ("大奖记录",),
    "退出": ("退出",),
    "半价": ("半价",),
    "继续": ("继续",),
    "关闭": ("关闭",),
}

XIANFU_REQUIRED_IMAGES: dict[int, dict[str, Any]] = {
    171: {
        "title": "仙府主页",
        "filename": "0171.png",
        "identity": ("仙府功能区",),
        "shapes": {
            "仙府功能区": {"ocr": "仙侣居", "identity": True},
            "寻仙台": {"jump": "172"},
            "离开": {"jump": "34"},
        },
    },
    172: {
        "title": "寻仙台",
        "filename": "0172.png",
        "identity": ("寻仙台",),
        "shapes": {
            "寻仙台": {"ocr": "寻仙台", "identity": True},
            "寻访": {"jump": "173"},
            "领悟绝技": {},
        },
    },
    173: {
        "title": "仙侣寻访",
        "filename": "0173.png",
        "identity": ("切换心愿",),
        "shapes": {
            "切换心愿": {"ocr": "切换心愿", "identity": True},
            "绝品仙侣": {"jump": "174", "ocr": "绝品仙侣"},
            "寻访一次": {"ocr": "寻访一次"},
            "返回": {"jump": "172"},
        },
    },
    174: {
        "title": "绝品仙侣",
        "filename": "0174.png",
        "identity": ("绝品仙侣标识",),
        "shapes": {
            "绝品仙侣标识": {"ocr": "绝品仙侣", "identity": True},
            "状态": {"ocr": "免费"},
            "价格": {},
            "免费提示": {"ocr": "免费"},
            "寻访": {"ocr": "寻访一次"},
            "大奖记录": {"ocr": "大奖记录"},
            "菜单": {"ocr": "绝品仙侣"},
            "退出": {"jump": "172"},
        },
    },
}

XIANFU_OPTIONAL_IMAGES: dict[int, dict[str, Any]] = {
    175: {
        "title": "继续寻访",
        "filename": "0175.png",
        "identity": ("关闭",),
        "shapes": {
            "关闭": {"jump": "174", "ocr": "关闭", "identity": True},
            "半价": {"ocr": "半价"},
            "继续": {"jump": "175", "ocr": "继续"},
        },
    },
}


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def as_xyxy(self) -> tuple[float, float, float, float]:
        return self.x, self.y, self.x + self.w, self.y + self.h

    def normalized(self, width: int, height: int) -> dict[str, float]:
        return {
            "x": max(0.0, min(1.0, self.x / max(1, width))),
            "y": max(0.0, min(1.0, self.y / max(1, height))),
            "w": max(0.0, min(1.0, self.w / max(1, width))),
            "h": max(0.0, min(1.0, self.h / max(1, height))),
        }


def _parse_crop(value: str) -> tuple[float, float, float, float]:
    parts = [float(item.strip()) for item in value.split(",") if item.strip()]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--old-crop must be x0,y0,x1,y1")
    x0, y0, x1, y1 = parts
    if x1 <= x0 or y1 <= y0:
        raise argparse.ArgumentTypeError("--old-crop must satisfy x1>x0 and y1>y0")
    return x0, y0, x1, y1


def _label_text(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict):
        return str(payload.get("text") or payload.get("label") or text).strip()
    return text


def _load_old_shapes(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    shapes: list[dict[str, Any]] = []
    for shape in data.get("shapes") or []:
        points = shape.get("points") or []
        if len(points) < 2:
            continue
        (x1, y1), (x2, y2) = points[:2]
        left, right = sorted((float(x1), float(x2)))
        top, bottom = sorted((float(y1), float(y2)))
        shapes.append({
            "label": _label_text(shape.get("label")),
            "old_box": Box(left, top, right - left, bottom - top),
            "raw": shape,
        })
    return shapes


def _project_box(box: Box, old_crop: tuple[float, float, float, float], new_size: tuple[int, int]) -> Box:
    x0, y0, x1, y1 = old_crop
    new_w, new_h = new_size
    sx = new_w / (x1 - x0)
    sy = new_h / (y1 - y0)
    return Box(
        x=(box.x - x0) * sx,
        y=(box.y - y0) * sy,
        w=box.w * sx,
        h=box.h * sy,
    )


def _image_to_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _capture_frame(output_dir: Path) -> tuple[Path, dict[str, Any]]:
    data, meta = screencap_mumu_adb_png()
    path = output_dir / f"current_adb_{time.strftime('%Y%m%d_%H%M%S')}.png"
    path.write_bytes(data)
    return path, meta


def _ocr_fragments(frame_path: Path) -> list[dict[str, Any]]:
    response = _recognize_data_annotation_ocr_frame(_image_to_data_url(frame_path))
    tokens = [token.model_dump() for token in response.tokens]
    return group_ocr_tokens(tokens)


def _shape_title(shape: dict[str, Any]) -> str:
    return str(shape.get("title") or shape.get("label") or "").strip()


def _image_number(node: dict[str, Any]) -> int | None:
    for key in ("number", "filename", "fileName", "title", "id"):
        text = str(node.get(key) or "")
        match = re.search(r"(\d{4})", text)
        if match:
            return int(match.group(1))
    return None


def _iter_images(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []

    def visit(items: list[dict[str, Any]]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "image":
                images.append(item)
            children = item.get("children")
            if isinstance(children, list):
                visit(children)

    visit(nodes)
    return images


def _ocr_text_in_shape(fragments: list[dict[str, Any]], shape: dict[str, Any], width: int, height: int, *, padding: int = 18) -> str:
    x1 = float(shape.get("x") or 0) * width - padding
    y1 = float(shape.get("y") or 0) * height - padding
    x2 = (float(shape.get("x") or 0) + float(shape.get("w") or 0)) * width + padding
    y2 = (float(shape.get("y") or 0) + float(shape.get("h") or 0)) * height + padding
    texts: list[str] = []
    for fragment in fragments:
        lx = float(fragment.get("x") or 0)
        ly = float(fragment.get("y") or 0)
        lw = float(fragment.get("w") or 0)
        lh = float(fragment.get("h") or 0)
        cx = lx + lw / 2
        cy = ly + lh / 2
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            texts.append(str(fragment.get("text") or ""))
    return "".join(texts)


def audit_xianfu_assets(
    *,
    asset_tree_path: Path,
    screenshot_dir: Path = DEFAULT_SCREENSHOT_DIR,
    output_dir: Path | None = None,
    audit_ocr: bool = False,
) -> dict[str, Any]:
    tree = json.loads(asset_tree_path.read_text(encoding="utf-8"))
    if not isinstance(tree, list):
        raise RuntimeError("资产树格式非法：根节点不是列表")
    images_by_number: dict[int, dict[str, Any]] = {}
    for image in _iter_images(tree):
        number = _image_number(image)
        if number is not None:
            images_by_number[number] = image

    output_dir = output_dir or codeyun_temp_root("fanxiu_xianfu_audit")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    all_ok = True

    def check_one(number: int, spec: dict[str, Any], *, required: bool) -> None:
        nonlocal all_ok
        image = images_by_number.get(number)
        row: dict[str, Any] = {
            "number": number,
            "title": spec.get("title"),
            "filename": spec.get("filename"),
            "required": required,
            "present": image is not None,
            "ok": True,
            "issues": [],
            "warnings": [],
            "shape_count": 0,
            "ocr": {},
        }
        if image is None:
            if required:
                row["ok"] = False
                row["issues"].append("资产树缺少图片")
                all_ok = False
            rows.append(row)
            return
        filename = str(image.get("filename") or image.get("fileName") or "")
        if filename != spec.get("filename"):
            row["ok"] = False
            row["issues"].append(f"filename 不一致：{filename}")
        screenshot = screenshot_dir / filename
        row["screenshot_path"] = str(screenshot)
        if not screenshot.is_file():
            row["ok"] = False
            row["issues"].append("截图文件缺失")
        shapes = [_shape for _shape in image.get("shapes") or [] if isinstance(_shape, dict)]
        row["shape_count"] = len(shapes)
        shapes_by_title = {_shape_title(shape): shape for shape in shapes}
        identity_titles = [title for title, shape in shapes_by_title.items() if bool(shape.get("isSceneIdentity"))]
        for identity in spec.get("identity") or ():
            if identity not in identity_titles:
                row["ok"] = False
                row["issues"].append(f"缺少场景身份：{identity}")
        for title, shape_spec in (spec.get("shapes") or {}).items():
            shape = shapes_by_title.get(title)
            if shape is None:
                row["ok"] = False
                row["issues"].append(f"缺少 shape：{title}")
                continue
            expected_jump = shape_spec.get("jump")
            if expected_jump is not None and str(shape.get("sceneJumpTarget") or "") != str(expected_jump):
                row["ok"] = False
                row["issues"].append(f"{title}.sceneJumpTarget 应为 {expected_jump}，实际 {shape.get('sceneJumpTarget')!r}")
            if bool(shape_spec.get("identity")) and not bool(shape.get("isSceneIdentity")):
                row["ok"] = False
                row["issues"].append(f"{title} 应标记为场景身份")
            expected_ocr = str(shape_spec.get("ocr") or "")
            if expected_ocr and str(shape.get("ocrText") or "") != expected_ocr:
                row["ok"] = False
                row["issues"].append(f"{title}.ocrText 应为 {expected_ocr}，实际 {shape.get('ocrText')!r}")
        if audit_ocr and screenshot.is_file():
            with Image.open(screenshot) as frame:
                width, height = frame.size
            fragments = _ocr_fragments(screenshot)
            for title, shape_spec in (spec.get("shapes") or {}).items():
                expected_ocr = str(shape_spec.get("ocr") or "")
                if not expected_ocr or title not in shapes_by_title:
                    continue
                text = _ocr_text_in_shape(fragments, shapes_by_title[title], width, height)
                row["ocr"][title] = text
                if expected_ocr not in text:
                    role = str(shapes_by_title[title].get("ocrMatchRole") or "")
                    if role == "required" or bool(shapes_by_title[title].get("isSceneIdentity")):
                        row["ok"] = False
                        row["issues"].append(f"{title} OCR 未命中 {expected_ocr}：{text!r}")
                    else:
                        row["warnings"].append(f"{title} 可选 OCR 未命中 {expected_ocr}：{text!r}")
        if not row["ok"] and required:
            all_ok = False
        rows.append(row)

    for number, spec in XIANFU_REQUIRED_IMAGES.items():
        check_one(number, spec, required=True)
    for number, spec in XIANFU_OPTIONAL_IMAGES.items():
        check_one(number, spec, required=False)

    result = {
        "ok": all_ok,
        "asset_tree_path": str(asset_tree_path),
        "screenshot_dir": str(screenshot_dir),
        "audit_ocr": audit_ocr,
        "rows": rows,
        "output_json": str(output_dir / "xianfu_asset_audit.json"),
    }
    Path(result["output_json"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _ocr_matches_for_label(label: str, fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keywords = SNAP_KEYWORDS.get(label, (label,))
    matches: list[dict[str, Any]] = []
    for fragment in fragments:
        text = str(fragment.get("text") or "")
        if any(keyword and keyword in text for keyword in keywords):
            matches.append(fragment)
    return matches


def _nearest_ocr_box(projected: Box, matches: list[dict[str, Any]], *, max_distance: float = 180.0) -> Box | None:
    best: tuple[float, dict[str, Any]] | None = None
    for line in matches:
        box = Box(float(line.get("x") or 0), float(line.get("y") or 0), float(line.get("w") or 0), float(line.get("h") or 0))
        distance = ((box.cx - projected.cx) ** 2 + (box.cy - projected.cy) ** 2) ** 0.5
        if best is None or distance < best[0]:
            best = (distance, line)
    if best is None or best[0] > max_distance:
        return None
    line = best[1]
    return Box(float(line.get("x") or 0), float(line.get("y") or 0), float(line.get("w") or 0), float(line.get("h") or 0))


def _load_font(size: int = 22) -> ImageFont.ImageFont | None:
    for name in ("simhei.ttf", "msyh.ttc", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return None


def _draw_box(draw: ImageDraw.ImageDraw, box: Box, color: tuple[int, int, int], label: str, font: ImageFont.ImageFont | None) -> None:
    draw.rectangle(box.as_xyxy(), outline=color, width=4)
    draw.text((box.x, max(0, box.y - 28)), label, fill=color, font=font)


def _draw_ocr_fragment(draw: ImageDraw.ImageDraw, fragment: dict[str, Any], font: ImageFont.ImageFont | None) -> None:
    box = Box(float(fragment.get("x") or 0), float(fragment.get("y") or 0), float(fragment.get("w") or 0), float(fragment.get("h") or 0))
    text = str(fragment.get("text") or "")
    color = (30, 144, 255)
    draw.rectangle(box.as_xyxy(), outline=color, width=2)
    if text:
        draw.text((box.x, max(0, box.y - 22)), text, fill=color, font=font)


def build_candidates(
    *,
    page: str,
    old_root: Path,
    frame_path: Path,
    old_crop: tuple[float, float, float, float],
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    old_json = old_root / f"{page}.json"
    if not old_json.exists():
        raise FileNotFoundError(f"old labelme file not found: {old_json}")
    frame = Image.open(frame_path).convert("RGB")
    new_size = frame.size
    fragments = _ocr_fragments(frame_path)
    candidates: list[dict[str, Any]] = []
    for item in _load_old_shapes(old_json):
        label = str(item["label"] or "")
        old_box: Box = item["old_box"]
        projected = _project_box(old_box, old_crop, new_size)
        matches = _ocr_matches_for_label(label, fragments)
        snapped = _nearest_ocr_box(projected, matches)
        final = snapped or projected
        source = "ocr_snap" if snapped else "projected"
        candidates.append({
            "page": page,
            "label": label,
            "source": source,
            "old_box": old_box.__dict__,
            "projected_box": projected.__dict__,
            "final_box": final.__dict__,
            "normalized": final.normalized(*new_size),
            "ocr_matches": matches,
        })

    annotated = frame.copy()
    draw = ImageDraw.Draw(annotated)
    font = _load_font()
    for fragment in fragments:
        _draw_ocr_fragment(draw, fragment, font)
    for candidate in candidates:
        projected = Box(**candidate["projected_box"])
        final = Box(**candidate["final_box"])
        label = str(candidate["label"])
        _draw_box(draw, projected, (255, 0, 0), f"old->{label}", font)
        if candidate["source"] == "ocr_snap":
            _draw_box(draw, final, (0, 255, 0), f"ocr->{label}", font)
    annotated_path = output_dir / f"{page}_candidate_overlay.jpg"
    annotated.save(annotated_path, quality=92)

    result = {
        "page": page,
        "old_root": str(old_root),
        "old_crop": old_crop,
        "frame_path": str(frame_path),
        "new_size": {"width": new_size[0], "height": new_size[1]},
        "annotated_path": str(annotated_path),
        "ocr_fragments": fragments,
        "candidates": candidates,
        "ocr_verified": all(candidate["source"] == "ocr_snap" for candidate in candidates),
        "unverified_labels": [candidate["label"] for candidate in candidates if candidate["source"] != "ocr_snap"],
    }
    (output_dir / f"{page}_candidates.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _shape_from_candidate(
    candidate: dict[str, Any],
    *,
    stamp: str,
    role: str,
    ocr_text: str = "",
    scene_jump_target: str = "",
    is_scene_identity: bool = False,
) -> dict[str, Any]:
    label = str(candidate.get("label") or "")
    box = candidate.get("normalized") if isinstance(candidate.get("normalized"), dict) else {}
    ocr_enabled = bool(ocr_text)
    return {
        "id": f"shape-xianfu-{stamp}-{label}",
        "kind": "shape",
        "title": label,
        "description": "",
        "floating": False,
        "jitterEnabled": False,
        "jitterRadius": 4,
        "isSceneIdentity": bool(is_scene_identity),
        "sceneIdentityRole": "required" if is_scene_identity else "off",
        "sceneJumpTarget": scene_jump_target,
        "loadDirection": "none",
        "imageMatchRole": "off",
        "pixelTolerance": 5,
        "ocrMatchRole": role,
        "ocrEnabled": ocr_enabled,
        "ocrText": ocr_text,
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
        "x": float(box.get("x") or 0),
        "y": float(box.get("y") or 0),
        "w": float(box.get("w") or 0),
        "h": float(box.get("h") or 0),
        "children": [],
    }


def _find_folder(items: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "folder" and str(item.get("title") or "") == title:
            return item
        children = item.get("children")
        if isinstance(children, list):
            found = _find_folder(children, title)
            if found is not None:
                return found
    return None


def _remove_image_by_filename(items: list[dict[str, Any]], filename: str) -> bool:
    removed = False
    kept: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and item.get("type") == "image" and str(item.get("filename") or "") == filename:
            removed = True
            continue
        children = item.get("children") if isinstance(item, dict) else None
        if isinstance(children, list) and _remove_image_by_filename(children, filename):
            removed = True
        kept.append(item)
    if removed:
        items[:] = kept
    return removed


def install_continue_visit_image(
    *,
    result: dict[str, Any],
    asset_tree_path: Path,
    screenshot_dir: Path,
    target_number: int = 175,
    allow_unverified: bool = False,
) -> dict[str, Any]:
    if result.get("page") != "继续寻访":
        raise RuntimeError("当前只支持安装「继续寻访」为 #175")
    if not allow_unverified and not bool(result.get("ocr_verified")):
        labels = ", ".join(str(item) for item in result.get("unverified_labels") or [])
        raise RuntimeError(f"OCR 未全部复核，拒绝写入资产树：{labels}")
    candidates = {str(item.get("label") or ""): item for item in result.get("candidates") or [] if isinstance(item, dict)}
    missing = [label for label in ("半价", "继续", "关闭") if label not in candidates]
    if missing:
        raise RuntimeError(f"缺少继续寻访候选：{', '.join(missing)}")

    frame_path = Path(str(result.get("frame_path") or ""))
    if not frame_path.is_file():
        raise FileNotFoundError(f"frame not found: {frame_path}")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    target_filename = f"{int(target_number):04d}.png"
    target_image_path = screenshot_dir / target_filename

    tree = json.loads(asset_tree_path.read_text(encoding="utf-8"))
    if not isinstance(tree, list):
        raise RuntimeError("资产树格式非法：根节点不是列表")
    folder = _find_folder(tree, "仙府")
    if folder is None:
        raise RuntimeError("资产树缺少「仙府」文件夹")
    children = folder.setdefault("children", [])
    if not isinstance(children, list):
        raise RuntimeError("「仙府」文件夹 children 格式非法")

    with Image.open(frame_path) as image:
        width, height = image.size
    stamp = str(int(time.time() * 1000))
    image_node = {
        "id": f"image-xianfu-{int(target_number)}-{stamp}",
        "type": "image",
        "title": "继续寻访",
        "filename": target_filename,
        "width": width,
        "height": height,
        "shapes": [
            _shape_from_candidate(candidates["关闭"], stamp=stamp, role="required", ocr_text="关闭", scene_jump_target="174", is_scene_identity=True),
            _shape_from_candidate(candidates["半价"], stamp=stamp, role="required", ocr_text="半价"),
            _shape_from_candidate(candidates["继续"], stamp=stamp, role="optional", ocr_text="继续", scene_jump_target=str(target_number)),
        ],
        "children": [],
    }

    backup_path = asset_tree_path.with_name(asset_tree_path.name + f".before-install-xianfu-{target_number}-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(asset_tree_path, backup_path)
    shutil.copy2(frame_path, target_image_path)
    _remove_image_by_filename(tree, target_filename)
    children.append(image_node)
    asset_tree_path.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "asset_tree_path": str(asset_tree_path),
        "backup_path": str(backup_path),
        "screenshot_path": str(target_image_path),
        "image": {"title": image_node["title"], "filename": image_node["filename"], "shape_count": len(image_node["shapes"])},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Draft old Xianfu labelme boxes into CodeYun data-annotation coordinates.")
    parser.add_argument("--old-root", type=Path, default=DEFAULT_OLD_XIANFU_ROOT)
    parser.add_argument("--page", default="主页")
    parser.add_argument("--frame", type=Path, default=None)
    parser.add_argument("--capture-current", action="store_true")
    parser.add_argument("--old-crop", type=_parse_crop, default=DEFAULT_OLD_CROP)
    parser.add_argument("--audit-assets", action="store_true", help="只审计当前仙府资产树 #171-#175 的结构和截图完整性")
    parser.add_argument("--audit-ocr", action="store_true", help="审计时额外对截图 OCR 文本做复核")
    parser.add_argument("--install-asset-tree", type=Path, default=None)
    parser.add_argument("--asset-tree", type=Path, default=None)
    parser.add_argument("--screenshot-dir", type=Path, default=DEFAULT_SCREENSHOT_DIR)
    parser.add_argument("--target-number", type=int, default=175)
    parser.add_argument("--allow-unverified-install", action="store_true")
    args = parser.parse_args()

    output_dir = codeyun_temp_root("fanxiu_xianfu_migration")
    if args.audit_assets:
        asset_tree = args.asset_tree or args.install_asset_tree
        if asset_tree is None:
            raise SystemExit("--audit-assets 需要提供 --asset-tree")
        result = audit_xianfu_assets(
            asset_tree_path=asset_tree,
            screenshot_dir=args.screenshot_dir,
            output_dir=output_dir,
            audit_ocr=bool(args.audit_ocr),
        )
        print(json.dumps(
            {
                "ok": result["ok"],
                "asset_tree_path": result["asset_tree_path"],
                "screenshot_dir": result["screenshot_dir"],
                "audit_ocr": result["audit_ocr"],
                "required": [
                    {"number": row["number"], "ok": row["ok"], "issues": row["issues"], "warnings": row.get("warnings") or []}
                    for row in result["rows"]
                    if row.get("required")
                ],
                "optional": [
                    {"number": row["number"], "present": row["present"], "ok": row["ok"], "issues": row["issues"], "warnings": row.get("warnings") or []}
                    for row in result["rows"]
                    if not row.get("required")
                ],
                "output_json": result["output_json"],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return

    frame_path = args.frame
    capture_meta: dict[str, Any] = {}
    if args.capture_current or frame_path is None:
        frame_path, capture_meta = _capture_frame(output_dir)
    assert frame_path is not None

    result = build_candidates(
        page=args.page,
        old_root=args.old_root,
        frame_path=frame_path,
        old_crop=args.old_crop,
        output_dir=output_dir,
    )
    if capture_meta:
        result["capture_meta"] = capture_meta
        Path(output_dir, f"{args.page}_candidates.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    installed: dict[str, Any] | None = None
    if args.install_asset_tree is not None:
        installed = install_continue_visit_image(
            result=result,
            asset_tree_path=args.install_asset_tree,
            screenshot_dir=args.screenshot_dir,
            target_number=args.target_number,
            allow_unverified=args.allow_unverified_install,
        )
    print(json.dumps(
        {
            "page": result["page"],
            "frame_path": result["frame_path"],
            "annotated_path": result["annotated_path"],
            "candidate_count": len(result["candidates"]),
            "ocr_verified": result["ocr_verified"],
            "unverified_labels": result["unverified_labels"],
            "output_json": str(output_dir / f"{args.page}_candidates.json"),
            "capture_meta": capture_meta,
            "installed": installed,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
