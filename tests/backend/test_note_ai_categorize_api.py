from unittest.mock import patch

import pytest
from sqlmodel import select

from backend.api.notes import (
    CODEX_DIARY_BLOCK_FIELD,
    CODEX_DIARY_DATE_FIELD,
    CODEX_DIARY_SCOPE_FIELD,
    CODEX_DIARY_SOURCE_THREADS_FIELD,
    CODEX_DIARY_WORKLOG_FIELD,
)
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
                    {"key": "custom_codeyun_general", "label": "CodeYun/综合", "color": "#00BFFF", "description": "CodeYun 本体的通用功能与工程事项。", "order": 10},
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
    assert "custom_codeyun_general | CodeYun/综合 | CodeYun 本体的通用功能与工程事项。" in kwargs["messages"][0]["content"]
    assert "本地回环地址" not in kwargs["messages"][0]["content"]
    assert "- 修复系统代理地址 | custom_codeyun_general(CodeYun/综合) | note(笔记) | doing(待办)" in kwargs["messages"][0]["content"]
    assert "- 登录模块技术方案 | project(项目) | document(文档) | idea(笔记)" not in kwargs["messages"][0]["content"]
    assert "bug | 缺陷" not in kwargs["messages"][0]["content"]
    assert "project | 项目" not in kwargs["messages"][0]["content"]
    assert "module | 模块" not in kwargs["messages"][0]["content"]
    assert "task | 任务" not in kwargs["messages"][0]["content"]


def test_ai_categorize_note_forces_fanxiu_for_zhenxie_title(client, session, auth_user):
    note = _make_note(
        "note-ai-categorize-zhenxie",
        auth_user.id,
        "日常镇邪任务模型",
        primary_category="custom_pyxllib",
        lifecycle_stage="done",
    )
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#606266", "order": 0},
                    {"key": "legacy_fanxiu", "label": "凡修", "color": "#67C23A", "order": 10},
                    {"key": "custom_pyxllib", "label": "pyxllib", "color": "#2C9BA7", "order": 20},
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
            "content": '{"primary_category":"custom_pyxllib","note_form":"note","lifecycle_stage":"done"}',
        },
    ) as mock_chat:
        response = client.post(
            f"/api/notes/{note.numeric_id}/ai-categorize",
            json={"provider": "deepseek", "model": "deepseek-chat"},
        )

    assert response.status_code == 200
    assert response.json()["note"]["primary_category"] == "legacy_fanxiu"
    prompt = mock_chat.call_args.kwargs["messages"][0]["content"]
    assert "命中领域专有词：镇邪、日常镇邪" in prompt
    assert "应归入 legacy_fanxiu(凡修)" in prompt


@pytest.mark.parametrize(
    ("title", "matched_term"),
    [
        ("动态插桩结果写回灵器前端表格", "动态插桩"),
        ("论道OCR切回GPU并提速96%", "论道"),
        ("活动答题奖励钩子闭环修复", "活动_答题"),
        ("兑换码缓存", "兑换码缓存"),
    ],
)
def test_ai_categorize_note_forces_fanxiu_for_runtime_business_title(
    client,
    session,
    auth_user,
    title,
    matched_term,
):
    note = _make_note(
        f"note-ai-categorize-fanxiu-{matched_term}",
        auth_user.id,
        title,
        primary_category="custom_codeyun_note",
        lifecycle_stage="done",
    )
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#606266", "order": 0},
                    {"key": "legacy_fanxiu", "label": "凡修", "color": "#67C23A", "order": 10},
                    {"key": "custom_codeyun_note", "label": "CodeYun/笔记", "color": "#446CCF", "order": 20},
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
            "content": '{"primary_category":"custom_codeyun_note","note_form":"note","lifecycle_stage":"done"}',
        },
    ) as mock_chat:
        response = client.post(
            f"/api/notes/{note.numeric_id}/ai-categorize",
            json={"provider": "deepseek", "model": "deepseek-chat"},
        )

    assert response.status_code == 200
    assert response.json()["note"]["primary_category"] == "legacy_fanxiu"
    prompt = mock_chat.call_args.kwargs["messages"][0]["content"]
    assert f"命中领域专有词：{matched_term}" in prompt
    assert "应归入 legacy_fanxiu(凡修)" in prompt


def _attach_diary_fields(note, *, block_key, duration_seconds, turn_count, thread_id):
    note.custom_fields = [
        [CODEX_DIARY_DATE_FIELD, "string", "2026-08-01"],
        [CODEX_DIARY_SCOPE_FIELD, "string", "entries:test"],
        [CODEX_DIARY_BLOCK_FIELD, "string", block_key],
        [CODEX_DIARY_SOURCE_THREADS_FIELD, "json", [thread_id]],
        [
            CODEX_DIARY_WORKLOG_FIELD,
            "json",
            {
                "version": 1,
                "date": "2026-08-01",
                "timezone": "Asia/Shanghai",
                "scope_key": "entries:test",
                "block_key": block_key,
                "duration_seconds": duration_seconds,
                "duration_minutes": round(duration_seconds / 60),
                "start_at": 1000 if block_key == "main" else 2000,
                "end_at": 1500 if block_key == "main" else 2500,
                "turn_count": turn_count,
                "source_thread_ids": [thread_id],
                "source_devices": ["codepc_mf"],
            },
        ],
    ]
    return note


