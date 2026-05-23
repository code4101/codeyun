from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu_lua_config import parse_fanxiu_generated_lua_config
from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root
from backend.core.fanxiu_wiki import strip_fanxiu_rich_text
from backend.core.fanxiu_wiki_user_fields import get_fanxiu_wiki_user_fields


DEFAULT_LINGJIE_GONGFA_JIE_ROWS = Path("parsed_configs/Lingjie-GongfaJie/rows.json")
DEFAULT_SPECIAL_GONGFA_JIE_ROWS = Path("parsed_configs/Special-GongfaJie/rows.json")
DEFAULT_GONGFA_ROWS = Path("parsed_configs/Gongfa/rows.json")
DEFAULT_GONGFA_SKILL_ROWS = Path("parsed_configs/GongfaSkill/rows.json")
DEFAULT_GONGFA_STAR_ROWS = Path("parsed_configs/GongfaStar/rows.json")
DEFAULT_GONGFA_UPGRADE_ROWS = Path("parsed_configs/GongfaUpgrade/rows.json")
DEFAULT_FAZE_EFFECT_RESOURCE_ROWS = Path("parsed_configs/FazeEffectResource/rows.json")
DEFAULT_FAZE_RESOURCE_ROWS = Path("parsed_configs/FazeResource/rows.json")
DEFAULT_ITEM_ROWS = Path("parsed_configs/Item/rows.json")
DEFAULT_LINGJIE_FEATURE_BASE_ROWS = Path("parsed_configs/FeatureBase/rows.json")
DEFAULT_LINGJIE_MAIN_FEATURE_ROWS = Path("parsed_configs/MainFeature/rows.json")
DEFAULT_LINGJIE_MAIN_FEATURE_PIN_ROWS = Path("parsed_configs/MainFeaturePin/rows.json")
DEFAULT_LINGJIE_SIDE_FEATURE_JIE_ROWS = Path("parsed_configs/SideFeatureJie/rows.json")
DEFAULT_LINGJIE_SIDE_FEATURE_PIN_ROWS = Path("parsed_configs/SideFeaturePin/rows.json")
DEFAULT_LINGJIE_SELF_GONGFA_JIE_ROWS = Path("parsed_configs/LingjieGongfaJie/rows.json")
DEFAULT_LINGJIE_SELF_GONGFA_STAR_ROWS = Path("parsed_configs/LingjieGongfaStar/rows.json")
DEFAULT_LINGJIE_FEATURE_CATALOG = Path("parsed_configs/lingjie_feature_catalog/lingjie_feature_catalog.json")
DEFAULT_LINGJIE_RUNTIME_DIR = Path("parsed_configs/lingjie_feature_catalog")
_WHITESPACE_RE = re.compile(r"\s+")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_export_file(path: str | Path | None, default: Path, *, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(path) if path else default
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"文件必须位于导出根目录内：{root}")
    if not resolved.is_file():
        raise FanxiuResourceError(f"文件不存在：{resolved}")
    return resolved


def _resolve_optional_export_file(
    path: str | Path | None,
    default: Path,
    *,
    export_root: str | Path | None = None,
) -> Path | None:
    root = resolve_fanxiu_export_root(export_root)
    raw_path = Path(path) if path else default
    resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    if not _is_relative_to(resolved, root):
        raise FanxiuResourceError(f"文件必须位于导出根目录内：{root}")
    if not resolved.is_file():
        if path:
            raise FanxiuResourceError(f"文件不存在：{resolved}")
        return None
    return resolved


def _resolve_export_dir(path: str | Path | None, *, export_root: str | Path | None = None) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    if path:
        raw_path = Path(path)
        resolved = raw_path.expanduser().resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
        if not _is_relative_to(resolved, root):
            raise FanxiuResourceError(f"目录必须位于导出根目录内：{root}")
        if not resolved.is_dir():
            raise FanxiuResourceError(f"目录不存在：{resolved}")
        return resolved

    candidates = [
        item
        for item in root.glob("by_source/lscripts/gamesystem/game/luaconfig_*/text_assets")
        if item.is_dir()
    ]
    if not candidates:
        raise FanxiuResourceError("未找到 gamesystem/game/luaconfig TextAsset 导出目录")
    return max(candidates, key=lambda item: (sum(1 for _ in item.glob("*.lua")), item.stat().st_mtime_ns))


def _load_json_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise FanxiuResourceError(f"JSON 文件不是行列表：{path}")
    return [item for item in data if isinstance(item, dict)]


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


def _preview(value: Any, limit: int = 180) -> str:
    text = _WHITESPACE_RE.sub(" ", strip_fanxiu_rich_text(_as_text(value))).strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _unique_join(values: list[Any], *, limit: int = 12) -> str:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = _as_text(value).strip()
        if not text or text == "None" or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= limit:
            break
    return "、".join(items)


