"""Keep a single incident's native Goal alive across bounded `codex exec` turns.

The first exec creates a fresh thread. Only continuations of that same incident
resume its returned ID; there is no configured or globally shared thread ID.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from backend.core.codex.app_server import read_codex_thread_goal
from backend.core.services.launcher import popen_service


def _emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def run_goal_dispatch(spec_path: Path) -> int:
    """Run until native Goal completion or an observable non-runnable state.

    No synthetic goal state or database access: each turn is audited through
    thread/goal/get. A continuation with no tool work stops to avoid token-only
    narration loops. Paused/blocked/limited goals are never auto-resumed.
    """
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    initial_command = spec['command']
    prompt = Path(spec['prompt_path']).read_bytes()
    command = initial_command
    thread_id = None
    continuation = False
    while True:
        process = popen_service(command, cwd=spec['workspace_dir'], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=sys.stderr, close_fds=True)
        work_count = 0
        try:
            process.stdin.write(prompt)
            process.stdin.close()
            for line in iter(process.stdout.readline, b''):
                text = line.decode('utf-8', errors='replace')
                sys.stdout.write(text)
                sys.stdout.flush()
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if event.get('type') == 'thread.started':
                    observed_id = event.get('thread_id')
                    if thread_id and observed_id != thread_id:
                        raise RuntimeError('Goal continuation unexpectedly created another thread')
                    thread_id = observed_id
                item = event.get('item') or {}
                if event.get('type') == 'item.completed' and item.get('type') in {
                    'command_execution', 'file_change', 'mcp_tool_call', 'web_search',
                }:
                    work_count += 1
            exit_code = process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        if exit_code or not thread_id:
            raise RuntimeError(f'Codex turn failed: exit={exit_code}, thread={thread_id}')
        goal = read_codex_thread_goal(thread_id).get('goal') or {}
        _emit({'type': 'goal.checked', 'thread_id': thread_id, 'goal': goal})
        if goal.get('status') == 'complete':
            _emit({'type': 'goal.completed', 'thread_id': thread_id})
            return 0
        if goal.get('status') != 'active':
            raise RuntimeError(f'维修 Goal 未完成且不能续轮：{goal.get("status") or "未建立"}')
        if continuation and not work_count:
            raise RuntimeError('维修续轮没有工具行动，停止空转；Goal 保留未完成，需要检查阻塞')
        # Parent exec options remain before the resume subcommand (public CLI).
        command = initial_command[:-1] + ['resume', thread_id, '-']
        prompt = (
            '继续本会话已有的原生 Goal。上一轮结束不代表目标完成；读取 Goal 和已有证据，'
            '执行下一项实际调查、修复或验证。不要新建 Goal、会话或维修 Agent。'
            '全部业务验收与运行权交还完成后才标记 complete；真正阻塞按原生 Goal 规则处理。'
        ).encode('utf-8')
        continuation = True


def main() -> int:
    try:
        return run_goal_dispatch(Path(sys.argv[1]))
    except Exception as exc:
        _emit({'type': 'error', 'message': f'{type(exc).__name__}: {exc}'})
        return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
