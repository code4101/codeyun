import time

from backend.app import app
from backend.core.auth import get_current_active_superuser
from backend.core.attendance.course_completion import COURSE_COMPLETION_TASK_KEY
from backend.core.jobs.executor import background_task_queue
from backend.core.jobs.scheduler import set_background_task_deleted
from backend.core.jobs.scheduler import NOTE_SHEET_PAGE_SNAPSHOT_BACKFILL_TASK_KEY
from backend.core.runtime.public_frontend_deploy import PUBLIC_FRONTEND_DEPLOY_TASK_KEY
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
        "codex_diary_yesterday_import",
        "ruanyf_weekly_note",
        "attendance_summary_monthly_templates",
        "media_sync_home_discovery",
        "attendance_fanbei_evening_steps",
        "attendance_fanbei_morning_steps",
        "market_quote_refresh",
        "storage_analysis",
        PUBLIC_FRONTEND_DEPLOY_TASK_KEY,
    }.issubset(task_keys)
    assert "auto_git_commit" not in task_keys
    assert "rime_config_sync" not in task_keys
    assert "queue" in payload


def test_admin_background_task_catalog_includes_optional_attendance_course_completion(client):
    app.dependency_overrides[get_current_active_superuser] = _admin_user
    try:
        response = client.get("/api/admin/background-tasks/catalog")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert response.status_code == 200
    payload = response.json()
    items_by_key = {item["key"]: item for item in payload["items"]}
    item = items_by_key[COURSE_COMPLETION_TASK_KEY]
    assert item["title"] == "考勤课程自动收尾"
    assert item["schedule_label"] == "每天 06:20"
    assert item["added"] is False


def test_admin_background_task_catalog_includes_optional_note_sheet_snapshot_backfill(client):
    app.dependency_overrides[get_current_active_superuser] = _admin_user
    try:
        response = client.get("/api/admin/background-tasks/catalog")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert response.status_code == 200
    payload = response.json()
    items_by_key = {item["key"]: item for item in payload["items"]}
    item = items_by_key[NOTE_SHEET_PAGE_SNAPSHOT_BACKFILL_TASK_KEY]
    assert item["title"] == "星云表格快照补齐"
    assert item["category"] == "表格"
    assert item["schedule_label"] == "未配置自动触发"
    assert item["added"] is False


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


def test_admin_background_tasks_can_trigger_fanbei_placeholder_job(client):
    app.dependency_overrides[get_current_active_superuser] = _admin_user
    try:
        response = client.post("/api/admin/background-tasks/attendance_fanbei_evening_steps/trigger")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_key"] == "attendance_fanbei_evening_steps"
    assert payload["queued"] is True
    assert payload["queue_task_id"]


def test_admin_background_tasks_can_delete_queue_record(client):
    background_task_queue.reset_for_tests()

    task_id = background_task_queue.enqueue("delete-me", lambda: None)
    deadline = time.time() + 3
    while time.time() < deadline:
        snapshot = background_task_queue.snapshot()
        if snapshot["is_idle"] and any(item["id"] == task_id for item in snapshot["recent"]):
            break
        time.sleep(0.02)

    app.dependency_overrides[get_current_active_superuser] = _admin_user
    try:
        response = client.delete(f"/api/admin/background-tasks/queue/{task_id}")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)
        background_task_queue.reset_for_tests()

    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_admin_background_tasks_can_delete_managed_task(client):
    app.dependency_overrides[get_current_active_superuser] = _admin_user
    try:
        response = client.delete("/api/admin/background-tasks/storage_analysis")
        status_response = client.get("/api/admin/background-tasks/status")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)
        set_background_task_deleted("storage_analysis", False)

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    task_keys = {item["key"] for item in status_response.json()["tasks"]}
    assert "storage_analysis" not in task_keys