def test_ai_categorize_diary_note_reaggregates_same_date_category(client, session, auth_user):
    main_note = _attach_diary_fields(_make_note(
        "diary-fanxiu-main",
        auth_user.id,
        "凡修行为树运行框架统一修复",
        content="<ol><li><span>行为树主线完成</span></li></ol><p><strong>来源</strong>：旧</p>",
        primary_category="legacy_fanxiu",
        lifecycle_stage="done",
        updated_at=1,
    ), block_key="main", duration_seconds=3600, turn_count=6, thread_id="thread-main")
    target_note = _attach_diary_fields(_make_note(
        "diary-fanxiu-target",
        auth_user.id,
        "论道OCR切回GPU并提速96%",
        content="<ol><li><span>论道 OCR 切回 GPU</span></li></ol><p><strong>来源</strong>：旧</p>",
        primary_category="custom_codeyun_note",
        lifecycle_stage="done",
        updated_at=2,
    ), block_key="target", duration_seconds=1200, turn_count=2, thread_id="thread-target")
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#606266", "order": 0},
                    {"key": "legacy_fanxiu", "label": "凡修", "color": "#67C23A", "order": 10},
                    {"key": "custom_codeyun_note", "label": "CodeYun/笔记", "color": "#446CCF", "order": 20},
                ]
            },
        )
    )
    session.add(main_note)
    session.add(target_note)
    session.commit()

    with patch(
        "backend.api.notes.chat_with_provider",
        return_value={
            "model": "deepseek-chat",
            "content": '{"primary_category":"custom_codeyun_note","note_form":"note","lifecycle_stage":"done"}',
        },
    ):
        response = client.post(
            f"/api/notes/{target_note.numeric_id}/ai-categorize",
            json={"provider": "deepseek", "model": "deepseek-chat"},
        )

    assert response.status_code == 200
    assert response.json()["note"]["title"] == main_note.title
    active_fanxiu_notes = session.exec(
        select(NoteNode).where(
            NoteNode.user_id == auth_user.id,
            NoteNode.primary_category == "legacy_fanxiu",
            NoteNode.deleted_at == None,  # noqa: E711
        )
    ).all()
    assert len(active_fanxiu_notes) == 1
    merged_note = active_fanxiu_notes[0]
    assert "行为树主线完成" in merged_note.content
    assert "论道 OCR 切回 GPU" in merged_note.content
    worklog = next(item[2] for item in merged_note.custom_fields if item[0] == CODEX_DIARY_WORKLOG_FIELD)
    assert worklog["duration_seconds"] == 4800
    assert worklog["turn_count"] == 8
    assert worklog["source_thread_ids"] == ["thread-main", "thread-target"]
    session.refresh(target_note)
    assert target_note.deleted_at is not None


def test_manual_diary_category_update_reaggregates_same_date_category(client, session, auth_user):
    main_note = _attach_diary_fields(_make_note(
        "diary-manual-main",
        auth_user.id,
        "凡修主日记",
        content="<ol><li><span>凡修主线</span></li></ol>",
        primary_category="legacy_fanxiu",
        lifecycle_stage="done",
        updated_at=1,
    ), block_key="main", duration_seconds=3600, turn_count=6, thread_id="thread-main")
    target_note = _attach_diary_fields(_make_note(
        "diary-manual-target",
        auth_user.id,
        "动态插桩结果",
        content="<ol><li><span>动态插桩支线</span></li></ol>",
        primary_category="custom_codeyun_note",
        lifecycle_stage="done",
        updated_at=2,
    ), block_key="target", duration_seconds=600, turn_count=1, thread_id="thread-target")
    session.add(main_note)
    session.add(target_note)
    session.commit()

    response = client.put(
        f"/api/notes/{target_note.numeric_id}",
        json={
            "primary_category": "legacy_fanxiu",
            "note_categories": [{"key": "legacy_fanxiu", "weight": 100}],
        },
    )

    assert response.status_code == 200
    assert response.json()["title"] == "凡修主日记"
    active_fanxiu_notes = session.exec(
        select(NoteNode).where(
            NoteNode.user_id == auth_user.id,
            NoteNode.primary_category == "legacy_fanxiu",
            NoteNode.deleted_at == None,  # noqa: E711
        )
    ).all()
    assert len(active_fanxiu_notes) == 1
    assert "凡修主线" in active_fanxiu_notes[0].content
    assert "动态插桩支线" in active_fanxiu_notes[0].content


def test_ai_categorize_note_forces_zaohua_domain_over_codeyun_note(client, session, auth_user):
    note = _make_note(
        "note-ai-categorize-zaohua",
        auth_user.id,
        "造化仙缘天道插件",
        primary_category="custom_codeyun_note",
        lifecycle_stage="done",
    )
    session.add(
        AppSetting(
            key=build_note_category_palette_setting_key(auth_user.id),
            value={
                "items": [
                    {"key": "general", "label": "综合", "color": "#606266", "order": 0},
                    {"key": "custom_codeyun_general", "label": "CodeYun/综合", "color": "#00BFFF", "order": 10},
                    {"key": "custom_codeyun_note", "label": "CodeYun/笔记", "color": "#446CCF", "order": 20},
                    {"key": "造化仙缘", "label": "造化仙缘", "color": "#9B2A20", "order": 30},
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
            "content": '{"primary_category":"custom_codeyun_note","note_form":"note","lifecycle_stage":"done"}',
        },
    ):
        response = client.post(
            f"/api/notes/{note.numeric_id}/ai-categorize",
            json={"provider": "deepseek", "model": "deepseek-chat"},
        )

    assert response.status_code == 200
    assert response.json()["note"]["primary_category"] == "造化仙缘"


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
                        {"key": "custom_logistics", "label": "后勤", "color": "#D2B48C", "order": 40},
                        {"key": "custom_ai", "label": "AI", "color": "#6F2DBD", "order": 50},
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
    assert "custom_logistics | 后勤" not in prompt
    assert "custom_ai | AI" not in prompt
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
