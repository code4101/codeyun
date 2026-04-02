from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import app
from backend.core.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.models import User


class ImmediateThread:
    def __init__(self, *, target, kwargs=None, daemon=None):
        self._target = target
        self._kwargs = kwargs or {}
        self.daemon = daemon

    def start(self):
        self._target(**self._kwargs)


def _override_user(user: User):
    app.dependency_overrides[get_current_user_from_token] = lambda: user
    app.dependency_overrides[get_optional_current_user_from_token] = lambda: user


def _clear_user_override():
    app.dependency_overrides.pop(get_current_user_from_token, None)
    app.dependency_overrides.pop(get_optional_current_user_from_token, None)


def _create_admin_user(session) -> User:
    user = User(
        username="admin-user",
        email="admin@example.com",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_attendance_config_requires_admin(client: TestClient, auth_user):
    response = client.get("/api/attendance/config")
    assert response.status_code == 403


def test_attendance_config_and_template_crud(client: TestClient, session, test_device):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    try:
        add_device_resp = client.post(
            "/api/devices/add",
            json={
                "mode": "local",
                "token": "attendance-local-token",
                "alias": "当前考勤设备",
            },
        )
        assert add_device_resp.status_code == 200
        entry_id = add_device_resp.json()["id"]

        account_resp = client.post(
            "/api/attendance/accounts",
            json={
                "login_username": "18850000000",
                "password": "plain-pass",
            },
        )
        assert account_resp.status_code == 200
        account = account_resp.json()
        assert account["password"] == "plain-pass"
        assert account["name"] == "18850000000"

        template_resp = client.post(
            "/api/attendance/templates",
            json={
                "name": "问题反馈表",
                "activity_id": "264266843",
                "is_active": True,
            },
        )
        assert template_resp.status_code == 200
        template = template_resp.json()
        assert template["activity_id"] == "264266843"

        config_resp = client.put(
            "/api/attendance/config",
            json={
                "current_wjx_account_id": account["id"],
                "execution_device_entry_id": entry_id,
            },
        )
        assert config_resp.status_code == 200
        config = config_resp.json()
        assert config["service"]["current_wjx_account_id"] == account["id"]
        assert config["service"]["execution_device_entry_id"] == entry_id
        assert config["current_account"]["password"] == "plain-pass"
        assert config["current_execution_device"]["entry_id"] == entry_id
        assert config["fixed_wjx_template"]["activity_id"] == "264266843"
    finally:
        _clear_user_override()


def test_attendance_run_completes_and_persists_global_selection(client: TestClient, session, monkeypatch, test_device):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    try:
        add_device_resp = client.post(
            "/api/devices/add",
            json={
                "mode": "local",
                "token": "attendance-local-token",
                "alias": "当前考勤设备",
            },
        )
        assert add_device_resp.status_code == 200
        entry_id = add_device_resp.json()["id"]

        account = client.post(
            "/api/attendance/accounts",
            json={
                "login_username": "18850000001",
                "password": "plain-pass",
            },
        ).json()

        config_resp = client.put(
            "/api/attendance/config",
            json={
                "execution_device_entry_id": entry_id,
            },
        )
        assert config_resp.status_code == 200

        monkeypatch.setattr(
            "backend.api.attendance._execute_run_on_entry",
            lambda entry_snapshot, execution_payload: {
                "action": execution_payload["action"],
                "visible_names": ["20260301第44届觉观", "20260309梵呗初阶"],
                "hidden_applied": ["20260301第44届觉观"],
                "added_applied": ["20260401第45届觉观"],
            },
        )
        monkeypatch.setattr("backend.api.attendance.threading.Thread", ImmediateThread)

        run_resp = client.post(
            "/api/attendance/wjx-runs",
            json={
                "action": "apply",
                "hide": ["20260301第44届觉观"],
                "add": ["20260401第45届觉观"],
            },
        )
        assert run_resp.status_code == 200
        run_id = run_resp.json()["id"]

        fetched_run = client.get(f"/api/attendance/wjx-runs/{run_id}")
        assert fetched_run.status_code == 200
        run = fetched_run.json()
        assert run["status"] == "completed"
        assert run["template_id"] == "wjx-course-catalog"
        assert run["result"]["added_applied"] == ["20260401第45届觉观"]

        config_resp = client.get("/api/attendance/config")
        assert config_resp.status_code == 200
        config = config_resp.json()
        assert config["service"]["current_wjx_account_id"] == account["id"]
        assert config["service"]["execution_device_entry_id"] == entry_id
    finally:
        _clear_user_override()


def test_attendance_config_rejects_inactive_execution_device(client: TestClient, session, test_device):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    try:
        add_device_resp = client.post(
            "/api/devices/add",
            json={
                "mode": "local",
                "token": "attendance-local-token",
                "alias": "当前考勤设备",
            },
        )
        assert add_device_resp.status_code == 200
        entry_id = add_device_resp.json()["id"]

        disable_resp = client.put(
            f"/api/devices/{entry_id}",
            json={
                "is_active": False,
            },
        )
        assert disable_resp.status_code == 200

        config_resp = client.put(
            "/api/attendance/config",
            json={
                "execution_device_entry_id": entry_id,
            },
        )
        assert config_resp.status_code == 400
        assert config_resp.json()["detail"] == "当前执行设备已停用"
    finally:
        _clear_user_override()


def test_attendance_account_is_singleton_and_uses_login_as_name(client: TestClient, session):
    admin_user = _create_admin_user(session)
    _override_user(admin_user)

    try:
        first_resp = client.post(
            "/api/attendance/accounts",
            json={
                "login_username": "18850000002",
                "password": "plain-pass",
            },
        )
        assert first_resp.status_code == 200
        first_account = first_resp.json()
        assert first_account["name"] == "18850000002"

        second_resp = client.post(
            "/api/attendance/accounts",
            json={
                "login_username": "18850000003",
                "password": "other-pass",
            },
        )
        assert second_resp.status_code == 400
        assert second_resp.json()["detail"] == "问卷星账号只支持一个，请直接编辑现有账号"

        update_resp = client.put(
            f"/api/attendance/accounts/{first_account['id']}",
            json={
                "login_username": "18850000009",
            },
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["login_username"] == "18850000009"
        assert updated["name"] == "18850000009"
    finally:
        _clear_user_override()
