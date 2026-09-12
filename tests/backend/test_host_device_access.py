"""本机 entry 是部署主机能力，不能由普通账号自助建立或取得凭据。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import device
from backend.models import User, UserDevice
from backend.schemas import UserDeviceCreate, UserDeviceUpdate


@pytest.fixture
def account(session, monkeypatch):
    user = User(username="device-account", hashed_password="unused")
    session.add(user)
    session.commit()
    session.refresh(user)
    monkeypatch.setattr(device, "get_device_id", lambda: "deployment-host")
    monkeypatch.setattr("backend.core.devices.device.get_device_id", lambda: "deployment-host")
    return user


def assert_forbidden(operation):
    with pytest.raises(HTTPException) as error:
        operation()
    assert error.value.status_code == 403


@pytest.mark.parametrize("mode", ["local", "remote"])
def test_account_cannot_create_or_read_host_entry(session, account, monkeypatch, mode):
    def forbidden_secret_read():
        pytest.fail("未授权请求不得读取部署主机令牌")

    monkeypatch.setattr(device, "get_device_token", forbidden_secret_read)
    request = UserDeviceCreate(
        mode=mode, token="untrusted", device_id="deployment-host" if mode == "remote" else None,
        server_url="https://code4101.com" if mode == "remote" else None,
    )
    assert_forbidden(lambda: device.add_user_device(request, session=session, current_user=account))
    legacy = UserDevice(user_id=account.id, name="legacy", mode=mode, device_id="deployment-host", token="legacy")
    session.add(legacy)
    session.commit()
    assert_forbidden(lambda: device.read_user_device_token(legacy.entry_id, session=session, current_user=account))
    assert_forbidden(lambda: device.update_user_device(
        legacy.entry_id, UserDeviceUpdate(token="replacement"), session=session, current_user=account,
    ))


def test_remote_identity_discovery_cannot_convert_entry_to_host(session, account, monkeypatch):
    monkeypatch.setattr(device.requests, "get", lambda *a, **kw: SimpleNamespace(
        status_code=200, json=lambda: {"id": "deployment-host", "hostname": "host"},
    ))
    assert_forbidden(lambda: device.add_user_device(
        UserDeviceCreate(mode="remote", token="supplied", server_url="https://code4101.com"),
        session=session, current_user=account,
    ))
    entry = UserDevice(user_id=account.id, name="remote", mode="remote", device_id="other-device", token="own-token", server_url="https://remote.example")
    session.add(entry)
    session.commit()
    assert_forbidden(lambda: device.update_user_device(
        entry.entry_id, UserDeviceUpdate(server_url="https://code4101.com"), session=session, current_user=account,
    ))
    session.refresh(entry)
    assert entry.device_id == "other-device"
    assert entry.server_url == "https://remote.example"
    assert entry.token == "own-token"


def test_own_remote_entry_remains_usable_and_cannot_change_mode(session, account):
    entry = device.add_user_device(
        UserDeviceCreate(mode="remote", device_id="other-device", token="own-token", server_url="https://remote.example"),
        session=session, current_user=account,
    )
    updated = device.update_user_device(
        entry.id, UserDeviceUpdate.model_validate({"mode": "local", "device_id": "deployment-host", "name": "renamed"}),
        session=session, current_user=account,
    )
    assert updated.mode == "remote"
    assert updated.device_id == "other-device"
    assert updated.name == "renamed"
    assert device.read_user_device_token(entry.id, session=session, current_user=account).token == "own-token"


def test_admin_can_create_and_read_host_entry(session, account, monkeypatch):
    account.is_superuser = True
    monkeypatch.setattr(device, "get_device_token", lambda: "host-token")
    entry = device.add_user_device(UserDeviceCreate(mode="local", token=""), session=session, current_user=account)
    assert entry.device_id == "deployment-host"
    assert device.read_user_device_token(entry.id, session=session, current_user=account).token == "host-token"


@pytest.mark.parametrize("mode", ["local", "remote"])
def test_legacy_host_entry_cannot_be_used_by_fanxiu_account(client, auth_user, session, monkeypatch, mode):
    monkeypatch.setattr("backend.core.devices.device.get_device_id", lambda: "deployment-host")
    entry = UserDevice(user_id=auth_user.id, name="legacy-host", mode=mode, device_id="deployment-host", token="legacy")
    session.add(entry)
    session.commit()
    response = client.post("/api/fanxiu/game-window2/screencap", json={"entry_id": entry.entry_id})
    assert response.status_code == 403, response.text
