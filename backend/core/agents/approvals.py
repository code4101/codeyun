"""Opt-in native human approval for local CC incidents (Agent SDK ``can_use_tool``).

Only requests created with ``approval='human'`` use this path; the default
``cli_policy`` incident keeps its headless ``claude -p`` transport. Claude
Code's own permission flow still runs first (hooks, settings deny/ask rules,
permission mode, allow rules); only a call it would otherwise prompt for reaches
the callback. Each such call becomes one durable request bound to dispatch,
generation, session, worker/CLI process identity, ``tool_use_id`` and input
digest. Exactly one decision is accepted. Unanswered, mismatched, duplicated,
cancelled or orphaned requests are denied. An allow runs the original input
once; it never returns modified input or persistent permission updates.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
import warnings
from dataclasses import asdict, is_dataclass
from pathlib import Path
from uuid import uuid4

import psutil
from filelock import FileLock

from backend.core.agents import background as bg

DECISIONS = ('allow', 'deny')
POLL_SECONDS = .5


def resolve_native_claude() -> str:
    """The same installed CLI as ``claude -p``; never the SDK-bundled copy or a .cmd shim."""
    found = shutil.which('claude')
    if not found:
        raise FileNotFoundError('claude')
    path = Path(found)
    if os.name != 'nt' or path.suffix.lower() == '.exe':
        return str(path.resolve())
    native = path.parent / 'node_modules' / '@anthropic-ai' / 'claude-code' / 'bin' / 'claude.exe'
    if not native.is_file():
        raise FileNotFoundError('Native claude.exe behind the npm shim was not found')
    return str(native.resolve())


def input_digest(tool_input: dict) -> str:
    text = json.dumps(tool_input, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _approvals(dispatch_id: str) -> Path:
    path = bg.incident_root(dispatch_id) / 'approvals'
    path.mkdir(exist_ok=True)
    return path


def _request_path(dispatch_id: str, request_id: str) -> Path:
    if len(request_id) != 32 or any(c not in '0123456789abcdef' for c in request_id):
        raise ValueError('Invalid permission request identity')
    return _approvals(dispatch_id) / f'{request_id}.json'


def _decision_path(dispatch_id: str, request_id: str) -> Path:
    return _request_path(dispatch_id, request_id).with_name(f'{request_id}.decision.json')


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def list_permission_requests(dispatch_id: str, *, pending_only: bool = True) -> list[dict]:
    """Requests with a projected ``state``; a pending request whose owner is gone reads ``stale``."""
    record = bg.read_record(dispatch_id)
    rows = []
    for path in _approvals(dispatch_id).glob('*.json'):
        if path.name.endswith('.decision.json') or path.name.endswith('.tmp'):
            continue
        row = _read(path)
        if row['state'] == 'pending' and (
                row['generation'] != record['generation'] or time.time() >= row['expires_at']
                or row['worker'] != record.get('worker') or row['child'] != record.get('child')
                or not bg.process_alive(row['worker']) or not bg.process_alive(row['child'])):
            row['state'] = 'stale'
        if not pending_only or row['state'] == 'pending':
            rows.append(row)
    return sorted(rows, key=lambda x: x['created_at'])


def decide_permission_request(dispatch_id: str, request_id: str, *, generation: int, tool_use_id: str,
                              input_sha256: str, decision: str, decided_by: str, message: str = '',
                              interrupt: bool = False) -> dict:
    """Record one bound decision for exactly this pending tool call.

    An allow must relay a genuine person's decision. A coordinator may deny
    a request that exceeds its authorization, naming itself in ``decided_by``;
    a policy denial never impersonates a human or grants permission.

    Every binding must match the live request: generation, tool_use_id, input
    digest, unexpired, and the same live worker and CLI process. The decision
    file is created exclusively, so a duplicate or racing second answer fails.
    """
    if decision not in DECISIONS:
        raise ValueError('decision must be allow or deny')
    if not str(decided_by).strip():
        raise ValueError('decided_by must name the person deciding')
    root = bg.incident_root(dispatch_id)
    path = _request_path(dispatch_id, request_id)
    with FileLock(str(root / 'record.lock'), timeout=10):
        record = bg.read_record(dispatch_id)
        if not path.is_file():
            raise ValueError('Unknown permission request')
        row = _read(path)
        if row['dispatch_id'] != dispatch_id or row['session_id'] != record['session_id']:
            raise RuntimeError('Request belongs to another dispatch or session')
        if generation != row['generation'] or generation != record['generation']:
            raise RuntimeError('Generation changed; this request is no longer answerable')
        if tool_use_id != row['tool_use_id'] or input_sha256 != row['input_sha256']:
            raise RuntimeError('Decision does not match the requested tool call')
        if row['state'] != 'pending' or record.get('state') != 'running':
            raise RuntimeError(f'Request is {row["state"]}; only a pending request can be decided')
        if time.time() >= row['expires_at']:
            raise RuntimeError('Request expired')
        if (row['worker'] != record.get('worker') or row['child'] != record.get('child')
                or not bg.process_alive(row['worker']) or not bg.process_alive(row['child'])):
            raise RuntimeError('Requesting worker/CLI is no longer the live owner')
        value = dict(request_id=request_id, dispatch_id=dispatch_id, generation=generation,
                     tool_use_id=tool_use_id, input_sha256=input_sha256, decision=decision,
                     decided_by=str(decided_by).strip(), message=str(message), interrupt=bool(interrupt),
                     decided_at=time.time())
        try:
            handle = os.open(_decision_path(dispatch_id, request_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError('Request was already decided') from None
        with os.fdopen(handle, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
    return value


class ApprovalBroker:
    """Worker-side ``can_use_tool``: one durable request per call, waits for a bound decision."""

    def __init__(self, dispatch_id: str, record: dict, update, *, poll_seconds: float = POLL_SECONDS):
        self.dispatch_id = dispatch_id
        self.record = record
        self.update = update
        self.poll_seconds = poll_seconds
        self.timeout = int(record['request'].get('approval_timeout_seconds', 1800))
        self.seen: set[str] = set()

    def _pending(self, request_id: str, add: bool) -> None:
        with FileLock(str(bg.incident_root(self.dispatch_id) / 'record.lock'), timeout=10):
            r = bg.read_record(self.dispatch_id)
            pending = [x for x in r.get('pending_approvals', []) if x != request_id]
            r['pending_approvals'] = pending + [request_id] if add else pending
            r['updated_at'] = time.time()
            bg.write_record(bg.incident_root(self.dispatch_id) / 'record.json', r)

    def _finish(self, request_id: str, state: str) -> dict:
        """Close a pending request; a decision filed before this lock is honoured."""
        path = _request_path(self.dispatch_id, request_id)
        with FileLock(str(bg.incident_root(self.dispatch_id) / 'record.lock'), timeout=10):
            row = _read(path)
            if row['state'] != 'pending':
                return row
            decision = _decision_path(self.dispatch_id, request_id)
            if decision.is_file():
                state = 'allowed' if _read(decision)['decision'] == 'allow' else 'denied'
            row.update(state=state, closed_at=time.time())
            bg.write_record(path, row)
            r = bg.read_record(self.dispatch_id)
            r['pending_approvals'] = [x for x in r.get('pending_approvals', []) if x != request_id]
            r['updated_at'] = time.time()
            bg.write_record(bg.incident_root(self.dispatch_id) / 'record.json', r)
        return row

    async def can_use_tool(self, tool_name: str, tool_input: dict, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        tool_use_id = str(getattr(context, 'tool_use_id', '') or '')
        if not tool_use_id or tool_use_id in self.seen:
            return PermissionResultDeny(message='Permission request without a unique tool_use_id was refused')
        self.seen.add(tool_use_id)
        r = bg.read_record(self.dispatch_id)
        request_id = uuid4().hex
        now = time.time()
        row = dict(request_id=request_id, dispatch_id=self.dispatch_id, generation=r['generation'],
                   session_id=r['session_id'], worker=r.get('worker'), child=r.get('child'),
                   tool_name=tool_name, tool_use_id=tool_use_id, input=tool_input,
                   input_sha256=input_digest(tool_input), agent_id=getattr(context, 'agent_id', None),
                   title=getattr(context, 'title', None), description=getattr(context, 'description', None),
                   decision_reason=getattr(context, 'decision_reason', None),
                   blocked_path=getattr(context, 'blocked_path', None),
                   created_at=now, expires_at=now + self.timeout, state='pending')
        bg.write_record(_request_path(self.dispatch_id, request_id), row)
        self._pending(request_id, True)
        decision_path = _decision_path(self.dispatch_id, request_id)
        try:
            while not decision_path.is_file() and time.time() < row['expires_at']:
                await asyncio.sleep(self.poll_seconds)
        except BaseException:  # cancellation/shutdown: the tool call does not run
            self._finish(request_id, 'cancelled')
            raise
        closed = self._finish(request_id, 'expired')
        if closed['state'] not in {'allowed', 'denied'}:
            return PermissionResultDeny(message='No human decision before the approval deadline; denied')
        decision = _read(decision_path)
        if (decision.get('request_id'), decision.get('generation'), decision.get('tool_use_id'),
                decision.get('input_sha256')) != (request_id, row['generation'], tool_use_id, row['input_sha256']):
            return PermissionResultDeny(message='Decision binding mismatch; denied')
        if decision['decision'] == 'allow':
            return PermissionResultAllow(updated_input=tool_input)
        return PermissionResultDeny(message=decision.get('message') or 'Denied by human reviewer',
                                    interrupt=bool(decision.get('interrupt')))

    def abandon_pending(self) -> None:
        for path in _approvals(self.dispatch_id).glob('*.json'):
            if path.name.endswith('.decision.json') or path.name.endswith('.tmp'):
                continue
            row = _read(path)
            if row['state'] == 'pending' and row['generation'] == self.record['generation']:
                self._finish(row['request_id'], 'abandoned')


def find_cli_process(cli_path: str, *, attempts: int = 50) -> psutil.Process:
    """The SDK's CLI child, identified by executable path under this worker."""
    target = os.path.normcase(str(Path(cli_path).resolve()))
    for _ in range(attempts):
        for child in psutil.Process().children(recursive=True):
            try:
                if os.path.normcase(child.exe()) == target:
                    return child
            except psutil.Error:
                continue
        time.sleep(.1)
    raise RuntimeError('Claude CLI process identity was not observable; refusing unowned writer')


