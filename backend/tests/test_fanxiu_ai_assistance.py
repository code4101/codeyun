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
    assert _holds(path)[result['dispatch_hold']]['launch_status'] == 'owner_active'
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


def test_detached_error_terminal_reports_the_awaiting_leased_hold_once(gate):
    # 2026-10-18: the caller lost its submit wait, the Cell then failed and
    # reconcile wrote the terminal; the hold stayed awaiting and no one was told.
    path, calls, request = gate
    key = _leased_hold(path, task='daily-xuanhuang')['key']
    task = {'id': 'daily-xuanhuang', 'last_message': 'SceneClickMismatch #420',
            'last_terminal_attempt': {'attempt_id': 'a1', 'scheduler_result': 'error'}}
    for _ in range(2):
        control._report_recovered_leased_action_error(
            task, None, {'error_type': 'SceneClickMismatch'}, scheduler_settings_path=path, world_facts_path=None)
    assert len(calls) == 1 and calls[0].problem == 'SceneClickMismatch #420'
    assert _holds(path)[key]['launch_status'] == 'launched'
    other = {**task, 'id': 'other', 'last_terminal_attempt': {'attempt_id': 'a2', 'scheduler_result': 'error'}}
    control._report_recovered_leased_action_error(other, None, {}, scheduler_settings_path=path, world_facts_path=None)
    assert len(calls) == 1 and list(_holds(path)) == [key]


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


def _claude(ident='c1'):
    from backend.core.agents.contracts import AgentDispatch
    return AgentDispatch(ident, 'claude', 'cc', 1, 'ws', 'request', 'prompt', 'out', 'err', 'now', session_id='s1')


@pytest.fixture
def exited_claude_owner(gate, monkeypatch):
    """A Claude owner launched earlier whose registered processes have exited."""
    path, calls, request = gate
    monkeypatch.setattr(assistance, 'escalate_to_codex', lambda req: calls.append(req) or _claude())
    request('old incident')
    status = {'value': 'failed'}
    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', lambda ident: SimpleNamespace(
        status=status['value'], error='Receipt incomplete', codex_url=None))
    resumes = []
    def resume(ident, prompt, *, mode=None):
        resumes.append((ident, prompt, mode))
        return _claude(ident)
    monkeypatch.setattr(assistance, 'resume_agent_dispatch', resume)
    state = assistance.read_ai_assistance_status(scheduler_settings_path=path)
    state['requested_at'] -= 3600  # the old incident is outside every cooldown
    assistance.write_data_annotation_json(path.with_name('ai_assistance.json'), state)
    return path, calls, request, resumes, status


def test_exited_failed_claude_owner_is_resumed_in_same_session_once(exited_claude_owner):
    path, calls, request, resumes, _ = exited_claude_owner
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(request, ['lingmai', 'lundao', 'ranking', 'login']))
    assert sum(result is not None for result in results) == 1
    assert len(calls) == 1 and len(resumes) == 1  # no second writer or new session
    assert resumes[0][0] == 'c1' and resumes[0][2] == 'repair'
    state = assistance.read_ai_assistance_status(scheduler_settings_path=path)
    assert state['dispatch']['dispatch_id'] == 'c1' and state['resumed']


def test_live_claude_owner_is_never_resumed_or_replaced(exited_claude_owner):
    _, calls, request, resumes, status = exited_claude_owner
    status['value'] = 'running'
    assert request('new natural fault') is None
    assert not resumes and len(calls) == 1


def test_refused_resume_keeps_owner_and_backs_off(exited_claude_owner, monkeypatch):
    path, calls, request, _, _ = exited_claude_owner
    def refuse(ident, prompt, *, mode=None):
        raise RuntimeError('Cannot resume beside a live owner')
    monkeypatch.setattr(assistance, 'resume_agent_dispatch', refuse)
    with pytest.raises(RuntimeError, match='live owner'):
        request('new natural fault')
    state = assistance.read_ai_assistance_status(scheduler_settings_path=path)
    assert state['status'] == 'dispatch_failed' and state['dispatch']['dispatch_id'] == 'c1'
    assert request('another fault') is None  # bounded retry, no high-frequency relaunch
    assert len(calls) == 1


# --- M1 deferred incident dispatch recovery: isolated provider contracts only ---

IDLE_KERNEL = {'alive': True, 'execution_state': 'idle', 'last_cell_submitted_at': 0, 'last_cell_completed_at': 0}


