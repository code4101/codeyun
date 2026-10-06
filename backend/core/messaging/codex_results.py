"""Thread-scoped result subscriptions; only the engineering worker sends.

Native hooks may submit completion payloads to record_codex_turn_result. The
worker also reads the provider's durable completion events so already-open
desktop threads need no restart or global notify replacement. Both paths share
the native (thread_id, turn_id) deduplication key and persisted outbox.
"""
from __future__ import annotations

import time
import re

from backend.core.codex.sessions import read_codex_completed_turns
from backend.core.messaging.wechat_agent_store import AgentStore
from backend.core.messaging.wechat_delivery import prepare_reply, deliver_reply


def result_store():
    from backend.core.messaging.wechat_agent import agent_root
    return AgentStore(agent_root() / 'events.sqlite')


def record_codex_stage_result(thread_id: str, stage_id: str, text: str, *, store=None):
    """Queue an explicitly completed business stage without waiting for Stop.

    The owning agent calls after reading the real completion receipt. stage_id
    identifies that receipt/attempt, not a poll or commentary. The existing
    subscription fixes the destination; retries never duplicate a stage.
    This records only: engineering retains all sending and readback ownership.
    """
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', stage_id):
        raise ValueError('Stable business stage ID required')
    if not text.strip():
        raise ValueError('Business stage result required')
    return record_codex_turn_result({'thread_id': thread_id,
        'turn_id': 'stage:' + stage_id, 'text': text}, store=store)


def subscribe_codex_results(thread_id: str, account_id: str, chat_id: str, *, enabled=True, include_latest=False,
                            allow_images=False):
    """Bind an existing owner's engineering group; never replay older history.

    Repeated enable calls preserve already queued/sent results. include_latest
    is an explicit initial synchronization, not a send probe or test message.
    """
    from backend.core.messaging.wechat_agent import load_config, OWNER_ACCOUNT
    hooks = [h for h in load_config().get('hooks', []) if h['account_id'] == account_id
             and h['chat_id'] == chat_id and h.get('kind', 'group') == 'group'
             and h.get('domain') == 'fanxiu' and OWNER_ACCOUNT in h.get('priority_sender_ids', [])]
    if len(hooks) != 1:
        raise ValueError('Explicit owner-enabled fanxiu destination required')
    turns = read_codex_completed_turns(thread_id)
    store = result_store()
    store.set_meta('codex_result_images:' + thread_id, bool(allow_images))
    store.subscribe_codex_thread(thread_id, hooks[0]['key'], turns[:-1] if include_latest else turns, enabled=enabled)
    if enabled and include_latest and turns:
        record_codex_turn_result(turns[-1], store=store)
    return store.codex_notifications_status()


def record_codex_turn_result(payload: dict, *, store=None):
    """Accept native Stop/notify/provider payloads; routing comes from subscription.

    A Stop event with stop_hook_active may be continued again. Ignore that
    intermediate Stop and let the durable task_complete event establish finality.
    """
    if payload.get('stop_hook_active'):
        return False
    thread = payload.get('thread_id') or payload.get('session_id') or payload.get('thread-id')
    turn = payload.get('turn_id') or payload.get('turn-id')
    text = payload.get('text') or payload.get('last_assistant_message') or payload.get('last-assistant-message')
    if not all(isinstance(v, str) and v.strip() for v in (thread, turn, text)):
        raise ValueError('Completion requires thread ID, native turn ID and final text')
    store = store or result_store()
    # Owner-authorized question screenshots live in one dedicated evidence
    # directory. General files and unrelated local images remain text only.
    text = text.replace('CODECLAW_FILE:', '文件路径：')
    if not store.get_meta('codex_result_images:' + thread, False):
        text = text.replace('CODECLAW_IMAGE:', '图片路径：')
    else:
        from backend.core.messaging.wechat_media_delivery import IMAGE_MARKER, image_path
        from backend.core.temp_paths import codeyun_temp_root
        allowed = codeyun_temp_root('fanxiu-question-evidence').resolve()
        for line in text.splitlines():
            match = IMAGE_MARKER.fullmatch(line)
            if match and not image_path(match[1]).is_relative_to(allowed):
                raise ValueError('咨询截图须放在 fanxiu-question-evidence 临时目录')
    return store.enqueue_codex_completion(thread, turn, '【凡修进度同步】\n' + text)


def sync_codex_results(service):
    """Engineering-only sweep: collect completions, refresh inbox, deliver receipts.

    It never resumes Codex or changes game execution. Failed discovery preserves
    pending notifications; failed/ambiguous delivery remains frozen for review.
    """
    store = service.store
    state = store.codex_notifications_status()
    for subscription in state['subscriptions']:
        if subscription['enabled']:
            for turn in read_codex_completed_turns(subscription['thread_id']):
                record_codex_turn_result(turn, store=store)
    hooks = {h['key']: h for h in service.config.get('hooks', [])}
    for notification in store.pending_codex_notifications():
        hook = hooks.get(notification['hook_key'])
        if hook is None or service.stop_event.is_set():
            continue
        service.poll_once()
        text = notification['text']
        plan = prepare_reply(text, [], private=False, limit=service.config.get('reply_max_chars', 800))
        deliver_reply(store, hook, 0, text, plan, notification_id=notification['id'],
                      sender=service.sender, image_sender=service.image_sender,
                      file_sender=service.file_sender, stop_event=service.stop_event)
    # Native API acceptance and archive echo are distinct evidence. Never resend
    # because the archive is delayed; retry only the read on later sweeps.
    for notification in store.codex_notifications_status()['notifications']:
        if notification['status'] != 'sent':
            continue
        marker = f"codex_result_verification:{notification['id']}"
        if (store.get_meta(marker) or {}).get('status') == 'verified':
            continue
        hook = hooks.get(notification['hook_key'])
        if hook is None:
            continue
        page = service.source(hook['account_id']).list_messages(hook['chat_id'], limit=100, include_resources=False)
        own = [row for row in page['items'] if row.get('sender_username') == hook['account_id']
               and row.get('create_time', 0) >= notification['created_at'] - 3]
        parts = prepare_reply(notification['text'], [], private=False,
                              limit=service.config.get('reply_max_chars', 800)).parts
        echoes = [next((row for row in own if part in row.get('message_text', '')), None)
                  for kind, part in parts if kind == 'text']
        verified = bool(echoes) and all(echoes)
        store.set_meta(marker, {'status': 'verified' if verified else 'awaiting_archive',
                               'checked_at': time.time(),
                               'local_ids': [row['local_id'] for row in echoes if row]})
    store.set_meta('codex_results_worker', {'checked_at': time.time(), 'error': None})