def _extract_item_ids(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        ids: list[str] = []
        for item in value:
            ids.extend(_extract_item_ids(item))
        return ids
    if isinstance(value, dict):
        ids: list[str] = []
        for item in value.values():
            ids.extend(_extract_item_ids(item))
        return ids
    text = _as_text(value)
    if not text:
        return []
    return re.findall(r"Item\|(\d+)(?:_\d+)?", text)


def _collect_rows_by_text_key(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = _as_text(row.get(key)).strip()
        if value:
            indexed[value].append(row)
    return indexed


def _row_key(row: dict[str, Any]) -> str:
    return _as_text(row.get("_row_key") or row.get("id")).strip()


def _base_skill_id(value: Any) -> str:
    return _as_text(value).strip().split("_", 1)[0]


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = (_as_text(row.get("_source_table")), _row_key(row))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _collect_item_rows_for_family(
    gid: str,
    source_rows: list[dict[str, Any]],
    items_by_id: dict[str, list[dict[str, Any]]],
    items_by_effect_value: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    consume_item_ids: list[str] = []
    for row in source_rows:
        for key in ("consume", "cost", "consumeItem", "consume_item"):
            consume_item_ids.extend(_extract_item_ids(row.get(key)))

    consume_items: list[dict[str, Any]] = []
    for item_id in consume_item_ids:
        consume_items.extend(items_by_id.get(item_id, []))

    effect_items = items_by_effect_value.get(gid, [])
    linked_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in consume_items + effect_items:
        item_id = _as_text(item.get("id") or item.get("_row_key")).strip()
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        linked_items.append(item)

    return {
        "consume_item_ids": _unique_join(consume_item_ids, limit=40),
        "consume_item_names": _unique_join([row.get("name_plain") or row.get("name") for row in consume_items], limit=40),
        "consume_item_icons": _unique_join([row.get("icon") for row in consume_items], limit=40),
        "linked_item_ids": _unique_join([row.get("id") or row.get("_row_key") for row in linked_items], limit=40),
        "linked_item_names": _unique_join([row.get("name_plain") or row.get("name") for row in linked_items], limit=40),
        "linked_item_icons": _unique_join([row.get("icon") for row in linked_items], limit=40),
        "linked_item_qualities": _unique_join([row.get("quality") for row in linked_items], limit=40),
    }


def _decode_json_maybe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _decode_timeline_tracks(value: Any) -> list[dict[str, Any]]:
    outer = _decode_json_maybe(value)
    if not isinstance(outer, list):
        return []

    tracks: list[dict[str, Any]] = []
    for item in outer:
        track = _decode_json_maybe(item)
        if not isinstance(track, dict):
            continue
        track_value = _decode_json_maybe(track.get("TrackValue"))
        if not isinstance(track_value, dict):
            track_value = {}
        tracks.append(
            {
                "name": track.get("TrackName") or track_value.get("Name") or "",
                "parent": track_value.get("ParentName") or "",
                "clips": track_value.get("ClipDataList") if isinstance(track_value.get("ClipDataList"), list) else [],
                "frame_count": track_value.get("FrameCount"),
                "total_time": track_value.get("TotalTime"),
                "track_type": track_value.get("TracType"),
            }
        )
    return tracks


def _summarize_timeline_fields(row: dict[str, Any]) -> dict[str, Any]:
    tracks = _decode_timeline_tracks(row.get("q_timeline_attacktrack")) + _decode_timeline_tracks(
        row.get("q_timeline_suffertrack")
    )
    clip_types: list[Any] = []
    effect_paths: list[Any] = []
    sound_ids: list[Any] = []
    hit_frames: list[Any] = []
    track_names: list[Any] = []
    clip_count = 0

    for track in tracks:
        track_names.append(track.get("name"))
        for clip in track.get("clips") or []:
            if not isinstance(clip, dict):
                continue
            clip_count += 1
            clip_type = clip.get("ClipType")
            clip_types.append(clip_type)
            args = clip.get("args") if isinstance(clip.get("args"), dict) else {}
            effect_paths.append(args.get("res_Name"))
            for key in ("Sound_Id", "Hit_Effect_Sound"):
                sound_id = args.get(key)
                if _as_int(sound_id):
                    sound_ids.append(sound_id)
            frame = args.get("Frame", clip.get("Start_Frame"))
            if _as_int(frame) is not None:
                hit_frames.append(frame)

    return {
        "track_count": len(tracks),
        "clip_count": clip_count,
        "track_names": _unique_join(track_names),
        "clip_types": _unique_join(clip_types),
        "effect_paths": _unique_join(effect_paths),
        "sound_ids": _unique_join(sound_ids),
        "hit_frames": _unique_join(hit_frames),
    }


def _compact_luaconfig_row(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    skill_id = _as_int(row.get("q_skillID")) or _as_int(row.get("_row_key"))
    skill_id_text = _as_text(skill_id or row.get("q_skillID") or row.get("_row_key"))
    summary = _summarize_timeline_fields(row)
    return {
        "source_file": path.name,
        "source_path": str(path),
        "skill_id": skill_id,
        "skill_id_text": skill_id_text,
        "feature_prefix8": skill_id_text[:8],
        "family_prefix6": skill_id_text[:6],
        "type": row.get("q_type") or "",
        "description": row.get("q_desc") or "",
        "track_time": row.get("q_track_time"),
        "timeline": row.get("q_timeline_displayName") or "",
        **summary,
    }


def _parse_luaconfig_candidates(config_dir: Path, feature_values: list[str]) -> list[dict[str, Any]]:
    prefixes8 = {feature for feature in feature_values if feature}
    prefixes6 = {feature[:6] for feature in feature_values if len(feature) >= 6}
    candidates = [
        path
        for path in config_dir.glob("*.lua")
        if path.stem[:8] in prefixes8 or path.stem[:6] in prefixes6
    ]

    rows: list[dict[str, Any]] = []
    for path in sorted(candidates, key=lambda item: item.name):
        parsed = parse_fanxiu_generated_lua_config(path)
        for row in parsed.get("rows", []):
            rows.append(_compact_luaconfig_row(path, row))
    return rows


def _feature_sort_key(value: Any) -> tuple[int, str]:
    text = _as_text(value)
    parsed = _as_int(text)
    return (parsed if parsed is not None else 10**12, text)


def _build_feature_family_rows(
    lingjie_rows: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    configs_by_family6: dict[str, list[dict[str, Any]]],
    items_by_id: dict[str, list[dict[str, Any]]] | None = None,
    items_by_effect_value: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    items_by_id = items_by_id or {}
    items_by_effect_value = items_by_effect_value or {}
    feature_row_by_id = {_as_text(row.get("feature")): row for row in feature_rows}
    rows_by_gid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in lingjie_rows:
        gid = _as_text(row.get("gid")).strip()
        if gid:
            rows_by_gid[gid].append(row)

    family_rows: list[dict[str, Any]] = []
    for gid, source_rows in sorted(rows_by_gid.items(), key=lambda item: _feature_sort_key(item[0])):
        features = sorted(
            {_as_text(row.get("feature")).strip() for row in source_rows if _as_text(row.get("feature")).strip()},
            key=_feature_sort_key,
        )
        if not features:
            continue
        linked_feature_rows = [feature_row_by_id[feature] for feature in features if feature in feature_row_by_id]
        direct_count = sum(1 for row in linked_feature_rows if row.get("direct_match_count"))
        family_count = sum(1 for row in linked_feature_rows if row.get("family_match_count"))
        unmatched_count = len(features) - family_count
        family_prefixes = sorted({feature[:6] for feature in features if len(feature) >= 6})
        family_configs: list[dict[str, Any]] = []
        for prefix in family_prefixes:
            family_configs.extend(configs_by_family6.get(prefix, []))
        preview_source = next((row for row in source_rows if row.get("describe")), source_rows[0])
        if direct_count:
            status = "direct_luaconfig"
        elif family_count:
            status = "family_luaconfig"
        else:
            status = "no_luaconfig_match"
        item_summary = _collect_item_rows_for_family(gid, source_rows, items_by_id, items_by_effect_value)

        family_rows.append(
            {
                "source_gid": gid,
                "status": status,
                "feature_prefixes": _unique_join(family_prefixes, limit=20),
                "feature_count": len(features),
                "features": _unique_join(features, limit=40),
                "source_row_count": len(source_rows),
                "source_jie": _unique_join([row.get("jie") for row in source_rows], limit=40),
                "source_names": _unique_join([row.get("name") for row in source_rows], limit=40),
                "source_describe": _preview(preview_source.get("describe") or preview_source.get("describe_plain"), 260),
                "direct_match_feature_count": direct_count,
                "family_match_feature_count": family_count,
                "unmatched_feature_count": unmatched_count,
                "candidate_count": len(family_configs),
                "candidate_ids": _unique_join([row.get("skill_id_text") for row in family_configs], limit=40),
                "candidate_descriptions": _unique_join([row.get("description") for row in family_configs], limit=40),
                "candidate_timelines": _unique_join([row.get("timeline") for row in family_configs], limit=40),
                "candidate_effect_paths": _unique_join([row.get("effect_paths") for row in family_configs], limit=40),
                "candidate_sound_ids": _unique_join([row.get("sound_ids") for row in family_configs], limit=40),
                **item_summary,
            }
        )
    return family_rows


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _sort_by_fields(row: dict[str, Any], *fields: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for field in fields:
        value = row.get(field)
        value_int = _as_int(value)
        parts.append((0, value_int) if value_int is not None else (1, _as_text(value)))
    parts.append(_as_text(row.get("_row_key") or row.get("id")))
    return tuple(parts)


def _compact_feature_value(value: Any, *, limit: int = 120) -> str:
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = _as_text(value)
    text = _WHITESPACE_RE.sub(" ", strip_fanxiu_rich_text(text)).strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _compact_lingjie_feature_row(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "row_key": _as_text(row.get("_row_key") or row.get("id")).strip(),
    }
    for field in fields:
        value = row.get(field)
        if value is not None and value != "":
            compact[field] = value
    plain_name = row.get("name_plain") or row.get("name")
    plain_describe = row.get("describe_plain") or row.get("describe")
    if plain_name:
        compact["name"] = strip_fanxiu_rich_text(_as_text(plain_name)).strip()
    if plain_describe:
        compact["describe"] = strip_fanxiu_rich_text(_as_text(plain_describe)).strip()
    return compact


def build_fanxiu_lingjie_feature_catalog(
    *,
    feature_base_rows_path: str | Path | None = None,
    main_feature_rows_path: str | Path | None = None,
    main_feature_pin_rows_path: str | Path | None = None,
    side_feature_jie_rows_path: str | Path | None = None,
    side_feature_pin_rows_path: str | Path | None = None,
    lingjie_gongfa_jie_rows_path: str | Path | None = None,
    lingjie_gongfa_star_rows_path: str | Path | None = None,
    gongfa_rows_path: str | Path | None = None,
    item_rows_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_feature_base_rows_path = _resolve_export_file(
        feature_base_rows_path,
        DEFAULT_LINGJIE_FEATURE_BASE_ROWS,
        export_root=export_root,
    )
    resolved_main_feature_rows_path = _resolve_export_file(
        main_feature_rows_path,
        DEFAULT_LINGJIE_MAIN_FEATURE_ROWS,
        export_root=export_root,
    )
    resolved_main_feature_pin_rows_path = _resolve_export_file(
        main_feature_pin_rows_path,
        DEFAULT_LINGJIE_MAIN_FEATURE_PIN_ROWS,
        export_root=export_root,
    )
    resolved_side_feature_jie_rows_path = _resolve_export_file(
        side_feature_jie_rows_path,
        DEFAULT_LINGJIE_SIDE_FEATURE_JIE_ROWS,
        export_root=export_root,
    )
    resolved_side_feature_pin_rows_path = _resolve_export_file(
        side_feature_pin_rows_path,
        DEFAULT_LINGJIE_SIDE_FEATURE_PIN_ROWS,
        export_root=export_root,
    )
    resolved_lingjie_gongfa_jie_rows_path = _resolve_export_file(
        lingjie_gongfa_jie_rows_path,
        DEFAULT_LINGJIE_SELF_GONGFA_JIE_ROWS,
        export_root=export_root,
    )
    resolved_lingjie_gongfa_star_rows_path = _resolve_export_file(
        lingjie_gongfa_star_rows_path,
        DEFAULT_LINGJIE_SELF_GONGFA_STAR_ROWS,
        export_root=export_root,
    )
    resolved_gongfa_rows_path = _resolve_optional_export_file(
        gongfa_rows_path,
        DEFAULT_GONGFA_ROWS,
        export_root=export_root,
    )
    resolved_item_rows_path = _resolve_optional_export_file(
        item_rows_path,
        DEFAULT_ITEM_ROWS,
        export_root=export_root,
    )

    out_dir = root / "parsed_configs" / "lingjie_feature_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)

    feature_base_rows = _load_json_rows(resolved_feature_base_rows_path)
    main_feature_rows = _load_json_rows(resolved_main_feature_rows_path)
    main_feature_pin_rows = _load_json_rows(resolved_main_feature_pin_rows_path)
    side_feature_jie_rows = _load_json_rows(resolved_side_feature_jie_rows_path)
    side_feature_pin_rows = _load_json_rows(resolved_side_feature_pin_rows_path)
    lingjie_gongfa_jie_rows = _load_json_rows(resolved_lingjie_gongfa_jie_rows_path)
    lingjie_gongfa_star_rows = _load_json_rows(resolved_lingjie_gongfa_star_rows_path)
    gongfa_rows = _load_json_rows(resolved_gongfa_rows_path) if resolved_gongfa_rows_path else []
    item_rows = _load_json_rows(resolved_item_rows_path) if resolved_item_rows_path else []

    feature_base_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in feature_base_rows:
        group = _as_text(row.get("group")).strip()
        if group:
            feature_base_by_group[group].append(row)

    main_feature_by_gongfa: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in main_feature_rows:
        gongfa_id = _as_text(row.get("gongfaId")).strip()
        if gongfa_id:
            main_feature_by_gongfa[gongfa_id].append(row)

    main_pin_by_gongfa: dict[str, list[dict[str, Any]]] = defaultdict(list)
    main_pin_by_feature_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in main_feature_pin_rows:
        gongfa_id = _as_text(row.get("gongfaId")).strip()
        feature_group = _as_text(row.get("featureGroup")).strip()
        if gongfa_id:
            main_pin_by_gongfa[gongfa_id].append(row)
        if feature_group:
            main_pin_by_feature_group[feature_group].append(row)

    side_jie_by_feature_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in side_feature_jie_rows:
        feature_group = _as_text(row.get("featureGroup")).strip()
        if feature_group:
            side_jie_by_feature_group[feature_group].append(row)

    side_pin_by_feature_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in side_feature_pin_rows:
        feature_group = _as_text(row.get("featureGroup")).strip()
        if feature_group:
            side_pin_by_feature_group[feature_group].append(row)

    jie_by_gongfa: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in lingjie_gongfa_jie_rows:
        gongfa_id = _as_text(row.get("gongfaId")).strip()
        if gongfa_id:
            jie_by_gongfa[gongfa_id].append(row)

    star_by_gongfa: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in lingjie_gongfa_star_rows:
        gongfa_id = _as_text(row.get("gongfaId")).strip()
        if gongfa_id:
            star_by_gongfa[gongfa_id].append(row)

    gongfa_by_id = _collect_rows_by_text_key(gongfa_rows, "id")
    items_by_effect_value = _collect_rows_by_text_key(item_rows, "effectValue")

    group_link_rows: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    all_gongfa_ids = sorted(
        set(main_feature_by_gongfa)
        | set(main_pin_by_gongfa)
        | set(jie_by_gongfa)
        | set(star_by_gongfa),
        key=lambda value: (_as_int(value) is None, _as_int(value) or 0, value),
    )
    used_feature_base_groups: set[str] = set()
    used_feature_groups: set[str] = set()
    linked_main_pin_groups: set[str] = set()
    linked_side_jie_groups: set[str] = set()
    linked_side_pin_groups: set[str] = set()

    for gongfa_id in all_gongfa_ids:
        main_features: list[dict[str, Any]] = []
        card_group_links: list[dict[str, Any]] = []
        for main_row in sorted(main_feature_by_gongfa.get(gongfa_id, []), key=lambda row: _sort_by_fields(row, "featureType", "id")):
            group_values = [_as_text(item).strip() for item in _as_list(main_row.get("groups")) if _as_text(item).strip()]
            expanded_groups: list[dict[str, Any]] = []
            for group in group_values:
                used_feature_base_groups.add(group)
                for base_row in sorted(feature_base_by_group.get(group, []), key=lambda row: _sort_by_fields(row, "featureGroup", "id")):
                    feature_group = _as_text(base_row.get("featureGroup")).strip()
                    if not feature_group:
                        continue
                    used_feature_groups.add(feature_group)
                    main_pin_rows = sorted(main_pin_by_feature_group.get(feature_group, []), key=lambda row: _sort_by_fields(row, "pin", "quality", "id"))
                    side_jie_rows = sorted(side_jie_by_feature_group.get(feature_group, []), key=lambda row: _sort_by_fields(row, "jie", "sortValue", "id"))
                    side_pin_rows = sorted(side_pin_by_feature_group.get(feature_group, []), key=lambda row: _sort_by_fields(row, "pin", "quality", "id"))
                    target_kinds: list[str] = []
                    if main_pin_rows:
                        target_kinds.append("main_pin")
                        linked_main_pin_groups.add(feature_group)
                    if side_jie_rows:
                        target_kinds.append("side_jie")
                        linked_side_jie_groups.add(feature_group)
                    if side_pin_rows:
                        target_kinds.append("side_pin")
                        linked_side_pin_groups.add(feature_group)
                    if not target_kinds:
                        target_kinds.append("unmatched")

                    sample_rows = (main_pin_rows or [])[:2] + (side_jie_rows or [])[:2] + (side_pin_rows or [])[:2]
                    link = {
                        "gongfa_id": gongfa_id,
                        "main_feature_id": _as_text(main_row.get("id") or main_row.get("_row_key")).strip(),
                        "feature_type": main_row.get("featureType"),
                        "group": group,
                        "feature_group": feature_group,
                        "key_feature": base_row.get("keyFeature"),
                        "weighted": base_row.get("weighted"),
                        "quality": base_row.get("quality"),
                        "target_kinds": target_kinds,
                        "main_pin_count": len(main_pin_rows),
                        "side_jie_count": len(side_jie_rows),
                        "side_pin_count": len(side_pin_rows),
                        "sample_names": _unique_join([row.get("name_plain") or row.get("name") for row in sample_rows], limit=8),
                        "sample_features": _unique_join([row.get("feature") or row.get("skill") for row in sample_rows], limit=8),
                        "sample_describes": _unique_join(
                            [_preview(row.get("describe_plain") or row.get("describe"), 80) for row in sample_rows],
                            limit=4,
                        ),
                    }
                    expanded_groups.append(link)
                    card_group_links.append(link)
                    group_link_rows.append(link)

            main_features.append(
                {
                    "row_key": _as_text(main_row.get("_row_key") or main_row.get("id")).strip(),
                    "id": main_row.get("id"),
                    "feature_type": main_row.get("featureType"),
                    "groups": group_values,
                    "condition": main_row.get("condition"),
                    "describe": strip_fanxiu_rich_text(
                        _as_text(main_row.get("describe_plain") or main_row.get("describe"))
                    ).strip(),
                    "expanded_groups": expanded_groups,
                }
            )

        main_pin_rows_for_card = sorted(main_pin_by_gongfa.get(gongfa_id, []), key=lambda row: _sort_by_fields(row, "pin", "quality", "id"))
        jie_rows_for_card = sorted(jie_by_gongfa.get(gongfa_id, []), key=lambda row: _sort_by_fields(row, "jie", "sortValue", "id"))
        star_rows_for_card = sorted(star_by_gongfa.get(gongfa_id, []), key=lambda row: _sort_by_fields(row, "star", "sortValue", "id"))
        matched_gongfa_rows = gongfa_by_id.get(gongfa_id, [])
        matched_item_rows = items_by_effect_value.get(gongfa_id, [])
        primary_gongfa = matched_gongfa_rows[0] if matched_gongfa_rows else {}
        primary_item = matched_item_rows[0] if matched_item_rows else {}
        display_name = (
            primary_gongfa.get("name_plain")
            or primary_gongfa.get("name")
            or primary_item.get("name_plain")
            or primary_item.get("name")
            or ""
        )
        display_description = (
            primary_gongfa.get("descript_plain")
            or primary_gongfa.get("describe_plain")
            or primary_gongfa.get("descript")
            or primary_gongfa.get("describe")
            or primary_item.get("descript_plain")
            or primary_item.get("describe_plain")
            or primary_item.get("descript")
            or primary_item.get("describe")
            or ""
        )
        cards.append(
            {
                "gongfa_id": gongfa_id,
                "name": strip_fanxiu_rich_text(_as_text(display_name)).strip(),
                "description": strip_fanxiu_rich_text(_as_text(display_description)).strip(),
                "icon": primary_item.get("icon") or primary_gongfa.get("icon") or "",
                "quality": primary_item.get("quality") or primary_gongfa.get("quality") or "",
                "item_count": len(matched_item_rows),
                "main_feature_count": len(main_features),
                "main_pin_count": len(main_pin_rows_for_card),
                "jie_count": len(jie_rows_for_card),
                "star_count": len(star_rows_for_card),
                "feature_group_link_count": len(card_group_links),
                "main_pin_group_count": sum(1 for row in card_group_links if "main_pin" in row["target_kinds"]),
                "side_jie_group_count": sum(1 for row in card_group_links if "side_jie" in row["target_kinds"]),
                "side_pin_group_count": sum(1 for row in card_group_links if "side_pin" in row["target_kinds"]),
                "main_feature_names": _unique_join([row.get("name_plain") or row.get("name") for row in main_pin_rows_for_card], limit=12),
                "side_feature_names": _unique_join([row.get("sample_names") for row in card_group_links if "side_jie" in row["target_kinds"]], limit=12),
                "jie_features": _unique_join([row.get("feature") for row in jie_rows_for_card], limit=20),
                "star_skills": _unique_join([row.get("skill") for row in star_rows_for_card], limit=20),
                "items": [
                    _compact_lingjie_feature_row(row, ["id", "quality", "icon", "effectValue"])
                    for row in matched_item_rows
                ],
                "main_features": main_features,
                "main_pin_rows": [
                    _compact_lingjie_feature_row(row, ["id", "gongfaId", "pin", "quality", "featureGroup", "feature", "sortValue"])
                    for row in main_pin_rows_for_card
                ],
                "jie_rows": [
                    _compact_lingjie_feature_row(row, ["id", "gongfaId", "jie", "feature", "param", "sortValue"])
                    for row in jie_rows_for_card
                ],
                "star_rows": [
                    _compact_lingjie_feature_row(row, ["id", "gongfaId", "star", "quality", "skill", "param", "sortValue", "cd"])
                    for row in star_rows_for_card
                ],
            }
        )

    feature_group_rows: list[dict[str, Any]] = []
    for feature_group in sorted(
        set(main_pin_by_feature_group) | set(side_jie_by_feature_group) | set(side_pin_by_feature_group),
        key=lambda value: (_as_int(value) is None, _as_int(value) or 0, value),
    ):
        main_pin_rows = sorted(main_pin_by_feature_group.get(feature_group, []), key=lambda row: _sort_by_fields(row, "pin", "quality", "id"))
        side_jie_rows = sorted(side_jie_by_feature_group.get(feature_group, []), key=lambda row: _sort_by_fields(row, "jie", "sortValue", "id"))
        side_pin_rows = sorted(side_pin_by_feature_group.get(feature_group, []), key=lambda row: _sort_by_fields(row, "pin", "quality", "id"))
        target_kinds = []
        if main_pin_rows:
            target_kinds.append("main_pin")
        if side_jie_rows:
            target_kinds.append("side_jie")
        if side_pin_rows:
            target_kinds.append("side_pin")
        feature_group_rows.append(
            {
                "feature_group": feature_group,
                "target_kinds": target_kinds,
                "main_pin_count": len(main_pin_rows),
                "side_jie_count": len(side_jie_rows),
                "side_pin_count": len(side_pin_rows),
                "sample_names": _unique_join(
                    [row.get("name_plain") or row.get("name") for row in main_pin_rows[:3] + side_jie_rows[:3] + side_pin_rows[:3]],
                    limit=10,
                ),
                "sample_features": _unique_join(
                    [row.get("feature") or row.get("skill") for row in main_pin_rows[:3] + side_jie_rows[:3] + side_pin_rows[:3]],
                    limit=10,
                ),
                "sample_params": _unique_join(
                    [_compact_feature_value(row.get("param")) for row in side_jie_rows[:3] + side_pin_rows[:3]],
                    limit=6,
                ),
            }
        )

    card_rows = [
        {
            "gongfa_id": card["gongfa_id"],
            "name": card["name"],
            "item_count": card["item_count"],
            "item_names": _unique_join([row.get("name") for row in card["items"]], limit=12),
            "icon": card["icon"],
            "quality": card["quality"],
            "description": _preview(card["description"], 120),
            "main_feature_count": card["main_feature_count"],
            "main_pin_count": card["main_pin_count"],
            "jie_count": card["jie_count"],
            "star_count": card["star_count"],
            "feature_group_link_count": card["feature_group_link_count"],
            "main_pin_group_count": card["main_pin_group_count"],
            "side_jie_group_count": card["side_jie_group_count"],
            "side_pin_group_count": card["side_pin_group_count"],
            "main_feature_names": card["main_feature_names"],
            "side_feature_names": card["side_feature_names"],
            "jie_features": card["jie_features"],
            "star_skills": card["star_skills"],
        }
        for card in cards
    ]
    tsv_group_link_rows = [
        {
            **row,
            "target_kinds": ",".join(row["target_kinds"]),
        }
        for row in group_link_rows
    ]
    tsv_feature_group_rows = [
        {
            **row,
            "target_kinds": ",".join(row["target_kinds"]),
        }
        for row in feature_group_rows
    ]

    stats = {
        "gongfa_count": len(cards),
        "feature_base_row_count": len(feature_base_rows),
        "feature_base_group_count": len(feature_base_by_group),
        "main_feature_row_count": len(main_feature_rows),
        "main_feature_pin_row_count": len(main_feature_pin_rows),
        "side_feature_jie_row_count": len(side_feature_jie_rows),
        "side_feature_pin_row_count": len(side_feature_pin_rows),
        "lingjie_gongfa_jie_row_count": len(lingjie_gongfa_jie_rows),
        "lingjie_gongfa_star_row_count": len(lingjie_gongfa_star_rows),
        "gongfa_row_count": len(gongfa_rows),
        "item_row_count": len(item_rows),
        "linked_gongfa_name_count": sum(1 for card in cards if card["name"]),
        "linked_item_count": sum(card["item_count"] for card in cards),
        "used_feature_base_group_count": len(used_feature_base_groups),
        "unused_feature_base_group_count": len(set(feature_base_by_group) - used_feature_base_groups),
        "linked_feature_group_count": len(used_feature_groups),
        "linked_main_pin_group_count": len(linked_main_pin_groups),
        "linked_side_jie_group_count": len(linked_side_jie_groups),
        "linked_side_pin_group_count": len(linked_side_pin_groups),
        "unmatched_group_link_count": sum(1 for row in group_link_rows if row["target_kinds"] == ["unmatched"]),
    }

    catalog_path = out_dir / "lingjie_feature_catalog.json"
    cards_tsv_path = out_dir / "lingjie_feature_cards.tsv"
    group_links_tsv_path = out_dir / "lingjie_feature_group_links.tsv"
    feature_groups_tsv_path = out_dir / "lingjie_feature_groups.tsv"
    report_path = out_dir / "lingjie_feature_catalog_report.md"

    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paths": {
                    "feature_base_rows": str(resolved_feature_base_rows_path),
                    "main_feature_rows": str(resolved_main_feature_rows_path),
                    "main_feature_pin_rows": str(resolved_main_feature_pin_rows_path),
                    "side_feature_jie_rows": str(resolved_side_feature_jie_rows_path),
                    "side_feature_pin_rows": str(resolved_side_feature_pin_rows_path),
                    "lingjie_gongfa_jie_rows": str(resolved_lingjie_gongfa_jie_rows_path),
                    "lingjie_gongfa_star_rows": str(resolved_lingjie_gongfa_star_rows_path),
                    "gongfa_rows": str(resolved_gongfa_rows_path) if resolved_gongfa_rows_path else "",
                    "item_rows": str(resolved_item_rows_path) if resolved_item_rows_path else "",
                },
                "stats": stats,
                "cards": cards,
                "feature_groups": feature_group_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_tsv(
        cards_tsv_path,
        card_rows,
        [
            "gongfa_id",
            "name",
            "item_count",
            "item_names",
            "icon",
            "quality",
            "description",
            "main_feature_count",
            "main_pin_count",
            "jie_count",
            "star_count",
            "feature_group_link_count",
            "main_pin_group_count",
            "side_jie_group_count",
            "side_pin_group_count",
            "main_feature_names",
            "side_feature_names",
            "jie_features",
            "star_skills",
        ],
    )
    _write_tsv(
        group_links_tsv_path,
        tsv_group_link_rows,
        [
            "gongfa_id",
            "main_feature_id",
            "feature_type",
            "group",
            "feature_group",
            "key_feature",
            "weighted",
            "quality",
            "target_kinds",
            "main_pin_count",
            "side_jie_count",
            "side_pin_count",
            "sample_names",
            "sample_features",
            "sample_describes",
        ],
    )
    _write_tsv(
        feature_groups_tsv_path,
        tsv_feature_group_rows,
        [
            "feature_group",
            "target_kinds",
            "main_pin_count",
            "side_jie_count",
            "side_pin_count",
            "sample_names",
            "sample_features",
            "sample_params",
        ],
    )
    report_path.write_text(
        "\n".join(
            [
                "# LingjieGongfa feature 结构目录",
                "",
                "`LingjieGongfa.*` 是另一套自创/灵界功法词条体系，和 `Lingjie-GongfaJie` 的道具进阶来源表不是同一张表。",
                "",
                "## 关系",
                "",
                "- MainFeature.gongfaId 定位功法。",
                "- MainFeature.groups 进入 FeatureBase.group。",
                "- FeatureBase.featureGroup 再落到 MainFeaturePin / SideFeatureJie / SideFeaturePin。",
                "- LingjieGongfaJie 与 LingjieGongfaStar 按 gongfaId 直接挂到同一张卡。",
                "",
                "## 统计",
                "",
                f"- 功法数：{stats['gongfa_count']}",
                f"- FeatureBase 行/组：{stats['feature_base_row_count']} / {stats['feature_base_group_count']}",
                f"- MainFeature 行：{stats['main_feature_row_count']}",
                f"- MainFeaturePin 行：{stats['main_feature_pin_row_count']}",
                f"- SideFeatureJie 行：{stats['side_feature_jie_row_count']}",
                f"- SideFeaturePin 行：{stats['side_feature_pin_row_count']}",
                f"- LingjieGongfaJie 行：{stats['lingjie_gongfa_jie_row_count']}",
                f"- LingjieGongfaStar 行：{stats['lingjie_gongfa_star_row_count']}",
                f"- Gongfa/Item 行：{stats['gongfa_row_count']} / {stats['item_row_count']}",
                f"- 已反查到名称的功法卡：{stats['linked_gongfa_name_count']}",
                f"- 已关联道具数：{stats['linked_item_count']}",
                f"- 已被 MainFeature 使用的 FeatureBase 组：{stats['used_feature_base_group_count']}",
                f"- 未被 MainFeature 使用的 FeatureBase 组：{stats['unused_feature_base_group_count']}",
                f"- 关联 main_pin / side_jie / side_pin 组：{stats['linked_main_pin_group_count']} / {stats['linked_side_jie_group_count']} / {stats['linked_side_pin_group_count']}",
                f"- 未命中目标表的 group link：{stats['unmatched_group_link_count']}",
                "",
                "## 输出文件",
                "",
                f"- lingjie_feature_catalog.json: {catalog_path}",
                f"- lingjie_feature_cards.tsv: {cards_tsv_path}",
                f"- lingjie_feature_group_links.tsv: {group_links_tsv_path}",
                f"- lingjie_feature_groups.tsv: {feature_groups_tsv_path}",
            ]
        ),
        encoding="utf-8",
    )

    return {
        "output_dir": str(out_dir),
        "stats": stats,
        "files": {
            "catalog": str(catalog_path),
            "cards_tsv": str(cards_tsv_path),
            "group_links_tsv": str(group_links_tsv_path),
            "feature_groups_tsv": str(feature_groups_tsv_path),
            "report": str(report_path),
        },
    }


def _default_lingjie_feature_catalog_source_files(root: Path) -> list[Path]:
    return [
        root / DEFAULT_LINGJIE_FEATURE_BASE_ROWS,
        root / DEFAULT_LINGJIE_MAIN_FEATURE_ROWS,
        root / DEFAULT_LINGJIE_MAIN_FEATURE_PIN_ROWS,
        root / DEFAULT_LINGJIE_SIDE_FEATURE_JIE_ROWS,
        root / DEFAULT_LINGJIE_SIDE_FEATURE_PIN_ROWS,
        root / DEFAULT_LINGJIE_SELF_GONGFA_JIE_ROWS,
        root / DEFAULT_LINGJIE_SELF_GONGFA_STAR_ROWS,
        root / DEFAULT_GONGFA_ROWS,
        root / DEFAULT_ITEM_ROWS,
    ]


def _is_lingjie_feature_catalog_stale(catalog_path: Path, root: Path) -> bool:
    if not catalog_path.is_file():
        return True
    try:
        with catalog_path.open("r", encoding="utf-8") as file:
            header = file.read(4096)
    except OSError:
        return True
    match = re.search(r'"schema_version"\s*:\s*(\d+)', header)
    if not match or int(match.group(1)) != 1:
        return True
    catalog_mtime_ns = catalog_path.stat().st_mtime_ns
    return any(
        source_path.is_file() and source_path.stat().st_mtime_ns > catalog_mtime_ns
        for source_path in _default_lingjie_feature_catalog_source_files(root)
    )


def _resolve_lingjie_feature_catalog_file(
    export_root: str | Path | None = None,
    *,
    rebuild_missing: bool = True,
) -> Path:
    root = resolve_fanxiu_export_root(export_root)
    path = (root / DEFAULT_LINGJIE_FEATURE_CATALOG).resolve()
    if not _is_relative_to(path, root):
        raise FanxiuResourceError(f"文件必须位于导出根目录内：{root}")
    if rebuild_missing and _is_lingjie_feature_catalog_stale(path, root):
        build_fanxiu_lingjie_feature_catalog(export_root=export_root)
    if not path.is_file():
        raise FanxiuResourceError(f"LingjieGongfa feature 目录不存在，请先生成：{path}")
    return path


@lru_cache(maxsize=4)
def _load_lingjie_feature_catalog_cached(path_text: str, mtime_ns: int, size: int, export_root_text: str) -> dict[str, Any]:
    catalog_path = Path(path_text)
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        raise FanxiuResourceError(f"LingjieGongfa feature 目录格式不正确：{catalog_path}")
    return {
        "export_root": export_root_text,
        "catalog_path": str(catalog_path),
        **data,
    }


def load_fanxiu_lingjie_feature_catalog(
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    catalog_path = _resolve_lingjie_feature_catalog_file(export_root, rebuild_missing=rebuild_missing)
    root = resolve_fanxiu_export_root(export_root)
    stat = catalog_path.stat()
    return _load_lingjie_feature_catalog_cached(str(catalog_path), stat.st_mtime_ns, stat.st_size, str(root))


def _read_lingjie_runtime_tsv(runtime_dir: Path, filename: str) -> list[dict[str, str]]:
    path = runtime_dir / filename
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return [dict(row) for row in csv.DictReader(file, delimiter="\t") if isinstance(row, dict)]


def _split_lingjie_runtime_values(value: Any) -> list[str]:
    return [item.strip() for item in re.split(r"[、,，;；\s]+", str(value or "")) if item.strip()]


def _compact_lingjie_runtime_row(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        value = row.get(field)
        if value not in (None, ""):
            result[field] = value
    return result


def _build_lingjie_runtime_context(root: Path) -> dict[str, dict[str, Any]]:
    runtime_dir = root / DEFAULT_LINGJIE_RUNTIME_DIR
    projected_rows = _read_lingjie_runtime_tsv(runtime_dir, "lingjie_runtime_projected_skills.tsv")
    profile_rows = _read_lingjie_runtime_tsv(runtime_dir, "lingjie_runtime_projected_skill_damage_profiles.tsv")
    family_rows = _read_lingjie_runtime_tsv(runtime_dir, "lingjie_runtime_projected_skill_damage_families.tsv")
    timeline_rows = _read_lingjie_runtime_tsv(runtime_dir, "lingjie_runtime_timeline_details.tsv")
    runtime_by_gongfa: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "projected_skill_count": 0,
            "profile_count": 0,
            "timeline_ids": set(),
            "careers": set(),
            "profile_samples": [],
            "damage_families": [],
            "timeline_samples": [],
        }
    )

    for row in projected_rows:
        gongfa_id = str(row.get("gongfa_id") or "").strip()
        if gongfa_id:
            runtime_by_gongfa[gongfa_id]["projected_skill_count"] += 1

    for row in profile_rows:
        gongfa_id = str(row.get("gongfa_id") or "").strip()
        if not gongfa_id:
            continue
        summary = runtime_by_gongfa[gongfa_id]
        summary["profile_count"] += 1
        if row.get("timeline_id"):
            summary["timeline_ids"].add(str(row.get("timeline_id")))
        if row.get("career"):
            summary["careers"].add(str(row.get("career")))
        if len(summary["profile_samples"]) < 8:
            summary["profile_samples"].append(
                _compact_lingjie_runtime_row(
                    row,
                    [
                        "star",
                        "projected_skill_id",
                        "skill_name",
                        "career",
                        "timeline_id",
                        "hit_count",
                        "first_hit_ms",
                        "last_hit_ms",
                        "hit_times_ms",
                        "hurt_percents",
                        "total_hurt_percent",
                        "damage_scope_types",
                        "scope_params",
                        "target_type",
                        "target_max",
                        "cd_time",
                    ],
                )
            )

    for row in family_rows:
        for gongfa_id in _split_lingjie_runtime_values(row.get("sample_gongfas")):
            summary = runtime_by_gongfa[gongfa_id]
            if len(summary["damage_families"]) >= 6:
                continue
            summary["damage_families"].append(
                _compact_lingjie_runtime_row(
                    row,
                    [
                        "family_id",
                        "careers",
                        "profile_count",
                        "skill_count",
                        "timeline_count",
                        "channel",
                        "hit_count",
                        "first_hit_ms",
                        "last_hit_ms",
                        "hit_times_ms",
                        "hurt_percents",
                        "total_hurt_percent",
                        "damage_scope_types",
                        "scope_params",
                        "scope",
                        "target_type",
                        "target_max",
                        "cd_times",
                        "fight_scores",
                        "sample_timelines",
                    ],
                )
            )

    timeline_by_id = {str(row.get("timeline_id")): row for row in timeline_rows if row.get("timeline_id")}
    for summary in runtime_by_gongfa.values():
        for timeline_id in sorted(summary["timeline_ids"]):
            row = timeline_by_id.get(timeline_id)
            if not row or len(summary["timeline_samples"]) >= 8:
                continue
            summary["timeline_samples"].append(
                _compact_lingjie_runtime_row(
                    row,
                    [
                        "timeline_id",
                        "careers",
                        "q_desc",
                        "q_track_time",
                        "hurt_event_count",
                        "q_hurt_events",
                        "effect_resources",
                        "sound_ids",
                    ],
                )
            )

    normalized: dict[str, dict[str, Any]] = {}
    for gongfa_id, summary in runtime_by_gongfa.items():
        timeline_ids = sorted(summary.pop("timeline_ids"))
        careers = sorted(summary.pop("careers"))
        if not any((summary["projected_skill_count"], summary["profile_count"], timeline_ids, careers)):
            continue
        normalized[gongfa_id] = {
            **summary,
            "timeline_count": len(timeline_ids),
            "timeline_ids": timeline_ids[:12],
            "careers": careers,
        }
    return normalized


def _lingjie_runtime_signature(root: Path) -> tuple[int, int]:
    runtime_dir = root / DEFAULT_LINGJIE_RUNTIME_DIR
    files = [
        runtime_dir / "lingjie_runtime_projected_skills.tsv",
        runtime_dir / "lingjie_runtime_projected_skill_damage_profiles.tsv",
        runtime_dir / "lingjie_runtime_projected_skill_damage_families.tsv",
        runtime_dir / "lingjie_runtime_timeline_details.tsv",
    ]
    existing = [path for path in files if path.is_file()]
    if not existing:
        return 0, 0
    return max(path.stat().st_mtime_ns for path in existing), sum(path.stat().st_size for path in existing)


def _normalize_lingjie_search_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    return _WHITESPACE_RE.sub(" ", strip_fanxiu_rich_text(text)).strip().lower()


def _build_lingjie_feature_search_doc(card: dict[str, Any], index: int) -> dict[str, Any]:
    text_parts: list[str] = [
        _normalize_lingjie_search_text(card.get("gongfa_id")),
        _normalize_lingjie_search_text(card.get("name")),
        _normalize_lingjie_search_text(card.get("description")),
        _normalize_lingjie_search_text(card.get("icon")),
        _normalize_lingjie_search_text(card.get("quality")),
        _normalize_lingjie_search_text(card.get("main_feature_names")),
        _normalize_lingjie_search_text(card.get("side_feature_names")),
        _normalize_lingjie_search_text(card.get("jie_features")),
        _normalize_lingjie_search_text(card.get("star_skills")),
    ]
    for item in card.get("items") or []:
        if isinstance(item, dict):
            text_parts.extend(
                [
                    _normalize_lingjie_search_text(item.get("id")),
                    _normalize_lingjie_search_text(item.get("name")),
                    _normalize_lingjie_search_text(item.get("icon")),
                ]
            )
    for main_feature in card.get("main_features") or []:
        if not isinstance(main_feature, dict):
            continue
        text_parts.extend(
            [
                _normalize_lingjie_search_text(main_feature.get("id")),
                _normalize_lingjie_search_text(main_feature.get("feature_type")),
                _normalize_lingjie_search_text(main_feature.get("groups")),
                _normalize_lingjie_search_text(main_feature.get("describe")),
            ]
        )
        for link in main_feature.get("expanded_groups") or []:
            if isinstance(link, dict):
                text_parts.extend(
                    [
                        _normalize_lingjie_search_text(link.get("group")),
                        _normalize_lingjie_search_text(link.get("feature_group")),
                        _normalize_lingjie_search_text(link.get("sample_names")),
                        _normalize_lingjie_search_text(link.get("sample_features")),
                        _normalize_lingjie_search_text(link.get("sample_describes")),
                    ]
                )
    return {
        "index": index,
        "card": card,
        "gongfa_id": str(card.get("gongfa_id") or ""),
        "name": _normalize_lingjie_search_text(card.get("name")),
        "combined": " ".join(part for part in text_parts if part),
    }


@lru_cache(maxsize=4)
def _load_lingjie_feature_runtime_index_cached(
    path_text: str,
    mtime_ns: int,
    size: int,
    export_root_text: str,
    runtime_mtime_ns: int,
    runtime_size: int,
) -> dict[str, Any]:
    catalog = _load_lingjie_feature_catalog_cached(path_text, mtime_ns, size, export_root_text)
    root = Path(export_root_text)
    cards = [card for card in catalog.get("cards") or [] if isinstance(card, dict)]
    cards_by_id = {str(card.get("gongfa_id")): card for card in cards if card.get("gongfa_id") not in (None, "")}
    return {
        "catalog": catalog,
        "cards_by_id": cards_by_id,
        "runtime_by_gongfa": _build_lingjie_runtime_context(root),
        "search_docs": tuple(_build_lingjie_feature_search_doc(card, index) for index, card in enumerate(cards)),
    }


def load_fanxiu_lingjie_feature_runtime_index(
    *,
    export_root: str | Path | None = None,
    rebuild_missing: bool = True,
) -> dict[str, Any]:
    catalog_path = _resolve_lingjie_feature_catalog_file(export_root, rebuild_missing=rebuild_missing)
    root = resolve_fanxiu_export_root(export_root)
    stat = catalog_path.stat()
    runtime_mtime_ns, runtime_size = _lingjie_runtime_signature(root)
    return _load_lingjie_feature_runtime_index_cached(
        str(catalog_path),
        stat.st_mtime_ns,
        stat.st_size,
        str(root),
        runtime_mtime_ns,
        runtime_size,
    )


def _format_lingjie_feature_search_item(card: dict[str, Any], score: int) -> dict[str, Any]:
    return {
        "gongfa_id": card.get("gongfa_id"),
        "name": card.get("name") or str(card.get("gongfa_id") or "未命名"),
        "icon": card.get("icon"),
        "quality": card.get("quality"),
        "item_count": card.get("item_count"),
        "item_names": [row.get("name") for row in card.get("items") or [] if isinstance(row, dict) and row.get("name")],
        "description_preview": _preview(card.get("description"), 160),
        "main_feature_names": card.get("main_feature_names"),
        "side_feature_names": card.get("side_feature_names"),
        "main_feature_count": card.get("main_feature_count"),
        "main_pin_count": card.get("main_pin_count"),
        "jie_count": card.get("jie_count"),
        "star_count": card.get("star_count"),
        "feature_group_link_count": card.get("feature_group_link_count"),
        "main_pin_group_count": card.get("main_pin_group_count"),
        "side_jie_group_count": card.get("side_jie_group_count"),
        "side_pin_group_count": card.get("side_pin_group_count"),
        "score": score,
    }


def search_fanxiu_lingjie_feature_cards(
    *,
    query: str = "",
    limit: int = 80,
    offset: int = 0,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    runtime = load_fanxiu_lingjie_feature_runtime_index(export_root=export_root)
    query_text = _normalize_lingjie_search_text(query)
    rows: list[tuple[int, int, dict[str, Any]]] = []
    for doc in runtime["search_docs"]:
        if not query_text:
            rows.append((0, doc["index"], doc["card"]))
            continue
        score = 0
        if query_text == doc["gongfa_id"]:
            score += 200
        if query_text and query_text in doc["name"]:
            score += 120
        occurrences = doc["combined"].count(query_text)
        if occurrences:
            score += occurrences * 10
        if score:
            rows.append((score, doc["index"], doc["card"]))

    rows.sort(key=lambda item: (-item[0], item[1]))
    page_rows = rows[offset : offset + limit]
    return {
        "total": len(rows),
        "limit": limit,
        "offset": offset,
        "catalog_path": runtime["catalog"].get("catalog_path") or "",
        "items": [_format_lingjie_feature_search_item(card, score) for score, _index, card in page_rows],
        "stats": runtime["catalog"].get("stats") or {},
    }


def get_fanxiu_lingjie_feature_card(
    gongfa_id: str | int,
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    runtime = load_fanxiu_lingjie_feature_runtime_index(export_root=export_root)
    card = runtime["cards_by_id"].get(str(gongfa_id))
    if card is None:
        raise FanxiuResourceError(f"没有找到 LingjieGongfa feature 卡：{gongfa_id}")
    result = dict(card)
    runtime_summary = runtime.get("runtime_by_gongfa", {}).get(str(gongfa_id))
    if runtime_summary:
        result["runtime_summary"] = runtime_summary
    result["user_fields"] = get_fanxiu_wiki_user_fields("lingjie", str(gongfa_id))
    return result


def build_fanxiu_gongfa_feature_probe(
    *,
    lingjie_rows_path: str | Path | None = None,
    config_dir: str | Path | None = None,
    item_rows_path: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_lingjie_rows_path = _resolve_export_file(
        lingjie_rows_path,
        DEFAULT_LINGJIE_GONGFA_JIE_ROWS,
        export_root=export_root,
    )
    resolved_config_dir = _resolve_export_dir(config_dir, export_root=export_root)
    resolved_item_rows_path = _resolve_optional_export_file(
        item_rows_path,
        DEFAULT_ITEM_ROWS,
        export_root=export_root,
    )
    out_dir = root / "parsed_configs" / "gongfa_feature_probe"
    out_dir.mkdir(parents=True, exist_ok=True)

    lingjie_rows = _load_json_rows(resolved_lingjie_rows_path)
    item_rows = _load_json_rows(resolved_item_rows_path) if resolved_item_rows_path else []
    items_by_id = _collect_rows_by_text_key(item_rows, "id")
    items_by_effect_value = _collect_rows_by_text_key(item_rows, "effectValue")
    rows_by_feature: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in lingjie_rows:
        feature = _as_text(row.get("feature")).strip()
        if feature:
            rows_by_feature[feature].append(row)

    feature_values = sorted(rows_by_feature)
    config_rows = _parse_luaconfig_candidates(resolved_config_dir, feature_values)
    configs_by_prefix8: dict[str, list[dict[str, Any]]] = defaultdict(list)
    configs_by_family6: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in config_rows:
        configs_by_prefix8[_as_text(row.get("feature_prefix8"))].append(row)
        configs_by_family6[_as_text(row.get("family_prefix6"))].append(row)

    feature_rows: list[dict[str, Any]] = []
    for feature, source_rows in sorted(rows_by_feature.items()):
        direct_configs = configs_by_prefix8.get(feature, [])
        family_configs = configs_by_family6.get(feature[:6], [])
        preview_source = next((row for row in source_rows if row.get("describe")), source_rows[0])
        feature_rows.append(
            {
                "feature": feature,
                "source_gid": _unique_join([row.get("gid") for row in source_rows], limit=20),
                "source_row_count": len(source_rows),
                "source_jie": _unique_join([row.get("jie") for row in source_rows], limit=20),
                "source_name": _unique_join([row.get("name") for row in source_rows], limit=20),
                "source_describe": _preview(preview_source.get("describe") or preview_source.get("describe_plain")),
                "direct_match_count": len(direct_configs),
                "family_match_count": len(family_configs),
                "match_kind": "direct_prefix8" if direct_configs else ("family_prefix6" if family_configs else ""),
                "config_ids": _unique_join([row.get("skill_id_text") for row in direct_configs], limit=20),
                "config_descriptions": _unique_join([row.get("description") for row in direct_configs], limit=20),
                "timelines": _unique_join([row.get("timeline") for row in direct_configs], limit=20),
                "effect_paths": _unique_join([row.get("effect_paths") for row in direct_configs], limit=20),
                "sound_ids": _unique_join([row.get("sound_ids") for row in direct_configs], limit=20),
                "hit_frames": _unique_join([row.get("hit_frames") for row in direct_configs], limit=20),
                "family_config_ids": _unique_join([row.get("skill_id_text") for row in family_configs], limit=30),
                "family_config_descriptions": _unique_join([row.get("description") for row in family_configs], limit=30),
            }
        )

    family_rows = _build_feature_family_rows(
        lingjie_rows,
        feature_rows,
        configs_by_family6,
        items_by_id=items_by_id,
        items_by_effect_value=items_by_effect_value,
    )
    feature_fields = [
        "feature",
        "source_gid",
        "source_row_count",
        "source_jie",
        "source_name",
        "source_describe",
        "direct_match_count",
        "family_match_count",
        "match_kind",
        "config_ids",
        "config_descriptions",
        "timelines",
        "effect_paths",
        "sound_ids",
        "hit_frames",
        "family_config_ids",
        "family_config_descriptions",
    ]
    family_fields = [
        "source_gid",
        "status",
        "feature_prefixes",
        "feature_count",
        "features",
        "source_row_count",
        "source_jie",
        "source_names",
        "source_describe",
        "direct_match_feature_count",
        "family_match_feature_count",
        "unmatched_feature_count",
        "candidate_count",
        "candidate_ids",
        "candidate_descriptions",
        "candidate_timelines",
        "candidate_effect_paths",
        "candidate_sound_ids",
        "consume_item_ids",
        "consume_item_names",
        "consume_item_icons",
        "linked_item_ids",
        "linked_item_names",
        "linked_item_icons",
        "linked_item_qualities",
    ]
    config_fields = [
        "skill_id",
        "source_file",
        "feature_prefix8",
        "family_prefix6",
        "type",
        "description",
        "track_time",
        "timeline",
        "track_count",
        "clip_count",
        "track_names",
        "clip_types",
        "effect_paths",
        "sound_ids",
        "hit_frames",
        "source_path",
    ]

    json_path = out_dir / "gongfa_feature_probe.json"
    features_tsv_path = out_dir / "feature_links.tsv"
    families_tsv_path = out_dir / "feature_families.tsv"
    configs_tsv_path = out_dir / "luaconfig_candidates.tsv"
    report_path = out_dir / "feature_probe_report.md"
    stats = {
        "feature_count": len(feature_rows),
        "feature_family_count": len(family_rows),
        "direct_match_feature_count": sum(1 for row in feature_rows if row["direct_match_count"]),
        "family_match_feature_count": sum(1 for row in feature_rows if row["family_match_count"]),
        "no_luaconfig_feature_count": sum(1 for row in feature_rows if not row["family_match_count"]),
        "no_luaconfig_family_count": sum(1 for row in family_rows if row["status"] == "no_luaconfig_match"),
        "luaconfig_candidate_count": len(config_rows),
        "item_row_count": len(item_rows),
        "linked_item_family_count": sum(1 for row in family_rows if row.get("linked_item_ids")),
        "source_lingjie_row_count": len(lingjie_rows),
    }
    json_path.write_text(
        json.dumps(
            {
                "lingjie_rows_path": str(resolved_lingjie_rows_path),
                "config_dir": str(resolved_config_dir),
                "item_rows_path": str(resolved_item_rows_path) if resolved_item_rows_path else "",
                "stats": stats,
                "families": family_rows,
                "features": feature_rows,
                "luaconfig_candidates": config_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_tsv(features_tsv_path, feature_rows, feature_fields)
    _write_tsv(families_tsv_path, family_rows, family_fields)
    _write_tsv(configs_tsv_path, config_rows, config_fields)
    report_path.write_text(
        "\n".join(
            [
                "# 凡修功法 feature 静态探查",
                "",
                f"- Lingjie-GongfaJie 行：{len(lingjie_rows)}",
                f"- feature family 数：{stats['feature_family_count']}",
                f"- feature 数：{stats['feature_count']}",
                f"- 直接命中 luaconfig feature：{stats['direct_match_feature_count']}",
                f"- 同家族命中 luaconfig feature：{stats['family_match_feature_count']}",
                f"- 未命中 luaconfig feature：{stats['no_luaconfig_feature_count']}",
                f"- 未命中 luaconfig family：{stats['no_luaconfig_family_count']}",
                f"- luaconfig 候选行：{stats['luaconfig_candidate_count']}",
                f"- 道具表行：{stats['item_row_count']}",
                f"- 已关联道具的 family：{stats['linked_item_family_count']}",
                "",
                "## 输出文件",
                "",
                f"- feature_links.tsv: {features_tsv_path}",
                f"- feature_families.tsv: {families_tsv_path}",
                f"- luaconfig_candidates.tsv: {configs_tsv_path}",
            ]
        ),
        encoding="utf-8",
    )

    return {
        "output_dir": str(out_dir),
        "lingjie_rows_path": str(resolved_lingjie_rows_path),
        "config_dir": str(resolved_config_dir),
        "item_rows_path": str(resolved_item_rows_path) if resolved_item_rows_path else "",
        "stats": stats,
        "files": {
            "probe_json": str(json_path),
            "feature_links_tsv": str(features_tsv_path),
            "feature_families_tsv": str(families_tsv_path),
            "luaconfig_candidates_tsv": str(configs_tsv_path),
            "report": str(report_path),
        },
    }


def _index_special_feature_sources(
    special_rows: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    rows_by_feature: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows_by_gid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in special_rows:
        gid = _as_text(row.get("gid")).strip()
        feature = _as_text(row.get("feature")).strip()
        if gid:
            rows_by_gid[gid].append(row)
        if feature:
            rows_by_feature[feature].append(row)
    return rows_by_feature, rows_by_gid


def _index_special_skill_tables(
    skill_rows: list[dict[str, Any]],
    star_rows: list[dict[str, Any]],
    upgrade_rows: list[dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    indexes: dict[str, dict[str, list[dict[str, Any]]]] = {
        "skill_exact": defaultdict(list),
        "skill_family6": defaultdict(list),
        "same_gongfa_skill": defaultdict(list),
        "star_exact": defaultdict(list),
        "star_family6": defaultdict(list),
        "same_gongfa_star": defaultdict(list),
        "upgrade_exact": defaultdict(list),
        "upgrade_family6": defaultdict(list),
        "same_gongfa_upgrade": defaultdict(list),
    }

    def add(index_name: str, value: Any, row: dict[str, Any]) -> None:
        text = _base_skill_id(value)
        if not text:
            return
        indexes[index_name][text].append(row)
        if len(text) >= 6:
            indexes[index_name.replace("_exact", "_family6")][text[:6]].append(row)

    for row in skill_rows:
        row["_source_table"] = "GongfaSkill"
        add("skill_exact", row.get("id") or row.get("_row_key"), row)
        add("skill_exact", row.get("group"), row)
        if origin_id := _as_text(row.get("originId")).strip():
            indexes["same_gongfa_skill"][origin_id].append(row)
    for row in star_rows:
        row["_source_table"] = "GongfaStar"
        add("star_exact", row.get("skill"), row)
        add("star_exact", row.get("showSkillGet"), row)
        if gid := _as_text(row.get("gid")).strip():
            indexes["same_gongfa_star"][gid].append(row)
    for row in upgrade_rows:
        row["_source_table"] = "GongfaUpgrade"
        add("upgrade_exact", row.get("skill"), row)
        add("upgrade_exact", row.get("showSkillGet"), row)
        if gid := _as_text(row.get("gid")).strip():
            indexes["same_gongfa_upgrade"][gid].append(row)
    return indexes


def _index_faze_rows(
    faze_rows: list[dict[str, Any]],
    source_table: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    exact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_prefix6: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in faze_rows:
        row["_source_table"] = source_table
        faze_id = _as_text(row.get("id") or row.get("_row_key")).strip()
        if not faze_id:
            continue
        exact[faze_id].append(row)
        if len(faze_id) >= 6:
            by_prefix6[faze_id[:6]].append(row)
    return exact, by_prefix6


def _faze_matches(feature: str, exact: dict[str, list[dict[str, Any]]], by_prefix6: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    matched = list(exact.get(feature, []))
    if len(feature) >= 6:
        for row in by_prefix6.get(feature[:6], []):
            faze_id = _as_text(row.get("id") or row.get("_row_key")).strip()
            if len(faze_id) >= 5 and feature.startswith(faze_id):
                matched.append(row)
    return _dedupe_rows(matched)


def _compact_special_skill_match(row: dict[str, Any]) -> str:
    source = _as_text(row.get("_source_table")).strip()
    row_id = _row_key(row)
    name = row.get("skillName_plain") or row.get("name_plain") or row.get("name")
    gid = row.get("gid") or row.get("originId")
    parts = [source, row_id]
    if name:
        parts.append(_preview(name, 40))
    if gid:
        parts.append(f"gid={gid}")
    return ":".join(parts)


def _compact_faze_match(row: dict[str, Any]) -> str:
    source = _as_text(row.get("_source_table")).strip() or "Faze"
    return ":".join(
        [
            source,
            _row_key(row),
            f"type={_as_text(row.get('type'))}",
            _preview(row.get("params") or row.get("tipStr_plain") or row.get("tipStr"), 80),
        ]
    ).rstrip(":")


def _status_for_special_feature(
    *,
    skill_exact: list[dict[str, Any]],
    star_exact: list[dict[str, Any]],
    upgrade_exact: list[dict[str, Any]],
    skill_family: list[dict[str, Any]],
    star_family: list[dict[str, Any]],
    upgrade_family: list[dict[str, Any]],
    same_gongfa_skill: list[dict[str, Any]],
    same_gongfa_star: list[dict[str, Any]],
    same_gongfa_upgrade: list[dict[str, Any]],
    faze_rows: list[dict[str, Any]],
    faze_resource_rows: list[dict[str, Any]],
    direct_configs: list[dict[str, Any]],
    family_configs: list[dict[str, Any]],
) -> str:
    if skill_exact:
        return "gongfa_skill_exact"
    if star_exact or upgrade_exact:
        return "star_upgrade_exact"
    if skill_family or star_family or upgrade_family:
        return "skill_family6"
    if faze_rows:
        return "faze_effect"
    if faze_resource_rows:
        return "faze_resource"
    if direct_configs:
        return "direct_luaconfig"
    if family_configs:
        return "family_luaconfig"
    if same_gongfa_skill or same_gongfa_star or same_gongfa_upgrade:
        return "same_gongfa_skill"
    return "text_only"


def _strongest_status(statuses: list[str]) -> str:
    priority = {
        "gongfa_skill_exact": 0,
        "star_upgrade_exact": 1,
        "skill_family6": 2,
        "faze_effect": 3,
        "faze_resource": 4,
        "direct_luaconfig": 5,
        "family_luaconfig": 6,
        "same_gongfa_skill": 7,
        "text_only": 8,
    }
    return min(statuses or ["text_only"], key=lambda item: priority.get(item, 999))


def build_fanxiu_special_gongfa_feature_probe(
    *,
    special_rows_path: str | Path | None = None,
    gongfa_rows_path: str | Path | None = None,
    skill_rows_path: str | Path | None = None,
    star_rows_path: str | Path | None = None,
    upgrade_rows_path: str | Path | None = None,
    faze_effect_rows_path: str | Path | None = None,
    faze_resource_rows_path: str | Path | None = None,
    item_rows_path: str | Path | None = None,
    config_dir: str | Path | None = None,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_export_root(export_root)
    resolved_special_rows_path = _resolve_export_file(
        special_rows_path,
        DEFAULT_SPECIAL_GONGFA_JIE_ROWS,
        export_root=export_root,
    )
    resolved_gongfa_rows_path = _resolve_optional_export_file(
        gongfa_rows_path,
        DEFAULT_GONGFA_ROWS,
        export_root=export_root,
    )
    resolved_skill_rows_path = _resolve_optional_export_file(
        skill_rows_path,
        DEFAULT_GONGFA_SKILL_ROWS,
        export_root=export_root,
    )
    resolved_star_rows_path = _resolve_optional_export_file(
        star_rows_path,
        DEFAULT_GONGFA_STAR_ROWS,
        export_root=export_root,
    )
    resolved_upgrade_rows_path = _resolve_optional_export_file(
        upgrade_rows_path,
        DEFAULT_GONGFA_UPGRADE_ROWS,
        export_root=export_root,
    )
    resolved_faze_rows_path = _resolve_optional_export_file(
        faze_effect_rows_path,
        DEFAULT_FAZE_EFFECT_RESOURCE_ROWS,
        export_root=export_root,
    )
    resolved_faze_resource_rows_path = _resolve_optional_export_file(
        faze_resource_rows_path,
        DEFAULT_FAZE_RESOURCE_ROWS,
        export_root=export_root,
    )
    resolved_item_rows_path = _resolve_optional_export_file(
        item_rows_path,
        DEFAULT_ITEM_ROWS,
        export_root=export_root,
    )
    resolved_config_dir = _resolve_export_dir(config_dir, export_root=export_root)

    out_dir = root / "parsed_configs" / "special_gongfa_feature_probe"
    out_dir.mkdir(parents=True, exist_ok=True)

    special_rows = _load_json_rows(resolved_special_rows_path)
    gongfa_rows = _load_json_rows(resolved_gongfa_rows_path) if resolved_gongfa_rows_path else []
    skill_rows = _load_json_rows(resolved_skill_rows_path) if resolved_skill_rows_path else []
    star_rows = _load_json_rows(resolved_star_rows_path) if resolved_star_rows_path else []
    upgrade_rows = _load_json_rows(resolved_upgrade_rows_path) if resolved_upgrade_rows_path else []
    faze_rows = _load_json_rows(resolved_faze_rows_path) if resolved_faze_rows_path else []
    faze_resource_rows = _load_json_rows(resolved_faze_resource_rows_path) if resolved_faze_resource_rows_path else []
    item_rows = _load_json_rows(resolved_item_rows_path) if resolved_item_rows_path else []

    rows_by_feature, rows_by_gid = _index_special_feature_sources(special_rows)
    gongfa_by_id = _collect_rows_by_text_key(gongfa_rows, "id")
    items_by_id = _collect_rows_by_text_key(item_rows, "id")
    items_by_effect_value = _collect_rows_by_text_key(item_rows, "effectValue")
    table_indexes = _index_special_skill_tables(skill_rows, star_rows, upgrade_rows)
    faze_exact, faze_by_prefix6 = _index_faze_rows(faze_rows, "FazeEffectResource")
    faze_resource_exact, faze_resource_by_prefix6 = _index_faze_rows(faze_resource_rows, "FazeResource")

    feature_values = sorted(rows_by_feature)
    config_rows = _parse_luaconfig_candidates(resolved_config_dir, feature_values)
    configs_by_prefix8: dict[str, list[dict[str, Any]]] = defaultdict(list)
    configs_by_family6: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in config_rows:
        configs_by_prefix8[_as_text(row.get("feature_prefix8"))].append(row)
        configs_by_family6[_as_text(row.get("family_prefix6"))].append(row)

    feature_rows: list[dict[str, Any]] = []
    feature_row_by_feature: dict[str, dict[str, Any]] = {}
    for feature, source_rows in sorted(rows_by_feature.items(), key=lambda item: _feature_sort_key(item[0])):
        prefix6 = feature[:6]
        skill_exact = _dedupe_rows(table_indexes["skill_exact"].get(feature, []))
        star_exact = _dedupe_rows(table_indexes["star_exact"].get(feature, []))
        upgrade_exact = _dedupe_rows(table_indexes["upgrade_exact"].get(feature, []))
        skill_family = _dedupe_rows(table_indexes["skill_family6"].get(prefix6, [])) if len(feature) >= 6 else []
        star_family = _dedupe_rows(table_indexes["star_family6"].get(prefix6, [])) if len(feature) >= 6 else []
        upgrade_family = _dedupe_rows(table_indexes["upgrade_family6"].get(prefix6, [])) if len(feature) >= 6 else []
        faze_matched = _faze_matches(feature, faze_exact, faze_by_prefix6)
        faze_resource_matched = _faze_matches(feature, faze_resource_exact, faze_resource_by_prefix6)
        direct_configs = configs_by_prefix8.get(feature, [])
        family_configs = configs_by_family6.get(prefix6, []) if len(feature) >= 6 else []
        preview_source = next((row for row in source_rows if row.get("describe") or row.get("describe_plain")), source_rows[0])
        source_gids = [_as_text(row.get("gid")).strip() for row in source_rows if _as_text(row.get("gid")).strip()]
        source_gongfa = [row for gid in source_gids for row in gongfa_by_id.get(gid, [])]
        same_gongfa_skill = _dedupe_rows([row for gid in source_gids for row in table_indexes["same_gongfa_skill"].get(gid, [])])
        same_gongfa_star = _dedupe_rows([row for gid in source_gids for row in table_indexes["same_gongfa_star"].get(gid, [])])
        same_gongfa_upgrade = _dedupe_rows([row for gid in source_gids for row in table_indexes["same_gongfa_upgrade"].get(gid, [])])
        item_summary = _collect_item_rows_for_family(
            source_gids[0] if source_gids else "",
            source_rows,
            items_by_id,
            items_by_effect_value,
        )
        status = _status_for_special_feature(
            skill_exact=skill_exact,
            star_exact=star_exact,
            upgrade_exact=upgrade_exact,
            skill_family=skill_family,
            star_family=star_family,
            upgrade_family=upgrade_family,
            same_gongfa_skill=same_gongfa_skill,
            same_gongfa_star=same_gongfa_star,
            same_gongfa_upgrade=same_gongfa_upgrade,
            faze_rows=faze_matched,
            faze_resource_rows=faze_resource_matched,
            direct_configs=direct_configs,
            family_configs=family_configs,
        )
        row = {
            "feature": feature,
            "status": status,
            "source_gid": _unique_join(source_gids, limit=30),
            "source_gongfa_names": _unique_join([row.get("name_plain") or row.get("name") for row in source_gongfa], limit=30),
            "source_row_count": len(source_rows),
            "source_jie": _unique_join([row.get("jie") for row in source_rows], limit=30),
            "source_name": _unique_join([row.get("name_plain") or row.get("name") for row in source_rows], limit=30),
            "source_describe": _preview(preview_source.get("describe") or preview_source.get("describe_plain"), 260),
            "skill_exact_count": len(skill_exact),
            "skill_exact": _unique_join([_compact_special_skill_match(row) for row in skill_exact], limit=30),
            "star_exact_count": len(star_exact),
            "star_exact": _unique_join([_compact_special_skill_match(row) for row in star_exact], limit=30),
            "upgrade_exact_count": len(upgrade_exact),
            "upgrade_exact": _unique_join([_compact_special_skill_match(row) for row in upgrade_exact], limit=30),
            "skill_family_count": len(skill_family),
            "skill_family": _unique_join([_compact_special_skill_match(row) for row in skill_family], limit=40),
            "star_family_count": len(star_family),
            "star_family": _unique_join([_compact_special_skill_match(row) for row in star_family], limit=40),
            "upgrade_family_count": len(upgrade_family),
            "upgrade_family": _unique_join([_compact_special_skill_match(row) for row in upgrade_family], limit=40),
            "same_gongfa_skill_count": len(same_gongfa_skill),
            "same_gongfa_skill": _unique_join([_compact_special_skill_match(row) for row in same_gongfa_skill], limit=40),
            "same_gongfa_star_count": len(same_gongfa_star),
            "same_gongfa_star": _unique_join([_compact_special_skill_match(row) for row in same_gongfa_star], limit=40),
            "same_gongfa_upgrade_count": len(same_gongfa_upgrade),
            "same_gongfa_upgrade": _unique_join([_compact_special_skill_match(row) for row in same_gongfa_upgrade], limit=40),
            "faze_match_count": len(faze_matched),
            "faze_matches": _unique_join([_compact_faze_match(row) for row in faze_matched], limit=40),
            "faze_resource_match_count": len(faze_resource_matched),
            "faze_resource_matches": _unique_join(
                [_compact_faze_match(row) for row in faze_resource_matched],
                limit=40,
            ),
            "direct_luaconfig_count": len(direct_configs),
            "family_luaconfig_count": len(family_configs),
            "luaconfig_ids": _unique_join([row.get("skill_id_text") for row in direct_configs], limit=30),
            "luaconfig_descriptions": _unique_join([row.get("description") for row in direct_configs], limit=30),
            "family_luaconfig_ids": _unique_join([row.get("skill_id_text") for row in family_configs], limit=40),
            "family_luaconfig_descriptions": _unique_join([row.get("description") for row in family_configs], limit=40),
            **item_summary,
        }
        feature_rows.append(row)
        feature_row_by_feature[feature] = row

    family_rows: list[dict[str, Any]] = []
    for gid, source_rows in sorted(rows_by_gid.items(), key=lambda item: _feature_sort_key(item[0])):
        features = sorted(
            {_as_text(row.get("feature")).strip() for row in source_rows if _as_text(row.get("feature")).strip()},
            key=_feature_sort_key,
        )
        linked_feature_rows = [feature_row_by_feature[feature] for feature in features if feature in feature_row_by_feature]
        if not linked_feature_rows:
            continue
        gongfa_rows_for_gid = gongfa_by_id.get(gid, [])
        item_summary = _collect_item_rows_for_family(gid, source_rows, items_by_id, items_by_effect_value)
        preview_source = next((row for row in source_rows if row.get("describe") or row.get("describe_plain")), source_rows[0])
        family_rows.append(
            {
                "source_gid": gid,
                "gongfa_names": _unique_join([row.get("name_plain") or row.get("name") for row in gongfa_rows_for_gid], limit=20),
                "status": _strongest_status([row["status"] for row in linked_feature_rows]),
                "feature_count": len(features),
                "features": _unique_join(features, limit=80),
                "source_row_count": len(source_rows),
                "source_jie": _unique_join([row.get("jie") for row in source_rows], limit=80),
                "source_describe": _preview(preview_source.get("describe") or preview_source.get("describe_plain"), 320),
                "skill_exact_feature_count": sum(1 for row in linked_feature_rows if row["skill_exact_count"]),
                "star_exact_feature_count": sum(1 for row in linked_feature_rows if row["star_exact_count"]),
                "upgrade_exact_feature_count": sum(1 for row in linked_feature_rows if row["upgrade_exact_count"]),
                "skill_family_feature_count": sum(1 for row in linked_feature_rows if row["skill_family_count"]),
                "same_gongfa_skill_feature_count": sum(
                    1 for row in linked_feature_rows if row["status"] == "same_gongfa_skill"
                ),
                "faze_feature_count": sum(1 for row in linked_feature_rows if row["faze_match_count"]),
                "faze_resource_feature_count": sum(
                    1 for row in linked_feature_rows if row["faze_resource_match_count"]
                ),
                "luaconfig_feature_count": sum(
                    1
                    for row in linked_feature_rows
                    if row["direct_luaconfig_count"] or row["family_luaconfig_count"]
                ),
                "text_only_feature_count": sum(1 for row in linked_feature_rows if row["status"] == "text_only"),
                **item_summary,
            }
        )

    feature_fields = [
        "feature",
        "status",
        "source_gid",
        "source_gongfa_names",
        "source_row_count",
        "source_jie",
        "source_name",
        "source_describe",
        "skill_exact_count",
        "skill_exact",
        "star_exact_count",
        "star_exact",
        "upgrade_exact_count",
        "upgrade_exact",
        "skill_family_count",
        "skill_family",
        "star_family_count",
        "star_family",
        "upgrade_family_count",
        "upgrade_family",
        "same_gongfa_skill_count",
        "same_gongfa_skill",
        "same_gongfa_star_count",
        "same_gongfa_star",
        "same_gongfa_upgrade_count",
        "same_gongfa_upgrade",
        "faze_match_count",
        "faze_matches",
        "faze_resource_match_count",
        "faze_resource_matches",
        "direct_luaconfig_count",
        "family_luaconfig_count",
        "luaconfig_ids",
        "luaconfig_descriptions",
        "family_luaconfig_ids",
        "family_luaconfig_descriptions",
        "consume_item_ids",
        "consume_item_names",
        "consume_item_icons",
        "linked_item_ids",
        "linked_item_names",
        "linked_item_icons",
        "linked_item_qualities",
    ]
    family_fields = [
        "source_gid",
        "gongfa_names",
        "status",
        "feature_count",
        "features",
        "source_row_count",
        "source_jie",
        "source_describe",
        "skill_exact_feature_count",
        "star_exact_feature_count",
        "upgrade_exact_feature_count",
        "skill_family_feature_count",
        "same_gongfa_skill_feature_count",
        "faze_feature_count",
        "faze_resource_feature_count",
        "luaconfig_feature_count",
        "text_only_feature_count",
        "consume_item_ids",
        "consume_item_names",
        "consume_item_icons",
        "linked_item_ids",
        "linked_item_names",
        "linked_item_icons",
        "linked_item_qualities",
    ]
    config_fields = [
        "skill_id",
        "source_file",
        "feature_prefix8",
        "family_prefix6",
        "type",
        "description",
        "track_time",
        "timeline",
        "track_count",
        "clip_count",
        "track_names",
        "clip_types",
        "effect_paths",
        "sound_ids",
        "hit_frames",
        "source_path",
    ]

    json_path = out_dir / "special_gongfa_feature_probe.json"
    features_tsv_path = out_dir / "special_feature_links.tsv"
    families_tsv_path = out_dir / "special_feature_families.tsv"
    configs_tsv_path = out_dir / "special_luaconfig_candidates.tsv"
    report_path = out_dir / "special_feature_probe_report.md"
    status_counts: dict[str, int] = defaultdict(int)
    for row in feature_rows:
        status_counts[_as_text(row.get("status"))] += 1
    stats = {
        "source_special_row_count": len(special_rows),
        "feature_count": len(feature_rows),
        "feature_family_count": len(family_rows),
        "gongfa_row_count": len(gongfa_rows),
        "skill_row_count": len(skill_rows),
        "star_row_count": len(star_rows),
        "upgrade_row_count": len(upgrade_rows),
        "faze_effect_row_count": len(faze_rows),
        "faze_resource_row_count": len(faze_resource_rows),
        "item_row_count": len(item_rows),
        "luaconfig_candidate_count": len(config_rows),
        "status_counts": dict(sorted(status_counts.items())),
        "skill_exact_feature_count": sum(1 for row in feature_rows if row["skill_exact_count"]),
        "star_exact_feature_count": sum(1 for row in feature_rows if row["star_exact_count"]),
        "upgrade_exact_feature_count": sum(1 for row in feature_rows if row["upgrade_exact_count"]),
        "skill_family_feature_count": sum(1 for row in feature_rows if row["skill_family_count"]),
        "same_gongfa_skill_feature_count": sum(
            1 for row in feature_rows if row["status"] == "same_gongfa_skill"
        ),
        "faze_feature_count": sum(1 for row in feature_rows if row["faze_match_count"]),
        "faze_resource_feature_count": sum(1 for row in feature_rows if row["faze_resource_match_count"]),
        "linked_item_family_count": sum(1 for row in family_rows if row.get("linked_item_ids")),
    }
    json_path.write_text(
        json.dumps(
            {
                "special_rows_path": str(resolved_special_rows_path),
                "gongfa_rows_path": str(resolved_gongfa_rows_path) if resolved_gongfa_rows_path else "",
                "skill_rows_path": str(resolved_skill_rows_path) if resolved_skill_rows_path else "",
                "star_rows_path": str(resolved_star_rows_path) if resolved_star_rows_path else "",
                "upgrade_rows_path": str(resolved_upgrade_rows_path) if resolved_upgrade_rows_path else "",
                "faze_effect_rows_path": str(resolved_faze_rows_path) if resolved_faze_rows_path else "",
                "faze_resource_rows_path": str(resolved_faze_resource_rows_path)
                if resolved_faze_resource_rows_path
                else "",
                "item_rows_path": str(resolved_item_rows_path) if resolved_item_rows_path else "",
                "config_dir": str(resolved_config_dir),
                "stats": stats,
                "families": family_rows,
                "features": feature_rows,
                "luaconfig_candidates": config_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_tsv(features_tsv_path, feature_rows, feature_fields)
    _write_tsv(families_tsv_path, family_rows, family_fields)
    _write_tsv(configs_tsv_path, config_rows, config_fields)
    report_path.write_text(
        "\n".join(
            [
                "# Special-GongfaJie feature 静态反查",
                "",
                f"- Special-GongfaJie 行：{len(special_rows)}",
                f"- feature family 数：{stats['feature_family_count']}",
                f"- feature 数：{stats['feature_count']}",
                f"- GongfaSkill 精确命中 feature：{stats['skill_exact_feature_count']}",
                f"- GongfaStar 精确命中 feature：{stats['star_exact_feature_count']}",
                f"- GongfaUpgrade 精确命中 feature：{stats['upgrade_exact_feature_count']}",
                f"- 技能家族前缀命中 feature：{stats['skill_family_feature_count']}",
                f"- 同功法技能兜底命中 feature：{stats['same_gongfa_skill_feature_count']}",
                f"- FazeEffectResource 命中 feature：{stats['faze_feature_count']}",
                f"- FazeResource 命中 feature：{stats['faze_resource_feature_count']}",
                f"- luaconfig 候选行：{stats['luaconfig_candidate_count']}",
                f"- 已关联道具的 family：{stats['linked_item_family_count']}",
                "",
                "## 状态分布",
                "",
                *[f"- {key}: {value}" for key, value in stats["status_counts"].items()],
                "",
                "## 输出文件",
                "",
                f"- special_feature_links.tsv: {features_tsv_path}",
                f"- special_feature_families.tsv: {families_tsv_path}",
                f"- special_luaconfig_candidates.tsv: {configs_tsv_path}",
            ]
        ),
        encoding="utf-8",
    )

    return {
        "output_dir": str(out_dir),
        "special_rows_path": str(resolved_special_rows_path),
        "config_dir": str(resolved_config_dir),
        "stats": stats,
        "files": {
            "probe_json": str(json_path),
            "feature_links_tsv": str(features_tsv_path),
            "feature_families_tsv": str(families_tsv_path),
            "luaconfig_candidates_tsv": str(configs_tsv_path),
            "report": str(report_path),
        },
    }
