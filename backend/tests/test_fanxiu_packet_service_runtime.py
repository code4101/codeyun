from __future__ import annotations

import os
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


def test_packet_service_status_can_skip_health(monkeypatch, tmp_path):
    state_path = tmp_path / "packet_service_state.json"
    state_path.write_text('{"updated_at": "2026-06-27 19:32:00"}', encoding="utf-8")
    monkeypatch.setenv("FX_PACKET_SERVICE_STATE", str(state_path))
    monkeypatch.setenv("FX_PACKET_SERVICE_LOG", str(tmp_path / "packet_service.log"))
    monkeypatch.setattr(service_runtime, "list_fanxiu_packet_service_processes", lambda: [{"pid": 1234}])
    monkeypatch.setattr(
        service_runtime,
        "build_fanxiu_packet_service_health",
        lambda _status: (_ for _ in ()).throw(AssertionError("health should be skipped")),
    )

    status = service_runtime.get_fanxiu_packet_service_status(include_health=False)

    assert "health" not in status


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


def test_request_fanxiu_packet_service_maintenance_submits_maintenance_action(monkeypatch):
    captured: dict[str, object] = {}

    def fake_submit(action: str, *, reason: str = "api", wait_seconds: float = 30.0):
        captured.update(action=action, reason=reason, wait_seconds=wait_seconds)
        return {"ok": True, "action": action}

    monkeypatch.setattr(service_runtime, "submit_fanxiu_packet_service_command", fake_submit)

    result = service_runtime.request_fanxiu_packet_service_maintenance(reason="test", wait_seconds=12)

    assert result["action"] == "maintenance"
    assert captured == {"action": "maintenance", "reason": "test", "wait_seconds": 12}


def test_request_fanxiu_packet_service_capture_ready_submits_readiness_action(monkeypatch):
    captured: dict[str, object] = {}

    def fake_submit(action: str, *, reason: str = "api", wait_seconds: float = 30.0):
        captured.update(action=action, reason=reason, wait_seconds=wait_seconds)
        return {"ok": True, "action": action}

    monkeypatch.setattr(service_runtime, "submit_fanxiu_packet_service_command", fake_submit)

    result = service_runtime.request_fanxiu_packet_service_capture_ready(reason="before-entry", wait_seconds=12)

    assert result["action"] == "ensure_capture_ready"
    assert captured == {"action": "ensure_capture_ready", "reason": "before-entry", "wait_seconds": 12}


