from sqlmodel import Session, select

import backend.api.task_manager as task_manager_api
from backend.models import User, UserDevice


def test_add_devices_append_order(client, session, auth_user):
    resp1 = client.post(
        "/api/devices/add",
        json={
            "mode": "remote",
            "device_id": "remote-1",
            "token": "token-1",
            "alias": "Node 1",
            "server_url": "http://node-1:8000",
        },
    )
    resp2 = client.post(
        "/api/devices/add",
        json={
            "mode": "remote",
            "device_id": "remote-2",
            "token": "token-2",
            "alias": "Node 2",
            "server_url": "http://node-2:8000",
        },
    )

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    devices = client.get("/api/devices/").json()
    assert [device["device_id"] for device in devices] == ["remote-1", "remote-2"]
    assert [device["mode"] for device in devices] == ["remote", "remote"]

    links = session.exec(
        select(UserDevice)
        .where(UserDevice.user_id == auth_user.id)
        .order_by(UserDevice.order_index)
    ).all()
    assert [(link.device_id, link.order_index) for link in links] == [("remote-1", 0), ("remote-2", 1)]


def test_add_remote_device_without_device_id_detects_identity(client, auth_user, monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"id": "auto-remote-device", "hostname": "codepc_auto"}

    def fake_get(url, headers=None, proxies=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["proxies"] = proxies
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("backend.api.device.requests.get", fake_get)

    resp = client.post(
        "/api/devices/add",
        json={
            "mode": "remote",
            "token": "auto-token",
            "server_url": "http://auto-node:8000",
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["device_id"] == "auto-remote-device"
    assert payload["name"] == "codepc_auto"
    assert payload["server_url"] == "http://auto-node:8000"
    assert captured["url"] == "http://auto-node:8000/api/device-control/status"
    assert captured["headers"]["Authorization"] == "Bearer auto-token"
    assert captured["headers"]["X-Device-Token"] == "auto-token"
    assert captured["proxies"] == {"http": "", "https": "", "all": "", "no_proxy": "*"}
    assert captured["timeout"] == 10


def test_update_remote_device_connection_refreshes_identity(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="old-remote-device",
        mode="remote",
        name="Old Remote",
        server_url="http://old-node:8000",
        token="old-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"id": "new-remote-device", "hostname": "new-host"}

    monkeypatch.setattr("backend.api.device.requests.get", lambda *args, **kwargs: FakeResponse())

    resp = client.put(
        f"/api/devices/{entry.entry_id}",
        json={
            "token": "new-token",
            "server_url": "http://new-node:8000",
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["device_id"] == "new-remote-device"
    assert payload["name"] == "Old Remote"
    assert payload["server_url"] == "http://new-node:8000"

    session.refresh(entry)
    assert entry.device_id == "new-remote-device"
    assert entry.token == "new-token"
    assert entry.server_url == "http://new-node:8000"


def test_device_list_hides_token_until_revealed(client, auth_user):
    resp = client.post(
        "/api/devices/add",
        json={
            "mode": "remote",
            "device_id": "remote-token-node",
            "token": "secret-entry-token",
            "alias": "Token Node",
            "server_url": "http://token-node:8000",
        },
    )

    assert resp.status_code == 200
    created = resp.json()
    assert "token" not in created

    devices = client.get("/api/devices/").json()
    assert len(devices) == 1
    assert "token" not in devices[0]

    token_resp = client.get(f"/api/devices/{created['id']}/token")
    assert token_resp.status_code == 200
    assert token_resp.json() == {"token": "secret-entry-token"}


def test_device_token_reveal_requires_owner(client, session, auth_user):
    other_user = User(
        username="otheruser",
        email="other@example.com",
        hashed_password="pw",
        is_active=True,
    )
    session.add(other_user)
    session.commit()
    session.refresh(other_user)

    other_entry = UserDevice(
        user_id=other_user.id,
        device_id="other-device",
        mode="remote",
        token="other-secret-token",
        name="Other Device",
        server_url="http://other-device:8000",
        is_active=True,
        order_index=0,
    )
    session.add(other_entry)
    session.commit()

    token_resp = client.get(f"/api/devices/{other_entry.entry_id}/token")
    assert token_resp.status_code == 404


def test_local_device_hides_persisted_url(client, session, auth_user):
    session.add(
        UserDevice(
            user_id=auth_user.id,
            device_id="local-1",
            mode="local",
            token="local-token",
            name="Local Node",
            server_url="http://localhost:8000",
            is_active=True,
            order_index=0,
        )
    )
    session.commit()

    devices = client.get("/api/devices/").json()

    assert len(devices) == 1
    assert devices[0]["device"]["type"] == "LocalDevice"
    assert devices[0]["device"]["server_url"] is None


def test_local_device_token_reveal_uses_current_device_token(client, session, auth_user, monkeypatch):
    monkeypatch.setattr("backend.api.device.get_device_token", lambda: "current-local-token")
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="local-1",
        mode="local",
        token="stale-local-token",
        name="Local Node",
        server_url="http://localhost:8000",
        is_active=True,
        order_index=0,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    token_resp = client.get(f"/api/devices/{entry.entry_id}/token")

    assert token_resp.status_code == 200
    assert token_resp.json() == {"token": "current-local-token"}
    session.refresh(entry)
    assert entry.token == "current-local-token"


def test_remote_loopback_urls_are_rejected(client, auth_user):
    for host in ["localhost", "127.0.0.1", "[::1]"]:
        resp = client.post(
            "/api/devices/add",
            json={
                "mode": "remote",
                "device_id": "loopback-test",
                "token": "token-1",
                "alias": "Loopback",
                "server_url": f"http://{host}:8000",
            },
        )
        assert resp.status_code == 400


def test_same_device_supports_multiple_entries(client, auth_user, test_device):
    local_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    remote_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "remote",
            "device_id": test_device["id"],
            "token": "remote-entry-token",
            "alias": "当前机器-局域网",
            "server_url": "http://machine-a:8000",
        },
    )

    assert local_resp.status_code == 200
    assert remote_resp.status_code == 200

    devices = client.get("/api/devices/").json()
    assert len(devices) == 2
    assert [device["device_id"] for device in devices] == [test_device["id"], test_device["id"]]
    assert [device["mode"] for device in devices] == ["local", "remote"]
    assert devices[0]["server_url"] is None
    assert devices[1]["server_url"] == "http://machine-a:8000"


def test_same_device_remote_entry_allows_lan_ip(client, auth_user, test_device):
    resp = client.post(
        "/api/devices/add",
        json={
            "mode": "remote",
            "device_id": test_device["id"],
            "token": "remote-entry-token",
            "alias": "当前机器-局域网",
            "server_url": "http://192.168.31.63:8000",
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["mode"] == "remote"
    assert payload["device_id"] == test_device["id"]
    assert payload["server_url"] == "http://192.168.31.63:8000"


def test_local_device_rejects_url_and_device_id(client, auth_user):
    resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "device_id": "should-not-pass",
            "token": "local-entry-token",
            "alias": "当前机器",
            "server_url": "http://localhost:8000",
        },
    )

    assert resp.status_code == 400


def test_create_tasks_append_order(client, engine, test_device, monkeypatch):
    monkeypatch.setattr(task_manager_api, "engine", engine)
    monkeypatch.setattr(task_manager_api.task_manager, "scan_running_tasks", lambda restore_timeouts=False: None)

    headers = {"Authorization": f"Bearer {test_device['token']}"}

    resp1 = client.post(
        "/api/task/create",
        json={"name": "First Task", "command": "python -V"},
        headers=headers,
    )
    resp2 = client.post(
        "/api/task/create",
        json={"name": "Second Task", "command": "python -c \"print('ok')\""},
        headers=headers,
    )

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    tasks = client.get("/api/task/", headers=headers).json()
    assert [task["name"] for task in tasks] == ["First Task", "Second Task"]
    assert [task["order"] for task in tasks] == [0, 1]
