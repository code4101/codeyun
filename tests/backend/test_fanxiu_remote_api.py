import pytest

from backend.core.access.feature_access import save_feature_access_policy_overrides
from backend.core.access.auth import get_password_hash
from backend.models import User


PREFIX = "/api/fanxiu/remote"


def grant(session, user, decision="allow"):
    save_feature_access_policy_overrides(session, subject_type="user", subject_user_id=user.id,
                                         overrides={"fanxiu.remote": decision})


def test_remote_requires_login_and_does_not_accept_device_token(client):
    for headers in ({}, {"X-Device-Token": "not-an-account-token"}):
        assert client.get(PREFIX + "/capabilities", headers=headers).status_code == 401


def test_capabilities_grant_and_revocation_on_existing_session(client, session, auth_user):
    assert client.get(PREFIX + "/capabilities").status_code == 403
    grant(session, auth_user)
    response = client.get(PREFIX + "/capabilities")
    assert response.status_code == 200
    assert {job["id"] for job in response.json()["jobs"]} == {"observe", "navigate", "runtime_probe"}
    created = client.post(PREFIX + "/sessions", json={"device_id": "test", "job_id": "observe"})
    assert created.status_code == 201
    sid = created.json()["session_id"]
    grant(session, auth_user, "deny")
    assert client.delete(PREFIX + f"/sessions/{sid}").status_code == 403


def test_remote_schema_rejects_arbitrary_commands_and_invalid_images(client, session, auth_user):
    grant(session, auth_user)
    assert client.post(PREFIX + "/sessions", json={"device_id": "schema-test", "job_id": "shell"}).status_code == 422
    assert client.post(PREFIX + "/sessions", json={"device_id": "schema-test", "job_id": "navigate"}).status_code == 422
    sid = client.post(PREFIX + "/sessions", json={"device_id": "schema-test", "job_id": "observe"}).json()["session_id"]
    request = {"request_id": "r1", "frame_id": "f1", "image_base64": "!invalid"}
    assert client.post(PREFIX + f"/sessions/{sid}/step", json=request).status_code == 422
    assert client.delete(PREFIX + f"/sessions/{sid}").status_code == 200


def test_inactive_account_is_rejected(client, session, auth_user):
    grant(session, auth_user)
    auth_user.is_active = False
    assert client.get(PREFIX + "/capabilities").status_code == 400


def test_real_registration_login_and_admin_grant_workflow(client, session):
    """Real JWT/SQL authorization path; no real account or game is modified."""
    admin = User(username="remote-test-admin", hashed_password=get_password_hash("test-secret"),
                 is_superuser=True)
    session.add(admin)
    session.commit()
    registered = client.post("/api/auth/register", json={"username": "new-remote-user", "password": "test-password"})
    assert registered.status_code == 200
    user_id = registered.json()["id"]
    assert registered.json()["is_superuser"] is False
    login = client.post("/api/auth/login/json", json={"username": "new-remote-user", "password": "test-password"})
    user_headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert client.get(PREFIX + "/capabilities", headers=user_headers).status_code == 403

    endpoint = f"/api/admin/feature-access/subjects/users/{user_id}"
    assert client.put(endpoint, headers=user_headers, json={"overrides": {"fanxiu.remote": "allow"}}).status_code == 400
    admin_login = client.post("/api/auth/login/json", json={"username": admin.username, "password": "test-secret"})
    admin_headers = {"Authorization": "Bearer " + admin_login.json()["access_token"]}
    assert client.put(endpoint, headers=admin_headers, json={"overrides": {"fanxiu.remote": "allow"}}).status_code == 200
    assert client.get(PREFIX + "/capabilities", headers=user_headers).status_code == 200
    created = client.post(PREFIX + "/sessions", headers=user_headers, json={"device_id": "jwt-test", "job_id": "observe"})
    assert created.status_code == 201
    sid = created.json()["session_id"]
    # Valid base64 with invalid image must fail closed, never capture the host.
    rejected = client.post(PREFIX + f"/sessions/{sid}/step", headers=user_headers,
                           json={"request_id": "bad-image", "frame_id": "bad-image", "image_base64": "YWJjZA=="})
    assert rejected.status_code == 422
    assert client.delete(PREFIX + f"/sessions/{sid}", headers=user_headers).status_code == 200
    assert client.put(endpoint, headers=admin_headers, json={"overrides": {"fanxiu.remote": "deny"}}).status_code == 200
    assert client.get(PREFIX + "/capabilities", headers=user_headers).status_code == 403


def test_action_schema_preserves_integer_pixel_contract():
    from backend.api.fanxiu_remote import RemoteAction
    action = RemoteAction(action_id="a", frame_id="f", kind="tap", expires_at=100, x=74, y=393)
    assert type(action.model_dump()["x"]) is int


def test_runtime_is_requested_on_demand_and_works_without_screenshot(client, session, auth_user):
    import time
    grant(session, auth_user)
    sid = client.post(PREFIX + "/sessions", json={"device_id": "runtime-test", "job_id": "runtime_probe"}).json()["session_id"]
    first = client.post(PREFIX + f"/sessions/{sid}/step", json={"request_id": "r1", "frame_id": "o1"})
    assert first.status_code == 200
    action = first.json()["action"]
    assert action["kind"] == "collect_runtime" and action["query"] == "process"
    snapshot = {"schema_version": 1, "query": "process", "source": "adb_proc_mem",
                "captured_at": time.time(), "completeness": "complete",
                "process_identity": {"package_name": "game", "pid": 42, "start_ticks": 1000, "device_serial": "local-test"},
                "facts": {"elf_class": 64}}
    second = client.post(PREFIX + f"/sessions/{sid}/step", json={"request_id": "r2", "frame_id": "o2",
                         "previous_action_id": action["action_id"], "previous_action_status": "executed",
                         "runtime_snapshot": snapshot})
    assert second.status_code == 200
    assert second.json()["status"] == "completed"
    assert second.json()["observation"]["runtime"]["facts"]["elf_class"] == 64
    assert client.delete(PREFIX + f"/sessions/{sid}").status_code == 200
