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


def test_readonly_research_has_no_repair_takeover_constraints(gate):
    path, calls, _ = gate
    assistance.request_ai_assistance(CodexEscalationRequest('research', 'unknown item', 'read evidence'),
        scheduler_settings_path=path, mode='read_only_research')
    assert len(calls) == 1
    assert any('不得取得游戏运行权' in c for c in calls[0].constraints)
    assert not any('正式 Job' in c for c in calls[0].constraints)
    assert control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']


def test_engineering_launches_once_across_concurrent_error_sources(gate):
    path, calls, request = gate
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(request, ['scene', 'login', 'ranking', 'job']))
    assert sum(result is not None for result in results) == 1
    assert len(calls) == 1
    assert control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']


def test_waiting_diagnostics_persist_without_opening_repair_gate(gate, monkeypatch):
    path, calls, request = gate
    request('first')
    original = assistance.read_ai_assistance_status(scheduler_settings_path=path)
    diagnostic = {'execution_state': 'waiting_blocked', 'native_goal_status': 'blocked',
                  'native_goal_source': 'app_server.thread/goal/get', 'turn_status': 'completed'}
    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', lambda ident: SimpleNamespace(
        status='running', error=None, codex_url='codex://threads/test',
        progress_diagnostics=diagnostic))
    assistance.refresh_ai_assistance_status(scheduler_settings_path=path)
    persisted = assistance.read_ai_assistance_status(scheduler_settings_path=path)
    assert persisted['agent_progress_diagnostics'] == diagnostic
    assert persisted['dispatch'] == original['dispatch']
    assert persisted['agent_status'] == 'running'
    assert request('second failure') is None
    assert len(calls) == 1


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


def test_handoff_request_carries_fanxiu_business(gate):
    path, calls, request = gate
    assert request() is not None
    assert len(calls) == 1
    assert calls[0].business == 'fanxiu'
    assert any('正式 Job' in constraint for constraint in calls[0].constraints)


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

    inspections = []

    def unavailable(ident):
        inspections.append(ident)
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
    assert request('another unknown') is None
    assert request('yet another unknown') is None
    assert len(inspections) == 1
    assert assistance.read_ai_assistance_status(scheduler_settings_path=path)['dispatch'] == before['dispatch']
    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', lambda ident: SimpleNamespace(
        status='failed', error='old repair failed', codex_url=None))
    assert request('new') is None
    assistance.refresh_ai_assistance_status(scheduler_settings_path=path)
    assert request('new') is not None
    assert len(calls) == 2


def _holds(path):
    return control.active_dispatch_holds(world_facts_path=path.with_name('world_facts.json'))


def _report(path, attempt='a1', task='resource-ranking'):
    return assistance.report_failed_job(task={'id': task, 'attempt_id': attempt},
                                        result={'status': 'error', 'error': 'SceneWaitTimeout #733'},
                                        entry_id='entry', scheduler_settings_path=path)


def test_dispatch_is_closed_and_hold_persisted_before_the_maintainer_launch(gate, monkeypatch):
    path, calls, request = gate
    seen = []

    def observe_launch(req):
        seen.append((control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled'],
                     list(_holds(path))))
        return CodexDispatch('abc123', 123, str(path.parent), 'request', 'prompt', 'out', 'err', 'now')
    monkeypatch.setattr(assistance, 'escalate_to_codex', observe_launch)
    result = _report(path)
    key = control.runtime_error_hold_key('resource-ranking', 'a1')
    assert seen == [(False, [key])]
    assert result['error'] == 'SceneWaitTimeout #733' and result['dispatch_hold'] == key
    hold = _holds(path)[key]
    assert hold['engineering_origin'] is True and hold['launch_status'] == 'launched' and hold['dispatch_id'] == 'abc123'


def test_suppressed_or_failed_launch_still_keeps_dispatch_closed_with_readable_hold(gate, monkeypatch):
    path, calls, request = gate
    request('live maintainer')                     # single live agent holds the gate
    result = _report(path)
    assert len(calls) == 1 and 'ai_assistance_dispatch' not in result
    assert _holds(path)[result['dispatch_hold']]['launch_status'] == 'suppressed'
    assert not control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']


def test_launch_exception_keeps_closed_gate_and_pending_fact(tmp_path, monkeypatch):
    path = tmp_path / 'settings.json'
    control.set_scheduler_job_group_enabled(True, scheduler_settings_path=path)
    def fail(request):
        raise OSError('CLI unavailable')
    monkeypatch.setattr(assistance, 'escalate_to_codex', fail)
    result = _report(path)
    hold = _holds(path)[result['dispatch_hold']]
    assert hold['launch_status'] == 'dispatch_failed' and 'CLI unavailable' in hold['launch_error']
    assert not control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']


def test_repeat_report_never_relaunches_or_refreezes_a_released_incident(gate):
    path, calls, request = gate
    first = _report(path)
    assert len(calls) == 1
    again = _report(path)
    assert again['dispatch_hold'] == first['dispatch_hold'] and len(calls) == 1
    world = path.with_name('world_facts.json')
    released = {**control.read_world_facts(world)['discoveries'][first['dispatch_hold']], 'status': 'released'}
    control.record_world_discovery(first['dispatch_hold'], released, path=world)
    control.set_scheduler_job_group_enabled(True, scheduler_settings_path=path)   # engineering recovered
    late = _report(path)
    assert late['dispatch_hold_existing'] == 'released' and len(calls) == 1
    assert control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']


