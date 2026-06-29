import re

NOTE_KIND_DEFAULT = "note"
NOTE_KIND_FANXIU_CHAR = "fanxiu_char"
NOTE_KIND_FANXIU_WARDROBE_ITEM = "fanxiu_wardrobe_item"
NOTE_KIND_FANXIU_ACTIVITY_ITEM = "fanxiu_activity_item"
NOTE_KIND_FANXIU_SPIRIT_BEAST_ITEM = "fanxiu_spirit_beast_item"
NOTE_KIND_FANXIU_MAGIC_TREASURE_ITEM = "fanxiu_magic_treasure_item"

NOTE_WEIGHT_MODE_EXPONENTIAL = "exponential"
NOTE_WEIGHT_MODE_LINEAR = "linear"

NOTE_TYPE_DEFAULT = "note"
NOTE_CATEGORY_DEFAULT = "general"
NOTE_TYPE_WEIGHT_DEFAULT = 100
NOTE_TYPE_WEIGHT_MIN = 0
NOTE_TYPE_WEIGHT_MAX = 100
NOTE_TYPE_BUILTIN_KEYS = ("project", "module", "task", "bug", "note", "doc", "memo")
NOTE_CATEGORY_BUILTIN_KEYS = ("general", "project", "module", "task", "bug")
NOTE_CATEGORY_PALETTE_SETTING_KEY_PREFIX = "note.category_palette.user"
NOTE_TYPE_PALETTE_SETTING_KEY_PREFIX = "note.type_palette.user"
NOTE_TYPE_LEGACY_COLOR_PREFIX = "legacy_color_"
NOTE_AUTO_CLASSIFICATION_BLOCKED_CATEGORY_KEYS = frozenset({"project", "module", "task"})
NOTE_AUTO_CLASSIFICATION_BLOCKED_CATEGORY_LABELS = frozenset({"项目", "模块", "任务", "重点"})
NOTE_FORM_DEFAULT = "note"
NOTE_FORM_DOCUMENT = "document"
NOTE_FORM_MEMO = "memo"
NOTE_FORM_MUSIC = "music"
NOTE_FORM_VIDEO = "video"
NOTE_FORM_GAME = "game"
NOTE_FORM_BOOK = "book"
NOTE_LIFECYCLE_STAGE_DEFAULT = "idea"
NOTE_SCENE_DEFAULT = NOTE_KIND_DEFAULT
NOTE_TYPE_BUILTIN_PALETTE = (
    {"key": "general", "label": "综合", "color": "#606266", "order": 0},
    {"key": "project", "label": "项目", "color": "#7B1FA2", "order": 10},
    {"key": "module", "label": "模块", "color": "#BA68C8", "order": 20},
    {"key": "task", "label": "任务", "color": "#409EFF", "order": 30},
    {"key": "bug", "label": "缺陷", "color": "#F56C6C", "order": 40},
)

HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
LEGACY_FORM_TYPE_TO_NOTE_FORM = {
    "note": NOTE_FORM_DEFAULT,
    "doc": NOTE_FORM_DOCUMENT,
    "memo": NOTE_FORM_MEMO,
}


def normalize_note_color(value) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not HEX_COLOR_RE.match(trimmed):
        return None
    if len(trimmed) == 4:
        return f"#{trimmed[1] * 2}{trimmed[2] * 2}{trimmed[3] * 2}".upper()
    return trimmed.upper()


def build_note_type_palette_setting_key(user_id: int) -> str:
    return f"{NOTE_TYPE_PALETTE_SETTING_KEY_PREFIX}.{int(user_id)}"


def build_note_category_palette_setting_key(user_id: int) -> str:
    return f"{NOTE_CATEGORY_PALETTE_SETTING_KEY_PREFIX}.{int(user_id)}"


