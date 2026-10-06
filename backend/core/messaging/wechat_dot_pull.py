"""Caller-driven future-community inbox. No model, dot RPC or semantic routing.

Reads never acknowledge input. Mutations require the single live listener to
advertise dot_pull ownership after an explicit configuration/deployment handoff.
"""
from __future__ import annotations

import re

from backend.core.messaging.wechat_delivery import prepare_reply, deliver_reply

HOOK_KEY = 'future-community'
ACCOUNT_ID = 'wxid_gxgjjgft1oj722'
CHAT_ID = '44812733824@chatroom'


class PullError(ValueError):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code, self.retryable = code, retryable

    def response(self):
        return {'status': 'error', 'error': {'code': self.code, 'retryable': self.retryable}}


class WechatDotPull:
    """Public business interface; persistence and locks remain provider-owned."""
    def __init__(self, *, store=None, config=None, health=None, storage=None, sender=None, refresh=None):
        from backend.core.messaging.wechat_agent import agent_root, load_config, wechat_agent_status
        from backend.core.messaging.wechat_agent_store import AgentStore
        self.store = store or AgentStore(agent_root() / 'events.sqlite')
        self.config = config if config is not None else load_config()
        self.health = health or wechat_agent_status
        self.storage, self.sender = storage, sender
        self.refresh = refresh
        self.hook = next((h for h in self.config.get('hooks', []) if h.get('key') == HOOK_KEY), None)
        if not self.hook or self.hook.get('account_id') != ACCOUNT_ID or self.hook.get('chat_id') != CHAT_ID:
            raise PullError('scope_unavailable')

    @staticmethod
    def cursor(seq: int) -> str:
        return f'{HOOK_KEY}:{seq}'

    @staticmethod
    def position(cursor: str) -> int:
        if not isinstance(cursor, str) or not re.fullmatch(HOOK_KEY + r':\d{1,16}', cursor):
            raise PullError('invalid_cursor')
        return int(cursor.split(':')[1])

    def messages(self, *, cursor: str | None = None, limit: int = 100) -> dict:
        """Read persisted listener events, every lane, without accepting work."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PullError('invalid_limit')
        health = self.health()
        if not health.get('running'):
            raise PullError('listener_unavailable', retryable=True)
        import time
        listener = health.get('listener') or {}
        if time.time() - listener.get('last_poll_at', health.get('last_poll_at') or 0) > 75:
            raise PullError('listener_stale', retryable=True)
        after = self.position(cursor) if cursor is not None else None
        page = self.store.read_dot_messages(HOOK_KEY, after, limit=limit)
        return {'status': 'new_messages' if page['messages'] else 'idle',
                'hook_key': HOOK_KEY, 'account_id': ACCOUNT_ID, 'chat_id': CHAT_ID,
                'messages': page['messages'], 'cursor': self.cursor(page['through_seq']),
                'acknowledged_cursor': self.cursor(page['consumed_seq']), 'has_more': page['has_more'],
                'processor': self.hook.get('processor', 'codex')}

    def backlog(self, *, cursor: str = HOOK_KEY + ':0', limit: int = 100):
        """Read old writer outcomes before deciding whether any backlog is ours."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PullError('invalid_limit')
        page = self.store.read_dot_backlog(HOOK_KEY, self.position(cursor), limit=limit)
        page['writer']['running'] = bool(self.health().get('running'))
        try:
            self.require_writer()
            permitted = True
        except PullError:
            permitted = False
        return {'status': 'backlog', 'hook_key': HOOK_KEY, 'chat_id': CHAT_ID,
                'cursor': self.cursor(page.pop('through_seq')), 'reply_permitted': permitted, **page}

    def require_writer(self):
        if self.hook.get('processor') != 'dot_pull' or self.hook.get('message_routing'):
            raise PullError('ownership_not_transferred')
        if not self.health().get('running'):
            raise PullError('listener_unavailable', retryable=True)
        try:
            self.store.require_dot_pull_owner(HOOK_KEY)
        except ValueError as exc:
            raise PullError(str(exc)) from None

    def acknowledge(self, source_seqs: list[int], cursor: str):
        self.require_writer()
        try:
            return self.store.acknowledge_dot_messages(HOOK_KEY, source_seqs, self.position(cursor))
        except ValueError as exc:
            raise PullError(str(exc), retryable=str(exc) == 'stale_context') from None

    def reply(self, request_id: str, source_seqs: list[int], cursor: str, text: str):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', request_id):
            raise PullError('invalid_request_id')
        if not isinstance(text, str) or not text.strip() or len(text) > 16000 or 'CODECLAW_' in text:
            raise PullError('invalid_text')
        if (not isinstance(source_seqs, list) or not source_seqs or len(source_seqs) > 100
                or any(type(s) is not int or s < 1 for s in source_seqs) or len(set(source_seqs)) != len(source_seqs)):
            raise PullError('invalid_sources')
        through = self.position(cursor)
        body = 'CODECLAW_REPLY_TO: ' + ','.join(map(str, sorted(source_seqs))) + '\n' + text
        from filelock import FileLock
        with FileLock(str(self.store.path.with_name(self.store.path.name + '.' + HOOK_KEY + '.pull.lock')), timeout=15):
            return self._publish_reply(request_id, source_seqs, through, body)

    def _publish_reply(self, request_id, source_seqs, through, body):
        old = self.store.dot_reply_receipt(HOOK_KEY, request_id)
        if old:
            if old['text'] != body or set(old['source_seqs']) != set(source_seqs) or old['reviewed_through'] != through:
                raise PullError('request_conflict')
            return self.receipt(request_id)  # Including sending/uncertain; never blind resend.
        self.require_writer()
        if self.refresh is not None:
            self.refresh()
        else:
            from backend.core.messaging.wechat_agent import refresh_account_events
            if self.storage is None:
                from pyxllib.autogui.wechat_accounts import get_account_storage
                self.storage = get_account_storage(ACCOUNT_ID)
            refresh_account_events(self.store, ACCOUNT_ID, self.storage)
        sources = self.store.dot_pending_sources(HOOK_KEY, source_seqs)
        from backend.core.messaging.wechat_content import is_image_message
        if not sources or all(is_image_message(m) for m in sources):
            raise PullError('invalid_sources')
        try:
            plan = prepare_reply(body, sources, private=True, limit=self.config.get('reply_max_chars', 800))
        except ValueError:
            raise PullError('invalid_sources') from None
        if plan.question_seqs != set(source_seqs):
            raise PullError('invalid_sources')
        try:
            deliver_reply(self.store, self.hook, through, body, plan, sender=self.sender, dot_request_id=request_id)
        except ValueError as exc:
            raise PullError(str(exc), retryable=str(exc) == 'stale_context') from None
        return self.receipt(request_id)

    def receipt(self, request_id: str, *, verify: bool = False):
        """sent is native acceptance; verified_sender_archive is actual self echo.

        It does not claim a recipient read acknowledgement. Uncertain results
        remain frozen until the existing explicit reconciliation API is used.
        """
        row = self.store.dot_reply_receipt(HOOK_KEY, request_id)
        if not row:
            raise PullError('reply_not_found')
        verification = self.store.get_meta('dot_pull_verification:' + str(row['id']), {'status': 'unverified'})
        if verify and row['status'] == 'sent' and verification['status'] != 'verified_sender_archive':
            try:
                if self.storage is None:
                    from pyxllib.autogui.wechat_accounts import get_account_storage
                    self.storage = get_account_storage(ACCOUNT_ID)
                page = self.storage.list_messages(CHAT_ID, limit=100, order='desc', include_resources=False)
                groups = self.store.get_meta('reply_groups:' + str(row['id']), [])
                from backend.core.messaging.wechat_delivery import split_wechat_text
                expected = [part for g in groups for part in split_wechat_text(g['text'], self.config.get('reply_max_chars', 800))]
                observed = []
                for m in page['items']:
                    if (m.get('sender_username') == ACCOUNT_ID and m.get('create_time', 0) >= int(row['created_at'])
                            and m.get('server_id')):
                        observed.append((m.get('message_text', ''), str(m['server_id'])))
                matched = []
                for part in expected:
                    match = next((m for m in observed if m[0] == part and m[1] not in matched), None)
                    if not match:
                        break
                    matched.append(match[1])
                if expected and len(matched) == len(expected):
                    verification = {'status': 'verified_sender_archive', 'server_ids': matched}
                    self.store.set_meta('dot_pull_verification:' + str(row['id']), verification)
            except Exception:
                verification = {'status': 'unavailable', 'retryable': True}
        return {'status': row['status'], 'request_id': request_id, 'reply_id': row['id'],
                'source_seqs': row['source_seqs'], 'cursor': self.cursor(row['reviewed_through']),
                'verification': verification}
