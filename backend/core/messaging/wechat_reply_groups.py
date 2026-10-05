"""Natural-language reply groups linked to verified inbox events.

CODECLAW_REPLY_TO is a host directive like CODECLAW_IMAGE, never user-facing.
The current text transport renders an explicit textual quote; it does not
claim to create a WeChat native reference card. Validate all groups before send.
"""
from __future__ import annotations

import re

MARKER = re.compile(r'^CODECLAW_REPLY_TO:\s*([0-9]+(?:\s*,\s*[0-9]+)*)\s*$')


def reply_groups(text: str, messages: list[dict]) -> list[dict]:
    """Partition candidate output, rejecting foreign, repeated or absent anchors.

    messages must be the provider's authenticated, still-pending inbox events,
    not model-supplied message metadata. Existing ungrouped output is unchanged.
    Directives inside code fences remain ordinary quoted code.
    """
    groups, lines, seqs = [], [], []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith('```'):
            fenced = not fenced
        match = MARKER.fullmatch(line) if not fenced else None
        if not fenced and line.startswith('CODECLAW_REPLY_TO:') and not match:
            raise ValueError('回复分组须关联有效的消息 seq')
        if match:
            if lines or seqs:
                body='\n'.join(lines).strip()
                if body and not seqs:
                    raise ValueError('分组回复的首段也须关联原消息')
                if seqs:
                    groups.append({'question_seqs': seqs, 'text': body})
            seqs=[int(value.strip()) for value in match[1].split(',')]
            lines=[]
        else:
            lines.append(line)
    body='\n'.join(lines).strip()
    if not seqs and not groups:
        return [{'question_seqs': [], 'text': text.strip()}]
    groups.append({'question_seqs': seqs, 'text': body})
    known={int(message['seq']): message for message in messages}
    seen=set()
    for group in groups:
        targets=group['question_seqs']
        if not group['text']:
            raise ValueError('分组回复不能为空')
        if len(set(targets))!=len(targets) or seen.intersection(targets):
            raise ValueError('同一原消息不能重复归入多个回复组')
        if not set(targets)<=known.keys():
            raise ValueError('回复分组引用了未核验或已完成的消息')
        seen.update(targets)
        anchor=known[targets[0]]
        content=str(anchor.get('text') or '').strip()
        sender=str(anchor.get('sender_id') or '')
        if sender and content.startswith(sender+':\n'):
            content=content[len(sender)+2:]
        if anchor.get('message_type') in (3, '3'):
            content='[图片]'
        elif anchor.get('message_type') in (34, '34'):
            content='[语音]'
        elif content.startswith('<?xml') or content.startswith('<msg'):
            content='[附件或引用消息]'
        content=' '.join(content.split())
        if len(content)>100:
            content=content[:100]+'…'
        group.update(anchor_seq=targets[0], anchor_local_id=anchor.get('local_id'),
                     quote_mode='text', quote_text=content or '[原消息]')
    return groups


def quoted_group_text(group: dict, body: str) -> str:
    """A visible source excerpt rather than an invented native reference card."""
    return f"回复「{group['quote_text']}」\n\n{body}" if group.get('quote_text') else body
