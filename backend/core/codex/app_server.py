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


def _read_codex_app_server(
    *,
    method: str,
    params: dict[str, Any] | None = None,
    timeout_seconds: float = 25.0,
    executable: str | None = None,
    popen_factory: Callable[..., subprocess.Popen[str]] = popen_service,
    config_overrides: tuple[tuple[str, str], ...] = (),
    experimental_api: bool = False,
) -> dict[str, Any]:
    """Perform one bounded public request through Codex app-server.

    This uses Codex's public JSON-RPC surface and does not submit a model request.
    The child is always bounded and stopped after the request. ``config_overrides``
    become ``-c key=value`` flags for this child only, so the ChatGPT account quota can
    be read even while ``config.toml`` points at another provider.
    """

    timeout = max(1.0, float(timeout_seconds))
    command = [executable or resolve_codex_executable(prefer_desktop=True)]
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
                    "capabilities": {"experimentalApi": True} if experimental_api else {},
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
        request: dict[str, Any] = {"id": 2, "method": method}
        if params is not None:
            request["params"] = params
        _write_message(process.stdin, request)
        response = _wait_for_response(
            process,
            stdout_lines,
            request_id=2,
            deadline=deadline,
            stage=method,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise CodexAppServerError(
                f"Codex app-server {method} 返回了无效 result"
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


def read_codex_rate_limits(
    *, timeout_seconds: float = 25.0, executable: str | None = None,
    popen_factory: Callable[..., subprocess.Popen[str]] = popen_service,
    config_overrides: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """Read authenticated account limits without starting a model turn."""
    return _read_codex_app_server(
        method=CODEX_RATE_LIMITS_METHOD, timeout_seconds=timeout_seconds,
        executable=executable, popen_factory=popen_factory, config_overrides=config_overrides,
    )


def read_codex_thread_turns(
    thread_id: str, *, limit: int = 10, cursor: str | None = None,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    """Read bounded persisted turn status/errors without loading a writer.

    Uses public experimental pagination with items omitted, so diagnosing a
    compact/network failure never downloads the full long-running tool history.
    Cursors remain provider-owned. This child app-server's stored history does
    not establish desktop writer ownership or authorize resuming/forking work.
    Unsupported pagination is an explicit failure, not an unbounded fallback.
    Official schema: https://learn.chatgpt.com/docs/app-server#list-thread-turns
    """
    if not str(thread_id).strip():
        raise ValueError('thread_id 不能为空')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit 必须是1到100的整数')
    if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
        raise ValueError('cursor 必须为提供方返回的非空字符串')
    params = {'threadId': thread_id, 'limit': limit, 'sortDirection': 'desc',
              'itemsView': 'notLoaded'}
    if cursor is not None:
        params['cursor'] = cursor
    result = _read_codex_app_server(
        method='thread/turns/list', params=params, timeout_seconds=timeout_seconds,
        experimental_api=True,
    )
    if not isinstance(result.get('data'), list):
        raise CodexAppServerError('原生 turn 分页未返回 data 列表')
    return result


def read_codex_thread_goal(thread_id: str, *, timeout_seconds: float = 25.0) -> dict[str, Any]:
    """Read native persisted Goal state; an agent's final text is not proof."""
    if not str(thread_id).strip():
        raise ValueError('thread_id 不能为空')
    return _read_codex_app_server(
        method='thread/goal/get', params={'threadId': thread_id}, timeout_seconds=timeout_seconds,
        experimental_api=True,
        config_overrides=(('features.goals', 'true'),),
    )


def resume_codex_thread_goal(
    thread_id: str, *, owner_resume_authorization: str,
    owner_objective: str | None = None,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    """Apply an explicit owner's resume to the existing native Goal.

    Automatic continuations must not clear a blocked/paused Goal themselves.
    The public thread/goal/set contract preserves budget and cumulative usage.
    An explicitly corrected owner_objective replaces an incorrectly interpreted
    objective in the same Goal; missing or terminal Goals are never recreated.
    ``owner_resume_authorization`` identifies the actual instruction, not an
    inferred desire to keep a worker running.
    """
    if not str(owner_resume_authorization).strip():
        raise ValueError('恢复 Goal 必须提供明确所有者授权依据')
    if owner_objective is not None and not owner_objective.strip():
        raise ValueError('修正 Goal 目标不能为空')
    before = read_codex_thread_goal(thread_id, timeout_seconds=timeout_seconds)
    goal = before.get('goal')
    if not isinstance(goal, dict):
        raise CodexAppServerError('原生 Goal 不存在，拒绝以恢复操作新建 Goal')
    status = goal.get('status')
    if status == 'active' and owner_objective is None:
        return before
    if status not in {'active', 'blocked', 'paused'}:
        raise CodexAppServerError(f'原生 Goal 状态 {status!r} 不允许此恢复操作')
    params = {'threadId': thread_id, 'status': 'active'}
    if owner_objective is not None:
        params['objective'] = owner_objective.strip()
    result = _read_codex_app_server(
        method='thread/goal/set', params=params,
        timeout_seconds=timeout_seconds, experimental_api=True,
        config_overrides=(('features.goals', 'true'),),
    )
    restored = result.get('goal')
    if not isinstance(restored, dict) or restored.get('status') != 'active':
        raise CodexAppServerError('原生 Goal 恢复未返回 active，必须重新核验现场')
    expected_objective = owner_objective.strip() if owner_objective is not None else goal.get('objective')
    if restored.get('objective') != expected_objective:
        raise CodexAppServerError('原生 Goal 恢复后目标发生变化，必须核验现场')
    return result
