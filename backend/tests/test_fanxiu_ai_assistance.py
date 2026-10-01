"""Ownership, persistence and process-dispatch contracts; no simulated game."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from backend.core.codex import CodexDispatch, CodexEscalationRequest
from backend.core.fanxiu.data_annotation import ai_assistance as assistance
from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control


@pytest.fixture
def gate(tmp_path, monkeypatch):
    path = tmp_path / 'settings.json'
    control.set_scheduler_job_group_enabled(True, scheduler_settings_path=path)
    calls = []
    def dispatch(request):
        calls.append(request)
        return CodexDispatch('abc123', 123, str(tmp_path), 'request', 'prompt', 'out', 'err', 'now')
    monkeypatch.setattr(assistance, 'escalate_to_codex', dispatch)
    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', lambda ident: SimpleNamespace(
        status='running', error=None, codex_url='codex://threads/test'))
    def request(title='incident'):
        return assistance.request_ai_assistance(
            CodexEscalationRequest(title, 'failed', 'repair'), scheduler_settings_path=path)
    return path, calls, request


def test_no_owner_and_ai_owner_never_launch(gate):
    path, calls, request = gate
    path.unlink()
    assert request() is None
    control.set_scheduler_job_group_enabled(False, scheduler_settings_path=path)
    assert request() is None
    assert not calls


def test_engineering_launches_once_across_concurrent_error_sources(gate):
    path, calls, request = gate
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(request, ['scene', 'login', 'ranking', 'job']))
    assert sum(result is not None for result in results) == 1
    assert len(calls) == 1
    assert control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']


def test_queued_launch_rechecks_owner_inside_shared_lock(gate):
    path, calls, request = gate
    with ThreadPoolExecutor(max_workers=1) as pool:
        with assistance.assistance_control_lock(path):
            pending = pool.submit(request)
            control.set_scheduler_job_group_enabled(False, scheduler_settings_path=path)
        assert pending.result(timeout=5) is None
    assert not calls


def test_disabled_kernel_cannot_dispatch(gate):
    path, calls, request = gate
    control.write_scheduler_settings({'job_group_enabled': True, 'behavior_tree_enabled': False},
                                     scheduler_settings_path=path)
    assert request() is None
    assert not calls


def test_agent_exit_does_not_mean_incident_resolved_and_cooldown_prevents_loop(gate, monkeypatch):
    path, calls, request = gate
    request()
    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', lambda ident: SimpleNamespace(
        status='completed', error=None, codex_url='codex://threads/test'))
    assert request() is None
    state = assistance.read_ai_assistance_status(scheduler_settings_path=path)
    assert state['resolved'] is False
    assert state['agent_status'] == 'completed'
    assert len(calls) == 1


def test_failed_launch_is_visible_and_rate_limited(gate, monkeypatch):
    path, calls, request = gate
    def fail(request):
        calls.append(request)
        raise OSError('CLI unavailable')
    monkeypatch.setattr(assistance, 'escalate_to_codex', fail)
    with pytest.raises(OSError, match='CLI unavailable'):
        request()
    assert request() is None
    assert len(calls) == 1
    assert assistance.read_ai_assistance_status(scheduler_settings_path=path)['status'] == 'dispatch_failed'
    assert control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']


def test_terminal_error_fallback_uses_same_gate(gate):
    path, calls, request = gate
    result = assistance.report_failed_job(task={'id': 'job'}, result={'status': 'error', 'error': 'broken'},
                                         entry_id='entry', scheduler_settings_path=path)
    assert result['ai_assistance_dispatch']['dispatch_id'] == 'abc123'
    assert len(calls) == 1
    assistance.report_failed_job(task={'id': 'other'}, result={'status': 'success'},
                                 entry_id='entry', scheduler_settings_path=path)
    assert len(calls) == 1


def test_alternating_faults_cannot_reset_cooldown(gate, monkeypatch):
    path, calls, request = gate
    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', lambda ident: SimpleNamespace(
        status='completed', error=None, codex_url=None))
    request('scene')
    request('login')
    assert request('scene') is None
    assert len(calls) == 2


def test_unreadable_owner_is_visible_and_recovers_without_duplicate_dispatch(gate, monkeypatch):
    path, calls, request = gate
    request('old')
    before = assistance.read_ai_assistance_status(scheduler_settings_path=path)

    def unavailable(ident):
        raise FileNotFoundError('deleted Node runtime')

    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', unavailable)
    with pytest.raises(FileNotFoundError, match='Node runtime'):
        request('new')
    state = assistance.read_ai_assistance_status(scheduler_settings_path=path)
    assert state['dispatch'] == before['dispatch']
    assert state['recent_requests'] == before['recent_requests']
    assert state['agent_status'] == 'unknown'
    assert state['ownership_check_status'] == 'failed'
    assert 'deleted Node runtime' in state['agent_error']
    assert len(calls) == 1
    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', lambda ident: SimpleNamespace(
        status='failed', error='old repair failed', codex_url=None))
    assert request('new') is not None
    assert len(calls) == 2
