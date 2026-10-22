"""Opt-in human approval: exact request/decision binding, fail-closed lifecycle, unchanged default."""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from backend.core.agents import approvals as ap
from backend.core.agents import background as bg
from backend.core.agents.contracts import AgentTaskRequest


def make_request(**changes):
    return AgentTaskRequest('approval probe', 'verify approval', 'read receipt', pair='cc', mode='smoke',
                            completion_criteria=('exact proof',), **changes)


def test_legacy_short_timeout_does_not_require_human_approval_deadline():
    # Existing CLI incidents can finish before the unused default human deadline.
    request = make_request(execution_timeout_seconds=60)
    assert 'approval probe' in bg.build_incident_prompt(request)
    with pytest.raises(ValueError, match='Approval timeout'):
        bg.build_incident_prompt(make_request(approval='human', execution_timeout_seconds=60))


def test_required_approval_tools_only_add_native_ask_rules():
    with pytest.raises(ValueError, match='approval=human'):
        bg.build_incident_prompt(make_request(approval_required_tools=('Bash',)))
    with pytest.raises(ValueError, match='supported built-in'):
        bg.build_incident_prompt(make_request(approval='human', approval_required_tools=('Bash(*)',)))
    assert bg.build_incident_prompt(make_request(approval='human', approval_required_tools=('Bash',)))


@pytest.fixture
def child():
    """A real live process standing in for the CLI, so identity checks are not mocked."""
    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        yield bg.process_identity(p.pid)
    finally:
        p.kill()
        p.wait(timeout=10)


@pytest.fixture
def incident(tmp_path, monkeypatch, child):
    def root(identity, *, create=False):
        path = tmp_path / identity
        if create:
            path.mkdir(exist_ok=True)
        return path
    monkeypatch.setattr(bg, 'incident_root', root)
    monkeypatch.setattr(bg, 'incident_logs', lambda identity: root(identity, create=True))
    monkeypatch.setattr(bg, 'launch_worker', lambda identity: bg.read_record(identity))
    record = bg.dispatch_claude(make_request(approval='human', approval_timeout_seconds=30), workspace_dir=tmp_path)
    record.update(state='running', worker=bg.process_identity(os.getpid()), child=child)
    bg.write_record(root(record['dispatch_id']) / 'record.json', record)
    return record, root(record['dispatch_id'])


def update_for(dispatch_id):
    def update(**changes):
        r = bg.read_record(dispatch_id)
        r.update(changes, updated_at=time.time())
        bg.write_record(bg.incident_root(dispatch_id) / 'record.json', r)
        return r
    return update


def ask(record, tool_input, *, tool_use_id='toolu_1', timeout=None, answer=None):
    """Run the broker callback; ``answer(row)`` is called from a reviewer thread once the request is pending."""
    broker = ap.ApprovalBroker(record['dispatch_id'], record, update_for(record['dispatch_id']), poll_seconds=.02)
    if timeout is not None:
        broker.timeout = timeout
    outcome = {}

    def reviewer():
        for _ in range(500):
            rows = ap.list_permission_requests(record['dispatch_id'])
            if rows:
                try:
                    outcome['decision'] = answer(rows[0])
                except Exception as exc:  # surfaced to the test
                    outcome['error'] = exc
                return
            time.sleep(.01)
    thread = threading.Thread(target=reviewer) if answer else None
    if thread:
        thread.start()
    result = asyncio.run(broker.can_use_tool('Bash', tool_input, SimpleNamespace(tool_use_id=tool_use_id)))
    if thread:
        thread.join(10)
    return result, outcome, broker


def decide(record, row, decision='allow', **changes):
    values = dict(generation=row['generation'], tool_use_id=row['tool_use_id'], input_sha256=row['input_sha256'],
                  decision=decision, decided_by='owner at terminal')
    values.update(changes)
    return ap.decide_permission_request(record['dispatch_id'], row['request_id'], **values)


def only_request(record):
    rows = ap.list_permission_requests(record['dispatch_id'], pending_only=False)
    assert len(rows) == 1
    return rows[0]


