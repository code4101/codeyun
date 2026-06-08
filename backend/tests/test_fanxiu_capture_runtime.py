from __future__ import annotations

from backend.core.fanxiu_capture_runtime import (
    DEFAULT_FANXIU_DEVICE_ID,
    FanxiuCaptureRuntimeService,
)


def test_capture_runtime_prefers_connected_bridge_device(monkeypatch):
    monkeypatch.delenv("FANXIU_CAPTURE_DEVICE_ID", raising=False)
    monkeypatch.delenv("FANXIU_ADB_DEVICE_ID", raising=False)

    service = FanxiuCaptureRuntimeService()
    calls: list[tuple[str, ...]] = []

    def fake_run_adb(args: list[str], timeout: float = 8) -> str:
        del timeout
        calls.append(tuple(args))
        if args == ["devices"]:
            return "List of devices attached\n192.168.31.181:5555\tdevice\n"
        if args == ["connect", "192.168.31.181:5555"]:
            return "already connected to 192.168.31.181:5555"
        if args == ["connect", DEFAULT_FANXIU_DEVICE_ID]:
            raise RuntimeError("stale default should not be tried first")
        raise AssertionError(f"unexpected adb args: {args}")

    monkeypatch.setattr(service, "_run_adb", fake_run_adb)

    service._connect_adb()

    assert service.device_id == "192.168.31.181:5555"
    assert ("connect", "192.168.31.181:5555") in calls
    assert ("connect", DEFAULT_FANXIU_DEVICE_ID) not in calls


def test_capture_runtime_env_device_has_priority(monkeypatch):
    monkeypatch.setenv("FANXIU_CAPTURE_DEVICE_ID", "10.0.0.8:5555")
    monkeypatch.delenv("FANXIU_ADB_DEVICE_ID", raising=False)

    service = FanxiuCaptureRuntimeService()
    calls: list[tuple[str, ...]] = []

    def fake_run_adb(args: list[str], timeout: float = 8) -> str:
        del timeout
        calls.append(tuple(args))
        if args == ["devices"]:
            return "List of devices attached\n10.0.0.8:5555\tdevice\n192.168.31.181:5555\tdevice\n"
        if args == ["connect", "10.0.0.8:5555"]:
            return "connected to 10.0.0.8:5555"
        raise AssertionError(f"unexpected adb args: {args}")

    monkeypatch.setattr(service, "_run_adb", fake_run_adb)

    service._connect_adb()

    assert service.device_id == "10.0.0.8:5555"
    assert calls[0] == ("devices",)
    assert calls[1] == ("connect", "10.0.0.8:5555")


def test_capture_runtime_idle_does_not_restart_tcpdump(monkeypatch):
    service = FanxiuCaptureRuntimeService(idle_finalize_seconds=1)
    service._current_remote_pcap_path = "/data/local/tmp/codeyun_fanxiu_runtime_test.pcap"
    service._last_remote_pcap_size = 4096
    service._last_remote_pcap_size_seen_at = 0.0

    monkeypatch.setattr(service, "_remote_capture_size", lambda remote_path: 4096)

    stop_calls = 0
    start_calls = 0

    def fake_stop_tcpdump_locked(*, queue_sync: bool = True) -> None:
        nonlocal stop_calls
        del queue_sync
        stop_calls += 1

    def fake_start_tcpdump_locked() -> None:
        nonlocal start_calls
        start_calls += 1

    monkeypatch.setattr(service, "_stop_tcpdump_locked", fake_stop_tcpdump_locked)
    monkeypatch.setattr(service, "_start_tcpdump_locked", fake_start_tcpdump_locked)

    service._finalize_idle_capture_locked()

    assert stop_calls == 0
    assert start_calls == 0
