import nbformat
import pytest

from backend.app import app
from backend.core.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.core.feature_access import FEATURE_ACCESS_SUBJECT_USER, save_feature_access_policy_overrides
from backend.core.notebook_lab import reset_notebook_lab_runtime
from backend.core.settings import get_settings
from backend.models import User, UserDevice


@pytest.fixture(autouse=True)
def _isolated_notebook_workdir(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEYUN_AI_NOTEBOOK_WORKDIR", str(tmp_path / "ai-notebooks"))
    get_settings.cache_clear()
    reset_notebook_lab_runtime()
    yield
    reset_notebook_lab_runtime()
    get_settings.cache_clear()


def _make_admin(session, user: User) -> User:
    user.is_superuser = True
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _create_local_entry(client, test_device) -> str:
    response = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": test_device["token"],
            "alias": "当前机器",
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def _new_notebook(path, sources: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    notebook = nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_code_cell(source) for source in sources],
        metadata={
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
    )
    with path.open("w", encoding="utf-8") as handle:
        nbformat.write(notebook, handle)


def test_ai_notebook_state_creates_default_notebook(client, session, auth_user, test_device):
    _make_admin(session, auth_user)
    entry_id = _create_local_entry(client, test_device)

    response = client.get(f"/api/device-entries/{entry_id}/ai-notebook/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["entry_id"] == entry_id
    assert payload["device_id"] == test_device["id"]
    assert payload["binding"]["exists"] is True
    assert payload["notebook_path"].endswith("test-device-local.ipynb")
    assert payload["cells"][0]["cell_id"]

    notebook_path = get_settings().ai_notebook_workdir / "test-device-local.ipynb"
    with notebook_path.open("r", encoding="utf-8") as handle:
        notebook = nbformat.read(handle, as_version=4)
    assert notebook.cells[0]["id"] == payload["cells"][0]["cell_id"]


def test_ai_notebook_binding_rejects_paths_outside_workdir(client, session, auth_user, test_device):
    _make_admin(session, auth_user)
    entry_id = _create_local_entry(client, test_device)

    response = client.put(
        f"/api/device-entries/{entry_id}/ai-notebook/binding",
        json={"notebook_path": "../escape.ipynb"},
    )

    assert response.status_code == 403
    assert "工作目录" in response.json()["detail"]


def test_ai_notebook_update_save_marks_following_cells_stale(client, session, auth_user, test_device):
    _make_admin(session, auth_user)
    entry_id = _create_local_entry(client, test_device)
    workdir = get_settings().ai_notebook_workdir
    notebook_path = workdir / "chain.ipynb"
    _new_notebook(notebook_path, ["a = 1\n", "b = a + 1\n", "print(b)\n"])

    binding_response = client.put(
        f"/api/device-entries/{entry_id}/ai-notebook/binding",
        json={"notebook_path": "chain.ipynb"},
    )
    assert binding_response.status_code == 200
    state = binding_response.json()
    target_cell = state["cells"][1]
    following_cell = state["cells"][2]

    update_response = client.put(
        f"/api/device-entries/{entry_id}/ai-notebook/cells/{target_cell['cell_id']}",
        json={"notebook_hash": state["notebook_hash"], "source": "b = a + 2\n"},
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["dirty"] is True
    assert updated["stale_cell_ids"] == [target_cell["cell_id"], following_cell["cell_id"]]

    save_response = client.post(
        f"/api/device-entries/{entry_id}/ai-notebook/save",
        json={"notebook_hash": updated["notebook_hash"]},
    )

    assert save_response.status_code == 200
    saved = save_response.json()
    assert saved["dirty"] is False
    assert saved["stale_cell_ids"] == [target_cell["cell_id"], following_cell["cell_id"]]
    with notebook_path.open("r", encoding="utf-8") as handle:
        notebook = nbformat.read(handle, as_version=4)
    assert notebook.cells[1]["source"] == "b = a + 2\n"


def test_ai_notebook_hash_conflict_returns_409(client, session, auth_user, test_device):
    _make_admin(session, auth_user)
    entry_id = _create_local_entry(client, test_device)
    state_response = client.get(f"/api/device-entries/{entry_id}/ai-notebook/state")
    assert state_response.status_code == 200
    state = state_response.json()

    notebook_path = get_settings().ai_notebook_workdir / "test-device-local.ipynb"
    _new_notebook(notebook_path, ["external = True\n"])

    response = client.put(
        f"/api/device-entries/{entry_id}/ai-notebook/cells/{state['cells'][0]['cell_id']}",
        json={"notebook_hash": state["notebook_hash"], "source": "x = 1\n"},
    )

    assert response.status_code == 409
    assert "刷新" in response.json()["detail"]


def test_ai_notebook_run_cell_uses_kernel_runtime_and_clears_stale(
    client,
    session,
    auth_user,
    test_device,
    monkeypatch,
):
    _make_admin(session, auth_user)
    entry_id = _create_local_entry(client, test_device)
    state = client.get(f"/api/device-entries/{entry_id}/ai-notebook/state").json()
    cell_id = state["cells"][0]["cell_id"]
    updated = client.put(
        f"/api/device-entries/{entry_id}/ai-notebook/cells/{cell_id}",
        json={"notebook_hash": state["notebook_hash"], "source": "print('ok')\n"},
    ).json()
    saved = client.post(
        f"/api/device-entries/{entry_id}/ai-notebook/save",
        json={"notebook_hash": updated["notebook_hash"]},
    ).json()

    def fake_execute(self, code, *, cwd, timeout_seconds=120.0):
        return "success", [nbformat.v4.new_output("stream", name="stdout", text=f"{code.strip()} -> ok\n")]

    monkeypatch.setattr("backend.core.notebook_lab.service.NotebookKernelRuntime.execute", fake_execute)

    response = client.post(
        f"/api/device-entries/{entry_id}/ai-notebook/run-cell",
        json={"notebook_hash": saved["notebook_hash"], "cell_id": cell_id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert "-> ok" in payload["outputs_summary"][0]
    assert cell_id not in payload["state"]["stale_cell_ids"]
    assert payload["state"]["cells"][0]["outputs_summary"]


def test_ai_notebook_remote_entry_proxies_to_device_api(client, session, auth_user, monkeypatch):
    _make_admin(session, auth_user)
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
            return {
                "session_id": "device:remote-device-1",
                "entry_id": "",
                "device_id": "remote-device-1",
                "binding": {
                    "entry_id": "",
                    "device_id": "remote-device-1",
                    "notebook_path": "D:/remote/demo.ipynb",
                    "workdir": "D:/remote",
                    "exists": True,
                    "updated_at": 1.0,
                },
                "notebook_path": "D:/remote/demo.ipynb",
                "notebook_hash": "abc",
                "kernel_status": "stopped",
                "cells": [],
                "stale_cell_ids": [],
                "last_error": None,
                "dirty": False,
            }

        @property
        def content(self):
            return b"{}"

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None, stream=False):
        captured.update(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.setattr("backend.api.device_entries.requests.request", fake_request)

    response = client.get(f"/api/device-entries/{entry.entry_id}/ai-notebook/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["entry_id"] == entry.entry_id
    assert payload["binding"]["entry_id"] == entry.entry_id
    assert captured["method"] == "GET"
    assert captured["url"] == "http://remote-device:8000/api/ai-notebook/state"
    assert captured["headers"]["X-Device-Token"] == "remote-token"


def test_ai_notebook_requires_login(client, session, auth_user, test_device):
    _make_admin(session, auth_user)
    entry_id = _create_local_entry(client, test_device)
    app.dependency_overrides.pop(get_current_user_from_token, None)
    app.dependency_overrides.pop(get_optional_current_user_from_token, None)

    response = client.get(f"/api/device-entries/{entry_id}/ai-notebook/state")

    assert response.status_code == 401


def test_ai_notebook_requires_feature_access(client, auth_user, test_device):
    entry_id = _create_local_entry(client, test_device)

    response = client.get(f"/api/device-entries/{entry_id}/ai-notebook/state")

    assert response.status_code == 403
    assert response.json()["detail"] == "当前账号无权访问该功能"


def test_ai_notebook_requires_admin_after_feature_access(client, session, auth_user, test_device):
    save_feature_access_policy_overrides(
        session,
        subject_type=FEATURE_ACCESS_SUBJECT_USER,
        subject_user_id=auth_user.id,
        overrides={"tools.ai-notebook": "allow"},
        updated_by_user_id=auth_user.id,
    )
    entry_id = _create_local_entry(client, test_device)

    response = client.get(f"/api/device-entries/{entry_id}/ai-notebook/state")

    assert response.status_code == 403
    assert response.json()["detail"] == "只有管理员可以使用 AI 协作 Notebook"
