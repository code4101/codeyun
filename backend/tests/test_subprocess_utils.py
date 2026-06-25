from __future__ import annotations

import json
import subprocess
import sys

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


def test_no_window_subprocess_kwargs_do_not_detach(monkeypatch):
    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    kwargs = subprocess_utils.no_window_subprocess_kwargs()

    assert kwargs["creationflags"] & subprocess_utils.WINDOWS_CREATE_NO_WINDOW
    assert not kwargs["creationflags"] & subprocess_utils.WINDOWS_DETACHED_PROCESS
    assert not kwargs["creationflags"] & subprocess_utils.WINDOWS_CREATE_NEW_PROCESS_GROUP


def test_install_no_window_popen_default_merges_creationflags(monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils.subprocess, "Popen", FakePopen)

    assert subprocess_utils.install_no_window_popen_default() is True
    proc = subprocess_utils.subprocess.Popen(["tool"], creationflags=0x20)

    assert isinstance(proc, FakePopen)
    assert calls[0][1]["creationflags"] & 0x20
    assert calls[0][1]["creationflags"] & subprocess_utils.WINDOWS_CREATE_NO_WINDOW
    assert calls[0][1]["startupinfo"].wShowWindow == subprocess.SW_HIDE


def test_install_no_window_popen_default_keeps_popen_subclassable(monkeypatch):
    class FakePopen:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils.subprocess, "Popen", FakePopen)

    assert subprocess_utils.install_no_window_popen_default() is True

    class ChildPopen(subprocess_utils.subprocess.Popen):
        pass

    assert issubclass(ChildPopen, FakePopen)


def test_install_no_window_popen_default_patches_cached_popen_reference(monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))

    cached_popen = FakePopen
    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils.subprocess, "Popen", FakePopen)

    assert subprocess_utils.install_no_window_popen_default() is True
    cached_popen(["tool"])

    assert calls[0][1]["creationflags"] & subprocess_utils.WINDOWS_CREATE_NO_WINDOW
    assert calls[0][1]["startupinfo"].wShowWindow == subprocess.SW_HIDE


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


def test_apply_python_no_window_env_prepends_sitecustomize(monkeypatch, tmp_path):
    sitecustomize_dir = tmp_path / "backend" / "core" / "runtime" / "no_window_sitecustomize"
    sitecustomize_dir.mkdir(parents=True)
    (sitecustomize_dir / "sitecustomize.py").write_text("", encoding="utf-8")
    existing_path = str(tmp_path / "existing")
    env = {"PYTHONPATH": existing_path}

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    result = subprocess_utils.apply_python_no_window_env(env, root_dir=tmp_path)

    assert result is env
    paths = env["PYTHONPATH"].split(subprocess_utils.os.pathsep)
    assert paths[0] == str(sitecustomize_dir)
    assert paths[1] == existing_path
    assert env["CODEYUN_NO_WINDOW_SUBPROCESS_DEFAULT"] == "1"


def test_apply_python_no_window_env_is_idempotent(monkeypatch, tmp_path):
    sitecustomize_dir = tmp_path / "backend" / "core" / "runtime" / "no_window_sitecustomize"
    sitecustomize_dir.mkdir(parents=True)
    (sitecustomize_dir / "sitecustomize.py").write_text("", encoding="utf-8")
    env = {"PYTHONPATH": str(sitecustomize_dir)}

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    subprocess_utils.apply_python_no_window_env(env, root_dir=tmp_path)

    assert env["PYTHONPATH"].split(subprocess_utils.os.pathsep).count(str(sitecustomize_dir)) == 1


def test_popen_python_script_background_injects_python_env(monkeypatch, tmp_path):
    calls = []
    sitecustomize_dir = tmp_path / "backend" / "core" / "runtime" / "no_window_sitecustomize"
    sitecustomize_dir.mkdir(parents=True)
    (sitecustomize_dir / "sitecustomize.py").write_text("", encoding="utf-8")

    class FakePopen:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils, "_repo_root_from_runtime", lambda: tmp_path)
    monkeypatch.setattr(subprocess_utils.subprocess, "Popen", FakePopen)

    subprocess_utils.popen_python_script_background(tmp_path / "worker.py", env={"PYTHONPATH": "external"})

    env = calls[0][1]["env"]
    assert env["PYTHONPATH"].split(subprocess_utils.os.pathsep)[0] == str(sitecustomize_dir)
    assert env["CODEYUN_NO_WINDOW_SUBPROCESS_DEFAULT"] == "1"


