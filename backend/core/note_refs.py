from __future__ import annotations

from typing import Iterable

from sqlalchemy import or_
from sqlmodel import Session, select

from backend.models import NoteNode


NotePublicApiId = int | str


def note_public_id(note: NoteNode) -> str:
    numeric_id = int(note.numeric_id or 0)
    if numeric_id > 0:
        return str(numeric_id)
    return str(note.id or "")


def note_public_api_id(note: NoteNode) -> NotePublicApiId:
    numeric_id = int(note.numeric_id or 0)
    if numeric_id > 0:
        return numeric_id
    return str(note.id or "")


def note_ref_aliases(note: NoteNode) -> set[str]:
    refs = {
        str(note.id or "").strip(),
        str(getattr(note, "legacy_id", None) or "").strip(),
        note_public_id(note),
    }
    return {ref for ref in refs if ref}


def note_edge_ref(note: NoteNode) -> str:
    return note_public_id(note)


def build_note_ref_map(notes: Iterable[NoteNode]) -> dict[str, NoteNode]:
    result: dict[str, NoteNode] = {}
    for note in notes:
        for ref in note_ref_aliases(note):
            result[ref] = note
    return result


def load_notes_by_refs(session: Session, user_id: int, refs: Iterable[str]) -> dict[str, NoteNode]:
    normalized_refs = {str(ref or "").strip() for ref in refs}
    normalized_refs.discard("")
    if not normalized_refs:
        return {}

    legacy_refs = [ref for ref in normalized_refs if not ref.isdecimal()]
    numeric_refs = [int(ref) for ref in normalized_refs if ref.isdecimal()]
    conditions = []
    if legacy_refs:
        conditions.append(NoteNode.id.in_(legacy_refs))
        conditions.append(NoteNode.legacy_id.in_(legacy_refs))
    if numeric_refs:
        conditions.append(NoteNode.numeric_id.in_(numeric_refs))
    if not conditions:
        return {}

    query = select(NoteNode).where(NoteNode.user_id == user_id)
    query = query.where(or_(*conditions) if len(conditions) > 1 else conditions[0])
    return build_note_ref_map(session.exec(query).all())
