from __future__ import annotations

import json
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from backend.core.settings import get_settings


_INVENTORY_FILENAME = "fanxiu_inventory.json"
_WARDROBE_HALL_KEY = "wardrobe_hall"
_WARDROBE_SECTION_KEYS = ("shizhuang", "wuqi", "huanshen", "beishi", "yuqi")
_SPIRIT_BEAST_HALL_KEY = "spirit_beast_hall"
_SPIRIT_BEAST_SECTION_KEYS = ("lingshou", "shengshou")
_MAGIC_TREASURE_HALL_KEY = "magic_treasure_hall"
_MAGIC_TREASURE_SECTION_KEYS = ("fabao", "xiantiangubao", "houtiangubao")
_SPIRIT_ARTIFACT_HALL_KEY = "spirit_artifact_hall"
_ACTIVITY_LIST_KEY = "activity_list"
_ACTIVITY_CROSS_COUNT_OPTIONS = {0, 1, 2, 4, 8, 16, 32, 64}
_ACTIVITY_CROSS_SUFFIX_RE = re.compile(r"^(?P<name>.*?)\s*(?<!\d)(?P<cross>64|32|16|8|4|2|1|0)\s*跨$")
_INVENTORY_TYPE_OPTIONS = {"攻击", "防御", "灵力", "辅助"}
_INVENTORY_STORAGE_VERSION = 2
_LEGACY_QUALITY_SHIFT_START = 7
_MAX_QUALITY_INDEX = 17
_INVENTORY_HALL_SECTION_KEYS = {
    _WARDROBE_HALL_KEY: _WARDROBE_SECTION_KEYS,
    _SPIRIT_BEAST_HALL_KEY: _SPIRIT_BEAST_SECTION_KEYS,
    _MAGIC_TREASURE_HALL_KEY: _MAGIC_TREASURE_SECTION_KEYS,
}
_SPIRIT_ARTIFACT_SEEDS = (
    ("血晶摩诃剑", ("柄", "刃", "穗", "鞘", "珠", "纹")),
    ("天月落星幡", ("镜", "幅", "带", "杆", "印", "纹")),
    ("弥罗宝光幢", ("焰", "柱", "环", "座", "珠", "纹")),
    ("鸿古干天戈", ("锋", "芒", "珠", "坠", "柄", "气")),
    ("青暝岁月灯", ("盏", "芯", "穗", "杆", "纹", "荧")),
    ("苍烟神火炉", ("饰", "盖", "身", "柄", "光", "座")),
    ("御海镇神图", ("卷", "瑚", "海", "轴", "灵", "山")),
    ("六界轮回盘", ("珠", "盘", "焰", "环", "荧", "晶")),
)
_SPIRIT_ARTIFACT_NAME_ALIASES = {
    "青冥岁月灯": "青暝岁月灯",
}
_SPIRIT_ARTIFACT_STAT_KEYS = ("chaos_power", "attack", "spirit_power", "health", "defense")
_SPIRIT_ARTIFACT_MARKET_DEFAULT_COST = 80
_SPIRIT_ARTIFACT_EXCLUSIVE_STAT_KEYS = {
    "血晶摩诃剑": ("暴击附伤", "暴击"),
    "天月落星幡": ("功法附伤", "招架", "神通吸血"),
    "弥罗宝光幢": ("法宝附伤", "炼体附伤", "闪避"),
    "鸿古干天戈": ("灵兽附伤", "仙语附伤", "全技能减伤"),
    "青暝岁月灯": ("灵宝抵御", "功法抵御", "全技能减伤"),
    "苍烟神火炉": ("招架", "灵兽附伤", "法宝附伤"),
    "御海镇神图": ("仙语附伤", "灵暴附伤", "灵暴"),
    "六界轮回盘": ("神识全技能增伤", "神识暴击", "神识暴击附伤", "神识最终增伤"),
}
_SPIRIT_ARTIFACT_PEERLESS_STEPS = {0, 25, 30}


