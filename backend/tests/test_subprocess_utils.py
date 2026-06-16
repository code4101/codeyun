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
