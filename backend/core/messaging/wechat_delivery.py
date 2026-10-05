"""Reply formatting and durable delivery, independent of task/model orchestration.

Every visible text segment is bounded, including its quote and numbering.
All references and attachments are validated before the first side effect.
Uncertain delivery is frozen for reconciliation, never retried by this layer.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.core.messaging.wechat_media_delivery import reply_attachments, send_image, send_file
from backend.core.messaging.wechat_reply_groups import reply_groups, quoted_group_text

DEFAULT_REPLY_LIMIT = 800


def split_wechat_text(text: str, limit: int = DEFAULT_REPLY_LIMIT) -> list[str]:
    """Lossless paragraph-aware splitting; length includes every visible character."""
    if type(limit) is not int or limit < 40:
        raise ValueError('reply length limit must be an integer >=40')
    parts = []
    while len(text) > limit:
        end = text.rfind('\n', 0, limit+1)
        if end < limit//2:
            end = limit
        parts.append(text[:end])
        text = text[end:]
    if text:
        parts.append(text)
    return parts


@dataclass
class ReplyPlan:
    groups: list[dict]
    parts: list[tuple[str, str]]
    question_seqs: set[int]


def prepare_reply(text: str, sources: list[dict], *, private: bool, limit: int = DEFAULT_REPLY_LIMIT) -> ReplyPlan:
    if type(limit) is not int or not 100 <= limit <= DEFAULT_REPLY_LIMIT:
        raise ValueError('reply_max_chars must be an integer 100..800')
    groups = reply_groups(text, sources) if private else [{'text':text, 'question_seqs':[]}]
    manifest, parts = [], []
    for group in groups:
        body, attachments = reply_attachments(group['text'])
        body = quoted_group_text(group, body)
        manifest.append({**{k:v for k,v in group.items() if k!='text'}, 'text':body})
        parts.extend(('text', part) for part in split_wechat_text(body, limit))
        parts.extend(attachments)
    return ReplyPlan(manifest, parts, {seq for group in groups for seq in group.get('question_seqs', [])})


def deliver_reply(store, hook: dict, through: int, text: str, plan: ReplyPlan, *,
                  sender=None, image_sender=None, file_sender=None, stop_event=None) -> str:
    """Return sent/uncertain/stale; partial groups retain every omitted question."""
    key = hook['key']
    identity = max(plan.question_seqs) if plan.question_seqs else through
    reply_id = store.reserve_reply(key, identity, text, reviewed_through=through)
    if reply_id is None:
        if store.pending_messages(key, through):
            return 'stale'
        raise RuntimeError('Reply already reserved; delivery must be reconciled')
    try:
        if sender is None:
            from pyxllib.autogui.weixin4_instrumentation import send_text
            sender = send_text
        store.set_meta(f'reply_groups:{reply_id}', plan.groups)
        receipts = []
        for index, (kind, part) in enumerate(plan.parts):
            store.set_meta(f'reply_parts:{reply_id}', {'sent':len(receipts), 'total':len(plan.parts), 'sending':index})
            if kind == 'text':
                result = sender(hook['chat_id'], part, sender_account_id=hook['account_id'])
            elif kind == 'image':
                result = (image_sender or send_image)(hook['chat_id'],part,sender_account_id=hook['account_id'],stop_event=stop_event)
            else:
                result = (file_sender or send_file)(hook['chat_id'],part,sender_account_id=hook['account_id'],stop_event=stop_event)
            receipts.append(result)
            store.set_meta(f'reply_parts:{reply_id}', {'sent':len(receipts), 'total':len(plan.parts), 'sending':None})
        store.finish_reply(reply_id, 'sent', receipts[0] if len(receipts)==1 else {'parts':receipts})
        return 'sent'
    except Exception as exc:
        store.finish_reply(reply_id, 'uncertain', {'error':str(exc)})
        return 'uncertain'
