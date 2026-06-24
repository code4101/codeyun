from unittest.mock import patch

from backend.models import NoteNode


def test_ai_categorize_note_updates_note_taxonomy(client, session, auth_user):
    note = NoteNode(
        id="note-ai-categorize-1",
        user_id=auth_user.id,
        title="修复登录接口报错",
        content="<p>用户登录时会出现 500 报错，需要尽快定位并修复。</p>",
        node_type="note",
        note_types=[{"key": "note", "weight": 100}],
        note_categories=[{"key": "general", "weight": 100}],
        primary_category="general",
        note_form="note",
        note_kind="note",
        note_scene="note",
        node_status="idea",
        lifecycle_stage="idea",
        created_at=1,
        updated_at=1,
        start_at=1,
        history=[],
    )
    session.add(note)
    session.commit()

    with patch(
        "backend.api.notes.chat_with_provider",
        return_value={
            "model": "deepseek-chat",
            "content": '{"primary_category":"bug","note_form":"note","lifecycle_stage":"doing","reason":"这是一个明确待修复的问题","confidence":0.96}',
        },
    ) as mock_chat:
        response = client.post(
            f"/api/notes/{note.id}/ai-categorize",
            json={
                "provider": "deepseek",
                "model": "deepseek-chat",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["app"] == "note-taxonomy"
    assert payload["provider"] == "deepseek"
    assert payload["model"] == "deepseek-chat"
    assert payload["summary"] == "已标记为 缺陷 / 笔记 / 待办"
    assert payload["note"]["primary_category"] == "bug"
    assert payload["note"]["note_categories"] == [{"key": "bug", "weight": 100}]
    assert payload["note"]["note_form"] == "note"
    assert payload["note"]["lifecycle_stage"] == "doing"
    assert payload["note"]["node_type"] == "bug"
    assert payload["note"]["node_status"] == "doing"

    session.refresh(note)
    assert note.primary_category == "bug"
    assert note.note_categories == [{"key": "bug", "weight": 100}]
    assert note.lifecycle_stage == "doing"
    assert note.node_type == "bug"
    assert note.node_status == "doing"

    kwargs = mock_chat.call_args.kwargs
    assert kwargs["provider_id"] == "deepseek"
    assert kwargs["model"] == "deepseek-chat"
    assert "修复登录接口报错" in kwargs["messages"][0]["content"]
    assert "500 报错" in kwargs["messages"][0]["content"]


def test_ai_categorize_note_rejects_unknown_category(client, session, auth_user):
    note = NoteNode(
        id="note-ai-categorize-2",
        user_id=auth_user.id,
        title="一条普通笔记",
        content="<p>只是随手记录一点东西。</p>",
        node_type="note",
        note_types=[{"key": "note", "weight": 100}],
        note_categories=[{"key": "general", "weight": 100}],
        primary_category="general",
        note_form="note",
        note_kind="note",
        note_scene="note",
        node_status="idea",
        lifecycle_stage="idea",
        created_at=1,
        updated_at=1,
        start_at=1,
        history=[],
    )
    session.add(note)
    session.commit()

    with patch(
        "backend.api.notes.chat_with_provider",
        return_value={
            "model": "deepseek-chat",
            "content": '{"primary_category":"unknown-category","note_form":"note","lifecycle_stage":"idea"}',
        },
    ):
        response = client.post(
            f"/api/notes/{note.id}/ai-categorize",
            json={
                "provider": "deepseek",
                "model": "deepseek-chat",
            },
        )

    assert response.status_code == 502
    assert "未知分类" in response.json()["detail"]
