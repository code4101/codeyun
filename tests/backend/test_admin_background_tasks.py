from backend.app import app
from backend.core.auth import get_current_active_superuser
from backend.models import User


def _admin_user():
    return User(
        id=1,
        username="admin",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )


def test_admin_background_tasks_status_lists_managed_tasks(client):
    app.dependency_overrides[get_current_active_superuser] = _admin_user
    try:
        response = client.get("/api/admin/background-tasks/status")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert response.status_code == 200
    payload = response.json()
    task_keys = {item["key"] for item in payload["tasks"]}
    assert {
        "auto_git_commit",
        "note_metadata_feedback_optimization",
        "codex_diary_yesterday_import",
        "attendance_summary_monthly_templates",
        "storage_analysis",
    }.issubset(task_keys)
    assert "queue" in payload


def test_admin_background_tasks_can_trigger_storage_job(client):
    app.dependency_overrides[get_current_active_superuser] = _admin_user
    try:
        response = client.post("/api/admin/background-tasks/storage_analysis/trigger")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_key"] == "storage_analysis"
    assert payload["queued"] is True
    assert payload["queue_task_id"]


def test_admin_background_tasks_can_trigger_codex_diary_job(client, monkeypatch):
    app.dependency_overrides[get_current_active_superuser] = _admin_user
    monkeypatch.setattr(
        "backend.api.notes.maybe_enqueue_codex_diary_yesterday_import",
        lambda *, trigger_reason="scheduled": f"queued-{trigger_reason}",
    )
    try:
        response = client.post("/api/admin/background-tasks/codex_diary_yesterday_import/trigger")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_key"] == "codex_diary_yesterday_import"
    assert payload["queued"] is True
    assert payload["queue_task_id"] == "queued-manual_admin"
