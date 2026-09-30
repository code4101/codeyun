from pathlib import Path
import asyncio
import threading

import pytest

from backend.core.codex import desktop, escalation, app_server


def test_goal_read_negotiates_experimental_capability(monkeypatch):
    calls = []
    def read(**kwargs):
        calls.append(kwargs)
        return {'goal': None}
    monkeypatch.setattr(app_server, '_read_codex_app_server', read)
    assert app_server.read_codex_thread_goal('owner') == {'goal': None}
    assert calls == [dict(method='thread/goal/get', params={'threadId': 'owner'},
                          timeout_seconds=25.0, experimental_api=True)]


@pytest.mark.parametrize('running_loop', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_sync_desktop_bridge_owns_loop_and_preserves_errors(monkeypatch, running_loop, fail):
    caller_thread = threading.get_ident()
    calls = []

    async def call(binding, name, arguments, timeout):
        calls.append(threading.get_ident())
        await asyncio.sleep(0)
        if fail:
            raise ValueError('provider failure')
        return {'name': name}

    monkeypatch.setattr(desktop, '_call', call)

    def invoke():
        return desktop.call_desktop_tool('read_thread', {}, binding={'test': True})

    async def in_loop():
        return invoke()

    if fail:
        with pytest.raises(ValueError, match='provider failure'):
            asyncio.run(in_loop()) if running_loop else invoke()
    else:
        assert (asyncio.run(in_loop()) if running_loop else invoke()) == {'name': 'read_thread'}
    assert len(calls) == 1
    assert (calls[0] != caller_thread) == running_loop


def test_desktop_uses_saved_project_defaults_and_fresh_creation(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(desktop, 'read_desktop_binding', lambda: {
        'workspace_dir': str(tmp_path), 'project_id': 'saved-project',
    })
    def call(name, args, **kwargs):
        if name == 'list_projects':
            return {'projects': []}
        calls.append((name, args))
        return {'threadId': f'thread-{len(calls)}'}
    monkeypatch.setattr(desktop, 'call_desktop_tool', call)
    first = desktop.create_desktop_repair(prompt='goal', title='incident', workspace=tmp_path)
    second = desktop.create_desktop_repair(prompt='goal', title='incident', workspace=tmp_path)
    assert first['threadId'] != second['threadId']
    for name, args in calls:
        assert name == 'create_thread'
        assert args['target'] == {'type': 'project', 'projectId': 'saved-project',
                                  'environment': {'type': 'local'}}
        assert 'model' not in args and 'thinking' not in args


@pytest.mark.parametrize(('goal', 'turn', 'active', 'expected'), [
    (None, 'completed', False, 'failed'),
    ('active', 'completed', False, 'running'),
    ('complete', 'completed', False, 'completed'),
    ('complete', 'inProgress', True, 'running'),
    ('blocked', 'completed', False, 'failed'),
    ('active', 'interrupted', False, 'failed'),
])
def test_desktop_completion_requires_native_goal(monkeypatch, goal, turn, active, expected):
    monkeypatch.setattr(desktop, 'call_desktop_tool', lambda *a, **k: {
        'thread': {'status': {'type': 'active' if active else 'idle'}},
        'turns': [{'status': turn, 'items': []}],
    })
    monkeypatch.setattr(app_server, 'read_codex_thread_goal', lambda *a: {'goal': {'status': goal}})
    assert desktop.read_desktop_repair('thread')['status'] == expected


def test_default_dispatch_uses_desktop_and_does_not_spawn_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(escalation, 'codeyun_temp_root', lambda *a, **k: tmp_path.joinpath(*a))
    monkeypatch.setattr(escalation, 'popen_service', lambda *a, **k: pytest.fail('CLI started'))
    monkeypatch.setattr(desktop, 'create_desktop_repair', lambda **k: {'threadId': 'new-desktop-id'})
    result = escalation.escalate_to_codex(escalation.CodexEscalationRequest('fault', 'error', 'fix'),
                                         workspace_dir=tmp_path)
    assert result.thread_id == 'new-desktop-id' and result.transport == 'desktop'
    assert result.pid == 0
    assert 'create_goal' in Path(result.prompt_path).read_text(encoding='utf-8')
    monkeypatch.setattr(desktop, 'read_desktop_repair', lambda t, **kw: dict(
        status='completed', error=None, goal_status='complete', last_message='done'))
    assert escalation.inspect_codex_dispatch(result.dispatch_id).status == 'completed'


def test_unavailable_desktop_never_falls_back_to_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(escalation, 'codeyun_temp_root', lambda *a, **k: tmp_path.joinpath(*a))
    monkeypatch.setattr(escalation, 'popen_service', lambda *a, **k: pytest.fail('CLI fallback'))
    def unavailable(**kwargs):
        raise RuntimeError('desktop unavailable')
    monkeypatch.setattr(desktop, 'create_desktop_repair', unavailable)
    with pytest.raises(RuntimeError, match='desktop unavailable'):
        escalation.escalate_to_codex(escalation.CodexEscalationRequest('fault', 'error', 'fix'),
                                     workspace_dir=tmp_path)


def test_uncertain_create_keeps_ownership_gate_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(escalation, 'codeyun_temp_root', lambda *a, **k: tmp_path.joinpath(*a))
    def uncertain(**kwargs):
        raise desktop.DesktopDispatchUncertain('lost response')
    monkeypatch.setattr(desktop, 'create_desktop_repair', uncertain)
    result = escalation.escalate_to_codex(escalation.CodexEscalationRequest('fault', 'error', 'fix'),
                                         workspace_dir=tmp_path)
    assert result.thread_id is None
    with pytest.raises(RuntimeError, match='禁止重复创建'):
        escalation.inspect_codex_dispatch(result.dispatch_id)


def test_completed_tool_receipt_survives_desktop_goal_clear(monkeypatch):
    monkeypatch.setattr(desktop, 'call_desktop_tool', lambda *a, **k: {
        'thread': {'status': {'type': 'idle'}}, 'turns': [{'status': 'completed'}],
    })
    monkeypatch.setattr(app_server, 'read_codex_thread_goal', lambda *a: {'goal': None})
    receipt = {'goal': {'threadId': 'thread', 'status': 'complete', 'objective': 'fix'}}
    assert desktop.read_desktop_repair('thread', goal_receipt=receipt)['status'] == 'completed'
    assert desktop.read_desktop_repair('another', goal_receipt=receipt)['status'] == 'failed'


def test_goal_receipt_checks_thread_and_cannot_reopen_completion(monkeypatch, tmp_path):
    monkeypatch.setattr(escalation, 'codeyun_temp_root', lambda *a, **k: tmp_path.joinpath(*a))
    monkeypatch.setattr(desktop, 'create_desktop_repair', lambda **k: {'threadId': 'owner'})
    d = escalation.escalate_to_codex(escalation.CodexEscalationRequest('fault', 'error', 'fix'),
                                    workspace_dir=tmp_path)
    with pytest.raises(ValueError, match='不匹配'):
        escalation.record_desktop_goal_result(d.dispatch_id, {'goal': {'threadId': 'other'}})
    receipt = {'goal': {'threadId': 'owner', 'objective': 'fix', 'status': 'complete'}}
    assert escalation.record_desktop_goal_result(d.dispatch_id, receipt) == receipt
    with pytest.raises(ValueError, match='不能退回'):
        escalation.record_desktop_goal_result(d.dispatch_id, {
            'goal': {'threadId': 'owner', 'objective': 'fix', 'status': 'active'},
        })