def get_inventory_storage_path() -> Path:
    return get_settings().data_dir / _INVENTORY_FILENAME


def load_wardrobe_hall() -> dict[str, list[dict[str, Any]]]:
    storage = _read_inventory_storage()
    return _load_inventory_hall(storage, _WARDROBE_HALL_KEY, _WARDROBE_SECTION_KEYS)


def load_spirit_beast_hall() -> dict[str, list[dict[str, Any]]]:
    storage = _read_inventory_storage()
    return _load_inventory_hall(storage, _SPIRIT_BEAST_HALL_KEY, _SPIRIT_BEAST_SECTION_KEYS)


def load_magic_treasure_hall() -> dict[str, list[dict[str, Any]]]:
    storage = _read_inventory_storage()
    return _load_inventory_hall(storage, _MAGIC_TREASURE_HALL_KEY, _MAGIC_TREASURE_SECTION_KEYS)


def load_spirit_artifact_hall() -> dict[str, Any]:
    storage = _read_inventory_storage()
    collections = storage.get("collections", {})
    if not isinstance(collections, dict):
        collections = {}
    return _normalize_spirit_artifact_hall(collections.get(_SPIRIT_ARTIFACT_HALL_KEY, {}))


def load_activity_list() -> list[dict[str, Any]]:
    storage = _read_inventory_storage()
    collections = storage.get("collections", {})
    if not isinstance(collections, dict):
        collections = {}
    activity_list = collections.get(_ACTIVITY_LIST_KEY, [])
    return _normalize_activity_list(activity_list)