def test_packet_service_action_timeout_uses_longer_budget_for_maintenance(monkeypatch):
    monkeypatch.delenv("FX_PACKET_SERVICE_COMMAND_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("FX_PACKET_SERVICE_MAINTENANCE_TIMEOUT_SECONDS", raising=False)

    assert service_runtime._packet_service_action_timeout_seconds("maintenance") == 180.0
    assert service_runtime._packet_service_action_timeout_seconds("packet_facts_catch_up") == 120.0
    assert service_runtime._packet_service_action_timeout_seconds("ensure_capture_ready") == 150.0


def test_packet_service_action_timeout_respects_global_floor_for_maintenance(monkeypatch):
    monkeypatch.setenv("FX_PACKET_SERVICE_COMMAND_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("FX_PACKET_SERVICE_MAINTENANCE_TIMEOUT_SECONDS", "180")

    assert service_runtime._packet_service_action_timeout_seconds("maintenance") == 240.0


def test_process_packet_service_command_runs_maintenance(monkeypatch, tmp_path):
    command_path = tmp_path / "maintenance.json"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    command_path.write_text('{"command_id":"maintenance-test","action":"maintenance","reason":"test"}', encoding="utf-8")
    monkeypatch.setattr(service_runtime, "_packet_service_result_path", lambda _command_id: result_dir / "maintenance-test.json")
    monkeypatch.setattr(
        service_runtime.fanxiu_packet_insight_worker,
        "maintenance_once",
        lambda: {"ok": True, "updated_at": "2026-07-10 17:40:00"},
    )

    payload = service_runtime._process_packet_service_command(command_path)

    assert payload["ok"] is True
    assert payload["action"] == "maintenance"
    assert payload["result"]["updated_at"] == "2026-07-10 17:40:00"
    assert not command_path.exists()
    assert (result_dir / "maintenance-test.json").exists()


def test_packet_service_health_flags_stale_maintenance_substate(monkeypatch):
    monkeypatch.setattr(
        service_runtime,
        "_latest_live_capture_summary",
        lambda _capture, _worker=None: {"age_seconds": 5, "path": "capture.pcap", "size": 128, "mtime_text": "2026-06-28 21:00:00"},
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


def test_packet_service_health_uses_capture_segment_duration_for_live_pcap_staleness(monkeypatch):
    monkeypatch.setattr(
        service_runtime,
        "_latest_live_capture_summary",
        lambda _capture, _worker=None: {"age_seconds": 240, "path": "capture.pcap", "size": 128},
    )
    monkeypatch.setattr(
        service_runtime,
        "_mail_database_freshness",
        lambda: {"ok": True, "exists": True, "record_count": 0, "latest_seen_age_seconds": 0},
    )

    health = service_runtime.build_fanxiu_packet_service_health(
        {
            "running": True,
            "capture_runtime": {
                "game_running": True,
                "tcpdump_ready": True,
                "max_segment_seconds": 300,
                "watchdog_interval_seconds": 60,
            },
        }
    )

    assert health["live_pcap_stale_after_seconds"] == 360
    assert "live_pcap_stale" not in health["issues"]


def test_packet_service_health_uses_active_heartbeat_for_maintenance(monkeypatch):
    monkeypatch.setattr(
        service_runtime,
        "_latest_live_capture_summary",
        lambda _capture, _worker=None: {"age_seconds": 5, "path": "capture.pcap", "size": 128, "mtime_text": "2026-06-28 21:00:00"},
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


def test_packet_service_health_flags_active_substate_without_real_heartbeat(monkeypatch):
    monkeypatch.setattr(
        service_runtime,
        "_latest_live_capture_summary",
        lambda _capture, _worker=None: {"age_seconds": 5, "path": "capture.pcap", "size": 128, "mtime_text": "2026-06-28 21:00:00"},
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
        }.get(value),
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
                "realtime": {
                    "updated_at": "2026-06-28 16:28:17",
                    "active": True,
                },
                "maintenance": {
                    "updated_at": "2026-06-28 16:28:17",
                    "active": True,
                },
            },
        }
    )

    assert "realtime_result_stale" in health["issues"]
    assert "maintenance_result_stale" in health["issues"]
    assert health["worker_realtime_age_seconds"] == 4 * 3600


def test_packet_service_health_flags_lagging_realtime_cursor_even_with_active_heartbeat(monkeypatch, tmp_path):
    cursor = tmp_path / "fanxiu_runtime_cursor.pcap"
    latest = tmp_path / "fanxiu_runtime_latest.pcap"
    cursor.write_bytes(b"older")
    latest.write_bytes(b"newer")
    cursor_mtime = cursor.stat().st_mtime
    latest_mtime = cursor_mtime + 400
    os.utime(latest, (latest_mtime, latest_mtime))

    monkeypatch.setattr(
        service_runtime,
        "_latest_live_capture_summary",
        lambda _capture, _worker=None: {
            "age_seconds": 5,
            "path": str(latest),
            "size": 128,
            "mtime": latest_mtime,
            "mtime_text": "2026-06-28 21:00:00",
        },
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
                "has_unconfirmed_gap": True,
                "confirmed_cursor_pcap": str(cursor),
                "realtime": {
                    "updated_at": "2026-06-28 16:28:17",
                    "confirmed_cursor_pcap": str(cursor),
                    "active": True,
                    "heartbeat_at": "2026-06-28 20:59:50",
                },
                "maintenance": {
                    "updated_at": "2026-06-28 16:28:17",
                    "active": True,
                    "heartbeat_at": "2026-06-28 20:59:50",
                },
            },
        }
    )

    assert "realtime_result_stale" not in health["issues"]
    assert "realtime_cursor_lagging" in health["issues"]
    assert health["realtime_cursor_lag_seconds"] == 400


