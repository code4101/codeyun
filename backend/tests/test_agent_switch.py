"""Pure policy, provider transport and owner-identity contracts; no game/model calls."""
import json
from unittest.mock import Mock

import pytest

from backend.core.agents import background, policy
from backend.core.agents.contracts import AgentTaskRequest


def test_switch_only_changes_requested_profile(monkeypatch, tmp_path):
    config = policy.load_config()
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setattr(policy, 'CONFIG', path)
    for resource in ['cc', 'codex', 'cc', 'codex']:
        result = policy.set_business_agent('fanxiu', resource)
        assert (result['main'], result['aux']) == (resource, None)
        expected = {**config, 'businesses': {**config['businesses'],
                    'fanxiu': {'main': resource, 'aux': None}}}
        assert json.loads(path.read_text(encoding='utf-8')) == expected
    before = path.read_bytes()
    for business, resource in [('missing', 'cc'), ('fanxiu', 'ds'), ('attendance', 'codex')]:
        with pytest.raises(ValueError):
            policy.set_business_agent(business, resource)
        assert path.read_bytes() == before


def test_codex_command_inherits_policy_and_resumes_exact_session(monkeypatch):
    monkeypatch.setattr('backend.core.codex.escalation.resolve_codex_executable', lambda **kw: 'codex.exe')
    monkeypatch.setenv('CODEX_THREAD_ID', 'parent-must-not-leak')
    first, env = background.incident_command({'provider': 'codex'})
    assert first == ['codex.exe', 'exec', '--json', '-']
    assert 'CODEX_THREAD_ID' not in env
    resumed, _ = background.incident_command({'provider': 'codex', 'resume': True, 'session_id': 'exact-session'})
    assert resumed == ['codex.exe', 'exec', '--json', 'resume', 'exact-session', '-']


def test_profile_change_does_not_migrate_exited_owner(monkeypatch, tmp_path):
    """A resumed CC record stays CC even after the business selects Codex."""
    root = tmp_path / 'incident'
    root.mkdir()
    request = AgentTaskRequest('x', 'p', 'o', business='fanxiu', mode='smoke',
                               completion_criteria=('proof',))
    from dataclasses import asdict
    record = dict(provider='claude', resource='cc', session_id='original', generation=0,
                  session_created=True, acceptance='incomplete', request=asdict(request),
                  mode='smoke', worker={}, child={})
    background.write_record(root / 'record.json', record)
    monkeypatch.setattr(background, 'incident_root', lambda *a, **kw: root)
    monkeypatch.setattr(background, 'incident_logs', lambda *a: root)
    launch = Mock(return_value='resumed')
    monkeypatch.setattr(background, 'launch_worker', launch)
    assert background.resume_claude('id', 'finish original') == 'resumed'
    persisted = json.loads((root / 'record.json').read_text(encoding='utf-8'))
    assert (persisted['provider'], persisted['resource'], persisted['session_id']) == ('claude', 'cc', 'original')
    assert persisted['generation'] == 1


@pytest.mark.parametrize('resource', ['cc', 'codex'])
def test_prompt_uses_shared_receipt_without_recursive_agents(resource):
    request = AgentTaskRequest('x', 'p', 'o', mode='smoke', completion_criteria=('proof',))
    text = background.build_incident_prompt(request, resource=resource)
    assert f'本任务仅使用 {resource}' in text
    assert '最终回执' in text


@pytest.mark.parametrize('wrong_session', [False, True])
@pytest.mark.parametrize('stream_case', [
    'clean', 'recovered_reconnect', 'unrecovered_reconnect', 'fatal_error', 'turn_failed',
    'missing_terminal',
])
def test_codex_worker_records_native_session_and_requires_same_resume(monkeypatch, tmp_path, wrong_session, stream_case):
    """Exercise actual subprocess JSONL transport, without a model or game fixture."""
    import os
    import sys
    from dataclasses import asdict
    root = tmp_path / 'incident'
    root.mkdir()
    monkeypatch.setattr(background, 'incident_root', lambda *a, **kw: root)
    monkeypatch.setattr(background, 'incident_logs', lambda *a: root)
    monkeypatch.setattr(background, 'launch_worker', lambda identity: background.read_record(identity))
    request = AgentTaskRequest('probe', 'transport', 'receipt', mode='smoke', completion_criteria=('proof',))
    record = background.dispatch_codex(request, workspace_dir=tmp_path)
    receipt = {'outcome': 'completed', 'criteria': [
        {'criterion': 'proof', 'satisfied': True, 'evidence': 'transport'}], 'summary': 'done'}
    events = [{'type': 'thread.started', 'thread_id': 'native-session'},
              {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(receipt)}},
              {'type': 'turn.completed'}]
    if stream_case in {'recovered_reconnect', 'unrecovered_reconnect'}:
        # Actual CLI routing-discovery retry transcript; no model/tool replay.
        events[1:1] = [
            {'type': 'error', 'message': 'Reconnecting... 2/5 (workspace routing discovery failed)'},
            {'type': 'error', 'message': 'Reconnecting... 3/5 (workspace routing discovery failed)'},
        ]
    elif stream_case == 'fatal_error':
        events.insert(1, {'type': 'error', 'message': 'Permission denied'})
    elif stream_case == 'turn_failed':
        events.insert(1, {'type': 'turn.failed', 'error': {'message': 'retry exhausted'}})
    if stream_case in {'missing_terminal', 'unrecovered_reconnect'}:
        events.pop()
    if wrong_session:
        record.update(session_id='original-session', resume=True, session_created=True)
        background.write_record(root / 'record.json', record)
    command = [sys.executable, '-c', 'import sys;sys.stdin.read();' +
               ';'.join('print(' + repr(json.dumps(e)) + ',flush=True)' for e in events)]
    monkeypatch.setattr(background, 'incident_command', lambda record: (command, dict(os.environ)))
    monkeypatch.setattr('backend.core.agents.cli.bootstrap', lambda config: None)
    background.run_worker(record['dispatch_id'])
    saved = background.read_record(record['dispatch_id'])
    if wrong_session:
        assert saved['acceptance'] == 'transport_failed'
        assert saved['session_id'] == 'original-session'
        assert 'another session' in saved['error']
    elif stream_case in {'unrecovered_reconnect', 'fatal_error', 'turn_failed', 'missing_terminal'}:
        assert saved['acceptance'] == 'transport_failed'
    else:
        assert saved['acceptance'] == 'accepted'
        assert saved['session_id'] == 'native-session'
        assert saved['session_created'] is True
        assert saved['child']['pid'] > 0 and saved['child']['created'] > 0
