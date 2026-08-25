from __future__ import annotations

import subprocess

import dev
from backend.core.services import _subprocess as subprocess_utils


def test_start_backend_uses_no_window_without_detaching(monkeypatch):
    captured = {}

    class FakeProcess:
        pass

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(dev, "ensure_backend_port_available", lambda *_args: None)
    monkeypatch.setattr(dev.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    process = dev.start_backend(
        "D:/repo",
        {"PYTHONUNBUFFERED": "1"},
        "D:/repo/.venv/Scripts/python.exe",
        "outer",
        "127.0.0.1",
        8000,
    )

    assert isinstance(process, FakeProcess)
    flags = captured["kwargs"]["creationflags"]
    assert flags & subprocess_utils.WINDOWS_CREATE_NO_WINDOW
    assert not flags & subprocess_utils.WINDOWS_DETACHED_PROCESS
    assert not flags & subprocess_utils.WINDOWS_CREATE_NEW_PROCESS_GROUP
    assert captured["kwargs"]["startupinfo"].wShowWindow == subprocess.SW_HIDE


def test_setup_env_applies_python_and_node_child_policies(monkeypatch, tmp_path):
    applied = []

    monkeypatch.setattr(dev, "load_dotenv_into_env", lambda *_args: None)
    monkeypatch.setattr(dev, "get_npm_path", lambda: "npm")
    monkeypatch.setattr(dev, "apply_background_node_env", lambda env, **_kwargs: applied.append("node") or env)
    monkeypatch.setattr(dev, "apply_background_python_env", lambda env, **_kwargs: applied.append("python") or env)

    dev.setup_env(str(tmp_path))

    assert applied == ["node", "python"]
