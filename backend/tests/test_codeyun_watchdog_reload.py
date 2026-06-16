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
        "visible_console_monitor": False,
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


def test_terminate_dev_processes_keeps_watchdog_child(tmp_path, monkeypatch):
    class FakeProcess:
        def __init__(self, pid, children=None, watchdog=False):
            self.pid = pid
            self._children = children or []
            self.watchdog = watchdog
            self.terminated = False
            self.killed = False

        def children(self, recursive=False):
            return list(self._children)

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    backend_child = FakeProcess(101)
    watchdog_child = FakeProcess(102, watchdog=True)
    dev_proc = FakeProcess(100, [backend_child, watchdog_child])

    monkeypatch.setattr(codeyun_watchdog, "list_dev_processes", lambda: [{"pid": 100}])
    monkeypatch.setattr(codeyun_watchdog.psutil, "Process", lambda pid: dev_proc)
    monkeypatch.setattr(codeyun_watchdog.psutil, "wait_procs", lambda processes, timeout: (list(processes), []))
    monkeypatch.setattr(codeyun_watchdog, "_matches_watchdog", lambda proc: bool(getattr(proc, "watchdog", False)))

    stopped = codeyun_watchdog.terminate_dev_processes(0.1, tmp_path / "watchdog.log")

    assert stopped == [100]
    assert backend_child.terminated is True
    assert watchdog_child.terminated is False
    assert dev_proc.terminated is True


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


def test_run_once_keeps_visible_console_monitor_alive(tmp_path, monkeypatch):
    args = _args(tmp_path, visible_console_monitor=True)
    calls: list[str] = []

    monkeypatch.setattr(
        codeyun_watchdog,
        "check_health",
        lambda *_args, **_kwargs: {
            "healthy": True,
            "backend": {"ok": True, "message": "HTTP 200"},
            "frontend": {"ok": True, "message": "HTTP 200"},
        },
    )
    monkeypatch.setattr(
        codeyun_watchdog,
        "ensure_monitor_running",
        lambda: calls.append("monitor") or {"pid": 123, "alive": True, "started_now": False},
    )

    result = codeyun_watchdog.run_once(args, codeyun_watchdog.WatchdogState())

    assert calls == ["monitor"]
    assert result["visible_console_monitor"]["pid"] == 123


def test_visible_console_monitor_can_be_disabled(tmp_path, monkeypatch):
    args = _args(tmp_path, visible_console_monitor=False)

    monkeypatch.setattr(
        codeyun_watchdog,
        "check_health",
        lambda *_args, **_kwargs: {
            "healthy": True,
            "backend": {"ok": True, "message": "HTTP 200"},
            "frontend": {"ok": True, "message": "HTTP 200"},
        },
    )
    monkeypatch.setattr(
        codeyun_watchdog,
        "ensure_monitor_running",
        lambda: (_ for _ in ()).throw(AssertionError("monitor should be skipped")),
    )

    result = codeyun_watchdog.run_once(args, codeyun_watchdog.WatchdogState())

    assert result["visible_console_monitor"] is None


def test_visible_console_monitor_restart_is_logged(tmp_path, monkeypatch):
    args = _args(tmp_path, visible_console_monitor=True)

    monkeypatch.setattr(
        codeyun_watchdog,
        "ensure_monitor_running",
        lambda: {"pid": 456, "alive": True, "started_now": True},
    )

    result = codeyun_watchdog._ensure_visible_console_monitor(args, tmp_path / "watchdog.log")

    assert result["pid"] == 456
    assert "Visible console monitor started: PID 456" in (tmp_path / "watchdog.log").read_text(encoding="utf-8")


def test_vite_build_is_not_classified_as_dev_runner(monkeypatch):
    class Proc:
        pid = 1

    proc = Proc()
    monkeypatch.setattr(codeyun_watchdog, "_safe_name", lambda _proc: "cmd.exe")
    monkeypatch.setattr(
        codeyun_watchdog,
        "_cmdline_text",
        lambda _proc: r"C:\WINDOWS\system32\cmd.exe /d /s /c vite build",
    )
    monkeypatch.setattr(codeyun_watchdog, "_process_in_project", lambda _proc: True)

    assert codeyun_watchdog._matches_codeyun_dev(proc) is False


def test_compileall_precheck_is_not_classified_as_dev_runner(monkeypatch):
    class Proc:
        pid = 1

    proc = Proc()
    monkeypatch.setattr(codeyun_watchdog, "_safe_name", lambda _proc: "pythonw.exe")
    monkeypatch.setattr(
        codeyun_watchdog,
        "_cmdline_text",
        lambda _proc: r"D:\repo\.venv\Scripts\pythonw.exe -m compileall -q backend scripts dev.py",
    )
    monkeypatch.setattr(codeyun_watchdog, "_process_in_project", lambda _proc: True)

    assert codeyun_watchdog._matches_codeyun_dev(proc) is False


def test_vite_dev_server_is_classified_as_dev_runner(monkeypatch):
    class Proc:
        pid = 1

    proc = Proc()
    monkeypatch.setattr(codeyun_watchdog, "_safe_name", lambda _proc: "node.exe")
    monkeypatch.setattr(
        codeyun_watchdog,
        "_cmdline_text",
        lambda _proc: r"C:\node.exe D:\home\chenkunze\slns\codeyun\frontend\node_modules\vite\bin\vite.js",
    )
    monkeypatch.setattr(codeyun_watchdog, "_process_in_project", lambda _proc: True)

    assert codeyun_watchdog._matches_codeyun_dev(proc) is True


def test_status_output_includes_visible_console_monitor(monkeypatch, capsys):
    monkeypatch.setattr(codeyun_watchdog, "_read_lock_pid", lambda _path: 123)
    monkeypatch.setattr(codeyun_watchdog, "_watchdog_ancestor_pids", lambda _pid: set())
    monkeypatch.setattr(codeyun_watchdog, "list_watchdog_processes", lambda: [])
    monkeypatch.setattr(codeyun_watchdog, "list_dev_processes", lambda: [])
    monkeypatch.setattr(
        codeyun_watchdog,
        "read_visible_console_monitor_status",
        lambda: {"pid": 456, "alive": True, "heartbeat_at": "2026-06-16 14:00:00"},
    )

    assert codeyun_watchdog.main(["--status"]) == 0

    output = capsys.readouterr().out
    assert "visible_console_monitor" in output
    assert "456" in output
