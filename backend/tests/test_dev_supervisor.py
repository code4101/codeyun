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
