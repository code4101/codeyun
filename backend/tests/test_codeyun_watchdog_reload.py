from __future__ import annotations

import argparse
import time
from pathlib import Path

from scripts import codeyun_watchdog


def _args(tmp_path: Path, **overrides):
    values = {
        "backend_url": "http://127.0.0.1:8000/api/health",
        "frontend_url": "http://127.0.0.1:5173/",
        "timeout": 0.1,
        "stop_timeout": 0.1,
        "startup_grace": 0.0,
        "reload": True,
        "reload_quiet": 1.0,
        "reload_check_timeout": 1.0,
        "log_path": str(tmp_path / "watchdog.log"),
        "dev_stdout": str(tmp_path / "dev.out.log"),
        "dev_stderr": str(tmp_path / "dev.err.log"),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_reload_change_waits_for_quiet_period(tmp_path, monkeypatch):
    args = _args(tmp_path, reload_quiet=120.0)
    state = codeyun_watchdog.WatchdogState(reload_snapshot={"old.py": (1, 1)})

    monkeypatch.setattr(
        codeyun_watchdog,
        "check_health",
        lambda *_args, **_kwargs: {
            "healthy": True,
            "backend": {"ok": True, "message": "HTTP 200"},
            "frontend": {"ok": True, "message": "HTTP 200"},
        },
    )
    monkeypatch.setattr(codeyun_watchdog, "build_reload_snapshot", lambda: {"old.py": (2, 1)})
    monkeypatch.setattr(
        codeyun_watchdog,
        "start_detached_dev",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not restart before quiet period")),
    )

    result = codeyun_watchdog.run_once(args, state)

    assert result["status"] == "reload_pending"
    assert str(state.pending_reload_reason or "").startswith("changed:")


def test_reload_precheck_failure_keeps_current_service(tmp_path, monkeypatch):
    args = _args(tmp_path, reload_quiet=0.1)
    state = codeyun_watchdog.WatchdogState(
        reload_snapshot={"old.py": (2, 1)},
        pending_reload_reason="changed: old.py",
        pending_reload_since=time.time() - 10,
    )

    monkeypatch.setattr(
        codeyun_watchdog,
        "check_health",
        lambda *_args, **_kwargs: {
            "healthy": True,
            "backend": {"ok": True, "message": "HTTP 200"},
            "frontend": {"ok": True, "message": "HTTP 200"},
        },
    )
    monkeypatch.setattr(codeyun_watchdog, "build_reload_snapshot", lambda: {"old.py": (2, 1)})
    monkeypatch.setattr(codeyun_watchdog, "run_reload_precheck", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        codeyun_watchdog,
        "terminate_dev_processes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("precheck failure must keep old service")),
    )

    result = codeyun_watchdog.run_once(args, state)

    assert result["status"] == "reload_precheck_failed"
    assert result["started_pid"] is None
    assert state.pending_reload_reason == "changed: old.py"


def test_unhealthy_service_restarts_without_reload_precheck(tmp_path, monkeypatch):
    args = _args(tmp_path)
    state = codeyun_watchdog.WatchdogState(reload_snapshot={"old.py": (1, 1)})

    monkeypatch.setattr(
        codeyun_watchdog,
        "check_health",
        lambda *_args, **_kwargs: {
            "healthy": False,
            "backend": {"ok": False, "message": "refused"},
            "frontend": {"ok": False, "message": "refused"},
        },
    )
    monkeypatch.setattr(codeyun_watchdog, "list_dev_processes", lambda: [])
    monkeypatch.setattr(
        codeyun_watchdog,
        "run_reload_precheck",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("recovery restart is not a hot reload")),
    )
    monkeypatch.setattr(codeyun_watchdog, "terminate_dev_processes", lambda *_args, **_kwargs: [10, 11])
    monkeypatch.setattr(codeyun_watchdog, "start_detached_dev", lambda *_args, **_kwargs: 22)
    monkeypatch.setattr(codeyun_watchdog, "build_reload_snapshot", lambda: {"old.py": (1, 1)})

    result = codeyun_watchdog.run_once(args, state)

    assert result["status"] == "restarted"
    assert result["stopped_pids"] == [10, 11]
    assert result["started_pid"] == 22
    assert state.pending_reload_reason is None


def test_default_reload_precheck_uses_pythonw_on_windows(monkeypatch, tmp_path):
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    pythonw = scripts_dir / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")

    monkeypatch.setattr(codeyun_watchdog, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(codeyun_watchdog.os, "name", "nt")
    monkeypatch.delenv("CODEYUN_WATCHDOG_RELOAD_CHECK_COMMAND", raising=False)

    command = codeyun_watchdog._resolve_reload_check_command()

    assert command[:4] == [str(pythonw), "-m", "compileall", "-q"]
