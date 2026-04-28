from typing import Any, Optional

from backend.models import NoteNode, User
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
    payload["custom_fields"] = _normalize_custom_fields_for_response(payload.get("custom_fields"))
    if not isinstance(payload.get("history"), list):
        payload["history"] = []
    payload["completion_progress_expr"] = get_completion_progress_expr(payload.get("custom_fields"))
    payload["completion_progress"] = evaluate_completion_progress_expr(payload.get("completion_progress_expr"))
    return payload
