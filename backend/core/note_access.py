from typing import Any, Optional

from backend.models import NoteNode, User


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
    payload["can_edit"] = can_edit_note(note, current_user)
    payload.update(extra_fields)
    return payload
