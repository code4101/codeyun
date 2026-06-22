import time
from unittest.mock import patch

from sqlmodel import Session, select

from backend.core.ai.chat import OllamaClientError
from backend.core.notes import metadata_feedback as metadata_feedback_module
from backend.core.notes.metadata_feedback import (
    NOTE_METADATA_FEEDBACK_COMPRESS_AFTER_SECONDS,
    create_note_metadata_feedback_optimization_run,
    record_codex_maintenance_feedback,
    run_note_metadata_feedback_optimization_worker,
)
from backend.models import CodexMaintenanceFeedback, NoteMetadataFeedback, NoteMetadataFeedbackOptimizationRun, NoteNode


def _make_note(
    user_id: int,
    note_id: str,
    title: str = "原始标题",
    *,
    content: str = "<p>这是一段用于反馈摘要的正文内容。</p>",
    note_form: str = "note",
) -> NoteNode:
    numeric_id = sum((index + 1) * ord(char) for index, char in enumerate(note_id)) % 1000000 + 1000
    return NoteNode(
        id=note_id,
        numeric_id=numeric_id,
        user_id=user_id,
        title=title,
        content=content,
        weight=0,
        node_type="general",
        note_types=[{"key": "general", "weight": 100}],
        note_categories=[{"key": "general", "weight": 100}],
        primary_category="general",
        note_form=note_form,
        note_kind="note",
        note_scene="note",
        node_status="idea",
        lifecycle_stage="idea",
        custom_fields=[],
        created_at=100.0,
        updated_at=100.0,
        start_at=100.0,
        history=[],
    )


