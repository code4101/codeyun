from backend.core.notes.access import (
    note_list_mapping_to_response_dict,
    note_to_list_response_dict,
    note_to_response_dict,
)
from backend.models import NoteNode, User


def test_note_list_response_omits_history_but_detail_keeps_it():
    user = User(id=1, username="alice", hashed_password="x")
    note = NoteNode(
        id="1",
        numeric_id=1,
        user_id=1,
        title="Daily note",
        history=[{"ts": 1710000000, "f": "title", "v": "Daily note"}],
    )

    summary = note_to_list_response_dict(note, user)
    detail = note_to_response_dict(note, user)

    assert "history" not in summary
    assert detail["history"] == [{"ts": 1710000000, "f": "title", "v": "Daily note"}]


def test_note_list_mapping_response_matches_model_summary():
    user = User(id=1, username="alice", hashed_password="x")
    note = NoteNode(
        id="uuid-1",
        numeric_id=7,
        user_id=1,
        title="Mapped note",
        weight=2,
        custom_fields=[["source_kind", "string", "chapter"]],
        start_at=1710000000,
        created_at=1710000000,
        updated_at=1710000100,
    )

    mapping = {
        "id": note.id,
        "numeric_id": note.numeric_id,
        "user_id": note.user_id,
        "title": note.title,
        "weight": note.weight,
        "node_type": note.node_type,
        "note_types": note.note_types,
        "note_categories": note.note_categories,
        "primary_category": note.primary_category,
        "note_form": note.note_form,
        "note_kind": note.note_kind,
        "note_scene": note.note_scene,
        "node_status": note.node_status,
        "lifecycle_stage": note.lifecycle_stage,
        "color": note.color,
        "weight_mode": note.weight_mode,
        "private_level": note.private_level,
        "custom_fields": note.custom_fields,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
        "start_at": note.start_at,
        "deleted_at": note.deleted_at,
        "deleted_by_user_id": note.deleted_by_user_id,
    }

    assert note_list_mapping_to_response_dict(mapping, user) == note_to_list_response_dict(note, user)
