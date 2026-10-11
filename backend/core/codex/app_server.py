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


def read_codex_thread_metadata(thread_id: str, *, timeout_seconds: float = 25.0) -> dict[str, Any]:
    """Read exact native thread metadata without turns, writer load or resume.

    The provider owns rollout-path discovery. A mismatched identity or missing
    path is an explicit failure, never a global-cache or guessed-path fallback.
    """
    if not str(thread_id).strip():
        raise ValueError('thread_id 不能为空')
    result = _read_codex_app_server(method='thread/read',
        params={'threadId': thread_id, 'includeTurns': False}, timeout_seconds=timeout_seconds)
    thread = result.get('thread')
    if not isinstance(thread, dict) or thread.get('id') != thread_id:
        raise CodexAppServerError('原生 thread 元数据身份不匹配')
    if not isinstance(thread.get('path'), str) or not thread['path'].strip():
        raise CodexAppServerError('原生 thread 元数据缺少 rollout path')
    return thread


def read_codex_thread_readonly_session(thread_id: str, *, timeout_seconds: float = 5) -> dict[str, Any]:
    """Observe metadata and one persisted turn in the SAME fresh reader process.

    This connection initializes then reads; it never loads/resumes a writer,
    starts a model, changes a Goal or reads a rollout/database directly. Its
    notLoaded/interrupted lifecycle view is not the desktop writer's authority.
    Initialization uses the client's bounded 30s contract, then each of the two
    reads has timeout_seconds. The reader is always closed after observation.
    """
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError('Exact original thread required')
    if type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 30:
        raise ValueError('Bounded read timeout required')
    from backend.core.codex.wechat_agent import CodexWechatClient
    client = CodexWechatClient()
    try:
        client.start()
        metadata = client.rpc('thread/read', {'threadId': thread_id, 'includeTurns': False},
            timeout=timeout_seconds).get('thread')
        if not isinstance(metadata, dict) or metadata.get('id') != thread_id:
            raise CodexAppServerError('Readonly session thread identity mismatch')
        history = client.rpc('thread/turns/list', {'threadId': thread_id, 'limit': 1,
            'sortDirection': 'desc', 'itemsView': 'notLoaded'}, timeout=timeout_seconds)
        if not isinstance(history.get('data'), list) or len(history['data']) > 1:
            raise CodexAppServerError('Readonly session exceeded bounded history contract')
        return {'thread_id': thread_id, 'source': 'fresh_independent_app_server',
            'metadata': metadata, 'turns': history['data'], 'writer_loaded_by_reader': False,
            'writer_authority': False, 'model_started': False}
    finally:
        client.close()


