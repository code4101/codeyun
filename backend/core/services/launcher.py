from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from backend.core.services._subprocess import (
    apply_node_windows_hide_env,
    apply_python_no_window_env,
    background_popen_kwargs,
    install_no_window_popen_default,
    managed_child_env,
    node_npm_command,
    node_script_command,
    popen_background,
    popen_python_module_background,
    popen_python_script_background,
    python_module_command,
    python_script_command,
    pythonw_command,
    resolve_npm_executable,
    resolve_python,
    resolve_pythonw,
    run_hidden,
)


def run_quiet(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Run a short command without opening a Windows console window."""

    return run_hidden(command, **kwargs)


def check_call_quiet(command: list[str], **kwargs: Any) -> None:
    """Run a short command and raise on failure without opening a console."""

    run_hidden(command, check=True, **kwargs)


def check_output_quiet(command: list[str], **kwargs: Any) -> bytes | str:
    """Return command output without opening a Windows console window."""

    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("check", True)
    result = run_hidden(command, **kwargs)
    return result.stdout


def popen_service(command: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
    """Start a long-running background service using CodeYun's no-window policy."""

    return popen_background(command, **kwargs)


def python_service_command(
    *args: str,
    preferred_root: str | Path | None = None,
    executable: str | Path | None = None,
) -> list[str]:
    return pythonw_command(*args, preferred_root=preferred_root, executable=executable)


def python_module_service_command(
    module: str,
    *args: str,
    preferred_root: str | Path | None = None,
    executable: str | Path | None = None,
) -> list[str]:
    return python_module_command(module, *args, preferred_root=preferred_root, executable=executable)


def python_script_service_command(
    script_path: str | Path,
    *args: str,
    preferred_root: str | Path | None = None,
    executable: str | Path | None = None,
) -> list[str]:
    return python_script_command(script_path, *args, preferred_root=preferred_root, executable=executable)


def popen_python_module_service(
    module: str,
    *args: str,
    preferred_root: str | Path | None = None,
    executable: str | Path | None = None,
    **kwargs: Any,
) -> subprocess.Popen[Any]:
    return popen_python_module_background(
        module,
        *args,
        preferred_root=preferred_root,
        executable=executable,
        **kwargs,
    )


def popen_python_script_service(
    script_path: str | Path,
    *args: str,
    preferred_root: str | Path | None = None,
    executable: str | Path | None = None,
    **kwargs: Any,
) -> subprocess.Popen[Any]:
    return popen_python_script_background(
        script_path,
        *args,
        preferred_root=preferred_root,
        executable=executable,
        **kwargs,
    )


def apply_background_node_env(env: dict[str, str], *, root_dir: str | Path | None = None) -> dict[str, str]:
    return apply_node_windows_hide_env(env, root_dir=root_dir)


def apply_background_python_env(env: dict[str, str], *, root_dir: str | Path | None = None) -> dict[str, str]:
    return apply_python_no_window_env(env, root_dir=root_dir)


def apply_managed_child_env(env: dict[str, str] | None = None, *, root_dir: str | Path | None = None) -> dict[str, str]:
    return managed_child_env(env, root_dir=root_dir)


def install_child_process_no_window_default() -> bool:
    return install_no_window_popen_default()
