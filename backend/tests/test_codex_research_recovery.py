"""Real TEMP recovery ledgers with mocked public transports, never model work."""
import copy
import json
from pathlib import Path

import pytest

from backend.core.codex import research_recovery as recovery


@pytest.fixture
def context(monkeypatch, tmp_path):
    goal = {'threadId': 'original', 'objective': 'all original objectives',
            'createdAt': 1, 'updatedAt': 2, 'status': 'active'}
    turn = {'id': 'failure-0', 'status': 'failed'}
    snapshot = {'thread_id': 'original', 'source_errors': [],
        'desktop': {'available': True, 'writer': 'inactive_observed',
            'latest_turn': turn, 'completed_tool_ids': []},
        'turn_history': {'available': True, 'turns': [copy.deepcopy(turn)]},
        'goal': {'available': True, 'value': goal}}
    clock = [100.0]
    monkeypatch.setattr(recovery.time, 'time', lambda: clock[0])
    monkeypatch.setattr(recovery, 'read_codex_research_snapshot', lambda *a, **kw: copy.deepcopy(snapshot))
    launches = []
    def launch(thread_id, **kw):
        launches.append((thread_id, kw))
        kw['on_start']('recovery-' + str(len(launches)))
        raise RuntimeError('remote compact stream disconnected')
    monkeypatch.setattr(recovery, 'run_codex_research_recovery_turn', launch)
    def call(dry_run=False):
        return recovery.recover_codex_research_once('original', owner_authorization='owner seq1979',
            prompt='continue all original work within original permissions',
            state_dir=tmp_path, dry_run=dry_run)
    def advance(turn_id, status='failed', tools=None):
        row = {'id': turn_id, 'status': status}
        snapshot['desktop']['latest_turn'] = row
        snapshot['desktop']['completed_tool_ids'] = tools or []
        snapshot['turn_history']['turns'] = [copy.deepcopy(row), copy.deepcopy(turn)]
    return snapshot, clock, launches, call, advance


def test_two_attempt_origin_episode_dedup_backoff_and_single_manual_record(context):
    snapshot, clock, launches, call, advance = context
    first = call()
    assert first['status'] == 'awaiting_outcome'
    origin = first['episode']['id']
    advance('recovery-1')
    for _ in range(3): assert call()['status'] == 'backoff'
    assert len(launches) == 1
    clock[0] = 131
    second = call()
    assert second['episode']['id'] == origin and len(second['episode']['attempts']) == 2
    advance('recovery-2')
    clock[0] = 192
    third = call()
    assert third['status'] == 'manual_review' and len(launches) == 2
    receipt = third['episode']['manual_intervention']
    for _ in range(3): assert call()['episode']['manual_intervention'] == receipt
    assert len(launches) == 2


@pytest.mark.parametrize('kind', ['success', 'tool'])
def test_new_progress_resolves_old_failure_and_resets_episode(context, kind):
    snapshot, clock, launches, call, advance = context
    first = call()
    advance('recovery-1', 'completed' if kind == 'success' else 'failed',
            [] if kind == 'success' else ['actual-tool-1'])
    clock[0] = 200
    if kind == 'success':
        assert call()['status'] == 'no_latest_failure'
        advance('new-independent-failure')
    result = call(dry_run=True)
    assert result['status'] == 'would_recover'
    assert result['episode']['id'] != first['episode']['id']
    assert result['episode']['attempts'] == []
    assert len(launches) == 1


def test_historical_failure_with_new_success_never_launches(context):
    _, _, launches, call, advance = context
    advance('new-success', 'completed')
    assert call()['status'] == 'no_latest_failure'
    assert launches == []


@pytest.mark.parametrize('condition', ['active_writer', 'unknown_writer', 'read_error',
    'wrong_thread', 'wrong_goal', 'missing_goal', 'missing_created', 'history_mismatch',
    'blocked', 'paused', 'complete', 'budgetLimited', 'usageLimited', 'unknown'])
def test_admission_rejections_never_launch_or_change_goal(context, condition):
    snapshot, _, launches, call, _ = context
    if condition in {'active_writer', 'unknown_writer'}:
        snapshot['desktop']['writer'] = 'active' if condition == 'active_writer' else 'unknown'
    elif condition == 'read_error': snapshot['source_errors'] = [{'source': 'goal', 'error': 'unavailable'}]
    elif condition == 'wrong_thread': snapshot['thread_id'] = 'other'
    elif condition == 'wrong_goal': snapshot['goal']['value']['threadId'] = 'other'
    elif condition == 'missing_goal': snapshot['goal']['value'] = None
    elif condition == 'missing_created': snapshot['goal']['value']['createdAt'] = None
    elif condition == 'history_mismatch': snapshot['turn_history']['turns'][0]['id'] = 'other'
    else: snapshot['goal']['value']['status'] = condition
    original = copy.deepcopy(snapshot['goal'])
    assert call()['status'] == 'rejected'
    assert snapshot['goal'] == original and launches == []