def _event(message) -> dict:
    body = asdict(message) if is_dataclass(message) else {'value': message}
    return {'type': type(message).__name__, **body}


async def _session(dispatch_id: str, record: dict, update, broker: ApprovalBroker) -> dict:
    from claude_agent_sdk import CanUseToolShadowedWarning, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, SystemMessage
    cli = resolve_native_claude()
    session = record['session_id']
    result: dict | None = None
    with Path(record['stdout_path']).open('ab', buffering=0) as output, \
            Path(record['stderr_path']).open('ab', buffering=0) as error:
        # Same rule surface as the headless path; settings deny/ask rules load
        # from the same sources; permission mode is inherited, not passed.
        options = ClaudeAgentOptions(
            cli_path=cli, cwd=record['workspace_dir'], allowed_tools=list(bg.TOOLS),
            disallowed_tools=[*bg.DISALLOWED_TOOLS, 'AskUserQuestion'],
            setting_sources=['user', 'project', 'local'],
            system_prompt={'type': 'preset', 'preset': 'claude_code'},
            can_use_tool=broker.can_use_tool,
            stderr=lambda line: error.write((line.rstrip('\n') + '\n').encode('utf-8', 'replace')),
            **({'resume': session} if record.get('resume') else {'session_id': session}))
        required = record['request'].get('approval_required_tools') or ()
        if required:
            # Official permission order evaluates deny, then ask, then mode/allow.
            # An explicit ask keeps broad existing allow rules from suppressing
            # the human callback. It grants nothing and leaves native deny rules
            # and the inherited permission mode in place.
            options.settings = json.dumps({'permissions': {'ask': list(required)}})
        client = ClaudeSDKClient(options)
        child = None
        try:
            with warnings.catch_warnings():
                # The existing allow list is intentionally unchanged; only calls it
                # does not cover (and settings ask rules) need a person.
                warnings.simplefilter('ignore', CanUseToolShadowedWarning)
                await client.connect()
            child = bg.process_identity(find_cli_process(cli).pid)
            broker.record = update(child=child, transport='sdk_human_approval')
            await client.query(Path(record['prompt_path']).read_text(encoding='utf-8'), session_id=session)
            async for message in client.receive_response():
                output.write((json.dumps(_event(message), ensure_ascii=False, default=str) + '\n').encode('utf-8'))
                if isinstance(message, SystemMessage) and message.subtype == 'init':
                    if message.data.get('session_id') != session:
                        raise RuntimeError('Claude returned another session identity')
                    update(session_created=True, permission_mode=message.data.get('permissionMode'))
                if isinstance(message, ResultMessage):
                    if message.session_id != session:
                        raise RuntimeError('Claude returned another session identity')
                    result = {'result': message.result, 'is_error': message.is_error,
                              'permission_denials': message.permission_denials}
                update(last_event_type=type(message).__name__)
        finally:
            broker.abandon_pending()
            try:
                await client.disconnect()
            finally:
                if child and bg.process_alive(child):
                    psutil.Process(child['pid']).kill()
    return result or {}


