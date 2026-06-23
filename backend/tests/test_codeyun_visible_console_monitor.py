from scripts import codeyun_visible_console_monitor as monitor
from scripts.codeyun_visible_console_monitor import _new_visible_window_events


def test_visible_window_reappearance_is_recorded_as_new_event() -> None:
    active_keys: set[tuple[int, int, str]] = set()
    window = {"hwnd": 100, "pid": 200, "title": "Terminal"}

    assert _new_visible_window_events([window], active_keys=active_keys) == [window]
    assert _new_visible_window_events([window], active_keys=active_keys) == []
    assert _new_visible_window_events([], active_keys=active_keys) == []
    assert _new_visible_window_events([window], active_keys=active_keys) == [window]


def test_read_status_missing_file_reports_paths_and_not_alive(tmp_path, monkeypatch) -> None:
    status_path = tmp_path / "missing_status.json"
    events_path = tmp_path / "events.jsonl"

    monkeypatch.setattr(monitor, "STATUS_PATH", status_path)
    monkeypatch.setattr(monitor, "EVENTS_PATH", events_path)

    status = monitor.read_status()

    assert status["alive"] is False
    assert status["status_path"] == str(status_path)
    assert status["events_path"] == str(events_path)


def test_read_status_malformed_file_reports_paths_and_not_alive(tmp_path, monkeypatch) -> None:
    status_path = tmp_path / "status.json"
    events_path = tmp_path / "events.jsonl"
    status_path.write_text("[not-a-status-object]", encoding="utf-8")

    monkeypatch.setattr(monitor, "STATUS_PATH", status_path)
    monkeypatch.setattr(monitor, "EVENTS_PATH", events_path)

    status = monitor.read_status()

    assert status["alive"] is False
    assert status["status_path"] == str(status_path)
    assert status["events_path"] == str(events_path)


def test_read_status_uses_pid_liveness_probe_without_real_process_state(tmp_path, monkeypatch) -> None:
    status_path = tmp_path / "status.json"
    events_path = tmp_path / "events.jsonl"
    status_path.write_text('{"pid": 123, "started_at": "2026-06-23 16:00:00"}', encoding="utf-8")

    monkeypatch.setattr(monitor, "STATUS_PATH", status_path)
    monkeypatch.setattr(monitor, "EVENTS_PATH", events_path)
    monkeypatch.setattr(monitor, "_pid_alive", lambda pid: pid == 123)

    status = monitor.read_status()

    assert status["alive"] is True
    assert status["started_at"] == "2026-06-23 16:00:00"
    assert status["status_path"] == str(status_path)
    assert status["events_path"] == str(events_path)