def test_metadata_feedback_records_metadata_changes_not_body_only(client, session: Session, auth_user):
    note = _make_note(auth_user.id, "feedback-note-1")
    session.add(note)
    session.commit()

    content_response = client.put(f"/api/notes/{note.numeric_id}", json={"content": "<p>只改正文。</p>"})
    assert content_response.status_code == 200
    assert session.exec(select(NoteMetadataFeedback)).all() == []

    title_response = client.put(f"/api/notes/{note.numeric_id}", json={"title": "修正后的标题"})
    assert title_response.status_code == 200

    rows = session.exec(select(NoteMetadataFeedback)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "pending"
    assert row.note_id == str(note.numeric_id)
    assert row.source_kind == "manual_update"
    assert row.field_names == ["title"]
    assert row.before_snapshot["title"] == "原始标题"
    assert row.after_snapshot["title"] == "修正后的标题"
    assert row.title_sample == "修正后的标题"
    assert "只改正文" in row.content_summary
    assert row.content_hash
    assert row.content_length > 0


def test_metadata_feedback_coalesces_document_node_to_latest_state(client, session: Session, auth_user):
    note = _make_note(auth_user.id, "feedback-document-1", note_form="document")
    session.add(note)
    session.commit()

    first = client.put(f"/api/notes/{note.numeric_id}", json={"title": "文档标题一"})
    second = client.put(f"/api/notes/{note.numeric_id}", json={"weight": 3})
    assert first.status_code == 200
    assert second.status_code == 200

    rows = session.exec(select(NoteMetadataFeedback)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.field_signature == "__document_metadata__"
    assert row.event_count == 2
    assert row.before_snapshot["title"] == "原始标题"
    assert row.after_snapshot["title"] == "文档标题一"
    assert row.after_snapshot["weight"] == 3
    assert sorted(row.field_names) == ["note_form", "title", "weight"]


def test_ai_categorize_writes_metadata_feedback(client, session: Session, auth_user):
    note = _make_note(auth_user.id, "feedback-ai-1", title="修复登录接口报错")
    session.add(note)
    session.commit()

    with patch(
        "backend.api.notes.chat_with_provider",
        return_value={
            "model": "deepseek-chat",
            "content": '{"primary_category":"bug","note_form":"note","lifecycle_stage":"doing"}',
        },
    ):
        response = client.post(
            f"/api/notes/{note.numeric_id}/ai-categorize",
            json={"provider": "deepseek", "model": "deepseek-chat"},
        )

    assert response.status_code == 200
    row = session.exec(select(NoteMetadataFeedback)).one()
    assert row.note_id == str(note.numeric_id)
    assert row.source_kind == "ai_categorize"
    assert row.source_kinds == ["ai_categorize"]
    assert row.source_ref_id == "note-taxonomy"
    assert row.before_snapshot["primary_category"] == "general"
    assert row.after_snapshot["primary_category"] == "bug"
    assert row.after_snapshot["lifecycle_stage"] == "doing"


def test_metadata_feedback_test_command_uses_argv_without_shell(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run_quiet(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(metadata_feedback_module, "run_quiet", fake_run_quiet)

    result = metadata_feedback_module._run_test_command("uv run pytest tests/backend/test_note_metadata_feedback_api.py")

    assert result["returncode"] == 0
    assert captured["command"] == ["uv", "run", "pytest", "tests/backend/test_note_metadata_feedback_api.py"]
    assert captured["kwargs"].get("shell") is None


def test_feedback_optimizer_codex_failure_is_skipped_without_consuming(client, session: Session, auth_user, engine):
    note = _make_note(auth_user.id, "feedback-failure-1")
    session.add(note)
    session.commit()
    response = client.put(f"/api/notes/{note.numeric_id}", json={"title": "失败场景标题"})
    assert response.status_code == 200
    feedback_id = session.exec(select(NoteMetadataFeedback.id)).one()
    maintenance = record_codex_maintenance_feedback(
        session,
        source_kind="codex_daily_summary",
        source_ref_id="failed-daily-run",
        user_id=auth_user.id,
        source_date="2026-06-14",
        stage="running_deepseek",
        error_message="timeout",
        context={"root_dir": "codeyun"},
    )
    session.commit()
    assert maintenance is not None

    run = create_note_metadata_feedback_optimization_run(session, trigger_reason="manual-test", enqueue=False)
    assert run is not None

    def failing_chat(**kwargs):
        raise OllamaClientError("Codex CLI quota exceeded")

    run_note_metadata_feedback_optimization_worker(engine, run.id, chat_func=failing_chat)

    session.refresh(run)
    feedback = session.get(NoteMetadataFeedback, feedback_id)
    assert run.status == "skipped"
    assert run.stage == "codex_unavailable"
    assert "quota" in (run.error_message or "")
    assert feedback is not None
    assert feedback.status == "pending"
    assert feedback.consumer_run_id is None
    maintenance_feedback = session.get(CodexMaintenanceFeedback, maintenance.id)
    assert maintenance_feedback is not None
    assert maintenance_feedback.status == "pending"
    assert maintenance_feedback.consumer_run_id is None

    normal_response = client.put(f"/api/notes/{note.numeric_id}", json={"content": "<p>后端仍可正常保存。</p>"})
    assert normal_response.status_code == 200


def test_codex_maintenance_feedback_records_and_coalesces_failures(session: Session, auth_user):
    row = record_codex_maintenance_feedback(
        session,
        source_kind="codex_diary_import",
        source_ref_id="run-1",
        user_id=auth_user.id,
        source_date="2026-06-14",
        stage="drafting",
        error_message="JSON decode failed",
        context={"prompt_version": "test-v1"},
        now=100.0,
    )
    session.commit()
    assert row is not None

    second = record_codex_maintenance_feedback(
        session,
        source_kind="codex_diary_import",
        source_ref_id="run-1",
        user_id=auth_user.id,
        source_date="2026-06-14",
        stage="drafting",
        error_message="JSON decode failed again",
        context={"prompt_version": "test-v2"},
        now=120.0,
    )
    session.commit()

    rows = session.exec(select(CodexMaintenanceFeedback)).all()
    assert len(rows) == 1
    assert second is not None
    assert rows[0].id == row.id == second.id
    assert rows[0].status == "pending"
    assert rows[0].error_type == "parse_error"
    assert rows[0].event_count == 2
    assert rows[0].context_json["prompt_version"] == "test-v2"


def test_feedback_optimizer_success_consumes_samples_and_compresses_old_rows(session: Session, auth_user, engine):
    now = time.time()
    note = _make_note(auth_user.id, "feedback-success-1")
    session.add(note)
    pending = NoteMetadataFeedback(
        user_id=auth_user.id,
        note_id=note.id,
        status="pending",
        source_kind="manual_update",
        source_kinds=["manual_update"],
        field_signature="title",
        field_names=["title"],
        before_snapshot={"title": "旧标题"},
        after_snapshot={"title": "新标题"},
        title_sample="新标题",
        content_summary="摘要",
        content_hash="hash",
        content_length=10,
        first_event_at=now,
        last_event_at=now,
        created_at=now,
        updated_at=now,
    )
    old_consumed = NoteMetadataFeedback(
        user_id=auth_user.id,
        note_id="old-feedback-note",
        status="consumed",
        source_kind="manual_update",
        source_kinds=["manual_update"],
        field_signature="title",
        field_names=["title"],
        before_snapshot={"title": "很旧"},
        after_snapshot={"title": "旧"},
        title_sample="旧",
        content_summary="旧摘要",
        content_hash="old",
        content_length=10,
        consumer_run_id="old-run",
        consumed_at=now - NOTE_METADATA_FEEDBACK_COMPRESS_AFTER_SECONDS - 60,
        first_event_at=now - 100,
        last_event_at=now - 100,
        created_at=now - 100,
        updated_at=now - 100,
    )
    maintenance_pending = CodexMaintenanceFeedback(
        user_id=auth_user.id,
        status="pending",
        source_kind="codex_diary_import",
        source_ref_id="diary-run-1",
        source_date="2026-06-14",
        stage="drafting",
        error_type="parse_error",
        error_message="JSON decode failed",
        context_json={"prompt_version": "test-v1", "source_turn_count": 3},
        first_event_at=now,
        last_event_at=now,
        created_at=now,
        updated_at=now,
    )
    old_maintenance = CodexMaintenanceFeedback(
        user_id=auth_user.id,
        status="consumed",
        source_kind="codex_daily_summary",
        source_ref_id="daily-run-old",
        source_date="2026-05-01",
        stage="running_deepseek",
        error_type="timeout",
        error_message="timeout",
        context_json={"root_dir": "old"},
        consumer_run_id="old-run",
        consumed_at=now - NOTE_METADATA_FEEDBACK_COMPRESS_AFTER_SECONDS - 60,
        first_event_at=now - 100,
        last_event_at=now - 100,
        created_at=now - 100,
        updated_at=now - 100,
    )
    session.add(pending)
    session.add(old_consumed)
    session.add(maintenance_pending)
    session.add(old_maintenance)
    session.commit()

    run = create_note_metadata_feedback_optimization_run(session, trigger_reason="manual-test", enqueue=False)
    assert run is not None

    def successful_chat(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        assert "新标题" in prompt
        assert "codex_failure_samples" in prompt
        assert "JSON decode failed" in prompt
        return {"model": "codex-test", "content": "未发现需要修改源码的稳定规律。"}

    run_note_metadata_feedback_optimization_worker(engine, run.id, chat_func=successful_chat)

    session.expire_all()
    refreshed_run = session.get(NoteMetadataFeedbackOptimizationRun, run.id)
    refreshed_pending = session.get(NoteMetadataFeedback, pending.id)
    refreshed_old = session.get(NoteMetadataFeedback, old_consumed.id)
    refreshed_maintenance_pending = session.get(CodexMaintenanceFeedback, maintenance_pending.id)
    refreshed_old_maintenance = session.get(CodexMaintenanceFeedback, old_maintenance.id)
    assert refreshed_run is not None
    assert refreshed_run.status == "completed"
    assert refreshed_run.sample_count == 2
    assert refreshed_run.consumed_feedback_ids == [pending.id]
    assert refreshed_run.test_results["consumed_maintenance_feedback_ids"] == [maintenance_pending.id]
    assert refreshed_pending is not None
    assert refreshed_pending.status == "consumed"
    assert refreshed_pending.consumer_run_id == run.id
    assert refreshed_maintenance_pending is not None
    assert refreshed_maintenance_pending.status == "consumed"
    assert refreshed_maintenance_pending.consumer_run_id == run.id
    assert refreshed_old is not None
    assert refreshed_old.before_snapshot is None
    assert refreshed_old.after_snapshot is None
    assert refreshed_old.content_summary == ""
    assert refreshed_old.compressed_at is not None
    assert refreshed_old_maintenance is not None
    assert refreshed_old_maintenance.error_message == ""
    assert refreshed_old_maintenance.context_json == {}
    assert refreshed_old_maintenance.compressed_at is not None


def test_metadata_feedback_status_api_reports_counts(client, session: Session, auth_user):
    session.add(
        NoteMetadataFeedback(
            user_id=auth_user.id,
            note_id="status-note",
            status="pending",
            source_kind="manual_update",
            source_kinds=["manual_update"],
            field_signature="title",
            field_names=["title"],
            before_snapshot={"title": "a"},
            after_snapshot={"title": "b"},
            title_sample="b",
            content_summary="摘要",
            content_hash="hash",
            content_length=2,
            first_event_at=1,
            last_event_at=1,
            created_at=1,
            updated_at=1,
        )
    )
    session.add(
        CodexMaintenanceFeedback(
            user_id=auth_user.id,
            status="pending",
            source_kind="codex_daily_summary",
            source_ref_id="status-run",
            source_date="2026-06-14",
            stage="running_deepseek",
            error_type="timeout",
            error_message="timeout",
            context_json={"root_dir": "codeyun"},
            first_event_at=1,
            last_event_at=1,
            created_at=1,
            updated_at=1,
        )
    )
    session.commit()

    response = client.get("/api/notes/metadata-feedback/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["pending_count"] == 1
    assert payload["maintenance_pending_count"] == 1
    assert payload["total_pending_count"] == 2
    assert payload["trigger_threshold"] == 200
    assert "queue" in payload
