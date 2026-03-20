from typing import Any, List, Optional, Tuple
import re
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select, func, or_
from sqlalchemy.orm.attributes import flag_modified
from backend.db import get_session
from backend.models import AppSetting, NoteNode, NoteEdge, User
from backend.schemas import (
    NoteCreate,
    NoteRead,
    NoteUpdate,
    NoteCategoryPaletteResponse,
    NoteCategoryMergeRequest,
    NoteCategoryPaletteUpdateRequest,
    EdgeCreate,
    EdgeRead,
    NoteListRead,
    GraphData,
    NoteFilterRule,
    NoteQueryRequest,
    NoteQueryResponse,
    NoteProgramRequest,
    NoteProgramResponse,
    NoteBatchUpdateRequest,
    NoteBatchUpdateResponse,
)
from backend.core.auth import get_current_active_user
from backend.core.note_access import note_to_response_dict
from backend.core.note_semantics import (
    NOTE_CATEGORY_BUILTIN_KEYS,
    NOTE_CATEGORY_DEFAULT,
    NOTE_FORM_DEFAULT,
    NOTE_KIND_DEFAULT,
    NOTE_LIFECYCLE_STAGE_DEFAULT,
    NOTE_SCENE_DEFAULT,
    NOTE_TYPE_BUILTIN_PALETTE,
    NOTE_TYPE_DEFAULT,
    build_legacy_color_type_key,
    build_note_category_palette_setting_key,
    build_note_type_palette_setting_key,
    derive_legacy_semantics_from_taxonomy,
    derive_note_taxonomy_from_legacy,
    derive_primary_category,
    derive_primary_node_type,
    get_legacy_color_from_type_key,
    is_legacy_color_type_key,
    merge_note_types,
    normalize_lifecycle_stage,
    normalize_note_categories,
    normalize_note_color,
    normalize_note_form,
    normalize_note_scene,
    normalize_note_types,
)
from backend.core.note_walker import NoteGraphContext, NoteWalker
import time
import uuid

router = APIRouter()

ALLOWED_ORDER_FIELDS = {"updated_at", "created_at", "start_at", "weight", "title", "private_level"}


def _normalize_note_type_palette_item(value: Any, fallback_order: int = 0) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    raw_key = value.get("key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return None
    key = raw_key.strip()
    if key == "note":
        key = NOTE_CATEGORY_DEFAULT
    elif key in {"doc", "memo"}:
        return None

    label = value.get("label")
    label = str(label).strip() if isinstance(label, str) else ""
    if not label:
        if is_legacy_color_type_key(key):
            legacy_color = get_legacy_color_from_type_key(key)
            label = f"旧色{legacy_color[1:]}" if legacy_color else key
        else:
            builtin_entry = next((item for item in NOTE_TYPE_BUILTIN_PALETTE if item["key"] == key), None)
            label = builtin_entry["label"] if builtin_entry else key
    elif key == NOTE_CATEGORY_DEFAULT and label == "笔记":
        # Auto-correct the historical builtin label carried over from the old type system.
        label = "综合"

    color = normalize_note_color(value.get("color"))
    if color is None and is_legacy_color_type_key(key):
        color = get_legacy_color_from_type_key(key)
    if color is None:
        builtin_entry = next((item for item in NOTE_TYPE_BUILTIN_PALETTE if item["key"] == key), None)
        if builtin_entry:
            color = builtin_entry["color"]
    if color is None:
        return None

    try:
        order = int(value.get("order", fallback_order))
    except (TypeError, ValueError):
        order = fallback_order

    source = value.get("source")
    builtin = bool(value.get("builtin")) or key in NOTE_CATEGORY_BUILTIN_KEYS
    if builtin:
        source = "builtin"
    elif is_legacy_color_type_key(key):
        source = "legacy"
    elif source not in {"builtin", "custom", "legacy"}:
        source = "custom"

    generated_from_color = normalize_note_color(value.get("generated_from_color"))
    if source == "legacy":
        generated_from_color = generated_from_color or get_legacy_color_from_type_key(key)

    return {
        "key": key,
        "label": label,
        "color": color,
        "order": order,
        "builtin": builtin,
        "source": source,
        "generated_from_color": generated_from_color,
    }


def _normalize_note_type_palette_items(items: Any) -> list[dict[str, Any]]:
    values = items if isinstance(items, list) else []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(values):
        normalized_item = _normalize_note_type_palette_item(item, fallback_order=index * 10)
        if not normalized_item:
            continue
        key = normalized_item["key"]
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_item)
    return normalized


def _load_note_type_palette_items(user_id: int, session: Session) -> list[dict[str, Any]]:
    for setting_key in (
        build_note_category_palette_setting_key(user_id),
        build_note_type_palette_setting_key(user_id),
    ):
        row = session.get(AppSetting, setting_key)
        if not row or not isinstance(row.value, dict):
            continue
        normalized = _normalize_note_type_palette_items(row.value.get("items"))
        if normalized:
            return normalized
    return []


def _default_note_type_palette_items() -> list[dict[str, Any]]:
    return _normalize_note_type_palette_items([
        {**item, "builtin": True, "source": "builtin"}
        for item in NOTE_TYPE_BUILTIN_PALETTE
    ])


def _resolve_effective_note_types(note: NoteNode) -> list[dict[str, Any]]:
    return _resolve_effective_note_types_payload(note.note_types, note.node_type, note.color)


def _resolve_effective_note_types_payload(
    note_types_value: Any,
    node_type_value: Any,
    color_value: Any,
) -> list[dict[str, Any]]:
    fallback_type = str(node_type_value or NOTE_TYPE_DEFAULT).strip() or NOTE_TYPE_DEFAULT
    note_types = normalize_note_types(note_types_value, fallback_type=fallback_type)
    normalized_note_color = normalize_note_color(color_value)
    if normalized_note_color and len(note_types) == 1:
        only_type = note_types[0]
        if only_type.get("key") == fallback_type and int(only_type.get("weight", 0)) == 100:
            legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
            if legacy_color_type_key:
                return [{"key": legacy_color_type_key, "weight": 100}]
    return note_types


