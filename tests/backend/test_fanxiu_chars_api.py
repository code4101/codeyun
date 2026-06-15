import uuid

from sqlmodel import select

from backend.app import app
from backend.api.fanxiu import FANXIU_CHAR_KIND, FANXIU_CHAR_TYPE, get_fanxiu_user
from backend.core.access.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.core.notes.semantics import NOTE_WEIGHT_MODE_LINEAR
from backend.models import NoteEdge, NoteNode


def _override_user(user):
    app.dependency_overrides[get_current_user_from_token] = lambda: user
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: user


def _clear_user_override():
    app.dependency_overrides.pop(get_current_user_from_token, None)
    app.dependency_overrides.pop(get_optional_current_user_from_token, None)


def test_fanxiu_char_put_returns_valid_note_read(client, session):
    fanxiu_user = get_fanxiu_user(session)
    _override_user(fanxiu_user)

    try:
        response = client.put(
            "/api/fanxiu/chars/银月",
            json={
                "title": "银月",
                "content": "",
                "weight": 0,
                "note_types": [{"key": "memo", "weight": 100}],
                "node_type": "memo",
                "note_kind": "fanxiu_char",
                "node_status": "idea",
                "weight_mode": "linear",
            },
        )
    finally:
        _clear_user_override()

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "银月"
    assert payload["custom_fields"] == []
    assert payload["history"] == []
    created_note = session.exec(select(NoteNode).where(NoteNode.title == "银月")).one()
    assert payload["id"] == created_note.numeric_id
    assert created_note.id == str(created_note.numeric_id)
    assert created_note.legacy_id and created_note.legacy_id != created_note.id


def test_fanxiu_chars_list_migrates_legacy_note(client, session):
    fanxiu_user = get_fanxiu_user(session)
    legacy_note = NoteNode(
        id=str(uuid.uuid4()),
        user_id=fanxiu_user.id,
        title="南宫婉",
        content="legacy-content",
        weight=7,
        node_type="note",
        note_kind="note",
        node_status="idea",
        weight_mode=None,
        custom_fields={},
        history=[],
        created_at=100.0,
        updated_at=100.0,
        start_at=100.0,
    )
    session.add(legacy_note)
    session.commit()

    response = client.get("/api/fanxiu/chars")

    assert response.status_code == 200
    payload = response.json()
    row = next(item for item in payload if item["title"] == "南宫婉")
    assert row["content"] == "legacy-content"
    assert row["weight"] == 7
    assert row["note_kind"] == FANXIU_CHAR_KIND
    assert row["custom_fields"] == []

    session.refresh(legacy_note)
    assert legacy_note.note_kind == FANXIU_CHAR_KIND
    assert legacy_note.weight_mode == NOTE_WEIGHT_MODE_LINEAR
    assert legacy_note.custom_fields == []


def test_fanxiu_chars_list_prefers_content_duplicate(client, session):
    fanxiu_user = get_fanxiu_user(session)
    blank_note = NoteNode(
        id=str(uuid.uuid4()),
        user_id=fanxiu_user.id,
        title="大衍神君",
        content="",
        weight=3,
        node_type=FANXIU_CHAR_TYPE,
        note_kind=FANXIU_CHAR_KIND,
        node_status="idea",
        weight_mode=NOTE_WEIGHT_MODE_LINEAR,
        custom_fields=[],
        history=[],
        created_at=100.0,
        updated_at=100.0,
        start_at=100.0,
    )
    content_note = NoteNode(
        id=str(uuid.uuid4()),
        user_id=fanxiu_user.id,
        title="大衍神君",
        content='<p><img src="/static/attachments/demo.png"/></p>',
        weight=0,
        node_type=FANXIU_CHAR_TYPE,
        note_kind=FANXIU_CHAR_KIND,
        node_status="idea",
        weight_mode=NOTE_WEIGHT_MODE_LINEAR,
        custom_fields=[],
        history=[],
        created_at=200.0,
        updated_at=200.0,
        start_at=200.0,
    )
    session.add(blank_note)
    session.add(content_note)
    session.commit()

    response = client.get("/api/fanxiu/chars")

    assert response.status_code == 200
    payload = response.json()
    row = next(item for item in payload if item["title"] == "大衍神君")
    assert row["id"] == content_note.id
    assert "demo.png" in row["content"]
    assert row["weight"] == 3

    remaining_notes = session.exec(
        select(NoteNode).where(
            NoteNode.user_id == fanxiu_user.id,
            NoteNode.title == "大衍神君",
            NoteNode.note_kind == FANXIU_CHAR_KIND,
        )
    ).all()
    assert [note.id for note in remaining_notes] == [content_note.id]