def test_ack_unknown_is_durable_and_never_replayed(context, monkeypatch):
    _, _, launches, call, _ = context
    def lost(*a, **kw):
        launches.append(a)
        raise TimeoutError('acknowledgement lost')
    monkeypatch.setattr(recovery, 'run_codex_research_recovery_turn', lost)
    first = call()
    assert first['status'] == 'manual_review'
    for _ in range(3): assert call()['status'] == 'manual_review'
    assert len(launches) == 1


def test_pre_admission_writer_race_does_not_spend_attempt(context, monkeypatch):
    _, _, launches, call, _ = context
    def race(*a, **kw):
        raise recovery.ResearchRecoveryAdmissionRejected('already has an active writer')
    monkeypatch.setattr(recovery, 'run_codex_research_recovery_turn', race)
    result = call()
    assert result['status'] == 'rejected' and result['episode']['attempts'] == []
    assert launches == []


def test_completed_transport_tombstone_prevents_stale_failed_replay(context, monkeypatch):
    _, _, launches, call, _ = context
    def done(*a, **kw):
        launches.append(a)
        kw['on_start']('new-completed')
    monkeypatch.setattr(recovery, 'run_codex_research_recovery_turn', done)
    assert call()['status'] == 'turn_completed'
    assert call()['status'] == 'no_latest_failure'
    assert len(launches) == 1


def test_crashed_claim_and_corrupt_ledger_never_replay(context):
    _, _, launches, call, _ = context
    result = call(dry_run=True)
    path = Path(result['evidence_path'])
    ledger = json.loads(path.read_text())
    ledger['episode']['status'] = 'attempting'
    path.write_text(json.dumps(ledger))
    assert call()['status'] == 'manual_review'
    path.write_text('null')
    with pytest.raises(ValueError, match='Invalid research recovery ledger'):
        call()
    assert path.read_text() == 'null' and launches == []


@pytest.mark.parametrize('race', ['none', 'writer', 'turn', 'goal', 'goal_read'])
def test_native_provider_acquires_writer_then_rechecks_exact_failure_and_goal(monkeypatch, race):
    from backend.core.codex import wechat_agent
    calls = []
    goal = {'threadId': 'original', 'objective': 'all goals', 'createdAt': 1, 'status': 'active'}
    identity = recovery.research_goal_identity(goal)
    class Client:
        def start(self): calls.append('start')
        def close(self): calls.append('close')
        def rpc(self, method, params):
            calls.append((method, params))
            assert params['threadId'] == 'original'
            if method == 'thread/resume':
                if race == 'writer': raise RuntimeError('already has an active writer')
                return {'thread': {'id': 'original'}}
            if method == 'thread/turns/list':
                return {'data': [{'id': 'failure-0' if race != 'turn' else 'new-success', 'status': 'failed'}]}
            if race == 'goal_read': raise ConnectionError('goal read failed')
            return {'goal': {**goal, 'status': 'blocked' if race == 'goal' else 'active'}}
        def run(self, thread_id, prompt, hard_interrupt, stop_event, on_start, **kw):
            kw['before_start']()
            calls.append('run')
            assert thread_id == 'original' and kw['raw_text'] is True
            on_start('recovery-1')
            return {'interrupted': False}
    monkeypatch.setattr(wechat_agent, 'CodexWechatClient', Client)
    starts = []
    if race == 'none':
        recovery.run_codex_research_recovery_turn('original', failed_turn_id='failure-0',
            goal_identity=identity, prompt='authorized', on_start=starts.append, on_tool=lambda *a: None)
        assert starts == ['recovery-1'] and calls[-2:] == ['run', 'close']
        assert [c[0] for c in calls if isinstance(c, tuple)] == [
            'thread/resume', 'thread/turns/list', 'thread/goal/get']
    else:
        with pytest.raises(recovery.ResearchRecoveryAdmissionRejected):
            recovery.run_codex_research_recovery_turn('original', failed_turn_id='failure-0',
                goal_identity=identity, prompt='authorized', on_start=starts.append, on_tool=lambda *a: None)
        assert starts == [] and 'run' not in calls and calls[-1] == 'close'