def build_legacy_color_type_key(color) -> str | None:
    normalized = normalize_note_color(color)
    if not normalized:
        return None
    return f"{NOTE_TYPE_LEGACY_COLOR_PREFIX}{normalized[1:].lower()}"


def is_legacy_color_type_key(key) -> bool:
    return isinstance(key, str) and key.startswith(NOTE_TYPE_LEGACY_COLOR_PREFIX)


def get_legacy_color_from_type_key(key) -> str | None:
    if not is_legacy_color_type_key(key):
        return None
    suffix = str(key)[len(NOTE_TYPE_LEGACY_COLOR_PREFIX):].strip()
    if len(suffix) != 6 or any(char not in "0123456789abcdefABCDEF" for char in suffix):
        return None
    return f"#{suffix.upper()}"


def is_note_auto_classification_blocked_category(key, label=None) -> bool:
    normalized_key = str(key or "").strip()
    normalized_label = str(label or "").strip()
    return (
        normalized_key in NOTE_AUTO_CLASSIFICATION_BLOCKED_CATEGORY_KEYS
        or normalized_label in NOTE_AUTO_CLASSIFICATION_BLOCKED_CATEGORY_LABELS
    )


def normalize_note_type_weight(value, default: int = NOTE_TYPE_WEIGHT_DEFAULT) -> int:
    try:
        weight = int(value)
    except (TypeError, ValueError):
        weight = default
    return max(NOTE_TYPE_WEIGHT_MIN, min(NOTE_TYPE_WEIGHT_MAX, weight))


def normalize_note_form(value, default: str = NOTE_FORM_DEFAULT) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {
        NOTE_FORM_DEFAULT,
        NOTE_FORM_DOCUMENT,
        NOTE_FORM_MEMO,
        NOTE_FORM_MUSIC,
        NOTE_FORM_VIDEO,
        NOTE_FORM_GAME,
        NOTE_FORM_BOOK,
    }:
        return normalized
    return default


def normalize_lifecycle_stage(value, default: str = NOTE_LIFECYCLE_STAGE_DEFAULT) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "predone":
        normalized = "done"
    return normalized or default


def normalize_note_scene(value, default: str = NOTE_SCENE_DEFAULT) -> str:
    normalized = str(value or "").strip()
    return normalized or default


