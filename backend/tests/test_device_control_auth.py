import pytest
from fastapi import HTTPException

from backend.core.access import auth


class _FakeDeviceManager:
    def get_device(self, _device_id):
        return None


def test_validate_api_token_accepts_active_local_device_entry_token(monkeypatch):
    calls = []

    class _FakeLocalDevice:
        def __init__(self, device_id, name, python_exec=None, api_token=None, order_index=0):
            self.device_id = device_id
            self.name = name
            self.python_exec = python_exec
            self.api_token = api_token
            self.order_index = order_index

    def fake_lookup(final_token, local_id):
        calls.append((final_token, local_id))
        if final_token == "entry-token" and local_id == "local-device":
            return _FakeLocalDevice(local_id, "codepc_mf", api_token=final_token)
        return None

    monkeypatch.setattr("backend.core.devices.device.get_device_id", lambda: "local-device")
    monkeypatch.setattr("backend.core.devices.device.device_manager", _FakeDeviceManager())
    monkeypatch.setattr(auth, "_validate_local_device_entry_token", fake_lookup)

    device = auth.validate_api_token_value("entry-token")

    assert device.device_id == "local-device"
    assert device.api_token == "entry-token"
    assert calls == [("entry-token", "local-device")]


def test_validate_api_token_still_reports_disabled_when_no_local_token(monkeypatch):
    monkeypatch.setattr("backend.core.devices.device.get_device_id", lambda: "local-device")
    monkeypatch.setattr("backend.core.devices.device.device_manager", _FakeDeviceManager())
    monkeypatch.setattr(auth, "_validate_local_device_entry_token", lambda *_args: None)

    with pytest.raises(HTTPException) as exc_info:
        auth.validate_api_token_value("wrong-token")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Device control is disabled on this node"
