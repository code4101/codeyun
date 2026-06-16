from __future__ import annotations

import os

from backend.core.devices import device


def test_windows_command_resolver_rewrites_uv_dev_to_pythonw(monkeypatch, tmp_path):
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    pythonw = scripts_dir / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")

    monkeypatch.setattr(device, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(device.os, "name", "nt")
    monkeypatch.setattr(device.sys, "executable", os.fspath(scripts_dir / "python.exe"))

    assert device.WindowsCommandResolver().resolve("uv run dev.py") == [os.fspath(pythonw), "dev.py"]


def test_windows_command_resolver_rewrites_python_script_to_pythonw(monkeypatch, tmp_path):
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    pythonw = scripts_dir / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")

    monkeypatch.setattr(device, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(device.os, "name", "nt")
    monkeypatch.setattr(device.sys, "executable", os.fspath(scripts_dir / "python.exe"))

    command = device.WindowsCommandResolver().resolve(
        r'D:\home\chenkunze\slns\codeyun\.venv\Scripts\python.exe scripts\sync_rime_config.py --target-name codepc_mi15'
    )

    assert command[:2] == [os.fspath(pythonw), r"scripts\sync_rime_config.py"]
    assert command[2:] == ["--target-name", "codepc_mi15"]