def _resolve_effective_note_categories_payload(
    note_categories_value: Any,
    primary_category_value: Any,
    note_types_value: Any,
    node_type_value: Any,
    note_kind_value: Any,
    node_status_value: Any,
    color_value: Any,
) -> list[dict[str, Any]]:
    fallback_category = str(primary_category_value or NOTE_CATEGORY_DEFAULT).strip() or NOTE_CATEGORY_DEFAULT
    note_categories = normalize_note_categories(note_categories_value, fallback_category=fallback_category)
    if note_categories_value:
        return note_categories
    taxonomy = derive_note_taxonomy_from_legacy(
        _resolve_effective_note_types_payload(note_types_value, node_type_value, color_value),
        node_type=str(node_type_value or NOTE_TYPE_DEFAULT).strip() or NOTE_TYPE_DEFAULT,
        note_kind=str(note_kind_value or NOTE_KIND_DEFAULT).strip() or NOTE_KIND_DEFAULT,
        node_status=str(node_status_value or NOTE_LIFECYCLE_STAGE_DEFAULT).strip() or NOTE_LIFECYCLE_STAGE_DEFAULT,
    )
    return normalize_note_categories(taxonomy["note_categories"], fallback_category=fallback_category)


def _find_in_use_note_type_keys(user_id: int, target_keys: set[str], session: Session) -> set[str]:
    if not target_keys:
        return set()

    unresolved = {key for key in target_keys if key}
    if not unresolved:
        return set()

    matched: set[str] = set()
    rows = session.exec(
        select(
            NoteNode.note_categories,
            NoteNode.primary_category,
            NoteNode.note_types,
            NoteNode.node_type,
            NoteNode.note_kind,
            NoteNode.node_status,
            NoteNode.color,
        ).where(NoteNode.user_id == user_id)
    ).all()
    for note_categories, primary_category, note_types, node_type, note_kind, node_status, color in rows:
        for item in _resolve_effective_note_categories_payload(
            note_categories,
            primary_category,
            note_types,
            node_type,
            note_kind,
            node_status,
            color,
        ):
            key = str(item.get("key") or "").strip()
            if key in unresolved:
                matched.add(key)
                unresolved.discard(key)
                if not unresolved:
                    return matched
    return matched


def _is_note_type_in_use(user_id: int, type_key: str, session: Session) -> bool:
    normalized_key = str(type_key or "").strip()
    if not normalized_key:
        return False
    return normalized_key in _find_in_use_note_type_keys(user_id, {normalized_key}, session)


def _collect_note_type_usage(user_id: int, session: Session) -> dict[str, float]:
    usage_hundredths: dict[str, int] = {}
    seen_keys: set[str] = set()
    rows = session.exec(
        select(
            NoteNode.note_categories,
            NoteNode.primary_category,
            NoteNode.note_types,
            NoteNode.node_type,
            NoteNode.note_kind,
            NoteNode.node_status,
            NoteNode.color,
        ).where(NoteNode.user_id == user_id)
    ).all()
    for note_categories, primary_category, note_types, node_type, note_kind, node_status, color in rows:
        seen_in_note: set[str] = set()
        for item in _resolve_effective_note_categories_payload(
            note_categories,
            primary_category,
            note_types,
            node_type,
            note_kind,
            node_status,
            color,
        ):
            key = str(item.get("key") or "").strip()
            if not key or key in seen_in_note:
                continue
            seen_in_note.add(key)
            seen_keys.add(key)
            try:
                weight = int(item.get("weight", 0))
            except (TypeError, ValueError):
                weight = 0
            weight = max(0, min(100, weight))
            usage_hundredths[key] = usage_hundredths.get(key, 0) + weight
    return {
        key: usage_hundredths.get(key, 0) / 100
        for key in seen_keys
    }


def _discover_used_note_type_items(usage: dict[str, float]) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    for index, key in enumerate(sorted(usage.keys())):
        if is_legacy_color_type_key(key):
            legacy_color = get_legacy_color_from_type_key(key)
            discovered.append({
                "key": key,
                "label": f"旧色{legacy_color[1:]}" if legacy_color else key,
                "color": legacy_color or "#606266",
                "order": 2000 + index,
                "builtin": False,
                "source": "legacy",
                "generated_from_color": legacy_color,
            })
            continue
        builtin_entry = next((item for item in NOTE_TYPE_BUILTIN_PALETTE if item["key"] == key), None)
        discovered.append({
            "key": key,
            "label": builtin_entry["label"] if builtin_entry else key,
            "color": builtin_entry["color"] if builtin_entry else "#606266",
            "order": builtin_entry["order"] if builtin_entry else 1000 + index,
            "builtin": bool(builtin_entry),
            "source": "builtin" if builtin_entry else "custom",
            "generated_from_color": None,
        })
    return discovered


def _discover_legacy_color_palette_items(user_id: int, session: Session) -> list[dict[str, Any]]:
    raw_colors = session.exec(
        select(NoteNode.color).where(
            NoteNode.user_id == user_id,
            NoteNode.color.is_not(None),
        )
    ).all()
    discovered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_color in enumerate(raw_colors):
        normalized_color = normalize_note_color(raw_color)
        if not normalized_color:
            continue
        key = build_legacy_color_type_key(normalized_color)
        if not key or key in seen:
            continue
        seen.add(key)
        discovered.append({
            "key": key,
            "label": f"旧色{normalized_color[1:]}",
            "color": normalized_color,
            "order": 2000 + index,
            "builtin": False,
            "source": "legacy",
            "generated_from_color": normalized_color,
        })
    return discovered


def _build_note_type_palette_response(user_id: int, session: Session) -> dict[str, list[dict[str, Any]]]:
    stored_items = _load_note_type_palette_items(user_id, session)
    base_items = stored_items or _default_note_type_palette_items()
    usage = _collect_note_type_usage(user_id, session)
    merged = {item["key"]: item for item in base_items}
    for item in _discover_used_note_type_items(usage):
        merged.setdefault(item["key"], item)
    for item in _discover_legacy_color_palette_items(user_id, session):
        merged.setdefault(item["key"], item)
    items = sorted(merged.values(), key=lambda item: (int(item.get("order", 0)), str(item.get("label", item["key"])).lower()))
    for item in items:
        item["usage_count"] = round(float(usage.get(item["key"], 0)), 2)
    return {"items": items}


