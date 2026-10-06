"""Caller-driven CodeYun wait/read/ack/reply/receipt client, without dot RPC.

wait checks the authenticated public API every five seconds. It returns after
new messages, timeout, cancellation or an error. Repeat wait to continue; no
daemon, scheduler, model, OAuth setup or credentials are created by this CLI.
Existing CODEYUN_DEVICE_TOKEN is passed only in a header to the loopback API.
--login uses the normal CodeYun account login with hidden terminal password
input. Its user session is kept only in this process, never saved or printed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def wait_for_messages(reader, *, cursor=None, timeout_seconds=25, cancel=None, clock=time.monotonic, pause=None):
    """Bounded, resumable waiting; only a deliberate ack/reply consumes input."""
    cancel = cancel or threading.Event()
    pause = pause or cancel.wait
    deadline = clock() + timeout_seconds if timeout_seconds is not None else float('inf')
    while True:
        if cancel.is_set():
            return {'status': 'cancelled', 'cursor': cursor}
        try:
            result = reader(cursor)
        except KeyboardInterrupt:
            return {'status': 'cancelled', 'cursor': cursor}
        if cancel.is_set():
            return {'status': 'cancelled', 'cursor': cursor}
        if result.get('status') != 'idle':
            result.setdefault('cursor', cursor)
            return result
        cursor = result['cursor']
        remaining = deadline - clock()
        if remaining <= 0:
            return {'status': 'timeout', 'cursor': cursor}
        try:
            pause(min(5, remaining))
        except KeyboardInterrupt:
            return {'status': 'cancelled', 'cursor': cursor}


class CodeYunClient:
    def __init__(self, *, session=None, base_url='http://127.0.0.1:8000', token=None):
        from urllib.parse import urlsplit
        parts = urlsplit(base_url)
        if parts.scheme != 'http' or parts.hostname not in {'localhost', '127.0.0.1', '::1'} or parts.username or parts.password or parts.query or parts.fragment or parts.path not in {'', '/'}:
            raise ValueError('A credential-free loopback HTTP origin is required')
        import requests
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip('/')
        if token is None:
            from backend.core.settings import get_settings
            token = get_settings().device_token
        self.headers = {'Authorization': 'Bearer ' + token} if token else {}

    def login(self, username, password):
        """Normal public account login; keep the bounded session in memory only."""
        import requests
        try:
            response = self.session.request('POST', self.base_url + '/api/auth/login/json',
                json={'username':username,'password':password}, timeout=10, allow_redirects=False)
        except (requests.Timeout, requests.ConnectionError):
            return {'status':'disconnected','error':{'code':'login_connection_lost','retryable':True}}
        if response.is_redirect:
            return {'status':'error','error':{'code':'unexpected_redirect','retryable':False}}
        if response.status_code != 200:
            return {'status':'error','error':{'code':'login_rejected','retryable':False},'http_status':response.status_code}
        try:
            data=response.json()
        except ValueError:
            data={}
        if not isinstance(data,dict) or not isinstance(data.get('access_token'),str) or not data['access_token']:
            return {'status':'error','error':{'code':'invalid_login_response','retryable':False}}
        self.headers={'Authorization':'Bearer '+data['access_token']}
        return {'status':'authenticated'}

    def call(self, method, path, *, payload=None, params=None):
        import requests
        try:
            response = self.session.request(method, self.base_url + '/api/wechat-archive/agent/dot' + path,
                json=payload, params=params, headers=self.headers, timeout=10, allow_redirects=False)
        except (requests.Timeout, requests.ConnectionError):
            return {'status': 'disconnected', 'error': {'code': 'connection_lost', 'retryable': True},
                    'request_id': (payload or {}).get('request_id')}
        if response.status_code in {401, 403, 404}:
            return {'status': 'error', 'error': {'code': {401:'authentication_required', 403:'access_denied',
                404:'endpoint_unavailable'}[response.status_code], 'retryable': False}, 'http_status': response.status_code}
        if response.is_redirect:
            return {'status': 'error', 'error': {'code': 'unexpected_redirect', 'retryable': False}}
        try:
            data = response.json()
        except ValueError:
            return {'status': 'error', 'error': {'code': 'invalid_response', 'retryable': True}}
        if not isinstance(data, dict) or data.get('status') not in {
                'idle', 'new_messages', 'backlog', 'error', 'acknowledged', 'sending', 'sent', 'uncertain', 'not_sent'}:
            return {'status': 'error', 'error': {'code': 'invalid_response', 'retryable': True}}
        if response.status_code >= 400 and data.get('status') != 'error':
            return {'status': 'error', 'error': {'code': 'http_error', 'retryable': response.status_code >= 500 or response.status_code == 429},
                    'http_status': response.status_code}
        return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--login', action='store_true', help='Normal account login using a hidden password prompt in your local terminal; session stays in memory')
    parser.add_argument('--username', default='code4101')
    commands = parser.add_subparsers(dest='command', required=True)
    wait = commands.add_parser('wait', help='Bounded five-second reads; does not acknowledge or invoke a model')
    wait.add_argument('--cursor', default=None)
    wait.add_argument('--timeout-seconds', type=float, default=25)
    wait.add_argument('--until-message', action='store_true', help='Keep this process waiting until messages, cancellation, disconnect or error; no model calls')
    commands.add_parser('read').add_argument('--cursor', default=None)
    backlog = commands.add_parser('backlog', help='Inspect sent, in-flight and unresolved historical inputs without claiming them')
    backlog.add_argument('--cursor', default='future-community:0')
    commands.add_parser('acknowledge').add_argument('--input', type=Path, required=True, help='JSON source_seqs/cursor')
    commands.add_parser('reply').add_argument('--input', type=Path, required=True, help='JSON request_id/source_seqs/cursor/text')
    receipt = commands.add_parser('receipt')
    receipt.add_argument('--request-id', required=True)
    receipt.add_argument('--verify', action='store_true')
    session=commands.add_parser('session',help='Keep one logged-in client alive behind a current-user-only local command pipe')
    session.add_argument('--session-id',default='default')
    session_command=commands.add_parser('session-command',help='Send one whitelisted JSON command to the already logged-in local session; no credential needed')
    session_command.add_argument('--session-id',default='default')
    session_command.add_argument('--input',type=Path,required=True)
    args = parser.parse_args()
    if args.command == 'wait' and not 0 <= args.timeout_seconds <= 55:
        parser.error('timeout-seconds must be 0..55; repeat the command to continue')
    try:
        if args.command=='session-command':
            from scripts.wechat_dot_session import send_command
            result=send_command(json.loads(args.input.read_text(encoding='utf-8-sig')),session_id=args.session_id)
            print(json.dumps(result,ensure_ascii=False))
            return 2 if result['status'] in {'error','disconnected'} else 0
        client = CodeYunClient(base_url=args.base_url)
        if args.command=='session' and not args.login:
            print(json.dumps({'status':'error','error':{'code':'interactive_login_required','retryable':False}}))
            return 2
        if args.login:
            if not sys.stdin.isatty():
                print(json.dumps({'status':'error','error':{'code':'interactive_login_required','retryable':False}}))
                return 2
            from getpass import getpass
            password = getpass('CodeYun password (hidden): ')
            try:
                authenticated = client.login(args.username, password)
            finally:
                del password
            if authenticated['status'] != 'authenticated':
                print(json.dumps(authenticated))
                return 2
        if args.command=='session':
            from scripts.wechat_dot_session import serve
            serve(client,session_id=args.session_id,ready=lambda value:print(json.dumps(value),flush=True))
            return 0
        if args.command == 'backlog':
            result = client.call('GET', '/backlog', params={'cursor':args.cursor})
        elif args.command in {'wait', 'read'}:
            reader = lambda cursor: client.call('GET', '/messages', params={'cursor': cursor} if cursor is not None else {})
            result = wait_for_messages(reader, cursor=args.cursor, timeout_seconds=None if args.until_message else args.timeout_seconds) if args.command == 'wait' else reader(args.cursor)
        elif args.command == 'receipt':
            import re
            if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', args.request_id):
                parser.error('invalid request-id')
            result = client.call('GET', '/replies/' + args.request_id, params={'verify': args.verify})
        else:
            payload = json.loads(args.input.read_text(encoding='utf-8-sig'))
            result = client.call('POST', '/replies' if args.command == 'reply' else '/acknowledge', payload=payload)
    except KeyboardInterrupt:
        result = {'status': 'cancelled', 'cursor': getattr(args, 'cursor', None)}
    except (ValueError, OSError, EOFError):
        result = {'status': 'error', 'error': {'code': 'invalid_input', 'retryable': False}}
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result['status'] in {'error', 'disconnected'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
