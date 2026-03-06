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