def _ensure_note_type_labels_unique(items: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for item in items:
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        normalized = label.casefold()
        previous = seen.get(normalized)
        if previous is not None:
            duplicates.append(label)
            continue
        seen[normalized] = label
    if duplicates:
        duplicate_labels = ", ".join(sorted(set(duplicates)))
        raise HTTPException(status_code=400, detail=f"Category labels must be unique: {duplicate_labels}")


def _get_note_type_palette_keys(user_id: int, session: Session) -> set[str]:
    response = _build_note_type_palette_response(user_id, session)
    return {
        str(item.get("key") or "").strip()
        for item in response.get("items", [])
        if str(item.get("key") or "").strip()
    }


def _build_note_taxonomy_fields(
    note_types: Any,
    node_type: Any,
    note_kind: Any,
    node_status: Any,
) -> dict[str, Any]:
    taxonomy = derive_note_taxonomy_from_legacy(
        note_types,
        node_type=str(node_type or NOTE_TYPE_DEFAULT).strip() or NOTE_TYPE_DEFAULT,
        note_kind=str(note_kind or NOTE_KIND_DEFAULT).strip() or NOTE_KIND_DEFAULT,
        node_status=str(node_status or "").strip() or None,
    )
    return {
        "note_categories": taxonomy["note_categories"],
        "primary_category": taxonomy["primary_category"],
        "note_form": taxonomy["note_form"],
        "note_scene": taxonomy["note_scene"],
        "lifecycle_stage": taxonomy["lifecycle_stage"],
    }


def _build_legacy_fields_from_taxonomy(
    note_categories: Any,
    primary_category: Any,
    note_form: Any,
    note_scene: Any,
    lifecycle_stage: Any,
) -> dict[str, Any]:
    normalized_primary_category = str(primary_category or NOTE_CATEGORY_DEFAULT).strip() or NOTE_CATEGORY_DEFAULT
    normalized_note_form = normalize_note_form(note_form, default=NOTE_FORM_DEFAULT)
    normalized_note_scene = normalize_note_scene(note_scene, default=NOTE_SCENE_DEFAULT)
    normalized_lifecycle_stage = normalize_lifecycle_stage(lifecycle_stage, default=NOTE_LIFECYCLE_STAGE_DEFAULT)
    legacy = derive_legacy_semantics_from_taxonomy(
        note_categories,
        primary_category=normalized_primary_category,
        note_form=normalized_note_form,
        note_scene=normalized_note_scene,
        lifecycle_stage=normalized_lifecycle_stage,
    )
    return {
        "note_categories": legacy["note_categories"],
        "primary_category": legacy["primary_category"],
        "note_form": legacy["note_form"],
        "note_scene": legacy["note_scene"],
        "lifecycle_stage": legacy["lifecycle_stage"],
        "note_types": legacy["note_types"],
        "node_type": legacy["node_type"],
        "note_kind": legacy["note_kind"],
        "node_status": legacy["node_status"],
    }


def _serialize_note_list(note: NoteNode, current_user: User) -> dict[str, Any]:
    return note_to_response_dict(note, current_user)


def _serialize_note_read(
    note: NoteNode,
    current_user: User,
    **extra_fields: Any,
) -> dict[str, Any]:
    return note_to_response_dict(note, current_user, **extra_fields)


NOTE_HISTORY_FIELD_MAP = {
    "node_type": "n",
    "note_types": "nt",
    "note_kind": "nk",
    "node_status": "s",
    "title": "t",
    "weight": "w",
    "content": "c",
    "private_level": "p",
    "color": "cl",
    "weight_mode": "wm",
}
NOTE_HISTORY_NON_MERGEABLE_FIELDS = {"node_type", "note_types", "note_kind", "node_status", "weight_mode"}


def _prepare_note_update_data(db_note: NoteNode, raw_note_data: dict[str, Any]) -> dict[str, Any]:
    note_data = dict(raw_note_data)
    uses_new_taxonomy_input = bool({
        "note_categories",
        "primary_category",
        "note_form",
        "note_scene",
        "lifecycle_stage",
    } & set(note_data.keys()))

    if uses_new_taxonomy_input:
        effective_categories = note_data.get("note_categories", db_note.note_categories)
        effective_primary_category = note_data.get("primary_category", db_note.primary_category or NOTE_CATEGORY_DEFAULT)
        effective_note_form = note_data.get("note_form", db_note.note_form or NOTE_FORM_DEFAULT)
        effective_note_scene = note_data.get("note_scene", db_note.note_scene or db_note.note_kind or NOTE_SCENE_DEFAULT)
        effective_lifecycle_stage = note_data.get("lifecycle_stage", db_note.lifecycle_stage or db_note.node_status or NOTE_LIFECYCLE_STAGE_DEFAULT)
        note_data.update(_build_legacy_fields_from_taxonomy(
            effective_categories,
            effective_primary_category,
            effective_note_form,
            effective_note_scene,
            effective_lifecycle_stage,
        ))
    elif "note_types" in note_data:
        fallback_type = note_data.get("node_type") or db_note.node_type or NOTE_TYPE_DEFAULT
        normalized_note_types = normalize_note_types(note_data.get("note_types"), fallback_type=fallback_type)
        next_color = note_data.get("color", db_note.color)
        normalized_note_color = normalize_note_color(next_color)
        if normalized_note_color and (
            not note_data.get("note_types")
            or (
                len(normalized_note_types) == 1
                and normalized_note_types[0].get("key") == fallback_type
                and int(normalized_note_types[0].get("weight", 0)) == 100
            )
        ):
            legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
            if legacy_color_type_key:
                normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]
        note_data["note_types"] = normalized_note_types
        note_data["node_type"] = derive_primary_node_type(normalized_note_types, fallback_type=fallback_type)
    elif "node_type" in note_data:
        replacement_type = note_data.get("node_type") or NOTE_TYPE_DEFAULT
        normalized_note_types = normalize_note_types([], fallback_type=replacement_type)
        note_data["note_types"] = normalized_note_types
        note_data["node_type"] = derive_primary_node_type(normalized_note_types, fallback_type=replacement_type)

    if "color" in note_data:
        note_data["color"] = normalize_note_color(note_data.get("color"))
    elif db_note.color:
        normalized_note_color = normalize_note_color(db_note.color)
        fallback_type = db_note.node_type or NOTE_TYPE_DEFAULT
        existing_note_types = normalize_note_types(db_note.note_types, fallback_type=fallback_type)
        if normalized_note_color and len(existing_note_types) == 1:
            only_type = existing_note_types[0]
            if only_type.get("key") == fallback_type and int(only_type.get("weight", 0)) == 100:
                legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
                if legacy_color_type_key:
                    note_data["note_types"] = [{"key": legacy_color_type_key, "weight": 100}]
                    note_data["node_type"] = legacy_color_type_key

    if not uses_new_taxonomy_input:
        effective_note_types = note_data.get("note_types", db_note.note_types)
        effective_node_type = note_data.get("node_type", db_note.node_type)
        effective_note_kind = note_data.get("note_kind", db_note.note_kind)
        effective_node_status = note_data.get("node_status", db_note.node_status)
        note_data.update(_build_note_taxonomy_fields(
            effective_note_types,
            effective_node_type,
            effective_note_kind,
            effective_node_status,
        ))

    return note_data


