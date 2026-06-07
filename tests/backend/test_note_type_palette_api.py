from backend.models import NoteNode


def test_note_type_palette_defaults_include_default_types(client, auth_user):
    response = client.get("/api/notes/category-palette")
    assert response.status_code == 200
    payload = response.json()
    keys = [item["key"] for item in payload["items"]]
    assert keys[:5] == ["general", "project", "module", "task", "bug"]


def test_note_type_palette_discovers_legacy_colors_and_persists_rename(client, session, auth_user):
    note = NoteNode(
        id="legacy-color-note",
        user_id=auth_user.id,
        title="Legacy Color",
        content="",
        node_type="note",
        note_types=[{"key": "note", "weight": 100}],
        color="#67c23a",
        created_at=1,
        updated_at=1,
        start_at=1,
    )
    session.add(note)
    session.commit()

    response = client.get("/api/notes/category-palette")
    assert response.status_code == 200
    payload = response.json()

    legacy_item = next(item for item in payload["items"] if item["key"] == "legacy_color_67c23a")
    assert legacy_item["label"] == "旧色67C23A"
    assert legacy_item["generated_from_color"] == "#67C23A"
    assert legacy_item["usage_count"] == 1

    update_response = client.put(
        "/api/notes/category-palette",
        json={
            "items": [
                {
                    "key": "legacy_color_67c23a",
                    "label": "考勤",
                    "color": "#67C23A",
                    "order": 2000,
                    "builtin": False,
                    "source": "legacy",
                    "generated_from_color": "#67C23A",
                }
            ]
        },
    )
    assert update_response.status_code == 200
    updated_item = next(item for item in update_response.json()["items"] if item["key"] == "legacy_color_67c23a")
    assert updated_item["label"] == "考勤"

    reload_response = client.get("/api/notes/category-palette")
    assert reload_response.status_code == 200
    reloaded_item = next(item for item in reload_response.json()["items"] if item["key"] == "legacy_color_67c23a")
    assert reloaded_item["label"] == "考勤"


def test_note_type_palette_usage_count_respects_type_weights(client, session, auth_user):
    note = NoteNode(
        id="weighted-type-note",
        user_id=auth_user.id,
        title="Weighted Type Note",
        content="",
        node_type="task",
        note_types=[
            {"key": "task", "weight": 100},
            {"key": "module", "weight": 50},
        ],
        created_at=1,
        updated_at=1,
        start_at=1,
    )
    session.add(note)
    session.commit()

    response = client.get("/api/notes/category-palette")
    assert response.status_code == 200
    payload = response.json()

    task_item = next(item for item in payload["items"] if item["key"] == "task")
    module_item = next(item for item in payload["items"] if item["key"] == "module")
    assert task_item["usage_count"] == 1
    assert module_item["usage_count"] == 0.5


def test_note_type_palette_hides_import_script_categories(client, session, auth_user):
    note = NoteNode(
        id="import-category-history-note",
        user_id=auth_user.id,
        title="Imported History",
        content="",
        node_type="note",
        note_categories=[{"key": "import_programming", "weight": 100}],
        primary_category="import_programming",
        created_at=1,
        updated_at=1,
        start_at=1,
    )
    session.add(note)
    session.commit()

    update_response = client.put(
        "/api/notes/category-palette",
        json={
            "items": [
                {
                    "key": "import_programming",
                    "label": "编程/技术",
                    "color": "#409EFF",
                    "order": 10,
                    "builtin": False,
                    "source": "import",
                },
                {
                    "key": "custom_codex",
                    "label": "CodeYun",
                    "color": "#67C23A",
                    "order": 20,
                    "builtin": False,
                    "source": "custom",
                },
            ]
        },
    )
    assert update_response.status_code == 200
    updated_items = {item["key"]: item for item in update_response.json()["items"]}
    assert "import_programming" not in updated_items
    assert updated_items["custom_codex"]["source"] == "custom"

    reload_response = client.get("/api/notes/category-palette")
    assert reload_response.status_code == 200
    reloaded_items = {item["key"]: item for item in reload_response.json()["items"]}
    assert "import_programming" not in reloaded_items
    assert reloaded_items["custom_codex"]["source"] == "custom"


