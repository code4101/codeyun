from __future__ import annotations

import json
import os

import pytest

import dev
from backend.core.services import dev_supervisor_control


def test_dev_supervisor_defaults_to_outer_backend_reload(monkeypatch):
    monkeypatch.delenv(dev.BACKEND_RELOAD_MODE_ENV, raising=False)

    args = dev.parse_args([])
    config = dev.load_config(args)

    assert config.backend_reload_mode == "outer"
    assert config.backend_reload_cooldown_seconds == 60.0
    assert config.backend_reload_max_wait_seconds == 600.0


def test_backend_reload_waits_for_quiet_period_but_has_maximum_wait():
    assert dev.backend_reload_trigger(
        now=59.0,
        first_change_at=0.0,
        last_change_at=30.0,
        quiet_seconds=60.0,
        max_wait_seconds=600.0,
    ) is None
    assert dev.backend_reload_trigger(
        now=90.0,
        first_change_at=0.0,
        last_change_at=30.0,
        quiet_seconds=60.0,
        max_wait_seconds=600.0,
    ) == "60.0s quiet period"
    assert dev.backend_reload_trigger(
        now=600.0,
        first_change_at=0.0,
        last_change_at=599.0,
        quiet_seconds=60.0,
        max_wait_seconds=600.0,
    ) == "600.0s maximum wait"


def test_manual_backend_reload_bypasses_waits():
    assert dev.backend_reload_trigger(
        now=1.0,
        first_change_at=0.0,
        last_change_at=1.0,
        quiet_seconds=60.0,
        max_wait_seconds=600.0,
        force_requested=True,
    ) == "manual force request"


def test_restart_backend_cli_writes_supervisor_request(monkeypatch):
    requests = []
    monkeypatch.setattr(dev, "request_backend_restart", lambda **kwargs: requests.append(kwargs) or {"request_id": "r1"})
    monkeypatch.setattr(dev, "log", lambda _message: None)
    monkeypatch.setattr(dev.sys, "argv", ["dev.py", "--restart-backend"])

    dev.main()

    assert requests == [{"source": "cli", "root_dir": os.path.dirname(os.path.abspath(dev.__file__))}]


def test_backend_restart_request_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_supervisor_control.tempfile, "gettempdir", lambda: os.fspath(tmp_path))

    request = dev_supervisor_control.request_backend_restart(source="test", root_dir=tmp_path)

    assert dev_supervisor_control.read_backend_restart_request(tmp_path) == request


def test_dev_supervisor_rejects_uvicorn_reload_mode(monkeypatch):
    monkeypatch.delenv(dev.BACKEND_RELOAD_MODE_ENV, raising=False)

    with pytest.raises(SystemExit):
        dev.parse_args(["--backend-reload-mode", "uvicorn"])


def test_windows_python_dev_runner_is_console_host(monkeypatch):
    monkeypatch.setattr(dev.os, "name", "nt")
    monkeypatch.setattr(dev.sys, "executable", os.fspath(r"D:\repo\.venv\Scripts\python.exe"))
    monkeypatch.delenv(dev.DEV_CONSOLE_HOST_ENV, raising=False)

    assert dev.is_console_host_enabled() is True


def test_pythonw_dev_runner_is_not_console_host(monkeypatch):
    monkeypatch.setattr(dev.os, "name", "nt")
    monkeypatch.setattr(dev.sys, "executable", os.fspath(r"D:\repo\.venv\Scripts\pythonw.exe"))
    monkeypatch.delenv(dev.DEV_CONSOLE_HOST_ENV, raising=False)

    assert dev.is_console_host_enabled() is False


def test_setup_env_prefers_explicit_python_for_recovery(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    default_scripts = root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    default_scripts.mkdir(parents=True)
    default_python = default_scripts / ("python.exe" if os.name == "nt" else "python")
    default_python.touch()
    recovery_scripts = tmp_path / "recovery-venv" / ("Scripts" if os.name == "nt" else "bin")
    recovery_scripts.mkdir(parents=True)
    recovery_python = recovery_scripts / ("python.exe" if os.name == "nt" else "python")
    recovery_python.touch()
    monkeypatch.setenv("CODEYUN_PYTHON_EXEC", os.fspath(recovery_python))
    monkeypatch.setattr(dev, "get_npm_path", lambda: "npm.cmd")
    monkeypatch.setattr(dev, "apply_background_node_env", lambda env, **_kwargs: env)
    monkeypatch.setattr(dev, "apply_background_python_env", lambda env, **_kwargs: env)

    env, python_executable, _npm_exec = dev.setup_env(os.fspath(root))

    assert python_executable == os.path.abspath(recovery_python)
    assert env["CODEYUN_PYTHON_EXEC"] == python_executable
    assert env["PATH"].split(os.pathsep)[0] == os.fspath(recovery_scripts)


def test_backend_tests_do_not_trigger_dev_backend_reload(tmp_path):
    root = tmp_path
    source_path = root / "backend" / "api" / "example.py"
    test_path = root / "backend" / "tests" / "test_example.py"

    assert dev.is_backend_watch_path(os.fspath(root), os.fspath(source_path)) is True
    assert dev.is_backend_watch_path(os.fspath(root), os.fspath(test_path)) is False


def test_frontend_health_probe_requires_javascript_response(monkeypatch):
    class Response:
        status = 200
        headers = {"Content-Type": "text/javascript"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(dev.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    assert dev.probe_frontend_http(5173) is True


def test_frontend_health_probe_handles_unreachable_server(monkeypatch):
    def fail(*_args, **_kwargs):
        raise dev.urllib.error.URLError("offline")

    monkeypatch.setattr(dev.urllib.request, "urlopen", fail)

    assert dev.probe_frontend_http(5173) is False


def test_backend_health_probe_requires_codeyun_health_payload(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"status":"ok","service":"codeyun-backend"}'

    monkeypatch.setattr(dev.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    assert dev.probe_backend_http(8000) is True


def test_backend_health_probe_rejects_unreachable_server(monkeypatch):
    def fail(*_args, **_kwargs):
        raise dev.urllib.error.URLError("offline")

    monkeypatch.setattr(dev.urllib.request, "urlopen", fail)

    assert dev.probe_backend_http(8000) is False


def test_console_host_status_keeps_heartbeat_and_component_health_separate(tmp_path, monkeypatch):
    status_path = tmp_path / "console-host.json"
    monkeypatch.setattr(dev, "console_host_status_path", lambda: os.fspath(status_path))
    monkeypatch.delattr(dev.write_console_host_status, "_started_at", raising=False)

    dev.write_console_host_status(
        os.fspath(tmp_path),
        "0.0.0.0",
        8000,
        5173,
        "outer",
        backend_healthy=False,
        backend_health_failure_count=3,
        frontend_healthy=True,
        frontend_health_failure_count=0,
    )

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["heartbeat_at"] > 0
    assert payload["healthy"] is False
    assert payload["components"]["backend"] == {
        "healthy": False,
        "consecutive_failures": 3,
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX file-lock behavior")
def test_dev_instance_lock_rejects_duplicate_repository_process(tmp_path):
    first = dev.acquire_dev_instance_lock(os.fspath(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="already running"):
            dev.acquire_dev_instance_lock(os.fspath(tmp_path))
    finally:
        first.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows named-mutex behavior")
def test_windows_dev_instance_lock_rejects_duplicate_repository_process(tmp_path):
    first = dev.acquire_dev_instance_lock(os.fspath(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="already running"):
            dev.acquire_dev_instance_lock(os.fspath(tmp_path))
    finally:
        first.close()