def test_packet_service_health_allows_short_realtime_catchup_window(monkeypatch, tmp_path):
    cursor = tmp_path / "fanxiu_runtime_cursor.pcap"
    latest = tmp_path / "fanxiu_runtime_latest.pcap"
    cursor.write_bytes(b"older")
    latest.write_bytes(b"newer")
    cursor_mtime = cursor.stat().st_mtime
    latest_mtime = cursor_mtime + 210
    os.utime(latest, (latest_mtime, latest_mtime))

    monkeypatch.setattr(
        service_runtime,
        "_latest_live_capture_summary",
        lambda _capture, _worker=None: {
            "age_seconds": 5,
            "path": str(latest),
            "size": 128,
            "mtime": latest_mtime,
            "mtime_text": "2026-06-28 21:00:00",
        },
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
                "has_unconfirmed_gap": True,
                "confirmed_cursor_pcap": str(cursor),
                "realtime": {
                    "updated_at": "2026-06-28 16:28:17",
                    "confirmed_cursor_pcap": str(cursor),
                    "active": True,
                    "heartbeat_at": "2026-06-28 20:59:50",
                },
                "maintenance": {
                    "updated_at": "2026-06-28 16:28:17",
                    "active": True,
                    "heartbeat_at": "2026-06-28 20:59:50",
                },
            },
        }
    )

    assert "realtime_result_stale" not in health["issues"]
    assert "realtime_cursor_lagging" not in health["issues"]
    assert health["realtime_cursor_lag_seconds"] == 210


def test_packet_service_health_suppresses_mail_stale_warning_without_recent_mail_protocol(monkeypatch):
    monkeypatch.setattr(
        service_runtime,
        "_latest_live_capture_summary",
        lambda _capture, _worker=None: {"age_seconds": 5, "path": "capture.pcap", "size": 128, "mtime_text": "2026-06-28 21:00:00"},
    )
    monkeypatch.setattr(
        service_runtime,
        "_mail_database_freshness",
        lambda: {
            "ok": True,
            "exists": True,
            "record_count": 10,
            "latest_seen_age_seconds": 7200,
            "latest_seen_capture_at": "2026-06-28 19:00:00",
        },
    )

    health = service_runtime.build_fanxiu_packet_service_health(
        {
            "running": True,
            "capture_runtime": {"game_running": True, "tcpdump_ready": True},
            "packet_worker": {
                "ok": True,
                "realtime_running": True,
                "mail_business_backlog_sync": {
                    "mail_source_probe": {
                        "source_count": 16,
                        "protocol_counts": {},
                        "has_any_mail_source": False,
                        "has_mail_action": False,
                    }
                },
            },
        }
    )

    assert "mail_database_stale" not in health["warnings"]
    assert health["mail_protocol_probe"]["has_any_mail_source"] is False


def test_packet_service_health_keeps_mail_stale_warning_with_recent_mail_protocol(monkeypatch):
    monkeypatch.setattr(
        service_runtime,
        "_latest_live_capture_summary",
        lambda _capture, _worker=None: {"age_seconds": 5, "path": "capture.pcap", "size": 128, "mtime_text": "2026-06-28 21:00:00"},
    )
    monkeypatch.setattr(
        service_runtime,
        "_mail_database_freshness",
        lambda: {
            "ok": True,
            "exists": True,
            "record_count": 10,
            "latest_seen_age_seconds": 7200,
            "latest_seen_capture_at": "2026-06-28 19:00:00",
        },
    )

    health = service_runtime.build_fanxiu_packet_service_health(
        {
            "running": True,
            "capture_runtime": {"game_running": True, "tcpdump_ready": True},
            "packet_worker": {
                "ok": True,
                "realtime_running": True,
                "mail_business_backlog_sync": {
                    "mail_source_probe": {
                        "source_count": 16,
                        "protocol_counts": {"SM_NewMail": 2},
                        "has_any_mail_source": True,
                        "has_mail_action": False,
                    }
                },
            },
        }
    )

    assert "mail_database_stale" in health["warnings"]
    assert health["mail_protocol_probe"]["protocol_counts"] == {"SM_NewMail": 2}


