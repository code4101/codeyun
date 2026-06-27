from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from backend.core.fanxiu.packet import service_runtime


def test_packet_service_status_reads_state_file(monkeypatch, tmp_path):
    state_path = tmp_path / "packet_service_state.json"
    log_path = tmp_path / "packet_service.log"
    state_path.write_text(
        """
{
  "updated_at": "2026-06-27 19:32:00",
  "capture_runtime": {"running": true, "state": "running"},
  "packet_worker": {"realtime_running": true, "maintenance_running": false}
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("FX_PACKET_SERVICE_STATE", str(state_path))
    monkeypatch.setenv("FX_PACKET_SERVICE_LOG", str(log_path))
    monkeypatch.setattr(service_runtime, "list_fanxiu_packet_service_processes", lambda: [{"pid": 1234}])

    status = service_runtime.get_fanxiu_packet_service_status()

    assert status["running"] is True
    assert status["process_count"] == 1
    assert status["pids"] == [1234]
    assert status["updated_at"] == "2026-06-27 19:32:00"
    assert status["capture_runtime"]["state"] == "running"
    assert status["packet_worker"]["realtime_running"] is True


def test_packet_worker_status_is_service_state_projection(monkeypatch, tmp_path):
    state_path = tmp_path / "packet_service_state.json"
    state_path.write_text(
        '{"packet_worker": {"updated_at": "2026-06-27 19:33:00", "realtime_running": true}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("FX_PACKET_SERVICE_STATE", str(state_path))
    monkeypatch.setattr(service_runtime, "list_fanxiu_packet_service_processes", lambda: [{"pid": 1234}])

    status = service_runtime.get_fanxiu_packet_worker_status()

    assert status["updated_at"] == "2026-06-27 19:33:00"
    assert status["realtime_running"] is True
    assert status["service_running"] is True
    assert status["pids"] == [1234]


def test_start_packet_service_uses_no_window_module_launcher(monkeypatch, tmp_path):
    calls: list[dict[str, object]] = []
    log_path = tmp_path / "packet_service.log"
    state_path = tmp_path / "packet_service_state.json"
    monkeypatch.setenv("FX_PACKET_SERVICE_LOG", str(log_path))
    monkeypatch.setenv("FX_PACKET_SERVICE_STATE", str(state_path))

    statuses = iter(
        [
            {"running": False, "process_count": 0, "pids": []},
            {"running": True, "process_count": 1, "pids": [4321]},
        ]
    )
    monkeypatch.setattr(service_runtime, "get_fanxiu_packet_service_status", lambda: next(statuses))

    def fake_popen_python_module_service(module, *args, **kwargs):
        calls.append({"module": module, "args": args, "kwargs": kwargs})
        return SimpleNamespace(pid=4321, poll=lambda: None)

    monkeypatch.setattr(service_runtime, "popen_python_module_service", fake_popen_python_module_service)

    result = service_runtime.start_fanxiu_packet_service(wait_seconds=0.1)

    assert result["status"] == "started"
    assert result["service"]["started_pid"] == 4321
    assert calls[0]["module"] == service_runtime.FANXIU_PACKET_SERVICE_MODULE
    kwargs = calls[0]["kwargs"]
    assert kwargs["cwd"] == str(service_runtime.ROOT_DIR)
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["env"]["FX_PACKET_SERVICE_LOG"] == str(log_path)
    assert kwargs["env"]["FX_PACKET_SERVICE_STATE"] == str(state_path)
    assert Path(kwargs["stdout"].name) == log_path