def _append_note_history(db_note: NoteNode, note_data: dict[str, Any], now_ts: int) -> bool:
    if db_note.history is None:
        db_note.history = []

    touched_history = False
    one_hour = 3600

    for field, new_val in note_data.items():
        if field not in NOTE_HISTORY_FIELD_MAP:
            continue

        old_val = getattr(db_note, field)
        if old_val == new_val:
            continue

        f_code = NOTE_HISTORY_FIELD_MAP[field]
        last_entry = None
        for entry in reversed(db_note.history):
            if entry.get("f") == f_code:
                last_entry = entry
                break

        is_mergeable = False
        if last_entry and (now_ts - last_entry["ts"]) < one_hour and field not in NOTE_HISTORY_NON_MERGEABLE_FIELDS:
            is_mergeable = True

        if is_mergeable:
            if field == "content":
                old_len = len(old_val) if old_val else 0
                new_len = len(new_val) if new_val else 0
                diff = new_len - old_len
                try:
                    current_delta = int(last_entry["v"].replace("+", ""))
                except Exception:
                    current_delta = 0
                last_entry["v"] = f"{current_delta + diff:+d}"
            else:
                last_entry["v"] = new_val
            last_entry["ts"] = now_ts
        else:
            value_to_log = new_val
            if field == "content":
                old_len = len(old_val) if old_val else 0
                new_len = len(new_val) if new_val else 0
                value_to_log = f"{new_len - old_len:+d}"
            db_note.history.append({"ts": now_ts, "f": f_code, "v": value_to_log})

        touched_history = True

    if touched_history:
        flag_modified(db_note, "history")

    return touched_history


def _get_accessible_note(note_id: str, current_user: User, session: Session) -> NoteNode | None:
    query = select(NoteNode).where(NoteNode.id == note_id)
    if not current_user.is_superuser:
        query = query.where(NoteNode.user_id == current_user.id)
    return session.exec(query).first()


def _get_filtered_edge_pool(
    seed_note_id: str,
    mode: str,
    user_id: int,
    session: Session
) -> List[NoteEdge]:
    edges = session.exec(select(NoteEdge).where(NoteEdge.user_id == user_id)).all()
    if mode == "satellite":
        edges = [edge for edge in edges if edge.target_id != seed_note_id]
    return edges


def _get_component_note_ids(
    seed_note_id: str,
    mode: str,
    user_id: int,
    session: Session
) -> Tuple[set[str], List[NoteEdge]]:
    start_note = session.exec(
        select(NoteNode).where(NoteNode.id == seed_note_id, NoteNode.user_id == user_id)
    ).first()
    if not start_note:
        raise HTTPException(status_code=404, detail="Note not found")

    edges = _get_filtered_edge_pool(seed_note_id, mode, user_id, session)
    adj: dict[str, list[str]] = {}
    for edge in edges:
        u, v = edge.source_id, edge.target_id
        adj.setdefault(u, []).append(v)
        adj.setdefault(v, []).append(u)

    visited = {seed_note_id}
    queue = [seed_note_id]
    while queue:
        curr = queue.pop(0)
        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

    return visited, edges


def _get_rule_value(note: NoteNode, field: str):
    if field.startswith("custom_fields."):
        key = field.split(".", 1)[1]
        custom_fields = note.custom_fields or []
        if isinstance(custom_fields, list):
            for item in custom_fields:
                if isinstance(item, list) and len(item) >= 3 and item[0] == key:
                    return item[2]
        elif isinstance(custom_fields, dict):
            return custom_fields.get(key)
        return None

    return getattr(note, field, None)


def _matches_rule(note: NoteNode, rule: NoteFilterRule) -> bool:
    field_value = _get_rule_value(note, rule.field)
    op = rule.op

    if op == "eq":
        return field_value == rule.value
    if op == "neq":
        return field_value != rule.value
    if op == "in":
        return field_value in rule.values
    if op == "not_in":
        return field_value not in rule.values
    if op == "contains":
        if field_value is None:
            return False
        return str(rule.value or "").lower() in str(field_value).lower()
    if op == "not_contains":
        if field_value is None:
            return True
        return str(rule.value or "").lower() not in str(field_value).lower()
    if op == "regex_search":
        if field_value is None:
            return False
        try:
            return re.search(str(rule.value or ""), str(field_value)) is not None
        except re.error:
            return False
    if op == "gte":
        return field_value is not None and field_value >= rule.value
    if op == "lte":
        return field_value is not None and field_value <= rule.value
    if op == "between":
        if len(rule.values) < 2 or field_value is None:
            return False
        start, end = rule.values[0], rule.values[1]
        return start <= field_value <= end

    return True


def _apply_sql_rule(query, rule: NoteFilterRule):
    if rule.field.startswith("custom_fields."):
        return query, False

    column = getattr(NoteNode, rule.field, None)
    if column is None:
        return query, False

    if rule.op == "eq":
        return query.where(column == rule.value), True
    if rule.op == "neq":
        return query.where(column != rule.value), True
    if rule.op == "in" and rule.values:
        return query.where(column.in_(rule.values)), True
    if rule.op == "not_in" and rule.values:
        return query.where(~column.in_(rule.values)), True
    if rule.op == "gte":
        return query.where(column >= rule.value), True
    if rule.op == "lte":
        return query.where(column <= rule.value), True
    if rule.op == "between" and len(rule.values) >= 2:
        return query.where(column >= rule.values[0]).where(column <= rule.values[1]), True
    if rule.op == "contains" and rule.field == "title" and rule.value is not None:
        return query.where(NoteNode.title.contains(str(rule.value))), True
    if rule.op == "not_contains" and rule.field == "title" and rule.value is not None:
        return query.where(~NoteNode.title.contains(str(rule.value))), True

    return query, False


def _sort_notes(notes: List[NoteNode], order_by: str, order_desc: bool) -> List[NoteNode]:
    sort_field = order_by if order_by in ALLOWED_ORDER_FIELDS else "updated_at"
    return sorted(
        notes,
        key=lambda note: (_get_rule_value(note, sort_field) is None, _get_rule_value(note, sort_field)),
        reverse=order_desc
    )


def _load_note_context(user_id: int, session: Session, *, include_edges: bool) -> NoteGraphContext:
    notes = session.exec(select(NoteNode).where(NoteNode.user_id == user_id)).all()
    edges = []
    if include_edges:
        edges = session.exec(select(NoteEdge).where(NoteEdge.user_id == user_id)).all()
    return NoteGraphContext.from_items(notes, edges)