def test_default_request_keeps_headless_cli_command(monkeypatch):
    from backend.core.agents import cli
    assert AgentTaskRequest('t', 'p', 'o').approval == 'cli_policy'
    monkeypatch.setattr(cli, 'cli_command', lambda driver, args, env: (args, env))
    args, _ = bg.incident_command({'provider': 'claude', 'session_id': 's'})
    assert args == ['-p', '--output-format', 'stream-json', '--verbose', '--session-id', 's',
                    '--disallowedTools', 'CronCreate', 'CronDelete', 'ScheduleWakeup', 'Task',
                    '--allowedTools', *bg.TOOLS]


def test_human_approval_is_cc_only_and_validated(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='only implemented for the local cc'):
        bg.build_incident_prompt(make_request(approval='human'), resource='codex')
    with pytest.raises(ValueError, match='Invalid approval mode'):
        bg.build_incident_prompt(make_request(approval='auto'))
    with pytest.raises(ValueError, match='Approval timeout'):
        bg.build_incident_prompt(make_request(approval='human', approval_timeout_seconds=7200))
    from backend.core.agents import dispatch
    monkeypatch.setattr(dispatch, 'resolve', lambda **kw: {'main': 'codex', 'aux': None})
    with pytest.raises(ValueError, match='仅支持本机 cc'):
        dispatch.dispatch_agent_task(make_request(approval='human'))


def test_allow_runs_original_input_once_without_permission_updates(incident):
    record, root = incident
    tool_input = {'command': 'echo outside'}
    result, outcome, _ = ask(record, tool_input, answer=lambda row: decide(record, row))
    assert 'error' not in outcome
    assert result.behavior == 'allow' and result.updated_input == tool_input and result.updated_permissions is None
    row = only_request(record)
    assert row['state'] == 'allowed' and row['input_sha256'] == ap.input_digest(tool_input)
    assert (row['worker'], row['child'], row['generation']) == (record['worker'], record['child'], 0)
    assert bg.read_record(record['dispatch_id'])['pending_approvals'] == []
    with pytest.raises(RuntimeError, match='only a pending request'):
        decide(record, row)


def test_human_deny_reaches_claude_with_message(incident):
    record, _ = incident
    result, _, _ = ask(record, {'command': 'x'}, answer=lambda row: decide(
        record, row, 'deny', message='use the public API', interrupt=True))
    assert (result.behavior, result.message, result.interrupt) == ('deny', 'use the public API', True)
    assert only_request(record)['state'] == 'denied'


@pytest.mark.parametrize('field,value,match', [
    ('generation', 1, 'Generation changed'),
    ('tool_use_id', 'toolu_other', 'does not match'),
    ('input_sha256', '0' * 64, 'does not match'),
    ('decision', 'always', 'allow or deny'),
    ('decided_by', ' ', 'person deciding'),
])
def test_mismatched_decisions_are_refused_and_request_expires_denied(incident, field, value, match):
    record, _ = incident
    result, outcome, _ = ask(record, {'command': 'x'}, timeout=.5,
                             answer=lambda row: decide(record, row, **{field: value}))
    assert match in str(outcome['error'])
    assert result.behavior == 'deny' and only_request(record)['state'] == 'expired'


def test_duplicate_answer_is_rejected_first_wins(incident):
    record, _ = incident
    seen = {}

    def answer(row):
        seen['first'] = decide(record, row, 'deny')
        with pytest.raises(RuntimeError, match='already decided'):
            decide(record, row, 'allow')
        return seen['first']
    result, outcome, _ = ask(record, {'command': 'x'}, answer=answer)
    assert 'error' not in outcome and result.behavior == 'deny'


def test_dead_worker_or_cli_cannot_be_approved(incident):
    record, root = incident
    dead = {'pid': record['child']['pid'], 'created': 1.0}

    def answer(row):
        r = bg.read_record(record['dispatch_id'])
        bg.write_record(root / 'record.json', {**r, 'child': dead})
        assert ap.list_permission_requests(record['dispatch_id']) == []  # projected stale
        return decide(record, row)
    result, outcome, _ = ask(record, {'command': 'x'}, timeout=.5, answer=answer)
    assert 'no longer the live owner' in str(outcome['error'])
    assert result.behavior == 'deny'