def test_packet_service_health_ignores_historical_mail_probe_for_stale_warning(monkeypatch):
    monkeypatch.setattr(
        service_runtime,
        "_latest_live_capture_summary",
        lambda _capture, _worker=None: {"age_seconds": 5, "path": "capture.pcap", "size": 128, "mtime_text": "2026-06-28 21:00:00"},
    )
    monkeypatch.setattr(
        service_runtime,
        "_mail_database_freshness",
        lambda: {
            "ok": True,
            "exists": True,
            "record_count": 10,
            "latest_seen_age_seconds": 7200,
            "latest_seen_capture_at": "2026-06-28 19:00:00",
        },
    )

    health = service_runtime.build_fanxiu_packet_service_health(
        {
            "running": True,
            "capture_runtime": {"game_running": True, "tcpdump_ready": True},
            "packet_worker": {
                "ok": True,
                "realtime_running": True,
                "mail_business_backlog_sync": {
                    "mail_source_probe": {
                        "source_count": 16,
                        "protocol_counts": {},
                        "has_any_mail_source": False,
                        "has_mail_action": False,
                    }
                },
                "maintenance": {
                    "bounded_mail_packet_sync": {
                        "mail_source_probe": {
                            "source_count": 96,
                            "protocol_counts": {"SM_NewMail": 4},
                            "has_any_mail_source": True,
                            "has_mail_action": True,
                        }
                    }
                },
            },
        }
    )

    assert "mail_database_stale" not in health["warnings"]
    assert health["mail_protocol_probe"]["protocol_counts"] == {}


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


def test_latest_live_capture_summary_prefers_packet_sync_active_path_without_directory_scan(monkeypatch, tmp_path):
    active = tmp_path / "fanxiu_runtime_snapshot_active.pcap"
    active.write_bytes(b"capture")

    def fail_glob(self, pattern):
        raise AssertionError("directory scan should not run when active capture path exists")

    monkeypatch.setattr(Path, "glob", fail_glob)

    summary = service_runtime._latest_live_capture_summary(
        {
            "current_pcap_path": str(tmp_path / "missing_current.pcap"),
            "packet_sync_active_path": str(active),
        }
    )

    assert summary["path"] == str(active)
    assert summary["name"] == active.name
    assert summary["source"] == "packet_sync_active"


def test_latest_live_capture_summary_uses_worker_latest_scanned_path_before_directory_scan(monkeypatch, tmp_path):
    scanned = tmp_path / "fanxiu_runtime_latest_scanned.pcap"
    scanned.write_bytes(b"decoded")

    def fail_glob(self, pattern):
        raise AssertionError("directory scan should not run when worker capture path exists")

    monkeypatch.setattr(Path, "glob", fail_glob)

    summary = service_runtime._latest_live_capture_summary(
        {"current_pcap_path": str(tmp_path / "missing_current.pcap")},
        {"latest_scanned_pcap": str(scanned)},
    )

    assert summary["path"] == str(scanned)
    assert summary["name"] == scanned.name
    assert summary["source"] == "worker_latest_scanned"


def test_latest_live_capture_summary_prefers_newest_existing_candidate(monkeypatch, tmp_path):
    current = tmp_path / "fanxiu_runtime_current.pcap"
    active = tmp_path / "fanxiu_runtime_snapshot_active.pcap"
    current.write_bytes(b"newer")
    active.write_bytes(b"older")
    current_mtime = current.stat().st_mtime
    older_mtime = current_mtime - 120
    os.utime(active, (older_mtime, older_mtime))

    def fail_glob(self, pattern):
        raise AssertionError("directory scan should not run when candidate capture paths exist")

    monkeypatch.setattr(Path, "glob", fail_glob)

    summary = service_runtime._latest_live_capture_summary(
        {
            "current_pcap_path": str(current),
            "packet_sync_active_path": str(active),
        },
        {"latest_scanned_pcap": str(active)},
    )

    assert summary["path"] == str(current)
    assert summary["name"] == current.name
    assert summary["source"] == "current_capture"


def test_latest_live_capture_summary_prefers_directory_newest_when_candidates_are_stale(monkeypatch, tmp_path):
    live_dir = tmp_path / "fanxiu" / "tcp-flow" / "live-captures"
    live_dir.mkdir(parents=True)
    stale_worker = live_dir / "fanxiu_runtime_older.pcap"
    fresh_dir = live_dir / "fanxiu_runtime_snapshot_fresh.pcap"
    stale_worker.write_bytes(b"older")
    fresh_dir.write_bytes(b"fresh")
    fresh_mtime = fresh_dir.stat().st_mtime
    stale_mtime = fresh_mtime - 120
    os.utime(stale_worker, (stale_mtime, stale_mtime))

    monkeypatch.setattr(service_runtime, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))

    summary = service_runtime._latest_live_capture_summary(
        {"current_pcap_path": str(tmp_path / "missing_current.pcap")},
        {"latest_scanned_pcap": str(stale_worker)},
    )

    assert summary["path"] == str(fresh_dir)
    assert summary["name"] == fresh_dir.name
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

    assert calls["count"] >= 1
    assert '"value": 1' in path.read_text(encoding="utf-8")


