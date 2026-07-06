from unittest.mock import patch

from backend.core.notes.semantics import build_note_category_palette_setting_key
from backend.models import AppSetting, NoteNode


def _numeric_note_id(note_id: str) -> int:
    return sum((index + 1) * ord(char) for index, char in enumerate(note_id)) % 1000000 + 1000


def _make_note(
    note_id,
    user_id,
    title,
    *,
    content="<p>测试内容。</p>",
    primary_category="general",
    note_form="note",
    lifecycle_stage="idea",
    updated_at=1,
):
    return NoteNode(
        id=note_id,
        numeric_id=_numeric_note_id(note_id),
        user_id=user_id,
        title=title,
        content=content,
        node_type=primary_category,
        note_types=[{"key": primary_category, "weight": 100}],
        note_categories=[{"key": primary_category, "weight": 100}],
        primary_category=primary_category,
        note_form=note_form,
        note_kind="note",
        note_scene="note",
        node_status=lifecycle_stage,
        lifecycle_stage=lifecycle_stage,
        created_at=updated_at,
        updated_at=updated_at,
        start_at=updated_at,
        history=[],
    )


def test_ai_categorize_note_updates_note_taxonomy(client, session, auth_user):
    note = NoteNode(
        id="note-ai-categorize-1",
        numeric_id=_numeric_note_id("note-ai-categorize-1"),
        user_id=auth_user.id,
        title="修复登录代理地址",
        content="<p>系统代理地址需要改成本地回环地址。</p>",
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
    reference_bug = NoteNode(
        id="note-ai-categorize-ref-codeyun",
        numeric_id=_numeric_note_id("note-ai-categorize-ref-codeyun"),
        user_id=auth_user.id,
        title="修复系统代理地址",
        content="<p>另一条参考笔记。</p>",
        node_type="custom_codeyun_general",
        note_types=[{"key": "custom_codeyun_general", "weight": 100}],
        note_categories=[{"key": "custom_codeyun_general", "weight": 100}],
        primary_category="custom_codeyun_general",
        note_form="note",
        note_kind="note",
        note_scene="note",
        node_status="doing",
        lifecycle_stage="doing",
        created_at=2,
        updated_at=2,
        start_at=2,
        history=[],
    )
    reference_doc = NoteNode(
        id="note-ai-categorize-ref-doc",
        numeric_id=_numeric_note_id("note-ai-categorize-ref-doc"),
        user_id=auth_user.id,
        title="登录模块技术方案",
        content="<p>另一条文档类参考笔记。</p>",
        node_type="project",
        note_types=[{"key": "project", "weight": 100}],
        note_categories=[{"key": "project", "weight": 100}],
        primary_category="project",
        note_form="document",
        note_kind="note",
        note_scene="note",
        node_status="idea",
        lifecycle_stage="idea",
        created_at=3,
        updated_at=3,
        start_at=3,
        history=[],
    )
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#606266", "order": 0},
                    {"key": "custom_codeyun_general", "label": "CodeYun/综合", "color": "#00BFFF", "order": 10},
                    {"key": "bug", "label": "缺陷", "color": "#F56C6C", "order": 20},
                    {"key": "project", "label": "项目", "color": "#7B1FA2", "order": 30},
                    {"key": "module", "label": "模块", "color": "#BA68C8", "order": 40},
                    {"key": "task", "label": "任务", "color": "#409EFF", "order": 50},
                ]
            },
        )
    )
    session.add(note)
    session.add(reference_bug)
    session.add(reference_doc)
    session.commit()

    with patch(
        "backend.api.notes.chat_with_provider",
        return_value={
            "model": "deepseek-chat",
            "content": '{"primary_category":"custom_codeyun_general","note_form":"note","lifecycle_stage":"doing","reason":"这是 CodeYun 系统配置修复","confidence":0.96}',
        },
    ) as mock_chat:
        response = client.post(
            f"/api/notes/{note.numeric_id}/ai-categorize",
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
    assert payload["summary"] == "已标记为 CodeYun/综合 / 笔记 / 待办"
    assert payload["note"]["primary_category"] == "custom_codeyun_general"
    assert payload["note"]["note_categories"] == [{"key": "custom_codeyun_general", "weight": 100}]
    assert payload["note"]["note_form"] == "note"
    assert payload["note"]["lifecycle_stage"] == "doing"
    assert payload["note"]["node_type"] == "custom_codeyun_general"
    assert payload["note"]["node_status"] == "doing"

    session.refresh(note)
    assert note.primary_category == "custom_codeyun_general"
    assert note.note_categories == [{"key": "custom_codeyun_general", "weight": 100}]
    assert note.lifecycle_stage == "doing"
    assert note.node_type == "custom_codeyun_general"
    assert note.node_status == "doing"

    kwargs = mock_chat.call_args.kwargs
    assert kwargs["provider_id"] == "deepseek"
    assert kwargs["model"] == "deepseek-chat"
    assert "仅根据当前节点标题" in kwargs["system_prompt"]
    assert "修复登录代理地址" in kwargs["messages"][0]["content"]
    assert "本地回环地址" not in kwargs["messages"][0]["content"]
    assert "- 修复系统代理地址 | custom_codeyun_general(CodeYun/综合) | note(笔记) | doing(待办)" in kwargs["messages"][0]["content"]
    assert "- 登录模块技术方案 | project(项目) | document(文档) | idea(笔记)" not in kwargs["messages"][0]["content"]
    assert "bug | 缺陷" not in kwargs["messages"][0]["content"]
    assert "project | 项目" not in kwargs["messages"][0]["content"]
    assert "module | 模块" not in kwargs["messages"][0]["content"]
    assert "task | 任务" not in kwargs["messages"][0]["content"]


def test_ai_categorize_reference_samples_are_balanced_by_taxonomy_combo(client, session, auth_user):
    note = _make_note(
        "note-ai-categorize-balanced",
        auth_user.id,
        "修复登录接口报错",
        content="<p>当前正文不应参与分类。</p>",
    )
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#606266", "order": 0},
                    {"key": "custom_codeyun_general", "label": "CodeYun/综合", "color": "#00BFFF", "order": 10},
                    {"key": "bug", "label": "缺陷", "color": "#F56C6C", "order": 20},
                    {"key": "project", "label": "项目", "color": "#7B1FA2", "order": 30},
                ]
            },
        )
    )
    session.add(note)
    for index in range(6):
        session.add(_make_note(
            f"note-ai-categorize-balanced-bug-{index}",
            auth_user.id,
            f"修复登录相关报错 {index}",
            primary_category="bug",
            note_form="note",
            lifecycle_stage="doing",
            updated_at=100 + index,
        ))
    session.add(_make_note(
        "note-ai-categorize-balanced-doc",
        auth_user.id,
        "登录模块技术方案",
        primary_category="project",
        note_form="document",
        lifecycle_stage="idea",
        updated_at=1,
    ))
    session.commit()

    with patch(
        "backend.api.notes.chat_with_provider",
        return_value={
            "model": "deepseek-chat",
            "content": '{"primary_category":"custom_codeyun_general","note_form":"note","lifecycle_stage":"doing"}',
        },
    ) as mock_chat:
        response = client.post(
            f"/api/notes/{note.numeric_id}/ai-categorize",
            json={
                "provider": "deepseek",
                "model": "deepseek-chat",
            },
        )

    assert response.status_code == 200
    prompt = mock_chat.call_args.kwargs["messages"][0]["content"]
    assert "bug | 缺陷" not in prompt
    assert "| bug(缺陷) | note(笔记) | doing(待办)" not in prompt
    assert "custom_codeyun_general | CodeYun/综合" in prompt
    assert "- 登录模块技术方案 | project(项目) | document(文档) | idea(笔记)" not in prompt
    assert "当前正文不应参与分类" not in prompt