def normalize_note_types(value, fallback_type: str | None = NOTE_TYPE_DEFAULT) -> list[dict[str, int | str]]:
    items = value if isinstance(value, list) else []
    normalized: list[dict[str, int | str]] = []
    seen: dict[str, int] = {}

    for item in items:
        key = None
        weight = NOTE_TYPE_WEIGHT_DEFAULT

        if isinstance(item, dict):
            raw_key = item.get("key")
            if isinstance(raw_key, str):
                key = raw_key.strip()
            weight = normalize_note_type_weight(item.get("weight"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            raw_key = item[0]
            if isinstance(raw_key, str):
                key = raw_key.strip()
            weight = normalize_note_type_weight(item[1])

        if not key:
            continue

        if key in seen:
            normalized[seen[key]] = {"key": key, "weight": weight}
            continue

        seen[key] = len(normalized)
        normalized.append({"key": key, "weight": weight})

    if normalized:
        return normalized

    fallback = (fallback_type or NOTE_TYPE_DEFAULT or "").strip()
    if not fallback:
        return []
    return [{"key": fallback, "weight": NOTE_TYPE_WEIGHT_DEFAULT}]


def normalize_note_categories(
    value,
    fallback_category: str | None = NOTE_CATEGORY_DEFAULT,
) -> list[dict[str, int | str]]:
    items = value if isinstance(value, list) else []
    normalized: list[dict[str, int | str]] = []
    seen: dict[str, int] = {}

    for item in items:
        key = None
        weight = NOTE_TYPE_WEIGHT_DEFAULT

        if isinstance(item, dict):
            raw_key = item.get("key")
            if isinstance(raw_key, str):
                key = raw_key.strip()
            weight = normalize_note_type_weight(item.get("weight"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            raw_key = item[0]
            if isinstance(raw_key, str):
                key = raw_key.strip()
            weight = normalize_note_type_weight(item[1])

        if not key:
            continue
        if key in LEGACY_FORM_TYPE_TO_NOTE_FORM:
            key = NOTE_CATEGORY_DEFAULT

        if key in seen:
            existing_index = seen[key]
            previous_weight = normalize_note_type_weight(normalized[existing_index].get("weight"))
            normalized[existing_index] = {
                "key": key,
                "weight": normalize_note_type_weight(previous_weight + weight),
            }
            continue

        seen[key] = len(normalized)
        normalized.append({"key": key, "weight": weight})

    if normalized:
        return normalized

    if fallback_category is None:
        return []
    fallback = (fallback_category or NOTE_CATEGORY_DEFAULT or "").strip()
    if not fallback:
        return []
    return [{"key": fallback, "weight": NOTE_TYPE_WEIGHT_DEFAULT}]


def get_legacy_general_type_key(note_form: str | None = NOTE_FORM_DEFAULT) -> str:
    normalized_form = normalize_note_form(note_form)
    if normalized_form == NOTE_FORM_DOCUMENT:
        return "doc"
    if normalized_form == NOTE_FORM_MEMO:
        return "memo"
    return NOTE_TYPE_DEFAULT


def derive_primary_node_type(note_types, fallback_type: str | None = NOTE_TYPE_DEFAULT) -> str:
    normalized = normalize_note_types(note_types, fallback_type=fallback_type)
    if not normalized:
        return (fallback_type or NOTE_TYPE_DEFAULT or "").strip() or NOTE_TYPE_DEFAULT

    best_index = 0
    best_weight = normalize_note_type_weight(normalized[0].get("weight"))
    for index, item in enumerate(normalized[1:], start=1):
        weight = normalize_note_type_weight(item.get("weight"))
        if weight > best_weight:
            best_index = index
            best_weight = weight

    key = normalized[best_index].get("key")
    return str(key).strip() or ((fallback_type or NOTE_TYPE_DEFAULT or "").strip() or NOTE_TYPE_DEFAULT)


def derive_primary_category(note_categories, fallback_category: str | None = NOTE_CATEGORY_DEFAULT) -> str | None:
    normalized = normalize_note_categories(note_categories, fallback_category=fallback_category)
    if not normalized:
        if fallback_category is None:
            return None
        return (fallback_category or NOTE_CATEGORY_DEFAULT or "").strip() or NOTE_CATEGORY_DEFAULT

    best_index = 0
    best_weight = normalize_note_type_weight(normalized[0].get("weight"))
    for index, item in enumerate(normalized[1:], start=1):
        weight = normalize_note_type_weight(item.get("weight"))
        if weight > best_weight:
            best_index = index
            best_weight = weight

    key = normalized[best_index].get("key")
    if key:
        return str(key).strip()
    if fallback_category is None:
        return None
    return (fallback_category or NOTE_CATEGORY_DEFAULT or "").strip() or NOTE_CATEGORY_DEFAULT


def derive_note_taxonomy_from_legacy(
    note_types,
    node_type: str | None = NOTE_TYPE_DEFAULT,
    note_kind: str | None = NOTE_KIND_DEFAULT,
    node_status: str | None = NOTE_LIFECYCLE_STAGE_DEFAULT,
) -> dict[str, str | None | list[dict[str, int | str]]]:
    normalized_note_types = normalize_note_types(note_types, fallback_type=node_type or NOTE_TYPE_DEFAULT)
    categories_seed: list[dict[str, int | str]] = []
    resolved_form = NOTE_FORM_DEFAULT
    best_form_weight = -1

    for item in normalized_note_types:
        key = str(item.get("key") or "").strip()
        weight = normalize_note_type_weight(item.get("weight"))
        mapped_form = LEGACY_FORM_TYPE_TO_NOTE_FORM.get(key)
        if mapped_form:
            categories_seed.append({"key": NOTE_CATEGORY_DEFAULT, "weight": weight})
            if weight > best_form_weight:
                resolved_form = mapped_form
                best_form_weight = weight
            continue
        categories_seed.append({"key": key, "weight": weight})

    note_categories = normalize_note_categories(categories_seed, fallback_category=NOTE_CATEGORY_DEFAULT)
    primary_category = derive_primary_category(note_categories, fallback_category=NOTE_CATEGORY_DEFAULT)
    return {
        "note_categories": note_categories,
        "primary_category": primary_category,
        "note_form": normalize_note_form(resolved_form),
        "lifecycle_stage": normalize_lifecycle_stage(node_status),
        "note_scene": normalize_note_scene(note_kind),
    }


def derive_legacy_semantics_from_taxonomy(
    note_categories,
    primary_category: str | None = NOTE_CATEGORY_DEFAULT,
    note_form: str | None = NOTE_FORM_DEFAULT,
    note_scene: str | None = NOTE_SCENE_DEFAULT,
    lifecycle_stage: str | None = NOTE_LIFECYCLE_STAGE_DEFAULT,
) -> dict[str, str | list[dict[str, int | str]]]:
    fallback_category = primary_category if primary_category is not None else None
    normalized_categories = normalize_note_categories(note_categories, fallback_category=fallback_category)
    normalized_primary_category = derive_primary_category(normalized_categories, fallback_category=fallback_category)

    legacy_note_types: list[dict[str, int | str]] = []
    general_legacy_key = get_legacy_general_type_key(note_form)
    for item in normalized_categories:
        key = str(item.get("key") or "").strip()
        weight = normalize_note_type_weight(item.get("weight"))
        legacy_key = general_legacy_key if key == NOTE_CATEGORY_DEFAULT else key
        legacy_note_types.append({"key": legacy_key, "weight": weight})

    legacy_primary_type = derive_primary_node_type(legacy_note_types, fallback_type=NOTE_TYPE_DEFAULT)
    return {
        "note_types": legacy_note_types,
        "node_type": legacy_primary_type,
        "note_kind": normalize_note_scene(note_scene),
        "node_status": normalize_lifecycle_stage(lifecycle_stage),
        "note_form": normalize_note_form(note_form),
        "note_categories": normalized_categories,
        "primary_category": normalized_primary_category,
        "note_scene": normalize_note_scene(note_scene),
        "lifecycle_stage": normalize_lifecycle_stage(lifecycle_stage),
    }


def merge_note_types(
    note_types,
    source_key: str,
    target_key: str,
    fallback_type: str | None = NOTE_TYPE_DEFAULT,
) -> tuple[list[dict[str, int | str]], bool]:
    normalized_source = str(source_key or "").strip()
    normalized_target = str(target_key or "").strip()
    normalized = normalize_note_types(note_types, fallback_type=fallback_type)
    if not normalized_source or not normalized_target or normalized_source == normalized_target:
        return normalized, False

    merged: list[dict[str, int | str]] = []
    source_weight = 0
    target_index: int | None = None
    changed = False

    for item in normalized:
        key = str(item.get("key") or "").strip()
        weight = normalize_note_type_weight(item.get("weight"))
        if key == normalized_source:
            source_weight += weight
            changed = True
            continue
        if key == normalized_target:
            target_index = len(merged)
        merged.append({"key": key, "weight": weight})

    if not changed:
        return normalized, False

    if target_index is None:
        merged.append({"key": normalized_target, "weight": normalize_note_type_weight(source_weight)})
    else:
        target_item = merged[target_index]
        target_weight = normalize_note_type_weight(target_item.get("weight"))
        merged[target_index] = {
            "key": normalized_target,
            "weight": normalize_note_type_weight(target_weight + source_weight),
        }

    return merged, True
