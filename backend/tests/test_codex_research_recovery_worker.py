"""Independent worker lifecycle with real TEMP files and controlled execution."""
import copy
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.codex import research_recovery_worker as worker


@pytest.fixture
def execution(monkeypatch, tmp_path):
    launches = []
    monkeypatch.setattr(worker, 'popen_service', lambda command, **kw:
        launches.append(command) or SimpleNamespace(pid=12345))
    monkeypatch.setattr(worker.psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 123.0))
    monkeypatch.setattr(worker, '_identity_alive', lambda *a: True)
    observation = {'thread_id': 'original', 'desktop': {'writer': 'inactive_observed',
        'latest_turn': {'id': 'failed-1', 'status': 'failed'}, 'completed_tool_ids': []},
        'goal': {'value': {'threadId': 'original', 'status': 'active'}}, 'source_errors': []}
    def dispatch():
        return worker.dispatch_codex_research_recovery('original', owner_authorization='explicit owner',
            prompt='retained complete scope', observation=observation, dry_run=True, state_dir=tmp_path)
    return tmp_path, launches, observation, dispatch


def test_long_execution_remains_one_generation_without_expiring_lease(execution, monkeypatch):
    directory, launches, observation, dispatch = execution
    entered, release = threading.Event(), threading.Event()
    def wait(*a, **kw):
        entered.set()
        assert release.wait(5)
        return {'status': 'would_recover', 'recovery_performed': False}
    monkeypatch.setattr(worker, 'recover_codex_research_once', wait)
    first = dispatch()
    path = Path(launches[0][-2])
    thread = threading.Thread(target=worker.run_codex_research_recovery_worker, args=(path, first['generation']))
    thread.start()
    assert entered.wait(2)
    try:
        for _ in range(3):
            assert dispatch()['status'] == 'running'
        observation['desktop']['latest_turn'] = {'id': 'different', 'status': 'failed'}
        assert dispatch()['generation'] == first['generation']
        assert len(launches) == 1
    finally:
        release.set(); thread.join(3)
    assert not thread.is_alive()
    result = worker.read_codex_research_recovery_worker('original', state_dir=directory)
    assert result['status'] == 'completed' and result['result']['status'] == 'would_recover'
    assert not result['recovery_performed']


def test_stop_before_admission_has_stopped_receipt_without_running_recovery(execution, monkeypatch):
    directory, launches, _, dispatch = execution
    first = dispatch()
    worker.request_codex_research_recovery_stop('original', generation=first['generation'], state_dir=directory)
    monkeypatch.setattr(worker, 'recover_codex_research_once', lambda *a, **kw: pytest.fail('stopped'))
    assert worker.run_codex_research_recovery_worker(Path(launches[0][-2]), first['generation']) == 0
    current = worker.read_codex_research_recovery_worker('original', state_dir=directory)
    assert current['status'] == 'stopped' and not current['recovery_performed']
    assert dispatch()['status'] == 'stopped' and len(launches) == 1


def test_worker_failure_not_reported_as_completed(execution, monkeypatch):
    directory, launches, _, dispatch = execution
    def fail(*a, **kw): raise ConnectionError('observer failure')
    monkeypatch.setattr(worker, 'recover_codex_research_once', fail)
    first = dispatch()
    assert worker.run_codex_research_recovery_worker(Path(launches[0][-2]), first['generation']) == 1
    current = worker.read_codex_research_recovery_worker('original', state_dir=directory)
    assert current['status'] == 'failed' and 'observer failure' in current['error']
    assert not current['recovery_performed'] and dispatch()['status'] == 'failed'
    assert len(launches) == 1


def test_crash_lost_worker_preserves_uncertainty_and_never_relaunches(execution, monkeypatch):
    directory, launches, _, dispatch = execution
    def crash(*a, **kw): raise SystemExit('crash before terminal receipt')
    monkeypatch.setattr(worker, 'recover_codex_research_once', crash)
    first = dispatch()
    with pytest.raises(SystemExit):
        worker.run_codex_research_recovery_worker(Path(launches[0][-2]), first['generation'])
    monkeypatch.setattr(worker, '_identity_alive', lambda *a: False)
    assert dispatch()['status'] == 'worker_lost'
    again = dispatch()
    assert again['status'] == 'uncertain' and len(launches) == 1
    manual = again['manual_intervention']
    assert dispatch()['manual_intervention'] == manual


