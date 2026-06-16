from __future__ import annotations

import subprocess

from backend.core.runtime import subprocess_utils


def test_hidden_subprocess_kwargs_hide_windows_console(monkeypatch):
    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    kwargs = subprocess_utils.hidden_subprocess_kwargs()

    assert kwargs["creationflags"] & subprocess_utils.WINDOWS_CREATE_NO_WINDOW
    assert kwargs["creationflags"] & getattr(
        subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        subprocess_utils.WINDOWS_CREATE_NEW_PROCESS_GROUP,
    )
    assert kwargs["creationflags"] & getattr(
        subprocess,
        "DETACHED_PROCESS",
        subprocess_utils.WINDOWS_DETACHED_PROCESS,
    )
    assert kwargs["startupinfo"].wShowWindow == subprocess.SW_HIDE


def test_background_popen_kwargs_detaches_windows_process(monkeypatch):
    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    kwargs = subprocess_utils.background_popen_kwargs(independent=True)

    assert kwargs["creationflags"] & subprocess_utils.WINDOWS_CREATE_NO_WINDOW
    assert kwargs["creationflags"] & subprocess_utils.WINDOWS_CREATE_BREAKAWAY_FROM_JOB
    assert kwargs["creationflags"] & getattr(
        subprocess,
        "DETACHED_PROCESS",
        subprocess_utils.WINDOWS_DETACHED_PROCESS,
    )


def test_resolve_pythonw_prefers_repo_venv_on_windows(monkeypatch, tmp_path):
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    pythonw = scripts_dir / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    assert subprocess_utils.resolve_pythonw(tmp_path, scripts_dir / "python.exe") == str(pythonw)


def test_resolve_python_prefers_repo_venv_on_windows(monkeypatch, tmp_path):
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    python = scripts_dir / "python.exe"
    python.write_text("", encoding="utf-8")

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    assert subprocess_utils.resolve_python(tmp_path, scripts_dir / "pythonw.exe") == str(python)


def test_node_script_command_uses_node_without_cmd(monkeypatch, tmp_path):
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")
    script = tmp_path / "vite.js"
    script.write_text("", encoding="utf-8")

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils.shutil, "which", lambda name: str(node) if name == "node.exe" else None)

    assert subprocess_utils.node_script_command(script, "build") == [str(node), str(script), "build"]


def test_python_module_command_prefers_pythonw(monkeypatch, tmp_path):
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    pythonw = scripts_dir / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    assert subprocess_utils.python_module_command("backend.app", "--host", "127.0.0.1", preferred_root=tmp_path) == [
        str(pythonw),
        "-m",
        "backend.app",
        "--host",
        "127.0.0.1",
    ]


def test_python_script_command_prefers_explicit_pythonw_sibling(monkeypatch, tmp_path):
    scripts_dir = tmp_path / "Scripts"
    scripts_dir.mkdir()
    python = scripts_dir / "python.exe"
    python.write_text("", encoding="utf-8")
    pythonw = scripts_dir / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")
    script = tmp_path / "worker.py"

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    assert subprocess_utils.python_script_command(script, "--loop", executable=python) == [
        str(pythonw),
        str(script),
        "--loop",
    ]


def test_run_hidden_merges_hidden_kwargs_without_duplicate_shell(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils.subprocess, "run", fake_run)

    subprocess_utils.run_hidden(["tool"], stdout=subprocess.PIPE)

    assert calls[0][1]["shell"] is False
    assert calls[0][1]["stdout"] == subprocess.PIPE
    assert calls[0][1]["creationflags"] & subprocess_utils.WINDOWS_CREATE_NO_WINDOW


def test_popen_background_defaults_to_devnull_and_hidden(monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils.subprocess, "Popen", FakePopen)

    subprocess_utils.popen_background(["tool"])

    kwargs = calls[0][1]
    assert kwargs["shell"] is False
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["creationflags"] & subprocess_utils.WINDOWS_CREATE_NO_WINDOW


def test_apply_node_windows_hide_env_injects_node_options(monkeypatch, tmp_path):
    preload = tmp_path / "scripts" / "node_windows_hide_child_processes.cjs"
    preload.parent.mkdir()
    preload.write_text("", encoding="utf-8")
    env = {"NODE_OPTIONS": "--max-old-space-size=2048"}

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    result = subprocess_utils.apply_node_windows_hide_env(env, root_dir=tmp_path)

    assert result is env
    assert f"--require={preload.as_posix()}" in env["NODE_OPTIONS"]
    assert "--max-old-space-size=2048" in env["NODE_OPTIONS"]
