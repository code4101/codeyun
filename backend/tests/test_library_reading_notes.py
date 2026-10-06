import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
from backend.api import library_reading_notes as api
from backend.models import User, LibraryBookAsset, LibraryReadingNote


def test_pages_are_independent_and_old_revisions_never_replace_newer_content():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    SQLModel.metadata.create_all(engine,tables=[User.__table__,LibraryBookAsset.__table__,LibraryReadingNote.__table__])
    with Session(engine) as session:
        owner=User(username='reader',hashed_password='test')
        other=User(username='outsider',hashed_password='test')
        session.add_all([owner,other]);session.commit();session.refresh(owner);session.refresh(other)
        for book_id in ('book-a','book-b'):
            session.add(LibraryBookAsset(id=book_id,owner_user_id=owner.id,source_kind=book_id,title=book_id))
        session.commit()
        scope=api.ReadingNoteScope(resource_id='book-a',chapter_id='chapter')
        assert api.read_reading_note(scope,session,owner).revision==0
        assert not session.exec(select(LibraryReadingNote)).all()
        first=api.save_reading_note(api.ReadingNoteWrite(**scope.model_dump(),content='<p>章节笔记</p>'),session,owner)
        for chapter in ('','parent'):
            api.save_reading_note(api.ReadingNoteWrite(resource_id='book-a',chapter_id=chapter,content=chapter or '整本书'),session,owner)
        assert api.read_reading_note(scope,session,owner).content=='<p>章节笔记</p>'
        assert api.read_reading_note(api.ReadingNoteScope(resource_id='book-b',chapter_id='chapter'),session,owner).content==''
        with pytest.raises(HTTPException) as denied: api.read_reading_note(scope,session,other)
        assert denied.value.status_code==404
        api.save_reading_note(api.ReadingNoteWrite(**scope.model_dump(),content='新内容',revision=first.revision),session,owner)
        for revision in (0,first.revision):
            with pytest.raises(HTTPException) as stale:
                api.save_reading_note(api.ReadingNoteWrite(**scope.model_dump(),content='旧内容',revision=revision),session,owner)
            assert stale.value.status_code==409
        assert api.read_reading_note(scope,session,owner).content=='新内容'
