import uuid
from datetime import datetime

from backend.models import NoteEdge, NoteNode


def make_note(
    auth_user,
    note_id: str,
    title: str,
    *,
    start_at: float,
    updated_at: float,
    node_status: str = "idea",
    node_type: str = "note",
    private_level: int = 0,
):
    return NoteNode(
        id=note_id,
        user_id=auth_user.id,
        title=title,
        content="",
        weight=100,
        node_type=node_type,
        node_status=node_status,
        private_level=private_level,
        custom_fields=[],
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


def month_start_timestamp(offset: int) -> float:
    now = datetime.now().astimezone()
    year = now.year
    month = now.month + offset

    while month <= 0:
        year -= 1
        month += 12
    while month > 12:
        year += 1
        month -= 12

    return datetime(year, month, 1, 12, 0, 0, tzinfo=now.tzinfo).timestamp()


def test_query_program_scan_supports_ordered_reinclude(client, session, auth_user):
    session.add(make_note(auth_user, "note-open", "Open", start_at=100.0, updated_at=100.0))
    session.add(make_note(auth_user, "note-done", "Done", start_at=100.0, updated_at=200.0, node_status="done"))
    session.add(make_note(auth_user, "note-rescue", "Rescue", start_at=100.0, updated_at=300.0, node_status="done"))
    session.commit()

    response = client.post(
        "/api/notes/query-program",
        json={
            "executor": {"kind": "scan"},
            "program": {
                "select": {
                    "default": False,
                    "rules": [
                        {"action": "include", "matcher": {"kind": "all"}},
                        {
                            "action": "exclude",
                            "matcher": {"kind": "field", "field": "node_status", "op": "eq", "value": "done"},
                        },
                        {"action": "include", "matcher": {"kind": "id", "ids": ["note-rescue"]}},
                    ],
                },
                "expand": {"default": False, "rules": []},
            },
            "result": {
                "include_edges": False,
                "order_by": "updated_at",
                "order_desc": False,
                "skip": 0,
                "limit": 10,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_nodes"] == 2
    assert [node["id"] for node in payload["nodes"]] == ["note-open", "note-rescue"]


def test_query_program_component_executor_supports_satellite_mode(client, session, auth_user):
    session.add(make_note(auth_user, "note-root", "Root", start_at=100.0, updated_at=100.0))
    session.add(make_note(auth_user, "note-parent", "Parent", start_at=100.0, updated_at=200.0))
    session.add(make_note(auth_user, "note-child", "Child", start_at=100.0, updated_at=300.0))
    session.add(make_note(auth_user, "note-leaf", "Leaf", start_at=100.0, updated_at=400.0))
    session.add(make_edge(auth_user, "note-parent", "note-root"))
    session.add(make_edge(auth_user, "note-root", "note-child"))
    session.add(make_edge(auth_user, "note-child", "note-leaf"))
    session.commit()

    response = client.post(
        "/api/notes/query-program",
        json={
            "executor": {
                "kind": "component",
                "seed_ids": ["note-root"],
                "mode": "satellite",
            },
            "program": {
                "expand": {
                    "default": False,
                    "rules": [{"action": "include", "matcher": {"kind": "all"}}],
                },
                "select": {"default": True, "rules": []},
            },
            "result": {
                "include_edges": True,
                "order_by": "updated_at",
                "order_desc": False,
                "skip": 0,
                "limit": 10,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [node["id"] for node in payload["nodes"]] == ["note-root", "note-child", "note-leaf"]
    assert {(edge["source_id"], edge["target_id"]) for edge in payload["edges"]} == {
        ("note-root", "note-child"),
        ("note-child", "note-leaf"),
    }


def test_query_program_keeps_expand_and_select_as_independent_channels(client, session, auth_user):
    session.add(make_note(auth_user, "note-root", "Root", start_at=100.0, updated_at=100.0))
    session.add(make_note(auth_user, "note-bridge", "Bridge", start_at=100.0, updated_at=200.0))
    session.add(make_note(auth_user, "note-leaf", "Leaf", start_at=100.0, updated_at=300.0))
    session.add(make_edge(auth_user, "note-root", "note-bridge"))
    session.add(make_edge(auth_user, "note-bridge", "note-leaf"))
    session.commit()

    response = client.post(
        "/api/notes/query-program",
        json={
            "executor": {
                "kind": "component",
                "seed_ids": ["note-root"],
                "mode": "planetary",
            },
            "program": {
                "expand": {
                    "default": False,
                    "rules": [{"action": "include", "matcher": {"kind": "all"}}],
                },
                "select": {
                    "default": False,
                    "rules": [
                        {"action": "include", "matcher": {"kind": "seed"}},
                        {"action": "include", "matcher": {"kind": "depth", "min_depth": 1, "max_depth": 1}},
                    ],
                },
            },
            "result": {
                "include_edges": True,
                "order_by": "updated_at",
                "order_desc": False,
                "skip": 0,
                "limit": 10,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [node["id"] for node in payload["nodes"]] == ["note-root", "note-bridge"]
    assert {(edge["source_id"], edge["target_id"]) for edge in payload["edges"]} == {
        ("note-root", "note-bridge"),
    }


def test_query_program_supports_native_relative_month_window_matcher(client, session, auth_user):
    inside = make_note(
        auth_user,
        "note-inside",
        "Inside",
        start_at=month_start_timestamp(0),
        updated_at=100.0,
    )
    outside = make_note(
        auth_user,
        "note-outside",
        "Outside",
        start_at=month_start_timestamp(-3),
        updated_at=200.0,
    )

    session.add(inside)
    session.add(outside)
    session.commit()

    response = client.post(
        "/api/notes/query-program",
        json={
            "executor": {"kind": "scan"},
            "program": {
                "expand": {"default": False, "rules": []},
                "select": {
                    "default": False,
                    "rules": [
                        {
                            "action": "include",
                            "matcher": {
                                "kind": "relative_month_window",
                                "field": "start_at",
                                "start_month_offset": -1,
                                "end_month_offset": 1,
                            },
                        }
                    ],
                },
            },
            "result": {
                "include_edges": False,
                "order_by": "updated_at",
                "order_desc": False,
                "skip": 0,
                "limit": 10,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [node["id"] for node in payload["nodes"]] == ["note-inside"]


def test_query_program_supports_field_between_with_relative_time_values(client, session, auth_user):
    inside = make_note(
        auth_user,
        "note-relative-inside",
        "Inside",
        start_at=month_start_timestamp(0),
        updated_at=100.0,
    )
    outside = make_note(
        auth_user,
        "note-relative-outside",
        "Outside",
        start_at=month_start_timestamp(-4),
        updated_at=200.0,
    )

    session.add(inside)
    session.add(outside)
    session.commit()

    response = client.post(
        "/api/notes/query-program",
        json={
            "executor": {"kind": "scan"},
            "program": {
                "expand": {"default": False, "rules": []},
                "select": {
                    "default": False,
                    "rules": [
                        {
                            "action": "include",
                            "matcher": {
                                "kind": "field",
                                "field": "start_at",
                                "op": "between",
                                "time_values": [
                                    {"kind": "relative", "unit": "month", "offset": -1, "boundary": "start"},
                                    {"kind": "relative", "unit": "month", "offset": 1, "boundary": "end"},
                                ],
                            },
                        }
                    ],
                },
            },
            "result": {
                "include_edges": False,
                "order_by": "updated_at",
                "order_desc": False,
                "skip": 0,
                "limit": 10,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [node["id"] for node in payload["nodes"]] == ["note-relative-inside"]


def test_query_program_supports_private_level_field_matcher(client, session, auth_user):
    session.add(make_note(auth_user, "note-public", "Public", start_at=100.0, updated_at=100.0, private_level=0))
    session.add(make_note(auth_user, "note-private", "Private", start_at=100.0, updated_at=200.0, private_level=1))
    session.commit()

    response = client.post(
        "/api/notes/query-program",
        json={
            "executor": {"kind": "scan"},
            "program": {
                "expand": {"default": False, "rules": []},
                "select": {
                    "default": False,
                    "rules": [
                        {
                            "action": "include",
                            "matcher": {
                                "kind": "field",
                                "field": "private_level",
                                "op": "gte",
                                "value": 1,
                            },
                        }
                    ],
                },
            },
            "result": {
                "include_edges": False,
                "order_by": "updated_at",
                "order_desc": False,
                "skip": 0,
                "limit": 10,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [node["id"] for node in payload["nodes"]] == ["note-private"]
