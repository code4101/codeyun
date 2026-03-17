from backend.migrations.manager import v16_migrate_note_weight_levels
from backend.models import NoteNode


def make_note(note_id: str, weight: int, node_type: str = "note") -> NoteNode:
    return NoteNode(
        id=note_id,
        user_id=1,
        title=note_id,
        content="",
        weight=weight,
        node_type=node_type,
        node_status="idea",
        private_level=0,
        custom_fields=[],
        created_at=100.0,
        updated_at=100.0,
        start_at=100.0,
        history=[],
    )


def test_v16_migrates_legacy_note_weights_but_keeps_memo_weights(session):
    notes = [
        make_note("note-50", 50),
        make_note("note-100", 100),
        make_note("note-200", 200),
        make_note("note-300", 300),
        make_note("memo-3", 3, node_type="memo"),
    ]
    for note in notes:
        session.add(note)
    session.commit()

    v16_migrate_note_weight_levels(session)

    migrated = {
        note.id: session.get(NoteNode, note.id).weight
        for note in notes
    }

    assert migrated["note-50"] == 0
    assert migrated["note-100"] == 0
    assert migrated["note-200"] == 1
    assert migrated["note-300"] == 2
    assert migrated["memo-3"] == 3
