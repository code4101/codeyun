"""Provider/ownership/receipt contracts; the subprocess fixtures never emulate a game."""
import json
import os
import sys
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

import psutil
import pytest

from backend.core.agents import background as bg
from backend.core.agents.contracts import AgentTaskRequest


@pytest.fixture
def incident(tmp_path, monkeypatch):
    def root(identity, *, create=False):
        path = tmp_path / identity
        if create:
            path.mkdir(exist_ok=True)
        return path
    monkeypatch.setattr(bg, 'incident_root', root)
    monkeypatch.setattr(bg, 'incident_logs', lambda identity: root(identity, create=True))
    monkeypatch.setattr(bg, 'launch_worker', lambda identity: bg.read_record(identity))
    request = AgentTaskRequest('transport probe', 'verify fixture', 'read receipt', pair='cc', mode='smoke',
                               completion_criteria=('exact proof',))
    record = bg.dispatch_claude(request, workspace_dir=tmp_path)
    return record, root(record['dispatch_id'])


def receipt(**changes):
    return {'outcome': 'completed', 'criteria': [
        {'criterion': 'exact proof', 'satisfied': True, 'evidence': 'readback'}], 'summary': 'verified', **changes}


def test_completion_requires_every_exact_criterion_and_real_evidence():
    request = {'completion_criteria': ['exact proof']}
    assert bg.parse_receipt(json.dumps(receipt()), request)['complete']
    assert not bg.parse_receipt(json.dumps(receipt(criteria=[
        {'criterion': 'exact proof', 'satisfied': True, 'evidence': ''}])), request)['complete']
    with pytest.raises(ValueError):
        bg.parse_receipt(json.dumps(receipt(criteria=[])), request)
    with pytest.raises(ValueError):
        bg.parse_receipt(json.dumps(receipt(criteria=receipt()['criteria'] * 2)), request)


def test_dead_worker_cannot_report_running_or_completed(incident):
    record, root = incident
    state = bg.inspect_claude(record['dispatch_id'])
    assert state.status == 'failed'
    assert state.acceptance == 'pending'
    assert state.provider == 'claude'
    assert state.session_id == record['session_id']


def test_pid_reuse_is_not_a_writer(monkeypatch):
    class Process:
        def create_time(self): return 200
        def is_running(self): return True
    monkeypatch.setattr(bg.psutil, 'Process', lambda pid: Process())
    assert not bg.process_alive({'pid': 20, 'created': 100})
    assert bg.process_alive({'pid': 20, 'created': 200})


def test_live_owner_overrides_terminal_projection(incident):
    record, root = incident
    record.update(state='completed', acceptance='accepted', worker=bg.process_identity(os.getpid()))
    bg.write_record(root / 'record.json', record)
    assert bg.inspect_claude(record['dispatch_id']).status == 'running'


def test_explicit_evidence_recheck_never_replays_model_tools(incident, monkeypatch):
    record, root = incident
    record.update(state='failed', acceptance='verifier_failed', receipt={**receipt(), 'complete': True})
    bg.write_record(root / 'record.json', record)
    monkeypatch.setattr(bg, 'run_checks', lambda names, row: {'external': {'ok': True}})
    state = bg.verify_claude_acceptance(record['dispatch_id'])
    assert state.status == 'completed'
    assert state.acceptance == 'accepted'
    assert bg.read_record(record['dispatch_id'])['generation'] == 0


def test_resume_refuses_live_owner_and_unconfirmed_session(incident):
    record, root = incident
    with pytest.raises(RuntimeError, match='not confirmed'):
        bg.resume_claude(record['dispatch_id'], 'recover original only')
    record.update(session_created=True, child=bg.process_identity(os.getpid()))
    bg.write_record(root / 'record.json', record)
    with pytest.raises(RuntimeError, match='live owner'):
        bg.resume_claude(record['dispatch_id'], 'recover original only')


