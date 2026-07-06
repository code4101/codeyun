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


def test_ensure_doctor_watch_background_uses_repo_root_script(monkeypatch, tmp_path):
    calls = []

    class TempRoot:
        def __call__(self, *parts):
            return tmp_path.joinpath(*parts)

    class FakeProcess:
        pid = 12345

    def fake_popen(script_path, *args, **kwargs):
        calls.append({"script_path": script_path, "args": args, "kwargs": kwargs})
        return FakeProcess()

    monkeypatch.setattr(runtime_control, "codeyun_temp_root", TempRoot())
    monkeypatch.setattr(runtime_control, "read_doctor_watch_heartbeat", lambda **_kwargs: {"active": False})
    monkeypatch.setattr(runtime_control, "read_doctor_watch_latest", lambda: {})
    monkeypatch.setattr(runtime_control, "popen_python_script_service", fake_popen)

    result = runtime_control.ensure_doctor_watch_background(interval_seconds=30, include_screenshot=False)

    assert result["started"] is True
    assert calls
    script_path = calls[0]["script_path"]
    assert script_path.name == "fanxiu_bt.py"
    assert script_path.parent.name == "scripts"
    assert script_path.is_file()
    assert "backend/core/scripts" not in script_path.as_posix()
    assert calls[0]["kwargs"]["cwd"] == str(script_path.parents[1])
