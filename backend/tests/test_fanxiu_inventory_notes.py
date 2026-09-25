import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.notes import (
    MissingInventoryNoteTitle,
    get_fanxiu_note_by_id,
    upsert_inventory_item_note,
)
from backend.core.notes.semantics import NOTE_KIND_FANXIU_WARDROBE_ITEM
from backend.models import NoteNode, User
from backend.schemas import NoteUpdate


def test_inventory_update_resolves_alias_and_preserves_unspecified_fields():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            owner = User(id=1, username="owner", hashed_password="x")
            note = NoteNode(
                id="item", numeric_id=101, legacy_id="old-item", user_id=1,
                note_kind=NOTE_KIND_FANXIU_WARDROBE_ITEM, title="旧名称", weight=9,
                content="保留内容", custom_fields=[["来源", "string", "原始"]],
            )
            session.add_all([owner, note])
            session.commit()
            assert get_fanxiu_note_by_id(session, owner, "old-item", NOTE_KIND_FANXIU_WARDROBE_ITEM) is note
            assert get_fanxiu_note_by_id(session, owner, "101", "wrong-kind") is None
            outsider = User(id=2, username="outsider", hashed_password="x")
            assert get_fanxiu_note_by_id(session, outsider, "101", NOTE_KIND_FANXIU_WARDROBE_ITEM) is None

            result = upsert_inventory_item_note(
                session, owner, {"note_id": "101", "name": "新名称", "rank": 1},
                NoteUpdate(), note_kind=NOTE_KIND_FANXIU_WARDROBE_ITEM,
                fallback_type="memo", item_start_at=123, sync_weight=False,
            )
            assert result.title == "新名称" and result.start_at == 123
            assert result.weight == 9 and result.content == "保留内容"
            assert result.custom_fields == [["来源", "string", "原始"]]

            with pytest.raises(MissingInventoryNoteTitle, match="名称缺失"):
                upsert_inventory_item_note(
                    session, owner, {"note_id": "101", "name": " "},
                    NoteUpdate(content="不应保存"), note_kind=NOTE_KIND_FANXIU_WARDROBE_ITEM,
                    fallback_type="memo", title_error_message="名称缺失",
                )
            session.refresh(note)
            assert note.content == "保留内容"

            from fastapi import HTTPException
            from backend.api.fanxiu import _upsert_fanxiu_inventory_item_note

            with pytest.raises(HTTPException) as error:
                _upsert_fanxiu_inventory_item_note(
                    session, owner, {"note_id": "101", "name": " "},
                    NoteUpdate(), note_kind=NOTE_KIND_FANXIU_WARDROBE_ITEM,
                    fallback_type="memo", title_error_message="名称缺失",
                )
            assert error.value.status_code == 400
            assert error.value.detail == "名称缺失"
    finally:
        engine.dispose()
