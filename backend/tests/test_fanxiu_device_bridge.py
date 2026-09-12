"""Pure ownership, lease and delivery contracts; no simulated game."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.core.fanxiu.remote.device_bridge import CommandRecord, DeviceBridge
from backend.core.fanxiu.remote.sessions import RemoteError


def connect(broker):
    return broker.connect(1, "device", "instance", {})["worker_id"]


def test_single_owner_and_account_isolation():
    broker = DeviceBridge()
    worker = connect(broker)
    assert connect(broker) == worker
    with pytest.raises(RemoteError):
        broker.connect(2, "other", "instance", {})
    with pytest.raises(RemoteError):
        broker.describe(2, worker)
    assert "capability" not in broker.describe(1, worker)


def test_lease_expiration_has_no_fallback():
    now = [100]
    broker = DeviceBridge(clock=lambda: now[0])
    worker = connect(broker)
    now[0] += 46
    with pytest.raises(RemoteError, match="租约"):
        broker.poll(1, worker, 0)


def test_delivery_once_receipt_idempotent_and_conflict_rejected():
    broker = DeviceBridge()
    worker = connect(broker)
    capability = broker.bind(1, worker)
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(broker.rpc, worker, capability, "r1", "input", {"kind": "tap"}, 3)
        command = broker.poll(1, worker, 1)["command"]
        assert command["request_id"] == "r1"
        assert broker.poll(1, worker, 0)["command"] is None
        broker.result(1, worker, "r1", "uncertain", {}, "lost")
        assert pending.result()["status"] == "uncertain"
    broker.result(1, worker, "r1", "uncertain", {}, "lost")
    assert broker.rpc(worker, capability, "r1", "input", {"kind": "tap"}, 1)["status"] == "uncertain"
    with pytest.raises(RemoteError):
        broker.result(1, worker, "r1", "ok", {})
    with pytest.raises(RemoteError):
        broker.rpc(worker, capability, "r1", "input", {"kind": "swipe"}, 1)


def test_capability_and_package_validation():
    broker = DeviceBridge()
    worker = connect(broker)
    with pytest.raises(RemoteError):
        broker.rpc(worker, "wrong", "r", "capture", {}, 1)
    with pytest.raises(RemoteError):
        broker.rpc(worker, broker.bind(1, worker), "r", "launch", {"package": "other"}, 1)


def test_large_response_consumed_but_receipt_retry_accepted():
    broker = DeviceBridge()
    worker = connect(broker)
    capability = broker.bind(1, worker)
    result = {"data_base64": "x" * 70000}
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(broker.rpc, worker, capability, "r", "capture", {}, 3)
        broker.poll(1, worker, 1)
        broker.result(1, worker, "r", "ok", result)
        assert pending.result()["result"] == result
    broker.result(1, worker, "r", "ok", result)
    assert broker.rpc(worker, capability, "r", "capture", {}, 1)["status"] == "uncertain"


def test_poll_does_not_scan_history_and_consumed_record_has_no_payload():
    class NoHistoryScan(dict):
        def values(self):
            raise AssertionError("poll must not scan command history")
        def items(self):
            raise AssertionError("poll must not scan command history")
        def __iter__(self):
            raise AssertionError("poll must not scan command history")
    broker = DeviceBridge()
    worker = connect(broker)
    history = NoHistoryScan({str(i): CommandRecord(b"x" * 32, 0, None, retired=True) for i in range(1000)})
    broker.workers[worker].commands = history
    assert broker.poll(1, worker, 0)["command"] is None
    capability = broker.bind(1, worker)
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(broker.rpc, worker, capability, "fresh", "shell_text", {"command": "test"}, 3)
        assert broker.poll(1, worker, 1)["command"]["request_id"] == "fresh"
        broker.result(1, worker, "fresh", "ok", {"stdout": "payload"})
        assert pending.result()["result"]["stdout"] == "payload"
    record = history["fresh"]
    assert record.retired and record.command is None and record.receipt is None
    assert len(record.fingerprint) == len(record.receipt_hash) == 32
    broker.result(1, worker, "fresh", "ok", {"stdout": "payload"})
    with pytest.raises(RemoteError):
        broker.rpc(worker, capability, "fresh", "shell_text", {"command": "changed"}, 1)
