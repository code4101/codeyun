from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil


_FANXIU_COMMAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cmd:fx-path", re.compile(r"(?:^|[\\/])ckz2025[\\/]+fx(?:[\\/]|$)", re.IGNORECASE)),
    ("cmd:fanxiu-script", re.compile(r"(?:^|[\\/])(?:tools[\\/]+)?凡修[^\\/]*\.py\b", re.IGNORECASE)),
    ("cmd:fanxiu-module", re.compile(r"\bxlsln\.ckz2025\.fx(?:\.|$)", re.IGNORECASE)),
    ("cmd:codex-fx-env-loader", re.compile(r"\bCODEX_FX_CONTINUE_CODE\b")),
    ("cmd:codex-fx-run-log", re.compile(r"\bCODEX_FX_RUN_LOG\b", re.IGNORECASE)),
    ("cmd:codex-fx-btree", re.compile(r"\bcodex_(?:patched|continue)_btree\b", re.IGNORECASE)),
    ("cmd:fanxiu-data-dir", re.compile(r"m2508凡修", re.IGNORECASE)),
)

_FANXIU_CWD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cwd:fx-path", re.compile(r"(?:^|/)xlsln/ckz2025/fx(?:/|$)", re.IGNORECASE)),
    ("cwd:fanxiu-data-dir", re.compile(r"(?:^|/)data/m2508凡修(?:/|$)", re.IGNORECASE)),
)

_FANXIU_ENV_KEYS = {
    "CODEX_FX_CONTINUE_CODE",
    "CODEX_FX_RUN_LOG",
}

_FANXIU_ENV_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("env:fx-path", re.compile(r"(?:^|[\\/])ckz2025[\\/]+fx(?:[\\/]|$)", re.IGNORECASE)),
    ("env:fanxiu-module", re.compile(r"\bxlsln\.ckz2025\.fx(?:\.|$)", re.IGNORECASE)),
    ("env:codex-fx-btree", re.compile(r"\bcodex_(?:patched|continue)_btree\b", re.IGNORECASE)),
    ("env:fanxiu-data-dir", re.compile(r"m2508凡修", re.IGNORECASE)),
)

_XLPROJECT_ROOT_PATTERN = re.compile(r"(?:^|/)xlproject/?$", re.IGNORECASE)
_PYTHON_STDIN_COMMAND_PATTERN = re.compile(
    r"(?:^|\s)(?:\"[^\"]*pythonw?\.exe\"|[^\s]*pythonw?(?:\.exe)?)\s+-$"
    r"|(?:^|\s)(?:\"[^\"]*uv(?:\.exe)?\"|[^\s]*uv(?:\.exe)?)\s+run\s+python\s+-$",
    re.IGNORECASE,
)

_SHELL_NAMES = {
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
}

_CWD_ONLY_RUNNER_NAMES = _SHELL_NAMES | {
    "py",
    "py.exe",
    "python",
    "python.exe",
    "pythonw.exe",
    "uv",
    "uv.exe",
}

_DIAGNOSTIC_SHELL_MARKERS = (
    "get-ciminstance win32_process",
    "win32_process",
    "get-process",
    "where-object",
    "select-object",
    "match_fanxiu_command_line",
)


@dataclass
class FanxiuProcessInfo:
    pid: int
    parent_pid: int | None
    name: str
    command_line: str
    created_at: str | None
    matched_reason: str


def _normalize_command_line(cmdline: Any) -> str:
    if isinstance(cmdline, (list, tuple)):
        return " ".join(str(part) for part in cmdline if part is not None)
    return str(cmdline or "")


def _normalize_search_text(value: Any) -> str:
    return str(value or "").replace("\\", "/")


def match_fanxiu_command_line(command_line: str) -> str | None:
    normalized = _normalize_search_text(command_line)
    for reason, pattern in _FANXIU_COMMAND_PATTERNS:
        if pattern.search(normalized):
            return reason
    return None


def match_fanxiu_cwd(cwd: str | None) -> str | None:
    normalized = _normalize_search_text(cwd)
    if not normalized:
        return None
    for reason, pattern in _FANXIU_CWD_PATTERNS:
        if pattern.search(normalized):
            return reason
    return None


def match_fanxiu_environ(environ: dict[str, str] | None) -> str | None:
    if not environ:
        return None

    for key in _FANXIU_ENV_KEYS:
        if key in environ:
            return f"env-key:{key}"

    haystack = " ".join(str(value) for value in environ.values() if value)
    normalized = _normalize_search_text(haystack)
    for reason, pattern in _FANXIU_ENV_VALUE_PATTERNS:
        if pattern.search(normalized):
            return reason
    return None


def _looks_like_diagnostic_shell(name: str, command_line: str) -> bool:
    if Path(name or "").name.lower() not in _SHELL_NAMES:
        return False
    normalized = command_line.lower().replace("\\", "/")
    return any(marker in normalized for marker in _DIAGNOSTIC_SHELL_MARKERS)


def _can_match_by_cwd_only(name: str) -> bool:
    normalized_name = Path(name or "").name.lower()
    return not normalized_name or normalized_name in _CWD_ONLY_RUNNER_NAMES


def _looks_like_xlproject_stdin_python(name: str, command_line: str, cwd: str | None) -> bool:
    normalized_name = Path(name or "").name.lower()
    if normalized_name not in _CWD_ONLY_RUNNER_NAMES:
        return False
    if not _XLPROJECT_ROOT_PATTERN.search(_normalize_search_text(cwd)):
        return False
    return bool(_PYTHON_STDIN_COMMAND_PATTERN.search(_normalize_search_text(command_line).strip()))


