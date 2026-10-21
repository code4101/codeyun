"""Incident-owner binding of take_ai_control after a runtime error closed the gate.

Pure ownership contract: real settings/hold files, the dispatch provider's
public inspect result and OS process identities; no game, Cell or Runtime.
"""
import json
import threading
from types import SimpleNamespace

import psutil
import pytest

from backend.core.agents import dispatch as agents_dispatch
from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control

HOLD = control.runtime_error_hold_key('job-a', 'attempt-1')
DISPATCH = 'a' * 32


def ancestor_identity():
    parent = psutil.Process().parent()
    return {'pid': parent.pid, 'created': parent.create_time()}


@pytest.fixture
def incident(monkeypatch, tmp_path):
    settings, world = tmp_path / 'settings.json', tmp_path / 'world_facts.json'
    control.write_scheduler_settings({'job_group_enabled': False}, scheduler_settings_path=settings)
    control.record_world_discovery(HOLD, {
        'status': 'active', 'kind': 'runtime_error', 'engineering_origin': True,
        'launch_status': 'launched', 'dispatch_id': DISPATCH}, path=world)
    (tmp_path / 'ai_assistance.json').write_text(json.dumps(
        {'status': 'dispatched', 'dispatch': {'dispatch_id': DISPATCH, 'session_id': 's-1'}}), encoding='utf-8')
    owner = SimpleNamespace(status='running', session_id='s-1',
                            progress_diagnostics={'child': ancestor_identity()})
    monkeypatch.setattr(agents_dispatch, 'inspect_agent_dispatch', lambda dispatch_id: owner)
    kernel = {'running': False, 'kernel': {'execution_state': 'idle'}}
    monkeypatch.setattr(control, 'kernel_scheduler_status', lambda **kw: dict(kernel))
    monkeypatch.setattr(control, 'read_scheduler_tasks', lambda **kw: [])
    monkeypatch.setattr(control, 'reconcile_stale_scheduler_attempts', lambda tasks, **kw: False)

    def take(**kw):
        return control.take_ai_control('entry-a', only_if_idle=True, scheduler_settings_path=settings,
                                       world_facts_path=world, scheduler_state_path=tmp_path / 'state.json', **kw)

    def hold():
        return control.read_world_facts(world)['discoveries'][HOLD]
    return SimpleNamespace(take=take, hold=hold, owner=owner, kernel=kernel, world=world, settings=settings)


def test_verified_incident_owner_acquires_once_and_is_idempotent(incident):
    first = incident.take(incident_hold_key=HOLD)
    assert (first['ai_control_acquired'], first['handoff_ready']) == (True, True)
    assert first['dispatch_revoked_by_this_call'] is False
    assert first['ai_control_reason'] == 'incident_owner_claimed'
    recorded = incident.hold()['ai_control_owner']
    assert (recorded['dispatch_id'], recorded['session_id']) == (DISPATCH, 's-1')
    again = incident.take(incident_hold_key=HOLD)
    assert again['ai_control_acquired'] is True
    assert again['ai_control_reason'] == 'incident_owner_already_holds'
    assert incident.hold()['ai_control_owner'] == recorded
    # Engineering dispatch stays closed; nothing reopened it.
    assert control.read_scheduler_settings(scheduler_settings_path=incident.settings)['job_group_enabled'] is False


def test_closed_gate_without_incident_identity_keeps_old_semantics(incident):
    result = incident.take()
    assert result['ai_control_acquired'] is False
    assert result['ai_control_reason'] == 'dispatch_already_revoked'
    assert 'ai_control_owner' not in incident.hold()


def test_native_session_is_bound_before_first_incident_claim(incident, monkeypatch):
    from backend.core.fanxiu.data_annotation import ai_assistance as assistance
    state_path = incident.settings.with_name('ai_assistance.json')
    state = json.loads(state_path.read_text(encoding='utf-8'))
    state['dispatch']['session_id'] = None
    state_path.write_text(json.dumps(state), encoding='utf-8')
    incident.owner.dispatch_id = DISPATCH
    incident.owner.error = None
    incident.owner.codex_url = None
    monkeypatch.setattr(assistance, 'inspect_codex_dispatch', lambda ident: incident.owner)
    result = incident.take(incident_hold_key=HOLD)
    assert (result['ai_control_acquired'], result['handoff_ready']) == (True, True)
    assert json.loads(state_path.read_text(encoding='utf-8'))['dispatch']['session_id'] == 's-1'


