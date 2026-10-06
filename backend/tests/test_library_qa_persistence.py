import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from backend.api import library_qa as api
from backend.models import User, LibraryBookAsset, LibraryQaConversation


def test_conversations_persist_sources_locations_clear_and_are_personal():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine, tables=[User.__table__, LibraryBookAsset.__table__, LibraryQaConversation.__table__])
    with Session(engine) as session:
        owner = User(username='qa-owner', hashed_password='x')
        outsider = User(username='qa-outsider', hashed_password='x')
        session.add_all([owner, outsider]); session.commit(); session.refresh(owner); session.refresh(outsider)
        session.add_all([LibraryBookAsset(id=book, owner_user_id=owner.id, source_kind=book) for book in ('a', 'b')]); session.commit()
        scope = api.ConversationScope(resource_type='rich-text', resource_id='a')
        empty = api.read_conversation(scope, session, owner)
        assert empty.revision == 0
        assert session.exec(select(LibraryQaConversation)).all() == []
        turns = [api.ConversationTurn(role='user', content='问题', location='第一章'),
                 api.ConversationTurn(role='assistant', content='回答[1]', location='第一章', result=api.ReadingAnswer(
                     answer='回答[1]', model='flash', sources=[api.QaSource(number=1, title='章节', chapter_id='one')],
                     context=api.QaContext(location='第一章', warnings=[], characters=20),
                     web_sources=[{'title': '网页来源', 'url': 'https://example.com'}]))]
        saved = api.save_conversation(api.ConversationWrite(**scope.model_dump(), turns=turns), session, owner)
        owner_id = owner.id
    # 新会话模拟刷新/跨设备读取，完整的来源与位置都恢复。
    with Session(engine) as session:
        owner = session.get(User, owner_id)
        loaded = api.read_conversation(scope, session, owner)
        assert loaded.turns == turns
        assert loaded.turns[1].result.web_sources[0]['title'] == '网页来源'
        assert api.read_conversation(api.ConversationScope(resource_type='rich-text', resource_id='b'), session, owner).turns == []
        with pytest.raises(HTTPException) as conflict:
            api.save_conversation(api.ConversationWrite(**scope.model_dump(), revision=0), session, owner)
        assert conflict.value.status_code == 409
        cleared = api.save_conversation(api.ConversationWrite(**scope.model_dump(), revision=saved.revision), session, owner)
        assert cleared.turns == []
        with pytest.raises(HTTPException) as conflict:
            api.save_conversation(api.ConversationWrite(**scope.model_dump(), turns=turns, revision=saved.revision), session, owner)
        assert conflict.value.status_code == 409
        assert api.read_conversation(scope, session, owner).turns == []
        outsider = session.exec(select(User).where(User.username == 'qa-outsider')).one()
        with pytest.raises(HTTPException) as denied:
            api.read_conversation(scope, session, outsider)
        assert denied.value.status_code == 404
        assert api.conversation_identity(owner.id, scope) != api.conversation_identity(outsider.id, scope)


def test_migration_is_idempotent():
    from backend.migrations.manager import v117_add_library_qa_conversations
    engine = create_engine('sqlite://')
    User.__table__.create(engine)
    with Session(engine) as session:
        v117_add_library_qa_conversations(session)
        v117_add_library_qa_conversations(session)
        assert session.exec(select(LibraryQaConversation)).all() == []