def read_codex_thread_turns(
    thread_id: str, *, limit: int = 10, cursor: str | None = None,
    sort_direction: str = 'desc',
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
    if sort_direction not in ('asc', 'desc'):
        raise ValueError('sort_direction 必须为 asc 或 desc')
    params = {'threadId': thread_id, 'limit': limit, 'sortDirection': sort_direction,
              'itemsView': 'notLoaded'}
    if cursor is not None:
        params['cursor'] = cursor
    result = _read_codex_app_server(
        method='thread/turns/list', params=params, timeout_seconds=timeout_seconds,
        experimental_api=True,
    )
    if not isinstance(result.get('data'), list) or len(result['data']) > limit:
        raise CodexAppServerError('原生 turn 分页未返回 data 列表')
    return result


def read_codex_thread_items(thread_id: str, *, turn_id: str, limit: int = 100,
                           cursor: str | None = None, timeout_seconds: float = 25.0) -> dict:
    """Read one bounded page of original persisted items from an exact turn.

    Native thread/items/list is experimental and may be unsupported. Propagate
    that error; never resume a writer or fall back to raw rollout/DB parsing.
    Contract: https://learn.chatgpt.com/docs/app-server#list-thread-turns
    """
    if not isinstance(thread_id, str) or not thread_id.strip() or not isinstance(turn_id, str) or not turn_id.strip():
        raise ValueError('Exact thread and turn IDs required')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Item limit requires 1..100')
    if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
        raise ValueError('Provider cursor must be a nonempty string')
    params = {'threadId': thread_id, 'turnId': turn_id, 'limit': limit, 'sortDirection': 'asc'}
    if cursor is not None:
        params['cursor'] = cursor
    result = _read_codex_app_server(method='thread/items/list', params=params,
                                   timeout_seconds=timeout_seconds, experimental_api=True)
    if not isinstance(result.get('data'), list) or len(result['data']) > limit:
        raise CodexAppServerError('Native item pagination returned an invalid bounded page')
    if any(not isinstance(row, dict) or row.get('turnId') != turn_id or
           not isinstance(row.get('item'), dict) for row in result['data']):
        raise CodexAppServerError('Native item pagination changed the requested turn identity')
    return result


def read_codex_thread_turn_window(thread_id: str, *, start_epoch: int, end_epoch: int,
                                 max_pages: int = 12, timeout_seconds: float = 90.0) -> dict:
    """Locate persisted turns intersecting one forensic window, without items.

    Owns pagination and a soft total elapsed budget. Child process startup and
    teardown can overrun the remaining RPC budget; late responses explicitly
    report deadline_exceeded and cannot prove complete coverage. Reaching a
    page/time bound never establishes absence of an action.
    Exact turn IDs can then be passed to read_codex_thread_items. Timestamps are
    native Unix seconds. A null historical start cannot be guessed from text.
    """
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError('Exact original thread required')
    if (type(start_epoch) is not int or type(end_epoch) is not int or
            start_epoch <= 0 or not start_epoch < end_epoch <= start_epoch + 3600):
        raise ValueError('A positive forensic window of at most one hour is required')
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise ValueError('max_pages requires 1..20')
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 120:
        raise ValueError('A bounded total timeout of at most 120s is required')
    started = time.monotonic()
    deadline = started + timeout_seconds
    cursor = None
    seen = set()
    matches = []
    unknown_times = []
    uncertain_overlap = []
    pages = 0
    covered = False
    read_error = None
    while pages < max_pages and deadline - time.monotonic() >= 1.0:
        try:
            page = read_codex_thread_turns(thread_id, limit=100, cursor=cursor,
                timeout_seconds=min(25.0, deadline - time.monotonic()))
        except CodexAppServerError as exc:
            read_error = str(exc)
            break  # Keep earlier pages; a read failure proves no lifecycle end.
        pages += 1
        for turn in page['data']:
            start = turn.get('startedAt')
            end = turn.get('completedAt')
            if type(start) is not int:
                unknown_times.append(turn.get('id'))
                continue
            if end is None and start < start_epoch:
                uncertain_overlap.append(turn.get('id'))
            elif end is not None and (type(end) is not int or end < start):
                unknown_times.append(turn.get('id'))
                continue
            if start <= end_epoch and (end is None or type(end) is int and end >= start_epoch):
                matches.append(turn)
        if time.monotonic() > deadline:
            break  # Process startup/teardown time also belongs to this budget.
        if not page.get('nextCursor'):
            covered = True
            break
        if page['data'] and all(type(t.get('startedAt')) is int and t['startedAt'] < start_epoch for t in page['data']):
            covered = True
            break
        cursor = page['nextCursor']
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise CodexAppServerError('Native turn pagination made no progress')
        seen.add(cursor)
    elapsed = time.monotonic() - started
    return {'thread_id': thread_id, 'start_epoch': start_epoch, 'end_epoch': end_epoch,
            'turns': matches, 'pages_read': pages,
            'complete': covered and not unknown_times and not uncertain_overlap and elapsed <= timeout_seconds,
            'pagination_complete': covered, 'elapsed_seconds': elapsed,
            'deadline_exceeded': elapsed > timeout_seconds,
            'read_error': read_error,
            'uncertain_overlap_turn_ids': uncertain_overlap,
            'unknown_timestamp_turn_ids': unknown_times, 'next_cursor': cursor,
            'read_only': True, 'writer_resumed': False}


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