def _apply_program_matcher(builder, matcher) -> None:
    if matcher.kind == "all":
        builder.all()
        return
    if matcher.kind == "none":
        builder.none()
        return
    if matcher.kind == "id":
        builder.match_id(matcher.ids)
        return
    if matcher.kind == "field":
        if not matcher.field:
            raise HTTPException(status_code=400, detail="field matcher requires a field name")
        is_time_field = matcher.field in {"start_at", "updated_at"}
        if is_time_field and (matcher.time_value is not None or matcher.time_values):
            builder.match_time_field(
                matcher.field,
                matcher.op or "eq",
                time_value=matcher.time_value,
                time_values=matcher.time_values,
            )
            return
        builder.match_field(
            matcher.field,
            matcher.op or "eq",
            value=matcher.value,
            values=matcher.values,
        )
        return
    if matcher.kind == "title_contains":
        builder.match_title(str(matcher.value or ""), ignore_case=matcher.ignore_case)
        return
    if matcher.kind == "seed":
        builder.is_seed()
        return
    if matcher.kind == "depth":
        builder.match_depth(min_depth=matcher.min_depth, max_depth=matcher.max_depth)
        return
    if matcher.kind == "relative_month_window":
        builder.relative_month_window(
            field=matcher.field or "start_at",
            start_month_offset=matcher.start_month_offset,
            end_month_offset=matcher.end_month_offset,
        )
        return

    raise HTTPException(status_code=400, detail=f"Unsupported matcher kind: {matcher.kind}")


def _build_program_walker(context: NoteGraphContext, request: NoteProgramRequest) -> NoteWalker:
    walker = NoteWalker(
        context,
        expand=request.program.expand.default,
        select=request.program.select.default,
    )

    for rule in request.program.expand.rules:
        builder = walker.expand if rule.action == "include" else walker.skip_expand
        _apply_program_matcher(builder, rule.matcher)

    for rule in request.program.select.rules:
        builder = walker.include if rule.action == "include" else walker.exclude
        _apply_program_matcher(builder, rule.matcher)

    return walker


def _ensure_seed_ids_exist(context: NoteGraphContext, seed_ids: List[str]) -> None:
    missing = [seed_id for seed_id in seed_ids if context.get_note(seed_id) is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"Seed notes not found: {', '.join(missing)}")


