"""Persistent CLI admission contract; no real process or Goal mutations."""
import json
from types import SimpleNamespace

import pytest

from backend.core.codex import escalation, goal_worker


@pytest.fixture
def dispatch(monkeypatch, tmp_path):
    root = tmp_path / 'original-dispatch'
    root.mkdir()
    stdout = root / 'stdout.jsonl'
    stdout.write_text('{"type":"thread.started","thread_id":"incident-1"}\n')
    stderr = root / 'stderr.log'
    stderr.write_text('original failure')
    payload = dict(dispatch_id='original-dispatch', pid=12345, thread_id='incident-1',
        workspace_dir=str(tmp_path), request_path=str(root / 'request.json'),
        stdout_path=str(stdout), stderr_path=str(stderr), created_at='original-time')
    (root / 'dispatch.json').write_text(json.dumps(payload))
    (root / 'goal-worker.json').write_text(json.dumps(dict(
        command=['codex', 'exec', '--json', '-'], workspace_dir=str(tmp_path),
        prompt_path=str(root / 'old-prompt.md'))))
    monkeypatch.setattr(escalation, '_dispatch_root', lambda *a, **kw: root)
    monkeypatch.setattr(escalation.psutil, 'pid_exists', lambda _: False)
    launches = []
    monkeypatch.setattr(escalation, 'popen_service',
        lambda *a, **kw: launches.append((a, kw)) or SimpleNamespace(pid=54321))
    return root, payload, launches


@pytest.mark.parametrize('goal', [None, {}, {'status': 'active'},
    {'threadId': 'other', 'status': 'active'},
    *({'threadId': 'incident-1', 'status': status} for status in
      ['blocked', 'paused', 'complete', 'budgetLimited', 'usageLimited', 'unknown', None])])
def test_nonactive_or_unidentified_goal_preserves_dispatch(monkeypatch, dispatch, goal):
    root, _, launches = dispatch
    original = {p.name: p.read_bytes() for p in root.iterdir()}
    monkeypatch.setattr(goal_worker, 'read_codex_thread_goal', lambda _: {'goal': goal})
    with pytest.raises(RuntimeError, match='恢复拒绝'):
        escalation.resume_codex_dispatch('original-dispatch', 'resume original')
    assert not list(root.glob('resume-*'))
    assert launches == []
    assert {name: (root / name).read_bytes() for name in original} == original


def test_read_failure_preserves_dispatch(monkeypatch, dispatch):
    root, _, launches = dispatch
    original = (root / 'dispatch.json').read_bytes()
    def fail(_): raise ConnectionError('native read failed')
    monkeypatch.setattr(goal_worker, 'read_codex_thread_goal', fail)
    with pytest.raises(ConnectionError, match='native read failed'):
        escalation.resume_codex_dispatch('original-dispatch', 'resume original')
    assert launches == [] and not list(root.glob('resume-*'))
    assert (root / 'dispatch.json').read_bytes() == original


def test_log_identity_mismatch_rejected_before_goal_read(monkeypatch, dispatch):
    root, _, launches = dispatch
    (root / 'stdout.jsonl').write_text('{"type":"thread.started","thread_id":"other"}\n')
    original = (root / 'dispatch.json').read_bytes()
    monkeypatch.setattr(goal_worker, 'read_codex_thread_goal',
        lambda _: pytest.fail('mismatched original identity must not read Goal'))
    with pytest.raises(RuntimeError, match='原会话身份不匹配'):
        escalation.resume_codex_dispatch('original-dispatch', 'resume original')
    assert launches == [] and not list(root.glob('resume-*'))
    assert (root / 'dispatch.json').read_bytes() == original


@pytest.mark.parametrize('legacy_initial_receipt', [False, True])
def test_active_goal_resumes_original_thread(monkeypatch, dispatch, legacy_initial_receipt):
    root, payload, launches = dispatch
    if legacy_initial_receipt:
        payload['thread_id'] = None  # Original CLI receipt precedes thread.started.
        (root / 'dispatch.json').write_text(json.dumps(payload))
    original = (root / 'dispatch.json').read_bytes()
    reads = []
    def read(thread_id):
        reads.append(thread_id)
        return {'goal': {'threadId': thread_id, 'status': 'active'}}
    monkeypatch.setattr(goal_worker, 'read_codex_thread_goal', read)
    result = escalation.resume_codex_dispatch('original-dispatch', 'resume original')
    assert reads == ['incident-1']
    assert result.dispatch_id == 'original-dispatch' and result.thread_id == 'incident-1'
    assert len(launches) == 1
    attempt, = root.glob('resume-*')
    spec = json.loads((attempt / 'goal-worker.json').read_text())
    assert spec['resume_thread_id'] == 'incident-1'
    assert spec['command'] == ['codex', 'exec', '--json', '-']
    assert json.loads((attempt / 'previous-dispatch.json').read_text()) == json.loads(original)


def test_missing_original_thread_rejected(monkeypatch, dispatch):
    root, payload, launches = dispatch
    payload['thread_id'] = None
    (root / 'dispatch.json').write_text(json.dumps(payload))
    (root / 'stdout.jsonl').write_text('')
    original = (root / 'dispatch.json').read_bytes()
    monkeypatch.setattr(goal_worker, 'read_codex_thread_goal', lambda _: pytest.fail('missing identity'))
    with pytest.raises(RuntimeError, match='原会话身份'):
        escalation.resume_codex_dispatch('original-dispatch', 'resume original')
    assert launches == [] and not list(root.glob('resume-*'))
    assert (root / 'dispatch.json').read_bytes() == original


def test_live_worker_retains_gate(monkeypatch, dispatch):
    root, _, launches = dispatch
    monkeypatch.setattr(escalation.psutil, 'pid_exists', lambda _: True)
    monkeypatch.setattr(goal_worker, 'read_codex_thread_goal', lambda _: pytest.fail('live worker'))
    with pytest.raises(RuntimeError, match='原维修进程仍存活'):
        escalation.resume_codex_dispatch('original-dispatch', 'resume original')
    assert launches == [] and not list(root.glob('resume-*'))
