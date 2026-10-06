"""Atomic, reversible note consolidation; the caller supplies the edited narrative.

Original note bodies and metadata remain in a snapshot and soft-archived notes.
Versions guard every participant. A mutation ID makes retries safe after a lost reply.
"""
from copy import deepcopy
import hashlib
import json
import time

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlmodel import Session, select

from backend.models import NoteNode, NoteEdge
from backend.core.notes.refs import load_notes_by_refs, note_public_id, note_ref_aliases


class NoteMergeRequest(BaseModel):
    target_id: str
    versions: dict[str, int] = Field(min_length=2, max_length=100)
    title: str = Field(min_length=1)
    content: str
    mutation_id: str = Field(min_length=1, max_length=200)


def merge_metadata(note: NoteNode, key: str, default=None):
    """Read a merge marker from the public [name, type, value] custom-field shape."""
    return next((row[2] for row in (note.custom_fields or [])
                 if isinstance(row, list) and len(row) >= 3 and row[0] == key), default)


def merge_notes(session: Session, user_id: int, request: NoteMergeRequest) -> NoteNode:
    """Merge only this user's notes; move edges and archive sources in one transaction."""
    fingerprint = hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True,
                                            ensure_ascii=False).encode()).hexdigest()
    refs = load_notes_by_refs(session, user_id, list(request.versions), include_deleted=True)
    notes = [refs.get(ref) for ref in request.versions]
    target = refs.get(request.target_id)
    if target is None or any(note is None for note in notes):
        raise HTTPException(404, 'Merge participants not found in this account')
    if len({note.id for note in notes}) != len(notes):
        raise HTTPException(422, 'Each note must occur exactly once')
    records = deepcopy(merge_metadata(target, '_merge_history', []))
    previous = next((r for r in records if r['mutation_id'] == request.mutation_id), None)
    if previous:
        if previous['fingerprint'] != fingerprint:
            raise HTTPException(409, 'Mutation ID reused with different input')
        return target
    for ref, note in zip(request.versions, notes):
        if note.deleted_at or note.version != request.versions[ref]:
            raise HTTPException(409, f'Note {ref} changed or was archived; reread before merging')
    now = time.time()
    snapshots = [dict(id=note_public_id(note), title=note.title, content=note.content,
                      format_type=note.format_type, version=note.version,
                      custom_fields=[deepcopy(row) for row in (note.custom_fields or [])
                                     if row[0] != '_merge_history']) for note in notes]
    records.append(dict(mutation_id=request.mutation_id, fingerprint=fingerprint,
                        merged_at=now, originals=snapshots))
    fields = [deepcopy(row) for row in (target.custom_fields or []) if row[0] != '_merge_history']
    fields.append(['_merge_history', 'json', records])
    aliases = set().union(*(note_ref_aliases(n) for n in notes))
    canonical = note_public_id(target)
    try:
        # Compare-and-swap each row inside the same transaction; no partial consolidation.
        with session.no_autoflush:
            for ref, note in zip(request.versions, notes):
                values = dict(version=note.version + 1, updated_at=now)
                if note.id == target.id:
                    values.update(title=request.title, content=request.content,
                                  format_type='markdown', custom_fields=fields)
                else:
                    source_fields = [deepcopy(row) for row in (note.custom_fields or [])
                                     if row[0] != '_merged_into']
                    source_fields.append(['_merged_into', 'json',
                        {'target_id': canonical, 'mutation_id': request.mutation_id}])
                    values.update(deleted_at=now, deleted_by_user_id=user_id,
                                  custom_fields=source_fields)
                result = session.execute(update(NoteNode).where(
                    NoteNode.id == note.id, NoteNode.user_id == user_id,
                    NoteNode.version == request.versions[ref],
                    (NoteNode.deleted_at.is_(None) | (NoteNode.deleted_at <= 0))
                ).values(**values).execution_options(synchronize_session=False))
                if result.rowcount != 1:
                    raise HTTPException(409, 'Concurrent note change; reread before merging')
            seen = set()
            for edge in session.exec(select(NoteEdge).where(NoteEdge.user_id == user_id)).all():
                source = canonical if str(edge.source_id) in aliases else str(edge.source_id)
                dest = canonical if str(edge.target_id) in aliases else str(edge.target_id)
                key = (source, dest, edge.label)
                if source == dest or key in seen:
                    if str(edge.source_id) in aliases or str(edge.target_id) in aliases:
                        session.delete(edge)
                    continue
                seen.add(key)
                edge.source_id, edge.target_id = source, dest
                session.add(edge)
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(target)
    return target