def _execute_note_program(
    request: NoteProgramRequest,
    *,
    current_user: User,
    user_id: int,
    session: Session,
):
    need_edges = request.result.include_edges or request.executor.kind == "component"
    context = _load_note_context(user_id, session, include_edges=need_edges)
    walker = _build_program_walker(context, request)

    if request.executor.kind == "scan":
        walk_result = walker.collect_all(include_edges=request.result.include_edges)
    elif request.executor.kind == "component":
        if not request.executor.seed_ids:
            raise HTTPException(status_code=400, detail="component executor requires seed_ids")
        _ensure_seed_ids_exist(context, request.executor.seed_ids)
        walk_result = walker.collect_component(
            request.executor.seed_ids,
            mode=request.executor.mode,
            max_depth=request.executor.max_depth,
            include_edges=request.result.include_edges,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported executor kind: {request.executor.kind}")

    sorted_nodes = _sort_notes(walk_result.nodes, request.result.order_by, request.result.order_desc)
    total_nodes = len(sorted_nodes)
    visible_nodes = sorted_nodes[request.result.skip: request.result.skip + request.result.limit]
    visible_ids = {node.id for node in visible_nodes}

    if request.result.include_edges:
        visible_edges = [
            edge for edge in walk_result.edges
            if edge.source_id in visible_ids and edge.target_id in visible_ids
        ]
    else:
        visible_edges = []

    return {
        "nodes": [_serialize_note_list(note, current_user) for note in visible_nodes],
        "edges": visible_edges,
        "total_nodes": total_nodes,
        "total_edges": len(visible_edges),
    }

# --- Notes ---

@router.get("/", response_model=List[NoteListRead])
def read_notes(
    skip: int = 0,
    limit: int = 128, # Default to 128 as requested
    created_start: Optional[float] = Query(None, description="Filter by start_at >= start"),
    created_end: Optional[float] = Query(None, description="Filter by start_at <= end"),
    updated_start: Optional[float] = Query(None, description="Filter by updated_at >= start"),
    updated_end: Optional[float] = Query(None, description="Filter by updated_at <= end"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Retrieve notes for the current user.
    Supports filtering by start_at (mapped from created_*) and update time range.
    Default limit is 128.
    """
    query = select(NoteNode).where(NoteNode.user_id == current_user.id)
    
    # Apply Time Filters
    # Note: 'created_start/end' params now filter by 'start_at' field
    if created_start is not None:
        query = query.where(NoteNode.start_at >= created_start)
    if created_end is not None:
        query = query.where(NoteNode.start_at <= created_end)
        
    if updated_start is not None:
        query = query.where(NoteNode.updated_at >= updated_start)
    if updated_end is not None:
        query = query.where(NoteNode.updated_at <= updated_end)
        
    # Order by updated_at desc to get the "latest" ones first
    query = query.order_by(NoteNode.updated_at.desc())
    
    statement = query.offset(skip).limit(limit)
    notes = session.exec(statement).all()
    return [_serialize_note_list(note, current_user) for note in notes]


@router.post("/query", response_model=NoteQueryResponse)
def query_notes(
    request: NoteQueryRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Query a reusable note set using a generic scope + rules model.
    """
    scope = request.scope
    query = select(NoteNode).where(NoteNode.user_id == current_user.id)

    edge_pool: List[NoteEdge] = []
    if scope.mode in {"planetary", "satellite"}:
        if not scope.seed_note_id:
            raise HTTPException(status_code=400, detail="seed_note_id is required for graph scopes")
        note_ids, edge_pool = _get_component_note_ids(
            scope.seed_note_id,
            scope.mode,
            current_user.id,
            session
        )
        if not note_ids:
            return {"nodes": [], "edges": [], "total_nodes": 0, "total_edges": 0}
        query = query.where(NoteNode.id.in_(note_ids))

    python_rules: List[NoteFilterRule] = []
    for rule in request.rules:
        query, handled = _apply_sql_rule(query, rule)
        if not handled:
            python_rules.append(rule)

    candidate_notes = session.exec(query).all()
    filtered_notes = [note for note in candidate_notes if all(_matches_rule(note, rule) for rule in python_rules)]
    sorted_notes = _sort_notes(filtered_notes, request.order_by, request.order_desc)

    total_nodes = len(sorted_notes)
    visible_notes = sorted_notes[request.skip: request.skip + request.limit]
    visible_ids = {note.id for note in visible_notes}

    if request.include_edges:
        if not edge_pool:
            edge_pool = session.exec(select(NoteEdge).where(NoteEdge.user_id == current_user.id)).all()
        visible_edges = [
            edge for edge in edge_pool
            if edge.source_id in visible_ids and edge.target_id in visible_ids
        ]
    else:
        visible_edges = []

    return {
        "nodes": [_serialize_note_list(note, current_user) for note in visible_notes],
        "edges": visible_edges,
        "total_nodes": total_nodes,
        "total_edges": len(visible_edges)
    }


@router.post("/query-program", response_model=NoteProgramResponse)
def query_note_program(
    request: NoteProgramRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Execute a walker-style filtering program over the current user's note graph.
    """
    return _execute_note_program(request, current_user=current_user, user_id=current_user.id, session=session)


@router.post("/batch-update", response_model=NoteBatchUpdateResponse)
def batch_update_notes(
    request: NoteBatchUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Batch update notes for the current user.
    """
    note_ids = [str(note_id).strip() for note_id in request.ids if str(note_id).strip()]
    if not note_ids:
        raise HTTPException(status_code=400, detail="ids is required")

    patch = request.patch.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="patch is required")
    if patch.get("weight") is not None and patch.get("weight_delta") is not None:
        raise HTTPException(status_code=400, detail="weight and weight_delta cannot be used together")

    notes = session.exec(
        select(NoteNode).where(
            NoteNode.user_id == current_user.id,
            NoteNode.id.in_(note_ids)
        )
    ).all()

    note_by_id = {str(note.id): note for note in notes}
    ordered_notes = [note_by_id[note_id] for note_id in note_ids if note_id in note_by_id]

    now_ts = int(time.time())
    updated_notes: List[NoteNode] = []
    touched_history = False

    for note in ordered_notes:
        next_patch = dict(patch)
        if "private_level" in next_patch and next_patch["private_level"] is not None:
            next_patch["private_level"] = int(next_patch["private_level"])
        if "weight_delta" in next_patch and next_patch["weight_delta"] is not None:
            delta = int(next_patch.pop("weight_delta"))
            next_patch["weight"] = int(note.weight or 0) + delta
        elif "weight" in next_patch and next_patch["weight"] is not None:
            next_patch["weight"] = int(next_patch["weight"])

        prepared_patch = _prepare_note_update_data(note, next_patch)
        changed_fields = {
            key: value
            for key, value in prepared_patch.items()
            if getattr(note, key) != value
        }
        if not changed_fields:
            continue

        if _append_note_history(note, changed_fields, now_ts):
            touched_history = True

        for key, value in changed_fields.items():
            setattr(note, key, value)

        note.updated_at = time.time()
        session.add(note)
        updated_notes.append(note)

    if updated_notes or touched_history:
        session.commit()
        for note in updated_notes:
            session.refresh(note)

    return {
        "updated_count": len(updated_notes),
        "notes": [_serialize_note_list(note, current_user) for note in updated_notes],
    }


@router.get("/category-palette", response_model=NoteCategoryPaletteResponse)
@router.get("/type-palette", response_model=NoteCategoryPaletteResponse)
def get_note_category_palette(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    return _build_note_type_palette_response(current_user.id, session)


@router.get("/category-palette/{category_key}/can-delete")
@router.get("/type-palette/{category_key}/can-delete")
def can_delete_note_category_palette_item(
    category_key: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    return {"can_delete": not _is_note_type_in_use(current_user.id, category_key, session)}


@router.put("/category-palette", response_model=NoteCategoryPaletteResponse)
@router.put("/type-palette", response_model=NoteCategoryPaletteResponse)
def update_note_category_palette(
    request: NoteCategoryPaletteUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    items = _normalize_note_type_palette_items([item.model_dump() for item in request.items])
    _ensure_note_type_labels_unique(items)
    next_keys = {item["key"] for item in items}
    existing_keys = {item["key"] for item in (_load_note_type_palette_items(current_user.id, session) or _default_note_type_palette_items())}
    removed_keys = {key for key in existing_keys if key not in next_keys}
    blocked_keys = sorted(_find_in_use_note_type_keys(current_user.id, removed_keys, session))
    if blocked_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete categories that are still in use: {', '.join(blocked_keys)}",
        )
    setting_key = build_note_category_palette_setting_key(current_user.id)
    row = session.get(AppSetting, setting_key)
    if row is None:
        row = AppSetting(key=setting_key)
    row.value = {"items": items}
    row.updated_at = time.time()
    session.add(row)
    session.commit()
    return _build_note_type_palette_response(current_user.id, session)


@router.post("/category-palette/merge", response_model=NoteCategoryPaletteResponse)
@router.post("/type-palette/merge", response_model=NoteCategoryPaletteResponse)
def merge_note_category_palette_item(
    request: NoteCategoryMergeRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    source_key = str(request.source_key or "").strip()
    target_key = str(request.target_key or "").strip()
    if not source_key or not target_key:
        raise HTTPException(status_code=400, detail="source_key and target_key are required")
    if source_key == target_key:
        raise HTTPException(status_code=400, detail="source_key and target_key must be different")

    palette_keys = _get_note_type_palette_keys(current_user.id, session)
    if source_key not in palette_keys:
        raise HTTPException(status_code=404, detail=f"Category not found: {source_key}")
    if target_key not in palette_keys:
        raise HTTPException(status_code=404, detail=f"Category not found: {target_key}")

    changed = False
    now = time.time()
    notes = session.exec(select(NoteNode).where(NoteNode.user_id == current_user.id)).all()
    for note in notes:
        effective_note_categories = _resolve_effective_note_categories_payload(
            note.note_categories,
            note.primary_category,
            note.note_types,
            note.node_type,
            note.note_kind,
            note.node_status,
            note.color,
        )
        merged_note_categories, did_change = merge_note_types(
            effective_note_categories,
            source_key=source_key,
            target_key=target_key,
            fallback_type=note.primary_category or NOTE_CATEGORY_DEFAULT,
        )
        if not did_change:
            continue
        taxonomy_payload = _build_legacy_fields_from_taxonomy(
            merged_note_categories,
            derive_primary_category(merged_note_categories, fallback_category=note.primary_category or NOTE_CATEGORY_DEFAULT),
            note.note_form or NOTE_FORM_DEFAULT,
            note.note_scene or note.note_kind or NOTE_SCENE_DEFAULT,
            note.lifecycle_stage or note.node_status or NOTE_LIFECYCLE_STAGE_DEFAULT,
        )
        for field, value in taxonomy_payload.items():
            setattr(note, field, value)
        note.updated_at = now
        session.add(note)
        changed = True

    if changed:
        session.commit()

    return _build_note_type_palette_response(current_user.id, session)

@router.post("/", response_model=NoteRead)
def create_note(
    note: NoteCreate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Create a new note.
    """
    normalized_note_color = normalize_note_color(note.color)
    uses_new_taxonomy_input = bool({
        "note_categories",
        "primary_category",
        "note_form",
        "note_scene",
        "lifecycle_stage",
    } & set(note.model_fields_set))

    if uses_new_taxonomy_input:
        taxonomy_fields = _build_legacy_fields_from_taxonomy(
            note.note_categories,
            note.primary_category or NOTE_CATEGORY_DEFAULT,
            note.note_form or NOTE_FORM_DEFAULT,
            note.note_scene or note.note_kind or NOTE_SCENE_DEFAULT,
            note.lifecycle_stage or note.node_status or NOTE_LIFECYCLE_STAGE_DEFAULT,
        )
        normalized_note_types = normalize_note_types(taxonomy_fields["note_types"], fallback_type=taxonomy_fields["node_type"])
        primary_node_type = str(taxonomy_fields["node_type"] or NOTE_TYPE_DEFAULT).strip() or NOTE_TYPE_DEFAULT
        effective_note_kind = str(taxonomy_fields["note_kind"] or NOTE_KIND_DEFAULT).strip() or NOTE_KIND_DEFAULT
        effective_node_status = str(taxonomy_fields["node_status"] or NOTE_LIFECYCLE_STAGE_DEFAULT).strip() or NOTE_LIFECYCLE_STAGE_DEFAULT
    else:
        normalized_note_types = normalize_note_types(note.note_types, fallback_type=note.node_type or NOTE_TYPE_DEFAULT)
        fallback_type = note.node_type or NOTE_TYPE_DEFAULT
        if normalized_note_color and (
            not note.note_types
            or (
                len(normalized_note_types) == 1
                and normalized_note_types[0].get("key") == fallback_type
                and int(normalized_note_types[0].get("weight", 0)) == 100
            )
        ):
            legacy_color_type_key = build_legacy_color_type_key(normalized_note_color)
            if legacy_color_type_key:
                normalized_note_types = [{"key": legacy_color_type_key, "weight": 100}]
        primary_node_type = derive_primary_node_type(normalized_note_types, fallback_type=note.node_type or NOTE_TYPE_DEFAULT)
        effective_note_kind = note.note_kind or NOTE_KIND_DEFAULT
        effective_node_status = note.node_status or NOTE_LIFECYCLE_STAGE_DEFAULT
        taxonomy_fields = _build_note_taxonomy_fields(
            normalized_note_types,
            primary_node_type,
            effective_note_kind,
            effective_node_status,
        )
    current_time = time.time()
    db_note = NoteNode(
        id=str(uuid.uuid4()), # Generate UUID
        user_id=current_user.id,
        title=note.title,
        content=note.content,
        weight=note.weight,
        node_type=primary_node_type,
        note_types=normalized_note_types,
        note_categories=taxonomy_fields["note_categories"],
        primary_category=taxonomy_fields["primary_category"],
        note_form=taxonomy_fields["note_form"],
        note_kind=effective_note_kind,
        note_scene=taxonomy_fields["note_scene"],
        node_status=effective_node_status,
        lifecycle_stage=taxonomy_fields["lifecycle_stage"],
        color=normalized_note_color,
        weight_mode=note.weight_mode,
        private_level=note.private_level,
        custom_fields=note.custom_fields,
        # parent_id=note.parent_id, # Deprecated
        created_at=current_time,
        updated_at=current_time,
        start_at=note.start_at if note.start_at is not None else current_time,
        history=[]
    )
    
    session.add(db_note)
    session.commit()
    session.refresh(db_note)
    return _serialize_note_read(db_note, current_user)

@router.get("/{note_id}", response_model=NoteRead)
def read_note(
    note_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Get a specific note.
    """
    note = _get_accessible_note(note_id, current_user, session)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    # Calculate edge count
    edge_count = session.exec(
        select(func.count()).select_from(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            or_(NoteEdge.source_id == note_id, NoteEdge.target_id == note_id)
        )
    ).one()
    
    # Calculate out_degree (for Satellite mode)
    out_degree = session.exec(
        select(func.count()).select_from(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            NoteEdge.source_id == note_id
        )
    ).one()

    # --- Inherited Custom Fields Logic ---
    # 1. Fetch direct parents (incoming edges)
    direct_parent_edges = session.exec(
        select(NoteEdge).where(
            NoteEdge.user_id == note.user_id,
            NoteEdge.target_id == note_id
        )
    ).all()
    
    parent_ids = [e.source_id for e in direct_parent_edges]
    
    # 2. Fetch parent nodes
    parent_nodes = []
    if parent_ids:
        parent_nodes = session.exec(
            select(NoteNode).where(
                NoteNode.id.in_(parent_ids),
                NoteNode.user_id == note.user_id
            )
        ).all()

    # 3. Ancestors (Simplified: just one level up for now or BFS for full ancestors?)
    # User asked for "Direct Parent" and "Other Indirect Ancestors".
    # For performance, let's go up 2-3 levels or fetch all reachable ancestors?
    # Fetching all ancestors in a graph can be heavy.
    # Let's implement a limited BFS upstream (e.g., max depth 3) to find ancestors.
    
    ancestor_fields = {} # Key -> [Key, Type, Value]
    direct_parent_fields = {} # Key -> [Key, Type, Value]
    
    # Process Direct Parents first
    for p_node in parent_nodes:
        if p_node.custom_fields:
            if isinstance(p_node.custom_fields, list):
                for field_item in p_node.custom_fields:
                    # field_item is [key, type, value] or {"key":...} (if old format lingers?)
                    # We migrated, so assume list [k, t, v]
                    if isinstance(field_item, list) and len(field_item) >= 3:
                        k, t, v = field_item[0], field_item[1], field_item[2]
                        direct_parent_fields[k] = [k, t, v]
            elif isinstance(p_node.custom_fields, dict):
                # Fallback for unmigrated (shouldn't happen if migration ran)
                for k, v in p_node.custom_fields.items():
                    direct_parent_fields[k] = [k, "string", v]
    
    # Process Ancestors (BFS Upstream)
    # We already have parents. Let's find their parents.
    visited_ancestors = set(parent_ids)
    queue = list(parent_ids)
    max_depth = 3 # Limit depth to prevent performance issues
    current_depth = 0
    
    while queue and current_depth < max_depth:
        # Get next level parents
        next_level_ids = []
        if queue:
            # Find edges where target is in queue (incoming to current level)
            upstream_edges = session.exec(
                select(NoteEdge).where(
                    NoteEdge.user_id == note.user_id,
                    NoteEdge.target_id.in_(queue)
                )
            ).all()
            
            new_ancestor_ids = []
            for edge in upstream_edges:
                if edge.source_id not in visited_ancestors and edge.source_id != note_id:
                    visited_ancestors.add(edge.source_id)
                    new_ancestor_ids.append(edge.source_id)
            
            if new_ancestor_ids:
                # Fetch these ancestor nodes
                ancestor_nodes = session.exec(
                    select(NoteNode).where(
                        NoteNode.id.in_(new_ancestor_ids),
                        NoteNode.user_id == note.user_id
                    )
                ).all()
                
                for anc_node in ancestor_nodes:
                    if anc_node.custom_fields:
                        if isinstance(anc_node.custom_fields, list):
                            for field_item in anc_node.custom_fields:
                                if isinstance(field_item, list) and len(field_item) >= 3:
                                    k, t, v = field_item[0], field_item[1], field_item[2]
                                    ancestor_fields[k] = [k, t, v]
                        elif isinstance(anc_node.custom_fields, dict):
                            for k, v in anc_node.custom_fields.items():
                                ancestor_fields[k] = [k, "string", v]
                
                queue = new_ancestor_ids
            else:
                queue = []
        current_depth += 1

    # Remove keys from ancestor_fields that are already in direct_parent_fields
    # User wants: 1. Own, 2. Direct Parent (but not own), 3. Indirect (but not own or direct)
    
    # We will return these as separate Lists (of Lists) in the response.
    # We need to filter out duplicates.
    
    # Direct fields are prioritized over Ancestor fields
    final_direct_fields = []
    final_ancestor_fields = []
    
    # But wait, frontend also checks against "Own" fields.
    # The API just returns parents/ancestors. Frontend does the "Own" check?
    # No, API should probably filter? Or return raw context?
    # Previous implementation returned raw context for parents/ancestors, frontend filtered against own.
    # Let's keep that.
    
    # Convert dicts back to lists
    final_direct_fields = list(direct_parent_fields.values())
    
    # Filter ancestors: if in direct, remove from ancestor
    for k in list(ancestor_fields.keys()):
        if k in direct_parent_fields:
            del ancestor_fields[k]
            
    final_ancestor_fields = list(ancestor_fields.values())
    
    return _serialize_note_read(
        note,
        current_user,
        edge_count=edge_count,
        out_degree=out_degree,
        inherited_fields={
            "direct": final_direct_fields,
            "ancestors": final_ancestor_fields,
        },
    )

@router.get("/{note_id}/connected-component", response_model=GraphData)
def get_connected_component(
    note_id: str,
    mode: str = Query("planetary", description="Mode: 'planetary' (default) or 'satellite'"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Get the weakly connected component containing the given note.
    mode='satellite': Ignore incoming edges to the center note (only outgoing).
    """
    note_ids, edges = _get_component_note_ids(note_id, mode, current_user.id, session)
    nodes = session.exec(
        select(NoteNode).where(
            NoteNode.user_id == current_user.id,
            NoteNode.id.in_(note_ids)
        )
    ).all()
    component_edges = [edge for edge in edges if edge.source_id in note_ids and edge.target_id in note_ids]
    return {"nodes": [_serialize_note_list(note, current_user) for note in nodes], "edges": component_edges}

@router.put("/{note_id}", response_model=NoteRead)
def update_note(
    note_id: str,
    note_in: NoteUpdate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Update a note.
    """
    db_note = _get_accessible_note(note_id, current_user, session)
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    note_data = _prepare_note_update_data(db_note, note_in.model_dump(exclude_unset=True))
    _append_note_history(db_note, note_data, int(time.time()))

    for key, value in note_data.items():
        setattr(db_note, key, value)
    
    db_note.updated_at = time.time()
    session.add(db_note)
    session.commit()
    session.refresh(db_note)
    return _serialize_note_read(db_note, current_user)

@router.delete("/{note_id}")
def delete_note(
    note_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Delete a note.
    """
    db_note = _get_accessible_note(note_id, current_user, session)
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    session.delete(db_note)
    session.commit()
    return {"ok": True}

# --- Edges ---

@router.get("/edges/", response_model=List[EdgeRead])
def read_edges(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Retrieve all edges for the current user.
    """
    statement = select(NoteEdge).where(NoteEdge.user_id == current_user.id)
    edges = session.exec(statement).all()
    return edges

@router.post("/edges/", response_model=EdgeRead)
def create_edge(
    edge: EdgeCreate,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Create a directed edge between two notes.
    Idempotent: If edge exists, return existing one (or update timestamp).
    """
    # Verify nodes exist and belong to user
    source = session.exec(select(NoteNode).where(NoteNode.id == edge.source_id, NoteNode.user_id == current_user.id)).first()
    target = session.exec(select(NoteNode).where(NoteNode.id == edge.target_id, NoteNode.user_id == current_user.id)).first()
    
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or Target node not found")

    # Prevent self-loop if desired (optional)
    if edge.source_id == edge.target_id:
        raise HTTPException(status_code=400, detail="Self-loops are not allowed")

    # Check if edge already exists
    statement = select(NoteEdge).where(
        NoteEdge.user_id == current_user.id,
        NoteEdge.source_id == edge.source_id,
        NoteEdge.target_id == edge.target_id
    )
    existing_edge = session.exec(statement).first()
    
    if existing_edge:
        # Idempotent: Return existing edge
        # Optionally update label if provided
        if edge.label is not None and edge.label != existing_edge.label:
            existing_edge.label = edge.label
            session.add(existing_edge)
            session.commit()
            session.refresh(existing_edge)
        return existing_edge

    db_edge = NoteEdge(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        source_id=edge.source_id,
        target_id=edge.target_id,
        label=edge.label,
        created_at=time.time()
    )
    session.add(db_edge)
    session.commit()
    session.refresh(db_edge)
    return db_edge

@router.delete("/edges/")
def delete_edge_by_nodes(
    source: str,
    target: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Delete an edge by source and target IDs.
    Robust: If edge doesn't exist, return success anyway (idempotent delete).
    """
    statement = select(NoteEdge).where(
        NoteEdge.user_id == current_user.id,
        NoteEdge.source_id == source,
        NoteEdge.target_id == target
    )
    db_edges = session.exec(statement).all()
    
    if not db_edges:
        # Already deleted or never existed, return success
        return {"ok": True, "message": "Edge not found, treated as deleted"}
    
    for edge in db_edges:
        session.delete(edge)
    
    session.commit()
    return {"ok": True}

@router.delete("/edges/{edge_id}")
def delete_edge(
    edge_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Delete an edge by ID.
    Robust: If edge doesn't exist, return success.
    """
    statement = select(NoteEdge).where(NoteEdge.id == edge_id, NoteEdge.user_id == current_user.id)
    db_edge = session.exec(statement).first()
    if not db_edge:
        # Robustness: Don't error on missing delete target
        return {"ok": True, "message": "Edge not found, treated as deleted"}
    
    session.delete(db_edge)
    session.commit()
    return {"ok": True}
