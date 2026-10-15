from __future__ import annotations

import os
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from threading import RLock
from typing import Any

from pyxllib.file.game_assets import (
    extract_wwise_wem_entries,
    load_unity_environment,
    locate_unity_bundle_offset,
    parse_wwise_bnk_chunks,
    parse_wwise_didx_entries,
    summarize_unity_bundle,
    export_unity_text_assets,
    export_unity_textures,
)


FANXIU_RESOURCE_ROOT_ENV = "FANXIU_RESOURCE_ROOT"
FANXIU_RESOURCE_EXPORT_ROOT_ENV = "FANXIU_RESOURCE_EXPORT_ROOT"
DEFAULT_FANXIU_REVERSE_ROOT = Path(r"C:\home\chenkunze\data\m2606凡修逆向")
DEFAULT_FANXIU_RESOURCE_ROOT = DEFAULT_FANXIU_REVERSE_ROOT / "frxx_game_files"
DEFAULT_FANXIU_RESOURCE_EXPORT_ROOT = DEFAULT_FANXIU_REVERSE_ROOT / "frxx_analysis_exports"
_SAFE_PATH_PART_RE = re.compile(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+")
_SPRITE_ICON_NAME_RE = re.compile(r"^[0-9A-Za-z_.-]{1,96}$")
_SPRITE_ATLAS_KEY_RE = re.compile(r"^(?P<key>icon\d*|skill\d*)_")
_SPRITE_EXPORT_LOCK = RLock()
_SPRITE_ICON_ALIASES: dict[str, dict[str, str]] = {
    "icon_item_0067": {
        "sprite_name": "xmgf_icon_0067",
        "reason": "Item 9070194 / 玉骨煞甲丹 references a missing legacy icon; xmgf_icon_0067 is the matching current visual asset and uieffect uses icon_0067 textures.",
    },
    "icon_skill_ld_zw_6001": {
        "sprite_name": "skill_icon2_ld_6001",
        "reason": "Item 3060015 / 绝招·天罡战气 references a missing legacy icon; skill_icon2_ld_6001 is the matching ld_6001 skill icon in the current atlas.",
    },
    "icon_item_0052": {
        "sprite_name": "xmgf_icon_0052",
        "reason": "Item 7 / 宗门资金 references a missing legacy icon; xmgf_icon_0052 is the current unclaimed money-bag style sprite with the same suffix and matching funds semantics.",
    },
}


class FanxiuResourceError(ValueError):
    pass


def _safe_path_part(value: str, fallback: str = "asset") -> str:
    text = _SAFE_PATH_PART_RE.sub("_", str(value or "").strip()).strip("._")
    return text[:64] if text else fallback


def _safe_relative_parts(path: Path) -> list[str]:
    return [_safe_path_part(part) for part in path.parts]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_fanxiu_resource_root(resource_root: str | os.PathLike[str] | None = None) -> Path:
    value = resource_root or os.environ.get(FANXIU_RESOURCE_ROOT_ENV) or DEFAULT_FANXIU_RESOURCE_ROOT
    return Path(value).expanduser().resolve()


def resolve_fanxiu_export_root(export_root: str | os.PathLike[str] | None = None) -> Path:
    value = export_root or os.environ.get(FANXIU_RESOURCE_EXPORT_ROOT_ENV) or DEFAULT_FANXIU_RESOURCE_EXPORT_ROOT
    return Path(value).expanduser().resolve()


def resolve_fanxiu_asset_path(
    path: str | os.PathLike[str],
    *,
    resource_root: str | os.PathLike[str] | None = None,
) -> tuple[Path, Path, str]:
    root = resolve_fanxiu_resource_root(resource_root)
    raw_path = Path(path)
    asset_path = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(asset_path, root):
        raise FanxiuResourceError(f"资源路径必须位于资源根目录内：{root}")
    if not asset_path.is_file():
        raise FanxiuResourceError(f"资源文件不存在：{asset_path}")
    return asset_path, root, asset_path.relative_to(root).as_posix()


def resolve_fanxiu_subdir(
    subdir: str | os.PathLike[str] | None = None,
    *,
    resource_root: str | os.PathLike[str] | None = None,
) -> tuple[Path, Path, str]:
    root = resolve_fanxiu_resource_root(resource_root)
    raw_subdir = Path(str(subdir or "."))
    target = raw_subdir.expanduser().resolve() if raw_subdir.is_absolute() else (root / raw_subdir).resolve()
    if not _is_relative_to(target, root):
        raise FanxiuResourceError(f"子目录必须位于资源根目录内：{root}")
    if not target.exists() or not target.is_dir():
        raise FanxiuResourceError(f"资源子目录不存在：{target}")
    return target, root, target.relative_to(root).as_posix()


def build_fanxiu_resource_summary(resource_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = resolve_fanxiu_resource_root(resource_root)
    if not root.exists():
        return {
            "resource_root": str(root),
            "exists": False,
            "file_count": 0,
            "total_bytes": 0,
            "suffix_counts": {},
            "top_dirs": [],
        }

    suffix_counts: Counter[str] = Counter()
    top_dir_counts: Counter[str] = Counter()
    top_dir_sizes: defaultdict[str, int] = defaultdict(int)
    file_count = 0
    total_bytes = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        rel_parts = path.relative_to(root).parts
        top_dir = rel_parts[0] if len(rel_parts) > 1 else "."
        suffix = path.suffix.lower() or "<none>"
        suffix_counts[suffix] += 1
        top_dir_counts[top_dir] += 1
        top_dir_sizes[top_dir] += size
        file_count += 1
        total_bytes += size

    top_dirs = [
        {
            "name": name,
            "file_count": top_dir_counts[name],
            "total_bytes": top_dir_sizes[name],
        }
        for name in sorted(top_dir_counts, key=lambda item: top_dir_sizes[item], reverse=True)
    ]
    return {
        "resource_root": str(root),
        "exists": True,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "suffix_counts": dict(suffix_counts.most_common()),
        "top_dirs": top_dirs,
    }


def list_fanxiu_unity_bundles(
    *,
    resource_root: str | os.PathLike[str] | None = None,
    subdir: str | os.PathLike[str] | None = None,
    limit: int = 100,
    scan_limit: int = 5000,
    inspect_objects: bool = False,
    max_objects: int = 30,
) -> dict[str, Any]:
    target_dir, root, normalized_subdir = resolve_fanxiu_subdir(subdir, resource_root=resource_root)
    limit = max(1, min(int(limit), 500))
    scan_limit = max(1, min(int(scan_limit), 50000))
    max_objects = max(0, min(int(max_objects), 200))
    scanned = 0
    bundles: list[dict[str, Any]] = []

    for path in target_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".bytes", ".ab", ".bundle", ""}:
            continue
        scanned += 1
        if scanned > scan_limit:
            break
        magic, offset = locate_unity_bundle_offset(path)
        if offset < 0:
            continue
        rel = path.relative_to(root).as_posix()
        item: dict[str, Any] = {
            "path": str(path),
            "relative_path": rel,
            "size": path.stat().st_size,
            "magic": magic.decode("ascii") if magic else "",
            "offset": offset,
        }
        if inspect_objects:
            item["summary"] = summarize_unity_bundle(path, max_objects=max_objects).to_dict()
        bundles.append(item)
        if len(bundles) >= limit:
            break

    return {
        "resource_root": str(root),
        "subdir": normalized_subdir,
        "scanned": min(scanned, scan_limit),
        "scan_limit": scan_limit,
        "limit": limit,
        "items": bundles,
    }


def inspect_fanxiu_unity_bundle(
    path: str | os.PathLike[str],
    *,
    resource_root: str | os.PathLike[str] | None = None,
    max_objects: int = 100,
) -> dict[str, Any]:
    asset_path, _root, relative_path = resolve_fanxiu_asset_path(path, resource_root=resource_root)
    summary = summarize_unity_bundle(asset_path, max_objects=max_objects).to_dict()
    summary["relative_path"] = relative_path
    return summary


def inspect_fanxiu_unity_bundle_manifest(
    path: str | os.PathLike[str],
    *,
    resource_root: str | os.PathLike[str] | None = None,
    name_patterns: list[str] | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Read a Unity AssetBundleManifest offline: bundle names, hashes and dependencies.

    The manifest maps bundle name -> content hash/dependency bundles only; it does not
    list assets inside bundles or Lua module -> file mappings. ``name_patterns`` are
    case-insensitive regexes selecting bundles to return in full (at most ``limit``).
    """
    import hashlib

    asset_path, _, relative_path = resolve_fanxiu_asset_path(path, resource_root=resource_root)
    patterns = [re.compile(p, re.IGNORECASE) for p in (name_patterns or [])]
    limit = max(0, min(int(limit), 5000))
    manifests = []
    for obj in load_unity_environment(asset_path).objects:
        if obj.type.name != 'AssetBundleManifest':
            continue
        tree = obj.read_typetree()
        names = dict(tree.get('AssetBundleNames') or [])
        infos = dict(tree.get('AssetBundleInfos') or [])
        top_dirs = Counter(name.split('/', 1)[0] for name in names.values())
        matched = []
        for index, name in sorted(names.items()):
            if not any(p.search(name) for p in patterns):
                continue
            info = infos.get(index) or {}
            raw_hash = info.get('AssetBundleHash') or {}
            digest = bytes(raw_hash.get(f'bytes[{i}]', 0) for i in range(16)).hex()
            matched.append({'index': index, 'name': name, 'hash': digest,
                            'dependencies': [names.get(dep, dep) for dep in info.get('AssetBundleDependencies') or []]})
        manifests.append({
            'path_id': obj.path_id,
            'name': tree.get('m_Name'),
            'bundle_count': len(names),
            'variant_count': len(tree.get('AssetBundlesWithVariant') or []),
            'with_dependencies': sum(1 for info in infos.values() if info.get('AssetBundleDependencies')),
            'top_dirs': dict(top_dirs.most_common()),
            'matched_count': len(matched),
            'matched': matched[:limit],
        })
    return {'relative_path': relative_path, 'md5': hashlib.md5(asset_path.read_bytes()).hexdigest(),
            'read_only': True, 'name_patterns': [p.pattern for p in patterns], 'manifests': manifests}


def _ui_rect_ancestry(start, transforms, game_objects):
    chain, seen, current = [], set(), start
    while current in transforms and current not in seen and len(chain) < 32:
        seen.add(current)
        tree = transforms[current]
        owner = tree.get('m_GameObject') or {}
        parent = tree.get('m_Father') or {}
        chain.append({'path_id': current,
                      'name': ((game_objects.get(owner.get('m_PathID')) or {}).get('m_Name')
                               if owner.get('m_FileID') == 0 else None),
                      'geometry': {k: tree.get(k) for k in (
                          'm_AnchorMin', 'm_AnchorMax', 'm_AnchoredPosition',
                          'm_SizeDelta', 'm_Pivot', 'm_LocalScale')},
                      'parent': parent})
        if parent.get('m_FileID') != 0 or not parent.get('m_PathID'):
            return {'nodes': chain, 'complete_local_chain': not parent.get('m_PathID')}
        current = parent['m_PathID']
    return {'nodes': chain, 'complete_local_chain': False}

def _ui_node_inspection_complete(nodes, errors, missing_names):
    return not errors and not missing_names and bool(nodes) and all(
        node['rect_ancestry'] is not None
        and node['rect_ancestry']['complete_local_chain']
        and all(component.get('type') is not None and 'decode_error' not in component
                for component in node['components'])
        for node in nodes)


def inspect_fanxiu_unity_ui_nodes(
    path: str | os.PathLike[str], *, names: list[str],
    resource_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Inspect exact named prefab nodes and local RectTransform ancestry.

    Static anchors are not screen hit boxes or proof of active raycast/event
    delivery. Component decode failures remain explicit; this never loads the
    game, invokes Lua, exports files or suggests a click. Names are exact and
    limited to 32; ancestry is bounded to 32 local references.
    """
    import hashlib

    if (not isinstance(names, list) or not names or len(names) > 32
            or any(not isinstance(name, str) or not name for name in names)):
        raise ValueError('需要1至32个精确UI节点名称')
    asset_path, _, relative_path = resolve_fanxiu_asset_path(path, resource_root=resource_root)
    environment = load_unity_environment(asset_path)
    objects = {}
    for obj in environment.objects:
        if obj.path_id in objects:
            raise FanxiuResourceError('资源包含跨文件重复path_id，不能猜测本地UI引用')
        objects[obj.path_id] = obj
    game_objects, transforms, errors = {}, {}, []
    for obj in objects.values():
        if obj.type.name not in {'GameObject', 'RectTransform'}:
            continue
        try:
            tree = obj.read_typetree()
            (game_objects if obj.type.name == 'GameObject' else transforms)[obj.path_id] = tree
        except Exception as exc:
            errors.append({'path_id': obj.path_id, 'type': obj.type.name, 'error': str(exc)[:500]})


    nodes = []
    for object_id, tree in game_objects.items():
        if tree.get('m_Name') not in names:
            continue
        components, ancestry = [], None
        for row in tree.get('m_Component') or []:
            reference = row.get('component') or {}
            obj = objects.get(reference.get('m_PathID')) if reference.get('m_FileID') == 0 else None
            item = {'reference': reference, 'type': obj.type.name if obj else None}
            if obj is None:
                item['decode_error'] = ('external_component_reference'
                                        if reference.get('m_FileID') != 0
                                        else 'missing_local_component_reference')
            if obj and obj.type.name == 'RectTransform':
                ancestry = _ui_rect_ancestry(obj.path_id, transforms, game_objects)
            elif obj and obj.type.name == 'MonoBehaviour':
                try:
                    item['tree'] = obj.read_typetree()
                except Exception as exc:
                    item['decode_error'] = str(exc)[:500]
            components.append(item)
        nodes.append({'path_id': object_id, 'name': tree['m_Name'],
                      'active_in_prefab': tree.get('m_IsActive'),
                      'components': components, 'rect_ancestry': ancestry})
    return {'relative_path': relative_path, 'sha256': hashlib.sha256(asset_path.read_bytes()).hexdigest(),
            'read_only': True, 'source': 'offline_unity_prefab', 'screen_hit_region_proven': False,
            'nodes': nodes, 'missing_names': sorted(set(names) - {n['name'] for n in nodes}),
            'component_decode_complete': _ui_node_inspection_complete(
                nodes, errors, set(names) - {n['name'] for n in nodes}),
            'decode_errors': errors}


def export_fanxiu_unity_textures(
    path: str | os.PathLike[str],
    *,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    max_textures: int | None = None,
) -> dict[str, Any]:
    asset_path, root, relative_path = resolve_fanxiu_asset_path(path, resource_root=resource_root)
    export_base = resolve_fanxiu_export_root(export_root)
    rel_stem = asset_path.relative_to(root).with_suffix("")
    out_dir = export_base / "by_source" / Path(*_safe_relative_parts(rel_stem)) / "textures"
    exports = export_unity_textures(
        asset_path,
        out_dir,
        max_textures=max_textures,
    )
    return {
        "resource_root": str(root),
        "export_root": str(export_base),
        "source_path": str(asset_path),
        "relative_path": relative_path,
        "output_dir": str(out_dir),
        "items": [item.to_dict() for item in exports],
    }


def export_fanxiu_unity_text_assets(
    path: str | os.PathLike[str],
    *,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    max_assets: int | None = None,
) -> dict[str, Any]:
    asset_path, root, relative_path = resolve_fanxiu_asset_path(path, resource_root=resource_root)
    export_base = resolve_fanxiu_export_root(export_root)
    rel_stem = asset_path.relative_to(root).with_suffix("")
    out_dir = export_base / "by_source" / Path(*_safe_relative_parts(rel_stem)) / "text_assets"
    exports = export_unity_text_assets(
        asset_path,
        out_dir,
        max_assets=max_assets,
    )
    return {
        "resource_root": str(root),
        "export_root": str(export_base),
        "source_path": str(asset_path),
        "relative_path": relative_path,
        "output_dir": str(out_dir),
        "items": [item.to_dict() for item in exports],
    }


def _atlas_key_for_sprite_name(sprite_name: str) -> str:
    match = _SPRITE_ATLAS_KEY_RE.match(sprite_name)
    if match:
        return match.group("key")
    return sprite_name.split("_", 1)[0]


def _candidate_sprite_atlas_files(resource_root: Path, sprite_name: str) -> list[Path]:
    atlas_dir = resource_root / "atlasnew"
    atlas_key = _atlas_key_for_sprite_name(sprite_name)
    atlas_keys = [atlas_key]
    parts = sprite_name.split("_")
    if len(parts) >= 2 and parts[0] == "icon":
        atlas_keys.append(parts[1])
        if parts[1] == "skill":
            atlas_keys.extend(["skill2", "skill3"])
        elif parts[1] == "item":
            atlas_keys.extend([f"icon{i}" for i in range(2, 10)])

    candidates: list[Path] = []
    seen: set[Path] = set()
    for key in atlas_keys:
        for path in sorted(atlas_dir.glob(f"{key}_*.bytes")):
            if path not in seen:
                candidates.append(path)
                seen.add(path)
    if candidates:
        return candidates
    return sorted(atlas_dir.glob("*.bytes"))


def export_fanxiu_sprite_icon(
    sprite_name: str,
    *,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    sprite_name = str(sprite_name or "").strip()
    if not _SPRITE_ICON_NAME_RE.match(sprite_name):
        raise FanxiuResourceError(f"图标名不合法：{sprite_name}")

    root = resolve_fanxiu_resource_root(resource_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = export_base / "icons"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{sprite_name}.png"
    if output_path.is_file():
        result = {
            "resource_root": str(root),
            "export_root": str(export_base),
            "sprite_name": sprite_name,
            "output_path": str(output_path),
            "cached": True,
        }
        if sprite_name in _SPRITE_ICON_ALIASES:
            result.update(
                {
                    "alias_sprite_name": _SPRITE_ICON_ALIASES[sprite_name].get("sprite_name", ""),
                    "alias_reason": _SPRITE_ICON_ALIASES[sprite_name].get("reason", ""),
                }
            )
        return result

    with _SPRITE_EXPORT_LOCK:
        if output_path.is_file():
            result = {
                "resource_root": str(root),
                "export_root": str(export_base),
                "sprite_name": sprite_name,
                "output_path": str(output_path),
                "cached": True,
            }
            if sprite_name in _SPRITE_ICON_ALIASES:
                result.update(
                    {
                        "alias_sprite_name": _SPRITE_ICON_ALIASES[sprite_name].get("sprite_name", ""),
                        "alias_reason": _SPRITE_ICON_ALIASES[sprite_name].get("reason", ""),
                    }
                )
            return result

        for atlas_path in _candidate_sprite_atlas_files(root, sprite_name):
            try:
                env = load_unity_environment(atlas_path)
            except Exception:
                continue
            for obj in env.objects:
                if obj.type.name != "Sprite":
                    continue
                obj_data = obj.read()
                name = getattr(obj_data, "name", "") or getattr(obj_data, "m_Name", "") or ""
                if name != sprite_name:
                    continue
                image = obj_data.image
                image.save(output_path)
                return {
                    "resource_root": str(root),
                    "export_root": str(export_base),
                    "source_path": str(atlas_path),
                    "relative_source_path": atlas_path.relative_to(root).as_posix(),
                    "sprite_name": sprite_name,
                    "output_path": str(output_path),
                    "width": int(image.size[0]),
                    "height": int(image.size[1]),
                    "path_id": int(obj.path_id),
                    "cached": False,
                }

        alias = _SPRITE_ICON_ALIASES.get(sprite_name)
        if alias:
            alias_sprite_name = str(alias.get("sprite_name") or "").strip()
            if alias_sprite_name and alias_sprite_name != sprite_name:
                alias_result = export_fanxiu_sprite_icon(
                    alias_sprite_name,
                    resource_root=root,
                    export_root=export_base,
                )
                alias_output_path = Path(alias_result["output_path"]).expanduser().resolve()
                if alias_output_path.is_file():
                    shutil.copyfile(alias_output_path, output_path)
                    result = {
                        "resource_root": str(root),
                        "export_root": str(export_base),
                        "sprite_name": sprite_name,
                        "output_path": str(output_path),
                        "width": alias_result.get("width"),
                        "height": alias_result.get("height"),
                        "cached": False,
                        "alias_sprite_name": alias_sprite_name,
                        "alias_reason": alias.get("reason", ""),
                        "alias_output_path": str(alias_output_path),
                    }
                    for key in ("source_path", "relative_source_path", "path_id"):
                        if key in alias_result:
                            result[f"alias_{key}"] = alias_result[key]
                    return result

    raise FanxiuResourceError(f"没有找到 Unity Sprite 图标：{sprite_name}")


def resolve_fanxiu_sprite_icon_path(
    sprite_name: str,
    *,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> Path:
    result = export_fanxiu_sprite_icon(sprite_name, resource_root=resource_root, export_root=export_root)
    path = Path(result["output_path"]).expanduser().resolve()
    root = resolve_fanxiu_export_root(export_root)
    if not _is_relative_to(path, root):
        raise FanxiuResourceError(f"图标路径必须位于导出目录内：{root}")
    if not path.is_file() or path.suffix.lower() != ".png":
        raise FanxiuResourceError(f"图标文件不存在或格式不支持：{path}")
    return path


def inspect_fanxiu_wwise_bank(
    path: str | os.PathLike[str],
    *,
    resource_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    asset_path, _root, relative_path = resolve_fanxiu_asset_path(path, resource_root=resource_root)
    chunks = parse_wwise_bnk_chunks(asset_path)
    entries = parse_wwise_didx_entries(asset_path)
    return {
        "source_path": str(asset_path),
        "relative_path": relative_path,
        "chunks": [item.to_dict() for item in chunks],
        "wem_entries": [item.to_dict() for item in entries],
    }


def extract_fanxiu_wwise_wems(
    path: str | os.PathLike[str],
    *,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    max_entries: int | None = None,
) -> dict[str, Any]:
    asset_path, root, relative_path = resolve_fanxiu_asset_path(path, resource_root=resource_root)
    export_base = resolve_fanxiu_export_root(export_root)
    rel_stem = asset_path.relative_to(root).with_suffix("")
    out_dir = export_base / "by_source" / Path(*_safe_relative_parts(rel_stem)) / "audio_wem"
    entries = extract_wwise_wem_entries(asset_path, out_dir, max_entries=max_entries)
    return {
        "resource_root": str(root),
        "export_root": str(export_base),
        "source_path": str(asset_path),
        "relative_path": relative_path,
        "output_dir": str(out_dir),
        "items": [item.to_dict() for item in entries],
    }
