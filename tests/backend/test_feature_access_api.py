from backend.app import app
from backend.core.auth import get_current_active_superuser
from backend.models import User


def _override_superuser():
    admin_user = User(
        id=999,
        username="admin",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )
    app.dependency_overrides[get_current_active_superuser] = lambda: admin_user
    return admin_user


def test_access_context_anonymous_uses_registry_defaults(client):
    response = client.get("/api/access/context")

    assert response.status_code == 200
    payload = response.json()
    flat_items = payload["flat_items"]

    assert payload["subject"] == {
        "kind": "anonymous",
        "is_authenticated": False,
        "is_superuser": False,
        "user_id": None,
        "username": None,
    }
    assert flat_items["home"]["effective_value"] is True
    assert flat_items["tools.ai-chat"]["effective_value"] is True
    assert flat_items["cluster.tasks"]["effective_value"] is False
    assert flat_items["admin.accounts"]["effective_value"] is False
    assert "home" in payload["effective_keys"]
    assert "admin.accounts" not in payload["effective_keys"]


def test_access_context_user_inherits_anonymous_defaults(client, auth_user):
    response = client.get("/api/access/context")

    assert response.status_code == 200
    payload = response.json()
    flat_items = payload["flat_items"]

    assert payload["subject"]["kind"] == "user"
    assert payload["subject"]["user_id"] == auth_user.id
    assert flat_items["home"]["effective_value"] is True
    assert flat_items["tools.ai-chat"]["effective_value"] is True
    assert flat_items["cluster-tools"]["effective_value"] is False
    assert flat_items["cluster-tools"]["source"] == "inherit_anonymous"
    assert flat_items["cluster.tasks"]["effective_value"] is False
    assert flat_items["cluster.tasks"]["source"] == "ancestor_denied"


def test_admin_feature_access_anonymous_parent_deny_forces_descendants_off(client):
    _override_superuser()
    try:
        response = client.put(
            "/api/admin/feature-access/subjects/anonymous",
            json={
                "overrides": {
                    "note-tools": "deny",
                }
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert response.status_code == 200
    payload = response.json()
    flat_items = payload["flat_items"]

    assert payload["overrides"]["note-tools"] == "deny"
    assert payload["overrides"]["notes.center"] == "deny"
    assert flat_items["note-tools"]["local_decision"] == "deny"
    assert flat_items["note-tools"]["effective_value"] is False
    assert flat_items["notes.center"]["local_decision"] == "deny"
    assert flat_items["notes.center"]["effective_value"] is False
    assert flat_items["notes.center"]["source"] == "ancestor_denied"
    assert "notes.center" not in payload["effective_keys"]


def test_admin_feature_access_user_allow_child_auto_opens_ancestors(client, session):
    member = User(
        username="member",
        hashed_password="pw",
        is_active=True,
        is_superuser=False,
    )
    session.add(member)
    session.commit()
    session.refresh(member)

    _override_superuser()
    try:
        response = client.put(
            f"/api/admin/feature-access/subjects/users/{member.id}",
            json={
                "overrides": {
                    "cluster.files": "allow",
                }
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert response.status_code == 200
    payload = response.json()
    flat_items = payload["flat_items"]

    assert payload["overrides"] == {
        "cluster-tools": "allow",
        "cluster.files": "allow",
    }
    assert flat_items["cluster-tools"]["local_decision"] == "allow"
    assert flat_items["cluster-tools"]["effective_value"] is True
    assert flat_items["cluster.files"]["local_decision"] == "allow"
    assert flat_items["cluster.files"]["effective_value"] is True
    assert "cluster.files" in payload["effective_keys"]


def test_admin_feature_access_user_deny_parent_closes_descendants(client, session):
    member = User(
        username="member-cascade",
        hashed_password="pw",
        is_active=True,
        is_superuser=False,
    )
    session.add(member)
    session.commit()
    session.refresh(member)

    _override_superuser()
    try:
        open_response = client.put(
            f"/api/admin/feature-access/subjects/users/{member.id}",
            json={
                "overrides": {
                    "cluster.files": "allow",
                }
            },
        )
        close_response = client.put(
            f"/api/admin/feature-access/subjects/users/{member.id}",
            json={
                "overrides": {
                    "cluster-tools": "deny",
                    "cluster.files": "allow",
                }
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert open_response.status_code == 200
    assert close_response.status_code == 200

    payload = close_response.json()
    flat_items = payload["flat_items"]

    assert payload["overrides"]["cluster-tools"] == "deny"
    assert payload["overrides"]["cluster.files"] == "deny"
    assert flat_items["cluster-tools"]["local_decision"] == "deny"
    assert flat_items["cluster-tools"]["effective_value"] is False
    assert flat_items["cluster.files"]["local_decision"] == "deny"
    assert flat_items["cluster.files"]["effective_value"] is False
    assert "cluster.files" not in payload["effective_keys"]


def test_admin_feature_access_rejects_superuser_specific_override(client, session):
    member = User(
        username="super-member",
        hashed_password="pw",
        is_active=True,
        is_superuser=True,
    )
    session.add(member)
    session.commit()
    session.refresh(member)

    _override_superuser()
    try:
        response = client.put(
            f"/api/admin/feature-access/subjects/users/{member.id}",
            json={
                "overrides": {
                    "cluster.tasks": "allow",
                }
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert response.status_code == 400
    assert response.json()["detail"] == "超级管理员无需单独配置功能权限"


def test_feature_access_denied_note_api_returns_403(client):
    _override_superuser()
    try:
        update_response = client.put(
            "/api/admin/feature-access/subjects/anonymous",
            json={
                "overrides": {
                    "note-tools": "deny",
                }
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert update_response.status_code == 200

    response = client.post(
        "/api/notes/query",
        json={
            "scope": {"mode": "all"},
            "rules": [],
            "order_by": "updated_at",
            "order_desc": True,
            "limit": 10,
            "include_edges": False,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "当前账号无权访问该功能"


def test_feature_access_denied_fanxiu_api_returns_403(client):
    _override_superuser()
    try:
        update_response = client.put(
            "/api/admin/feature-access/subjects/anonymous",
            json={
                "overrides": {
                    "fanxiu": "deny",
                }
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_active_superuser, None)

    assert update_response.status_code == 200

    response = client.get("/api/fanxiu/status")

    assert response.status_code == 403
    assert response.json()["detail"] == "当前账号无权访问该功能"
