"""Personal reading pages scoped to a stable book and catalog ID, not page number.

GET never creates an empty page. PUT compares a revision atomically so another
tab cannot silently replace newer text. Reading permission and user ownership
are shared with the library; even shared books have separate personal notes.
"""
import hashlib
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from backend.api.library_annotations import require_library_resource_access
from backend.core.access.auth import get_current_active_user
from backend.db import get_session
from backend.models import LibraryReadingNote, User

router = APIRouter()


class ReadingNoteScope(BaseModel):
    resource_type: str = Field(default='rich-text', max_length=40)
    resource_id: str = Field(min_length=1, max_length=160)
    chapter_id: str = Field(default='', max_length=512)


class ReadingNoteWrite(ReadingNoteScope):
    content: str = Field(default='', max_length=1_000_000)
    revision: int = Field(default=0, ge=0)


class ReadingNotePage(ReadingNoteWrite):
    updated_at: float | None = None


def note_identity(user_id: int, scope: ReadingNoteScope) -> str:
    return hashlib.sha256(json.dumps([user_id, scope.resource_type, scope.resource_id,
                                    scope.chapter_id], ensure_ascii=False).encode()).hexdigest()


@router.get('', response_model=ReadingNotePage)
def read_reading_note(scope: ReadingNoteScope = Depends(), session: Session = Depends(get_session),
                      current_user: User = Depends(get_current_active_user)):
    require_library_resource_access(session, current_user, scope.resource_type, scope.resource_id)
    row = session.get(LibraryReadingNote, note_identity(current_user.id, scope))
    return ReadingNotePage(**scope.model_dump(), content=row.content if row else '',
                           revision=row.revision if row else 0, updated_at=row.updated_at if row else None)


@router.put('', response_model=ReadingNotePage)
def save_reading_note(payload: ReadingNoteWrite, session: Session = Depends(get_session),
                      current_user: User = Depends(get_current_active_user)):
    require_library_resource_access(session, current_user, payload.resource_type, payload.resource_id)
    identity = note_identity(current_user.id, payload)
    now = time.time()
    try:
        if payload.revision == 0:
            session.add(LibraryReadingNote(id=identity, user_id=current_user.id,
                **payload.model_dump(exclude={'revision'}), revision=1, updated_at=now))
        else:
            result = session.execute(update(LibraryReadingNote).where(
                LibraryReadingNote.id == identity, LibraryReadingNote.revision == payload.revision
            ).values(content=payload.content, revision=payload.revision + 1, updated_at=now))
            if result.rowcount != 1:
                session.rollback()
                raise HTTPException(409, '笔记已有更新，请保留草稿并重新读取，避免覆盖其他窗口的修改')
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, '笔记已有更新，请保留草稿并重新读取') from None
    return ReadingNotePage(**payload.model_dump(exclude={'revision'}),
                           revision=payload.revision + 1, updated_at=now)
