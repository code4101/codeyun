from typing import Any, Optional

from backend.models import NoteNode, User
from backend.core.note_refs import note_public_api_id
from backend.core.note_semantics import (
    NOTE_CATEGORY_DEFAULT,
    NOTE_FORM_DEFAULT,
    NOTE_LIFECYCLE_STAGE_DEFAULT,
    NOTE_SCENE_DEFAULT,
    derive_legacy_semantics_from_taxonomy,
    derive_note_taxonomy_from_legacy,
)
from backend.core.note_progress import (
    evaluate_completion_progress_expr,
    get_completion_progress_expr,
)
from backend.core.yuque_html import normalize_legacy_yuque_lake_html


def can_edit_note(note: NoteNode, current_user: Optional[User]) -> bool:
    if current_user is None:
        return False
    return current_user.is_superuser or current_user.id == note.user_id


def _infer_custom_field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    return "string"


def _normalize_custom_fields_for_response(value: Any) -> list[list[Any]]:
    if isinstance(value, list):
        normalized: list[list[Any]] = []
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                key = str(item[0] or "").strip()
                if not key:
                    continue
                normalized.append([key, str(item[1] or "string"), item[2]])
                continue
            if isinstance(item, dict):
                key = str(item.get("key") or "").strip()
                if not key:
                    continue
                field_value = item.get("value")
                normalized.append([key, str(item.get("type") or _infer_custom_field_type(field_value)), field_value])
        return normalized

    if isinstance(value, dict):
        return [
            [str(key), _infer_custom_field_type(field_value), field_value]
            for key, field_value in value.items()
            if str(key).strip()
        ]

    return []


def note_to_response_dict(
    note: NoteNode,
    current_user: Optional[User],
    **extra_fields: Any,
) -> dict[str, Any]:
    payload = note.model_dump()
    payload["id"] = note_public_api_id(note)
    if payload.get("note_categories") or payload.get("primary_category") or payload.get("note_form") or payload.get("note_scene") or payload.get("lifecycle_stage"):
        normalized = derive_legacy_semantics_from_taxonomy(
            payload.get("note_categories"),
            payload.get("primary_category") or NOTE_CATEGORY_DEFAULT,
            payload.get("note_form") or NOTE_FORM_DEFAULT,
            payload.get("note_scene") or payload.get("note_kind") or NOTE_SCENE_DEFAULT,
            payload.get("lifecycle_stage") or payload.get("node_status") or NOTE_LIFECYCLE_STAGE_DEFAULT,
        )
    else:
        normalized = derive_note_taxonomy_from_legacy(
            payload.get("note_types"),
            payload.get("node_type"),
            payload.get("note_kind"),
            payload.get("node_status"),
        )
    payload.update(normalized)
    payload["can_edit"] = can_edit_note(note, current_user)
    payload.update(extra_fields)
    if isinstance(payload.get("content"), str):
        payload["content"] = normalize_legacy_yuque_lake_html(payload["content"])
    payload["custom_fields"] = _normalize_custom_fields_for_response(payload.get("custom_fields"))
    if not isinstance(payload.get("history"), list):
        payload["history"] = []
    payload["completion_progress_expr"] = get_completion_progress_expr(payload.get("custom_fields"))
    payload["completion_progress"] = evaluate_completion_progress_expr(payload.get("completion_progress_expr"))
    return payload


def note_to_list_response_dict(note: NoteNode, current_user: Optional[User]) -> dict[str, Any]:
    return note_list_mapping_to_response_dict(
        {
            "id": note.id,
            "numeric_id": note.numeric_id,
            "user_id": note.user_id,
            "title": note.title,
            "weight": note.weight,
            "node_type": note.node_type,
            "note_types": note.note_types,
            "note_categories": note.note_categories,
            "primary_category": note.primary_category,
            "note_form": note.note_form,
            "note_kind": note.note_kind,
            "note_scene": note.note_scene,
            "node_status": note.node_status,
            "lifecycle_stage": note.lifecycle_stage,
            "color": note.color,
            "weight_mode": note.weight_mode,
            "private_level": note.private_level,
            "custom_fields": note.custom_fields,
            "created_at": note.created_at,
            "updated_at": note.updated_at,
            "start_at": note.start_at,
            "deleted_at": note.deleted_at,
            "deleted_by_user_id": note.deleted_by_user_id,
        },
        current_user,
    )


def note_list_mapping_to_response_dict(note: Any, current_user: Optional[User]) -> dict[str, Any]:
    numeric_id = int(note.get("numeric_id") or 0)
    user_id = int(note.get("user_id") or 0)
    payload = {
        "id": numeric_id if numeric_id > 0 else str(note.get("id") or ""),
        "numeric_id": note.get("numeric_id"),
        "user_id": user_id,
        "title": note.get("title") or "",
        "weight": note.get("weight") or 0,
        "node_type": note.get("node_type"),
        "note_types": note.get("note_types") or [],
        "note_categories": note.get("note_categories") or [],
        "primary_category": note.get("primary_category"),
        "note_form": note.get("note_form"),
        "note_kind": note.get("note_kind"),
        "note_scene": note.get("note_scene"),
        "node_status": note.get("node_status"),
        "lifecycle_stage": note.get("lifecycle_stage"),
        "color": note.get("color"),
        "weight_mode": note.get("weight_mode"),
        "private_level": note.get("private_level") or 0,
        "custom_fields": _normalize_custom_fields_for_response(note.get("custom_fields")),
        "can_edit": bool(current_user and (current_user.is_superuser or current_user.id == user_id)),
        "created_at": float(note.get("created_at") or 0),
        "updated_at": float(note.get("updated_at") or 0),
        "start_at": float(note.get("start_at") or 0),
        "deleted_at": note.get("deleted_at"),
        "deleted_by_user_id": note.get("deleted_by_user_id"),
    }

    if payload.get("note_categories") or payload.get("primary_category") or payload.get("note_form") or payload.get("note_scene") or payload.get("lifecycle_stage"):
        normalized = derive_legacy_semantics_from_taxonomy(
            payload.get("note_categories"),
            payload.get("primary_category") or NOTE_CATEGORY_DEFAULT,
            payload.get("note_form") or NOTE_FORM_DEFAULT,
            payload.get("note_scene") or payload.get("note_kind") or NOTE_SCENE_DEFAULT,
            payload.get("lifecycle_stage") or payload.get("node_status") or NOTE_LIFECYCLE_STAGE_DEFAULT,
        )
    else:
        normalized = derive_note_taxonomy_from_legacy(
            payload.get("note_types"),
            payload.get("node_type"),
            payload.get("note_kind"),
            payload.get("node_status"),
        )
    payload.update(normalized)
    payload["completion_progress_expr"] = get_completion_progress_expr(payload.get("custom_fields"))
    payload["completion_progress"] = evaluate_completion_progress_expr(payload.get("completion_progress_expr"))
    return payload