def test_real_client_admission_hook_runs_after_defaults_before_turn_start(monkeypatch):
    from backend.core.codex.wechat_agent import CodexWechatClient
    client = CodexWechatClient()
    order = []
    monkeypatch.setattr(client, 'current_defaults', lambda: order.append('defaults') or {'model': 'original'})
    monkeypatch.setattr(client, 'rpc', lambda *a, **kw: pytest.fail('rejected admission must not submit'))
    def reject():
        order.append('admission')
        raise recovery.ResearchRecoveryAdmissionRejected('Goal changed immediately before submit')
    with pytest.raises(recovery.ResearchRecoveryAdmissionRejected):
        client.run('original', 'prompt', lambda: False, None, lambda _: None,
            raw_text=True, before_start=reject)
    assert order == ['defaults', 'admission']


def test_thread_lock_is_held_during_transport_claim(context, monkeypatch):
    from filelock import FileLock, Timeout
    _, _, _, call, _ = context
    dry = call(dry_run=True)
    lock_path = Path(dry['evidence_path']).with_suffix('.lock')
    def launch(*a, **kw):
        with pytest.raises(Timeout):
            with FileLock(str(lock_path), timeout=0): pass
        kw['on_start']('completed-recovery')
    monkeypatch.setattr(recovery, 'run_codex_research_recovery_turn', launch)
    assert call()['status'] == 'turn_completed'


def test_public_missing_evidence_read_creates_nothing(tmp_path):
    directory = tmp_path / 'absent'
    result = recovery.read_codex_research_recovery('original', state_dir=directory)
    assert not result['found'] and result['episode'] is None
    assert not directory.exists()


def test_known_native_failure_persists_error_but_keeps_backoff_gate(context, monkeypatch):
    from backend.core.codex.turn_errors import CodexTurnFailed
    snapshot, clock, launches, call, advance = context
    error = {'message': 'remote compact failed', 'codexErrorInfo': {
        'ResponseStreamDisconnected': {'httpStatusCode': 502}}}
    def launch(thread_id, **kw):
        launches.append(thread_id)
        kw['on_start']('recovery-1')
        raise CodexTurnFailed(thread_id, 'recovery-1', error)
    monkeypatch.setattr(recovery, 'run_codex_research_recovery_turn', launch)
    result = call()
    assert result['status'] == 'native_turn_failed'
    attempt = result['episode']['attempts'][0]
    assert attempt['status'] == 'failed' and attempt['native_error'] == error
    assert attempt['native_terminal_status'] == 'failed'
    advance('recovery-1')
    assert call()['status'] == 'backoff'
    assert len(launches) == 1
    snapshot['goal']['value']['status'] = 'blocked'
    clock[0] = 140
    assert call()['status'] == 'rejected'
    assert len(launches) == 1


def test_foreign_failure_receipt_cannot_confirm_owned_outcome(context, monkeypatch):
    from backend.core.codex.turn_errors import CodexTurnFailed
    snapshot, clock, launches, call, advance = context
    def launch(thread_id, **kw):
        kw['on_start']('recovery-1')
        raise CodexTurnFailed('foreign-thread', 'recovery-1', {'message': 'failed'})
    monkeypatch.setattr(recovery, 'run_codex_research_recovery_turn', launch)
    result = call()
    assert result['status'] == 'awaiting_outcome'
    attempt = result['episode']['attempts'][0]
    assert attempt['status'] == 'outcome_unknown'
    assert 'native_error' not in attempt


def test_new_tool_progress_preserves_current_failed_terminal(context, monkeypatch, tmp_path):
    from backend.core.codex.turn_errors import CodexTurnFailed
    snapshot, clock, launches, call, advance = context
    error = {'message': 'compact failed after tool work'}
    def launch(thread_id, **kw):
        kw['on_start']('recovery-1')
        kw['on_tool']('recovery-1', 'real-completed-tool-id')
        raise CodexTurnFailed(thread_id, 'recovery-1', error)
    monkeypatch.setattr(recovery, 'run_codex_research_recovery_turn', launch)
    result = call()
    assert result['status'] == 'new_tool_progress'
    assert result['native_terminal_status'] == 'failed' and result['native_error'] == error
    assert result['failed_turn_id'] == 'recovery-1'
    assert 'current native turn failed' in result['reason']
    ledger = recovery.read_codex_research_recovery('original', state_dir=tmp_path)
    assert ledger['episode'] is None
    closed = ledger['closed'][-1]
    assert closed['resolved_tool_ids'] == ['real-completed-tool-id']
    assert closed['attempts'][-1]['native_terminal_status'] == 'failed'
    assert closed['attempts'][-1]['native_error'] == error
