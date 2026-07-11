from __future__ import annotations

import argparse
import os
from pathlib import Path

from scripts import codeyun_watchdog


def _args(tmp_path: Path, **overrides):
    values = {
        "backend_url": "http://127.0.0.1:8000/api/health",
        "frontend_url": "http://127.0.0.1:5173/",
        "interval": 60,
        "timeout": 0.1,
        "stop_timeout": 0.1,
        "startup_grace": 0.0,
        "log_path": str(tmp_path / "watchdog.log"),
        "dev_stdout": str(tmp_path / "dev.out.log"),
        "dev_stderr": str(tmp_path / "dev.err.log"),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_unhealthy_service_restarts_without_source_precheck(tmp_path, monkeypatch):
    args = _args(tmp_path)

    monkeypatch.setattr(
        codeyun_watchdog,
        "check_health",
        lambda *_args, **_kwargs: {
            "healthy": False,
            "backend": {"ok": False, "message": "refused"},
            "frontend": {"ok": False, "message": "refused"},
        },
    )
    monkeypatch.setattr(codeyun_watchdog, "list_dev_component_processes", lambda: [])
    monkeypatch.setattr(
        codeyun_watchdog,
        "read_console_host_status",
        lambda **_kwargs: {"running": False},
    )
    monkeypatch.setattr(codeyun_watchdog, "terminate_dev_processes", lambda *_args, **_kwargs: [10, 11])
    monkeypatch.setattr(codeyun_watchdog, "start_detached_dev", lambda *_args, **_kwargs: 22)

    result = codeyun_watchdog.run_once(args)

    assert result["status"] == "restarted"
    assert result["stopped_pids"] == [10, 11]
    assert result["started_pid"] == 22


def test_console_host_suppresses_watchdog_restart(tmp_path, monkeypatch):
    args = _args(tmp_path)

    monkeypatch.setattr(
        codeyun_watchdog,
        "check_health",
        lambda *_args, **_kwargs: {
            "healthy": False,
            "backend": {"ok": False, "message": "refused"},
            "frontend": {"ok": False, "message": "refused"},
        },
    )
    monkeypatch.setattr(
        codeyun_watchdog,
        "read_console_host_status",
        lambda **_kwargs: {"running": True, "pid": 123, "heartbeat_age_seconds": 1.0},
    )
    monkeypatch.setattr(
        codeyun_watchdog,
        "start_detached_dev",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("console host owns restart")),
    )
    monkeypatch.setattr(
        codeyun_watchdog,
        "terminate_dev_processes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("console host must not be killed")),
    )

    result = codeyun_watchdog.run_once(args)

    assert result["status"] == "console_host_observed"
    assert result["started_pid"] is None
    assert result["console_host"]["pid"] == 123


def test_watchdog_does_not_manage_critical_command_services(tmp_path, monkeypatch):
    args = _args(tmp_path)

    monkeypatch.setattr(
        codeyun_watchdog,
        "check_health",
        lambda *_args, **_kwargs: {
            "healthy": True,
            "backend": {"ok": True, "message": "ok"},
            "frontend": {"ok": True, "message": "ok"},
        },
    )
    monkeypatch.setattr(
        codeyun_watchdog,
        "read_console_host_status",
        lambda **_kwargs: {"running": False},
    )

    result = codeyun_watchdog.run_once(args)

    assert result["status"] == "healthy"
    assert "critical_command_services" not in result


def test_starting_result_uses_component_processes_key(tmp_path, monkeypatch):
    args = _args(tmp_path, startup_grace=60)

    monkeypatch.setattr(
        codeyun_watchdog,
        "check_health",
        lambda *_args, **_kwargs: {
            "healthy": False,
            "backend": {"ok": False, "message": "refused"},
            "frontend": {"ok": False, "message": "refused"},
        },
    )
    monkeypatch.setattr(
        codeyun_watchdog,
        "read_console_host_status",
        lambda **_kwargs: {"running": False},
    )
    monkeypatch.setattr(codeyun_watchdog.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        codeyun_watchdog,
        "list_dev_component_processes",
        lambda: [{"pid": 100, "started_at": 95.0}],
    )
    monkeypatch.setattr(
        codeyun_watchdog,
        "start_detached_dev",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("still inside startup grace")),
    )

    result = codeyun_watchdog.run_once(args)

    assert result["status"] == "starting"
    assert result["dev_component_processes"] == [{"pid": 100, "started_at": 95.0}]
    assert "dev_processes" not in result


def test_watchdog_fallback_ignores_custom_dev_command_env(monkeypatch):
    monkeypatch.setenv("CODEYUN_WATCHDOG_DEV_COMMAND", r'pythonw.exe dev.py --backend-reload-mode off')
    monkeypatch.setattr(codeyun_watchdog.os, "name", "posix")
    monkeypatch.setattr(codeyun_watchdog.shutil, "which", lambda name: "/usr/bin/uv")

    assert codeyun_watchdog._resolve_dev_start_command() == ["/usr/bin/uv", "run", "dev.py"]