def test_resume_preserves_identity_and_rotates_log_generation(incident):
    record, root = incident
    record.update(session_created=True, state='failed')
    bg.write_record(root / 'record.json', record)
    resumed = bg.resume_claude(record['dispatch_id'], 'inspect previous effects; continue')
    assert resumed['session_id'] == record['session_id']
    assert resumed['generation'] == 1
    assert resumed['resume'] is True
    assert Path(resumed['stdout_path']).name == 'stdout-1.jsonl'
    assert Path(resumed['prompt_path']).read_text(encoding='utf-8').endswith('inspect previous effects; continue')


@pytest.fixture
def fanxiu_maintenance(incident, monkeypatch):
    """A terminal, unaccepted Fanxiu maintenance incident with a confirmed session."""
    record, root = incident
    request = AgentTaskRequest('fanxiu probe', 'verify mode contract', 'read receipt', pair='cc',
                               business='fanxiu', mode='maintenance', completion_criteria=('exact proof',))
    record.update(request=asdict(request), mode='maintenance', session_created=True, state='failed')
    bg.write_record(root / 'record.json', record)
    bg.write_record(root / 'request.json', asdict(request))
    return record, root


def _snapshot(root):
    return {p.name: p.read_bytes() for p in sorted(root.iterdir()) if p.is_file() and not p.name.endswith('.lock')}


def test_resume_without_mode_keeps_recorded_mode(fanxiu_maintenance):
    record, root = fanxiu_maintenance
    resumed = bg.resume_claude(record['dispatch_id'], 'continue offline')
    assert resumed['mode'] == 'maintenance' and resumed['request']['mode'] == 'maintenance'
    assert 'mode_history' not in resumed
    assert '不操作游戏' in Path(resumed['prompt_path']).read_text(encoding='utf-8')


def test_explicit_maintenance_to_repair_keeps_session_provider_and_adds_control_check(fanxiu_maintenance):
    record, root = fanxiu_maintenance
    resumed = bg.resume_claude(record['dispatch_id'], 'continue on the game', mode='repair')
    assert (resumed['session_id'], resumed['provider'], resumed['generation']) == (record['session_id'], 'claude', 1)
    assert resumed['mode'] == resumed['request']['mode'] == 'repair'
    assert 'fanxiu.engineering_control_returned' in resumed['request']['acceptance_checks']
    assert json.loads((root / 'request.json').read_text(encoding='utf-8')) == resumed['request']
    archived = json.loads((root / 'request-gen0.json').read_text(encoding='utf-8'))
    assert archived['mode'] == 'maintenance' and archived['business'] == 'fanxiu'
    assert resumed['mode_history'][-1]['from'] == 'maintenance' and resumed['mode_history'][-1]['to'] == 'repair'
    prompt = Path(resumed['prompt_path']).read_text(encoding='utf-8')
    assert '不操作游戏' not in prompt and 'job_verifications' in prompt


@pytest.mark.parametrize('case', ['invalid', 'live', 'accepted', 'unconfirmed'])
def test_refused_mode_transition_has_no_side_effects(fanxiu_maintenance, case):
    record, root = fanxiu_maintenance
    if case == 'live':
        record.update(child=bg.process_identity(os.getpid()))
    elif case == 'accepted':
        record.update(acceptance='accepted')
    elif case == 'unconfirmed':
        record.update(session_created=False)
    bg.write_record(root / 'record.json', record)
    before = _snapshot(root)
    with pytest.raises((ValueError, RuntimeError)):
        bg.resume_claude(record['dispatch_id'], 'continue', mode='bogus' if case == 'invalid' else 'repair')
    assert _snapshot(root) == before


def test_dispatch_api_rejects_mode_without_durable_record(monkeypatch, tmp_path):
    from backend.core.agents import dispatch
    monkeypatch.setattr(bg, 'incident_root', lambda identity, create=False: tmp_path / identity)
    with pytest.raises(ValueError, match='requires a durable local incident record'):
        dispatch.resume_agent_dispatch('0' * 32, 'continue', mode='repair')


