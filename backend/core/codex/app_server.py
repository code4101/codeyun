from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any, TextIO

from backend.core.codex.escalation import resolve_codex_executable
from backend.core.services.launcher import popen_service


CODEX_RATE_LIMITS_METHOD = "account/rateLimits/read"


class CodexAppServerError(RuntimeError):
    """Report a bounded, observable Codex app-server protocol failure."""


def _write_message(stream: TextIO, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()


def _drain_lines(stream: TextIO, target: queue.Queue[str] | list[str]) -> None:
    for line in iter(stream.readline, ""):
        if isinstance(target, queue.Queue):
            target.put(line)
        else:
            target.append(line.rstrip())


def _wait_for_response(
    process: subprocess.Popen[str],
    stdout_lines: queue.Queue[str],
    *,
    request_id: int,
    deadline: float,
    stage: str,
) -> dict[str, Any]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexAppServerError(f"Codex app-server {stage} 超时")
        try:
            line = stdout_lines.get(timeout=min(0.2, remaining))
        except queue.Empty:
            return_code = process.poll()
            if return_code is not None:
                raise CodexAppServerError(
                    f"Codex app-server 在 {stage} 前退出，exit_code={return_code}"
                )
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("id") != request_id:
            continue
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or error).strip()
            raise CodexAppServerError(f"Codex app-server {stage} 失败：{message}")
        return payload


def read_codex_rate_limits(
    *,
    timeout_seconds: float = 25.0,
    executable: str | None = None,
    popen_factory: Callable[..., subprocess.Popen[str]] = popen_service,
    config_overrides: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """Read the authenticated account rate-limit snapshot through Codex app-server.

    This uses Codex's public JSON-RPC surface and does not submit a model request.
    The child is always bounded and stopped after the single read.  ``config_overrides``
    become ``-c key=value`` flags for this child only, so the ChatGPT account quota can
    be read even while ``config.toml`` points at another provider.
    """

    timeout = max(1.0, float(timeout_seconds))
    command = [executable or resolve_codex_executable()]
    for key, value in config_overrides:
        command.extend(["-c", f"{key}={value}"])
    command.extend(["app-server", "--listen", "stdio://"])
    process = popen_factory(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        close_fds=True,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.terminate()
        raise CodexAppServerError("Codex app-server 未建立标准输入输出管道")

    stdout_lines: queue.Queue[str] = queue.Queue()
    stderr_lines: list[str] = []
    stdout_thread = threading.Thread(
        target=_drain_lines,
        args=(process.stdout, stdout_lines),
        name="codex-rate-limits-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_lines,
        args=(process.stderr, stderr_lines),
        name="codex-rate-limits-stderr",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.monotonic() + timeout
    try:
        _write_message(
            process.stdin,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "codeyun-weekly-quota", "version": "1.0.0"},
                    "capabilities": {},
                },
            },
        )
        _wait_for_response(
            process,
            stdout_lines,
            request_id=1,
            deadline=deadline,
            stage="initialize",
        )
        _write_message(process.stdin, {"method": "initialized"})
        _write_message(process.stdin, {"id": 2, "method": CODEX_RATE_LIMITS_METHOD})
        response = _wait_for_response(
            process,
            stdout_lines,
            request_id=2,
            deadline=deadline,
            stage=CODEX_RATE_LIMITS_METHOD,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise CodexAppServerError(
                f"Codex app-server {CODEX_RATE_LIMITS_METHOD} 返回了无效 result"
            )
        return result
    except (BrokenPipeError, OSError) as exc:
        detail = next((line for line in reversed(stderr_lines) if line.strip()), "")
        suffix = f"；stderr={detail}" if detail else ""
        raise CodexAppServerError(f"Codex app-server 通信失败：{exc}{suffix}") from exc
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
