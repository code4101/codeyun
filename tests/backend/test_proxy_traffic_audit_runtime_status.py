import sqlite3
from pathlib import Path

from backend.core.runtime import management as runtime_management
from backend.core.runtime import proxy_traffic_audit as proxy_traffic_audit_core
from backend.core.runtime import proxy_traffic_audit_runtime


def test_get_proxy_traffic_audit_status_lightweight_mode_skips_summary(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "proxy-traffic-audit.sqlite"
    proxy_traffic_audit_core.init_proxy_traffic_audit_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO collector_state(key, value) VALUES (?, ?)",
            ("last_sample_at", "2026-06-23T00:00:00Z"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO collector_state(key, value) VALUES (?, ?)",
            ("last_sample_summary", "2 connections"),
        )
        conn.commit()

    monkeypatch.setattr(proxy_traffic_audit_runtime, "list_proxy_traffic_audit_processes", lambda: [])
    monkeypatch.setattr(proxy_traffic_audit_runtime, "get_proxy_traffic_audit_db_path", lambda: db_path)

    def fail_summary(**_kwargs):
        raise AssertionError("summarize_proxy_traffic should not run in lightweight mode")

    monkeypatch.setattr(proxy_traffic_audit_runtime, "summarize_proxy_traffic", fail_summary)

    status = proxy_traffic_audit_runtime.get_proxy_traffic_audit_status(include_summary=False)

    assert status["last_sample_at"] == "2026-06-23T00:00:00Z"
    assert status["last_sample_summary"] == "2 connections"
    assert status["top_hosts"] == []


def test_runtime_management_uses_lightweight_proxy_traffic_status(monkeypatch):
    captured: list[bool] = []

    def fake_status(*, include_summary: bool = True):
        captured.append(include_summary)
        return {
            "title": "代理流量审计",
            "running": True,
            "state": "running",
            "state_label": "运行中",
            "interval_seconds": 2,
            "module": "backend.services.proxy_traffic_audit_daemon",
            "cwd": "D:/home/chenkunze/slns/codeyun",
            "db_path": "D:/tmp/proxy-traffic-audit.sqlite",
            "log_path": "D:/tmp/proxy-traffic-audit.log",
            "last_sample_at": "2026-06-23T00:00:00Z",
            "last_sample_summary": "2 connections",
            "top_hosts": [],
            "process_count": 1,
            "pids": [123],
        }

    monkeypatch.setattr(runtime_management, "get_proxy_traffic_audit_status", fake_status)

    item = runtime_management._serialize_proxy_traffic_audit_service_item()

    assert captured == [False]
    assert item["status"]["last_sample_summary"] == "2 connections"
    assert item["status"]["top_hosts"] == []
