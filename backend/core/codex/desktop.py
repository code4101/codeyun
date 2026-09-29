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
from pathlib import Path
from typing import Any

from backend.core.settings import ROOT_DIR, get_settings


class DesktopDispatchUncertain(RuntimeError):
    """The create request may have reached the desktop; never retry blindly."""


def desktop_binding_path() -> Path:
    return get_settings().data_dir / 'codex' / 'desktop.json'


def read_desktop_binding() -> dict:
    path = desktop_binding_path()
    if not path.is_file():
        raise RuntimeError('尚未绑定 Codex 桌面：请在桌面会话调用 bind_codex_desktop()')
    return json.loads(path.read_text(encoding='utf-8'))


async def _call(binding: dict, name: str, arguments: dict, timeout: float) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    environment = dict(os.environ)
    environment['CODEX_APP_TOOLS_PIPE_PATH'] = binding['pipe_path']
    params = StdioServerParameters(
        command=binding['node_path'], args=[binding['server_path']], env=environment,
    )
    async with asyncio.timeout(timeout):
        async with stdio_client(params) as (reader, writer):
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

    Intended for synchronous engineering workers. A timeout after creation is
    ambiguous; callers must not retry create_thread automatically.
    """
    return asyncio.run(_call(binding or read_desktop_binding(), name, arguments, timeout))


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


def read_desktop_repair(thread_id: str, *, goal_receipt: dict | None = None) -> dict:
    """Read desktop ownership and persisted native Goal; never resume a chat."""
    from backend.core.codex.app_server import read_codex_thread_goal
    payload = call_desktop_tool('read_thread', {'threadId': thread_id, 'hostId': 'local',
                                              'turnLimit': 1, 'maxOutputCharsPerItem': 1000})
    goal = read_codex_thread_goal(thread_id).get('goal') or {}
    receipt_goal = (goal_receipt or {}).get('goal') or {}
    if (not goal and receipt_goal.get('threadId') == thread_id
            and receipt_goal.get('status') in {'complete', 'paused', 'blocked'}):
        goal = receipt_goal
    turns = payload.get('turns') or []
    latest = turns[0] if turns else {}
    messages = [item.get('text') for item in latest.get('items', [])
                if item.get('type') == 'agentMessage' and item.get('text')]
    active = (payload.get('thread', {}).get('status') or {}).get('type') == 'active'
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
                last_message=messages[-1] if messages else None)