def test_fanxiu_chars_list_retargets_edges_when_merging_duplicate(client, session):
    fanxiu_user = get_fanxiu_user(session)
    duplicate_note = NoteNode(
        id=str(uuid.uuid4()),
        user_id=fanxiu_user.id,
        title="凌玉灵",
        content="",
        weight=0,
        node_type=FANXIU_CHAR_TYPE,
        note_kind=FANXIU_CHAR_KIND,
        node_status="idea",
        weight_mode=NOTE_WEIGHT_MODE_LINEAR,
        custom_fields=[],
        history=[],
        created_at=100.0,
        updated_at=100.0,
        start_at=100.0,
    )
    canonical_note = NoteNode(
        id=str(uuid.uuid4()),
        user_id=fanxiu_user.id,
        title="凌玉灵",
        content="canonical",
        weight=0,
        node_type=FANXIU_CHAR_TYPE,
        note_kind=FANXIU_CHAR_KIND,
        node_status="idea",
        weight_mode=NOTE_WEIGHT_MODE_LINEAR,
        custom_fields=[],
        history=[],
        created_at=200.0,
        updated_at=200.0,
        start_at=200.0,
    )
    target_note = NoteNode(
        id=str(uuid.uuid4()),
        user_id=fanxiu_user.id,
        title="关联目标",
        content="target",
        weight=0,
        node_type="note",
        note_kind="note",
        node_status="idea",
        custom_fields=[],
        history=[],
        created_at=300.0,
        updated_at=300.0,
        start_at=300.0,
    )
    edge = NoteEdge(
        id=str(uuid.uuid4()),
        user_id=fanxiu_user.id,
        source_id=duplicate_note.id,
        target_id=target_note.id,
        label="rel",
        created_at=100.0,
    )
    session.add(duplicate_note)
    session.add(canonical_note)
    session.add(target_note)
    session.add(edge)
    session.commit()

    response = client.get("/api/fanxiu/chars")

    assert response.status_code == 200
    session.refresh(edge)
    assert edge.source_id == canonical_note.id
    assert edge.target_id == target_note.id
    assert session.get(NoteNode, duplicate_note.id) is None


def test_fanxiu_read_char_merges_legacy_data_into_existing_stub(client, session):
    fanxiu_user = get_fanxiu_user(session)
    stub_note = NoteNode(
        id=str(uuid.uuid4()),
        user_id=fanxiu_user.id,
        title="黄泉鬼母",
        content="",
        weight=0,
        node_type=FANXIU_CHAR_TYPE,
        note_kind=FANXIU_CHAR_KIND,
        node_status="idea",
        weight_mode=NOTE_WEIGHT_MODE_LINEAR,
        custom_fields=[],
        history=[],
        created_at=200.0,
        updated_at=200.0,
        start_at=200.0,
    )
    legacy_note = NoteNode(
        id=str(uuid.uuid4()),
        user_id=fanxiu_user.id,
        title="黄泉鬼母",
        content="legacy-note",
        weight=9,
        node_type="note",
        note_kind="note",
        node_status="idea",
        weight_mode=None,
        custom_fields={},
        history=[],
        created_at=100.0,
        updated_at=100.0,
        start_at=100.0,
    )
    session.add(stub_note)
    session.add(legacy_note)
    session.commit()

    response = client.get("/api/fanxiu/chars/黄泉鬼母")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == stub_note.id
    assert payload["content"] == "legacy-note"
    assert payload["weight"] == 9
    assert payload["custom_fields"] == []

    session.refresh(stub_note)
    assert stub_note.content == "legacy-note"
    assert stub_note.weight == 9
