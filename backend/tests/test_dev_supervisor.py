from __future__ import annotations

import os

import pytest

import dev
from backend.core.runtime import uvicorn_hidden


def test_dev_supervisor_defaults_to_outer_backend_reload(monkeypatch):
    monkeypatch.delenv(dev.BACKEND_RELOAD_MODE_ENV, raising=False)

    args = dev.parse_args([])
    config = dev.load_config(args)

    assert config.backend_reload_mode == "outer"


def test_dev_supervisor_rejects_uvicorn_reload_mode(monkeypatch):
    monkeypatch.delenv(dev.BACKEND_RELOAD_MODE_ENV, raising=False)

    with pytest.raises(SystemExit):
        dev.parse_args(["--backend-reload-mode", "uvicorn"])


def test_uvicorn_hidden_rejects_reload_arguments():
    with pytest.raises(SystemExit):
        uvicorn_hidden._parse_args(["--reload"])


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
