"""Dispatch through the installed desktop MCP provider, never its private pipe API.

Bind once from an authorized desktop chat. The binding records that chat as the
request origin, not as the repair destination: every incident calls create_thread.
The desktop owns execution and Goal continuation; CodeYun only submits and reads.
An unavailable desktop is an error, never an implicit CLI fallback.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from backend.core.settings import ROOT_DIR, get_settings
from backend.core.temp_paths import codeyun_temp_root


class DesktopDispatchUncertain(RuntimeError):
    """The create request may have reached the desktop; never retry blindly."""


def read_desktop_transport_diagnostics(*, log_path: Path | None = None,
                                     max_bytes: int = 65536) -> dict:
    """Read bounded stderr pattern counts, never raw log text or credentials.

    This log belongs to CodeYun's desktop MCP provider subprocess, not the
    model's remote compact request. Its shared lines lack request/turn identity;
    pattern matches cannot attribute a historical model failure or prove repair.
    No provider is started, rebound or resumed by this query.
    """
    if type(max_bytes) is not int or not 1 <= max_bytes <= 65536:
        raise ValueError('Desktop diagnostic tail must be 1..65536 bytes')
    path = log_path if log_path is not None else codeyun_temp_root('codex-desktop', create=False) / 'provider.stderr.log'
    result = {'source': 'codeyun_desktop_mcp_provider_stderr', 'read_only': True,
              'request_correlation_available': False, 'remote_compact_root_cause': 'unknown',
              'recovery_performed': False, 'raw_log_exposed': False, 'max_bytes': max_bytes}
    try:
        with path.open('rb') as stream:
            size = stream.seek(0, 2)
            offset = max(0, size - max_bytes)
            stream.seek(offset)
            tail = stream.read(max_bytes)
    except FileNotFoundError:
        return {**result, 'status': 'missing', 'bytes_read': 0, 'pattern_counts': {}}
    if offset:
        # Ignore the first partial line, never classify a sliced log fragment.
        tail = tail.partition(b'\n')[2]
    text = tail.decode('utf-8', errors='replace')
    patterns = {'endpoint_missing': r'connect ENOENT',
                'timeout': r'TimeoutError|timed out|deadline exceeded',
                'remote_compact_message': r'Error running remote compact task',
                'response_decode_message': r'error decoding response body'}
    return {**result, 'status': 'available', 'file_bytes': size, 'bytes_read': len(tail),
            'tail_truncated': offset > 0, 'line_count': len(text.splitlines()),
            'pattern_counts': {name: len(re.findall(pattern, text, flags=re.IGNORECASE))
                               for name, pattern in patterns.items()}}


def desktop_transport_error(exc: BaseException) -> str:
    """Expose the first transport failure inside async task-group wrappers."""
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(desktop_transport_error(child) for child in exc.exceptions)
    message = f"{type(exc).__name__}: {exc}"
    if "connect ENOENT" in str(exc):
        message += "；Codex 桌面连接已失效，请从当前桌面会话执行 uv run python scripts/codex_desktop.py bind"
    return message


def desktop_binding_path() -> Path:
    return get_settings().data_dir / 'codex' / 'desktop.json'


def read_desktop_binding() -> dict:
    path = desktop_binding_path()
    if not path.is_file():
        raise RuntimeError('尚未绑定 Codex 桌面：请在桌面会话调用 bind_codex_desktop()')
    return resolve_desktop_provider_binding(json.loads(path.read_text(encoding='utf-8')))


def resolve_desktop_provider_binding(binding: dict) -> dict:
    """Resolve replaceable provider files without changing the authorized owner.

    Desktop updates replace versioned runtime directories. A binding retains
    the project, source chat and pipe authorization, but a deleted Node/provider
    installation must resolve to the current installed files on every call.
    Reads never rewrite the binding or create a repair chat.
    """
    resolved = dict(binding)
    node = Path(str(binding.get('node_path') or ''))
    if not node.is_file():
        candidates = []
        if current := os.environ.get('CODEX_MCP_NODE_PATH'):
            candidates.append(Path(current))
        if local := os.environ.get('LOCALAPPDATA'):
            runtime = Path(local) / 'OpenAI/Codex/runtimes/cua_node'
            candidates.extend(sorted(runtime.glob('*/bin/node.exe'),
                                     key=lambda p: p.stat().st_mtime, reverse=True))
        node = next((p for p in candidates if p.is_file()), None)
        if node is None:
            raise FileNotFoundError(f'Codex 桌面 Node runtime 已失效且无可用安装：{binding.get("node_path")}')
        resolved['node_path'] = str(node)
    server = Path(str(binding.get('server_path') or ''))
    if not server.is_file():
        home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
        providers = sorted((home / 'plugins/cache/openai-bundled/codex-app-tools').glob('*/server.mjs'),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        server = next((p for p in providers if p.is_file()), None)
        if server is None:
            raise FileNotFoundError(f'Codex 桌面 MCP provider 已失效且无可用安装：{binding.get("server_path")}')
        resolved['server_path'] = str(server)
    return resolved


async def _call(binding: dict, name: str, arguments: dict, timeout: float) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    binding = resolve_desktop_provider_binding(binding)
    environment = dict(os.environ)
    environment['CODEX_APP_TOOLS_PIPE_PATH'] = binding['pipe_path']
    params = StdioServerParameters(
        command=binding['node_path'], args=[binding['server_path']], env=environment,
    )
    # IPython replaces sys.stderr with an OutStream without fileno(). The
    # provider subprocess requires an OS file descriptor, even in a worker.
    log_path = codeyun_temp_root('codex-desktop') / 'provider.stderr.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as errlog:
        async with asyncio.timeout(timeout), stdio_client(params, errlog=errlog) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool(
                    name, arguments, meta={'openai/threadId': binding['source_thread_id']},
                )
                texts = [part.text for part in result.content if part.type == 'text']
                if result.is_error:
                    raise RuntimeError('\n'.join(texts))
                for value in texts:
                    try:
                        payload = json.loads(value)
                    except ValueError:
                        continue
                    if isinstance(payload, dict):
                        return payload
                raise RuntimeError(f'Codex desktop {name} 未返回结构化结果')


def call_desktop_tool(name: str, arguments: dict, *, binding: dict | None = None,
                      timeout: float = 45) -> dict:
    """Call the provider's public MCP contract, preserving caller attribution.

    Works in both synchronous workers and the Jupyter thread's active event
    loop. The MCP session owns a separate loop in that case; never nest loops.
    A timeout after creation is ambiguous and must not trigger another create.
    """
    resolved = binding or read_desktop_binding()

    def invoke() -> dict:
        try:
            return asyncio.run(_call(resolved, name, arguments, timeout))
        except BaseExceptionGroup as exc:
            raise RuntimeError(desktop_transport_error(exc)) from exc

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return invoke()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='codex-desktop') as worker:
        return worker.submit(invoke).result()


def bind_codex_desktop(*, workspace_dir: str | Path = ROOT_DIR) -> dict:
    """Verify and save the current desktop connection and matching saved project.

    Run from the desktop executor which supplies CODEX_THREAD_ID and the provider
    environment. Rebind after a desktop restart if its connection endpoint changes.
    No thread, model turn or game action is started by this operation.
    """
    names = ('CODEX_THREAD_ID', 'CODEX_APP_TOOLS_PIPE_PATH', 'CODEX_MCP_NODE_PATH')
    if any(not os.environ.get(name) for name in names):
        raise RuntimeError('绑定必须从 Codex 桌面会话执行，缺少桌面连接环境')
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    manifests = sorted((home / 'plugins/cache/openai-bundled/codex-app-tools').glob('*/.mcp.json'),
                       key=lambda path: path.stat().st_mtime, reverse=True)
    if not manifests:
        raise RuntimeError('未安装 codex-app-tools MCP provider')
    manifest = json.loads(manifests[0].read_text(encoding='utf-8'))
    server = manifest['mcpServers']['codex_app']
    server_path = (manifests[0].parent / server['args'][-1]).resolve()
    binding = dict(source_thread_id=os.environ['CODEX_THREAD_ID'],
                   pipe_path=os.environ['CODEX_APP_TOOLS_PIPE_PATH'],
                   node_path=os.environ['CODEX_MCP_NODE_PATH'], server_path=str(server_path))
    projects = call_desktop_tool('list_projects', {}, binding=binding)['projects']
    workspace = Path(workspace_dir).resolve()
    matches = [p for p in projects if p.get('hostId') == 'local'
               and p.get('path') and Path(p['path']).resolve() == workspace]
    if len(matches) != 1:
        raise RuntimeError(f'桌面必须存在唯一匹配的本地项目：{workspace}')
    binding.update(project_id=matches[0]['projectId'], project_label=matches[0]['label'],
                   workspace_dir=str(workspace))
    path = desktop_binding_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(binding, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return {'project_id': binding['project_id'], 'project_label': binding['project_label'],
            'workspace_dir': str(workspace), 'binding_path': str(path)}


def create_desktop_repair(*, prompt: str, title: str, workspace: Path,
                          model: str | None = None, reasoning_effort: str | None = None) -> dict:
    """Create a fresh project chat with desktop defaults unless explicitly overridden."""
    binding = read_desktop_binding()
    if Path(binding['workspace_dir']).resolve() != workspace.resolve():
        raise ValueError('维修工作区与已绑定的 Codex 桌面项目不匹配')
    arguments: dict[str, Any] = dict(
        prompt=prompt, title=title,
        target={'type': 'project', 'projectId': binding['project_id'],
                'environment': {'type': 'local'}},
    )
    if model:
        arguments['model'] = model
    if reasoning_effort:
        arguments['thinking'] = reasoning_effort
    # Read-only preflight distinguishes an absent desktop from an ambiguous
    # failure after the mutating request has been sent.
    call_desktop_tool('list_projects', {}, binding=binding)
    try:
        return call_desktop_tool('create_thread', arguments, binding=binding, timeout=90)
    except Exception as exc:
        raise DesktopDispatchUncertain(f'桌面投递结果不明，须核对桌面后再投递：{exc}') from exc


def create_desktop_project_chat(*, project_id: str, prompt: str, title: str) -> dict:
    """Create in a registered local desktop project, validating its public identity."""
    binding = read_desktop_binding()
    projects = call_desktop_tool('list_projects', {}, binding=binding)['projects']
    if not any(p.get('projectId') == project_id and p.get('hostId') == 'local' for p in projects):
        raise ValueError('项目不存在或不是本机桌面项目，请刷新项目列表')
    arguments = dict(prompt=prompt, title=title, target={
        'type': 'project', 'projectId': project_id, 'environment': {'type': 'local'},
    })
    try:
        return call_desktop_tool('create_thread', arguments, binding=binding, timeout=90)
    except Exception as exc:
        raise DesktopDispatchUncertain(f'桌面投递结果不明，须核对桌面后再投递：{exc}') from exc


def read_desktop_repair(thread_id: str, *, goal_receipt: dict | None = None) -> dict:
    """Read ownership and progress separately; never resume or release a chat.

    ``status`` retains the conservative dispatch gate. An old active receipt
    can hold that gate while ``progress_diagnostics`` reports a native blocked
    Goal and a completed turn. Assistant prose is never Goal evidence.
    """
    from backend.core.codex.app_server import read_codex_thread_goal
    payload = call_desktop_tool('read_thread', {'threadId': thread_id, 'hostId': 'local',
                                              'turnLimit': 1, 'maxOutputCharsPerItem': 1000})
    turns = payload.get('turns') or []
    latest = turns[0] if turns else {}
    thread = payload.get('thread') or {}
    state = (thread.get('status') or {}).get('type')
    # Not-loaded/system-error is unknown ownership, not an observed idle writer.
    # inspect_codex_dispatch propagates this failure; assistance retains its
    # current owner and suppresses replacement launches until explicit refresh.
    if state not in {'active', 'idle'}:
        raise RuntimeError(f'桌面维修写入权无法核验：state={state!r}，保留当前操作者')
    if thread.get('id') != thread_id:
        # Status and Goal receipts cannot establish ownership without the
        # requested thread's identity, including an otherwise valid idle state.
        raise RuntimeError('桌面维修线程身份缺失或不匹配，保留当前操作者')
    active = state == 'active'
    # A failed/interrupted turn cannot have completed the repair. Querying its
    # Goal adds no evidence and can wedge the dispatch gate for a thread that
    # never initialized persistent Goal state in the local app-server.
    if not active and latest.get('status') in {'failed', 'interrupted'}:
        return dict(status='failed', error=f"桌面维修未完成：turn={latest['status']}",
                    goal_status=None, last_message=None)
    receipt_goal = (goal_receipt or {}).get('goal') or {}
    # The native desktop tool receipt is tied to this dispatch and survives
    # desktop Goal clearing. A separate CLI app-server can use a different
    # Goal database; never require that database to corroborate this receipt.
    if (receipt_goal.get('threadId') == thread_id and receipt_goal.get('objective')
            and receipt_goal.get('status') in {'active', 'complete', 'paused', 'blocked'}):
        goal = receipt_goal
    elif active or latest.get('status') == 'inProgress':
        # Live desktop ownership alone closes the one-agent gate, including
        # the interval before its first create_goal/record-goal call.
        goal = {}
    else:
        from backend.core.codex.app_server import CodexAppServerError
        try:
            goal = read_codex_thread_goal(thread_id).get('goal') or {}
        except CodexAppServerError as exc:
            # The turn is already inactive. Missing Goal evidence proves no
            # completion, but must not wedge ownership of a dead repair chat.
            return dict(status='failed', error=f'无法核验维修 Goal：{exc}',
                        goal_status=None, last_message=None)
    messages = [item.get('text') for item in latest.get('items', [])
                if item.get('type') == 'agentMessage' and item.get('text')]
    diagnostics = dict(checked_at=time.time(), thread_state=state, thread_state_source='desktop.read_thread',
                       turn_id=latest.get('id'), turn_status=latest.get('status'),
                       receipt_goal_status=receipt_goal.get('status'),
                       native_goal_status=None, native_goal_source='unknown',
                       execution_state='executing' if latest.get('status') == 'inProgress' else 'unknown')
    if goal is receipt_goal and goal.get('status') == 'active' and latest.get('status') == 'completed':
        # A creation receipt is not a current progress receipt. Read native
        # state only for diagnosis: failure/mismatch must not open ownership.
        try:
            current = read_codex_thread_goal(thread_id).get('goal') or {}
            same_goal = (current.get('threadId') == thread_id
                         and current.get('objective') == goal.get('objective')
                         and current.get('status') in {'active', 'complete', 'paused', 'blocked'}
                         and isinstance(current.get('updatedAt'), (int, float))
                         and current['updatedAt'] >= (goal.get('updatedAt') or 0)
                         and (not goal.get('createdAt')
                              or current.get('createdAt') == goal['createdAt']))
            if same_goal:
                diagnostics.update(native_goal_status=current['status'],
                                   native_goal_source='app_server.thread/goal/get',
                                   native_goal_updated_at=current['updatedAt'],
                                   receipt_conflicts_with_native=current['status'] != goal['status'])
                if state == 'idle' and current['status'] in {'blocked', 'paused'}:
                    diagnostics['execution_state'] = 'waiting_blocked' if current['status'] == 'blocked' else 'waiting_paused'
                elif state == 'idle' and current['status'] == 'active':
                    diagnostics['execution_state'] = 'between_turns'
            else:
                diagnostics['native_goal_error'] = 'missing_or_unbound_current_goal'
        except Exception as exc:
            diagnostics['native_goal_error'] = f'{type(exc).__name__}: {exc}'
    complete = goal.get('status') == 'complete' and latest.get('status') == 'completed' and not active
    if complete:
        status, error = 'completed', None
    elif active or latest.get('status') == 'inProgress':
        status, error = 'running', None
    elif not turns:
        status, error = 'starting', None
    elif goal.get('status') == 'active' and latest.get('status') == 'completed':
        # The desktop owns native Goal continuation between turns.
        status, error = 'running', None
    else:
        status, error = 'failed', f'桌面维修未完成：Goal={goal.get("status") or "未建立"}，turn={latest.get("status")}'
    return dict(status=status, error=error, goal_status=goal.get('status'),
                last_message=messages[-1] if messages else None,
                progress_diagnostics=diagnostics)


def observe_desktop_threads(targets: list[dict], *, timeout_ms: int | None = None) -> dict:
    """Bounded public observation, never ownership release or writer activation.

    Default snapshot mode intentionally uses only ``read_thread`` when event
    waiting is unavailable. An explicit timeout (including zero) requests one
    ``wait_threads`` call; a failure gets at most one read per target, retaining
    the first error. Snapshot fallback has no event-wakeup semantics, and its
    read-page cursor must never replace or advance a wait ``afterCursor``.
    This API neither polls, resumes, launches, sends nor declares Goal complete.
    """
    if not isinstance(targets, list) or not 1 <= len(targets) <= 8:
        raise ValueError('Observation requires 1..8 targets')
    for target in targets:
        if (not isinstance(target, dict) or set(target) - {'threadId', 'hostId', 'afterCursor'}
                or not isinstance(target.get('threadId'), str) or not target['threadId'].strip()
                or any(not isinstance(target[key], str) or not target[key].strip()
                       for key in ('hostId', 'afterCursor') if key in target)):
            raise ValueError('Invalid public observation target')
    if timeout_ms is not None and (type(timeout_ms) is not int or not 0 <= timeout_ms <= 120000):
        raise ValueError('Wait timeout must be 0..120000 milliseconds')
    first_error = None
    if timeout_ms is not None:
        try:
            payload = call_desktop_tool('wait_threads', {'targets': targets, 'timeoutMs': timeout_ms},
                                        timeout=timeout_ms / 1000 + 25)
            return dict(source='desktop.wait_threads', mode='wait_snapshot' if timeout_ms == 0 else 'event_wait',
                        event_wakeup_supported=timeout_ms > 0, payload=payload,
                        ownership_release_authorized=False)
        except Exception as exc:
            first_error = desktop_transport_error(exc)
    observations = []
    for target in targets:
        arguments = {'threadId': target['threadId'], 'turnLimit': 1, 'maxOutputCharsPerItem': 1000}
        if 'hostId' in target:
            arguments['hostId'] = target['hostId']
        observation = dict(thread_id=target['threadId'], host_id=target.get('hostId'),
                           source='desktop.read_thread', checked_at=time.time(),
                           wait_cursor_advanced=False, status='unknown')
        try:
            payload = call_desktop_tool('read_thread', arguments, timeout=25)
            thread = payload.get('thread') or {}
            if thread.get('id') != target['threadId']:
                raise ValueError('Observation returned a different or missing thread identity')
            turns = payload.get('turns') or []
            latest = turns[0] if turns else None
            observation.update(status='observed', thread_state=thread.get('status'), latest_turn=latest)
        except Exception as exc:
            observation['error'] = desktop_transport_error(exc)
        observations.append(observation)
    return dict(source='desktop.read_thread', mode='snapshot_fallback' if first_error else 'snapshot',
                first_wait_error=first_error, observations=observations,
                wait_cursor_advanced=False, event_wakeup_supported=False,
                ownership_release_authorized=False)
