"""所有微信处理器共用的消息接入规则，唤醒与业务权限互相独立。

default 保持历史优先发言人/跟进窗口行为；mentions 每次新请求要求 @；
all_members 允许任何成员直接发起。机器人自身消息始终不能唤醒。
"""
from __future__ import annotations

import re
import time


def is_hard(event: dict, hook: dict) -> bool:
    """原生 @ 元数据优先；文本兼容只匹配开头的完整 @ 名称。"""
    if event.get('sender_id') == hook['account_id']:
        return False
    if set(event.get('mentions') or []) & set(hook.get('mention_ids') or [hook['account_id']]):
        return True
    text = event.get('text', '')
    sender = event.get('sender_id', '')
    if sender and text.startswith(sender + ':\n'):
        text = text[len(sender) + 2:]
    return any(re.match(r'^@' + re.escape(alias) + r'(?:\s|[，,:：]|$)', text)
               for alias in hook.get('mention_aliases') or [])


def wake_messages(messages: list[dict], hook: dict) -> list[dict]:
    """返回能发起请求的消息；明确 @ 模式下所有者也必须 @。"""
    mode = hook.get('wake_mode', 'default')
    return [m for m in messages if m.get('sender_id') != hook['account_id'] and (
        hook.get('kind') == 'owner_private' or is_hard(m, hook) or mode == 'all_members'
        or (mode == 'default' and m.get('sender_id') in hook.get('priority_sender_ids', [])))]


def admit_messages(messages: list[dict], hook: dict, state: dict) -> bool:
    """新请求的通用门控；正在执行的请求如何接受补充由执行器负责。"""
    if not any(m.get('sender_id') != hook['account_id'] for m in messages):
        return False
    if wake_messages(messages, hook):
        return True
    if hook.get('wake_mode') == 'mentions':
        return False
    window = hook.get('followup_seconds', 0)
    return bool(state.get('thread_id')) and (window == 0 or state.get('awake_until', 0) >= time.time())
