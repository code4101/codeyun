"""远程客户端使用普通账号授权；权限不扩散到标注和本机调度。"""

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from backend.core.access.feature_access import (
    build_feature_access_admin_subject_context,
    is_feature_access_allowed,
    save_feature_access_policy_overrides,
)
from backend.core.access.feature_access_guard import ensure_feature_access
from backend.models import User


@pytest.fixture
def remote_users(session):
    users = [User(username=name, hashed_password="unused") for name in ("remote-owner", "other")]
    session.add_all(users)
    session.commit()
    for user in users:
        session.refresh(user)
    return users


def test_remote_defaults_deny_anonymous_and_unassigned_accounts(session, remote_users):
    for user in (None, *remote_users):
        with pytest.raises(HTTPException) as error:
            ensure_feature_access(session, feature_key="fanxiu.remote", current_user=user)
        assert error.value.status_code == 403


def test_remote_account_grant_is_visible_to_existing_admin_ui_and_isolated(session, remote_users):
    owner, other = remote_users
    save_feature_access_policy_overrides(
        session, subject_type="user", subject_user_id=owner.id,
        overrides={"fanxiu.remote": "allow"},
    )
    assert ensure_feature_access(session, feature_key="fanxiu.remote", current_user=owner) is owner
    assert not is_feature_access_allowed(session, feature_key="fanxiu.remote", current_user=other)
    assert not is_feature_access_allowed(session, feature_key="fanxiu.remote", current_user=None)
    for feature in ("fanxiu.data-annotation", "fanxiu.kernel-scheduler", "fanxiu.instrumentation", "admin.accounts"):
        assert not is_feature_access_allowed(session, feature_key=feature, current_user=owner)
    context = build_feature_access_admin_subject_context(
        session, subject_type="user", subject_user=owner,
    )
    item = context["flat_items"]["fanxiu.remote"]
    assert item["title"] == "凡修远程客户端"
    assert item["local_decision"] == "allow"
    assert item["effective_value"] is True


@pytest.mark.parametrize("overrides", [{}, {"fanxiu.remote": "deny"}])
def test_remote_revocation_applies_to_next_request_with_same_user(engine, session, remote_users, overrides):
    owner, _ = remote_users
    save_feature_access_policy_overrides(
        session, subject_type="user", subject_user_id=owner.id,
        overrides={"fanxiu.remote": "allow"},
    )
    assert ensure_feature_access(session, feature_key="fanxiu.remote", current_user=owner) is owner
    with Session(engine) as admin_session:
        save_feature_access_policy_overrides(
            admin_session, subject_type="user", subject_user_id=owner.id, overrides=overrides,
        )
    with Session(engine) as next_request:
        with pytest.raises(HTTPException) as error:
            ensure_feature_access(next_request, feature_key="fanxiu.remote", current_user=owner)
    assert error.value.status_code == 403


@pytest.mark.parametrize("remote_granted", [False, True])
def test_local_instrumentation_rejects_remote_accounts_before_touching_runtime(
    session, remote_users, remote_granted, monkeypatch,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import fanxiu_instrumentation
    from backend.core.access.auth import get_current_active_user
    from backend.db import get_session

    owner, _ = remote_users
    if remote_granted:
        save_feature_access_policy_overrides(
            session, subject_type="user", subject_user_id=owner.id,
            overrides={"fanxiu.remote": "allow"},
        )
    def unexpected_runtime_access(*args, **kwargs):
        pytest.fail("拒绝请求不能接触部署主机 Runtime")

    monkeypatch.setattr(fanxiu_instrumentation.fanxiu_instrumentation_service, "inspect", unexpected_runtime_access)
    monkeypatch.setattr(fanxiu_instrumentation.fanxiu_instrumentation_service, "ensure_server", unexpected_runtime_access)
    app = FastAPI()
    app.include_router(fanxiu_instrumentation.router)
    app.dependency_overrides[get_current_active_user] = lambda: owner
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        assert client.get("/dynamic-instrumentation/status").status_code == 403
        assert client.post("/dynamic-instrumentation/server/ensure", json={}).status_code == 403


def test_local_instrumentation_can_be_granted_independently(session, remote_users):
    owner, _ = remote_users
    save_feature_access_policy_overrides(
        session, subject_type="user", subject_user_id=owner.id,
        overrides={"fanxiu.instrumentation": "allow"},
    )
    assert ensure_feature_access(session, feature_key="fanxiu.instrumentation", current_user=owner) is owner
    assert not is_feature_access_allowed(session, feature_key="fanxiu.remote", current_user=owner)


def test_remote_account_cannot_access_global_kernel_or_info_window(client, auth_user, session):
    save_feature_access_policy_overrides(
        session, subject_type="user", subject_user_id=auth_user.id,
        overrides={"fanxiu.remote": "allow"},
    )
    for path in ("status", "info-window", "settings", "world-facts"):
        response = client.get(f"/api/fanxiu/kernel-scheduler/{path}")
        assert response.status_code == 403, (path, response.text)
    response = client.post("/api/fanxiu/kernel-scheduler/info-window/settings", json={})
    assert response.status_code == 403
    response = client.post("/api/fanxiu/kernel-scheduler/kernel/restart", json={})
    assert response.status_code == 403
