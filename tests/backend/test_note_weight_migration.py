from backend.migrations.manager import v16_migrate_note_weight_levels, v17_add_note_semantics_fields, v18_add_note_types, v19_add_note_taxonomy_fields, v20_repair_note_category_drift, v21_merge_predone_into_done
from backend.models import AppSetting, NoteNode


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


def test_v17_backfills_note_kind_and_weight_mode_from_legacy_node_type(session):
    note = make_note("memo-legacy", 3, node_type="memo")
    session.add(note)
    session.commit()

    v17_add_note_semantics_fields(session)

    migrated = session.get(NoteNode, note.id)
    assert migrated is not None
    assert migrated.note_kind == "note"
    assert migrated.weight_mode == "linear"


def test_v18_backfills_note_types_from_legacy_node_type(session):
    note = make_note("note-legacy-type", 0, node_type="doc")
    session.add(note)
    session.commit()

    v18_add_note_types(session)

    migrated = session.get(NoteNode, note.id)
    assert migrated is not None
    assert migrated.note_types == [{"key": "doc", "weight": 100}]


def test_v19_backfills_note_taxonomy_from_legacy_fields(session):
    note = make_note("note-taxonomy", 0, node_type="doc")
    note.note_types = [{"key": "doc", "weight": 100}]
    note.note_kind = "note"
    note.node_status = "doing"
    session.add(note)
    session.commit()

    v19_add_note_taxonomy_fields(session)

    migrated = session.get(NoteNode, note.id)
    assert migrated is not None
    assert migrated.note_categories == [{"key": "general", "weight": 100}]
    assert migrated.primary_category == "general"
    assert migrated.note_form == "document"
    assert migrated.note_scene == "note"
    assert migrated.lifecycle_stage == "doing"


def test_v20_repairs_primary_category_drift_and_general_label(session):
    note = make_note("note-category-drift", 0, node_type="task")
    note.note_types = [{"key": "task", "weight": 100}]
    note.note_categories = [{"key": "task", "weight": 100}]
    note.primary_category = "general"
    note.note_form = "note"
    note.note_scene = "note"
    note.lifecycle_stage = "todo"
    session.add(note)

    palette = AppSetting(
        key="note.category_palette.user.1",
        value={
            "items": [
                {"key": "general", "label": "笔记", "color": "#606266", "order": 0, "builtin": True, "source": "builtin"},
                {"key": "task", "label": "任务", "color": "#409EFF", "order": 10, "builtin": True, "source": "builtin"},
            ]
        },
        updated_at=100.0,
    )
    session.add(palette)
    session.commit()

    v20_repair_note_category_drift(session)

    migrated = session.get(NoteNode, note.id)
    assert migrated is not None
    assert migrated.primary_category == "task"
    assert migrated.node_type == "task"

    repaired_palette = session.get(AppSetting, "note.category_palette.user.1")
    assert repaired_palette is not None
    assert repaired_palette.value["items"][0]["label"] == "综合"


def test_v21_merges_predone_into_done(session):
    note = make_note("note-predone", 0, node_type="task")
    note.node_status = "predone"
    note.lifecycle_stage = "predone"
    session.add(note)
    session.commit()

    v21_merge_predone_into_done(session)

    migrated = session.get(NoteNode, note.id)
    assert migrated is not None
    assert migrated.node_status == "done"
    assert migrated.lifecycle_stage == "done"
