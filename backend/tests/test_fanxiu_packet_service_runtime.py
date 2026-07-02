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


def test_packet_service_health_flags_stale_maintenance_substate(monkeypatch):
    monkeypatch.setattr(
        service_runtime,
        "_latest_live_capture_summary",
        lambda _capture: {"age_seconds": 5, "path": "capture.pcap", "size": 128, "mtime_text": "2026-06-28 21:00:00"},
    )
    monkeypatch.setattr(
        service_runtime,
        "_mail_database_freshness",
        lambda: {"ok": True, "exists": True, "record_count": 0, "latest_seen_age_seconds": 0},
    )
    monkeypatch.setattr(service_runtime, "_age_seconds", lambda value: {"2026-06-28 21:00:00": 30, "2026-06-28 16:28:17": 4 * 3600}.get(value, 0))

    health = service_runtime.build_fanxiu_packet_service_health(
        {
            "running": True,
            "capture_runtime": {"game_running": True, "tcpdump_ready": True, "watchdog_last_check_at": "2026-06-28 21:00:00"},
            "packet_worker": {
                "ok": True,
                "updated_at": "2026-06-28 21:00:00",
                "realtime_running": True,
                "maintenance_running": True,
                "realtime_interval_seconds": 15,
                "maintenance_interval_seconds": 1800,
                "realtime": {"updated_at": "2026-06-28 21:00:00"},
                "maintenance": {"updated_at": "2026-06-28 16:28:17"},
            },
        }
    )

    assert "maintenance_result_stale" in health["issues"]
    assert health["worker_maintenance_age_seconds"] == 4 * 3600


def test_packet_service_health_uses_active_heartbeat_for_maintenance(monkeypatch):
    monkeypatch.setattr(
        service_runtime,
        "_latest_live_capture_summary",
        lambda _capture: {"age_seconds": 5, "path": "capture.pcap", "size": 128, "mtime_text": "2026-06-28 21:00:00"},
    )
    monkeypatch.setattr(
        service_runtime,
        "_mail_database_freshness",
        lambda: {"ok": True, "exists": True, "record_count": 0, "latest_seen_age_seconds": 0},
    )
    monkeypatch.setattr(
        service_runtime,
        "_age_seconds",
        lambda value: {
            "2026-06-28 21:00:00": 30,
            "2026-06-28 16:28:17": 4 * 3600,
            "2026-06-28 20:59:50": 10,
        }.get(value, 0),
    )

    health = service_runtime.build_fanxiu_packet_service_health(
        {
            "running": True,
            "capture_runtime": {"game_running": True, "tcpdump_ready": True, "watchdog_last_check_at": "2026-06-28 21:00:00"},
            "packet_worker": {
                "ok": True,
                "updated_at": "2026-06-28 21:00:00",
                "realtime_running": True,
                "maintenance_running": True,
                "realtime_interval_seconds": 15,
                "maintenance_interval_seconds": 1800,
                "realtime": {"updated_at": "2026-06-28 21:00:00"},
                "maintenance": {
                    "updated_at": "2026-06-28 16:28:17",
                    "active": True,
                    "heartbeat_at": "2026-06-28 20:59:50",
                },
            },
        }
    )

    assert "maintenance_result_stale" not in health["issues"]
    assert health["worker_maintenance_age_seconds"] == 10


def test_latest_live_capture_summary_ignores_vanished_candidates(monkeypatch, tmp_path):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    vanished = live_dir / "vanished.pcap"
    stable = live_dir / "stable.pcap"
    stable.write_bytes(b"new")
    original_glob = Path.glob
    original_is_file = Path.is_file
    original_stat = Path.stat

    monkeypatch.setattr(service_runtime, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))

    def glob_with_vanished_file(self, pattern):
        if self == live_dir and pattern == "*.pcap":
            return iter([vanished, stable])
        return original_glob(self, pattern)

    def is_file_with_vanished_file(self):
        if self == vanished:
            return True
        return original_is_file(self)

    def stat_with_vanished_file(self, *args, **kwargs):
        if self == vanished:
            raise FileNotFoundError(str(self))
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", glob_with_vanished_file)
    monkeypatch.setattr(Path, "is_file", is_file_with_vanished_file)
    monkeypatch.setattr(Path, "stat", stat_with_vanished_file)

    summary = service_runtime._latest_live_capture_summary({})

    assert summary["path"] == str(stable)
    assert summary["name"] == "stable.pcap"
    assert summary["source"] == "latest_live_dir"


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


def test_write_json_retries_replace_on_windows_permission_error(monkeypatch, tmp_path):
    path = tmp_path / "packet_service_state.json"
    calls = {"count": 0}
    original_replace = Path.replace

    def flaky_replace(self, target):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError(13, "denied", str(target))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    service_runtime._write_json(path, {"ok": True, "value": 1})

    assert calls["count"] >= 2
    assert '"value": 1' in path.read_text(encoding="utf-8")
