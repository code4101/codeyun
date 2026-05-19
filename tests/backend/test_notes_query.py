import uuid

from backend.models import NoteEdge, NoteNode


def _numeric_note_id(note_id: str) -> int:
    return sum((index + 1) * ord(char) for index, char in enumerate(note_id)) % 1000000 + 1000


def make_note(
    auth_user,
    note_id: str,
    title: str,
    *,
    start_at: float,
    updated_at: float,
    weight: int = 0,
    node_type: str = "note",
    node_status: str = "idea",
    private_level: int = 0,
    custom_fields=None,
):
    return NoteNode(
        id=note_id,
        numeric_id=_numeric_note_id(note_id),
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
    assert [node["id"] for node in payload["nodes"]] == [target.numeric_id]


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
    assert [node["id"] for node in payload["nodes"]] == [project_note.numeric_id]


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
    assert [node["id"] for node in payload["nodes"]] == [private_note.numeric_id]


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
    assert [node["id"] for node in regex_payload["nodes"]] == [_numeric_note_id("note-dash")]

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
    assert [node["id"] for node in not_contains_payload["nodes"]] == [_numeric_note_id("note-clean")]


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
            "scope": {"mode": "planetary", "seed_note_id": root.numeric_id},
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
        root.numeric_id,
        parent.numeric_id,
        child.numeric_id,
    }
    assert len(planetary_payload["edges"]) == 2

    satellite = client.post(
        "/api/notes/query",
        json={
            "scope": {"mode": "satellite", "seed_note_id": root.numeric_id},
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
        root.numeric_id,
        child.numeric_id,
    }
    assert len(satellite_payload["edges"]) == 1
    edge = satellite_payload["edges"][0]
    assert edge["source_id"] == root.numeric_id
    assert edge["target_id"] == child.numeric_id


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
            "ids": [first.numeric_id, second.numeric_id],
            "patch": {"private_level": 1},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["updated_count"] == 2
    assert [note["id"] for note in payload["notes"]] == [first.numeric_id, second.numeric_id]
    assert [note["private_level"] for note in payload["notes"]] == [1, 1]

    session.refresh(first)
    session.refresh(second)
    session.refresh(untouched)
    assert first.private_level == 1
    assert second.private_level == 1
    assert untouched.private_level == 0


def test_batch_update_notes_supports_taxonomy_and_weight_delta(client, session, auth_user):
    first = make_note(auth_user, "note-batch-a", "A", start_at=100.0, updated_at=100.0, weight=0, node_type="task", node_status="idea")
    second = make_note(auth_user, "note-batch-b", "B", start_at=100.0, updated_at=200.0, weight=4, node_type="bug", node_status="todo")

    first.note_types = [{"key": "task", "weight": 100}]
    first.note_categories = [{"key": "task", "weight": 100}]
    first.primary_category = "task"
    first.note_form = "note"
    first.note_scene = "note"
    first.lifecycle_stage = "idea"

    second.note_types = [{"key": "bug", "weight": 100}]
    second.note_categories = [{"key": "bug", "weight": 100}]
    second.primary_category = "bug"
    second.note_form = "note"
    second.note_scene = "note"
    second.lifecycle_stage = "todo"

    session.add(first)
    session.add(second)
    session.commit()

    response = client.post(
        "/api/notes/batch-update",
        json={
            "ids": [first.numeric_id, second.numeric_id],
            "patch": {
                "note_categories": [{"key": "general", "weight": 100}],
                "primary_category": "general",
                "note_form": "document",
                "lifecycle_stage": "doing",
                "private_level": 2,
                "weight_delta": 3,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["updated_count"] == 2
    assert [note["id"] for note in payload["notes"]] == [first.numeric_id, second.numeric_id]
    assert [note["weight"] for note in payload["notes"]] == [3, 7]
    for note in payload["notes"]:
        assert note["note_categories"] == [{"key": "general", "weight": 100}]
        assert note["primary_category"] == "general"
        assert note["note_form"] == "document"
        assert note["lifecycle_stage"] == "doing"
        assert note["private_level"] == 2

    session.refresh(first)
    session.refresh(second)
    for note in (first, second):
        assert note.weight in {3, 7}
        assert note.private_level == 2
        assert note.note_categories == [{"key": "general", "weight": 100}]
        assert note.primary_category == "general"
        assert note.note_form == "document"
        assert note.lifecycle_stage == "doing"
        assert note.note_types == [{"key": "doc", "weight": 100}]
        assert note.node_type == "doc"
        assert note.node_status == "doing"