def test_popen_background_injects_python_env_for_python_command(monkeypatch, tmp_path):
    calls = []
    sitecustomize_dir = tmp_path / "backend" / "core" / "runtime" / "no_window_sitecustomize"
    sitecustomize_dir.mkdir(parents=True)
    (sitecustomize_dir / "sitecustomize.py").write_text("", encoding="utf-8")
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")

    class FakePopen:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils, "_repo_root_from_runtime", lambda: tmp_path)
    monkeypatch.setattr(subprocess_utils.subprocess, "Popen", FakePopen)

    subprocess_utils.popen_background([str(pythonw), "worker.py"], env={"PYTHONPATH": "external"})

    env = calls[0][1]["env"]
    assert env["PYTHONPATH"].split(subprocess_utils.os.pathsep)[0] == str(sitecustomize_dir)
    assert env["CODEYUN_NO_WINDOW_SUBPROCESS_DEFAULT"] == "1"


def test_popen_background_injects_managed_env_for_non_python_command(monkeypatch, tmp_path):
    calls = []
    sitecustomize_dir = tmp_path / "backend" / "core" / "runtime" / "no_window_sitecustomize"
    sitecustomize_dir.mkdir(parents=True)
    (sitecustomize_dir / "sitecustomize.py").write_text("", encoding="utf-8")
    preload = tmp_path / "scripts" / "node_windows_hide_child_processes.cjs"
    preload.parent.mkdir(parents=True)
    preload.write_text("", encoding="utf-8")

    class FakePopen:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils, "_repo_root_from_runtime", lambda: tmp_path)
    monkeypatch.setattr(subprocess_utils.subprocess, "Popen", FakePopen)

    subprocess_utils.popen_background(["git.exe", "status"], env={"PYTHONPATH": "external"})

    env = calls[0][1]["env"]
    assert env["PYTHONPATH"].split(subprocess_utils.os.pathsep)[0] == str(sitecustomize_dir)
    assert env["CODEYUN_NO_WINDOW_SUBPROCESS_DEFAULT"] == "1"
    assert f"--require={preload.as_posix()}" in env["NODE_OPTIONS"]


def test_run_hidden_injects_python_env_for_python_command(monkeypatch, tmp_path):
    calls = []
    sitecustomize_dir = tmp_path / "backend" / "core" / "runtime" / "no_window_sitecustomize"
    sitecustomize_dir.mkdir(parents=True)
    (sitecustomize_dir / "sitecustomize.py").write_text("", encoding="utf-8")
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils, "_repo_root_from_runtime", lambda: tmp_path)
    monkeypatch.setattr(subprocess_utils.subprocess, "run", fake_run)

    subprocess_utils.run_hidden([str(python), "worker.py"], env={"PYTHONPATH": "external"})

    env = calls[0][1]["env"]
    assert env["PYTHONPATH"].split(subprocess_utils.os.pathsep)[0] == str(sitecustomize_dir)
    assert env["CODEYUN_NO_WINDOW_SUBPROCESS_DEFAULT"] == "1"


def test_run_hidden_injects_node_env_for_node_command(monkeypatch, tmp_path):
    calls = []
    preload = tmp_path / "scripts" / "node_windows_hide_child_processes.cjs"
    preload.parent.mkdir(parents=True)
    preload.write_text("", encoding="utf-8")
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils, "_repo_root_from_runtime", lambda: tmp_path)
    monkeypatch.setattr(subprocess_utils.subprocess, "run", fake_run)

    subprocess_utils.run_hidden([str(node), "tool.js"], env={"NODE_OPTIONS": "--trace-warnings"})

    env = calls[0][1]["env"]
    assert f"--require={preload.as_posix()}" in env["NODE_OPTIONS"]
    assert "--trace-warnings" in env["NODE_OPTIONS"]


def test_python_service_env_patches_real_child_interpreter():
    if subprocess_utils.os.name != "nt":
        return

    env = subprocess_utils.python_service_env()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, os, subprocess; "
                "print(json.dumps({"
                "'flag': os.getenv('CODEYUN_NO_WINDOW_SUBPROCESS_DEFAULT'), "
                "'patched': bool(getattr(subprocess.Popen, '_codeyun_no_window_default', False))"
                "}))"
            ),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
        **subprocess_utils.hidden_subprocess_kwargs(),
    )

    payload = json.loads(result.stdout)
    assert payload == {"flag": "1", "patched": True}


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