def test_launch_ack_unknown_never_replayed(execution, monkeypatch):
    _, launches, _, dispatch = execution
    def lost(*a, **kw):
        launches.append(a)
        raise TimeoutError('launch outcome unknown')
    monkeypatch.setattr(worker, 'popen_service', lost)
    assert dispatch()['status'] == 'uncertain'
    assert dispatch()['status'] == 'uncertain' and len(launches) == 1


def test_sweep_observes_second_registration_while_recovery_waits(execution, monkeypatch):
    from backend.core.codex import research_supervision as supervision, research_recovery
    from backend.core.messaging.wechat_agent_store import AgentStore
    directory, launches, observation, _ = execution
    store = AgentStore(directory / 'provider.sqlite')
    entered, release = threading.Event(), threading.Event()
    observed = []
    def inspect(thread_id, **kw):
        observed.append(thread_id)
        snapshot = copy.deepcopy(observation)
        snapshot['thread_id'] = thread_id
        return {'snapshot': snapshot, 'deferred_failures': []}
    monkeypatch.setattr(supervision, 'inspect_codex_research', inspect)
    def wait(*a, **kw):
        entered.set(); assert release.wait(5)
        return {'status': 'would_recover', 'recovery_performed': False}
    monkeypatch.setattr(worker, 'recover_codex_research_once', wait)
    for thread_id in ['original', 'second']:
        supervision.register_codex_research(thread_id, store=store, notify=False)
    research_recovery.configure_codex_research_recovery('original', store=store,
        owner_authorization='explicit owner', prompt='scope', enabled=True, dry_run=True)
    first = supervision.inspect_registered_codex_research_once(store=store, state_dir=directory / 'patrol')
    claim = next(row for row in first['results'] if row['thread_id'] == 'original')['recovery']
    thread = threading.Thread(target=worker.run_codex_research_recovery_worker,
        args=(Path(launches[0][-2]), claim['generation']))
    thread.start(); assert entered.wait(2)
    try:
        registry = store.get_meta('codex_research_supervision_registry')
        for row in registry.values(): row.update(next_check_at=0, in_flight_until=0)
        store.set_meta('codex_research_supervision_registry', registry)
        before = len(observed)
        second = supervision.inspect_registered_codex_research_once(store=store, state_dir=directory / 'patrol')
        assert 'second' in observed[before:]
        current = next(row for row in second['results'] if row['thread_id'] == 'original')
        assert current['recovery']['status'] == 'running'
        assert current['recovery']['generation'] == claim['generation'] and len(launches) == 1
        assert not second['recovery_performed']
    finally:
        release.set(); thread.join(3)
    assert not thread.is_alive()


def test_stop_during_wait_is_not_a_completed_recovery(execution, monkeypatch):
    directory, launches, _, dispatch = execution
    entered, release = threading.Event(), threading.Event()
    def wait(*a, **kw):
        entered.set(); assert release.wait(5)
        assert kw['stop_event'].is_set()
        return {'status': 'interrupted', 'recovery_performed': True}
    monkeypatch.setattr(worker, 'recover_codex_research_once', wait)
    first = dispatch()
    thread = threading.Thread(target=worker.run_codex_research_recovery_worker,
        args=(Path(launches[0][-2]), first['generation']))
    thread.start(); assert entered.wait(2)
    try:
        receipt = worker.request_codex_research_recovery_stop('original',
            generation=first['generation'], state_dir=directory)
        assert not receipt['completed']
    finally:
        release.set(); thread.join(3)
    current = worker.read_codex_research_recovery_worker('original', state_dir=directory)
    assert current['status'] == 'stopped' and current['result']['status'] == 'interrupted'


def test_wall_deadline_is_not_extended_by_healthy_events(monkeypatch):
    from backend.core.codex import wechat_agent
    client = wechat_agent.CodexWechatClient()
    clock = [0]
    monkeypatch.setattr(wechat_agent.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(client, 'current_defaults', lambda: {'model': 'original-model'})
    calls = []
    def rpc(method, params):
        calls.append((method, params))
        return {'turn': {'id': 'owned-recovery'}}
    monkeypatch.setattr(client, 'rpc', rpc)
    class Events:
        def get(self, timeout):
            clock[0] += 6
            return {'method': 'item/completed', 'params': {'threadId': 'original',
                'turnId': 'owned-recovery', 'item': {'type': 'commandExecution', 'status': 'completed'}}}
    client.events = Events()
    with pytest.raises(TimeoutError, match='wall deadline'):
        client.run('original', 'prompt', lambda: False, threading.Event(), lambda _: None,
            raw_text=True, timeout=100, wall_timeout=10)
    assert clock[0] == 12
    assert calls[-1] == ('turn/interrupt', {'threadId': 'original', 'turnId': 'owned-recovery'})
