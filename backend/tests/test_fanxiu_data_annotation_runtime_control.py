import json
import time

from backend.core.fanxiu.data_annotation import runtime_control


def test_read_doctor_watch_latest_prefers_heartbeat_latest_path_when_stale(monkeypatch, tmp_path):
    watch_dir = tmp_path / "fanxiu-watch"
    watch_dir.mkdir()
    stable_path = watch_dir / "doctor_watch_latest.json"
    latest_path = watch_dir / "doctor_watch_20260703_113441.latest.json"
    heartbeat_path = watch_dir / "doctor_watch_heartbeat.json"

    stable_path.write_text('{"summary":"stable"}', encoding="utf-8")
    latest_path.write_text('{"summary":"latest"}', encoding="utf-8")
    heartbeat_path.write_text(
        json.dumps({
            "updated_at": time.time() - 3600,
            "latest_path": latest_path.as_posix(),
            "stable_latest_path": stable_path.as_posix(),
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(runtime_control, "doctor_watch_latest_path", lambda: stable_path)
    monkeypatch.setattr(runtime_control, "doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        runtime_control,
        "_doctor_watch_latest_candidates",
        lambda: (_ for _ in ()).throw(AssertionError("should not scan fallback candidates")),
    )

    payload = runtime_control.read_doctor_watch_latest()

    assert payload["exists"] is True
    assert payload["path"] == str(latest_path)
    assert payload["snapshot"]["summary"] == "latest"
    assert payload["heartbeat"]["active"] is False