def run_human_session(dispatch_id: str, record: dict, update) -> dict:
    """Drive one generation; raises TimeoutError at the incident's execution deadline."""
    broker = ApprovalBroker(dispatch_id, record, update)

    async def bounded():
        try:
            async with asyncio.timeout(record['request'].get('execution_timeout_seconds', 3600)):
                return await _session(dispatch_id, record, update, broker)
        except TimeoutError:
            raise TimeoutError('Agent execution timed out; preserve effects and explicitly resume original session') from None
    return asyncio.run(bounded())


def main(argv=None) -> int:
    """Reviewer CLI: ``list`` shows pending calls; ``decide`` needs an interactive confirmation."""
    parser = argparse.ArgumentParser(prog='python -m backend.core.agents.approvals')
    sub = parser.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('list')
    listing.add_argument('dispatch_id')
    listing.add_argument('--all', action='store_true')
    decide = sub.add_parser('decide')
    decide.add_argument('dispatch_id')
    decide.add_argument('request_id')
    decide.add_argument('decision', choices=DECISIONS)
    decide.add_argument('--by', required=True, help='the person making this decision')
    decide.add_argument('--message', default='')
    decide.add_argument('--interrupt', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'list':
        print(json.dumps(list_permission_requests(args.dispatch_id, pending_only=not args.all),
                         ensure_ascii=False, indent=2))
        return 0
    row = next((x for x in list_permission_requests(args.dispatch_id) if x['request_id'] == args.request_id), None)
    if row is None:
        print('No pending request with that identity', file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print('decide requires an interactive terminal for the human confirmation', file=sys.stderr)
        return 2
    print(json.dumps({k: row[k] for k in ('tool_name', 'title', 'description', 'decision_reason',
                                          'blocked_path', 'input', 'generation', 'tool_use_id')},
                     ensure_ascii=False, indent=2))
    if input(f'Type {args.decision} to confirm: ').strip() != args.decision:
        print('Not confirmed; nothing recorded', file=sys.stderr)
        return 1
    print(json.dumps(decide_permission_request(
        args.dispatch_id, args.request_id, generation=row['generation'], tool_use_id=row['tool_use_id'],
        input_sha256=row['input_sha256'], decision=args.decision, decided_by=args.by,
        message=args.message, interrupt=args.interrupt), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
