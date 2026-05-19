import uuid

from backend.app import app
from backend.api.fanxiu import FANXIU_CHAR_KIND, FANXIU_CHAR_TYPE, get_fanxiu_user
from backend.core.note_semantics import NOTE_WEIGHT_MODE_LINEAR
from backend.core.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.models import NoteEdge, NoteNode, User


def _numeric_note_id(note_id: str) -> int:
    return sum((index + 1) * ord(char) for index, char in enumerate(note_id)) % 1000000 + 1000


def make_note(owner: User, note_id: str, title: str) -> NoteNode:
    return NoteNode(
        id=note_id,
        numeric_id=_numeric_note_id(note_id),
        user_id=owner.id,
        title=title,
        content="",
        weight=100,
        node_type="note",
        node_status="idea",
        private_level=0,
        custom_fields=[],
        created_at=100.0,
        updated_at=100.0,
        start_at=100.0,
        history=[],
    )


def test_note_query_payload_marks_owned_notes_as_editable(client, session, auth_user):
    session.add(make_note(auth_user, "note-owned", "Owned"))
    session.commit()

    response = client.post(
        "/api/notes/query",
        json={
            "scope": {"mode": "all"},
            "rules": [],
            "order_by": "updated_at",
            "order_desc": True,
            "limit": 10,
            "include_edges": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["nodes"][0]["can_edit"] is True


def test_superuser_can_update_another_users_note(client, session):
    owner = User(
        username="owner",
        email="owner@example.com",
        hashed_password="pw",
        is_active=True,
        is_superuser=False,
    )
    admin = User(
        username="admin",
        email="admin@example.com",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )
    session.add(owner)
    session.add(admin)
    session.commit()
    session.refresh(owner)
    session.refresh(admin)

    note = make_note(owner, "note-foreign", "Foreign")
    session.add(note)
    session.commit()

    app.dependency_overrides[get_current_user_from_token] = lambda: admin
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: admin
    try:
        response = client.put(
            f"/api/notes/{note.numeric_id}",
            json={"title": "Updated By Admin"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user_from_token, None)
        app.dependency_overrides.pop(get_optional_current_user_from_token, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Updated By Admin"
    assert payload["can_edit"] is True

    session.refresh(note)
    assert note.title == "Updated By Admin"


def test_note_update_serializes_legacy_custom_fields_dict(client, session, auth_user):
    note = make_note(auth_user, "note-legacy-custom-fields", "Legacy Custom Fields")
    note.custom_fields = {"priority": "high", "done": False, "count": 2}
    session.add(note)
    session.commit()

    response = client.put(
        f"/api/notes/{note.numeric_id}",
        json={"content": "updated"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["content"] == "updated"
    assert payload["custom_fields"] == [
        ["priority", "string", "high"],
        ["done", "boolean", False],
        ["count", "number", 2],
    ]


def test_delete_note_removes_connected_edges(client, session, auth_user):
    source = make_note(auth_user, "note-delete-source", "Delete Source")
    target = make_note(auth_user, "note-delete-target", "Delete Target")
    edge = NoteEdge(
        id="edge-delete-source-target",
        user_id=auth_user.id,
        source_id=source.id,
        target_id=target.id,
    )
    session.add(source)
    session.add(target)
    session.add(edge)
    session.commit()

    response = client.delete(f"/api/notes/{source.numeric_id}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert session.get(NoteNode, source.id) is None
    assert session.get(NoteNode, target.id) is not None
    assert session.get(NoteEdge, edge.id) is None


def test_fanxiu_public_read_returns_can_edit_for_current_viewer(client, session):
    fanxiu_user = get_fanxiu_user(session)
    note = NoteNode(
        id=str(uuid.uuid4()),
        user_id=fanxiu_user.id,
        title="天鹏祭司",
        content="demo",
        weight=1,
        node_type=FANXIU_CHAR_TYPE,
        note_kind=FANXIU_CHAR_KIND,
        node_status="idea",
        weight_mode=NOTE_WEIGHT_MODE_LINEAR,
        private_level=0,
        custom_fields=[],
        created_at=100.0,
        updated_at=100.0,
        start_at=100.0,
        history=[],
    )
    session.add(note)
    session.commit()

    anonymous_response = client.get(f"/api/fanxiu/chars/{note.title}")
    assert anonymous_response.status_code == 200
    assert anonymous_response.json()["can_edit"] is False

    app.dependency_overrides[get_optional_current_user_from_token] = lambda: fanxiu_user
    try:
        owner_response = client.get(f"/api/fanxiu/chars/{note.title}")
    finally:
        app.dependency_overrides.pop(get_optional_current_user_from_token, None)

    assert owner_response.status_code == 200
    assert owner_response.json()["can_edit"] is True
