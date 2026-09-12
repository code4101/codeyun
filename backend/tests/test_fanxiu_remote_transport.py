"""Device ownership and failure isolation, independent of game behavior."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.core.fanxiu.client import remote_transport as transport
from backend.core.fanxiu.remote import device_bridge


class RecordingTransport:
    worker_id = "worker-a"

    def __init__(self):
        self.calls = []

    def call(self, operation, args, **kwargs):
        self.calls.append(operation)
        if operation == "health":
            return {"status": "healthy"}
        raise TimeoutError("lost receipt")


def test_scope_rejects_nested_owners_background_io_and_local_fallback(monkeypatch):
    recorded = RecordingTransport()
    monkeypatch.setattr(device_bridge, "get_kernel_transport", lambda worker: recorded)
    with transport.use_remote_device("worker-a"):
        assert transport.remote_device_scope_id() == "worker-a"
        with pytest.raises(RuntimeError, match="禁止回落"):
            transport.reject_local_device_access("adb")
        with pytest.raises(RuntimeError, match="占用"):
            with transport.use_remote_device("worker-b"):
                pass
        with ThreadPoolExecutor() as pool:
            call = pool.submit(transport.call_remote_device, "capture")
            with pytest.raises(RuntimeError, match="后台线程"):
                call.result()
        with pytest.raises(TimeoutError, match="lost receipt"):
            transport.call_remote_device("input")
        assert recorded.calls == ["health", "input"]
        assert transport.remote_device_active()
    assert not transport.remote_device_active()
    assert transport.remote_device_scope_id() == ""


def test_setup_failure_releases_ownership(monkeypatch):
    def unavailable(worker):
        raise ConnectionError("broker offline")
    monkeypatch.setattr(device_bridge, "get_kernel_transport", unavailable)
    for _ in range(2):
        with pytest.raises(ConnectionError):
            with transport.use_remote_device("worker-a"):
                pass
        assert not transport.remote_device_active()


def test_binary_receipt_limit_and_metadata():
    assert transport.remote_bytes({"data_base64": "YWJj", "metadata": {"adb_size": [900, 1600]}}) == (
        b"abc", {"adb_size": "Physical size: 900x1600", "input": "remote-client"})
    with pytest.raises(RuntimeError, match="超限"):
        transport.remote_bytes({"data_base64": "YWJj"}, max_bytes=2)


def test_runtime_permission_is_lazy_and_scoped_to_attempt(monkeypatch):
    class RuntimeTransport(RecordingTransport):
        def call(self, operation, args, **kwargs):
            if operation == "root":
                self.calls.append(operation)
                return {"rooted": True}
            return super().call(operation, args, **kwargs)
    recorded = RuntimeTransport()
    monkeypatch.setattr(device_bridge, "get_kernel_transport", lambda worker: recorded)
    for _ in range(2):
        with transport.use_remote_device("worker-a"):
            assert recorded.calls[-1] == "health"
            transport.ensure_remote_runtime_ready()
            transport.ensure_remote_runtime_ready()
    assert recorded.calls == ["health", "root", "health", "root"]
