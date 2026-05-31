from backend.core.fanxiu_capture_runtime import FanxiuCaptureRuntimeService


def test_capture_runtime_tracks_multiple_reasons(monkeypatch):
    service = FanxiuCaptureRuntimeService(supervisor_interval=0.01)
    monkeypatch.setattr(service, "_ensure_supervisor_locked", lambda: None)

    service.ensure_running("game-window3")
    status = service.ensure_running("packet-capture")

    assert status["active_reasons"] == ["game-window3", "packet-capture"]

    status = service.release("game-window3")

    assert status["active_reasons"] == ["packet-capture"]
    assert status["state"] == "stopped"


def test_capture_runtime_force_stop_clears_reasons(monkeypatch):
    service = FanxiuCaptureRuntimeService(supervisor_interval=0.01)
    monkeypatch.setattr(service, "_ensure_supervisor_locked", lambda: None)

    service.ensure_running("runtime-manual")
    service.ensure_running("game-window3")
    status = service.force_stop()

    assert status["active_reasons"] == []
    assert status["running"] is False
    assert status["state"] == "stopped"
