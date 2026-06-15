import time

from backend.core.fanxiu.runtime.capture_runtime import FANXIU_CAPTURE_RUNTIME_WATCHDOG_REASON, FanxiuCaptureRuntimeService


def test_capture_runtime_tracks_multiple_reasons(monkeypatch):
    service = FanxiuCaptureRuntimeService(supervisor_interval=0.01)
    monkeypatch.setattr(service, "_ensure_supervisor_locked", lambda: None)

    service.ensure_running("data-annotation")
    status = service.ensure_running("packet-capture")

    assert status["active_reasons"] == ["data-annotation", "packet-capture"]

    status = service.release("data-annotation")

    assert status["active_reasons"] == ["packet-capture"]
    assert status["state"] == "stopped"


def test_capture_runtime_force_stop_clears_reasons(monkeypatch):
    service = FanxiuCaptureRuntimeService(supervisor_interval=0.01)
    monkeypatch.setattr(service, "_ensure_supervisor_locked", lambda: None)

    service.ensure_running("runtime-manual")
    service.ensure_running("data-annotation")
    status = service.force_stop()

    assert status["active_reasons"] == []
    assert status["running"] is False
    assert status["state"] == "stopped"


def test_capture_runtime_finalizes_idle_nonempty_capture(monkeypatch):
    service = FanxiuCaptureRuntimeService(supervisor_interval=0.01, idle_finalize_seconds=1)
    events: list[str] = []

    monkeypatch.setattr(service, "_remote_capture_size", lambda _remote_path: 2048)
    monkeypatch.setattr(service, "_stop_tcpdump_locked", lambda **_kwargs: events.append("stop"))
    monkeypatch.setattr(service, "_start_tcpdump_locked", lambda: events.append("start"))

    with service._lock:
        service._current_remote_pcap_path = "/data/local/tmp/current.pcap"
        service._last_remote_pcap_size = 2048
        service._last_remote_pcap_size_seen_at = time.monotonic() - 2
        service._active_reasons.add("packet-capture")
        service._finalize_idle_capture_locked()

    assert events == ["stop", "start"]


def test_capture_runtime_keeps_growing_capture_open(monkeypatch):
    service = FanxiuCaptureRuntimeService(supervisor_interval=0.01, idle_finalize_seconds=1)
    events: list[str] = []

    monkeypatch.setattr(service, "_remote_capture_size", lambda _remote_path: 4096)
    monkeypatch.setattr(service, "_stop_tcpdump_locked", lambda **_kwargs: events.append("stop"))
    monkeypatch.setattr(service, "_start_tcpdump_locked", lambda: events.append("start"))

    with service._lock:
        service._current_remote_pcap_path = "/data/local/tmp/current.pcap"
        service._last_remote_pcap_size = 2048
        service._last_remote_pcap_size_seen_at = time.monotonic() - 2
        service._finalize_idle_capture_locked()

    assert events == []
    assert service._last_remote_pcap_size == 4096


def test_capture_runtime_watchdog_ensures_running_when_game_is_running(monkeypatch):
    service = FanxiuCaptureRuntimeService(supervisor_interval=0.01)
    monkeypatch.setattr(service, "_ensure_supervisor_locked", lambda: None)
    monkeypatch.setattr(service, "probe_game_running", lambda: True)

    status = service.watchdog_once()

    assert status["active_reasons"] == [FANXIU_CAPTURE_RUNTIME_WATCHDOG_REASON]
    assert status["watchdog_last_action"] == "ensure_running"


def test_capture_runtime_watchdog_skips_when_game_is_not_running(monkeypatch):
    service = FanxiuCaptureRuntimeService(supervisor_interval=0.01)
    monkeypatch.setattr(service, "_ensure_supervisor_locked", lambda: None)
    monkeypatch.setattr(service, "probe_game_running", lambda: False)

    status = service.watchdog_once()

    assert status["active_reasons"] == []
    assert status["running"] is False
    assert status["watchdog_last_action"] == "skip_no_game"


def test_capture_runtime_watchdog_releases_auto_reason_when_game_stops(monkeypatch):
    service = FanxiuCaptureRuntimeService(supervisor_interval=0.01)
    monkeypatch.setattr(service, "_ensure_supervisor_locked", lambda: None)
    monkeypatch.setattr(service, "_tcpdump_process_alive_locked", lambda: False)
    service.ensure_running(FANXIU_CAPTURE_RUNTIME_WATCHDOG_REASON)
    service.ensure_running("packet-page")
    monkeypatch.setattr(service, "probe_game_running", lambda: False)

    status = service.watchdog_once()

    assert status["active_reasons"] == ["packet-page"]
    assert status["watchdog_last_action"] == "skip_no_game"


def test_capture_runtime_flush_recent_seals_without_queueing_sync(tmp_path, monkeypatch):
    service = FanxiuCaptureRuntimeService(supervisor_interval=0.01)
    monkeypatch.setattr(service, "_ensure_supervisor_locked", lambda: None)
    service.ensure_running("mail-claim-check")
    pcap = tmp_path / "recent.pcap"
    pcap.write_bytes(b"pcap" * 16)
    events: list[tuple[str, bool | None]] = []

    monkeypatch.setattr(service, "_tcpdump_process_alive_locked", lambda: True)
    monkeypatch.setattr(service, "_stop_tcpdump_locked", lambda *, queue_sync=True: events.append(("stop", queue_sync)))
    monkeypatch.setattr(service, "_start_tcpdump_locked", lambda: events.append(("start", None)))
    with service._lock:
        service._current_pcap_path = str(pcap)

    result = service.flush_recent_capture("mail-test")

    assert result["flushed"] is True
    assert result["pcap_path"] == str(pcap)
    assert events == [("stop", False), ("start", None)]
