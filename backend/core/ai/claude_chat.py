"""Bounded CC inference: images are temporary local files read by Claude's Read.

This adapter does not give inference calls repair/edit/game authority. Full
maintenance tasks use agents.run_agent or the durable incident boundary.
"""
import json
import tempfile
from pathlib import Path

from backend.core.agents.cli import run_agent
from backend.core.temp_paths import codeyun_temp_root


def chat(provider, *, messages, system_prompt, response_format, timeout_seconds):
    from backend.core.ai.chat import write_cli_image_files, OllamaClientError
    with tempfile.TemporaryDirectory(dir=codeyun_temp_root('cc-chat')) as directory:
        root = Path(directory)
        images = write_cli_image_files(messages, root)
        prompt = '\n'.join([
            system_prompt or '',
            '仅完成本次推理，不操作游戏、不修改文件、不启动其他 agent。图片只能用 Read 读取。',
            json.dumps(messages_without_images(messages), ensure_ascii=False),
            '本次图片路径（顺序与消息图片一致）：' + json.dumps([str(p) for p in images]),
            ('只输出符合以下结构的 JSON：' + json.dumps(response_format, ensure_ascii=False))
                if response_format else '只输出请求的结果。'])
        result = run_agent(resource='cc', prompt=prompt, directory=provider.workspace_dir or root,
            session_id=provider.session_id, session_provider='claude' if provider.session_id else '',
            timeout=max(1, int(timeout_seconds or provider.timeout_seconds)), allowed_tools=['Read'],
            disallowed_tools=['Bash', 'Edit', 'Write', 'NotebookEdit', 'Task', 'Skill', 'WebFetch', 'WebSearch',
                              'CronCreate', 'CronDelete', 'ScheduleWakeup'])
        if not result['ok']:
            raise OllamaClientError(f"CC 推理失败：{result['status']}；session={result.get('session_id', '')}")
        content = result['text'].strip()
        if response_format:
            if content.startswith('```') and content.endswith('```'):
                content = '\n'.join(content.splitlines()[1:-1]).strip()
            try:
                json.loads(content)
            except ValueError as exc:
                raise OllamaClientError('CC 未返回合法 JSON；不会改用其他模型') from exc
        return {'content': content, 'model': 'claude (CLI default)', 'session_id': result['session_id'],
                'provider': 'claude'}


def messages_without_images(messages):
    return [{key: value for key, value in message.items() if key != 'images'} for message in messages]
