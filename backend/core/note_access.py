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


def can_edit_note(note: NoteNode, current_user: Optional[User]) -> bool:
    if current_user is None:
        return False
    return current_user.is_superuser or current_user.id == note.user_id


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
    return payload
