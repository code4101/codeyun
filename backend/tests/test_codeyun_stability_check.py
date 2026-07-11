from __future__ import annotations

import argparse
import os
from pathlib import Path

from scripts import codeyun_stability_check


def _args(**overrides):
    values = {
        "backend_url": "http://127.0.0.1:8000/api/health",
        "frontend_url": "http://127.0.0.1:5173/",
        "timeout": 0.1,
        "startup_grace": 180.0,
        "watchdog_interval": 60.0,
        "popup_window_hours": 2.0,
        "ensure_popup_monitor": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_stability_report_ok_when_health_watchdog_and_popup_audit_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(codeyun_stability_check, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(codeyun_stability_check, "LATEST_REPORT_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(codeyun_stability_check, "HISTORY_REPORT_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(
        codeyun_stability_check.codeyun_watchdog,
        "check_health",
        lambda *_args, **_kwargs: {
            "healthy": True,
            "backend": {"ok": True, "message": "HTTP 200"},
            "frontend": {"ok": True, "message": "HTTP 200"},
        },
    )
    monkeypatch.setattr(
        codeyun_stability_check.codeyun_watchdog,
        "read_console_host_status",
        lambda **_kwargs: {"running": True, "pid": 123, "heartbeat_age_seconds": 1.0},
    )
    monkeypatch.setattr(
        codeyun_stability_check.codeyun_watchdog,
        "list_watchdog_processes",
        lambda: [{"pid": 456, "name": "pythonw.exe"}],
    )
    monkeypatch.setattr(
        codeyun_stability_check.codeyun_popup_audit,
        "ensure_monitor_running",
        lambda **_kwargs: {"alive": True},
    )
    monkeypatch.setattr(
        codeyun_stability_check.codeyun_popup_audit,
        "audit_recent",
        lambda *_args, **_kwargs: {
            "coverage_valid": True,
            "codeyun_events": 0,
            "codeyun_workspace_events": 0,
            "attention_events": 0,
        },
    )

    report = codeyun_stability_check.build_report(_args())

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert report["watchdog"]["running"] is True
    assert Path(report["report_path"]).is_file()
    assert Path(report["history_path"]).is_file()


def test_stability_report_fails_on_codeyun_service_popup(tmp_path, monkeypatch):
    monkeypatch.setattr(codeyun_stability_check, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(codeyun_stability_check, "LATEST_REPORT_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(codeyun_stability_check, "HISTORY_REPORT_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(
        codeyun_stability_check.codeyun_watchdog,
        "check_health",
        lambda *_args, **_kwargs: {
            "healthy": True,
            "backend": {"ok": True, "message": "HTTP 200"},
            "frontend": {"ok": True, "message": "HTTP 200"},
        },
    )
    monkeypatch.setattr(
        codeyun_stability_check.codeyun_watchdog,
        "read_console_host_status",
        lambda **_kwargs: {"running": False},
    )
    monkeypatch.setattr(
        codeyun_stability_check.codeyun_watchdog,
        "list_watchdog_processes",
        lambda: [{"pid": 456, "name": "pythonw.exe"}],
    )
    monkeypatch.setattr(
        codeyun_stability_check.codeyun_popup_audit,
        "ensure_monitor_running",
        lambda **_kwargs: {"alive": True},
    )
    monkeypatch.setattr(
        codeyun_stability_check.codeyun_popup_audit,
        "audit_recent",
        lambda *_args, **_kwargs: {
            "coverage_valid": True,
            "codeyun_events": 1,
            "codeyun_workspace_events": 0,
            "attention_events": 1,
        },
    )

    report = codeyun_stability_check.build_report(_args())

    assert report["ok"] is False
    assert report["status"] == "attention_required"
    assert any("visible console" in problem for problem in report["problems"])


def test_scheduled_task_install_uses_two_hour_hidden_python_command(monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr(codeyun_stability_check.os, "name", "nt")
    monkeypatch.setattr(
        codeyun_stability_check,
        "_scheduled_task_command",
        lambda: r"C:\repo\.venv\Scripts\pythonw.exe C:\repo\scripts\codeyun_stability_check.py --json",
    )

    def fake_run_schtasks(*args):
        captured["args"] = args
        return Result()

    monkeypatch.setattr(codeyun_stability_check, "_run_schtasks", fake_run_schtasks)

    result = codeyun_stability_check.install_scheduled_task()

    assert result["ok"] is True
    assert result["interval_hours"] == 2
    assert "/SC" in captured["args"]
    assert "HOURLY" in captured["args"]
    assert "/MO" in captured["args"]
    assert "2" in captured["args"]
    assert "pythonw.exe" in result["run_command"]


def test_root_console_launcher_declares_console_host_and_outer_reload():
    launcher = Path(codeyun_stability_check.ROOT_DIR) / "codeyun-dev.cmd"
    text = launcher.read_text(encoding="utf-8")

    assert "CODEYUN_DEV_CONSOLE_HOST=1" in text
    assert "CODEYUN_DEV_BACKEND_RELOAD_MODE=outer" in text
    assert "uv run dev.py" in text
    assert "pythonw.exe" not in text.lower()