@pytest.mark.parametrize('failure', ['', 'permission', 'identity', 'verifier', 'missing'])
def test_worker_checks_transport_identity_permissions_and_engineering_receipt(incident, monkeypatch, failure):
    from backend.core.agents import cli
    record, root = incident
    monkeypatch.setattr(cli, 'bootstrap', lambda config: None)
    init = {'type': 'system', 'subtype': 'init', 'session_id': record['session_id']}
    final = {'type': 'result', 'session_id': record['session_id'], 'result': json.dumps(receipt())}
    if failure == 'permission': final['permission_denials'] = [{'tool_name': 'Edit'}]
    if failure == 'identity': init['session_id'] = 'another-session'
    if failure == 'missing': final['result'] = '{}'
    payload = '\n'.join(map(json.dumps, [init, final]))
    monkeypatch.setattr(cli, 'cli_command', lambda driver, args, env: (
        [sys.executable, '-c', 'import sys; sys.stdin.read(); print(' + repr(payload) + ')'], env))
    monkeypatch.setattr(bg, 'run_checks', lambda names, row: {'external': {'ok': failure != 'verifier'}})
    bg.run_worker(record['dispatch_id'])
    state = bg.read_record(record['dispatch_id'])
    assert state['state'] == ('failed' if failure else 'completed')
    assert state['acceptance'] == {
        '': 'accepted', 'verifier': 'verifier_failed', 'missing': 'receipt_incomplete',
        'permission': 'transport_failed', 'identity': 'transport_failed'}[failure]
    assert state['session_id'] == record['session_id']


def test_incident_ids_cannot_escape_directory():
    for value in ('', '../other', 'a' * 31, 'G' * 32):
        with pytest.raises(ValueError):
            bg.incident_root(value)


def test_worker_finalizes_while_descendant_still_holds_stdout(incident, monkeypatch):
    """Real subprocess regression: a surviving output holder must not hold ownership."""
    from backend.core.agents import cli
    record, root = incident
    marker = root / 'descendant.json'
    init = {'type': 'system', 'subtype': 'init', 'session_id': record['session_id']}
    final = {'type': 'result', 'session_id': record['session_id'], 'result': json.dumps(receipt())}
    script = (
        'import subprocess,sys,json,psutil; '
        'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"], '
        'stdout=sys.stdout, stderr=sys.stderr, '
        'creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)); '
        f'open({str(marker)!r},"w").write(json.dumps({{"pid":p.pid,"created":psutil.Process(p.pid).create_time()}})); '
        f'print({json.dumps(init)!r},flush=True); print({json.dumps(final)!r},flush=True)'
    )
    monkeypatch.setattr(cli, 'bootstrap', lambda config: None)
    monkeypatch.setattr(cli, 'cli_command', lambda driver, args, env: ([sys.executable, '-c', script], env))
    monkeypatch.setattr(bg, 'run_checks', lambda names, row: {})
    started = time.monotonic()
    try:
        bg.run_worker(record['dispatch_id'])
        assert time.monotonic() - started < 10
        assert bg.read_record(record['dispatch_id'])['acceptance'] == 'accepted'
        assert bg.process_alive(json.loads(marker.read_text()))
    finally:
        if marker.exists():
            identity = json.loads(marker.read_text())
            if bg.process_alive(identity):
                process = psutil.Process(identity['pid'])
                process.terminate()
                process.wait(timeout=10)


def test_recovery_refuses_live_cli_and_changed_generation(incident):
    record, root = incident
    record.update(state='running', child=bg.process_identity(os.getpid()))
    bg.write_record(root / 'record.json', record)
    with pytest.raises(RuntimeError, match='Generation changed'):
        bg.recover_stalled_claude(record['dispatch_id'], expected_generation=999, reason='test')
    with pytest.raises(RuntimeError, match='CLI writer is live'):
        bg.recover_stalled_claude(record['dispatch_id'], expected_generation=0, reason='test')


