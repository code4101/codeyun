from __future__ import annotations

import csv
import hashlib
import json
import re
import ast
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from pyxllib.file.game_assets import load_unity_environment, locate_unity_bundle_offset

from backend.core.fanxiu_resources import (
    FanxiuResourceError,
    resolve_fanxiu_asset_path,
    resolve_fanxiu_export_root,
    resolve_fanxiu_resource_root,
)


_HASH_SUFFIX_RE = re.compile(r"^(?P<name>.+)_(?P<hash>[0-9a-fA-F]{32})$")
_SAFE_QUERY_SPLIT_RE = re.compile(r"\s+")
_STATIC_ASSET_FIELDNAMES = [
    "asset_id",
    "asset_group",
    "source_kind",
    "category",
    "name",
    "stem",
    "hash_suffix",
    "relative_path",
    "bytes",
    "suffix",
    "unity_magic",
    "unity_offset",
    "mesh_count",
    "mesh_vertices",
    "mesh_faces",
    "material_count",
    "texture_count",
    "animation_count",
    "ui_gameobject_count",
    "visible_data_type",
    "unity_object_count",
    "unity_object_types",
    "unity_primary_type",
    "unity_named_objects",
    "unity_script_names",
    "unity_read_error_count",
    "unity_parse_status",
    "unity_parse_error",
    "detail_status",
    "semantic_id",
    "semantic_group",
    "semantic_type",
    "semantic_name",
    "semantic_summary",
    "semantic_refs",
    "semantic_visual_count",
    "semantic_visual_names",
    "semantic_visual_categories",
    "semantic_visual_media_paths",
    "semantic_catalog_version",
    "semantic_variant_count",
    "semantic_variant_refs",
    "linked_asset_count",
    "linked_asset_groups",
    "linked_asset_names",
    "linked_asset_paths",
    "primary_asset_path",
]
_STATIC_ASSET_SOURCE_GROUPS: dict[str, str] = {
    "model": "model",
    "effect": "effect",
    "uieffect": "effect",
    "ui": "ui",
    "scenepart": "scene",
    "wholescene": "scene",
    "animationclip": "animation",
    "animatorcontroller": "animation",
    "playable": "animation",
}
_STATIC_ASSET_QUERY_ALIASES: dict[str, list[str]] = {
    "模型": ["model", "pre_", "mod_"],
    "素材": ["model", "effect", "ui", "scene", "animation"],
    "特效": ["effect", "eff_", "fx"],
    "界面": ["ui", "win", "view", "panel"],
    "道具": ["item", "icon_item"],
    "功能": ["openfunction", "function", "mainui", "common_function"],
    "活动": ["activity", "gift", "charge", "mainui"],
    "礼包": ["gift", "charge", "activitygift"],
    "绿品": ["绿瓶", "小绿瓶", "littlebottle", "bottleworld", "common_icon_1002", "ui_glassworld"],
    "绿瓶": ["小绿瓶", "littlebottle", "bottleworld", "common_icon_1002", "ui_glassworld"],
    "小绿瓶": ["littlebottle", "bottleworld", "worldinthebottle", "common_icon_1002", "ui_glassworld"],
    "炼丹炉": ["丹炉", "liandanlu", "pre_common_liandanlu", "pre_scenemodel_liandanlu", "bottleworld"],
    "首充": ["首充豪礼", "firstcharge", "mainui_icon_0737", "common_function_icon_0006"],
    "公告": ["notice", "gonggao", "announcement", "annou", "bulletin", "doupold_ggl", "ggl_zw"],
    "游戏公告": ["notice", "gonggao", "announcement", "annou", "bulletin", "doupold_ggl", "ggl_zw"],
    "场景": ["scene", "scenepart", "wholescene"],
    "动画": ["animation", "anim", "clip", "controller"],
    "法宝": ["fabao", "talisman"],
    "飞剑": ["feijian", "flysword"],
    "武器": ["weapon"],
    "翅膀": ["wing"],
    "伙伴": ["partner"],
    "怪物": ["monster"],
    "首领": ["boss"],
    "粒子": ["particle", "particlesystem", "effect"],
    "贴图": ["texture", "texture2d"],
    "材质": ["material"],
    "脚本": ["mono", "monobehaviour", "monoscript"],
    "timeline": ["timeline", "playable"],
}
_STATIC_ASSET_CATALOG_VIEWS = {"raw", "gallery", "semantic"}
_SAFE_PREVIEW_STEM_RE = re.compile(r"[^0-9A-Za-z_.-]+")
ASSET_PREVIEW_CACHE_VERSION = "v3"
ASSET_SEMANTIC_CATALOG_VERSION = "v4"
_UNITY_OBJECT_NAME_TYPES = {
    "AnimationClip",
    "AnimatorController",
    "GameObject",
    "Material",
    "Mesh",
    "MonoScript",
    "ParticleSystem",
    "Sprite",
    "Texture2D",
}
_VISIBLE_DATA_TYPE_LABELS: dict[str, str] = {
    "ui_prefab": "UI Prefab",
    "particle_effect": "Particle Effect",
    "skinned_mesh": "Skinned Mesh",
    "mesh_model": "Mesh Model",
    "animation_clip": "AnimationClip",
    "animator_controller": "AnimatorController",
    "timeline_config": "Timeline Config",
    "scene_prefab": "Scene Prefab",
    "script_config": "Script Config",
    "asset_bundle": "AssetBundle",
    "unity_asset": "Unity Asset",
}
_SEMANTIC_GROUP_ORDER = {
    "function": 0,
    "item": 1,
    "activity": 2,
    "activity_gift": 3,
    "model": 4,
    "monster": 5,
    "gongfa_skill": 6,
    "skill": 7,
    "buff": 8,
}
_SEMANTIC_CONFIG_TABLES = (
    "Model",
    "Skill",
    "BuffResource",
    "Monster",
    "GongfaSkill",
    "Item",
    "OpenFunction",
    "Activity",
    "ActivityGift",
)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _asset_query_terms(query: str | None) -> list[str]:
    text = (query or "").strip().lower()
    if not text:
        return []
    terms = [item for item in _SAFE_QUERY_SPLIT_RE.split(text) if item]
    if not terms:
        terms = [text]
    for key, aliases in _STATIC_ASSET_QUERY_ALIASES.items():
        if key in text:
            terms.extend(alias.lower() for alias in aliases)
    seen: set[str] = set()
    return [term for term in terms if term and not (term in seen or seen.add(term))]


