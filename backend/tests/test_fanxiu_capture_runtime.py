from __future__ import annotations

import os
import time
from pathlib import Path

from backend.core.fanxiu.runtime.capture_runtime import (
    DEFAULT_FANXIU_DEVICE_ID,
    FANXIU_CAPTURE_RUNTIME_WATCHDOG_REASON,
    FanxiuCaptureRuntimeService,
)


def test_capture_runtime_watchdog_enabled_by_default(monkeypatch):
    from backend.app import _fanxiu_capture_runtime_service_enabled

    monkeypatch.delenv("FX_CAPTURE_RUNTIME_SERVICE_ENABLED", raising=False)
    monkeypatch.delenv("FX_RUNTIME_SERVICES", raising=False)

    assert _fanxiu_capture_runtime_service_enabled() is True


def test_capture_runtime_watchdog_can_be_disabled(monkeypatch):
    from backend.app import _fanxiu_capture_runtime_service_enabled

    monkeypatch.setenv("FX_CAPTURE_RUNTIME_SERVICE_ENABLED", "0")
    monkeypatch.delenv("FX_RUNTIME_SERVICES", raising=False)

    assert _fanxiu_capture_runtime_service_enabled() is False


def test_capture_runtime_watchdog_retries_and_recovers(monkeypatch):
    service = FanxiuCaptureRuntimeService()
    checks = iter([False, True])
    supervisor_starts = 0

    def fake_probe_game_running() -> bool:
        return next(checks)

    def fake_ensure_supervisor_locked() -> None:
        nonlocal supervisor_starts
        supervisor_starts += 1

    monkeypatch.setattr(service, "probe_game_running", fake_probe_game_running)
    monkeypatch.setattr(service, "_ensure_supervisor_locked", fake_ensure_supervisor_locked)

    first = service.watchdog_once()
    second = service.watchdog_once()

    assert first["watchdog_last_action"] == "skip_no_game"
    assert FANXIU_CAPTURE_RUNTIME_WATCHDOG_REASON not in first["active_reasons"]
    assert second["watchdog_last_action"] == "ensure_running"
    assert FANXIU_CAPTURE_RUNTIME_WATCHDOG_REASON in second["active_reasons"]
    assert supervisor_starts == 1


def test_capture_runtime_watchdog_loop_checks_before_wait(monkeypatch):
    service = FanxiuCaptureRuntimeService()
    calls = 0

    def fake_watchdog_once():
        nonlocal calls
        calls += 1
        service._watchdog_stop_event.set()
        return service.status()

    monkeypatch.setattr(service, "watchdog_once", fake_watchdog_once)

    service._watchdog_loop()

    assert calls == 1


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


