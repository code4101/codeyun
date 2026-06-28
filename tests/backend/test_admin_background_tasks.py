import time

from backend.app import app
from backend.core.auth import get_current_active_superuser
from backend.core.attendance.course_completion import COURSE_COMPLETION_TASK_KEY
from backend.core.background_task_queue import background_task_queue
from backend.core.background_task_runner import set_background_task_deleted
from backend.core.fanxiu_tianjige_crawler import FANXIU_TIANJIGE_QUIZ_TASK_KEY
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
        "ruanyf_weekly_note",
        "attendance_summary_monthly_templates",
        "media_sync_home_discovery",
        "attendance_fanbei_evening_steps",
        "attendance_fanbei_morning_steps",
        "rime_config_sync",
        "market_quote_refresh",
        "storage_analysis",
        "fanxiu_slimming",
    }.issubset(task_keys)
    assert "queue" in payload


def test_admin_background_task_catalog_includes_optional_fanxiu_crawler(client):
    app.dependency_overrides[get_current_active_superuser] = _admin_user
    try:
        response = client.get("/api/admin/background-tasks/catalog")
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert response.status_code == 200
    payload = response.json()
    items_by_key = {item["key"]: item for item in payload["items"]}
    crawler_item = items_by_key[FANXIU_TIANJIGE_QUIZ_TASK_KEY]
    assert crawler_item["title"] == "凡修天机阁抢答爬虫"
    assert crawler_item["schedule_label"] == "每周二/三/四 17:59:50"
    assert crawler_item["added"] is False


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
