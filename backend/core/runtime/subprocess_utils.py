from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
WINDOWS_CREATE_NO_WINDOW = 0x08000000
WINDOWS_DETACHED_PROCESS = 0x00000008


def _repo_root_from_runtime() -> Path:
    return Path(__file__).resolve().parents[3]


def _windows_startupinfo_hidden() -> Any:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def hidden_subprocess_kwargs(*, new_process_group: bool = True, detached: bool = True) -> dict[str, Any]:
    """Return kwargs for subprocess.run/check_call that must not flash a console."""

    if os.name != "nt":
        return {}
    creationflags = WINDOWS_CREATE_NO_WINDOW
    if new_process_group:
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", WINDOWS_CREATE_NEW_PROCESS_GROUP)
    if detached:
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", WINDOWS_DETACHED_PROCESS)
    return {
        "creationflags": creationflags,
        "startupinfo": _windows_startupinfo_hidden(),
    }


def background_popen_kwargs(*, independent: bool = True) -> dict[str, Any]:
    """Return kwargs for long-running background children.

    On Windows this detaches the process from the current console/job and hides
    any console window. On POSIX it starts a new session.
    """

    if os.name != "nt":
        return {"start_new_session": True} if independent else {}
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", WINDOWS_CREATE_NEW_PROCESS_GROUP)
    creationflags |= WINDOWS_CREATE_NO_WINDOW
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", WINDOWS_DETACHED_PROCESS)
    if independent:
        creationflags |= WINDOWS_CREATE_BREAKAWAY_FROM_JOB
    return {
        "creationflags": creationflags,
        "startupinfo": _windows_startupinfo_hidden(),
    }


def resolve_pythonw(preferred_root: str | Path | None = None, executable: str | Path | None = None) -> str:
    """Prefer pythonw.exe on Windows so background Python tasks never open consoles."""

    current = Path(executable or sys.executable)
    if os.name == "nt":
        if preferred_root is not None:
            venv_pythonw = Path(preferred_root) / ".venv" / "Scripts" / "pythonw.exe"
            if venv_pythonw.is_file():
                return os.fspath(venv_pythonw)
        if current.name.lower() == "python.exe":
            sibling_pythonw = current.with_name("pythonw.exe")
            if sibling_pythonw.is_file():
                return os.fspath(sibling_pythonw)
    return os.fspath(current)


def resolve_python(preferred_root: str | Path | None = None, executable: str | Path | None = None) -> str:
    """Prefer python.exe for hidden services that still need normal stdio semantics."""

    current = Path(executable or sys.executable)
    if os.name == "nt":
        if preferred_root is not None:
            venv_python = Path(preferred_root) / ".venv" / "Scripts" / "python.exe"
            if venv_python.is_file():
                return os.fspath(venv_python)
        if current.name.lower() == "pythonw.exe":
            sibling_python = current.with_name("python.exe")
            if sibling_python.is_file():
                return os.fspath(sibling_python)
    return os.fspath(current)


def resolve_npm_executable() -> str:
    if os.name != "nt":
        return shutil.which("npm") or "npm"
    npm_path = shutil.which("npm.cmd") or shutil.which("npm")
    if npm_path:
        return npm_path
    node_path = shutil.which("node.exe") or shutil.which("node")
    if node_path:
        npm_candidate = Path(node_path).with_name("npm.cmd")
        if npm_candidate.is_file():
            return os.fspath(npm_candidate)
    trae_npm = Path.home() / ".trae" / "sdks" / "versions" / "node" / "current" / "npm.cmd"
    if trae_npm.is_file():
        return os.fspath(trae_npm)
    return "npm.cmd"


def node_npm_command(*args: str, npm_executable: str | Path | None = None) -> list[str]:
    """Run npm through node+npm-cli.js on Windows to avoid npm.cmd/cmd.exe consoles."""

    npm = os.fspath(npm_executable) if npm_executable else resolve_npm_executable()
    if os.name == "nt":
        node = shutil.which("node.exe") or shutil.which("node")
        npm_cli = Path(npm).resolve(strict=False).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if node and npm_cli.is_file():
            return [node, os.fspath(npm_cli), *args]
    return [npm, *args]


def node_script_command(script_path: str | Path, *args: str) -> list[str]:
    node = shutil.which("node.exe" if os.name == "nt" else "node") or shutil.which("node")
    if node:
        return [node, os.fspath(script_path), *args]
    return ["node", os.fspath(script_path), *args]


def apply_node_windows_hide_env(env: dict[str, str], *, root_dir: str | Path | None = None) -> dict[str, str]:
    """Ensure background Node tools hide their own Windows child processes."""

    if os.name != "nt":
        return env
    root = Path(root_dir).resolve(strict=False) if root_dir is not None else _repo_root_from_runtime()
    preload = (root / "scripts" / "node_windows_hide_child_processes.cjs").resolve(strict=False)
    if not preload.is_file():
        return env
    option = f"--require={preload.as_posix()}"
    existing = str(env.get("NODE_OPTIONS") or "").strip()
    if option in existing.split():
        return env
    env["NODE_OPTIONS"] = f"{option} {existing}".strip()
    return env


def run_hidden(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    kwargs.setdefault("shell", False)
    kwargs.update(hidden_subprocess_kwargs())
    return subprocess.run(command, **kwargs)


def popen_background(command: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    kwargs.setdefault("shell", False)
    kwargs.update(background_popen_kwargs(independent=True))
    return subprocess.Popen(command, **kwargs)


def pythonw_command(
    *args: str,
    preferred_root: str | Path | None = None,
    executable: str | Path | None = None,
) -> list[str]:
    return [resolve_pythonw(preferred_root=preferred_root, executable=executable), *args]


def python_module_command(
    module: str,
    *args: str,
    preferred_root: str | Path | None = None,
    executable: str | Path | None = None,
) -> list[str]:
    return pythonw_command("-m", module, *args, preferred_root=preferred_root, executable=executable)


def python_script_command(
    script_path: str | Path,
    *args: str,
    preferred_root: str | Path | None = None,
    executable: str | Path | None = None,
) -> list[str]:
    return pythonw_command(os.fspath(script_path), *args, preferred_root=preferred_root, executable=executable)


def popen_python_module_background(
    module: str,
    *args: str,
    preferred_root: str | Path | None = None,
    executable: str | Path | None = None,
    **kwargs: Any,
) -> subprocess.Popen[Any]:
    return popen_background(
        python_module_command(module, *args, preferred_root=preferred_root, executable=executable),
        **kwargs,
    )


def popen_python_script_background(
    script_path: str | Path,
    *args: str,
    preferred_root: str | Path | None = None,
    executable: str | Path | None = None,
    **kwargs: Any,
) -> subprocess.Popen[Any]:
    return popen_background(
        python_script_command(script_path, *args, preferred_root=preferred_root, executable=executable),
        **kwargs,
    )