@pytest.mark.parametrize('mutate, reason', [
    (lambda i: setattr(i.owner, 'progress_diagnostics', {'child': {'pid': 4, 'created': 1.0}}),
     'caller_not_incident_owner'),
    (lambda i: setattr(i.owner, 'progress_diagnostics', {'child': {}}), 'caller_not_incident_owner'),
    (lambda i: setattr(i.owner, 'status', 'failed'), 'incident_owner_not_live'),
    (lambda i: setattr(i.owner, 'session_id', 's-other'), 'incident_session_mismatch'),
    (lambda i: control.record_world_discovery(HOLD, {**i.hold(), 'dispatch_id': 'b' * 32}, path=i.world),
     'incident_dispatch_superseded'),
    (lambda i: control.record_world_discovery(HOLD, {**i.hold(), 'launch_status': 'owner_active'}, path=i.world),
     'incident_dispatch_unbound'),
    (lambda i: control.record_world_discovery(HOLD, {**i.hold(), 'status': 'released'}, path=i.world),
     'incident_hold_not_active'),
    (lambda i: control.record_world_discovery(HOLD, {**i.hold(), 'engineering_origin': False}, path=i.world),
     'incident_hold_not_active'),
    (lambda i: control.record_world_discovery(HOLD, {**i.hold(), 'ai_control_owner': {
        'dispatch_id': 'c' * 32, 'session_id': 's-9', 'child': {}}}, path=i.world), 'foreign_ai_owner'),
])
def test_unproven_or_foreign_caller_fails_closed(incident, mutate, reason):
    mutate(incident)
    before = incident.hold()
    result = incident.take(incident_hold_key=HOLD)
    assert result['ai_control_acquired'] is False
    assert result['ai_control_reason'] == reason
    assert incident.hold() == before


def test_resumed_generation_takes_over_only_after_previous_child_exited(incident):
    me = psutil.Process()
    alive = {'pid': me.pid, 'created': me.create_time()}
    control.record_world_discovery(HOLD, {**incident.hold(), 'ai_control_owner': {
        'dispatch_id': DISPATCH, 'session_id': 's-1', 'child': alive}}, path=incident.world)
    assert incident.take(incident_hold_key=HOLD)['ai_control_reason'] == 'previous_owner_process_alive'
    control.record_world_discovery(HOLD, {**incident.hold(), 'ai_control_owner': {
        'dispatch_id': DISPATCH, 'session_id': 's-1', 'child': {**alive, 'created': 1.0}}}, path=incident.world)
    moved = incident.take(incident_hold_key=HOLD)
    assert (moved['ai_control_acquired'], moved['ai_control_reason']) == (True, 'incident_owner_claimed')
    assert incident.hold()['ai_control_owner']['child'] == ancestor_identity()


def test_unreadable_owner_fails_closed(incident, monkeypatch):
    def broken(dispatch_id):
        raise OSError('record unreadable')
    monkeypatch.setattr(agents_dispatch, 'inspect_agent_dispatch', broken)
    result = incident.take(incident_hold_key=HOLD)
    assert (result['ai_control_acquired'], result['ai_control_reason']) == (False, 'incident_owner_unverified')


def test_busy_kernel_refuses_before_any_ownership_claim(incident):
    incident.kernel['kernel'] = {'execution_state': 'busy'}
    result = incident.take(incident_hold_key=HOLD)
    assert (result['ai_control_acquired'], result['handoff_reason']) == (False, 'kernel_busy')
    assert 'ai_control_owner' not in incident.hold()


def test_incident_identity_cannot_request_interrupt_or_default_takeover(incident):
    with pytest.raises(ValueError):
        control.take_ai_control('entry-a', incident_hold_key=HOLD, scheduler_settings_path=incident.settings,
                                world_facts_path=incident.world)


def test_concurrent_claims_record_a_single_owner(incident):
    results = []
    threads = [threading.Thread(target=lambda: results.append(incident.take(incident_hold_key=HOLD)))
               for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(r['ai_control_acquired'] for r in results)
    assert sorted(r['ai_control_reason'] for r in results) == (
        ['incident_owner_already_holds'] * 3 + ['incident_owner_claimed'])
