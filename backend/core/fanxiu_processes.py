from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import psutil


_FANXIU_PATTERNS = (
    re.compile(r"ckz2025[\\/]+fx", re.IGNORECASE),
    re.compile(r"tools[\\/]+凡修[^\\/]*\.py", re.IGNORECASE),
    re.compile(r"凡修[^\\/]*\.py", re.IGNORECASE),
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


def match_fanxiu_command_line(command_line: str) -> str | None:
    normalized = command_line.replace("\\", "/")
    for pattern in _FANXIU_PATTERNS:
        if pattern.search(normalized):
            return pattern.pattern
    return None


def _process_info(proc: psutil.Process, reason: str) -> FanxiuProcessInfo | None:
    try:
        cmdline = _normalize_command_line(proc.cmdline())
        created_at = datetime.fromtimestamp(proc.create_time()).strftime("%Y-%m-%d %H:%M:%S")
        return FanxiuProcessInfo(
            pid=proc.pid,
            parent_pid=proc.ppid(),
            name=proc.name(),
            command_line=cmdline,
            created_at=created_at,
            matched_reason=reason,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def _match_fanxiu_process(proc: psutil.Process) -> str | None:
    try:
        cmdline = _normalize_command_line(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    if not cmdline:
        return None

    return match_fanxiu_command_line(cmdline)


def list_fanxiu_processes() -> list[dict[str, Any]]:
    current_pid = os.getpid()
    items: list[FanxiuProcessInfo] = []
    for proc in psutil.process_iter(["pid"]):
        if proc.pid == current_pid:
            continue
        reason = _match_fanxiu_process(proc)
        if not reason:
            continue
        info = _process_info(proc, reason)
        if info is not None:
            items.append(info)
    items.sort(key=lambda item: (item.created_at or "", item.pid))
    return [asdict(item) for item in items]


def _collect_process_tree(root: psutil.Process) -> list[psutil.Process]:
    try:
        children = root.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        children = []
    return [*children, root]


def terminate_fanxiu_processes(timeout: float = 3.0) -> dict[str, Any]:
    current_pid = os.getpid()
    matched: dict[int, psutil.Process] = {}
    for proc in psutil.process_iter(["pid"]):
        if proc.pid == current_pid:
            continue
        if _match_fanxiu_process(proc):
            matched[proc.pid] = proc

    before = []
    targets: dict[int, psutil.Process] = {}
    for proc in matched.values():
        info = _process_info(proc, _match_fanxiu_process(proc) or "descendant")
        if info is not None:
            before.append(asdict(info))
        for target in _collect_process_tree(proc):
            if target.pid != current_pid:
                targets[target.pid] = target

    errors: list[dict[str, Any]] = []
    terminated: dict[int, dict[str, Any]] = {}

    # Children first so a parent cannot keep a worker alive while it exits.
    ordered_targets = sorted(targets.values(), key=lambda proc: proc.pid, reverse=True)
    for proc in ordered_targets:
        try:
            info = _process_info(proc, "matched-or-descendant")
            proc.terminate()
            if info is not None:
                terminated[proc.pid] = asdict(info)
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            errors.append({"pid": proc.pid, "error": str(exc)})

    _, alive = psutil.wait_procs(list(targets.values()), timeout=timeout)
    for proc in alive:
        try:
            info = _process_info(proc, "matched-or-descendant")
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
