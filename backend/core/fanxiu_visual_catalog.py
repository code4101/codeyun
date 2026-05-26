from __future__ import annotations

import csv
import html
import io
import json
import math
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import imagehash
from PIL import Image, ImageChops, ImageOps
from pyxllib.file.game_assets import load_unity_environment

from backend.core.fanxiu_apk_static import resolve_fanxiu_apk_unpacked_root
from backend.core.fanxiu_resources import (
    FanxiuResourceError,
    resolve_fanxiu_export_root,
    resolve_fanxiu_resource_root,
)


_ATLAS_HASH_SUFFIX_RE = re.compile(r"^(?P<key>.+)_[0-9a-fA-F]{32}$")
_SAFE_PATH_PART_RE = re.compile(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_VISUAL_HASH_BITS = 64
_VISUAL_PHASH_ALGORITHM = "phash-8"
_VISUAL_DHASH_ALGORITHM = "dhash-8"
_VISUAL_SIMILARITY_INDEX_FIELDS = [
    "source_kind",
    "name",
    "category",
    "asset_group",
    "width",
    "height",
    "atlas_key",
    "source_path",
    "path_id",
    "bytes",
    "media_path",
    "absolute_media_path",
    "phash",
    "dhash",
    "phash_algorithm",
    "dhash_algorithm",
    "normalized_width",
    "normalized_height",
    "hash_error",
]
_USAGE_TOKEN_RE = re.compile(r"[0-9A-Za-z_.-]{3,128}")
_SPRITE_TARGET_CATEGORIES = {
    "ability_icon",
    "buff_icon",
    "fashion_icon",
    "head_portrait",
    "item_or_ui_icon",
    "logo",
    "skill_icon",
    "title_label",
}
_SPRITE_ICON_CATEGORIES = {
    "ability_icon",
    "buff_icon",
    "fashion_icon",
    "head_portrait",
    "item_or_ui_icon",
    "logo",
    "skill_icon",
}
_TARGET_ATLAS_KEYS = {
    "ability",
    "buff",
    "fashionicon",
    "head",
    "icon",
    "icon2",
    "icon3",
    "icon4",
    "icon5",
    "icon6",
    "icon7",
    "icon8",
    "icon9",
    "skill",
    "skill2",
    "skill3",
}
_APK_TARGET_TERMS = (
    "logo",
    "launcher",
    "splash",
    "icon",
    "r_icon",
    "sy37",
    "sqwan",
    "taptap",
    "unity_static_splash",
)
_VISUAL_QUERY_ALIASES = {
    "公告": ["gonggao", "notice", "announcement", "annou", "bulletin", "ggl"],
    "游戏公告": ["gonggao", "notice", "announcement", "annou", "bulletin", "ggl"],
    "开服": ["kaifu", "openserver", "serveropen"],
    "活动": ["huodong", "activity", "yxhd"],
    "安全": ["anquan", "safe", "safety", "risk"],
    "风险": ["fengxian", "risk"],
    "背景": ["bg", "background"],
    "大图": ["bg", "background", "banner", "img", "pic"],
    "图片": ["image", "img", "pic"],
}


def _safe_path_part(value: Any, fallback: str = "asset") -> str:
    text = _SAFE_PATH_PART_RE.sub("_", str(value or "").strip()).strip("._")
    return text[:96] if text else fallback


def _write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _visual_query_terms(query: str | None) -> list[str]:
    text = (query or "").strip().lower()
    if not text:
        return []
    terms = [text]
    for key, aliases in _VISUAL_QUERY_ALIASES.items():
        if key in text:
            terms.extend(alias.lower() for alias in aliases)
    seen: set[str] = set()
    return [term for term in terms if term and not (term in seen or seen.add(term))]


def _atlas_key_from_path(path: Path) -> str:
    match = _ATLAS_HASH_SUFFIX_RE.match(path.stem)
    if match:
        return match.group("key")
    return path.stem


def _rect_number(rect: Any, attr: str) -> float | None:
    if rect is None:
        return None
    if isinstance(rect, dict):
        value = rect.get(attr)
    else:
        value = getattr(rect, attr, None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rounded_int(value: float | None) -> int:
    if value is None:
        return 0
    return max(0, int(round(value)))


def _sprite_name(data: Any) -> str:
    return str(getattr(data, "name", "") or getattr(data, "m_Name", "") or "").strip()


def _classify_sprite(atlas_key: str, sprite_name: str) -> str:
    key = atlas_key.lower()
    name = sprite_name.lower()
    combined = f"{key}/{name}"
    if "logo" in combined:
        return "logo"
    if key.startswith("skill") or name.startswith("skill") or "_skill_" in name:
        return "skill_icon"
    if key == "fashionicon" or "fashionicon" in name:
        return "fashion_icon"
    if key == "head" or name.startswith("head") or "_head_" in name:
        return "head_portrait"
    if key == "ability" or name.startswith("ability") or "_ability_" in name:
        return "ability_icon"
    if key == "buff" or name.startswith("buff") or "_buff_" in name:
        return "buff_icon"
    if key.startswith("icon") or name.startswith("icon") or "_icon_" in name:
        return "item_or_ui_icon"
    if any(token in name for token in ("title", "biaoti", "_name_", "_zw_")):
        return "title_label"
    return "sprite"


def _visual_asset_group(
    *,
    source_kind: str,
    category: str,
    atlas_key: str = "",
    name: str = "",
    width: int = 0,
    height: int = 0,
) -> str:
    normalized_category = str(category or "").strip()
    if normalized_category in _SPRITE_ICON_CATEGORIES or normalized_category in {"apk_icon", "apk_logo", "apk_splash"}:
        return "icon"
    if normalized_category == "title_label":
        return "text"
    if str(source_kind or "").strip() == "apk_image":
        return "apk"
    combined = f"{atlas_key}/{name}".lower()
    if max(int(width or 0), int(height or 0)) >= 300:
        return "image"
    if any(token in combined for token in ("bg", "background", "banner", "img", "pic", "login", "panel", "board")):
        return "image"
    return "sprite"


def _is_target_sprite(atlas_key: str, sprite_name: str, category: str) -> bool:
    return category in _SPRITE_TARGET_CATEGORIES or atlas_key.lower() in _TARGET_ATLAS_KEYS or "logo" in sprite_name.lower()


def _sprite_export_path(output_dir: Path, atlas_key: str, sprite_name: str, path_id: Any) -> Path:
    safe_name = _safe_path_part(sprite_name, "sprite")
    safe_path_id = _safe_path_part(str(path_id).replace("-", "m"), "0")
    return output_dir / "sprite_images" / _safe_path_part(atlas_key) / f"{safe_name}_{safe_path_id}.png"


def _relative_media_path(path: str | Path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _image_dimensions(path: Path) -> tuple[int, int, str]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height), str(image.mode)
    except Exception:
        return 0, 0, ""


def _trim_uniform_border(image: Image.Image, *, tolerance: int = 8) -> Image.Image:
    if image.width <= 2 or image.height <= 2:
        return image
    rgb = image.convert("RGB")
    background = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
    diff = ImageChops.difference(rgb, background).convert("L")
    mask = diff.point(lambda value: 255 if value > tolerance else 0)
    bbox = mask.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    if right - left < 2 or bottom - top < 2:
        return image
    return image.crop(bbox)


def _normalize_visual_hash_image(image: Image.Image) -> Image.Image:
    normalized = ImageOps.exif_transpose(image)
    if normalized.mode in {"RGBA", "LA"} or "transparency" in normalized.info:
        rgba = normalized.convert("RGBA")
        alpha_bbox = rgba.getchannel("A").getbbox()
        if alpha_bbox:
            rgba = rgba.crop(alpha_bbox)
        canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        canvas.alpha_composite(rgba)
        normalized = canvas.convert("RGB")
    else:
        normalized = normalized.convert("RGB")
    return _trim_uniform_border(normalized)


def _compute_visual_similarity_hashes_from_image(image: Image.Image) -> dict[str, Any]:
    normalized = _normalize_visual_hash_image(image)
    return {
        "phash": str(imagehash.phash(normalized, hash_size=8)),
        "dhash": str(imagehash.dhash(normalized, hash_size=8)),
        "normalized_width": int(normalized.width),
        "normalized_height": int(normalized.height),
    }


def _compute_visual_similarity_hashes_from_path(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        return _compute_visual_similarity_hashes_from_image(image)


def _compute_visual_similarity_hashes_from_bytes(data: bytes) -> dict[str, Any]:
    with Image.open(io.BytesIO(data)) as image:
        return _compute_visual_similarity_hashes_from_image(image)


def _hash_distance(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    try:
        return (int(str(left), 16) ^ int(str(right), 16)).bit_count()
    except Exception:
        return None


def _aspect_similarity(left_width: Any, left_height: Any, right_width: Any, right_height: Any) -> float:
    try:
        lw = float(left_width)
        lh = float(left_height)
        rw = float(right_width)
        rh = float(right_height)
    except (TypeError, ValueError):
        return 0.0
    if lw <= 0 or lh <= 0 or rw <= 0 or rh <= 0:
        return 0.0
    left_ratio = lw / lh
    right_ratio = rw / rh
    if left_ratio <= 0 or right_ratio <= 0:
        return 0.0
    distance = abs(math.log(left_ratio / right_ratio))
    return max(0.0, 1.0 - min(distance / 1.25, 1.0))


def _visual_similarity_score(phash_distance: int | None, dhash_distance: int | None, aspect_score: float) -> float:
    p_score = 0.0 if phash_distance is None else max(0.0, 1.0 - phash_distance / _VISUAL_HASH_BITS)
    d_score = 0.0 if dhash_distance is None else max(0.0, 1.0 - dhash_distance / _VISUAL_HASH_BITS)
    return max(0.0, min(1.0, p_score * 0.72 + d_score * 0.23 + aspect_score * 0.05))


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _preview_value(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _sprite_tokens(value: Any, sprite_names: set[str]) -> list[str]:
    if not value:
        return []
    hits = {match.group(0) for match in _USAGE_TOKEN_RE.finditer(str(value)) if match.group(0) in sprite_names}
    return sorted(hits)


def _usage_source_kind(path: Path, export_root: Path) -> str:
    try:
        rel_parts = path.relative_to(export_root).parts
    except ValueError:
        return "text"
    if rel_parts[:1] == ("parsed_configs",) and path.name == "rows.tsv":
        return "config_rows"
    if rel_parts[:2] == ("by_source", "lscripts"):
        return "lua_text_asset"
    return "text"


def _scan_rows_tsv_sprite_usage(
    path: Path,
    *,
    export_root: Path,
    sprite_names: set[str],
    max_rows: int,
    rows: list[dict[str, Any]],
) -> bool:
    relative_path = path.relative_to(export_root).as_posix()
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row_number, row in enumerate(reader, start=2):
            row_key = row.get("_row_key") or row.get("id") or ""
            for field, value in row.items():
                tokens = _sprite_tokens(value, sprite_names)
                for token in tokens:
                    rows.append(
                        {
                            "sprite_name": token,
                            "source_kind": "config_rows",
                            "relative_path": relative_path,
                            "row_key": row_key,
                            "field": field,
                            "line_no": row_number,
                            "value_preview": _preview_value(value),
                        }
                    )
                    if len(rows) >= max_rows:
                        return False
    return True


def _scan_line_sprite_usage(
    path: Path,
    *,
    export_root: Path,
    sprite_names: set[str],
    max_rows: int,
    rows: list[dict[str, Any]],
) -> bool:
    relative_path = path.relative_to(export_root).as_posix()
    source_kind = _usage_source_kind(path, export_root)
    with path.open("r", encoding="utf-8-sig", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            tokens = _sprite_tokens(line, sprite_names)
            for token in tokens:
                rows.append(
                    {
                        "sprite_name": token,
                        "source_kind": source_kind,
                        "relative_path": relative_path,
                        "row_key": "",
                        "field": "line",
                        "line_no": line_no,
                        "value_preview": _preview_value(line),
                    }
                )
                if len(rows) >= max_rows:
                    return False
    return True


def _build_sprite_usage_rows(
    *,
    export_root: Path,
    output_dir: Path,
    target_sprite_rows: list[dict[str, Any]],
    max_usage_rows: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    sprite_names = {str(row.get("name") or "") for row in target_sprite_rows if row.get("name")}
    usage_rows: list[dict[str, Any]] = []
    scanned_files = 0
    errored_files = 0
    truncated = False
    candidate_paths: list[Path] = []
    parsed_configs = export_root / "parsed_configs"
    if parsed_configs.exists():
        candidate_paths.extend(sorted(path for path in parsed_configs.rglob("rows.tsv") if output_dir not in path.parents))
    lscript_exports = export_root / "by_source" / "lscripts"
    if lscript_exports.exists():
        candidate_paths.extend(sorted(lscript_exports.rglob("*.lua")))
    if max_usage_rows <= 0 or not sprite_names:
        return [], [], {
            "usage_scan_candidate_file_count": len(candidate_paths),
            "usage_scan_file_count": 0,
            "usage_scan_error_count": 0,
            "usage_ref_count": 0,
            "usage_sprite_count": 0,
            "usage_scan_truncated": bool(max_usage_rows <= 0 and candidate_paths),
            "usage_scan_max_rows": max_usage_rows,
        }

    for path in candidate_paths:
        scanned_files += 1
        try:
            if path.name == "rows.tsv":
                keep_scanning = _scan_rows_tsv_sprite_usage(
                    path,
                    export_root=export_root,
                    sprite_names=sprite_names,
                    max_rows=max_usage_rows,
                    rows=usage_rows,
                )
            else:
                keep_scanning = _scan_line_sprite_usage(
                    path,
                    export_root=export_root,
                    sprite_names=sprite_names,
                    max_rows=max_usage_rows,
                    rows=usage_rows,
                )
        except OSError:
            errored_files += 1
            keep_scanning = True
        if not keep_scanning:
            truncated = True
            break

    sprite_meta = {
        str(row.get("name") or ""): {
            "atlas_key": row.get("atlas_key", ""),
            "category": row.get("category", ""),
            "image_path": row.get("image_path", ""),
        }
        for row in target_sprite_rows
        if row.get("name")
    }
    by_sprite: dict[str, dict[str, Any]] = {}
    path_samples: dict[tuple[str, str], list[str]] = {}
    for row in usage_rows:
        sprite_name = str(row.get("sprite_name") or "")
        source_kind = str(row.get("source_kind") or "")
        summary = by_sprite.setdefault(
            sprite_name,
            {
                "sprite_name": sprite_name,
                "atlas_key": sprite_meta.get(sprite_name, {}).get("atlas_key", ""),
                "category": sprite_meta.get(sprite_name, {}).get("category", ""),
                "image_path": sprite_meta.get(sprite_name, {}).get("image_path", ""),
                "usage_ref_count": 0,
                "config_ref_count": 0,
                "lua_ref_count": 0,
                "other_ref_count": 0,
                "sample_config_paths": "",
                "sample_lua_paths": "",
            },
        )
        summary["usage_ref_count"] += 1
        if source_kind == "config_rows":
            summary["config_ref_count"] += 1
            sample_key = (sprite_name, "config")
        elif source_kind == "lua_text_asset":
            summary["lua_ref_count"] += 1
            sample_key = (sprite_name, "lua")
        else:
            summary["other_ref_count"] += 1
            sample_key = (sprite_name, "other")
        samples = path_samples.setdefault(sample_key, [])
        relative_path = str(row.get("relative_path") or "")
        if relative_path and relative_path not in samples and len(samples) < 5:
            samples.append(relative_path)

    for sprite_name, summary in by_sprite.items():
        summary["sample_config_paths"] = " | ".join(path_samples.get((sprite_name, "config"), []))
        summary["sample_lua_paths"] = " | ".join(path_samples.get((sprite_name, "lua"), []))

    summary_rows = sorted(
        by_sprite.values(),
        key=lambda row: (-int(row.get("usage_ref_count") or 0), str(row.get("sprite_name") or "")),
    )
    stats = {
        "usage_scan_candidate_file_count": len(candidate_paths),
        "usage_scan_file_count": scanned_files,
        "usage_scan_error_count": errored_files,
        "usage_ref_count": len(usage_rows),
        "usage_sprite_count": len(summary_rows),
        "usage_scan_truncated": truncated,
        "usage_scan_max_rows": max_usage_rows,
    }
    return usage_rows, summary_rows, stats


def _classify_apk_image(relative_path: str) -> str:
    low = relative_path.lower()
    if "logo" in low:
        return "apk_logo"
    if "splash" in low:
        return "apk_splash"
    if "launcher" in low or "icon" in low or "r_icon" in low:
        return "apk_icon"
    if "sy37" in low or "sqwan" in low:
        return "sdk_ui"
    if "taptap" in low:
        return "taptap_ui"
    return "apk_image"


def _is_target_apk_image(relative_path: str, category: str) -> bool:
    low = relative_path.lower()
    return category != "apk_image" and any(term in low for term in _APK_TARGET_TERMS)


def _copy_apk_gallery_image(source_path: Path, apk_root: Path, output_dir: Path) -> str:
    rel = source_path.relative_to(apk_root)
    parts = [_safe_path_part(part) for part in rel.parts[:-1]]
    target_dir = output_dir / "apk_images" / Path(*parts) if parts else output_dir / "apk_images"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{_safe_path_part(rel.stem)}{source_path.suffix.lower()}"
    if not target_path.exists():
        shutil.copy2(source_path, target_path)
    return str(target_path)


def _scan_apk_images(apk_root: Path, output_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    for path in sorted(apk_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        rel = path.relative_to(apk_root).as_posix()
        category = _classify_apk_image(rel)
        target = _is_target_apk_image(rel, category)
        width, height, mode = _image_dimensions(path)
        gallery_path = _copy_apk_gallery_image(path, apk_root, output_dir)
        row = {
            "source_kind": "apk_image",
            "relative_path": rel,
            "name": path.stem,
            "category": category,
            "asset_group": _visual_asset_group(source_kind="apk_image", category=category, name=path.stem, width=width, height=height),
            "width": width,
            "height": height,
            "mode": mode,
            "bytes": path.stat().st_size,
            "path": str(path),
            "target": str(target).lower(),
            "gallery_path": gallery_path,
        }
        if target:
            target_rows.append(row)
        all_rows.append(row)
    return all_rows, target_rows


def _write_visual_gallery_html(
    path: Path,
    *,
    output_dir: Path,
    stats: dict[str, Any],
    sprite_rows: list[dict[str, Any]],
    apk_rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        "<title>Fanxiu Static Icon / Logo Gallery</title>",
        "<style>",
        "body{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;background:#f7f7f5;color:#242424}",
        "h1{font-size:24px;margin:0 0 12px} h2{font-size:18px;margin:28px 0 12px}",
        ".stats{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}.stat{background:white;border:1px solid #ddd;border-radius:6px;padding:6px 10px;font-size:12px}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:10px}",
        ".card{background:white;border:1px solid #ddd;border-radius:6px;padding:8px;min-width:0}",
        ".thumb{width:100%;height:96px;object-fit:contain;background:#eee;border-radius:4px}",
        ".name{font-size:12px;margin-top:6px;overflow-wrap:anywhere}.meta{font-size:11px;color:#666;overflow-wrap:anywhere}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>Fanxiu Static Icon / Logo Gallery</h1>",
        '<div class="stats">',
    ]
    for key in [
        "atlas_bundle_count",
        "atlas_sprite_count",
        "target_sprite_count",
        "exported_sprite_image_count",
        "apk_image_count",
        "target_apk_image_count",
    ]:
        lines.append(f'<div class="stat">{html.escape(key)}: {html.escape(str(stats.get(key, "")))}</div>')
    lines.extend(["</div>", "<h2>Atlas Sprites</h2>", '<div class="grid">'])
    for row in sprite_rows:
        img_path = str(row.get("image_path") or "")
        if not img_path:
            continue
        rel_img = _relative_media_path(img_path, output_dir)
        lines.extend(
            [
                '<div class="card">',
                f'<img class="thumb" loading="lazy" src="{html.escape(rel_img)}">',
                f'<div class="name">{html.escape(str(row.get("name") or ""))}</div>',
                f'<div class="meta">{html.escape(str(row.get("category") or ""))} · {html.escape(str(row.get("atlas_key") or ""))}</div>',
                f'<div class="meta">{html.escape(str(row.get("width") or ""))}x{html.escape(str(row.get("height") or ""))}</div>',
                "</div>",
            ]
        )
    lines.extend(["</div>", "<h2>APK Built-in Images</h2>", '<div class="grid">'])
    for row in apk_rows:
        img_path = str(row.get("gallery_path") or "")
        if not img_path:
            continue
        rel_img = _relative_media_path(img_path, output_dir)
        lines.extend(
            [
                '<div class="card">',
                f'<img class="thumb" loading="lazy" src="{html.escape(rel_img)}">',
                f'<div class="name">{html.escape(str(row.get("name") or ""))}</div>',
                f'<div class="meta">{html.escape(str(row.get("category") or ""))}</div>',
                f'<div class="meta">{html.escape(str(row.get("relative_path") or ""))}</div>',
                f'<div class="meta">{html.escape(str(row.get("width") or ""))}x{html.escape(str(row.get("height") or ""))}</div>',
                "</div>",
            ]
        )
    lines.extend(["</div>", "</body>", "</html>"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_visual_catalog_report(
    path: Path,
    *,
    stats: dict[str, Any],
    files: dict[str, str],
    category_counts: dict[str, int],
    top_bundle_rows: list[dict[str, Any]],
    apk_category_counts: dict[str, int],
    top_usage_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# Fanxiu static visual asset catalog",
        "",
        "Static read-only catalog for Unity `atlasnew` sprites and APK built-in images. The full image manifest is `visual_asset_catalog.tsv`; the legacy icon/logo subset is preserved separately for focused icon browsing.",
        "",
        "## Stats",
        "",
    ]
    for key, value in stats.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Sprite Category Counts", ""])
    for key, value in sorted(category_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## APK Image Category Counts", ""])
    for key, value in sorted(apk_category_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Top Atlas Bundles", ""])
    for row in top_bundle_rows[:30]:
        lines.append(
            f"- `{row.get('atlas_key')}` sprites `{row.get('sprite_count')}`, targets `{row.get('target_sprite_count')}`, source `{row.get('relative_source_path')}`"
        )
    lines.extend(["", "## Top Sprite Usage", ""])
    for row in top_usage_rows[:30]:
        lines.append(
            f"- `{row.get('sprite_name')}` refs `{row.get('usage_ref_count')}`, config `{row.get('config_ref_count')}`, lua `{row.get('lua_ref_count')}`, category `{row.get('category')}`"
        )
    lines.extend(["", "## Files", ""])
    for key, value in files.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This report exports local static image metadata and cropped sprite PNGs only. It does not inspect runtime state, traffic payload values, account data, or modify APK/game resources.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_static_visual_catalog(
    *,
    resource_root: str | Path | None = None,
    apk_root: str | Path | None = None,
    export_root: str | Path | None = None,
    export_target_images: bool = True,
    max_export_images: int | None = None,
    include_apk_images: bool = True,
    build_usage_index: bool = True,
    max_usage_rows: int = 200000,
) -> dict[str, Any]:
    root = resolve_fanxiu_resource_root(resource_root)
    if not root.exists() or not root.is_dir():
        raise FanxiuResourceError(f"资源根目录不存在：{root}")
    atlas_dir = root / "atlasnew"
    if not atlas_dir.exists() or not atlas_dir.is_dir():
        raise FanxiuResourceError(f"atlasnew 目录不存在：{atlas_dir}")

    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "parsed_configs" / "visual_catalog"
    output_dir.mkdir(parents=True, exist_ok=True)
    max_export = None if max_export_images is None else max(0, int(max_export_images))

    sprite_rows: list[dict[str, Any]] = []
    visual_asset_rows: list[dict[str, Any]] = []
    target_sprite_rows: list[dict[str, Any]] = []
    bundle_rows: list[dict[str, Any]] = []
    sprite_category_counts: Counter[str] = Counter()
    exported_images = 0
    export_errors = 0

    atlas_files = sorted(atlas_dir.glob("*.bytes"))
    for source_path in atlas_files:
        atlas_key = _atlas_key_from_path(source_path)
        relative_source_path = source_path.relative_to(root).as_posix()
        object_counts: Counter[str] = Counter()
        bundle_category_counts: Counter[str] = Counter()
        bundle_target_count = 0
        bundle_export_count = 0
        try:
            env = load_unity_environment(source_path)
        except Exception as exc:
            bundle_rows.append(
                {
                    "atlas_key": atlas_key,
                    "relative_source_path": relative_source_path,
                    "bytes": source_path.stat().st_size,
                    "object_count": 0,
                    "sprite_count": 0,
                    "texture_count": 0,
                    "target_sprite_count": 0,
                    "exported_sprite_count": 0,
                    "category_counts_json": "{}",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        for obj in env.objects:
            object_type = str(getattr(getattr(obj, "type", None), "name", ""))
            object_counts[object_type] += 1
            if object_type != "Sprite":
                continue
            data = obj.read()
            name = _sprite_name(data)
            rect = getattr(data, "m_Rect", None)
            width = _rounded_int(_rect_number(rect, "width"))
            height = _rounded_int(_rect_number(rect, "height"))
            category = _classify_sprite(atlas_key, name)
            asset_group = _visual_asset_group(
                source_kind="atlas_sprite",
                category=category,
                atlas_key=atlas_key,
                name=name,
                width=width,
                height=height,
            )
            target = _is_target_sprite(atlas_key, name, category)
            sprite_category_counts[category] += 1
            bundle_category_counts[category] += 1
            image_path = ""
            export_error = ""
            if target:
                bundle_target_count += 1
            if export_target_images and (max_export is None or exported_images < max_export):
                try:
                    target_path = _sprite_export_path(output_dir, atlas_key, name, getattr(obj, "path_id", "0"))
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    if not target_path.exists():
                        data.image.save(target_path)
                    image_path = str(target_path)
                    exported_images += 1
                    bundle_export_count += 1
                except Exception as exc:
                    export_error = f"{type(exc).__name__}: {exc}"
                    export_errors += 1

            row = {
                "source_kind": "atlas_sprite",
                "atlas_key": atlas_key,
                "relative_source_path": relative_source_path,
                "name": name,
                "category": category,
                "asset_group": asset_group,
                "width": width,
                "height": height,
                "rect_x": _rect_number(rect, "x") or "",
                "rect_y": _rect_number(rect, "y") or "",
                "path_id": getattr(obj, "path_id", ""),
                "target": str(target).lower(),
                "image_path": image_path,
                "export_error": export_error,
            }
            sprite_rows.append(row)
            if image_path:
                visual_asset_rows.append(row)
            if target:
                target_sprite_rows.append(row)

        bundle_rows.append(
            {
                "atlas_key": atlas_key,
                "relative_source_path": relative_source_path,
                "bytes": source_path.stat().st_size,
                "object_count": sum(object_counts.values()),
                "sprite_count": object_counts.get("Sprite", 0),
                "texture_count": object_counts.get("Texture2D", 0),
                "target_sprite_count": bundle_target_count,
                "exported_sprite_count": bundle_export_count,
                "category_counts_json": json.dumps(dict(bundle_category_counts), ensure_ascii=False, sort_keys=True),
                "error": "",
            }
        )

    apk_rows: list[dict[str, Any]] = []
    target_apk_rows: list[dict[str, Any]] = []
    apk_scan_error = ""
    if include_apk_images:
        try:
            resolved_apk_root = resolve_fanxiu_apk_unpacked_root(apk_root)
            apk_rows, target_apk_rows = _scan_apk_images(resolved_apk_root, output_dir)
        except FanxiuResourceError as exc:
            if apk_root is not None:
                raise
            apk_scan_error = str(exc)

    sprite_tsv = output_dir / "atlas_sprite_catalog.tsv"
    bundle_tsv = output_dir / "atlas_bundle_summary.tsv"
    visual_asset_tsv = output_dir / "visual_asset_catalog.tsv"
    target_sprite_tsv = output_dir / "icon_logo_sprite_catalog.tsv"
    apk_tsv = output_dir / "apk_visual_assets.tsv"
    target_apk_tsv = output_dir / "apk_icon_logo_assets.tsv"
    usage_tsv = output_dir / "sprite_usage_refs.tsv"
    usage_summary_tsv = output_dir / "sprite_usage_summary.tsv"
    html_path = output_dir / "icon_logo_gallery.html"
    report_path = output_dir / "static_visual_catalog_report.md"
    json_path = output_dir / "static_visual_catalog.json"

    _write_tsv(
        sprite_tsv,
        sprite_rows,
        [
            "source_kind",
            "atlas_key",
            "relative_source_path",
            "name",
            "category",
            "asset_group",
            "width",
            "height",
            "rect_x",
            "rect_y",
            "path_id",
            "target",
            "image_path",
            "export_error",
        ],
    )
    _write_tsv(
        visual_asset_tsv,
        visual_asset_rows,
        [
            "source_kind",
            "atlas_key",
            "relative_source_path",
            "name",
            "category",
            "asset_group",
            "width",
            "height",
            "path_id",
            "image_path",
            "export_error",
        ],
    )
    _write_tsv(
        bundle_tsv,
        bundle_rows,
        [
            "atlas_key",
            "relative_source_path",
            "bytes",
            "object_count",
            "sprite_count",
            "texture_count",
            "target_sprite_count",
            "exported_sprite_count",
            "category_counts_json",
            "error",
        ],
    )
    _write_tsv(
        target_sprite_tsv,
        target_sprite_rows,
        [
            "source_kind",
            "atlas_key",
            "relative_source_path",
            "name",
            "category",
            "asset_group",
            "width",
            "height",
            "path_id",
            "image_path",
            "export_error",
        ],
    )
    _write_tsv(
        apk_tsv,
        apk_rows,
        [
            "source_kind",
            "relative_path",
            "name",
            "category",
            "asset_group",
            "width",
            "height",
            "mode",
            "bytes",
            "path",
            "target",
            "gallery_path",
        ],
    )
    _write_tsv(
        target_apk_tsv,
        target_apk_rows,
        [
            "source_kind",
            "relative_path",
            "name",
            "category",
            "asset_group",
            "width",
            "height",
            "mode",
            "bytes",
            "path",
            "gallery_path",
        ],
    )
    similarity_stats = _write_visual_similarity_index(output_dir, _load_static_visual_manifest_rows(output_dir))
    if build_usage_index:
        usage_rows, usage_summary_rows, usage_stats = _build_sprite_usage_rows(
            export_root=export_base,
            output_dir=output_dir,
            target_sprite_rows=target_sprite_rows,
            max_usage_rows=max(0, int(max_usage_rows)),
        )
    else:
        usage_rows = []
        usage_summary_rows = []
        usage_stats = {
            "usage_scan_candidate_file_count": 0,
            "usage_scan_file_count": 0,
            "usage_scan_error_count": 0,
            "usage_ref_count": 0,
            "usage_sprite_count": 0,
            "usage_scan_truncated": False,
            "usage_scan_max_rows": max(0, int(max_usage_rows)),
        }
    _write_tsv(
        usage_tsv,
        usage_rows,
        [
            "sprite_name",
            "source_kind",
            "relative_path",
            "row_key",
            "field",
            "line_no",
            "value_preview",
        ],
    )
    _write_tsv(
        usage_summary_tsv,
        usage_summary_rows,
        [
            "sprite_name",
            "atlas_key",
            "category",
            "usage_ref_count",
            "config_ref_count",
            "lua_ref_count",
            "other_ref_count",
            "sample_config_paths",
            "sample_lua_paths",
            "image_path",
        ],
    )

    stats = {
        "resource_root": str(root),
        "export_root": str(export_base),
        "atlas_bundle_count": len(atlas_files),
        "atlas_bundle_error_count": sum(1 for row in bundle_rows if row.get("error")),
        "atlas_sprite_count": len(sprite_rows),
        "atlas_texture_count": sum(int(row.get("texture_count") or 0) for row in bundle_rows),
        "visual_asset_count": len(visual_asset_rows) + len(apk_rows),
        "visual_sprite_asset_count": len(visual_asset_rows),
        "target_sprite_count": len(target_sprite_rows),
        "exported_sprite_image_count": exported_images,
        "sprite_export_error_count": export_errors,
        "apk_image_count": len(apk_rows),
        "target_apk_image_count": len(target_apk_rows),
        "apk_scan_error": apk_scan_error,
        "metadata_only_no_runtime_payloads": True,
        **similarity_stats,
        **usage_stats,
    }
    files = {
        "atlas_sprites": str(sprite_tsv),
        "atlas_bundles": str(bundle_tsv),
        "visual_assets": str(visual_asset_tsv),
        "icon_logo_sprites": str(target_sprite_tsv),
        "apk_images": str(apk_tsv),
        "apk_icon_logo_images": str(target_apk_tsv),
        "sprite_usage_refs": str(usage_tsv),
        "sprite_usage_summary": str(usage_summary_tsv),
        "visual_similarity_index": str(output_dir / "visual_similarity_index.tsv"),
        "gallery_html": str(html_path),
        "markdown": str(report_path),
        "json": str(json_path),
    }
    top_bundle_rows = sorted(bundle_rows, key=lambda row: int(row.get("target_sprite_count") or 0), reverse=True)
    apk_category_counts = Counter(str(row.get("category") or "") for row in apk_rows)
    _write_visual_gallery_html(
        html_path,
        output_dir=output_dir,
        stats=stats,
        sprite_rows=target_sprite_rows,
        apk_rows=target_apk_rows,
    )
    _write_visual_catalog_report(
        report_path,
        stats=stats,
        files=files,
        category_counts=dict(sprite_category_counts),
        top_bundle_rows=top_bundle_rows,
        apk_category_counts=dict(apk_category_counts),
        top_usage_rows=usage_summary_rows,
    )
    json_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "category_counts": dict(sprite_category_counts),
                "apk_category_counts": dict(apk_category_counts),
                "top_bundles": top_bundle_rows[:80],
                "top_sprite_usage": usage_summary_rows[:120],
                "sample_visual_assets": visual_asset_rows[:120],
                "sample_target_sprites": target_sprite_rows[:120],
                "sample_target_apk_images": target_apk_rows[:120],
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "output_dir": str(output_dir),
        "stats": stats,
        "category_counts": dict(sprite_category_counts),
        "apk_category_counts": dict(apk_category_counts),
        "files": files,
        "top_bundles": top_bundle_rows[:30],
        "top_sprite_usage": usage_summary_rows[:30],
    }


def _visual_media_root(export_root: str | Path | None = None) -> Path:
    return resolve_fanxiu_export_root(export_root) / "parsed_configs" / "visual_catalog"


def _coerce_visual_media_path(value: str | None, media_root: Path) -> tuple[Path | None, str]:
    text = str(value or "").strip()
    if not text:
        return None, ""
    raw_path = Path(text)
    media_path = raw_path.expanduser().resolve() if raw_path.is_absolute() else (media_root / raw_path).resolve()
    if not _is_relative_to(media_path, media_root.resolve()):
        return None, ""
    if not media_path.is_file() or media_path.suffix.lower() not in _IMAGE_SUFFIXES:
        return None, ""
    return media_path, _relative_media_path(media_path, media_root)


def _load_static_visual_manifest_rows(media_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_files = {
        "atlas_sprite": media_root / "visual_asset_catalog.tsv",
        "apk_image": media_root / "apk_visual_assets.tsv",
    }
    if not source_files["atlas_sprite"].is_file():
        source_files["atlas_sprite"] = media_root / "icon_logo_sprite_catalog.tsv"
    if not source_files["apk_image"].is_file():
        source_files["apk_image"] = media_root / "apk_icon_logo_assets.tsv"
    for source_file_kind, source_file in source_files.items():
        for raw in _read_tsv(source_file):
            row_source_kind = raw.get("source_kind") or source_file_kind
            media_value = raw.get("image_path") or raw.get("gallery_path") or raw.get("path")
            media_path, relative_media_path = _coerce_visual_media_path(media_value, media_root)
            if media_path is None:
                continue
            rows.append(
                {
                    "source_kind": row_source_kind,
                    "name": raw.get("name") or Path(relative_media_path).stem,
                    "category": raw.get("category") or "",
                    "asset_group": raw.get("asset_group") or _visual_asset_group(
                        source_kind=row_source_kind,
                        category=raw.get("category") or "",
                        atlas_key=raw.get("atlas_key") or "",
                        name=raw.get("name") or Path(relative_media_path).stem,
                        width=int(raw.get("width") or 0),
                        height=int(raw.get("height") or 0),
                    ),
                    "width": int(raw.get("width") or 0),
                    "height": int(raw.get("height") or 0),
                    "atlas_key": raw.get("atlas_key") or "",
                    "source_path": raw.get("relative_source_path") or raw.get("relative_path") or "",
                    "path_id": raw.get("path_id") or "",
                    "bytes": int(raw.get("bytes") or 0),
                    "media_path": relative_media_path,
                    "absolute_media_path": str(media_path),
                }
            )
    return rows


def _write_visual_similarity_index(media_root: Path, rows: list[dict[str, Any]]) -> dict[str, int]:
    index_rows: list[dict[str, Any]] = []
    hash_error_count = 0
    for row in rows:
        index_row = {field: row.get(field, "") for field in _VISUAL_SIMILARITY_INDEX_FIELDS}
        index_row["phash_algorithm"] = _VISUAL_PHASH_ALGORITHM
        index_row["dhash_algorithm"] = _VISUAL_DHASH_ALGORITHM
        media_path = Path(str(row.get("absolute_media_path") or ""))
        try:
            hashes = _compute_visual_similarity_hashes_from_path(media_path)
            index_row.update(hashes)
            index_row["hash_error"] = ""
        except Exception as exc:
            index_row["hash_error"] = f"{type(exc).__name__}: {exc}"
            hash_error_count += 1
        index_rows.append(index_row)

    _write_tsv(media_root / "visual_similarity_index.tsv", index_rows, _VISUAL_SIMILARITY_INDEX_FIELDS)
    return {
        "visual_similarity_index_count": len(index_rows),
        "visual_similarity_hash_error_count": hash_error_count,
    }


def _load_visual_similarity_index_rows(media_root: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    index_path = media_root / "visual_similarity_index.tsv"
    if not index_path.is_file():
        return_rows = _load_static_visual_manifest_rows(media_root)
        stats = _write_visual_similarity_index(media_root, return_rows)
    else:
        stats = {}
    rows = [
        row
        for row in _read_tsv(index_path)
        if row.get("phash") and row.get("dhash") and not row.get("hash_error")
    ]
    stats.setdefault("visual_similarity_index_count", len(rows))
    stats.setdefault("visual_similarity_hash_error_count", 0)
    return rows, stats


def resolve_fanxiu_visual_media_path(path: str, export_root: str | Path | None = None) -> Path:
    media_root = _visual_media_root(export_root)
    media_path, _relative_path = _coerce_visual_media_path(path, media_root)
    if media_path is None:
        raise FanxiuResourceError(f"视觉资源不存在或不在图鉴目录内：{media_root}")
    return media_path


def load_fanxiu_static_visual_manifest(
    *,
    export_root: str | Path | None = None,
    query: str | None = None,
    category: str | None = None,
    asset_group: str | None = None,
    source_kind: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    media_root = _visual_media_root(export_root)
    if not media_root.is_dir():
        raise FanxiuResourceError(f"静态视觉图鉴目录不存在，请先运行 static visual catalog：{media_root}")

    rows = _load_static_visual_manifest_rows(media_root)

    query_terms = _visual_query_terms(query)
    normalized_category = (category or "").strip().lower()
    normalized_asset_group = (asset_group or "").strip().lower()
    normalized_source_kind = (source_kind or "").strip().lower()

    def row_matches_query_scope(row: dict[str, Any]) -> bool:
        if normalized_category and str(row.get("category", "")).lower() != normalized_category:
            return False
        if normalized_source_kind and str(row.get("source_kind", "")).lower() != normalized_source_kind:
            return False
        if not query_terms:
            return True
        haystack = " ".join(
            str(row.get(field, ""))
            for field in (
                "source_kind",
                "name",
                "category",
                "asset_group",
                "atlas_key",
                "source_path",
                "path_id",
                "media_path",
            )
        ).lower()
        return any(term in haystack for term in query_terms)

    query_rows = [row for row in rows if row_matches_query_scope(row)]
    filtered_rows = [
        row
        for row in query_rows
        if not normalized_asset_group or str(row.get("asset_group", "")).lower() == normalized_asset_group
    ]
    filtered_rows.sort(key=lambda row: (str(row["category"]), str(row["source_kind"]), str(row["name"]), str(row["media_path"])))
    safe_limit = max(1, min(int(limit), 5000))
    safe_offset = max(0, int(offset))
    stats = {
        "total": len(rows),
        "categories": dict(Counter(str(row.get("category", "")) for row in rows)),
        "asset_groups": dict(Counter(str(row.get("asset_group", "")) for row in rows)),
        "query_asset_groups": dict(Counter(str(row.get("asset_group", "")) for row in query_rows)),
        "query_total": len(query_rows),
        "source_kinds": dict(Counter(str(row.get("source_kind", "")) for row in rows)),
    }
    return {
        "manifest_root": str(media_root),
        "query": query or "",
        "category": category or "",
        "asset_group": asset_group or "",
        "source_kind": source_kind or "",
        "total": len(rows),
        "filtered": len(filtered_rows),
        "offset": safe_offset,
        "limit": safe_limit,
        "stats": stats,
        "rows": filtered_rows[safe_offset : safe_offset + safe_limit],
    }


def search_fanxiu_static_visual_by_image(
    *,
    image_bytes: bytes,
    export_root: str | Path | None = None,
    query: str | None = None,
    category: str | None = None,
    asset_group: str | None = None,
    source_kind: str | None = None,
    limit: int = 80,
    offset: int = 0,
    max_prefilter: int = 600,
) -> dict[str, Any]:
    media_root = _visual_media_root(export_root)
    if not media_root.is_dir():
        raise FanxiuResourceError(f"静态视觉图鉴目录不存在，请先运行 static visual catalog：{media_root}")
    if not image_bytes:
        raise FanxiuResourceError("请上传一张用于相似搜索的图片")

    try:
        query_hashes = _compute_visual_similarity_hashes_from_bytes(image_bytes)
    except Exception as exc:
        raise FanxiuResourceError(f"无法读取上传图片：{exc}") from exc

    index_rows, index_stats = _load_visual_similarity_index_rows(media_root)
    query_terms = _visual_query_terms(query)
    normalized_category = (category or "").strip().lower()
    normalized_asset_group = (asset_group or "").strip().lower()
    normalized_source_kind = (source_kind or "").strip().lower()

    def row_matches_query_scope(row: dict[str, Any]) -> bool:
        if normalized_category and str(row.get("category", "")).lower() != normalized_category:
            return False
        if normalized_source_kind and str(row.get("source_kind", "")).lower() != normalized_source_kind:
            return False
        if not query_terms:
            return True
        haystack = " ".join(
            str(row.get(field, ""))
            for field in (
                "source_kind",
                "name",
                "category",
                "asset_group",
                "atlas_key",
                "source_path",
                "path_id",
                "media_path",
            )
        ).lower()
        return any(term in haystack for term in query_terms)

    query_rows = [row for row in index_rows if row_matches_query_scope(row)]
    matched_rows = [
        row
        for row in query_rows
        if not normalized_asset_group or str(row.get("asset_group", "")).lower() == normalized_asset_group
    ]
    query_phash = str(query_hashes["phash"])
    query_dhash = str(query_hashes["dhash"])
    prefiltered: list[tuple[int, dict[str, Any]]] = []
    for row in matched_rows:
        phash_distance = _hash_distance(query_phash, str(row.get("phash") or ""))
        if phash_distance is None:
            continue
        prefiltered.append((phash_distance, row))
    prefiltered.sort(key=lambda item: (item[0], str(item[1].get("name") or ""), str(item[1].get("media_path") or "")))

    safe_limit = max(1, min(int(limit), 5000))
    safe_offset = max(0, int(offset))
    safe_prefilter = max(safe_limit, min(max(int(max_prefilter), 20), 5000))

    ranked_rows: list[dict[str, Any]] = []
    for phash_distance, row in prefiltered[:safe_prefilter]:
        dhash_distance = _hash_distance(query_dhash, str(row.get("dhash") or ""))
        aspect_score = _aspect_similarity(
            query_hashes.get("normalized_width"),
            query_hashes.get("normalized_height"),
            row.get("normalized_width") or row.get("width"),
            row.get("normalized_height") or row.get("height"),
        )
        similarity = _visual_similarity_score(phash_distance, dhash_distance, aspect_score)
        ranked_row = dict(row)
        ranked_row.update(
            {
                "width": int(row.get("width") or 0),
                "height": int(row.get("height") or 0),
                "bytes": int(row.get("bytes") or 0),
                "phash_distance": phash_distance,
                "dhash_distance": dhash_distance if dhash_distance is not None else "",
                "aspect_similarity": round(aspect_score, 6),
                "similarity": round(similarity, 6),
                "similarity_percent": round(similarity * 100, 2),
            }
        )
        ranked_rows.append(ranked_row)

    ranked_rows.sort(
        key=lambda row: (
            -float(row.get("similarity") or 0),
            int(row.get("phash_distance") or 999),
            int(row.get("dhash_distance") or 999),
            str(row.get("name") or ""),
            str(row.get("media_path") or ""),
        )
    )
    for rank, row in enumerate(ranked_rows, start=1):
        row["similarity_rank"] = rank

    stats = {
        "total": len(index_rows),
        "filtered": len(ranked_rows),
        "prefiltered": min(len(prefiltered), safe_prefilter),
        "max_prefilter": safe_prefilter,
        "categories": dict(Counter(str(row.get("category", "")) for row in index_rows)),
        "asset_groups": dict(Counter(str(row.get("asset_group", "")) for row in index_rows)),
        "query_asset_groups": dict(Counter(str(row.get("asset_group", "")) for row in query_rows)),
        "query_total": len(query_rows),
        "source_kinds": dict(Counter(str(row.get("source_kind", "")) for row in index_rows)),
        **index_stats,
    }
    return {
        "manifest_root": str(media_root),
        "query": query or "",
        "category": category or "",
        "asset_group": asset_group or "",
        "source_kind": source_kind or "",
        "total": len(index_rows),
        "filtered": len(ranked_rows),
        "offset": safe_offset,
        "limit": safe_limit,
        "stats": stats,
        "query_hash": {
            "phash": query_phash,
            "dhash": query_dhash,
            "phash_algorithm": _VISUAL_PHASH_ALGORITHM,
            "dhash_algorithm": _VISUAL_DHASH_ALGORITHM,
            "normalized_width": query_hashes.get("normalized_width"),
            "normalized_height": query_hashes.get("normalized_height"),
        },
        "rows": ranked_rows[safe_offset : safe_offset + safe_limit],
    }
