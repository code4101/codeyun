from __future__ import annotations

import os

from backend.core.devices import device


def test_windows_background_command_resolver_rewrites_uv_dev_to_pythonw(monkeypatch, tmp_path):
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


def test_local_device_start_task_uses_unified_launcher_for_python_script(monkeypatch, tmp_path):
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    python = scripts_dir / "python.exe"
    python.write_text("", encoding="utf-8")
    pythonw = scripts_dir / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")
    calls = []

    class FakePopen:
        pid = 12345

    class FakePsutilProcess:
        def __init__(self, pid):
            self.pid = pid

    monkeypatch.setattr(device.LocalDevice, "load_pids", lambda self: None)
    monkeypatch.setattr(device.LocalDevice, "save_pids", lambda self: None)
    monkeypatch.setattr(device.LogManager, "prepare_log_path", lambda _device_id, _task_id: os.fspath(tmp_path / "task.log"))
    monkeypatch.setattr(device.TimeoutWatchdog, "start", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(device.psutil, "Process", FakePsutilProcess)
    monkeypatch.setattr(device, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(device.os, "name", "nt")
    monkeypatch.setattr(device.sys, "platform", "win32")
    monkeypatch.setattr(device.sys, "executable", os.fspath(python))

    def fake_popen_service(command, **kwargs):
        calls.append((command, kwargs))
        return FakePopen()

    monkeypatch.setattr(device, "popen_service", fake_popen_service)

    local_device = device.LocalDevice(device_id="local", name="local")
    result = local_device.start_task("task-1", f'"{python}" worker.py --flag', cwd=os.fspath(tmp_path))

    assert result == {"status": "started", "pid": FakePopen.pid}
    assert calls[0][0] == [os.fspath(pythonw), "worker.py", "--flag"]
    assert calls[0][1]["cwd"] == os.fspath(tmp_path)