def test_write_json_falls_back_to_direct_write_when_replace_stays_locked(monkeypatch, tmp_path):
    path = tmp_path / "packet_service_state.json"
    original_write_text = Path.write_text
    calls = {"replace": 0, "direct_write": 0}

    def locked_replace(self, target):
        calls["replace"] += 1
        raise PermissionError(13, "denied", str(target))

    def track_write_text(self, data, *args, **kwargs):
        if self == path:
            calls["direct_write"] += 1
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(Path, "write_text", track_write_text)

    service_runtime._write_json(path, {"ok": True, "value": 2})

    assert calls["replace"] >= 1
    assert calls["direct_write"] == 1
    assert '"value": 2' in path.read_text(encoding="utf-8")


def test_write_json_falls_back_when_atomic_write_reports_winerror_5(monkeypatch, tmp_path):
    path = tmp_path / "packet_service_state.json"
    original_write_text = Path.write_text
    calls = {"replace": 0, "direct_write": 0}

    def locked_replace(self, target):
        calls["replace"] += 1
        exc = OSError(13, "denied", str(target))
        exc.winerror = 5
        raise exc

    def track_write_text(self, data, *args, **kwargs):
        if self == path:
            calls["direct_write"] += 1
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(Path, "write_text", track_write_text)

    service_runtime._write_json(path, {"ok": True, "value": 3})

    assert calls["replace"] >= 1
    assert calls["direct_write"] == 1
    assert '"value": 3' in path.read_text(encoding="utf-8")


def test_write_packet_service_state_retries_direct_write_when_target_is_temporarily_locked(monkeypatch, tmp_path):
    monkeypatch.setattr(service_runtime, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    state_path = service_runtime.get_fanxiu_packet_service_state_path()
    original_write_text = Path.write_text
    calls = {"state_writes": 0}

    def fake_write_json(path, payload):
        if path == state_path:
            raise PermissionError(13, "denied", str(path))
        return service_runtime._write_json_non_atomic(path, payload)

    def flaky_write_text(self, data, *args, **kwargs):
        if self == state_path:
            calls["state_writes"] += 1
            if calls["state_writes"] < 3:
                raise PermissionError(13, "denied", str(self))
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(service_runtime, "_write_json", fake_write_json)
    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    service_runtime._write_packet_service_state_json(state_path, {"updated_at": "2026-07-11 04:10:00", "ok": True})

    assert calls["state_writes"] == 3
    assert service_runtime._read_json(state_path, {})["updated_at"] == "2026-07-11 04:10:00"


def test_service_loop_still_processes_commands_when_state_write_fails(monkeypatch):
    events = {"processed": False, "state_writes": 0}

    monkeypatch.setattr(service_runtime.fanxiu_capture_runtime_service, "start_watchdog", lambda **_: None)
    monkeypatch.setattr(service_runtime.fanxiu_capture_runtime_service, "stop_watchdog", lambda: None)
    monkeypatch.setattr(service_runtime.fanxiu_packet_insight_worker, "start", lambda: None)
    monkeypatch.setattr(service_runtime.fanxiu_packet_insight_worker, "stop", lambda: None)
    monkeypatch.setattr(service_runtime.time, "monotonic", lambda: 100.0)

    def fake_write_state(extra=None):
        events["state_writes"] += 1
        if extra is None:
            raise PermissionError(13, "denied", "packet_service_state.json")
        return {"ok": True, "extra": extra}

    def fake_process_commands():
        events["processed"] = True
        raise SystemExit(0)

    monkeypatch.setattr(service_runtime, "write_fanxiu_packet_service_state", fake_write_state)
    monkeypatch.setattr(service_runtime, "process_pending_fanxiu_packet_service_commands", fake_process_commands)

    try:
        service_runtime.run_fanxiu_packet_service_loop(state_interval_seconds=15.0)
    except SystemExit:
        pass

    assert events["state_writes"] == 1
    assert events["processed"] is True