def test_reused_tool_use_id_and_missing_id_are_denied(incident):
    record, _ = incident
    broker = ap.ApprovalBroker(record['dispatch_id'], record, update_for(record['dispatch_id']), poll_seconds=.02)
    broker.timeout = .1
    first = asyncio.run(broker.can_use_tool('Bash', {}, SimpleNamespace(tool_use_id='toolu_1')))
    again = asyncio.run(broker.can_use_tool('Bash', {}, SimpleNamespace(tool_use_id='toolu_1')))
    missing = asyncio.run(broker.can_use_tool('Bash', {}, SimpleNamespace(tool_use_id=None)))
    assert [x.behavior for x in (first, again, missing)] == ['deny'] * 3
    assert len(ap.list_permission_requests(record['dispatch_id'], pending_only=False)) == 1


def test_cancelled_wait_closes_request_and_refuses_late_answer(incident):
    record, _ = incident
    broker = ap.ApprovalBroker(record['dispatch_id'], record, update_for(record['dispatch_id']), poll_seconds=.02)

    async def cancelled():
        task = asyncio.create_task(broker.can_use_tool('Bash', {}, SimpleNamespace(tool_use_id='toolu_1')))
        await asyncio.sleep(.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancelled())
    row = only_request(record)
    assert row['state'] == 'cancelled'
    with pytest.raises(RuntimeError, match='only a pending request'):
        decide(record, row)


def pending_row(record, request_id='a' * 32):
    bg.write_record(ap._request_path(record['dispatch_id'], request_id), dict(
        request_id=request_id, dispatch_id=record['dispatch_id'], generation=0, session_id=record['session_id'],
        worker=record['worker'], child=record['child'], tool_name='Bash', tool_use_id='t', input={},
        input_sha256='d', title=None, description=None, decision_reason=None, blocked_path=None,
        created_at=time.time(), expires_at=time.time() + 60, state='pending'))
    return request_id


def test_session_end_abandons_pending_requests(incident):
    record, _ = incident
    broker = ap.ApprovalBroker(record['dispatch_id'], record, update_for(record['dispatch_id']))
    pending_row(record)
    broker.abandon_pending()
    assert only_request(record)['state'] == 'abandoned'


def test_resume_keeps_recorded_approval_unless_explicit(incident):
    record, root = incident
    legacy = asdict(make_request())
    legacy.pop('approval')  # pre-feature record
    record.update(request=legacy, state='failed', worker={}, child={}, session_created=True)
    bg.write_record(root / 'record.json', record)
    kept = bg.resume_claude(record['dispatch_id'], 'continue')
    assert kept['request'].get('approval', 'cli_policy') == 'cli_policy' and 'approval_history' not in kept
    kept.update(state='failed')
    bg.write_record(root / 'record.json', kept)
    with pytest.raises(ValueError, match='Invalid approval'):
        bg.resume_claude(record['dispatch_id'], 'continue', approval='bypass')
    opted = bg.resume_claude(record['dispatch_id'], 'continue', approval='human')
    assert opted['request']['approval'] == 'human' and opted['session_id'] == record['session_id']
    assert opted['approval_history'][-1]['from'] == 'cli_policy' and 'mode_history' not in opted
    assert json.loads((root / 'request.json').read_text(encoding='utf-8'))['approval'] == 'human'


@pytest.mark.parametrize('failure', ['', 'denials', 'error'])
def test_worker_routes_human_requests_and_keeps_business_acceptance(incident, monkeypatch, failure):
    from backend.core.agents import cli
    record, root = incident
    record.update(state='starting')
    bg.write_record(root / 'record.json', record)
    monkeypatch.setattr(cli, 'bootstrap', lambda config: None)
    monkeypatch.setattr(cli, 'cli_command', lambda *a: pytest.fail('human approval must not use claude -p'))
    final = {'result': json.dumps({'outcome': 'completed', 'criteria': [
        {'criterion': 'exact proof', 'satisfied': True, 'evidence': 'readback'}], 'summary': 'ok'}),
        'is_error': failure == 'error', 'permission_denials': [{'tool_name': 'Bash'}] if failure == 'denials' else []}
    monkeypatch.setattr(ap, 'run_human_session', lambda dispatch_id, r, update: final)
    monkeypatch.setattr(bg, 'run_checks', lambda names, row: {})
    bg.run_worker(record['dispatch_id'])
    assert bg.read_record(record['dispatch_id'])['acceptance'] == ('transport_failed' if failure else 'accepted')