def _normalize_static_asset_catalog_view(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text in {"business", "semantic"}:
        return "semantic"
    if text in {"wiki", "gallery"}:
        return "gallery"
    if text in _STATIC_ASSET_CATALOG_VIEWS:
        return text
    return "raw"


def _is_static_asset_gallery_candidate(row: dict[str, Any]) -> bool:
    name = str(row.get("name") or row.get("stem") or "").strip().lower()
    category = str(row.get("category") or "").strip().lower()
    visible_type = str(row.get("visible_data_type") or "").strip().lower()
    if not name:
        return False
    if name.startswith(("pre_", "mat_", "tex_")):
        return False
    if name.endswith("_low"):
        return False
    if category in {"basemodel", "material"}:
        return False
    if visible_type in {"asset_bundle", "unity_asset"}:
        return False
    return True


def _matches_static_asset_catalog_view(row: dict[str, Any], catalog_view: str) -> bool:
    if catalog_view == "gallery":
        return _is_static_asset_gallery_candidate(row)
    return True


def _config_rows(output_dir: Path, table: str) -> list[dict[str, str]]:
    return _read_tsv(output_dir.parent / table / "rows.tsv")


def _first_text(row: dict[str, Any], *fields: str) -> str:
    for field in fields:
        text = str(row.get(field, "") or "").strip()
        if text:
            return text
    return ""


def _parse_listish_tokens(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        data = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        data = text
    if isinstance(data, (list, tuple, set)):
        return [str(item).strip() for item in data if str(item).strip()]
    return [item for item in re.split(r"[|,;\s]+", str(data)) if item]


def _path_token(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    return Path(text).name.lower()


def _asset_semantic_catalog_path(output_dir: Path) -> Path:
    return output_dir / "static_asset_semantic_catalog.tsv"


def _asset_semantic_input_paths(output_dir: Path) -> list[Path]:
    paths = [output_dir / "static_asset_catalog.tsv"]
    visual_catalog_path = _visual_catalog_path(output_dir)
    if visual_catalog_path.is_file():
        paths.append(visual_catalog_path)
    for table in _SEMANTIC_CONFIG_TABLES:
        config_path = output_dir.parent / table / "rows.tsv"
        if config_path.is_file():
            paths.append(config_path)
    return paths


def _semantic_catalog_version_matches(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                return str(row.get("semantic_catalog_version") or "") == ASSET_SEMANTIC_CATALOG_VERSION
    except Exception:
        return False
    return False


def _tsv_fieldnames(path: Path) -> list[str]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        try:
            return next(reader)
        except StopIteration:
            return []


def _visual_catalog_dir(output_dir: Path) -> Path:
    return output_dir.parent / "visual_catalog"


def _visual_catalog_path(output_dir: Path) -> Path:
    visual_dir = _visual_catalog_dir(output_dir)
    full_catalog = visual_dir / "visual_asset_catalog.tsv"
    if full_catalog.is_file():
        return full_catalog
    return visual_dir / "icon_logo_sprite_catalog.tsv"


def _relative_visual_media_path(image_path: Any, visual_dir: Path) -> str:
    text = str(image_path or "").strip()
    if not text:
        return ""
    raw_path = Path(text)
    media_path = raw_path.resolve() if raw_path.is_absolute() else (visual_dir / raw_path).resolve()
    if not _is_relative_to(media_path, visual_dir.resolve()):
        return ""
    if media_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return ""
    try:
        return media_path.relative_to(visual_dir.resolve()).as_posix()
    except ValueError:
        return ""


def _build_visual_lookup(output_dir: Path) -> dict[str, list[dict[str, Any]]]:
    visual_dir = _visual_catalog_dir(output_dir)
    catalog_path = _visual_catalog_path(output_dir)
    lookup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not catalog_path.is_file():
        return lookup
    for row in _read_tsv(catalog_path):
        name = str(row.get("name") or "").strip()
        media_path = _relative_visual_media_path(row.get("image_path"), visual_dir)
        if not name or not media_path:
            continue
        item = {
            "name": name,
            "category": str(row.get("category") or ""),
            "asset_group": str(row.get("asset_group") or ""),
            "atlas_key": str(row.get("atlas_key") or ""),
            "width": str(row.get("width") or ""),
            "height": str(row.get("height") or ""),
            "media_path": media_path,
            "source_path": str(row.get("relative_source_path") or ""),
        }
        lookup[name.lower()].append(item)
    return lookup


def _visual_lookup_by_token(lookup: dict[str, list[dict[str, Any]]], token: Any) -> list[dict[str, Any]]:
    text = str(token or "").strip().replace("\\", "/")
    if not text:
        return []
    keys = {text.lower(), Path(text).stem.lower(), Path(text).name.lower()}
    result: list[dict[str, Any]] = []
    for key in keys:
        result.extend(lookup.get(key, []))
    return _dedupe_visual_rows(result)


def _visual_lookup_by_fields(
    lookup: dict[str, list[dict[str, Any]]],
    row: dict[str, Any],
    fields: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[str]]:
    visuals: list[dict[str, Any]] = []
    refs: list[str] = []
    for field in fields:
        value = row.get(field, "")
        text = str(value or "").strip()
        if not text:
            continue
        field_hits: list[dict[str, Any]] = []
        for token in _parse_listish_tokens(value):
            field_hits.extend(_visual_lookup_by_token(lookup, token))
        if not field_hits:
            field_hits.extend(_visual_lookup_by_token(lookup, text))
        if field_hits:
            refs.append(f"{field}={text}")
            visuals.extend(field_hits)
    return _dedupe_visual_rows(visuals), refs


def _dedupe_visual_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("media_path") or row.get("name") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _visual_refs_text(visuals: list[dict[str, Any]], *, field: str, limit: int = 12) -> str:
    values = _unique_limited([str(row.get(field, "")) for row in visuals], limit=limit)
    return " | ".join(values)


def _split_ref_text(value: Any, *, separator: str = "|") -> list[str]:
    return [item.strip() for item in str(value or "").split(separator) if item.strip()]


def _merge_group_count_text(values: list[str]) -> str:
    counts: Counter[str] = Counter()
    for value in values:
        for part in str(value or "").split(";"):
            text = part.strip()
            if not text:
                continue
            key, sep, raw_count = text.partition(":")
            try:
                count = int(float(raw_count)) if sep else 1
            except ValueError:
                count = 1
            if key.strip():
                counts[key.strip()] += count
    return "; ".join(f"{key}:{count}" for key, count in counts.most_common())


def _semantic_catalog_group_key(row: dict[str, Any]) -> tuple[str, str, str] | None:
    group = str(row.get("semantic_group") or "").strip()
    name = str(row.get("semantic_name") or row.get("name") or "").strip()
    visual_names = str(row.get("semantic_visual_names") or "").strip()
    if not name:
        return None
    if group == "model":
        linked_assets = str(row.get("linked_asset_paths") or row.get("linked_asset_names") or "").strip()
        if not visual_names or not linked_assets:
            return None
        return group, linked_assets, visual_names
    if group == "monster":
        linked_assets = str(row.get("linked_asset_paths") or row.get("linked_asset_names") or "").strip()
        if not visual_names or not linked_assets:
            return None
        return group, f"{name}|{linked_assets}", visual_names
    if group not in {"activity", "activity_gift", "function", "gongfa_skill", "item", "skill"}:
        return None
    if group in {"activity", "activity_gift", "function", "gongfa_skill", "item"} and not visual_names:
        return None
    return group, name, visual_names


def _merge_semantic_catalog_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) <= 1:
        row = dict(rows[0])
        row["semantic_variant_count"] = int(row.get("semantic_variant_count") or 1)
        row["semantic_variant_refs"] = str(row.get("semantic_variant_refs") or row.get("semantic_id") or "")
        return row
    first = dict(rows[0])
    variant_ids = _unique_limited([str(row.get("semantic_id") or "") for row in rows], limit=80)
    visual_names = _unique_limited(
        [item for row in rows for item in _split_ref_text(row.get("semantic_visual_names"))],
        limit=24,
    )
    visual_paths = _unique_limited(
        [item for row in rows for item in _split_ref_text(row.get("semantic_visual_media_paths"))],
        limit=24,
    )
    linked_names = _unique_limited(
        [item for row in rows for item in _split_ref_text(row.get("linked_asset_names"))],
        limit=24,
    )
    linked_paths = _unique_limited(
        [item for row in rows for item in _split_ref_text(row.get("linked_asset_paths"))],
        limit=24,
    )
    refs = _unique_limited(
        [item for row in rows for item in _split_ref_text(row.get("semantic_refs"))],
        limit=24,
    )
    summary = str(first.get("semantic_summary") or first.get("name") or "").strip()
    if summary:
        summary = f"{summary}（合并 {len(rows)} 个配置档位）"
    else:
        summary = f"合并 {len(rows)} 个配置档位"
    group = str(first.get("semantic_group") or "")
    first_name = str(first.get("semantic_name") or first.get("name") or "").strip()
    merged_name = first_name
    if group == "model" and first_name.isdigit():
        label = visual_names[0] if visual_names else (linked_names[0] if linked_names else first_name)
        merged_name = f"{label} / {len(rows)} 个模型配置"
    digest = hashlib.sha1(
        "|".join(
            [
                group,
                merged_name,
                "|".join(visual_names),
                "|".join(linked_paths),
            ]
        ).encode("utf-8", errors="ignore")
    ).hexdigest()[:12]
    first.update(
        {
            "asset_id": f"{group}:group:{digest}",
            "name": merged_name,
            "stem": f"{group}:group:{digest}",
            "semantic_id": f"{group}:group:{digest}",
            "semantic_name": merged_name,
            "semantic_summary": summary,
            "semantic_refs": " | ".join(refs[:18]),
            "semantic_visual_count": len(visual_paths) or len(visual_names),
            "semantic_visual_names": " | ".join(visual_names),
            "semantic_visual_categories": _merge_group_count_text([str(row.get("semantic_visual_categories") or "") for row in rows]),
            "semantic_visual_media_paths": " | ".join(visual_paths),
            "semantic_variant_count": len(rows),
            "semantic_variant_refs": " | ".join(variant_ids),
            "linked_asset_count": len(linked_paths) if linked_paths else sum(int(row.get("linked_asset_count") or 0) for row in rows),
            "linked_asset_groups": _merge_group_count_text([str(row.get("linked_asset_groups") or "") for row in rows]),
            "linked_asset_names": " | ".join(linked_names),
            "linked_asset_paths": " | ".join(linked_paths),
            "primary_asset_path": linked_paths[0] if linked_paths else str(first.get("primary_asset_path") or ""),
            "relative_path": linked_paths[0] if linked_paths else str(first.get("relative_path") or ""),
            "bytes": sum(int(row.get("bytes") or 0) for row in rows),
        }
    )
    return first


def _collapse_semantic_catalog_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collapsed: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = _semantic_catalog_group_key(row)
        if key is None:
            collapsed.append(row)
        else:
            grouped[key].append(row)
    for group_rows in grouped.values():
        collapsed.append(_merge_semantic_catalog_group(group_rows))
    return collapsed


def _build_asset_lookup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_numeric: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        name = str(row.get("name") or "").strip().lower()
        stem = str(row.get("stem") or "").strip().lower()
        rel = str(row.get("relative_path") or "").strip().lower()
        for key in {name, stem, _path_token(rel)}:
            if key:
                by_name[key].append(row)
        for number in set(re.findall(r"\d{4,}", " ".join([name, stem, rel]))):
            by_numeric[number].append(row)
    return {"by_name": by_name, "by_numeric": by_numeric}


def _asset_lookup_by_token(lookup: dict[str, Any], token: Any) -> list[dict[str, Any]]:
    key = _path_token(token)
    if not key:
        return []
    return list(lookup["by_name"].get(key, []))


def _asset_lookup_by_number(lookup: dict[str, Any], number: Any) -> list[dict[str, Any]]:
    text = str(number or "").strip()
    if not re.fullmatch(r"\d{4,}", text):
        return []
    candidates = lookup["by_numeric"].get(text, [])
    result: list[dict[str, Any]] = []
    for row in candidates:
        name = str(row.get("name") or "").lower()
        if (
            name == text
            or name.startswith(f"{text}_")
            or name.startswith(f"timeline{text}")
            or name.startswith(f"eff_jq_{text}")
            or f"_{text}_" in name
        ):
            result.append(row)
    return result


def _dedupe_asset_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("relative_path") or row.get("asset_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _asset_refs_text(assets: list[dict[str, Any]], *, field: str, limit: int = 12) -> str:
    values = _unique_limited([str(row.get(field, "")) for row in assets], limit=limit)
    return " | ".join(values)


def _semantic_row(
    *,
    semantic_id: str,
    semantic_group: str,
    semantic_type: str,
    name: str,
    summary: str,
    refs: list[str],
    assets: list[dict[str, Any]],
    visuals: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    linked_assets = _dedupe_asset_rows(assets)
    visual_rows = _dedupe_visual_rows(visuals or [])
    if not linked_assets and not visual_rows:
        return None
    primary = linked_assets[0] if linked_assets else {}
    linked_groups = Counter(str(row.get("asset_group", "")) for row in linked_assets if row.get("asset_group"))
    linked_names = _asset_refs_text(linked_assets, field="name", limit=18)
    linked_paths = _asset_refs_text(linked_assets, field="relative_path", limit=18)
    visual_categories = Counter(str(row.get("category", "")) for row in visual_rows if row.get("category"))
    visual_names = _visual_refs_text(visual_rows, field="name", limit=18)
    visual_media_paths = _visual_refs_text(visual_rows, field="media_path", limit=18)
    title = name or semantic_id
    return {
        "asset_id": semantic_id,
        "asset_group": semantic_group,
        "source_kind": str(primary.get("source_kind", "")),
        "category": semantic_type,
        "name": title,
        "stem": semantic_id,
        "hash_suffix": "",
        "relative_path": str(primary.get("relative_path", "")),
        "bytes": sum(int(row.get("bytes") or 0) for row in linked_assets),
        "suffix": "",
        "unity_magic": "",
        "unity_offset": 0,
        "mesh_count": sum(int(row.get("mesh_count") or 0) for row in linked_assets),
        "mesh_vertices": sum(int(row.get("mesh_vertices") or 0) for row in linked_assets),
        "mesh_faces": sum(int(row.get("mesh_faces") or 0) for row in linked_assets),
        "material_count": sum(int(row.get("material_count") or 0) for row in linked_assets),
        "texture_count": sum(int(row.get("texture_count") or 0) for row in linked_assets),
        "animation_count": sum(int(row.get("animation_count") or 0) for row in linked_assets),
        "ui_gameobject_count": sum(int(row.get("ui_gameobject_count") or 0) for row in linked_assets),
        "visible_data_type": f"semantic_{semantic_type}",
        "unity_object_count": sum(int(row.get("unity_object_count") or 0) for row in linked_assets),
        "unity_object_types": _asset_refs_text(linked_assets, field="unity_object_types", limit=6),
        "unity_primary_type": str(primary.get("unity_primary_type", "")),
        "unity_named_objects": _asset_refs_text(linked_assets, field="unity_named_objects", limit=6),
        "unity_script_names": _asset_refs_text(linked_assets, field="unity_script_names", limit=6),
        "unity_read_error_count": sum(int(row.get("unity_read_error_count") or 0) for row in linked_assets),
        "unity_parse_status": "semantic",
        "unity_parse_error": "",
        "detail_status": "semantic",
        "semantic_id": semantic_id,
        "semantic_group": semantic_group,
        "semantic_type": semantic_type,
        "semantic_name": title,
        "semantic_summary": summary,
        "semantic_refs": " | ".join(_unique_limited(refs, limit=18)),
        "semantic_visual_count": len(visual_rows),
        "semantic_visual_names": visual_names,
        "semantic_visual_categories": "; ".join(f"{key}:{count}" for key, count in visual_categories.most_common()),
        "semantic_visual_media_paths": visual_media_paths,
        "semantic_catalog_version": ASSET_SEMANTIC_CATALOG_VERSION,
        "semantic_variant_count": 1,
        "semantic_variant_refs": semantic_id,
        "linked_asset_count": len(linked_assets),
        "linked_asset_groups": "; ".join(f"{key}:{count}" for key, count in linked_groups.most_common()),
        "linked_asset_names": linked_names,
        "linked_asset_paths": linked_paths,
        "primary_asset_path": str(primary.get("relative_path", "")),
    }


def _append_semantic_row(rows: list[dict[str, Any]], seen: set[str], row: dict[str, Any] | None) -> None:
    if not row:
        return
    key = str(row.get("semantic_id") or row.get("asset_id") or "")
    if not key or key in seen:
        return
    seen.add(key)
    rows.append(row)


def _config_ref_values(row: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    refs: list[str] = []
    for field in fields:
        value = str(row.get(field) or "").strip().replace("|", "/")
        if value:
            refs.append(f"{field}={value}")
    return refs


def _open_function_asset_refs(lookup: dict[str, Any], row: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    assets: list[dict[str, Any]] = []
    refs: list[str] = []
    for field in ("luaPath", "windowId"):
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        refs.append(f"{field}={value}")
        tokens = [value]
        tokens.append(value.replace(".", "/").rsplit("/", 1)[-1])
        for token in tokens:
            assets.extend(_asset_lookup_by_token(lookup, token))
    return _dedupe_asset_rows(assets), refs


def _append_small_bottle_semantic_row(
    semantic_rows: list[dict[str, Any]],
    seen: set[str],
    *,
    lookup: dict[str, Any],
    visual_lookup: dict[str, list[dict[str, Any]]],
) -> None:
    visuals = _visual_lookup_by_token(visual_lookup, "common_icon_1002")
    if not visuals:
        return
    assets: list[dict[str, Any]] = []
    for token in (
        "littlebottleadvanceview",
        "bottleworld",
        "bottleplayer",
        "ui_glassworld",
        "pre_eff_ui_bottle_chuansuo",
        "pre_eff_ui_worldinthebottle_01_jingchupingzixianjie",
    ):
        assets.extend(_asset_lookup_by_token(lookup, token))
    _append_semantic_row(
        semantic_rows,
        seen,
        _semantic_row(
            semantic_id="function:small_bottle",
            semantic_group="function",
            semantic_type="function",
            name="小绿瓶 / 修炼入口",
            summary="BookMainPanel.UpdateBottleImg 使用 common_icon_1002；OpenFunction 小绿瓶入口跳转 LittleBottleAdvanceView/BottleWorld。",
            refs=[
                "OpenFunction=8008 跳转小绿瓶",
                "OpenFunction=15000 小绿瓶升级",
                "BookMainPanel.lua:163 common_icon_1002",
                "bottleworld ConfigValue[70]=common,common_icon_1002",
            ],
            assets=assets,
            visuals=visuals,
        ),
    )


def build_fanxiu_static_asset_semantic_catalog(
    *,
    export_root: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    output_dir = _catalog_dir(export_root)
    manifest_path = output_dir / "static_asset_catalog.tsv"
    if not manifest_path.is_file():
        raise FanxiuResourceError(f"静态素材图鉴不存在：{manifest_path}")
    semantic_path = _asset_semantic_catalog_path(output_dir)
    semantic_inputs = _asset_semantic_input_paths(output_dir)
    semantic_fields = set(_tsv_fieldnames(semantic_path))
    expected_fields = set(_STATIC_ASSET_FIELDNAMES)
    inputs_mtime = max(path.stat().st_mtime for path in semantic_inputs)
    if (
        semantic_path.is_file()
        and not force
        and semantic_path.stat().st_mtime >= inputs_mtime
        and expected_fields.issubset(semantic_fields)
        and _semantic_catalog_version_matches(semantic_path)
    ):
        rows = _read_tsv(semantic_path)
        return {"manifest": str(semantic_path), "semantic_count": len(rows), "cached": True}

    asset_rows = _read_tsv(manifest_path)
    lookup = _build_asset_lookup(asset_rows)
    visual_lookup = _build_visual_lookup(output_dir)
    semantic_rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    model_rows = _config_rows(output_dir, "Model")
    model_assets_by_id: dict[str, list[dict[str, Any]]] = {}
    model_visuals_by_id: dict[str, list[dict[str, Any]]] = {}
    model_names_by_id: dict[str, str] = {}
    for row in model_rows:
        model_id = str(row.get("id") or row.get("_row_key") or "").strip()
        if not model_id:
            continue
        assets: list[dict[str, Any]] = []
        refs: list[str] = []
        for field in ("ResPath", "AniController", "RTAniController"):
            value = row.get(field, "")
            if value:
                refs.append(f"{field}={value}")
                assets.extend(_asset_lookup_by_token(lookup, value))
        for field in ("idle", "walk", "fightIdle", "run", "dead", "talkidle", "weakIdle", "weakWalk"):
            for token in _parse_listish_tokens(row.get(field, "")):
                refs.append(f"{field}={token}")
                assets.extend(_asset_lookup_by_token(lookup, token))
        assets = _dedupe_asset_rows(assets)
        visuals, visual_refs = _visual_lookup_by_fields(visual_lookup, row, ("head", "lieHead", "lie"))
        refs.extend(visual_refs)
        model_assets_by_id[model_id] = assets
        model_visuals_by_id[model_id] = visuals
        model_names_by_id[model_id] = str(row.get("name") or "")
        _append_semantic_row(
            semantic_rows,
            seen,
            _semantic_row(
                semantic_id=f"model:{model_id}",
                semantic_group="model",
                semantic_type="model",
                name=str(row.get("name") or model_id),
                summary=f"Model {model_id} 组合了 ResPath、AnimatorController 和动作片段。",
                refs=refs,
                assets=assets,
                visuals=visuals,
            ),
        )

    skill_rows = _config_rows(output_dir, "Skill")
    skill_assets_by_id: dict[str, list[dict[str, Any]]] = {}
    skill_names_by_id: dict[str, str] = {}
    for row in skill_rows:
        skill_id = str(row.get("id") or row.get("_row_key") or "").strip()
        if not skill_id:
            continue
        assets = []
        refs = []
        for field in ("timelineId", "maxRankTimelineId", "jian_timelineId", "mo_timelineId", "sha_timelineId", "xian_timelineId"):
            for value in _parse_listish_tokens(row.get(field, "")):
                if value and value not in {"0", "None"}:
                    refs.append(f"{field}={value}")
                    assets.extend(_asset_lookup_by_token(lookup, f"timeline{value}"))
                    assets.extend(_asset_lookup_by_number(lookup, value))
        assets.extend(_asset_lookup_by_number(lookup, skill_id))
        assets = _dedupe_asset_rows(assets)
        visuals, visual_refs = _visual_lookup_by_fields(visual_lookup, row, ("icon",))
        refs.extend(visual_refs)
        skill_assets_by_id[skill_id] = assets
        skill_names_by_id[skill_id] = _first_text(row, "name_plain", "name") or skill_id
        _append_semantic_row(
            semantic_rows,
            seen,
            _semantic_row(
                semantic_id=f"skill:{skill_id}",
                semantic_group="skill",
                semantic_type="skill",
                name=skill_names_by_id[skill_id],
                summary=f"Skill {skill_id} 关联 timeline / effect / animation 资源。",
                refs=refs,
                assets=assets,
                visuals=visuals,
            ),
        )

    for row in _config_rows(output_dir, "BuffResource"):
        buff_id = str(row.get("id") or row.get("_row_key") or "").strip()
        if not buff_id:
            continue
        assets = _asset_lookup_by_number(lookup, buff_id)
        refs = [f"id={buff_id}"]
        for field in ("stateEffect", "viewSkillEffect", "getBuff", "removeBuff"):
            value = str(row.get(field) or "").strip()
            if value and value not in {"0", "None"}:
                refs.append(f"{field}={value}")
                assets.extend(_asset_lookup_by_number(lookup, value))
        assets = _dedupe_asset_rows(assets)
        if not assets:
            continue
        visuals, visual_refs = _visual_lookup_by_fields(visual_lookup, row, ("icon",))
        refs.extend(visual_refs)
        _append_semantic_row(
            semantic_rows,
            seen,
            _semantic_row(
                semantic_id=f"buff:{buff_id}",
                semantic_group="buff",
                semantic_type="buff",
                name=_first_text(row, "name_plain", "name") or buff_id,
                summary=_first_text(row, "desc_plain", "desc") or f"BuffResource {buff_id}",
                refs=refs,
                assets=assets,
                visuals=visuals,
            ),
        )

    for row in _config_rows(output_dir, "Monster"):
        monster_id = str(row.get("id") or row.get("_row_key") or "").strip()
        if not monster_id:
            continue
        assets: list[dict[str, Any]] = []
        refs: list[str] = []
        model_id = str(row.get("modelId") or "").strip()
        if model_id:
            refs.append(f"modelId={model_id}")
            assets.extend(model_assets_by_id.get(model_id, []))
        visuals = list(model_visuals_by_id.get(model_id, [])) if model_id else []
        for field in ("defaultSkill", "playerSkill", "specialSkill"):
            value = str(row.get(field) or "").strip()
            if value:
                refs.append(f"{field}={value}")
                for skill_id in re.findall(r"\d{4,}", value):
                    assets.extend(skill_assets_by_id.get(skill_id, []))
                    assets.extend(_asset_lookup_by_number(lookup, skill_id))
        title = _first_text(row, "name_plain", "name") or monster_id
        if model_id and model_names_by_id.get(model_id):
            title = f"{title} / {model_names_by_id[model_id]}"
        _append_semantic_row(
            semantic_rows,
            seen,
            _semantic_row(
                semantic_id=f"monster:{monster_id}",
                semantic_group="monster",
                semantic_type="monster",
                name=title,
                summary=f"Monster {monster_id} 由 modelId 和默认技能组合得到。",
                refs=refs,
                assets=assets,
                visuals=visuals,
            ),
        )

    for row in _config_rows(output_dir, "GongfaSkill"):
        row_key = str(row.get("_row_key") or row.get("id") or "").strip()
        skill_value = str(row.get("skill") or "").strip()
        if not row_key:
            continue
        assets: list[dict[str, Any]] = []
        refs: list[str] = []
        if skill_value:
            refs.append(f"skill={skill_value}")
            for skill_id in re.findall(r"\d{4,}", skill_value):
                assets.extend(skill_assets_by_id.get(skill_id, []))
                assets.extend(_asset_lookup_by_number(lookup, skill_id))
        title = _first_text(row, "skillName_plain", "skillName", "name_plain", "name") or row_key
        visuals, visual_refs = _visual_lookup_by_fields(visual_lookup, row, ("icon",))
        refs.extend(visual_refs)
        _append_semantic_row(
            semantic_rows,
            seen,
            _semantic_row(
                semantic_id=f"gongfa_skill:{row_key}",
                semantic_group="gongfa_skill",
                semantic_type="gongfa_skill",
                name=title,
                summary=_first_text(row, "describe_plain", "describe", "effectDescribe_plain", "effectDescribe")
                or f"GongfaSkill {row_key}",
                refs=refs,
                assets=assets,
                visuals=visuals,
            ),
        )

    for row in _config_rows(output_dir, "Item"):
        item_id = str(row.get("id") or row.get("_row_key") or "").strip()
        if not item_id:
            continue
        visuals, visual_refs = _visual_lookup_by_fields(visual_lookup, row, ("icon", "smallIcon"))
        if not visuals:
            continue
        refs = [f"id={item_id}"]
        refs.extend(_config_ref_values(row, ("quality", "type", "subType", "itemLabel", "iconPatch", "smallIconPatch")))
        refs.extend(visual_refs)
        _append_semantic_row(
            semantic_rows,
            seen,
            _semantic_row(
                semantic_id=f"item:{item_id}",
                semantic_group="item",
                semantic_type="item",
                name=_first_text(row, "name_plain", "name") or item_id,
                summary=_first_text(row, "effDescript_plain", "descript_plain", "effDescript", "descript") or f"Item {item_id}",
                refs=refs,
                assets=[],
                visuals=visuals,
            ),
        )

    for row in _config_rows(output_dir, "OpenFunction"):
        function_id = str(row.get("id") or row.get("_row_key") or "").strip()
        if not function_id:
            continue
        visuals, visual_refs = _visual_lookup_by_fields(visual_lookup, row, ("icon", "icon2", "iconShow", "waySprite"))
        assets, asset_refs = _open_function_asset_refs(lookup, row)
        refs = [f"id={function_id}"]
        refs.extend(_config_ref_values(row, ("type", "subType", "sort", "iconPatch", "wayAtlas")))
        refs.extend(asset_refs)
        refs.extend(visual_refs)
        _append_semantic_row(
            semantic_rows,
            seen,
            _semantic_row(
                semantic_id=f"function:{function_id}",
                semantic_group="function",
                semantic_type="function",
                name=_first_text(row, "name_plain", "name", "tittleName_plain", "tittleName") or function_id,
                summary=_first_text(row, "descript_plain", "descript", "descriptUnlock_plain", "descriptUnlock") or f"OpenFunction {function_id}",
                refs=refs,
                assets=assets,
                visuals=visuals,
            ),
        )

    _append_small_bottle_semantic_row(semantic_rows, seen, lookup=lookup, visual_lookup=visual_lookup)

    for row in _config_rows(output_dir, "Activity"):
        activity_id = str(row.get("id") or row.get("_row_key") or "").strip()
        if not activity_id:
            continue
        visuals, visual_refs = _visual_lookup_by_fields(
            visual_lookup,
            row,
            ("icon", "iconShow", "iconEffect", "waySprite", "btnPic", "btnPic2", "bg", "bgImg", "titleImg", "tittleImg", "riChengImg"),
        )
        if not visuals:
            continue
        refs = [f"id={activity_id}"]
        refs.extend(_config_ref_values(row, ("activityId", "baseId", "popType", "startTime", "endTime")))
        refs.extend(visual_refs)
        summary_parts = [
            _first_text(row, "tittleName_plain", "tittleName"),
            _first_text(row, "joinConditionDescribe_plain", "joinConditionDescribe"),
        ]
        _append_semantic_row(
            semantic_rows,
            seen,
            _semantic_row(
                semantic_id=f"activity:{activity_id}",
                semantic_group="activity",
                semantic_type="activity",
                name=_first_text(row, "name_plain", "name", "tittleName_plain", "tittleName") or activity_id,
                summary="；".join(item for item in summary_parts if item) or f"Activity {activity_id}",
                refs=refs,
                assets=[],
                visuals=visuals,
            ),
        )

    for row in _config_rows(output_dir, "ActivityGift"):
        gift_id = str(row.get("id") or row.get("_row_key") or "").strip()
        if not gift_id:
            continue
        visuals, visual_refs = _visual_lookup_by_fields(visual_lookup, row, ("icon", "bg", "bgImg", "titleImg"))
        if not visuals:
            continue
        refs = [f"id={gift_id}"]
        refs.extend(_config_ref_values(row, ("activityId", "costs", "reward", "times", "sort")))
        refs.extend(visual_refs)
        summary = _first_text(row, "title_plain", "title") or f"ActivityGift {gift_id}"
        reward = str(row.get("reward") or "").strip()
        if reward:
            summary = f"{summary}；奖励 {reward[:160]}"
        _append_semantic_row(
            semantic_rows,
            seen,
            _semantic_row(
                semantic_id=f"activity_gift:{gift_id}",
                semantic_group="activity_gift",
                semantic_type="activity_gift",
                name=_first_text(row, "title_plain", "title") or gift_id,
                summary=summary,
                refs=refs,
                assets=[],
                visuals=visuals,
            ),
        )

    semantic_rows = _collapse_semantic_catalog_rows(semantic_rows)
    semantic_rows.sort(
        key=lambda item: (
            _SEMANTIC_GROUP_ORDER.get(str(item.get("semantic_group", "")), 99),
            0 if int(item.get("semantic_visual_count") or 0) > 0 else 1,
            str(item.get("semantic_name", "")),
            str(item.get("semantic_id", "")),
        )
    )
    _write_tsv(semantic_path, semantic_rows, _STATIC_ASSET_FIELDNAMES)
    return {"manifest": str(semantic_path), "semantic_count": len(semantic_rows), "cached": False}


def _split_hashed_stem(stem: str) -> tuple[str, str]:
    match = _HASH_SUFFIX_RE.match(stem)
    if not match:
        return stem, ""
    return match.group("name"), match.group("hash").lower()


def _source_kind(relative_path: str) -> str:
    return relative_path.split("/", 1)[0] if relative_path else ""


def _category(relative_path: str) -> str:
    parts = relative_path.split("/")
    if len(parts) >= 3:
        return parts[1]
    return _source_kind(relative_path)


def _asset_group(source_kind: str) -> str:
    return _STATIC_ASSET_SOURCE_GROUPS.get(source_kind, source_kind or "asset")


def _asset_id(relative_path: str) -> str:
    return relative_path


def _type_counts_text(type_counts: Counter[str], *, limit: int = 10) -> str:
    return "; ".join(f"{key}:{count}" for key, count in type_counts.most_common(limit) if key)


def _unique_limited(values: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _infer_visible_data_type(source_kind: str, type_counts: Counter[str]) -> str:
    if type_counts.get("AnimatorController"):
        return "animator_controller"
    if type_counts.get("AnimationClip"):
        return "animation_clip"
    if type_counts.get("ParticleSystem") or type_counts.get("ParticleSystemRenderer"):
        return "particle_effect"
    if type_counts.get("RectTransform") or type_counts.get("CanvasRenderer") or type_counts.get("Canvas"):
        return "ui_prefab"
    if source_kind in {"scenepart", "wholescene"} and (
        type_counts.get("GameObject") or type_counts.get("Transform") or type_counts.get("MeshRenderer")
    ):
        return "scene_prefab"
    if type_counts.get("SkinnedMeshRenderer"):
        return "skinned_mesh"
    if type_counts.get("Mesh") or type_counts.get("MeshFilter") or type_counts.get("MeshRenderer"):
        return "mesh_model"
    if source_kind == "playable" or type_counts.get("PlayableDirector"):
        return "timeline_config"
    if type_counts.get("MonoBehaviour") or type_counts.get("MonoScript"):
        return "script_config"
    if type_counts.get("AssetBundle") and sum(type_counts.values()) == type_counts.get("AssetBundle"):
        return "asset_bundle"
    return "unity_asset"


def _parse_unity_object_summary(path: Path, *, source_kind: str, max_named_objects: int = 18) -> dict[str, Any]:
    if path.stat().st_size <= 0:
        return {"unity_parse_status": "empty", "unity_parse_error": "empty file"}
    try:
        env = load_unity_environment(path)
    except Exception as exc:  # pragma: no cover - true files vary by Unity bundle edge case
        return {"unity_parse_status": "error", "unity_parse_error": f"{type(exc).__name__}: {exc}"[:300]}

    type_counts: Counter[str] = Counter()
    named_objects: list[str] = []
    script_names: list[str] = []
    read_errors = 0
    for obj in getattr(env, "objects", []):
        object_type = getattr(getattr(obj, "type", None), "name", str(getattr(obj, "type", ""))) or "<unknown>"
        type_counts[str(object_type)] += 1
        if object_type not in _UNITY_OBJECT_NAME_TYPES:
            continue
        if len(named_objects) >= max_named_objects and object_type != "MonoScript":
            continue
        try:
            data = obj.read()
        except Exception:
            read_errors += 1
            continue
        name = str(getattr(data, "m_Name", "") or getattr(data, "name", "") or "").strip()
        if not name:
            continue
        if object_type == "MonoScript":
            script_names.append(name)
        if len(named_objects) < max_named_objects:
            named_objects.append(f"{object_type}:{name}")

    visible_data_type = _infer_visible_data_type(source_kind, type_counts)
    return {
        "visible_data_type": visible_data_type,
        "unity_object_count": sum(type_counts.values()),
        "unity_object_types": _type_counts_text(type_counts),
        "unity_primary_type": _VISIBLE_DATA_TYPE_LABELS.get(visible_data_type, visible_data_type),
        "unity_named_objects": " | ".join(_unique_limited(named_objects, limit=max_named_objects)),
        "unity_script_names": " | ".join(_unique_limited(script_names, limit=12)),
        "unity_read_error_count": read_errors,
        "unity_parse_status": "parsed",
        "unity_parse_error": "",
    }


def _collect_embedded_images(env: Any, *, limit: int = 24) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for obj in getattr(env, "objects", []):
        object_type = str(getattr(getattr(obj, "type", None), "name", ""))
        if object_type not in {"Sprite", "Texture2D"}:
            continue
        try:
            data = obj.read()
            image = getattr(data, "image", None)
        except Exception:
            continue
        if image is None:
            continue
        try:
            image = image.convert("RGBA")
        except Exception:
            continue
        name = _object_name(data) or f"{object_type}:{getattr(obj, 'path_id', len(images))}"
        images.append(
            {
                "name": name,
                "object_type": object_type,
                "path_id": int(getattr(obj, "path_id", 0) or 0),
                "width": int(getattr(image, "width", 0) or 0),
                "height": int(getattr(image, "height", 0) or 0),
                "image": image,
            }
        )
        if len(images) >= limit:
            break
    return images


def _safe_preview_image_stem(index: int, name: str) -> str:
    clean = _SAFE_PREVIEW_STEM_RE.sub("_", name).strip("._")[:48] or "image"
    return f"{index:03d}_{clean}"


def _write_original_image_previews(images: list[dict[str, Any]], output_dir: Path, catalog_root: Path) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for index, item in enumerate(images):
        image = item["image"]
        name = str(item.get("name") or f"image_{index}")
        output_path = output_dir / f"{_safe_preview_image_stem(index, name)}.png"
        image.save(output_path)
        media_path = output_path.relative_to(catalog_root).as_posix()
        items.append(
            {
                "name": name,
                "kind": "original_image",
                "object_type": item.get("object_type") or "",
                "path_id": item.get("path_id") or 0,
                "width": item.get("width") or int(getattr(image, "width", 0) or 0),
                "height": item.get("height") or int(getattr(image, "height", 0) or 0),
                "media_path": media_path,
                "is_original_image": True,
            }
        )
    return items


def _collect_rect_layout(env: Any) -> tuple[list[dict[str, Any]], Counter[str]]:
    gameobject_names: dict[int, str] = {}
    type_counts: Counter[str] = Counter()
    for obj in getattr(env, "objects", []):
        object_type = str(getattr(getattr(obj, "type", None), "name", ""))
        type_counts[object_type] += 1
        if object_type != "GameObject":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        path_id = int(getattr(obj, "path_id", 0))
        gameobject_names[path_id] = _object_name(data)

    rects: list[dict[str, Any]] = []
    for obj in getattr(env, "objects", []):
        object_type = str(getattr(getattr(obj, "type", None), "name", ""))
        if object_type != "RectTransform":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        path_id = int(getattr(obj, "path_id", 0))
        gameobject_id = _pptr_path_id(getattr(data, "m_GameObject", None))
        parent_id = _pptr_path_id(getattr(data, "m_Father", None))
        size = getattr(data, "m_SizeDelta", None)
        pos = getattr(data, "m_AnchoredPosition", None) or getattr(data, "m_LocalPosition", None)
        width = abs(_vec_number(size, "x", 0.0))
        height = abs(_vec_number(size, "y", 0.0))
        if width < 1 and height < 1:
            width = height = 24
        rects.append(
            {
                "id": path_id,
                "parent_id": parent_id,
                "name": gameobject_names.get(gameobject_id or 0, "") or f"RectTransform {path_id}",
                "x": _vec_number(pos, "x", 0.0),
                "y": _vec_number(pos, "y", 0.0),
                "width": max(width, 6.0),
                "height": max(height, 6.0),
            }
        )
    return rects, type_counts


def _write_ui_layout_preview(env: Any, output_path: Path) -> bool:
    rects, _type_counts = _collect_rect_layout(env)
    if not rects:
        return False

    rect_by_id = {rect["id"]: rect for rect in rects}
    children: dict[int, list[int]] = defaultdict(list)
    roots: list[int] = []
    for rect in rects:
        parent_id = rect.get("parent_id")
        if parent_id in rect_by_id:
            children[int(parent_id)].append(int(rect["id"]))
        else:
            roots.append(int(rect["id"]))

    def subtree_size(node_id: int) -> int:
        return 1 + sum(subtree_size(child_id) for child_id in children.get(node_id, []))

    root_id = max(roots or [int(rects[0]["id"])], key=subtree_size)
    positioned: list[dict[str, Any]] = []

    def walk(node_id: int, base_x: float, base_y: float, depth: int) -> None:
        rect = rect_by_id[node_id]
        current = dict(rect)
        current["abs_x"] = base_x + float(rect["x"])
        current["abs_y"] = base_y - float(rect["y"])
        current["depth"] = depth
        positioned.append(current)
        for child_id in children.get(node_id, [])[:80]:
            walk(child_id, current["abs_x"], current["abs_y"], depth + 1)

    walk(root_id, 0.0, 0.0, 0)
    if not positioned:
        return False
    positioned = sorted(positioned, key=lambda item: (int(item.get("depth", 0)), -(float(item["width"]) * float(item["height"]))))[:160]
    min_x = min(float(item["abs_x"]) - float(item["width"]) / 2 for item in positioned)
    max_x = max(float(item["abs_x"]) + float(item["width"]) / 2 for item in positioned)
    min_y = min(float(item["abs_y"]) - float(item["height"]) / 2 for item in positioned)
    max_y = max(float(item["abs_y"]) + float(item["height"]) / 2 for item in positioned)
    if max_x <= min_x or max_y <= min_y:
        return False

    pad = 26
    content_w = max(max_x - min_x, 1.0)
    content_h = max(max_y - min_y, 1.0)
    max_side = 1800.0
    scale = min(1.0, max_side / max(content_w, content_h))
    width = max(1, int(round(content_w * scale + pad * 2)))
    height = max(1, int(round(content_h * scale + pad * 2)))

    def sx(value: float) -> float:
        return pad + (value - min_x) * scale

    def sy(value: float) -> float:
        return pad + (value - min_y) * scale

    colors = ["#2bb3c0", "#e3a22c", "#7fbd58", "#de6f75", "#8f7cc3", "#4b9ad8"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8f4e8"/>',
    ]
    for item in positioned:
        x = sx(float(item["abs_x"]) - float(item["width"]) / 2)
        y = sy(float(item["abs_y"]) - float(item["height"]) / 2)
        w = max(3.0, float(item["width"]) * scale)
        h = max(3.0, float(item["height"]) * scale)
        depth = int(item.get("depth", 0))
        color = colors[depth % len(colors)]
        opacity = max(0.12, 0.32 - min(depth, 8) * 0.025)
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'fill="{color}" fill-opacity="{opacity:.2f}" stroke="{color}" stroke-width="1"/>'
        )
        if w > 52 and h > 14:
            name = xml_escape(str(item.get("name", ""))[:28])
            parts.append(f'<text x="{x + 4:.1f}" y="{y + 13:.1f}" font-size="10" fill="#182230">{name}</text>')
    parts.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts), encoding="utf-8")
    return True


def _write_object_summary_preview(env: Any, output_path: Path) -> None:
    type_counts: Counter[str] = Counter()
    names: list[str] = []
    for obj in getattr(env, "objects", []):
        object_type = str(getattr(getattr(obj, "type", None), "name", "")) or "Object"
        type_counts[object_type] += 1
        if object_type not in _UNITY_OBJECT_NAME_TYPES or len(names) >= 28:
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        name = _object_name(data)
        if name:
            names.append(f"{object_type}:{name}")
    width = 920
    height = 620
    max_count = max(type_counts.values() or [1])
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8f4e8"/>',
    ]
    y = 28
    for object_type, count in type_counts.most_common(12):
        bar_w = 520 * count / max_count
        parts.append(f'<text x="30" y="{y + 15}" font-size="13" fill="#182230">{xml_escape(object_type)}</text>')
        parts.append(f'<rect x="190" y="{y}" width="{bar_w:.1f}" height="20" fill="#2bb3c0" fill-opacity="0.34" stroke="#2bb3c0"/>')
        parts.append(f'<text x="{200 + bar_w:.1f}" y="{y + 15}" font-size="12" fill="#182230">{count}</text>')
        y += 30
    chip_x = 30
    chip_y = y + 22
    for name in _unique_limited(names, limit=24):
        label = xml_escape(name[:48])
        chip_w = min(360, 10 + len(label) * 7)
        if chip_x + chip_w > width - 28:
            chip_x = 30
            chip_y += 30
        parts.append(f'<rect x="{chip_x}" y="{chip_y}" width="{chip_w}" height="22" fill="#fffdf4" stroke="#d8c48c"/>')
        parts.append(f'<text x="{chip_x + 5}" y="{chip_y + 15}" font-size="11" fill="#694f14">{label}</text>')
        chip_x += chip_w + 8
    parts.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts), encoding="utf-8")


def _preview_manifest_path(preview_root: Path, stem: str) -> Path:
    return preview_root / f"{stem}.json"


def _cached_preview_manifest(path: Path, catalog_root: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return None
    for item in items:
        media_path = str(item.get("media_path") or "")
        if not media_path:
            return None
        target = (catalog_root / media_path).resolve()
        if not _is_relative_to(target, catalog_root.resolve()) or not target.is_file():
            return None
    data["cached"] = True
    return data


def _write_preview_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_fanxiu_static_asset_preview_manifest(
    path: str | Path,
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    asset_path, root, relative_path = resolve_fanxiu_asset_path(path, resource_root=resource_root)
    catalog_root = _catalog_dir(export_root)
    preview_root = _preview_dir(export_root)
    stem = _preview_stem(relative_path)
    manifest_path = _preview_manifest_path(preview_root, stem)
    if not force:
        cached = _cached_preview_manifest(manifest_path, catalog_root)
        if cached:
            cached["resource_root"] = str(root)
            return cached

    try:
        env = load_unity_environment(asset_path)
    except Exception as exc:
        output_path = preview_root / f"{stem}.svg"
        _write_object_error_preview(output_path, error=f"{type(exc).__name__}: {exc}")
        media_path = output_path.relative_to(catalog_root).as_posix()
        result = {
            "resource_root": str(root),
            "relative_path": relative_path,
            "cached": False,
            "preview_kind": "error",
            "items": [
                {
                    "name": Path(relative_path).stem,
                    "kind": "error_svg",
                    "media_path": media_path,
                    "is_original_image": False,
                }
            ],
        }
        _write_preview_manifest(manifest_path, result)
        return result

    images = _collect_embedded_images(env)
    if images:
        items = _write_original_image_previews(images, preview_root / f"{stem}_images", catalog_root)
        preview_kind = "original_images"
    else:
        output_path = preview_root / f"{stem}.svg"
        if _write_ui_layout_preview(env, output_path):
            preview_kind = "ui_layout"
            kind = "layout_svg"
        else:
            _write_object_summary_preview(env, output_path)
            preview_kind = "object_summary"
            kind = "summary_svg"
        items = [
            {
                "name": Path(relative_path).stem,
                "kind": kind,
                "media_path": output_path.relative_to(catalog_root).as_posix(),
                "is_original_image": False,
            }
        ]
    result = {
        "resource_root": str(root),
        "relative_path": relative_path,
        "cached": False,
        "preview_kind": preview_kind,
        "items": items,
    }
    _write_preview_manifest(manifest_path, result)
    return result


def build_fanxiu_static_asset_preview(
    path: str | Path,
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    manifest = build_fanxiu_static_asset_preview_manifest(
        path,
        resource_root=resource_root,
        export_root=export_root,
        force=force,
    )
    items = manifest.get("items") or []
    if not items:
        raise FanxiuResourceError(f"素材预览为空：{path}")
    first_item = dict(items[0])
    preview_media_path = str(first_item.get("media_path") or "")
    preview_path = resolve_fanxiu_static_asset_preview_media_path(preview_media_path, export_root=export_root)
    return {
        **manifest,
        "preview_path": str(preview_path),
        "preview_media_path": preview_media_path,
        "preview_kind": manifest.get("preview_kind") or first_item.get("kind") or "",
        "preview_item": first_item,
    }


def _write_object_error_preview(output_path: Path, *, error: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(
            [
                '<svg xmlns="http://www.w3.org/2000/svg" width="920" height="620" viewBox="0 0 920 620">',
                '<rect width="100%" height="100%" fill="#f8f4e8"/>',
                '<text x="28" y="42" font-size="15" fill="#b42318">Preview parse failed</text>',
                f'<text x="28" y="74" font-size="12" fill="#694f14">{xml_escape(error[:180])}</text>',
                "</svg>",
            ]
        ),
        encoding="utf-8",
    )


def resolve_fanxiu_static_asset_preview_media_path(
    path: str | Path,
    *,
    export_root: str | Path | None = None,
) -> Path:
    root = _catalog_dir(export_root).resolve()
    target = (root / str(path)).resolve()
    if not _is_relative_to(target, root):
        raise FanxiuResourceError(f"素材预览路径必须位于图鉴目录内：{root}")
    if target.suffix.lower() not in {".png", ".svg"}:
        raise FanxiuResourceError(f"素材预览格式不支持：{target.suffix}")
    if not target.is_file():
        raise FanxiuResourceError(f"素材预览不存在：{target}")
    return target


def _catalog_dir(export_root: str | Path | None = None) -> Path:
    return resolve_fanxiu_export_root(export_root) / "parsed_configs" / "asset_catalog"


def _preview_dir(export_root: str | Path | None = None) -> Path:
    return _catalog_dir(export_root) / "previews"


def _preview_stem(relative_path: str) -> str:
    clean = _SAFE_PREVIEW_STEM_RE.sub("_", Path(relative_path).stem).strip("._")[:56] or "asset"
    digest = hashlib.sha1(relative_path.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{clean}_{digest}_{ASSET_PREVIEW_CACHE_VERSION}"


def _pptr_path_id(value: Any) -> int | None:
    for attr in ("path_id", "m_PathID", "PathID"):
        raw = getattr(value, attr, None)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _vec_number(value: Any, axis: str, default: float = 0.0) -> float:
    for attr in (axis, axis.upper(), f"m_{axis.upper()}"):
        raw = getattr(value, attr, None)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    return default


def _object_name(data: Any) -> str:
    return str(getattr(data, "m_Name", "") or getattr(data, "name", "") or "").strip()


def _load_existing_detail_index(export_root: str | Path | None = None) -> dict[str, dict[str, Any]]:
    root = resolve_fanxiu_export_root(export_root)
    index_dir = root / "indexes"
    details: dict[str, dict[str, Any]] = defaultdict(dict)

    for row in _read_tsv(index_dir / "mesh_summary.tsv"):
        source = str(row.get("source", ""))
        detail = details[source]
        detail["mesh_count"] = int(detail.get("mesh_count") or 0) + 1
        detail["mesh_vertices"] = int(detail.get("mesh_vertices") or 0) + int(float(row.get("vertices") or 0))
        detail["mesh_faces"] = int(detail.get("mesh_faces") or 0) + int(float(row.get("faces") or 0))

    for row in _read_tsv(index_dir / "material_summary.tsv"):
        source = str(row.get("source", ""))
        details[source]["material_count"] = int(details[source].get("material_count") or 0) + 1

    for row in _read_tsv(index_dir / "model_textures.tsv"):
        source = str(row.get("source", ""))
        details[source]["texture_count"] = int(details[source].get("texture_count") or 0) + 1

    for row in _read_tsv(index_dir / "animations.tsv"):
        source = str(row.get("source", ""))
        details[source]["animation_count"] = int(details[source].get("animation_count") or 0) + 1

    for row in _read_tsv(index_dir / "ui_gameobjects.tsv"):
        source = str(row.get("source", ""))
        details[source]["ui_gameobject_count"] = int(details[source].get("ui_gameobject_count") or 0) + 1

    return details


def _format_report(
    *,
    resource_root: Path,
    output_dir: Path,
    rows: list[dict[str, Any]],
    stats: dict[str, Any],
) -> str:
    lines = [
        "# Fanxiu Static Asset Catalog",
        "",
        "- Scope: Unity-style static asset bundle inventory for model/effect/UI/scene/animation resources.",
        "- Boundary: metadata-only path and index catalog. It does not render 3D previews, export meshes, mutate bundles, or read runtime/account/network payloads.",
        "",
        "## Summary",
        "",
        f"- Resource root: `{resource_root}`",
        f"- Output dir: `{output_dir}`",
        f"- Asset rows: `{stats.get('asset_count', 0)}`",
        f"- Total bytes: `{stats.get('total_bytes', 0)}`",
        f"- Existing detail coverage: `{stats.get('detail_covered_asset_count', 0)}` rows with prior mesh/material/texture/animation/UI detail TSV hits.",
        f"- Unity object type coverage: `{stats.get('unity_parsed_asset_count', 0)}` parsed rows, `{stats.get('unity_parse_error_count', 0)}` errors.",
        "",
        "## Asset Groups",
        "",
    ]
    for key, count in Counter(str(row.get("asset_group", "")) for row in rows).most_common():
        lines.append(f"- `{key}`: `{count}`")
    lines.extend(["", "## Top Categories", ""])
    for key, count in Counter(str(row.get("category", "")) for row in rows).most_common(40):
        lines.append(f"- `{key}`: `{count}`")
    lines.extend(["", "## Visible Data Types", ""])
    for key, count in Counter(str(row.get("visible_data_type", "")) for row in rows if row.get("visible_data_type")).most_common(40):
        lines.append(f"- `{key}`: `{count}`")
    lines.extend(["", "## Largest Assets", ""])
    for row in sorted(rows, key=lambda item: int(item.get("bytes") or 0), reverse=True)[:30]:
        lines.append(
            f"- `{row.get('relative_path', '')}`: `{row.get('bytes', '')}` bytes, group `{row.get('asset_group', '')}`"
        )
    lines.append("")
    return "\n".join(lines)


def build_fanxiu_static_asset_catalog(
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
    source_kinds: list[str] | tuple[str, ...] | None = None,
    max_files: int | None = None,
    verify_unity: bool = False,
    parse_unity_objects: bool = False,
    max_parse_files: int | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_resource_root(resource_root)
    output_dir = _catalog_dir(export_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    requested_kinds = [str(item).strip().lower() for item in (source_kinds or _STATIC_ASSET_SOURCE_GROUPS.keys()) if str(item).strip()]
    allowed_kinds = [item for item in requested_kinds if item in _STATIC_ASSET_SOURCE_GROUPS]
    if not allowed_kinds:
        allowed_kinds = list(_STATIC_ASSET_SOURCE_GROUPS)
    max_files_value = None if max_files is None else max(0, int(max_files))
    details = _load_existing_detail_index(export_root)

    rows: list[dict[str, Any]] = []
    skipped_non_unity = 0
    parsed_unity_count = 0
    unity_parse_error_count = 0
    max_parse_files_value = None if max_parse_files is None else max(0, int(max_parse_files))
    for source_kind in allowed_kinds:
        source_dir = root / source_kind
        if not source_dir.is_dir():
            continue
        for path in sorted(source_dir.rglob("*"), key=lambda item: item.as_posix().lower()):
            if not path.is_file():
                continue
            if max_files_value is not None and len(rows) >= max_files_value:
                break
            relative_path = path.relative_to(root).as_posix()
            magic_text = ""
            offset_value = ""
            if verify_unity:
                magic, offset = locate_unity_bundle_offset(path)
                if offset < 0:
                    skipped_non_unity += 1
                    continue
                magic_text = magic.decode("ascii", errors="ignore") if magic else ""
                offset_value = int(offset)
            name, hash_suffix = _split_hashed_stem(path.stem)
            detail = details.get(relative_path, {})
            unity_summary: dict[str, Any] = {}
            should_parse_unity = parse_unity_objects and (
                max_parse_files_value is None or parsed_unity_count + unity_parse_error_count < max_parse_files_value
            )
            if should_parse_unity:
                unity_summary = _parse_unity_object_summary(path, source_kind=source_kind)
                if unity_summary.get("unity_parse_status") == "parsed":
                    parsed_unity_count += 1
                else:
                    unity_parse_error_count += 1
            row = {
                "asset_id": _asset_id(relative_path),
                "asset_group": _asset_group(source_kind),
                "source_kind": source_kind,
                "category": _category(relative_path),
                "name": name,
                "stem": path.stem,
                "hash_suffix": hash_suffix,
                "relative_path": relative_path,
                "bytes": path.stat().st_size,
                "suffix": path.suffix.lower() or "<none>",
                "unity_magic": magic_text,
                "unity_offset": offset_value,
                "mesh_count": detail.get("mesh_count", ""),
                "mesh_vertices": detail.get("mesh_vertices", ""),
                "mesh_faces": detail.get("mesh_faces", ""),
                "material_count": detail.get("material_count", ""),
                "texture_count": detail.get("texture_count", ""),
                "animation_count": detail.get("animation_count", ""),
                "ui_gameobject_count": detail.get("ui_gameobject_count", ""),
                "visible_data_type": unity_summary.get("visible_data_type", ""),
                "unity_object_count": unity_summary.get("unity_object_count", ""),
                "unity_object_types": unity_summary.get("unity_object_types", ""),
                "unity_primary_type": unity_summary.get("unity_primary_type", ""),
                "unity_named_objects": unity_summary.get("unity_named_objects", ""),
                "unity_script_names": unity_summary.get("unity_script_names", ""),
                "unity_read_error_count": unity_summary.get("unity_read_error_count", ""),
                "unity_parse_status": unity_summary.get("unity_parse_status", ""),
                "unity_parse_error": unity_summary.get("unity_parse_error", ""),
                "detail_status": "indexed" if detail else "",
            }
            rows.append(row)
        if max_files_value is not None and len(rows) >= max_files_value:
            break

    rows.sort(key=lambda item: (str(item.get("asset_group", "")), str(item.get("source_kind", "")), str(item.get("relative_path", ""))))
    manifest_path = output_dir / "static_asset_catalog.tsv"
    _write_tsv(manifest_path, rows, _STATIC_ASSET_FIELDNAMES)
    stats = {
        "asset_count": len(rows),
        "total_bytes": sum(int(row.get("bytes") or 0) for row in rows),
        "source_kinds": dict(Counter(str(row.get("source_kind", "")) for row in rows).most_common()),
        "asset_groups": dict(Counter(str(row.get("asset_group", "")) for row in rows).most_common()),
        "categories": dict(Counter(str(row.get("category", "")) for row in rows).most_common()),
        "detail_covered_asset_count": sum(1 for row in rows if row.get("detail_status")),
        "skipped_non_unity_count": skipped_non_unity,
        "verify_unity": verify_unity,
        "parse_unity_objects": parse_unity_objects,
        "unity_parsed_asset_count": parsed_unity_count,
        "unity_parse_error_count": unity_parse_error_count,
        "visible_data_types": dict(Counter(str(row.get("visible_data_type", "")) for row in rows if row.get("visible_data_type")).most_common()),
        "unity_primary_types": dict(Counter(str(row.get("unity_primary_type", "")) for row in rows if row.get("unity_primary_type")).most_common()),
    }
    report_path = output_dir / "static_asset_catalog_report.md"
    report_path.write_text(
        _format_report(resource_root=root, output_dir=output_dir, rows=rows, stats=stats),
        encoding="utf-8",
    )
    json_path = output_dir / "static_asset_catalog.json"
    json_path.write_text(
        json.dumps(
            {
                "resource_root": str(root),
                "output_dir": str(output_dir),
                "manifest": str(manifest_path),
                "report": str(report_path),
                "stats": stats,
                "sample_assets": rows[:80],
                "metadata_only_no_runtime_payloads": True,
                "unity_objects_metadata_only": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "resource_root": str(root),
        "output_dir": str(output_dir),
        "manifest": str(manifest_path),
        "report": str(report_path),
        "json": str(json_path),
        "stats": stats,
        "metadata_only_no_runtime_payloads": True,
        "unity_objects_metadata_only": True,
    }


def load_fanxiu_static_asset_manifest(
    *,
    resource_root: str | Path | None = None,
    export_root: str | Path | None = None,
    query: str | None = None,
    catalog_view: str | None = None,
    asset_group: str | None = None,
    source_kind: str | None = None,
    category: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    output_dir = _catalog_dir(export_root)
    manifest_path = output_dir / "static_asset_catalog.tsv"
    if not manifest_path.is_file():
        build_fanxiu_static_asset_catalog(resource_root=resource_root, export_root=export_root)
    if not manifest_path.is_file():
        raise FanxiuResourceError(f"静态素材图鉴不存在：{manifest_path}")
    normalized_catalog_view = _normalize_static_asset_catalog_view(catalog_view)
    raw_rows = _read_tsv(manifest_path)
    if normalized_catalog_view == "semantic":
        build_fanxiu_static_asset_semantic_catalog(export_root=export_root)
        semantic_path = _asset_semantic_catalog_path(output_dir)
        rows = _read_tsv(semantic_path)
    else:
        rows = raw_rows
    normalized_group = (asset_group or "").strip().lower()
    normalized_kind = (source_kind or "").strip().lower()
    normalized_category = (category or "").strip().lower()
    query_terms = _asset_query_terms(query)

    def row_matches_query_scope(row: dict[str, Any]) -> bool:
        if normalized_kind and str(row.get("source_kind", "")).lower() != normalized_kind:
            return False
        if normalized_category and str(row.get("category", "")).lower() != normalized_category:
            return False
        haystack = " ".join(
            str(row.get(field, ""))
            for field in (
                "asset_group",
                "source_kind",
                "category",
                "name",
                "stem",
                "relative_path",
                "visible_data_type",
                "unity_primary_type",
                "unity_object_types",
                "unity_named_objects",
                "unity_script_names",
                "semantic_id",
                "semantic_group",
                "semantic_type",
                "semantic_name",
                "semantic_summary",
                "semantic_refs",
                "semantic_visual_names",
                "semantic_visual_categories",
                "semantic_visual_media_paths",
                "semantic_variant_refs",
                "linked_asset_groups",
                "linked_asset_names",
                "linked_asset_paths",
            )
        ).lower()
        if query_terms and not any(term in haystack for term in query_terms):
            return False
        return True

    query_rows = [row for row in rows if row_matches_query_scope(row)]
    raw_query_rows = [row for row in raw_rows if row_matches_query_scope(row)]
    view_rows = [row for row in query_rows if _matches_static_asset_catalog_view(row, normalized_catalog_view)]
    raw_gallery_rows = [row for row in raw_rows if _matches_static_asset_catalog_view(row, "gallery")]
    query_gallery_rows = [row for row in raw_query_rows if _matches_static_asset_catalog_view(row, "gallery")]
    semantic_rows_for_stats = rows if normalized_catalog_view == "semantic" else _read_tsv(_asset_semantic_catalog_path(output_dir))

    filtered: list[dict[str, Any]] = []
    for row in view_rows:
        if normalized_group and str(row.get("asset_group", "")).lower() != normalized_group:
            continue
        item: dict[str, Any] = {}
        for field in _STATIC_ASSET_FIELDNAMES:
            value = row.get(field, "")
            if field in {
                "bytes",
                "unity_offset",
                "mesh_count",
                "mesh_vertices",
                "mesh_faces",
                "material_count",
                "texture_count",
                "animation_count",
                "ui_gameobject_count",
                "unity_object_count",
                "unity_read_error_count",
                "linked_asset_count",
                "semantic_visual_count",
                "semantic_variant_count",
            }:
                try:
                    item[field] = int(float(value)) if value != "" else 0
                except ValueError:
                    item[field] = 0
            else:
                item[field] = value
        filtered.append(item)

    if query_terms:
        raw_query = (query or "").strip().lower()

        def query_rank(row: dict[str, Any]) -> int:
            primary = " ".join(
                str(row.get(field, ""))
                for field in ("semantic_name", "name", "stem", "semantic_id", "semantic_type", "semantic_group")
            ).lower()
            secondary = " ".join(
                str(row.get(field, ""))
                for field in (
                    "semantic_summary",
                    "semantic_refs",
                    "semantic_visual_names",
                    "semantic_variant_refs",
                    "linked_asset_names",
                    "relative_path",
                )
            ).lower()
            score = 0
            if raw_query:
                variant_ids = {
                    item.rsplit(":", 1)[-1]
                    for item in _split_ref_text(row.get("semantic_variant_refs"))
                    if item.strip()
                }
                if raw_query in variant_ids:
                    score += 18000
                for field in ("semantic_name", "name", "stem"):
                    if str(row.get(field, "")).strip().lower() == raw_query:
                        score += 20000
                if raw_query in primary:
                    score += 8000
                elif raw_query in secondary:
                    score += 1200
            for term in query_terms:
                if term in primary:
                    score += 1000
                elif term in secondary:
                    score += 120
            if int(row.get("semantic_visual_count") or 0) > 0:
                score += 20
            return score

        filtered.sort(
            key=lambda item: (
                -query_rank(item),
                _SEMANTIC_GROUP_ORDER.get(str(item.get("semantic_group") or item.get("asset_group") or ""), 99),
                str(item.get("semantic_name") or item.get("name") or ""),
                str(item.get("semantic_id") or item.get("asset_id") or ""),
            )
        )

    limit = max(1, min(int(limit), 5000))
    offset = max(0, int(offset))
    page_rows = filtered[offset : offset + limit]
    return {
        "manifest_root": str(output_dir),
        "manifest": str(manifest_path),
        "query": query or "",
        "catalog_view": normalized_catalog_view,
        "asset_group": asset_group or "",
        "source_kind": source_kind or "",
        "category": category or "",
        "total": len(rows),
        "filtered": len(filtered),
        "offset": offset,
        "limit": limit,
        "stats": {
            "total": len(rows),
            "asset_groups": dict(Counter(str(row.get("asset_group", "")) for row in rows).most_common()),
            "source_kinds": dict(Counter(str(row.get("source_kind", "")) for row in rows).most_common()),
            "categories": dict(Counter(str(row.get("category", "")) for row in rows).most_common()),
            "visible_data_types": dict(Counter(str(row.get("visible_data_type", "")) for row in rows if row.get("visible_data_type")).most_common()),
            "unity_primary_types": dict(Counter(str(row.get("unity_primary_type", "")) for row in rows if row.get("unity_primary_type")).most_common()),
            "catalog_views": {
                "semantic": len(semantic_rows_for_stats),
                "gallery": len(raw_gallery_rows),
                "raw": len(raw_rows),
            },
            "query_asset_groups": dict(Counter(str(row.get("asset_group", "")) for row in view_rows).most_common()),
            "query_source_kinds": dict(Counter(str(row.get("source_kind", "")) for row in view_rows).most_common()),
            "query_categories": dict(Counter(str(row.get("category", "")) for row in view_rows).most_common()),
            "query_visible_data_types": dict(
                Counter(str(row.get("visible_data_type", "")) for row in view_rows if row.get("visible_data_type")).most_common()
            ),
            "query_unity_primary_types": dict(
                Counter(str(row.get("unity_primary_type", "")) for row in view_rows if row.get("unity_primary_type")).most_common()
            ),
            "query_catalog_views": {
                "semantic": len(query_rows) if normalized_catalog_view == "semantic" else 0,
                "gallery": len(query_gallery_rows),
                "raw": len(raw_query_rows),
            },
            "query_total": len(view_rows),
            "raw_query_total": len(raw_query_rows),
        },
        "rows": page_rows,
    }