def test_watchdog_fallback_uses_pythonw_dev_on_windows(monkeypatch, tmp_path):
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")
    monkeypatch.setattr(codeyun_watchdog.os, "name", "nt")
    monkeypatch.setattr(codeyun_watchdog, "resolve_pythonw", lambda *_args, **_kwargs: os.fspath(pythonw))

    assert codeyun_watchdog._resolve_dev_start_command() == [os.fspath(pythonw), "dev.py"]


def test_watchdog_detached_dev_defaults_to_outer_backend_reload(tmp_path, monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 123

    def fake_popen_service(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setenv("CODEYUN_DEV_BACKEND_RELOAD_MODE", "off")
    monkeypatch.delenv(codeyun_watchdog.WATCHDOG_BACKEND_RELOAD_MODE_ENV, raising=False)
    monkeypatch.setattr(codeyun_watchdog, "_resolve_dev_start_command", lambda: ["pythonw.exe", "dev.py"])
    monkeypatch.setattr(codeyun_watchdog, "popen_service", fake_popen_service)

    started_pid = codeyun_watchdog.start_detached_dev(
        tmp_path / "watchdog.log",
        tmp_path / "dev.out.log",
        tmp_path / "dev.err.log",
    )

    assert started_pid == 123
    assert captured["command"] == ["pythonw.exe", "dev.py"]
    assert captured["env"]["CODEYUN_DEV_BACKEND_RELOAD_MODE"] == "outer"


def test_codeyun_instance_summary_avoids_duplicate_state_fields():
    summary = codeyun_watchdog.summarize_codeyun_dev_instance([
        {"pid": 10, "name": "pythonw.exe", "cmdline": "pythonw.exe dev.py"},
        {"pid": 11, "name": "node.exe", "cmdline": "node.exe vite.js"},
    ])

    assert summary == {
        "running": True,
        "instance_count": 1,
        "component_pids": {"dev_runner": [10], "frontend": [11]},
    }


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

    monkeypatch.setattr(codeyun_watchdog, "list_dev_component_processes", lambda: [{"pid": 100}])
    monkeypatch.setattr(codeyun_watchdog.psutil, "Process", lambda pid: dev_proc)
    monkeypatch.setattr(codeyun_watchdog.psutil, "wait_procs", lambda processes, timeout: (list(processes), []))
    monkeypatch.setattr(codeyun_watchdog, "_matches_watchdog", lambda proc: bool(getattr(proc, "watchdog", False)))

    stopped = codeyun_watchdog.terminate_dev_processes(0.1, tmp_path / "watchdog.log")

    assert stopped == [100]
    assert backend_child.terminated is True
    assert watchdog_child.terminated is False
    assert dev_proc.terminated is True


def test_terminate_dev_processes_ignores_vanished_pid(tmp_path, monkeypatch):
    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid
            self.terminated = False

        def children(self, recursive=False):
            return []

        def terminate(self):
            self.terminated = True

        def kill(self):
            raise AssertionError("live process should not need kill")

    live_proc = FakeProcess(101)

    def fake_process(pid):
        if pid == 100:
            raise codeyun_watchdog.psutil.NoSuchProcess(pid)
        return live_proc

    monkeypatch.setattr(codeyun_watchdog, "list_dev_component_processes", lambda: [{"pid": 100}, {"pid": 101}])
    monkeypatch.setattr(codeyun_watchdog.psutil, "Process", fake_process)
    monkeypatch.setattr(codeyun_watchdog.psutil, "wait_procs", lambda processes, timeout: (list(processes), []))

    stopped = codeyun_watchdog.terminate_dev_processes(0.1, tmp_path / "watchdog.log")

    assert stopped == [101]
    assert live_proc.terminated is True


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


def test_status_output_includes_console_host(monkeypatch, capsys):
    monkeypatch.setattr(codeyun_watchdog, "_read_lock_pid", lambda _path: 123)
    monkeypatch.setattr(codeyun_watchdog, "_watchdog_ancestor_pids", lambda _pid: set())
    monkeypatch.setattr(codeyun_watchdog, "list_watchdog_processes", lambda: [])
    monkeypatch.setattr(codeyun_watchdog, "list_dev_component_processes", lambda: [])
    monkeypatch.setattr(
        codeyun_watchdog,
        "read_console_host_status",
        lambda: {"running": True, "pid": 456},
    )

    assert codeyun_watchdog.main(["--status"]) == 0

    output = capsys.readouterr().out
    assert "console_host" in output
    assert "dev_component_processes" in output
    assert "dev_processes" not in output
    assert "456" in output
