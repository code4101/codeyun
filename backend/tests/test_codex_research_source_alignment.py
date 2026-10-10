"""Readonly connection provenance and stable live-writer bracket contracts."""
import copy

import pytest

from backend.core.codex import app_server, research_supervision


def test_same_reader_connection_never_resumes_loads_or_starts_model(monkeypatch):
    from backend.core.codex import wechat_agent
    calls = []
    class Client:
        def start(self): calls.append('initialize')
        def close(self): calls.append('close')
        def rpc(self, method, params, **kw):
            calls.append((method, params))
            if method == 'thread/read':
                return {'thread': {'id': 'original', 'status': {'type': 'notLoaded'}}}
            return {'data': [{'id': 'live-turn', 'status': 'interrupted'}]}
    monkeypatch.setattr(wechat_agent, 'CodexWechatClient', Client)
    result = app_server.read_codex_thread_readonly_session('original')
    assert [row[0] for row in calls if isinstance(row, tuple)] == ['thread/read', 'thread/turns/list']
    assert calls[0] == 'initialize' and calls[-1] == 'close'
    assert not result['writer_authority'] and not result['writer_loaded_by_reader'] and not result['model_started']


@pytest.mark.parametrize('mode', ['stable', 'live_changed', 'reader_error', 'wrong_turn', 'bad_status'])
def test_active_live_writer_cannot_be_released_by_independent_interrupted_view(monkeypatch, mode):
    calls = []
    live = {'thread': {'id': 'original', 'status': {'type': 'active'}},
            'turns': [{'id': 'live-turn', 'status': 'inProgress'}]}
    def desktop(*args, **kw):
        calls.append('live')
        result = copy.deepcopy(live)
        if mode == 'live_changed' and calls.count('live') == 2:
            result['thread']['status']['type'] = 'idle'
            result['turns'][0]['status'] = 'completed'
        return result
    def child(*args, **kw):
        calls.append('child')
        if mode == 'reader_error': raise ConnectionError('read failed')
        return {'metadata': {'id': 'original', 'status': {'type': 'notLoaded'}},
            'turns': [{'id': 'other' if mode == 'wrong_turn' else 'live-turn',
                       'status': 'unexpected' if mode == 'bad_status' else 'interrupted'}]}
    monkeypatch.setattr(research_supervision, 'call_desktop_tool', desktop)
    monkeypatch.setattr(app_server, 'read_codex_thread_readonly_session', child)
    result = research_supervision.read_codex_research_source_alignment('original')
    assert calls == ['live', 'child', 'live']
    assert result['same_turn_lifecycle_contrast_observed'] == (mode == 'stable')
    assert not result['writer_gate_released'] and not result['recovery_admission_granted']
    assert not result['goal_changed'] and result['interrupted_parser_root_cause'] == 'unknown'


def test_wrong_native_identity_closes_reader_without_fallback(monkeypatch):
    from backend.core.codex import wechat_agent
    calls = []
    class Client:
        def start(self): pass
        def close(self): calls.append('closed')
        def rpc(self, method, params, **kw):
            return {'thread': {'id': 'other'}}
    monkeypatch.setattr(wechat_agent, 'CodexWechatClient', Client)
    with pytest.raises(app_server.CodexAppServerError, match='identity mismatch'):
        app_server.read_codex_thread_readonly_session('original')
    assert calls == ['closed']


def test_regular_snapshot_authority_retains_active_writer_and_recovery_gate():
    from backend.core.codex.research_recovery import _latest, ResearchRecoveryAdmissionRejected
    desktop = {'available': True, 'writer': 'active',
        'latest_turn': {'id': 'same-turn', 'status': 'inProgress'}}
    history = {'available': True, 'turns': [{'id': 'same-turn', 'status': 'interrupted'}]}
    authority = research_supervision.project_codex_research_authority(desktop, history)
    assert authority['writer'] == 'active' and authority['same_turn_lifecycle_contrast']
    assert not authority['latest_turn_agreement'] and not authority['writer_gate_released']
    with pytest.raises(ResearchRecoveryAdmissionRejected, match='writer retained'):
        _latest({'thread_id': 'original', 'source_errors': [], 'authority': authority}, 'original')
