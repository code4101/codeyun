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