@pytest.fixture
def no_game(monkeypatch):
    """Any Cell/ownership/schedule action from the retry tick fails the test."""
    def forbidden(*args, **kwargs):
        raise AssertionError('retry tick must not touch the game, ownership or Job schedule')
    for name in ('submit_task_cell', 'stop_current_task', 'take_ai_control', 'run_now_scheduler_task',
                 'set_scheduler_task_next_time', 'restart_behavior_tree_kernel', 'write_scheduler_tasks'):
        monkeypatch.setattr(control, name, forbidden)


def _tick(path, **kwargs):
    kwargs.setdefault('kernel_status', IDLE_KERNEL)
    return assistance.retry_deferred_runtime_error_launches(
        scheduler_settings_path=path, scheduler_state_path=path.with_name('tasks.json'), **kwargs)


def _clock(monkeypatch, at):
    monkeypatch.setattr(assistance, 'time', SimpleNamespace(time=lambda: at))


def _decide(path, title):
    decision = {}
    dispatch = assistance.request_ai_assistance(
        CodexEscalationRequest(title, 'f', 'r'), scheduler_settings_path=path, decision=decision)
    return dispatch, decision


def test_typed_decision_reports_real_suppression_branches(gate, monkeypatch):
    path, calls, request = gate
    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', lambda ident: SimpleNamespace(
        status='completed', error=None, codex_url=None))
    request('first')
    state = assistance.read_ai_assistance_status(scheduler_settings_path=path)
    dispatch, decision = _decide(path, 'first')
    assert dispatch is None and decision == {'reason': 'same_title_cooldown',
                                             'retry_after': state['requested_at'] + 1800}
    dispatch, decision = _decide(path, 'other title')  # an accepted owner sets no cross-title cooldown
    assert dispatch is not None and decision == {'reason': 'launched', 'retry_after': None}
    state = assistance.read_ai_assistance_status(scheduler_settings_path=path)
    assistance.write_data_annotation_json(path.with_name('ai_assistance.json'), {**state, 'status': 'dispatch_failed'})
    dispatch, decision = _decide(path, 'third title')
    assert dispatch is None and decision == {'reason': 'dispatch_failed_cooldown',
                                             'retry_after': state['requested_at'] + 1800}
    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', lambda ident: SimpleNamespace(
        status='running', error=None, codex_url=None))
    assert _decide(path, 'fourth')[1] == {'reason': 'live_owner', 'retry_after': None}


def test_exited_owner_cooldown_defers_then_resumes_same_session_exactly_once(
        exited_claude_owner, monkeypatch, no_game):
    path, calls, request, resumes, _ = exited_claude_owner
    state = assistance.read_ai_assistance_status(scheduler_settings_path=path)
    state['requested_at'] += 3600 - 60   # owner exited, but was requested a minute ago: resume cooldown
    assistance.write_data_annotation_json(path.with_name('ai_assistance.json'), state)
    result = _report(path)
    hold = _holds(path)[result['dispatch_hold']]
    assert hold['launch_status'] == 'deferred' and hold['deferred_reason'] == 'resume_cooldown'
    assert hold['retry_after'] == pytest.approx(state['requested_at'] + 1800)
    assert not control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']
    assert _tick(path, now=hold['retry_after'] - 1)['dispatched'] == 0       # before cooldown: 0
    assert not resumes
    _clock(monkeypatch, hold['retry_after'] + 1)
    first = _tick(path)                                                        # due: exactly 1
    assert first['dispatched'] == 1 and first['resumed'] is True
    assert len(resumes) == 1 and resumes[0][0] == 'c1' and len(calls) == 1   # same session, no new writer
    assert _tick(path)['dispatched'] == 0 and len(resumes) == 1               # second tick: 0
    hold = _holds(path)[result['dispatch_hold']]
    assert hold['launch_status'] == 'launched' and hold['dispatch_id'] == 'c1'


def test_live_owner_keeps_visible_incident_and_is_never_replaced(exited_claude_owner, no_game):
    path, calls, request, resumes, status = exited_claude_owner
    status['value'] = 'running'
    key = _report(path)['dispatch_hold']
    assert _holds(path)[key]['launch_status'] == 'owner_active' and _holds(path)[key]['retry_after'] is None
    for _ in range(3):
        assert _tick(path)['dispatched'] == 0
    assert not resumes and len(calls) == 1 and _holds(path)[key]['launch_status'] == 'owner_active'
    status['value'] = 'failed'          # the owner exited unaccepted; its incident is still pending
    assert _tick(path)['dispatched'] == 1
    assert [r[0] for r in resumes] == ['c1'] and len(calls) == 1