def match_fanxiu_process_fields(
    *,
    name: str = "",
    command_line: str = "",
    cwd: str | None = None,
    environ: dict[str, str] | None = None,
) -> str | None:
    cwd_reason = match_fanxiu_cwd(cwd)
    env_reason = match_fanxiu_environ(environ)
    command_reason = match_fanxiu_command_line(command_line)

    if _looks_like_diagnostic_shell(name, command_line) and not (cwd_reason or env_reason):
        return None

    if _looks_like_xlproject_stdin_python(name, command_line, cwd):
        return "cmd+cwd:xlproject-python-stdin"
    if env_reason:
        return env_reason
    if command_reason:
        return command_reason
    if cwd_reason and _can_match_by_cwd_only(name):
        return cwd_reason
    return None


def _safe_command_line(proc: psutil.Process) -> str:
    try:
        return _normalize_command_line(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return ""


def _safe_cwd(proc: psutil.Process) -> str | None:
    try:
        return proc.cwd()
    except (psutil.NoSuchProcess, psutil.AccessDenied, FileNotFoundError, OSError):
        return None


def _safe_environ(proc: psutil.Process) -> dict[str, str] | None:
    try:
        return proc.environ()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _safe_name(proc: psutil.Process) -> str:
    try:
        return proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return ""


def _safe_ppid(proc: psutil.Process) -> int | None:
    try:
        return proc.ppid()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _safe_create_time(proc: psutil.Process) -> float | None:
    try:
        return proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _process_info(proc: psutil.Process, reason: str) -> FanxiuProcessInfo | None:
    try:
        created_ts = _safe_create_time(proc)
        return FanxiuProcessInfo(
            pid=proc.pid,
            parent_pid=_safe_ppid(proc),
            name=_safe_name(proc),
            command_line=_safe_command_line(proc),
            created_at=datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S") if created_ts else None,
            matched_reason=reason,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _match_fanxiu_process(proc: psutil.Process) -> str | None:
    name = _safe_name(proc)
    command_line = _safe_command_line(proc)
    cwd = _safe_cwd(proc)
    reason = match_fanxiu_process_fields(name=name, command_line=command_line, cwd=cwd)
    if reason:
        return reason
    return match_fanxiu_process_fields(name=name, command_line=command_line, cwd=cwd, environ=_safe_environ(proc))


def _collect_process_tree(root: psutil.Process) -> list[psutil.Process]:
    try:
        children = root.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        children = []
    return [*children, root]


def _collect_fanxiu_process_targets() -> tuple[dict[int, tuple[psutil.Process, str]], dict[int, tuple[psutil.Process, str]]]:
    current_pid = os.getpid()
    direct: dict[int, tuple[psutil.Process, str]] = {}
    for proc in psutil.process_iter(["pid"]):
        if proc.pid == current_pid:
            continue
        reason = _match_fanxiu_process(proc)
        if reason:
            direct[proc.pid] = (proc, reason)

    targets: dict[int, tuple[psutil.Process, str]] = dict(direct)
    for root_pid, (proc, _reason) in direct.items():
        for target in _collect_process_tree(proc):
            if target.pid == current_pid:
                continue
            targets.setdefault(target.pid, (target, f"descendant-of:{root_pid}"))
    return direct, targets


def list_fanxiu_processes() -> list[dict[str, Any]]:
    _direct, targets = _collect_fanxiu_process_targets()
    items: list[FanxiuProcessInfo] = []
    for proc, reason in targets.values():
        info = _process_info(proc, reason)
        if info is not None:
            items.append(info)
    items.sort(key=lambda item: (item.created_at or "", item.pid))
    return [asdict(item) for item in items]


def _target_depth(pid: int, parent_pids: dict[int, int | None]) -> int:
    depth = 0
    seen = {pid}
    parent_pid = parent_pids.get(pid)
    while parent_pid in parent_pids and parent_pid not in seen:
        seen.add(parent_pid)
        depth += 1
        parent_pid = parent_pids.get(parent_pid)
    return depth


def terminate_fanxiu_processes(timeout: float = 3.0) -> dict[str, Any]:
    direct, targets = _collect_fanxiu_process_targets()

    before = []
    for proc, reason in direct.values():
        info = _process_info(proc, reason)
        if info is not None:
            before.append(asdict(info))

    errors: list[dict[str, Any]] = []
    terminated: dict[int, dict[str, Any]] = {}

    # Children first so a parent cannot keep a worker alive while it exits.
    parent_pids = {pid: _safe_ppid(proc) for pid, (proc, _reason) in targets.items()}
    ordered_pids = sorted(targets, key=lambda pid: (_target_depth(pid, parent_pids), pid), reverse=True)
    for pid in ordered_pids:
        proc, reason = targets[pid]
        try:
            info = _process_info(proc, reason)
            proc.terminate()
            if info is not None:
                terminated[proc.pid] = asdict(info)
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            errors.append({"pid": proc.pid, "error": str(exc)})

    _, alive = psutil.wait_procs([proc for proc, _reason in targets.values()], timeout=timeout)
    for proc in alive:
        try:
            _reason = targets.get(proc.pid, (proc, "matched-or-descendant"))[1]
            info = _process_info(proc, _reason)
            proc.kill()
            if info is not None:
                terminated[proc.pid] = asdict(info)
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            errors.append({"pid": proc.pid, "error": str(exc)})

    if alive:
        psutil.wait_procs(alive, timeout=timeout)

    time.sleep(0.2)
    return {
        "matched": before,
        "terminated": sorted(terminated.values(), key=lambda item: item["pid"]),
        "remaining": list_fanxiu_processes(),
        "errors": errors,
    }