def test_file_stream_preserves_split_utf8_and_skips_previous_bytes(tmp_path):
    path = tmp_path / 'events.jsonl'
    path.write_bytes(b'previous invocation\n')
    offset = path.stat().st_size
    script = ('import os,time; os.write(1,b"\\xe6"); time.sleep(.15); '
              'os.write(1,b"\\xb1\\x89\\nfinal-without-newline")')
    with path.open('ab', buffering=0) as output:
        process = subprocess.Popen([sys.executable, '-c', script], stdout=output,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert list(bg.follow_process_log(process, path, start_offset=offset)) == ['汉', 'final-without-newline']
        assert process.wait(timeout=10) == 0


def test_file_stream_drains_bytes_written_between_empty_read_and_exit(tmp_path):
    path = tmp_path / 'events.jsonl'
    path.write_bytes(b'')

    class ExitsAfterFinalWrite:
        """poll() appends the final result exactly when exit is first observed."""
        def poll(self):
            with path.open('ab') as late:
                late.write(b'{"type":"result"}\n')
            return 0

    assert list(bg.follow_process_log(ExitsAfterFinalWrite(), path)) == ['{"type":"result"}']


def test_explicit_recovery_reaps_stranded_worker_without_replay(incident):
    record, root = incident
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    old = subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=flags)
    child_identity = bg.process_identity(old.pid)
    old.wait(timeout=10)
    worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], creationflags=flags)
    try:
        record.update(state='running', worker=bg.process_identity(worker.pid), child=child_identity)
        bg.write_record(root / 'record.json', record)
        state = bg.recover_stalled_claude(record['dispatch_id'], expected_generation=0,
                                        reason='confirmed CLI exit, output holder remains')
        assert state.status == 'failed' and state.acceptance == 'transport_failed'
        assert not bg.process_alive(record['worker'])
        saved = bg.read_record(record['dispatch_id'])
        assert saved['session_id'] == record['session_id'] and saved['generation'] == 0
        # On Windows the no-window launcher can exit itself when its child
        # is reaped, so the wrapper need not require a separate termination.
        assert saved['recovery']['stopped']
        assert all(not bg.process_alive(item) for item in saved['recovery']['stopped'])
    finally:
        if worker.poll() is None:
            worker.terminate()
        worker.wait(timeout=10)


class FakeProcess:
    """Mocked OS table: pid -> create time; no real process is ever signalled."""
    table, tree, signals = {}, {}, []

    def __init__(self, pid):
        if pid not in self.table:
            raise psutil.NoSuchProcess(pid)
        self.pid = pid

    def create_time(self): return self.table[self.pid]
    def is_running(self): return self.pid in self.table
    def children(self, recursive=False): return [FakeProcess(p) for p in self.tree.get(self.pid, [])]
    def terminate(self):
        self.signals.append(self.pid)
        self.table.pop(self.pid, None)
    def wait(self, timeout=None): return 0


@pytest.fixture
def fake_os(monkeypatch):
    FakeProcess.table = {10: 1.0, 11: 2.0, 12: 3.0, 20: 4.0, 99: 5.0}
    FakeProcess.tree = {10: [11, 12]}  # 99 is unrelated and must survive
    FakeProcess.signals = []
    monkeypatch.setattr(bg.psutil, 'Process', FakeProcess)
    return FakeProcess


def live_cancel_record(incident, **changes):
    record, root = incident
    record.update(state='running', child={'pid': 10, 'created': 1.0}, worker={'pid': 20, 'created': 4.0},
                  session_created=True, receipt={'kept': True})
    record.update(changes)
    bg.write_record(root / 'record.json', record)
    return record, root


