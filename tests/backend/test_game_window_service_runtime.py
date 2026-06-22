from backend.core.runtime import game_window_service as game_window_runtime


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")

    def json(self):
        return self._payload


def test_game_window_service_status_rebinds_existing_http_daemon(monkeypatch):
    monkeypatch.setattr(
        game_window_runtime,
        "list_game_window_service_processes",
        lambda: [{"pid": 7788, "name": "python.exe", "cmdline": "python -m backend.services.game_window_daemon"}],
    )

    def fake_get(url, *, timeout):
        assert url == "http://127.0.0.1:8766/api/services/game-window/status"
        return _FakeResponse({
            "ok": True,
            "service": {
                "key": "fanxiu-game-window",
                "title": "凡修画面流",
                "running": True,
                "state": "running",
            },
        })

    monkeypatch.setattr(game_window_runtime.requests, "get", fake_get)

    status = game_window_runtime.get_game_window_service_status()

    assert status["running"] is True
    assert status["pids"] == [7788]
    assert status["url"] == "http://127.0.0.1:8766"


def test_game_window_service_status_skips_http_probe_when_process_and_port_are_both_absent(monkeypatch):
    monkeypatch.setattr(game_window_runtime, "list_game_window_service_processes", lambda: [])
    monkeypatch.setattr(game_window_runtime, "_is_tcp_port_open", lambda host, port, timeout=0.1: False)

    def fail_get(*args, **kwargs):
        raise AssertionError("requests.get should not run when game window daemon is clearly offline")

    monkeypatch.setattr(game_window_runtime.requests, "get", fail_get)

    status = game_window_runtime.get_game_window_service_status()

    assert status["running"] is False
    assert status["state"] == "stopped"
    assert status["process_count"] == 0