def test_unverified_ownership_fails_closed_and_stays_visible(exited_claude_owner, no_game):
    path, calls, request, resumes, _ = exited_claude_owner
    state = assistance.read_ai_assistance_status(scheduler_settings_path=path)
    assistance.write_data_annotation_json(path.with_name('ai_assistance.json'),
                                          {**state, 'ownership_check_status': 'failed'})
    key = _report(path)['dispatch_hold']
    assert _holds(path)[key]['launch_status'] == 'owner_unverified'
    assert _tick(path)['dispatched'] == 0 and not resumes and len(calls) == 1
    assert _holds(path)[key]['launch_status'] == 'owner_unverified'


def test_live_execution_or_recent_ai_cell_blocks_retry(gate, monkeypatch, no_game):
    path, calls, request = gate
    def down(req):
        raise OSError('down')
    monkeypatch.setattr(assistance, 'escalate_to_codex', down)
    key = _report(path)['dispatch_hold']
    hold = _holds(path)[key]
    monkeypatch.setattr(assistance, 'escalate_to_codex', lambda req: calls.append(req) or _claude('n1'))
    later = hold['retry_after'] + 1
    busy = {**IDLE_KERNEL, 'execution_state': 'busy'}
    assert _tick(path, now=later, kernel_status=busy) == {'dispatched': 0, 'reason': 'kernel_busy', 'hold': key}
    recent = {**IDLE_KERNEL, 'last_cell_completed_at': later - 60}
    assert _tick(path, now=later, kernel_status=recent)['reason'] == 'recent_cell_activity'
    assert not calls and _holds(path)[key] == hold


def test_dispatch_failures_back_off_bounded_then_need_operator(gate, monkeypatch, no_game):
    path, calls, request = gate
    attempts = []
    def fail(req):
        attempts.append(req)
        raise OSError('CLI unavailable')
    monkeypatch.setattr(assistance, 'escalate_to_codex', fail)
    key = _report(path)['dispatch_hold']
    at = 0.0
    for _ in range(20):
        hold = _holds(path)[key]
        if hold['launch_status'] == 'needs_operator':
            break
        assert hold['launch_status'] in {'dispatch_failed', 'deferred'} and 'CLI unavailable' in hold['launch_error']
        if hold['launch_status'] == 'dispatch_failed' and hold.get('last_retry_at'):
            assert 0 < hold['retry_after'] - hold['last_retry_at'] <= 3600   # bounded, visible backoff
        at = hold['retry_after'] + 1
        _clock(monkeypatch, at)
        assert _tick(path)['dispatched'] == 0
    hold = _holds(path)[key]
    assert hold['launch_status'] == 'needs_operator' and hold['retry_after'] is None
    assert hold['dispatch_failures'] == assistance.MAX_DISPATCH_FAILURES == len(attempts)
    _clock(monkeypatch, at + 10 * 3600)
    assert _tick(path)['dispatched'] == 0 and len(attempts) == assistance.MAX_DISPATCH_FAILURES
    assert key in _holds(path)   # never forgotten: the incident stays active and readable


def test_retry_tick_never_changes_job_schedule_or_gate(gate, monkeypatch, no_game):
    import json
    path, calls, request = gate
    tasks = path.with_name('tasks.json')
    tasks.write_text(json.dumps([{'id': 'resource-ranking', 'next_time': '2026-10-20 05:00:00',
                                  'last_result': 'error', 'last_run_at': '2026-10-20 04:00:00'}]), encoding='utf-8')
    before = tasks.read_bytes()
    request('live maintainer')
    key = _report(path)['dispatch_hold']
    hold = _holds(path)[key]
    assert hold['launch_status'] == 'owner_active'
    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', lambda ident: SimpleNamespace(
        status='completed', error=None, codex_url=None))   # an accepted owner of another incident exited
    assert _tick(path)['dispatched'] == 1 and len(calls) == 2
    assert _tick(path)['dispatched'] == 0 and len(calls) == 2
    assert tasks.read_bytes() == before
    assert not control.read_scheduler_settings(scheduler_settings_path=path)['job_group_enabled']
    assert _holds(path)[key]['launch_status'] == 'launched'
