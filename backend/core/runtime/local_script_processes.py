from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil


_SCRIPT_EXTENSIONS = {
    ".py",
    ".pyw",
    ".ipynb",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".ps1",
    ".bat",
    ".cmd",
    ".sh",
}

_EXTENSION_KIND = {
    ".py": "python",
    ".pyw": "python",
    ".ipynb": "jupyter",
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
    ".ts": "node",
    ".tsx": "node",
    ".ps1": "powershell",
    ".bat": "cmd",
    ".cmd": "cmd",
    ".sh": "shell",
}

_PYTHON_NAMES = {"python", "python.exe", "pythonw.exe", "py", "py.exe", "uv", "uv.exe"}
_NODE_RUNNER_NAMES = {
    "node",
    "node.exe",
    "npm",
    "npm.cmd",
    "npx",
    "npx.cmd",
    "pnpm",
    "pnpm.cmd",
    "yarn",
    "yarn.cmd",
}
_POWERSHELL_NAMES = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
_SHELL_NAMES = {"bash", "bash.exe", "sh", "sh.exe"}


@dataclass
class LocalScriptProcessInfo:
    pid: int
    parent_pid: int | None
    name: str
    kind: str
    script: str
    script_path: str | None
    command_line: str
    cwd: str | None
    created_at: str | None
    runtime_seconds: int | None
    project_hint: str


def normalize_command_line(cmdline: Any) -> str:
    if isinstance(cmdline, (list, tuple)):
        return " ".join(str(part) for part in cmdline if part is not None)
    return str(cmdline or "")


def _clean_arg(arg: Any) -> str:
    return str(arg or "").strip().strip("\"'")


def _arg_extension(arg: str) -> str:
    cleaned = _clean_arg(arg)
    if not cleaned:
        return ""
    cleaned = cleaned.split("?", 1)[0]
    return Path(cleaned).suffix.lower()


def _has_script_extension(arg: str) -> bool:
    return _arg_extension(arg) in _SCRIPT_EXTENSIONS


def _resolve_script_path(arg: str, cwd: str | None) -> str | None:
    cleaned = _clean_arg(arg)
    if not cleaned:
        return None
    path = Path(cleaned)
    if path.is_absolute():
        return str(path)
    if cwd:
        return str(Path(cwd) / path)
    return cleaned


def _infer_project_hint(script_path: str | None, cwd: str | None, command_line: str) -> str:
    haystack = " ".join(part for part in (script_path, cwd, command_line) if part).replace("\\", "/").lower()
    if re.search(r"/ckz2025/fx(?:/|$)", haystack):
        return "fx"
    if re.search(r"/codeyun(?:/|$)", haystack):
        return "codeyun"
    if script_path:
        return Path(script_path).parent.name or Path(script_path).name
    if cwd:
        return Path(cwd).name
    return ""


def _infer_from_module(args: list[str], exe_name: str) -> tuple[str, str, str | None] | None:
    if exe_name not in _PYTHON_NAMES:
        return None
    for index, arg in enumerate(args[:-1]):
        if arg == "-m" and args[index + 1].strip():
            return "python-module", f"-m {args[index + 1]}", None
    return None


def _infer_from_python_stdin(args: list[str], exe_name: str) -> tuple[str, str, str | None] | None:
    if exe_name in {"uv", "uv.exe"}:
        lowered = [arg.lower() for arg in args]
        if len(lowered) >= 4 and lowered[1] == "run" and lowered[2].startswith("python") and lowered[3] == "-":
            return "python-stdin", "uv run python -", None
        return None

    if exe_name in _PYTHON_NAMES and "-" in args[1:]:
        return "python-stdin", "python -", None
    return None


def _infer_from_runner(args: list[str], exe_name: str) -> tuple[str, str, str | None] | None:
    if not args:
        return None

    if exe_name in _NODE_RUNNER_NAMES:
        for index, arg in enumerate(args):
            if arg == "run" and index + 1 < len(args):
                return "npm", f"{Path(args[0]).name} run {args[index + 1]}", None

    if exe_name in _POWERSHELL_NAMES:
        for index, arg in enumerate(args[:-1]):
            if arg.lower() in {"-file", "-f"} and _has_script_extension(args[index + 1]):
                script_path = _resolve_script_path(args[index + 1], None)
                return "powershell", Path(args[index + 1]).name, script_path

    if exe_name in _SHELL_NAMES and len(args) > 1 and _has_script_extension(args[1]):
        script_path = _resolve_script_path(args[1], None)
        return "shell", Path(args[1]).name, script_path

    return None


def _infer_script(args: list[str], cwd: str | None) -> tuple[str, str, str | None] | None:
    if not args:
        return None

    exe_name = Path(args[0]).name.lower()
    stdin_match = _infer_from_python_stdin(args, exe_name)
    if stdin_match is not None:
        return stdin_match

    runner_match = _infer_from_runner(args, exe_name)
    if runner_match is not None:
        return runner_match

    for arg in args:
        if not _has_script_extension(arg):
            continue
        script_path = _resolve_script_path(arg, cwd)
        extension = _arg_extension(arg)
        return _EXTENSION_KIND.get(extension, "script"), Path(arg).name, script_path

    return _infer_from_module(args, exe_name)


def _process_info(proc: psutil.Process, current_time: float) -> LocalScriptProcessInfo | None:
    try:
        args = [_clean_arg(arg) for arg in proc.cmdline()]
        args = [arg for arg in args if arg]
        if not args:
            return None
        try:
            cwd = proc.cwd()
        except (psutil.AccessDenied, FileNotFoundError, OSError):
            cwd = None
        inferred = _infer_script(args, cwd)
        if inferred is None:
            return None
        kind, script, script_path = inferred
        created_ts = proc.create_time()
        command_line = normalize_command_line(args)
        return LocalScriptProcessInfo(
            pid=proc.pid,
            parent_pid=proc.ppid(),
            name=proc.name(),
            kind=kind,
            script=script,
            script_path=script_path,
            command_line=command_line,
            cwd=cwd,
            created_at=datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S"),
            runtime_seconds=max(0, int(current_time - created_ts)),
            project_hint=_infer_project_hint(script_path, cwd, command_line),
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def list_local_script_processes(*, include_current: bool = True) -> list[dict[str, Any]]:
    current_pid = os.getpid()
    current_time = time.time()
    items: list[LocalScriptProcessInfo] = []
    for proc in psutil.process_iter(["pid"]):
        if not include_current and proc.pid == current_pid:
            continue
        info = _process_info(proc, current_time)
        if info is not None:
            items.append(info)

    items.sort(key=lambda item: (item.project_hint != "fx", item.project_hint, item.created_at or "", item.pid))
    return [asdict(item) for item in items]