def test_ai_categorize_note_filters_blocked_custom_category_labels(client, session, auth_user):
    note = _make_note("note-ai-categorize-blocked-label", auth_user.id, "整理关键结论")
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                        {"key": "general", "label": "综合", "color": "#606266", "order": 0},
                        {"key": "focus", "label": "重点", "color": "#E6A23C", "order": 10},
                        {"key": "bug", "label": "缺陷", "color": "#F56C6C", "order": 20},
                        {"key": "custom_codeyun_general", "label": "CodeYun/综合", "color": "#00BFFF", "order": 30},
                ]
            },
        )
    )
    session.add(note)
    session.commit()

    with patch(
        "backend.api.notes.chat_with_provider",
        return_value={
            "model": "deepseek-chat",
            "content": '{"primary_category":"general","note_form":"note","lifecycle_stage":"idea"}',
        },
    ) as mock_chat:
        response = client.post(
            f"/api/notes/{note.numeric_id}/ai-categorize",
            json={"provider": "deepseek", "model": "deepseek-chat"},
        )

    assert response.status_code == 200
    prompt = mock_chat.call_args.kwargs["messages"][0]["content"]
    assert "focus | 重点" not in prompt
    assert "general | 综合" in prompt
    assert "bug | 缺陷" not in prompt
    assert "custom_codeyun_general | CodeYun/综合" in prompt


def test_ai_categorize_note_rejects_unknown_category(client, session, auth_user):
    note = NoteNode(
        id="note-ai-categorize-2",
        numeric_id=_numeric_note_id("note-ai-categorize-2"),
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
            f"/api/notes/{note.numeric_id}/ai-categorize",
            json={
                "provider": "deepseek",
                "model": "deepseek-chat",
            },
        )

    assert response.status_code == 502
    assert "未知分类" in response.json()["detail"]


def test_ai_categorize_note_rejects_blocked_builtin_category(client, session, auth_user):
    note = _make_note("note-ai-categorize-blocked", auth_user.id, "跟进一个执行事项")
    session.add(note)
    session.commit()

    with patch(
        "backend.api.notes.chat_with_provider",
        return_value={
            "model": "deepseek-chat",
            "content": '{"primary_category":"task","note_form":"note","lifecycle_stage":"doing"}',
        },
    ):
        response = client.post(
            f"/api/notes/{note.numeric_id}/ai-categorize",
            json={"provider": "deepseek", "model": "deepseek-chat"},
        )

    assert response.status_code == 502
    assert "未知分类" in response.json()["detail"]


def test_ai_categorize_note_requires_title_even_when_content_exists(client, session, auth_user):
    note = NoteNode(
        id="note-ai-categorize-no-title",
        numeric_id=_numeric_note_id("note-ai-categorize-no-title"),
        user_id=auth_user.id,
        title="",
        content="<p>正文里其实有不少信息，但现在不应再参与分类。</p>",
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

    with patch("backend.api.notes.chat_with_provider") as mock_chat:
        response = client.post(
            f"/api/notes/{note.numeric_id}/ai-categorize",
            json={
                "provider": "deepseek",
                "model": "deepseek-chat",
            },
        )

    assert response.status_code == 400
    assert "缺少可供分析的标题" in response.json()["detail"]
    mock_chat.assert_not_called()
