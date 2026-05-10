from __future__ import annotations

import pytest

from backend.core.trusted_python_runs import get_trusted_python_run


def test_device_control_python_run_script_returns_result(client, test_device):
    response = client.post(
        "/api/device-control/python-runs",
        headers={"X-Device-Token": test_device["token"]},
        json={
            "mode": "script",
            "script": "print('hello from trusted run')\nresult = {'answer': 42}",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["ok"] is True
    assert payload["result"] == {"answer": 42}
    assert "hello from trusted run" in payload["stdout"]


def test_device_control_python_run_module_call(client, test_device):
    response = client.post(
        "/api/device-control/python-runs",
        headers={"X-Device-Token": test_device["token"]},
        json={
            "mode": "module_call",
            "module": "math",
            "callable": "sqrt",
            "args": [81],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["result"] == 9.0


def test_trusted_python_run_rejects_non_uuid_run_id():
    with pytest.raises(FileNotFoundError):
        get_trusted_python_run("..")