def test_cancel_stops_exact_child_tree_and_worker_preserving_evidence(incident, fake_os):
    record, root = live_cancel_record(incident)
    before = sorted(p.name for p in root.iterdir())
    state = bg.cancel_claude(record['dispatch_id'], expected_generation=0, reason='human authorized replacement')
    assert fake_os.signals == [12, 11, 10, 20]  # descendants first, writer worker last
    assert 99 in fake_os.table
    assert state.status == 'failed' and state.acceptance == 'transport_failed'
    saved = bg.read_record(record['dispatch_id'])
    assert [x['pid'] for x in saved['cancellation']['stopped']] == [12, 11, 10, 20]
    assert saved['session_id'] == record['session_id'] and saved['generation'] == 0
    assert saved['receipt'] == {'kept': True} and saved['session_created']
    assert sorted(p.name for p in root.iterdir()) == before
    # Repeat is a no-op, never a new signal or audit.
    bg.cancel_claude(record['dispatch_id'], expected_generation=0, reason='again')
    assert fake_os.signals == [12, 11, 10, 20]
    assert bg.read_record(record['dispatch_id'])['cancellation'] == saved['cancellation']


@pytest.mark.parametrize('case', ['generation', 'pid_reused', 'missing_child', 'missing_worker', 'dead_child'])
def test_cancel_refuses_without_exact_identity_or_generation(incident, fake_os, case):
    changes = {'pid_reused': {'child': {'pid': 10, 'created': 9.0}},
               'missing_child': {'child': {}}, 'missing_worker': {'worker': {}},
               'dead_child': {'child': {'pid': 77, 'created': 1.0}}}.get(case, {})
    record, root = live_cancel_record(incident, **changes)
    before = (root / 'record.json').read_text(encoding='utf-8')
    with pytest.raises(RuntimeError):
        bg.cancel_claude(record['dispatch_id'], expected_generation=5 if case == 'generation' else 0, reason='x')
    assert fake_os.signals == []
    assert (root / 'record.json').read_text(encoding='utf-8') == before


def test_cancel_terminal_record_is_noop_but_refuses_live_terminal_owner(incident, fake_os):
    record, root = live_cancel_record(incident, state='completed', acceptance='accepted',
                                      child={'pid': 77, 'created': 1.0}, worker={'pid': 78, 'created': 1.0})
    assert bg.cancel_claude(record['dispatch_id'], expected_generation=0, reason='x').status == 'completed'
    assert bg.read_record(record['dispatch_id'])['acceptance'] == 'accepted'
    record.update(child={'pid': 10, 'created': 1.0})
    bg.write_record(root / 'record.json', record)
    with pytest.raises(RuntimeError, match='live owner'):
        bg.cancel_claude(record['dispatch_id'], expected_generation=0, reason='x')
    assert fake_os.signals == []
    with pytest.raises(ValueError, match='reason'):
        bg.cancel_claude(record['dispatch_id'], expected_generation=0, reason=' ')


def test_cancel_api_requires_durable_local_record(monkeypatch, tmp_path):
    from backend.core.agents import dispatch
    monkeypatch.setattr(bg, 'incident_root', lambda identity, create=False: tmp_path / identity)
    with pytest.raises(ValueError, match='durable local'):
        dispatch.cancel_agent_dispatch('a' * 32, expected_generation=0, reason='x')


def test_local_route_failure_returns_to_main_without_provider_fallback(monkeypatch):
    from types import SimpleNamespace
    from backend.core.ai import chat
    from backend.core.messaging.wechat_message_routing import classify_local
    metadata = []
    service = SimpleNamespace(store=SimpleNamespace(hook=lambda key: {'summary': ''},
                              set_meta=lambda *args: metadata.append(args)))
    calls = []
    def unavailable(**kwargs):
        calls.append(kwargs['provider_id'])
        raise TimeoutError()
    monkeypatch.setattr(chat, 'chat_with_provider', unavailable)
    assert classify_local(service, {'key': 'fx', 'domain': 'fanxiu', 'account_id': 'bot'},
                          {'seq': 3, 'sender_id': 'user'}, [])['action'] == 'defer'
    assert calls == ['ollama']
    assert metadata
