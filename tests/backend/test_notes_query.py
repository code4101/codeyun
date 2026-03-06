import uuid

from backend.models import NoteEdge, NoteNode


def make_note(
    auth_user,
    note_id: str,
    title: str,
    *,
    start_at: float,
    updated_at: float,
    weight: int = 100,
    node_type: str = "note",
    node_status: str = "idea",
    private_level: int = 0,
    custom_fields=None,
):
    return NoteNode(
        id=note_id,
        user_id=auth_user.id,
        title=title,
        content="",
        weight=weight,
        node_type=node_type,
        node_status=node_status,
        private_level=private_level,
        custom_fields=custom_fields or [],
        created_at=start_at,
        updated_at=updated_at,
        start_at=start_at,
        history=[],
    )


def make_edge(auth_user, source_id: str, target_id: str):
    return NoteEdge(
        id=str(uuid.uuid4()),
        user_id=auth_user.id,
        source_id=source_id,
        target_id=target_id,
        created_at=100.0,
    )


def test_query_notes_filters_and_sorts_all_scope(client, session, auth_user):
    early = make_note(auth_user, "note-early", "Alpha", start_at=100.0, updated_at=200.0)
    target = make_note(
        auth_user,
        "note-target",
        "Beta",
        start_at=200.0,
        updated_at=400.0,
        weight=120,
        custom_fields=[["topic", "string", "project"]],
    )
    late = make_note(auth_user, "note-late", "Gamma", start_at=300.0, updated_at=300.0)

    session.add(early)
    session.add(target)
    session.add(late)
    session.commit()

    response = client.post(
        "/api/notes/query",
        json={
            "scope": {"mode": "all"},
            "rules": [
                {"field": "start_at", "op": "between", "values": [150.0, 250.0]},
                {"field": "updated_at", "op": "gte", "value": 350.0},
            ],
            "order_by": "updated_at",
            "order_desc": True,
            "limit": 10,
            "include_edges": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_nodes"] == 1
    assert payload["total_edges"] == 0
    assert [node["id"] for node in payload["nodes"]] == ["note-target"]


def test_query_notes_supports_custom_field_rules(client, session, auth_user):
    project_note = make_note(
        auth_user,
        "note-project",
        "Project",
        start_at=100.0,
        updated_at=200.0,
        custom_fields=[["topic", "string", "project"]],
    )
    ops_note = make_note(
        auth_user,
        "note-ops",
        "Ops",
        start_at=100.0,
        updated_at=300.0,
        custom_fields=[["topic", "string", "ops"]],
    )

    session.add(project_note)
    session.add(ops_note)
    session.commit()

    response = client.post(
        "/api/notes/query",
        json={
            "scope": {"mode": "all"},
            "rules": [
                {"field": "custom_fields.topic", "op": "eq", "value": "project"},
            ],
            "order_by": "updated_at",
            "order_desc": True,
            "limit": 10,
            "include_edges": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_nodes"] == 1
    assert payload["total_edges"] == 0
    assert [node["id"] for node in payload["nodes"]] == ["note-project"]


def test_query_notes_supports_private_level_rules(client, session, auth_user):
    public_note = make_note(
        auth_user,
        "note-public",
        "Public",
        start_at=100.0,
        updated_at=200.0,
        private_level=0,
    )
    private_note = make_note(
        auth_user,
        "note-private",
        "Private",
        start_at=100.0,
        updated_at=300.0,
        private_level=2,
    )

    session.add(public_note)
    session.add(private_note)
    session.commit()

    response = client.post(
        "/api/notes/query",
        json={
            "scope": {"mode": "all"},
            "rules": [
                {"field": "private_level", "op": "gte", "value": 1},
            ],
            "order_by": "updated_at",
            "order_desc": True,
            "limit": 10,
            "include_edges": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_nodes"] == 1
    assert [node["id"] for node in payload["nodes"]] == ["note-private"]


def test_query_notes_supports_title_text_rules(client, session, auth_user):
    session.add(make_note(auth_user, "note-dash", "-", start_at=100.0, updated_at=100.0))
    session.add(make_note(auth_user, "note-double", "--", start_at=100.0, updated_at=200.0))
    session.add(make_note(auth_user, "note-clean", "Task", start_at=100.0, updated_at=300.0))
    session.commit()

    regex_response = client.post(
        "/api/notes/query",
        json={
            "scope": {"mode": "all"},
            "rules": [
                {"field": "title", "op": "regex_search", "value": "^-$"},
            ],
            "order_by": "updated_at",
            "order_desc": False,
            "limit": 10,
            "include_edges": False,
        },
    )

    assert regex_response.status_code == 200
    regex_payload = regex_response.json()
    assert regex_payload["total_nodes"] == 1
    assert [node["id"] for node in regex_payload["nodes"]] == ["note-dash"]

    not_contains_response = client.post(
        "/api/notes/query",
        json={
            "scope": {"mode": "all"},
            "rules": [
                {"field": "title", "op": "not_contains", "value": "-"},
            ],
            "order_by": "updated_at",
            "order_desc": False,
            "limit": 10,
            "include_edges": False,
        },
    )

    assert not_contains_response.status_code == 200
    not_contains_payload = not_contains_response.json()
    assert not_contains_payload["total_nodes"] == 1
    assert [node["id"] for node in not_contains_payload["nodes"]] == ["note-clean"]


def test_query_notes_graph_scopes_follow_planetary_and_satellite_rules(client, session, auth_user):
    root = make_note(auth_user, "note-root", "Root", start_at=100.0, updated_at=100.0)
    parent = make_note(auth_user, "note-parent", "Parent", start_at=100.0, updated_at=200.0)
    child = make_note(auth_user, "note-child", "Child", start_at=100.0, updated_at=300.0)
    isolated = make_note(auth_user, "note-isolated", "Isolated", start_at=100.0, updated_at=400.0)

    session.add(root)
    session.add(parent)
    session.add(child)
    session.add(isolated)
    session.add(make_edge(auth_user, "note-parent", "note-root"))
    session.add(make_edge(auth_user, "note-root", "note-child"))
    session.commit()

    planetary = client.post(
        "/api/notes/query",
        json={
            "scope": {"mode": "planetary", "seed_note_id": "note-root"},
            "rules": [],
            "order_by": "updated_at",
            "order_desc": True,
            "limit": 10,
            "include_edges": True,
        },
    )

    assert planetary.status_code == 200
    planetary_payload = planetary.json()
    assert {node["id"] for node in planetary_payload["nodes"]} == {
        "note-root",
        "note-parent",
        "note-child",
    }
    assert len(planetary_payload["edges"]) == 2

    satellite = client.post(
        "/api/notes/query",
        json={
            "scope": {"mode": "satellite", "seed_note_id": "note-root"},
            "rules": [],
            "order_by": "updated_at",
            "order_desc": True,
            "limit": 10,
            "include_edges": True,
        },
    )

    assert satellite.status_code == 200
    satellite_payload = satellite.json()
    assert {node["id"] for node in satellite_payload["nodes"]} == {
        "note-root",
        "note-child",
    }
    assert len(satellite_payload["edges"]) == 1
    edge = satellite_payload["edges"][0]
    assert edge["source_id"] == "note-root"
    assert edge["target_id"] == "note-child"


def test_batch_update_notes_sets_private_level(client, session, auth_user):
    first = make_note(auth_user, "note-a", "A", start_at=100.0, updated_at=100.0)
    second = make_note(auth_user, "note-b", "B", start_at=100.0, updated_at=200.0)
    untouched = make_note(auth_user, "note-c", "C", start_at=100.0, updated_at=300.0)

    session.add(first)
    session.add(second)
    session.add(untouched)
    session.commit()

    response = client.post(
        "/api/notes/batch-update",
        json={
            "ids": ["note-a", "note-b"],
            "patch": {"private_level": 1},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["updated_count"] == 2
    assert [note["id"] for note in payload["notes"]] == ["note-a", "note-b"]
    assert [note["private_level"] for note in payload["notes"]] == [1, 1]

    session.refresh(first)
    session.refresh(second)
    session.refresh(untouched)
    assert first.private_level == 1
    assert second.private_level == 1
    assert untouched.private_level == 0
