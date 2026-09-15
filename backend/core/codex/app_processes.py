"""Stop and restart the local Codex / ChatGPT desktop processes.

Codex only reads ``config.toml`` at startup, so a service switch has to close the
running client first and reopen it afterwards.  The desktop client ships as a
Windows Store (packaged) app, so it is relaunched through its Start-menu AppID
when possible instead of executing the ``WindowsApps`` binary directly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import psutil


_APP_PROCESS_NAMES = {"chatgpt"}
_CODEX_PROCESS_PREFIXES = ("codex",)
_STOP_TIMEOUT_SECONDS = 8.0


def _normalized_name(name: str) -> str:
    """Normalize a process name so ``ChatGPT.exe`` matches ``chatgpt``.

    ``psutil`` reports the executable name including the ``.exe`` suffix on
    Windows, while PowerShell and the start-menu AppID omit it.
    """

    lowered = name.lower()
    return lowered[:-4] if lowered.endswith(".exe") else lowered
_APP_ID_QUERY = (
    "(Get-StartApps | Where-Object { $_.Name -match 'ChatGPT|Codex' } | "
    "Select-Object -First 1 -ExpandProperty AppID)"
)


def _matches_codex_process(name: str, executable: str) -> bool:
    lowered = _normalized_name(name)
    if lowered in _APP_PROCESS_NAMES:
        return True
    if lowered.startswith(_CODEX_PROCESS_PREFIXES):
        return True
    return "codex" in executable.lower()


def _terminate(process: psutil.Process) -> bool:
    try:
        process.terminate()
    except psutil.Error:
        return False
    try:
        process.wait(timeout=_STOP_TIMEOUT_SECONDS)
        return True
    except psutil.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=_STOP_TIMEOUT_SECONDS)
            return True
        except psutil.Error:
            return False
    except psutil.Error:
        return False


def stop_codex_processes() -> dict[str, Any]:
    """Terminate local Codex/ChatGPT processes and describe how to reopen them."""

    current_pid = os.getpid()
    targets: list[tuple[psutil.Process, str]] = []
    app_exe = ""
    app_running = False

    for process in psutil.process_iter(["pid", "name", "exe"]):
        info = process.info
        pid = info.get("pid")
        if pid == current_pid:
            continue
        name = str(info.get("name") or "")
        executable = str(info.get("exe") or "")
        if not _matches_codex_process(name, executable):
            continue
        if _normalized_name(name) in _APP_PROCESS_NAMES:
            app_running = True
            app_exe = executable or app_exe
        targets.append((process, name))

    stopped: list[dict[str, Any]] = []
    for process, name in targets:
        if _terminate(process):
            stopped.append({"pid": process.pid, "name": name})

    return {
        "was_app_running": app_running,
        "app_exe": app_exe,
        "stopped": stopped,
    }


def _resolve_start_app_id() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        return ""
    try:
        completed = subprocess.run(
            [executable, "-NoProfile", "-Command", _APP_ID_QUERY],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in (completed.stdout or "").splitlines():
        value = line.strip()
        if value:
            return value
    return ""


def start_codex_app(snapshot: dict[str, Any]) -> bool:
    """Reopen the ChatGPT desktop client if it was running before the switch."""

    if not snapshot.get("was_app_running"):
        return False

    if os.name == "nt":
        app_id = _resolve_start_app_id()
        if app_id:
            try:
                subprocess.Popen(
                    ["explorer.exe", f"shell:AppsFolder\\{app_id}"],
                    close_fds=True,
                )
                return True
            except OSError:
                pass

    app_exe = str(snapshot.get("app_exe") or "").strip()
    if app_exe and Path(app_exe).is_file():
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen([app_exe], close_fds=True, creationflags=creationflags)
            return True
        except OSError:
            return False
    return False
