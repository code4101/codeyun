import json

from backend.app import app
from backend.core.access.auth import get_current_active_superuser
from backend.core.access import feature_access as feature_access_module
from backend.models import User


EXPECTED_DEFAULT_ANONYMOUS_KEYS = {
    "home",
    "author-contact",
    "tools",
    "tools.password-generator",
    "tools.image-browser",
    "tools.color-tools",
    "attendance-tools",
    "attendance.wjx-feedback",
    "game-tools",
    "fanxiu",
    "fanxiu.calculator",
    "fanxiu.draw-calc",
    "fanxiu.discount",
    "fanxiu.lottery-model",
    "fanxiu.activity-list",
    "fanxiu.activity-list.kunlun-secret",
    "fanxiu.activity-list.modao-invasion",
    "fanxiu.activity-list.shouyuan-exploration",
    "fanxiu.activity-list.divine-resource",
    "fanxiu.activity-list.qiji-zhumo",
    "fanxiu.activity-list.xianzhou-marathon",
    "fanxiu.wiki",
    "fanxiu.inventory",
    "fanxiu.inventory.wardrobe-hall",
    "fanxiu.inventory.spirit-beast-hall",
    "fanxiu.inventory.magic-treasure-hall",
    "fanxiu.inventory.magic-treasure-formations",
    "fanxiu.inventory.spirit-artifact-hall",
    "fanxiu.labelme",
    "fanxiu.recharge",
    "fanxiu.cuijian-trial",
    "dsp.calculator",
    "magic-craft",
    "magic-craft.xor-matrix",
    "note-tools",
    "notes.center",
    "notes.chat-data",
    "notes.infinite-canvas",
}


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
    assert set(payload["overrides"]) == EXPECTED_DEFAULT_ANONYMOUS_KEYS
    assert set(payload["overrides"].values()) == {"allow"}
    assert flat_items["home"]["effective_value"] is True
    assert flat_items["tools.password-generator"]["effective_value"] is True
    assert flat_items["tools.ai-chat"]["effective_value"] is False
    assert flat_items["fanxiu.calculator"]["effective_value"] is True
    assert flat_items["fanxiu.data-annotation"]["effective_value"] is False
    assert flat_items["notes.center"]["effective_value"] is True
    assert flat_items["cluster.tasks"]["effective_value"] is False
    assert flat_items["admin.accounts"]["effective_value"] is False
    assert set(payload["effective_keys"]) == EXPECTED_DEFAULT_ANONYMOUS_KEYS
    assert "admin.accounts" not in payload["effective_keys"]


def test_access_context_user_inherits_anonymous_defaults(client, auth_user):
    response = client.get("/api/access/context")

    assert response.status_code == 200
    payload = response.json()
    flat_items = payload["flat_items"]

    assert payload["subject"]["kind"] == "user"
    assert payload["subject"]["user_id"] == auth_user.id
    assert flat_items["home"]["effective_value"] is True
    assert flat_items["home"]["source"] == "inherit_anonymous"
    assert flat_items["tools.password-generator"]["effective_value"] is True
    assert flat_items["tools.ai-chat"]["effective_value"] is False
    assert flat_items["fanxiu.calculator"]["effective_value"] is True
    assert flat_items["fanxiu.data-annotation"]["effective_value"] is False
    assert flat_items["cluster-tools"]["effective_value"] is False
    assert flat_items["cluster-tools"]["source"] == "inherit_anonymous"
    assert flat_items["cluster.tasks"]["effective_value"] is False
    assert flat_items["cluster.tasks"]["source"] == "ancestor_denied"
    assert set(payload["effective_keys"]) == EXPECTED_DEFAULT_ANONYMOUS_KEYS


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

    response = client.get("/api/fanxiu/data-annotation/runtime/status")

    assert response.status_code == 403
    assert response.json()["detail"] == "当前账号无权访问该功能"


def test_feature_access_registry_reloads_when_registry_file_changes(tmp_path, monkeypatch):
    registry_path = tmp_path / "permissionRegistry.json"
    registry_payload = {
        "version": 1,
        "nodes": [
            {
                "key": "attendance.orders",
                "title": "订单操作",
                "node_type": "feature",
                "sort_order": 10,
                "route_paths": ["/attendance/orders"],
                "menu_paths": ["/attendance/orders"],
                "api_scopes": ["attendance-tools.orders"],
                "default_anonymous_allow": False,
            }
        ],
    }
    registry_path.write_text(json.dumps(registry_payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(feature_access_module, "FEATURE_ACCESS_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(feature_access_module, "iter_plugin_permission_registry_files", lambda: ())
    feature_access_module.clear_feature_access_registry_cache()

    initial_registry = feature_access_module.load_feature_access_registry()
    assert initial_registry.node_map["attendance.orders"].title == "订单操作"

    registry_payload["nodes"][0]["title"] = "订单"
    registry_path.write_text(json.dumps(registry_payload, ensure_ascii=False), encoding="utf-8")

    reloaded_registry = feature_access_module.load_feature_access_registry()
    assert reloaded_registry.node_map["attendance.orders"].title == "订单"
