from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlmodel import Session

from backend.core.resource_identity import RESOURCE_TYPE_NOTE, allocate_resource_id
from backend.models import NoteNode


@dataclass(frozen=True)
class NewNoteIdentity:
    primary_id: str
    numeric_id: int
    legacy_id: str


def allocate_new_note_identity(session: Session) -> NewNoteIdentity:
    legacy_id = str(uuid.uuid4())
    numeric_id = allocate_resource_id(session, RESOURCE_TYPE_NOTE, legacy_id)
    primary_id = str(numeric_id)
    if session.get(NoteNode, primary_id) is not None:
        raise RuntimeError(f"allocated note resource id conflicts with existing note primary key: {primary_id}")
    return NewNoteIdentity(primary_id=primary_id, numeric_id=numeric_id, legacy_id=legacy_id)