def save_wardrobe_hall(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    storage_path = get_inventory_storage_path()
    storage_path.parent.mkdir(parents=True, exist_ok=True)

    storage = _read_inventory_storage()
    return _save_inventory_hall(storage, storage_path, _WARDROBE_HALL_KEY, _WARDROBE_SECTION_KEYS, payload)


def save_spirit_beast_hall(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    storage_path = get_inventory_storage_path()
    storage_path.parent.mkdir(parents=True, exist_ok=True)

    storage = _read_inventory_storage()
    return _save_inventory_hall(storage, storage_path, _SPIRIT_BEAST_HALL_KEY, _SPIRIT_BEAST_SECTION_KEYS, payload)


def save_magic_treasure_hall(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    storage_path = get_inventory_storage_path()
    storage_path.parent.mkdir(parents=True, exist_ok=True)

    storage = _read_inventory_storage()
    return _save_inventory_hall(storage, storage_path, _MAGIC_TREASURE_HALL_KEY, _MAGIC_TREASURE_SECTION_KEYS, payload)


def save_spirit_artifact_hall(payload: dict[str, Any]) -> dict[str, Any]:
    storage_path = get_inventory_storage_path()
    storage_path.parent.mkdir(parents=True, exist_ok=True)

    storage = _read_inventory_storage()
    collections = storage.get("collections", {})
    if not isinstance(collections, dict):
        collections = {}

    normalized = _normalize_spirit_artifact_hall(payload)
    collections[_SPIRIT_ARTIFACT_HALL_KEY] = normalized
    storage["version"] = _storage_version(storage)
    storage["collections"] = collections

    storage_path.write_text(
        json.dumps(storage, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized


def save_activity_list(payload: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    storage_path = get_inventory_storage_path()
    storage_path.parent.mkdir(parents=True, exist_ok=True)

    storage = _read_inventory_storage()
    collections = storage.get("collections", {})
    if not isinstance(collections, dict):
        collections = {}

    normalized = _normalize_activity_list(payload)
    collections[_ACTIVITY_LIST_KEY] = normalized
    storage["version"] = _storage_version(storage)
    storage["collections"] = collections

    storage_path.write_text(
        json.dumps(storage, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized


def migrate_inventory_note_ids(note_id_map: dict[str, str]) -> int:
    normalized_map = {
        str(old_ref).strip(): str(new_ref).strip()
        for old_ref, new_ref in note_id_map.items()
        if str(old_ref).strip() and str(new_ref).strip()
    }
    if not normalized_map:
        return 0

    storage_path = get_inventory_storage_path()
    if not storage_path.exists():
        return 0

    storage = _read_inventory_storage()
    updated_count = _replace_note_id_refs(storage, normalized_map)
    if updated_count <= 0:
        return 0

    storage_path.write_text(
        json.dumps(storage, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return updated_count


def _replace_note_id_refs(value: Any, note_id_map: dict[str, str]) -> int:
    if isinstance(value, list):
        return sum(_replace_note_id_refs(item, note_id_map) for item in value)
    if not isinstance(value, dict):
        return 0

    updated_count = 0
    raw_note_id = str(value.get("note_id") or "").strip()
    next_note_id = note_id_map.get(raw_note_id)
    if next_note_id and next_note_id != raw_note_id:
        value["note_id"] = next_note_id
        updated_count += 1

    for child in value.values():
        if isinstance(child, (dict, list)):
            updated_count += _replace_note_id_refs(child, note_id_map)
    return updated_count


def _default_inventory_hall(section_keys: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    return {key: [] for key in section_keys}


def _read_inventory_storage() -> dict[str, Any]:
    storage_path = get_inventory_storage_path()
    if not storage_path.exists():
        return {"version": _INVENTORY_STORAGE_VERSION, "warehouses": {}, "collections": {}}

    try:
        payload = json.loads(storage_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"读取凡修道具仓库失败：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"凡修道具仓库不是有效 JSON：{storage_path}") from exc

    if not isinstance(payload, dict):
        raise ValueError("凡修道具仓库根节点不是对象。")
    return payload


def _normalize_wardrobe_hall(raw_payload: Any) -> dict[str, list[dict[str, Any]]]:
    return _normalize_inventory_hall(raw_payload, _WARDROBE_SECTION_KEYS)


def _normalize_spirit_beast_hall(raw_payload: Any) -> dict[str, list[dict[str, Any]]]:
    return _normalize_inventory_hall(raw_payload, _SPIRIT_BEAST_SECTION_KEYS)


def _normalize_magic_treasure_hall(raw_payload: Any) -> dict[str, list[dict[str, Any]]]:
    return _normalize_inventory_hall(raw_payload, _MAGIC_TREASURE_SECTION_KEYS)


def _default_spirit_artifact_hall() -> dict[str, Any]:
    return {
        "artifacts": [
            {
                "order": artifact_index + 1,
                "name": artifact_name,
                "rows": [
                    {
                        "order": part_index + 1,
                        "part_name": part_name,
                        "rank": 0,
                        "realm": 0,
                        "artifact_peerless_1": 0,
                        "artifact_peerless_2": 0,
                        **{key: "" for key in _SPIRIT_ARTIFACT_STAT_KEYS},
                        "stat_raw_values": {
                            key: ""
                            for key in _SPIRIT_ARTIFACT_STAT_KEYS
                        },
                        "exclusive_stats": {
                            key: ""
                            for key in _SPIRIT_ARTIFACT_EXCLUSIVE_STAT_KEYS.get(artifact_name, ())
                        },
                        "exclusive_stat_raw_values": {
                            key: ""
                            for key in _SPIRIT_ARTIFACT_EXCLUSIVE_STAT_KEYS.get(artifact_name, ())
                        },
                    }
                    for part_index, part_name in enumerate(part_names)
                ],
            }
            for artifact_index, (artifact_name, part_names) in enumerate(_SPIRIT_ARTIFACT_SEEDS)
        ],
        "market_currency_count": 0,
        "market_items": [],
        "storage_bag_items": [],
    }


def _normalize_spirit_artifact_hall(raw_payload: Any) -> dict[str, Any]:
    default_hall = _default_spirit_artifact_hall()
    if not isinstance(raw_payload, dict):
        return default_hall

    raw_artifacts = raw_payload.get("artifacts", [])
    if not isinstance(raw_artifacts, list):
        raw_artifacts = []
    raw_by_name: dict[str, dict[str, Any]] = {}
    for item in raw_artifacts:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name") or "").strip()
        if not raw_name:
            continue
        canonical_name = _SPIRIT_ARTIFACT_NAME_ALIASES.get(raw_name, raw_name)
        if canonical_name == raw_name:
            raw_by_name[canonical_name] = item
        else:
            raw_by_name.setdefault(canonical_name, item)

    normalized_artifacts: list[dict[str, Any]] = []
    for artifact_index, (artifact_name, part_names) in enumerate(_SPIRIT_ARTIFACT_SEEDS):
        raw_artifact = raw_by_name.get(artifact_name, {})
        raw_rows = raw_artifact.get("rows", []) if isinstance(raw_artifact, dict) else []
        if not isinstance(raw_rows, list):
            raw_rows = []
        raw_rows_by_part = {
            str(item.get("part_name") or item.get("partName") or "").strip(): item
            for item in raw_rows
            if isinstance(item, dict) and str(item.get("part_name") or item.get("partName") or "").strip()
        }
        rows = [
            _normalize_spirit_artifact_row(
                raw_rows_by_part.get(part_name, {}),
                part_name,
                part_index + 1,
                _SPIRIT_ARTIFACT_EXCLUSIVE_STAT_KEYS.get(artifact_name, ()),
            )
            for part_index, part_name in enumerate(part_names)
        ]
        normalized_artifacts.append(
            {
                "order": artifact_index + 1,
                "name": artifact_name,
                "rows": rows,
            }
        )
    return {
        "artifacts": normalized_artifacts,
        "market_currency_count": _normalize_nonnegative_int(
            raw_payload.get("market_currency_count", raw_payload.get("marketCurrencyCount"))
        ),
        "market_items": _normalize_spirit_artifact_market_items(raw_payload.get("market_items", raw_payload.get("marketItems"))),
        "storage_bag_items": _normalize_spirit_artifact_storage_bag_items(
            raw_payload.get("storage_bag_items", raw_payload.get("storageBagItems"))
        ),
    }


def _normalize_spirit_artifact_market_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []

    seen: set[tuple[str, str]] = set()
    normalized_items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        raw_artifact_name = str(raw_item.get("artifact_name") or raw_item.get("artifactName") or "").strip()
        artifact_name = _SPIRIT_ARTIFACT_NAME_ALIASES.get(raw_artifact_name, raw_artifact_name)
        if artifact_name not in dict(_SPIRIT_ARTIFACT_SEEDS):
            continue
        part_name = str(raw_item.get("part_name") or raw_item.get("partName") or "").strip()
        if part_name not in dict(_SPIRIT_ARTIFACT_SEEDS)[artifact_name]:
            continue
        item_key = (artifact_name, part_name)
        if item_key in seen:
            continue
        seen.add(item_key)
        cost = _normalize_nonnegative_int(raw_item.get("cost"))
        normalized_items.append(
            {
                "order": len(normalized_items) + 1,
                "artifact_name": artifact_name,
                "part_name": part_name,
                "cost": cost or _SPIRIT_ARTIFACT_MARKET_DEFAULT_COST,
            }
        )
    return normalized_items


def _normalize_spirit_artifact_storage_bag_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []

    normalized_items: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        title = str(raw_item.get("title") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        choices = _normalize_spirit_artifact_storage_bag_choices(raw_item.get("choices"))
        if not choices:
            continue
        normalized_items.append(
            {
                "order": len(normalized_items) + 1,
                "title": title,
                "quantity": _normalize_nonnegative_int(raw_item.get("quantity")),
                "choices": choices,
            }
        )
    return normalized_items


def _normalize_spirit_artifact_storage_bag_choices(raw_choices: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_choices, list):
        return []

    part_names_by_artifact = dict(_SPIRIT_ARTIFACT_SEEDS)
    seen: set[tuple[str, str]] = set()
    normalized_choices: list[dict[str, Any]] = []
    for raw_choice in raw_choices:
        if not isinstance(raw_choice, dict):
            continue
        raw_name = str(raw_choice.get("raw_name") or raw_choice.get("rawName") or "").strip()
        raw_artifact_name = str(raw_choice.get("artifact_name") or raw_choice.get("artifactName") or "").strip()
        artifact_name = _SPIRIT_ARTIFACT_NAME_ALIASES.get(raw_artifact_name, raw_artifact_name)
        part_name = str(raw_choice.get("part_name") or raw_choice.get("partName") or "").strip()
        if artifact_name not in part_names_by_artifact or part_name not in part_names_by_artifact.get(artifact_name, ()):
            continue
        choice_key = (artifact_name, part_name) if artifact_name and part_name else ("", raw_name)
        if choice_key in seen:
            continue
        seen.add(choice_key)
        normalized_choices.append(
            {
                "order": len(normalized_choices) + 1,
                "raw_name": raw_name,
                "artifact_name": artifact_name,
                "part_name": part_name,
            }
        )
    return normalized_choices


def _normalize_spirit_artifact_row(
    raw_row: Any,
    part_name: str,
    order: int,
    exclusive_stat_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    if not isinstance(raw_row, dict):
        raw_row = {}
    artifact_peerless_1 = _normalize_nonnegative_int(
        raw_row.get(
            "artifact_peerless_1",
            raw_row.get("artifactPeerless1", raw_row.get("aura_peerless", raw_row.get("auraPeerless"))),
        )
    )
    artifact_peerless_2 = _normalize_nonnegative_int(
        raw_row.get("artifact_peerless_2", raw_row.get("artifactPeerless2"))
    )
    if artifact_peerless_1 not in _SPIRIT_ARTIFACT_PEERLESS_STEPS:
        artifact_peerless_1 = 0
    if artifact_peerless_2 not in _SPIRIT_ARTIFACT_PEERLESS_STEPS:
        artifact_peerless_2 = 0
    normalized = {
        "order": order,
        "part_name": part_name,
        "rank": _normalize_nonnegative_int(raw_row.get("rank")),
        "realm": _normalize_nonnegative_int(raw_row.get("realm")),
        "artifact_peerless_1": artifact_peerless_1,
        "artifact_peerless_2": artifact_peerless_2,
    }
    for key in _SPIRIT_ARTIFACT_STAT_KEYS:
        camel_key = "".join([key.split("_")[0], *[part.title() for part in key.split("_")[1:]]])
        normalized[key] = str(raw_row.get(key, raw_row.get(camel_key, "")) or "").strip()
    raw_stat_raw_values = raw_row.get("stat_raw_values", raw_row.get("statRawValues"))
    if not isinstance(raw_stat_raw_values, dict):
        raw_stat_raw_values = {}
    normalized["stat_raw_values"] = {}
    for key in _SPIRIT_ARTIFACT_STAT_KEYS:
        camel_key = "".join([key.split("_")[0], *[part.title() for part in key.split("_")[1:]]])
        normalized["stat_raw_values"][key] = str(raw_stat_raw_values.get(key, raw_stat_raw_values.get(camel_key, "")) or "").strip()
    raw_exclusive_stats = raw_row.get("exclusive_stats", raw_row.get("exclusiveStats"))
    if not isinstance(raw_exclusive_stats, dict):
        raw_exclusive_stats = {}
    normalized["exclusive_stats"] = {
        key: str(raw_exclusive_stats.get(key, "") or "").strip()
        for key in exclusive_stat_keys
    }
    raw_exclusive_stat_raw_values = raw_row.get(
        "exclusive_stat_raw_values",
        raw_row.get("exclusiveStatRawValues"),
    )
    if not isinstance(raw_exclusive_stat_raw_values, dict):
        raw_exclusive_stat_raw_values = {}
    normalized["exclusive_stat_raw_values"] = {
        key: str(raw_exclusive_stat_raw_values.get(key, "") or "").strip()
        for key in exclusive_stat_keys
    }
    return normalized


def _storage_version(storage: dict[str, Any]) -> int:
    try:
        version = int(storage.get("version"))
    except (TypeError, ValueError, AttributeError):
        return 1
    return max(version, 1)


def _load_inventory_hall(
    storage: dict[str, Any],
    hall_key: str,
    section_keys: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    warehouses = storage.get("warehouses", {})
    if not isinstance(warehouses, dict):
        warehouses = {}
    inventory_hall = warehouses.get(hall_key, {})
    if not isinstance(inventory_hall, dict):
        inventory_hall = {}
    return _normalize_inventory_hall(
        inventory_hall,
        section_keys,
        legacy_quality_schema=_storage_version(storage) < _INVENTORY_STORAGE_VERSION,
    )


def _save_inventory_hall(
    storage: dict[str, Any],
    storage_path: Path,
    hall_key: str,
    section_keys: tuple[str, ...],
    payload: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    warehouses = storage.get("warehouses", {})
    if not isinstance(warehouses, dict):
        warehouses = {}

    if _storage_version(storage) < _INVENTORY_STORAGE_VERSION:
        warehouses = _migrate_inventory_warehouses(warehouses)

    normalized = _normalize_inventory_hall(payload, section_keys)
    warehouses[hall_key] = normalized
    storage["version"] = _INVENTORY_STORAGE_VERSION
    storage["warehouses"] = warehouses

    storage_path.write_text(
        json.dumps(storage, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized


def _normalize_inventory_hall(
    raw_payload: Any,
    section_keys: tuple[str, ...],
    *,
    legacy_quality_schema: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    normalized = _default_inventory_hall(section_keys)
    if not isinstance(raw_payload, dict):
        return normalized

    for section_key in section_keys:
        raw_items = raw_payload.get(section_key, [])
        if not isinstance(raw_items, list):
            continue
        normalized[section_key] = [
            normalized_item
            for item in raw_items
            if (
                normalized_item := _normalize_inventory_item(
                    item,
                    legacy_quality_schema=legacy_quality_schema,
                )
            )
            is not None
        ]
    return normalized


def _normalize_activity_list(raw_payload: Any) -> list[dict[str, Any]]:
    raw_items = raw_payload.get("items", []) if isinstance(raw_payload, dict) else raw_payload
    if not isinstance(raw_items, list):
        return []

    normalized_items = [
        normalized_item
        for item in raw_items
        if (normalized_item := _normalize_activity_item(item)) is not None
    ]
    return sorted(normalized_items, key=_activity_sort_key)


def _normalize_inventory_item(
    raw_item: Any,
    *,
    legacy_quality_schema: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(raw_item, dict):
        return None

    item_id = str(raw_item.get("id") or "").strip() or str(uuid.uuid4())
    name = str(raw_item.get("name") or "").strip()
    note_id = str(raw_item.get("note_id") or "").strip() or None
    rank = _normalize_rank(raw_item.get("rank"))
    shenlian = _normalize_shenlian(raw_item.get("shenlian"))
    item_type = _normalize_inventory_type(raw_item.get("type"))
    quality = _normalize_quality(raw_item.get("quality"), legacy_quality_schema=legacy_quality_schema)
    main_use = str(raw_item.get("main_use") or "").strip()
    acquisition = str(raw_item.get("acquisition") or "").strip()
    item_date = _normalize_date(raw_item.get("date"))

    normalized_item = {
        "id": item_id,
        "name": name,
        "rank": rank,
        "shenlian": shenlian,
        "date": item_date.isoformat(),
    }
    if item_type:
        normalized_item["type"] = item_type
    if quality is not None:
        normalized_item["quality"] = quality
    if main_use:
        normalized_item["main_use"] = main_use
    if acquisition:
        normalized_item["acquisition"] = acquisition
    if note_id:
        normalized_item["note_id"] = note_id
    return normalized_item


def _normalize_activity_item(raw_item: Any) -> dict[str, Any] | None:
    if not isinstance(raw_item, dict):
        return None

    item_id = str(raw_item.get("id") or "").strip() or str(uuid.uuid4())
    raw_name = str(raw_item.get("name") or "").strip()
    name, extracted_cross_count = _split_activity_name_cross_count(raw_name)
    cross_count = extracted_cross_count
    if cross_count is None:
        cross_count = _normalize_activity_cross_count(
            raw_item.get("cross_count", raw_item.get("crossCount", raw_item.get("cross"))),
        )
    note_id = str(raw_item.get("note_id") or "").strip() or None
    start_date = _normalize_date(raw_item.get("start_date"))
    end_date = _normalize_date(raw_item.get("end_date"), fallback=start_date)

    normalized_item = {
        "id": item_id,
        "name": name,
        "cross_count": cross_count,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    if note_id:
        normalized_item["note_id"] = note_id
    return normalized_item


def _split_activity_name_cross_count(value: Any) -> tuple[str, int | None]:
    text = str(value or "").strip()
    match = _ACTIVITY_CROSS_SUFFIX_RE.match(text)
    if not match:
        return text, None
    cross_count = _normalize_activity_cross_count(match.group("cross"))
    name = match.group("name").strip()
    return name, cross_count


def _normalize_activity_cross_count(value: Any) -> int:
    if value is None:
        return 0
    text = str(value).strip()
    if text.endswith("跨"):
        text = text[:-1].strip()
    try:
        numeric = int(text)
    except (TypeError, ValueError):
        return 0
    return numeric if numeric in _ACTIVITY_CROSS_COUNT_OPTIONS else 0


def _activity_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    start_date = _normalize_date(item.get("start_date")).toordinal()
    end_date = _normalize_date(item.get("end_date")).toordinal()
    item_id = str(item.get("id") or "")
    return (-start_date, -end_date, item_id)


def _normalize_rank(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_optional_rank(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _normalize_nonnegative_int(value: Any) -> int:
    normalized = _normalize_optional_rank(value)
    if normalized is None:
        return 0
    return max(normalized, 0)


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _normalize_inventory_type(value: Any) -> str:
    normalized = str(value or "").strip()
    return normalized if normalized in _INVENTORY_TYPE_OPTIONS else ""


def _normalize_quality(value: Any, *, legacy_quality_schema: bool = False) -> int | None:
    normalized = _normalize_optional_rank(value)
    if normalized is None:
        return None
    if legacy_quality_schema and normalized >= _LEGACY_QUALITY_SHIFT_START:
        normalized += 1
    return max(0, min(normalized, _MAX_QUALITY_INDEX))


def _migrate_inventory_warehouses(raw_warehouses: dict[str, Any]) -> dict[str, Any]:
    migrated: dict[str, Any] = {}
    for hall_key, section_keys in _INVENTORY_HALL_SECTION_KEYS.items():
        raw_hall = raw_warehouses.get(hall_key, {})
        if not isinstance(raw_hall, dict):
            raw_hall = {}
        migrated[hall_key] = _normalize_inventory_hall(
            raw_hall,
            section_keys,
            legacy_quality_schema=True,
        )
    return migrated


def _normalize_shenlian(value: Any) -> int:
    normalized = _normalize_optional_rank(value)
    if normalized is None:
        return 0
    return max(0, normalized)


def _normalize_date(value: Any, fallback: date | None = None) -> date:
    text = str(value or "").strip()
    if not text:
        return fallback or date.today()

    try:
        return date.fromisoformat(text)
    except ValueError:
        return fallback or date.today()

