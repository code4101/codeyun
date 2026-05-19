from sqlmodel import select

from backend.core.guest_notes import GUEST_NOTES_USERNAME, RUANYF_WEEKLY_ISSUE_FIELD
from backend.models import NoteNode, User


def test_anonymous_notes_use_shared_guest_workspace(client, session):
    response = client.get("/api/notes/")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) >= 8
    assert all(item["can_edit"] is True for item in payload)

    guest_user = session.exec(select(User).where(User.username == GUEST_NOTES_USERNAME)).one()
    guest_notes = session.exec(select(NoteNode).where(NoteNode.user_id == guest_user.id)).all()

    assert guest_user.is_active is True
    assert len(guest_notes) >= 8
    assert any(note.title.startswith("科技爱好者周刊（第 300 期）") for note in guest_notes)


def test_anonymous_can_create_and_update_guest_note(client, session):
    created = client.post(
        "/api/notes/",
        json={"title": "游客新节点", "content": "<p>公开可编辑</p>"},
    )

    assert created.status_code == 200
    created_payload = created.json()
    assert created_payload["title"] == "游客新节点"
    assert created_payload["can_edit"] is True

    updated = client.put(
        f"/api/notes/{created_payload['id']}",
        json={"title": "游客已编辑节点"},
    )

    assert updated.status_code == 200
    assert updated.json()["title"] == "游客已编辑节点"

    guest_user = session.exec(select(User).where(User.username == GUEST_NOTES_USERNAME)).one()
    saved_note = session.exec(select(NoteNode).where(NoteNode.numeric_id == int(created_payload["id"]))).first()

    assert saved_note is not None
    assert saved_note.user_id == guest_user.id


def test_anonymous_query_program_reads_guest_seed_graph(client):
    response = client.post(
        "/api/notes/query-program",
        json={
            "executor": {"kind": "scan"},
            "program": {
                "select": {
                    "default": False,
                    "rules": [{"action": "include", "matcher": {"kind": "all"}}],
                },
                "expand": {"default": False, "rules": []},
            },
            "result": {
                "include_edges": True,
                "order_by": "updated_at",
                "order_desc": False,
                "skip": 0,
                "limit": 20,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_nodes"] >= 8
    assert payload["total_edges"] >= 7


def test_anonymous_guest_seed_contains_public_weekly_note(client, session):
    response = client.get("/api/notes/")

    assert response.status_code == 200

    guest_user = session.exec(select(User).where(User.username == GUEST_NOTES_USERNAME)).one()
    weekly_note = session.get(NoteNode, "guest-star-notes-weekly-issue-300")

    assert weekly_note is not None
    assert weekly_note.user_id == guest_user.id
    assert weekly_note.title == "科技爱好者周刊（第 300 期）：三十年，解决人生三大问题"
    assert [RUANYF_WEEKLY_ISSUE_FIELD, "number", 300] in weekly_note.custom_fields