def test_create_note_promotes_legacy_color_to_note_type(client, auth_user):
    response = client.post(
        "/api/notes/",
        json={
            "title": "Legacy Type Note",
            "content": "",
            "node_type": "note",
            "note_types": [],
            "color": "#67c23a",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["node_type"] == "legacy_color_67c23a"
    assert payload["note_types"] == [{"key": "legacy_color_67c23a", "weight": 100}]
    assert payload["color"] == "#67C23A"


def test_create_note_backfills_new_taxonomy_fields(client, auth_user):
    response = client.post(
        "/api/notes/",
        json={
            "title": "Document Note",
            "content": "",
            "node_type": "doc",
            "note_types": [],
            "node_status": "doing",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["note_form"] == "document"
    assert payload["note_scene"] == "note"
    assert payload["lifecycle_stage"] == "doing"
    assert payload["primary_category"] == "general"
    assert payload["note_categories"] == [{"key": "general", "weight": 100}]


def test_create_note_accepts_extended_note_forms(client, auth_user):
    response = client.post(
        "/api/notes/",
        json={
            "title": "Music Note",
            "content": "",
            "note_categories": [{"key": "general", "weight": 100}],
            "primary_category": "general",
            "note_form": "music",
            "note_scene": "note",
            "lifecycle_stage": "idea",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["note_form"] == "music"
    assert payload["primary_category"] == "general"
    assert payload["note_categories"] == [{"key": "general", "weight": 100}]


def test_note_type_palette_cannot_delete_type_that_is_still_used(client, session, auth_user):
    note = NoteNode(
        id="note-using-task",
        user_id=auth_user.id,
        title="Task Note",
        content="",
        node_type="task",
        note_types=[{"key": "task", "weight": 100}],
        created_at=1,
        updated_at=1,
        start_at=1,
    )
    session.add(note)
    session.commit()

    response = client.put(
        "/api/notes/category-palette",
        json={
            "items": [
                {"key": "general", "label": "综合", "color": "#606266", "order": 0, "builtin": True, "source": "builtin"},
                {"key": "project", "label": "项目", "color": "#7B1FA2", "order": 10, "builtin": True, "source": "builtin"},
                {"key": "module", "label": "模块", "color": "#BA68C8", "order": 20, "builtin": True, "source": "builtin"},
                {"key": "bug", "label": "缺陷", "color": "#F56C6C", "order": 40, "builtin": True, "source": "builtin"},
            ]
        },
    )
    assert response.status_code == 400
    assert "task" in response.json()["detail"]


def test_note_type_palette_rejects_duplicate_labels(client, auth_user):
    response = client.put(
        "/api/notes/category-palette",
        json={
            "items": [
                {"key": "general", "label": "通用", "color": "#606266", "order": 0, "builtin": True, "source": "builtin"},
                {"key": "task", "label": "通用", "color": "#409EFF", "order": 10, "builtin": True, "source": "builtin"},
            ]
        },
    )
    assert response.status_code == 400
    assert "unique" in response.json()["detail"].lower()


def test_note_type_palette_merge_moves_existing_bindings(client, session, auth_user):
    note = NoteNode(
        id="note-merge-type",
        user_id=auth_user.id,
        title="Merge Type Note",
        content="",
        node_type="module",
        note_types=[
            {"key": "module", "weight": 50},
            {"key": "task", "weight": 40},
        ],
        created_at=1,
        updated_at=1,
        start_at=1,
    )
    session.add(note)
    session.commit()

    response = client.post(
        "/api/notes/category-palette/merge",
        json={"source_key": "module", "target_key": "task"},
    )
    assert response.status_code == 200
    payload = response.json()

    module_item = next(item for item in payload["items"] if item["key"] == "module")
    task_item = next(item for item in payload["items"] if item["key"] == "task")
    assert module_item["usage_count"] == 0
    assert task_item["usage_count"] == 0.9

    session.refresh(note)
    assert note.note_types == [{"key": "task", "weight": 90}]
    assert note.node_type == "task"


def test_note_type_palette_delete_check_short_circuits_by_key(client, session, auth_user):
    note = NoteNode(
        id="note-using-module",
        user_id=auth_user.id,
        title="Module Note",
        content="",
        node_type="module",
        note_types=[{"key": "module", "weight": 100}],
        created_at=1,
        updated_at=1,
        start_at=1,
    )
    session.add(note)
    session.commit()

    blocked = client.get("/api/notes/category-palette/module/can-delete")
    assert blocked.status_code == 200
    assert blocked.json()["can_delete"] is False

    free = client.get("/api/notes/category-palette/free-category/can-delete")
    assert free.status_code == 200
    assert free.json()["can_delete"] is True