def test_ai_owned_failure_keeps_incident_but_never_launches(gate):
    path, calls, request = gate
    control.set_scheduler_job_group_enabled(False, scheduler_settings_path=path)  # AI already owns the game
    result = _report(path, attempt='ai1')
    hold = _holds(path)[result['dispatch_hold']]
    assert calls == [] and hold['engineering_origin'] is False and hold['launch_status'] == 'not_engineering_origin'
    assert assistance.request_ai_assistance(
        assistance.CodexEscalationRequest('x', 'y', 'repair'), scheduler_settings_path=path,
        incident_hold_key=result['dispatch_hold']) is None


def test_non_error_terminals_never_hold(gate):
    path, calls, request = gate
    for status in ('success', 'skipped', 'pending', 'interrupted'):
        assistance.report_failed_job(task={'id': 'job', 'attempt_id': status}, result={'status': status},
                                     entry_id='entry', scheduler_settings_path=path)
    assert _holds(path) == {} and control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']


def _leased_hold(path, attempt='a1', task='resource-ranking'):
    return control.record_leased_action_hold(
        f'{task} leased action', 'RuntimeError: adb screencap timeout', 'recognized battle',
        task_id=task, attempt_id=attempt, scheduler_settings_path=path,
        world_facts_path=path.with_name('world_facts.json'), read_identity=False)


def test_engineering_leased_hold_then_terminal_report_launches_exactly_one_maintainer(gate, monkeypatch):
    path, calls, request = gate
    seen = []

    def observe_launch(req):
        seen.append((control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled'],
                     list(_holds(path)), control.runtime_error_launch_authorized(
                         key, world_facts_path=path.with_name('world_facts.json'))))
        return CodexDispatch('abc123', 123, str(path.parent), 'request', 'prompt', 'out', 'err', 'now')
    monkeypatch.setattr(assistance, 'escalate_to_codex', observe_launch)
    key = control.runtime_error_hold_key('resource-ranking', 'a1')
    first = _leased_hold(path)
    assert first['key'] == key and first['engineering_origin'] is True
    assert first['launch_status'] == 'awaiting_terminal_report'
    assert not control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']
    assert not control.runtime_error_launch_authorized(key, world_facts_path=path.with_name('world_facts.json'))
    result = _report(path)
    assert seen == [(False, [key], True)] and result['error'] == 'SceneWaitTimeout #733'
    hold = _holds(path)[key]
    assert hold['origin'] == 'leased_action_stop' and hold['last_liveness_evidence'] == 'recognized battle'
    assert hold['engineering_origin'] is True and hold['launch_status'] == 'launched'
    assert hold['terminal_error'] == 'SceneWaitTimeout #733'
    assert _report(path)['dispatch_hold_existing'] == 'active' and len(seen) == 1


def test_ai_owned_leased_hold_or_other_attempt_never_borrows_engineering_origin(gate):
    path, calls, request = gate
    control.set_scheduler_job_group_enabled(False, scheduler_settings_path=path)   # AI owns the game
    _leased_hold(path, attempt='ai1')
    ai = _report(path, attempt='ai1')
    assert _holds(path)[ai['dispatch_hold']]['launch_status'] == 'not_engineering_origin'
    control.set_scheduler_job_group_enabled(True, scheduler_settings_path=path)
    _leased_hold(path, attempt='eng1')                     # engineering incident of another attempt
    other = _report(path, attempt='other')                  # gate already closed by eng1, not its own
    assert _holds(path)[other['dispatch_hold']]['engineering_origin'] is False
    assert _holds(path)[control.runtime_error_hold_key('resource-ranking', 'eng1')]['launch_status'] == 'awaiting_terminal_report'
    assert calls == []


def test_released_leased_hold_is_neither_relaunched_nor_refrozen(gate):
    path, calls, request = gate
    key = _leased_hold(path)['key']
    world = path.with_name('world_facts.json')
    control.record_world_discovery(key, {**control.read_world_facts(world)['discoveries'][key], 'status': 'released'},
                                   path=world)
    control.set_scheduler_job_group_enabled(True, scheduler_settings_path=path)
    assert _report(path)['dispatch_hold_existing'] == 'released' and calls == []
    assert control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']


def test_executor_leased_hold_carries_the_formal_attempt_identity(monkeypatch):
    from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeExecutor
    from backend.core.fanxiu.data_annotation.subtask_execution import job_log_context
    captured = []
    monkeypatch.setattr(control, 'record_leased_action_hold',
                        lambda *args, **kwargs: captured.append(kwargs) or {'key': 'k'})
    owner = SimpleNamespace(_log=lambda *args: None)
    with job_log_context('daily-xuanhuang', 'att-9'):
        assert BehaviorTreeExecutor.hold_dispatch_after_unsafe_stop(owner, 'x', 'r', 'battle') is True
    assert BehaviorTreeExecutor.hold_dispatch_after_unsafe_stop(owner, 'x', 'r', 'battle') is True
    assert captured == [{'task_id': 'daily-xuanhuang', 'attempt_id': 'att-9'}, {'task_id': '', 'attempt_id': ''}]