@pytest.mark.parametrize('required', [(), ('Bash',)])
def test_sdk_session_uses_native_cli_same_rules_and_records_identity(incident, monkeypatch, child, required):
    import claude_agent_sdk as sdk
    record, root = incident
    record['request']['approval_required_tools'] = required
    bg.write_record(root / 'record.json', record)
    captured = {}

    class Client:
        def __init__(self, options):
            captured['options'] = options

        async def connect(self):
            captured['connected'] = True

        async def query(self, prompt, session_id):
            captured['query'] = (prompt, session_id)

        async def receive_response(self):
            yield sdk.SystemMessage('init', {'session_id': record['session_id'], 'permissionMode': 'auto'})
            yield sdk.ResultMessage('success', 1, 1, False, 1, record['session_id'], result='{}', permission_denials=[])

        async def disconnect(self):
            captured['disconnected'] = True
    monkeypatch.setattr(sdk, 'ClaudeSDKClient', Client)
    monkeypatch.setattr(ap, 'resolve_native_claude', lambda: 'C:/native/claude.exe')
    monkeypatch.setattr(ap, 'find_cli_process', lambda cli: psutil.Process(child['pid']))
    result = ap.run_human_session(record['dispatch_id'], bg.read_record(record['dispatch_id']),
                                  update_for(record['dispatch_id']))
    options = captured['options']
    assert options.cli_path == 'C:/native/claude.exe' and options.session_id == record['session_id']
    assert options.resume is None and options.permission_mode is None
    assert options.allowed_tools == bg.TOOLS and options.disallowed_tools == [*bg.DISALLOWED_TOOLS, 'AskUserQuestion']
    assert options.setting_sources == ['user', 'project', 'local']
    assert options.system_prompt == {'type': 'preset', 'preset': 'claude_code'}
    assert options.settings == (json.dumps({'permissions': {'ask': list(required)}}) if required else None)
    assert options.permission_prompt_tool_name is None and callable(options.can_use_tool)
    assert captured['query'][1] == record['session_id'] and captured['disconnected']
    state = bg.read_record(record['dispatch_id'])
    assert (state['child'], state['transport'], state['session_created'], state['permission_mode']) == (
        child, 'sdk_human_approval', True, 'auto')
    assert result == {'result': '{}', 'is_error': False, 'permission_denials': []}
    events = [json.loads(x)['type'] for x in Path(record['stdout_path']).read_text(encoding='utf-8').splitlines()]
    assert events == ['SystemMessage', 'ResultMessage']


def test_native_cli_resolution_never_uses_shim_or_bundled_copy():
    path = Path(ap.resolve_native_claude())
    assert path.suffix.lower() == '.exe' and '_bundled' not in path.parts


def test_reviewer_cli_requires_interactive_confirmation(incident, monkeypatch):
    record, _ = incident
    request_id = pending_row(record)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: False))
    assert ap.main(['decide', record['dispatch_id'], request_id, 'allow', '--by', 'x']) == 2
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr('builtins.input', lambda prompt: 'no')
    assert ap.main(['decide', record['dispatch_id'], request_id, 'allow', '--by', 'x']) == 1
    assert not ap._decision_path(record['dispatch_id'], request_id).exists()
    monkeypatch.setattr('builtins.input', lambda prompt: 'allow')
    assert ap.main(['decide', record['dispatch_id'], request_id, 'allow', '--by', 'x']) == 0
    assert json.loads(ap._decision_path(record['dispatch_id'], request_id).read_text())['decided_by'] == 'x'
