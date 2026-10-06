"""Use the owner's existing Your dot without installing a plugin.

Contract observed in desktop 26.930.3930: ``/tbo/primary`` selects the
existing dot; its messaging room accepts idempotent user requests. Credentials
stay on this computer and are sent only to the fixed ChatGPT origin. This
provider owns session lookup, request identity and response correlation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


class DotConnectionError(RuntimeError):
    """A bounded, credential-free error suitable for engineering status."""


class CurrentDotClient:
    """Read and message the account's selected dot; never create another dot."""

    def __init__(self, *, session=None, auth_path: Path | None = None):
        if session is None:
            from curl_cffi.requests import Session
            session = Session(impersonate='chrome')
        self.session = session
        self.auth_path = auth_path or Path(os.environ.get('CODEX_HOME', Path.home()/'.codex'))/'auth.json'

    def request(self, method: str, path: str, *, body=None, params=None):
        if not path.startswith(('/tbo/', '/messaging/rooms/')) or '..' in path or '?' in path:
            raise ValueError('Unsupported dot request path')
        try:
            tokens = json.loads(self.auth_path.read_text(encoding='utf-8'))['tokens']
            token, account = tokens['access_token'], tokens['account_id']
        except (OSError, ValueError, KeyError, TypeError):
            raise DotConnectionError('Existing desktop login is unavailable') from None
        try:
            response = self.session.request(method, 'https://chatgpt.com/backend-api'+path,
                headers={'Authorization':'Bearer '+token, 'ChatGPT-Account-Id':account,
                         'Accept':'application/json'}, json=body, params=params, timeout=20)
        except Exception:
            raise DotConnectionError('Dot request outcome is unknown; reconcile the same request id') from None
        if response.status_code != 200:
            raise DotConnectionError(f'Dot endpoint returned HTTP {response.status_code}')
        try:
            return response.json()
        except ValueError:
            raise DotConnectionError('Dot endpoint did not return JSON') from None

    def current(self) -> dict:
        result = self.request('GET', '/tbo/primary')
        selection = result.get('selection') or {}
        profile = result.get('profile') or {}
        if not selection.get('available') or not selection.get('messaging_room_id'):
            raise DotConnectionError('The existing selected dot is unavailable')
        room_id = selection['messaging_room_id']
        room = self.request('GET', f'/messaging/rooms/{room_id}')
        members = [m for m in room.get('members', []) if m.get('aeon_id') == selection['aeon_id']]
        if len(members) != 1:
            raise DotConnectionError('Selected dot messaging identity is ambiguous')
        return {'thread_id':selection['thread_id'], 'room_id':room_id,
                'dot_id':selection['aeon_id'], 'member_id':members[0]['account_user_id'],
                'paused':bool(profile.get('is_paused'))}

    @staticmethod
    def request_id(identity: str) -> str:
        return str(uuid5(NAMESPACE_URL, 'codeyun:future-community:'+identity))

    def messages(self, room_id: str) -> list[dict]:
        return self.request('GET', f'/messaging/rooms/{room_id}/messages', params={'limit':20})['items']

    def send(self, dot: dict, *, request_id: str, text: str) -> dict:
        """Reconcile first; retries preserve the server's idempotency token.

        An unknown result must be reconciled using this same request id, never
        turned into a new request. No WeChat operation occurs here.
        """
        for message in reversed(self.messages(dot['room_id'])):
            if (message.get('request_id') == request_id
                    and message.get('account_user_id') != dot['member_id']):
                return message
        return self.request('POST', f"/messaging/rooms/{dot['room_id']}/messages", body={
            'content':{'text':text, 'attachments':[]}, 'request_id':request_id,
            'idempotency_token':request_id})

    @staticmethod
    def message_text(message: dict) -> str:
        content = message.get('content') or {}
        if isinstance(content, dict) and isinstance(content.get('text'), str):
            return content['text']
        parts = []
        for raw in message.get('raw_messages') or []:
            if raw.get('role') not in (None, 'assistant'):
                continue
            value = raw.get('content') or {}
            if isinstance(value, dict):
                parts.extend(p for p in value.get('parts', []) if isinstance(p, str))
        return '\n'.join(parts)

    def reply(self, dot: dict, request_id: str) -> dict | None:
        """Only accept a dot response explicitly carrying this request marker."""
        for message in reversed(self.messages(dot['room_id'])):
            # Calpico uses role=user for both participants. The selected dot's
            # room membership, rather than role or message text, is authority.
            if message.get('account_user_id') != dot['member_id'] or message.get('deleted_at'):
                continue
            text = self.message_text(message)
            if request_id in text:
                return {'message_id':message['id'], 'text':text}
        return None
