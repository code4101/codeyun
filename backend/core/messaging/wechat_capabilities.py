"""Group-level capability limits that depend on which AI provider owns a WeChat group.

Claude-owned groups must not start the isolated Codex Computer Use executors
used for GUI attachment sending and GUI downloads. Plain text API replies and
readable cached attachments stay available. Other groups are unchanged.
"""
from __future__ import annotations

import os
import re

PROVIDER_ENV = 'CODEYUN_WECHAT_PROVIDER'  # set in the Claude child's environment
GUI_LIMIT_NOTE = '（本群当前不支持由工程发送或下载图片/文件附件，附件未发送；如需附件请由所有者处理或另行说明。）'
_MARKER = re.compile(r'^CODECLAW_(?:IMAGE|FILE):\s*(.+?)\s*$')


def codex_gui_allowed(hook: dict | None = None) -> bool:
    if hook is not None:
        return hook.get('processor') != 'claude'
    return os.environ.get(PROVIDER_ENV) != 'claude'


def require_codex_gui(what: str, *, allowed: bool | None = None) -> None:
    if not (codex_gui_allowed() if allowed is None else allowed):
        raise PermissionError(f'{what} requires a Codex Computer Use executor, which this Claude group does not use')


def drop_unsupported_attachments(hook: dict, text: str) -> tuple[str, bool]:
    """Pre-send capability check: remove attachment directives and say so in the reply itself."""
    if codex_gui_allowed(hook):
        return text, False
    lines, removed, fenced = [], False, False
    for line in text.splitlines():
        if line.lstrip().startswith('```'):
            fenced = not fenced
        if not fenced and _MARKER.fullmatch(line):
            removed = True
        else:
            lines.append(line)
    if not removed:
        return text, False
    body = '\n'.join(lines).strip()
    return (body + '\n' + GUI_LIMIT_NOTE).strip(), True
