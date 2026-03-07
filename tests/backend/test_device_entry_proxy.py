from backend.models import UserDevice


def test_local_entry_proxy_create_and_list_tasks(client, auth_user, test_device):
    entry_resp = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_resp.status_code == 200
    entry_id = entry_resp.json()["id"]

    create_resp = client.post(
        f"/api/device-entries/{entry_id}/task/create",
        json={
            "name": "Proxy Local Task",
            "command": "python -V",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["device_id"] == test_device["id"]

    list_resp = client.get(f"/api/device-entries/{entry_id}/task/")
    assert list_resp.status_code == 200
    tasks = list_resp.json()
    assert len(tasks) == 1
    assert tasks[0]["name"] == "Proxy Local Task"
    assert tasks[0]["device_id"] == test_device["id"]
    assert tasks[0]["status"]["running"] is False


def test_remote_entry_proxy_forwards_request(client, session, auth_user, monkeypatch):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id="remote-device-1",
        mode="remote",
        name="Remote Device",
        server_url="http://remote-device:8000",
        token="remote-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return [{"id": "task-1", "name": "Remote Task", "status": {"running": False}}]

        @property
        def content(self):
            return b'[]'

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    resp = client.get(f"/api/device-entries/{entry.entry_id}/task/")
    assert resp.status_code == 200
    assert resp.json()[0]["name"] == "Remote Task"

    assert captured["method"] == "GET"
    assert captured["url"] == "http://remote-device:8000/api/task/"
    assert captured["headers"]["Authorization"] == "Bearer remote-token"
    assert captured["headers"]["X-Device-Token"] == "remote-token"
    assert captured["timeout"] == 10
