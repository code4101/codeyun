from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.notes import FANXIU_CHAR_KIND, get_or_migrate_fanxiu_char_note
from backend.models import NoteEdge, NoteNode, User


def test_character_migration_preserves_data_edges_and_is_repeatable():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            owner = User(id=1, username="notes-owner", hashed_password="x")
            current = NoteNode(
                id="current", numeric_id=101, user_id=1, title="韩立",
                note_kind=FANXIU_CHAR_KIND, content="当前内容",
                custom_fields=[["保留", "string", "新值"]],
            )
            legacy = NoteNode(
                id="legacy", numeric_id=102, user_id=1, title="韩立",
                content="旧内容", weight=5,
                custom_fields=[["保留", "string", "旧值"], ["补充", "number", 3]],
                history=[{"ts": 1, "f": "content", "v": "旧内容"}],
            )
            other = NoteNode(id="other", numeric_id=103, user_id=1, title="其他")
            session.add_all([owner, current, legacy, other])
            session.add(NoteEdge(id="edge", user_id=1, source_id="legacy", target_id="103", label="关联"))
            session.commit()

            migrated = get_or_migrate_fanxiu_char_note(session, owner, "韩立")
            assert migrated.id == "current"
            # 迁移只暂存，调用方能够撤销整次合并。
            session.rollback()
            assert session.get(NoteNode, "legacy") is not None

            migrated = get_or_migrate_fanxiu_char_note(session, owner, "韩立")
            session.commit()
            assert session.get(NoteNode, "legacy") is None
            assert "当前内容" in migrated.content and "旧内容" in migrated.content
            assert migrated.custom_fields == [["保留", "string", "新值"], ["补充", "number", 3]]
            assert migrated.weight == 5
            assert len(migrated.history) == 1
            edge = session.exec(select(NoteEdge)).one()
            assert (edge.source_id, edge.target_id) == ("101", "103")
            content = migrated.content
            timestamp = migrated.updated_at

            repeated = get_or_migrate_fanxiu_char_note(session, owner, "韩立")
            session.commit()
            assert repeated.content == content
            assert repeated.updated_at == timestamp
            assert len(session.exec(select(NoteEdge)).all()) == 1
    finally:
        engine.dispose()


def test_character_read_merges_only_detached_view_and_update_performs_migration():
    from sqlalchemy import inspect
    from backend.core.fanxiu.notes import read_character_note, upsert_character_note
    from backend.schemas import NoteUpdate

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            owner = User(id=1, username="owner", hashed_password="x")
            current = NoteNode(id="current", numeric_id=101, user_id=1, title="韩立",
                               note_kind=FANXIU_CHAR_KIND, content="当前", updated_at=100)
            legacy = NoteNode(id="legacy", numeric_id=102, user_id=1, title="韩立",
                              content="旧内容", weight=5, updated_at=200,
                              custom_fields=[["补充", "number", 3]])
            other = NoteNode(id="other", numeric_id=103, user_id=1, title="其他")
            session.add_all([owner, current, legacy, other])
            session.add(NoteEdge(id="edge", user_id=1, source_id="legacy", target_id="103", label="关联"))
            session.commit()
            before = {note.id: note.model_dump() for note in session.exec(select(NoteNode)).all()}
            view = read_character_note(session, owner, "韩立")
            assert inspect(view).transient
            assert "当前" in view.content and "旧内容" in view.content
            assert view.custom_fields == [["补充", "number", 3]]
            assert view.updated_at == 200
            assert read_character_note(session, owner, "韩立").model_dump() == view.model_dump()
            assert not session.new and not session.dirty and not session.deleted
            session.expire_all()
            assert {note.id: note.model_dump() for note in session.exec(select(NoteNode)).all()} == before
            assert session.get(NoteEdge, "edge").source_id == "legacy"

            updated = upsert_character_note(session, owner, "韩立", NoteUpdate(content="显式编辑"))
            assert updated.id == "current" and updated.content == "显式编辑"
            assert session.get(NoteNode, "legacy") is None
            assert session.get(NoteEdge, "edge").source_id == "101"
    finally:
        engine.dispose()


def test_character_get_does_not_create_owner_or_synchronize_credentials():
    import pytest
    from fastapi import HTTPException
    from backend.api.fanxiu_access import FANXIU_USERNAME
    from backend.api.fanxiu_notes import read_char, read_chars, XIANZHOU_RACE_CHAR_NAMES

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(User(id=1, username="code4101", hashed_password="admin-hash", password_plain="admin-plain"))
            session.commit()
            assert read_chars(current_user=None, session=session) == []
            with pytest.raises(HTTPException) as error:
                read_char("韩立", current_user=None, session=session)
            assert error.value.status_code == 404
            assert len(session.exec(select(User)).all()) == 1

            owner = User(id=2, username=FANXIU_USERNAME, hashed_password="owner-hash", password_plain="owner-plain")
            note = NoteNode(id="legacy", numeric_id=101, user_id=2, title=XIANZHOU_RACE_CHAR_NAMES[0], content="旧笔记")
            session.add_all([owner, note]); session.commit()
            rows = read_chars(current_user=None, session=session)
            assert len(rows) == 1 and rows[0]["content"] == "旧笔记"
            assert not session.new and not session.dirty and not session.deleted
            session.expire_all()
            assert owner.hashed_password == "owner-hash" and owner.password_plain == "owner-plain"
            assert note.note_kind != FANXIU_CHAR_KIND
    finally:
        engine.dispose()