def test_capture_runtime_starts_local_stream_tcpdump_by_default(monkeypatch, tmp_path):
    service = FanxiuCaptureRuntimeService()
    service._capture_stream_to_local = True
    service.device_id = "127.0.0.1:7555"
    popen_calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, args, **_kwargs):
            popen_calls.append(args)
            self.stdout = None
            self.stderr = None

        def poll(self):
            return None

    monkeypatch.setattr("backend.core.fanxiu.runtime.capture_runtime.resolve_fanxiu_tcp_live_capture_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "_adb_path", lambda: Path("adb.exe"))
    monkeypatch.setattr(service, "_cleanup_stale_codeyun_tcpdump_locked", lambda: None)
    monkeypatch.setattr(service, "_verify_local_stream_capture", lambda _path: None)
    monkeypatch.setattr("backend.core.fanxiu.runtime.capture_runtime.subprocess.Popen", FakeProcess)

    service._start_tcpdump_locked()

    assert service._capture_mode == "local-stream"
    assert service._current_remote_pcap_path == ""
    assert popen_calls
    assert popen_calls[0][3:5] == ["shell", "-T"]
    assert "tcpdump -U -i wlan0 -s 0 -w -" in popen_calls[0][-1]
    assert "2>/dev/null" in popen_calls[0][-1]


def test_capture_runtime_stream_writer_flushes_available_pipe_bytes(tmp_path):
    service = FanxiuCaptureRuntimeService()
    read_fd, write_fd = os.pipe()
    local_path = tmp_path / "stream.pcap"

    class FakeStdout:
        def fileno(self):
            return read_fd

    class FakeProcess:
        stdout = FakeStdout()

    os.write(write_fd, b"pcap-header")
    os.close(write_fd)
    try:
        service._write_tcpdump_stream_to_file(FakeProcess(), local_path)
    finally:
        os.close(read_fd)

    assert local_path.read_bytes() == b"pcap-header"


def test_capture_runtime_local_stream_stop_queues_sealed_pcap(monkeypatch, tmp_path):
    service = FanxiuCaptureRuntimeService()
    pcap = tmp_path / "fanxiu_runtime_test.pcap"
    pcap.write_bytes(b"x" * 128)
    queued: list[str] = []

    class FakeProcess:
        stdout = None
        stderr = None

        def poll(self):
            return 0

    service._capture_mode = "local-stream"
    service._current_pcap_path = str(pcap)
    service._tcpdump_process = FakeProcess()
    monkeypatch.setattr(service, "_run_adb", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(service, "_cleanup_stale_local_tcpdump_adb_locked", lambda: None)
    monkeypatch.setattr(service, "_start_runtime_packet_sync_thread", lambda path: queued.append(path))

    service._stop_tcpdump_locked(queue_sync=True)

    assert service._current_pcap_size == 128
    assert queued == [str(pcap)]
    assert service._capture_mode == ""


def test_capture_runtime_packet_sync_is_serialized(monkeypatch):
    service = FanxiuCaptureRuntimeService()
    started: list[str] = []

    class AliveThread:
        def is_alive(self):
            return True

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            del target, name, daemon
            self.args = args

        def start(self):
            started.append(self.args[0])

        def is_alive(self):
            return False

    service._packet_sync_thread = AliveThread()
    service._packet_sync_active_path = "busy.pcap"
    service._start_runtime_packet_sync_thread("next.pcap")

    assert started == []
    assert service._packet_sync_skipped_count == 1
    assert any("runtime packet sync skipped" in line for line in service.log_lines())

    service._packet_sync_thread = None
    monkeypatch.setattr("backend.core.fanxiu.runtime.capture_runtime.threading.Thread", FakeThread)
    service._start_runtime_packet_sync_thread("next.pcap")

    assert started == ["next.pcap"]
    assert service._packet_sync_active_path == "next.pcap"


def test_capture_runtime_packet_sync_clears_active_path(monkeypatch):
    service = FanxiuCaptureRuntimeService()
    service._packet_sync_active_path = "done.pcap"
    monkeypatch.setattr(
        "backend.core.fanxiu.packet.insight_worker.sync_fanxiu_capture_paths",
        lambda *_args, **_kwargs: {"decoded": [], "decoded_count": 0, "skipped_count": 0, "error_count": 0},
    )

    service._decode_and_sync_runtime_packets("done.pcap")

    assert service._packet_sync_active_path == ""


def test_capture_runtime_idle_seals_and_restarts_tcpdump(monkeypatch):
    service = FanxiuCaptureRuntimeService(idle_finalize_seconds=1)
    service._active_reasons.add("test")
    service._current_remote_pcap_path = "/data/local/tmp/codeyun_fanxiu_runtime_test.pcap"
    service._current_pcap_path = "fanxiu_runtime_test.pcap"
    service._last_remote_pcap_size = 4096
    service._last_remote_pcap_size_seen_at = time.monotonic() - 2
    service._last_snapshot_remote_pcap_size = 4096

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

    assert stop_calls == 1
    assert start_calls == 1


def test_capture_runtime_idle_waits_until_size_is_stable(monkeypatch):
    service = FanxiuCaptureRuntimeService(idle_finalize_seconds=60)
    service._active_reasons.add("test")
    service._current_remote_pcap_path = "/data/local/tmp/codeyun_fanxiu_runtime_test.pcap"
    service._last_remote_pcap_size = 4096
    service._last_remote_pcap_size_seen_at = 0.0
    service._last_snapshot_remote_pcap_size = 4096

    monkeypatch.setattr(service, "_remote_capture_size", lambda remote_path: 8192)

    stop_calls = 0

    def fake_stop_tcpdump_locked(*, queue_sync: bool = True) -> None:
        nonlocal stop_calls
        del queue_sync
        stop_calls += 1

    monkeypatch.setattr(service, "_stop_tcpdump_locked", fake_stop_tcpdump_locked)

    service._finalize_idle_capture_locked()

    assert stop_calls == 0
    assert service._last_remote_pcap_size == 8192


def test_capture_runtime_seals_when_segment_exceeds_max_age(monkeypatch):
    service = FanxiuCaptureRuntimeService(idle_finalize_seconds=60, max_segment_seconds=10)
    service._active_reasons.add("test")
    service._current_remote_pcap_path = "/data/local/tmp/codeyun_fanxiu_runtime_test.pcap"
    service._current_pcap_path = "fanxiu_runtime_test.pcap"
    service._last_remote_pcap_size = 4096
    service._last_remote_pcap_size_seen_at = time.monotonic()
    service._last_snapshot_remote_pcap_size = 4096
    service._tcpdump_started_monotonic = time.monotonic() - 11

    monkeypatch.setattr(service, "_remote_capture_size", lambda remote_path: 8192)

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

    assert stop_calls == 1
    assert start_calls == 1


def test_capture_runtime_snapshots_running_pcap_without_stopping_tcpdump(monkeypatch, tmp_path):
    service = FanxiuCaptureRuntimeService(idle_finalize_seconds=1)
    service.device_id = "127.0.0.1:7555"
    service._current_remote_pcap_path = "/data/local/tmp/codeyun_fanxiu_runtime_test.pcap"
    queued: list[str] = []
    adb_calls: list[tuple[str, ...]] = []

    monkeypatch.setattr("backend.core.fanxiu.runtime.capture_runtime.resolve_fanxiu_tcp_live_capture_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "_remote_capture_size", lambda remote_path: 4096)
    monkeypatch.setattr(service, "_start_runtime_packet_sync_thread", lambda local_path: queued.append(local_path))

    def fake_run_adb(args: list[str], timeout: float = 8) -> str:
        del timeout
        adb_calls.append(tuple(args))
        if len(args) >= 5 and args[2] == "pull":
            Path(args[4]).write_bytes(b"x" * 4096)
        return ""

    monkeypatch.setattr(service, "_run_adb", fake_run_adb)

    service._finalize_idle_capture_locked()

    assert queued
    assert Path(queued[0]).is_file()
    assert "snapshot" in Path(queued[0]).name
    assert service._last_snapshot_remote_pcap_size == 4096
    assert any(call[2:4] == ("shell", "cp") for call in adb_calls)
    assert not any("pkill" in call for call in adb_calls)
