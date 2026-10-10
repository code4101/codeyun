"""Continuation protocol tests; no model or game calls."""
import io
import json

import pytest

from backend.core.codex import goal_worker


@pytest.mark.parametrize('final_status', ['complete', 'active', 'blocked'])
def test_worker_resumes_same_incident_and_stops_at_terminal(monkeypatch, tmp_path, final_status):
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('repair', encoding='utf-8')
    spec = tmp_path / 'worker.json'
    spec.write_text(json.dumps(dict(command=['codex', 'exec', '--json', '-'],
                                   prompt_path=str(prompt), workspace_dir=str(tmp_path))), encoding='utf-8')
    commands = []

    class Process:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO((json.dumps({'type': 'thread.started', 'thread_id': 'incident-1'}) + '\n').encode())

        def wait(self):
            return 0

        def poll(self):
            return 0

    def launch(command, **kwargs):
        commands.append(command)
        return Process()

    statuses = iter(['active', 'active', final_status])
    observed = []

    def read_goal(thread_id):
        observed.append(thread_id)
        return {'goal': {'threadId': thread_id, 'status': next(statuses)}}

    monkeypatch.setattr(goal_worker, 'popen_service', launch)
    monkeypatch.setattr(goal_worker, 'read_codex_thread_goal', read_goal)
    if final_status == 'complete':
        assert goal_worker.run_goal_dispatch(spec) == 0
    else:
        with pytest.raises(RuntimeError, match='没有工具行动|不能续轮'):
            goal_worker.run_goal_dispatch(spec)
    assert commands == [['codex', 'exec', '--json', '-'],
                        ['codex', 'exec', '--json', 'resume', 'incident-1', '-']]
    assert observed == ['incident-1', 'incident-1', 'incident-1']


@pytest.mark.parametrize('goal', [None, {}, {'threadId': 'other', 'status': 'active'},
    *({'threadId': 'incident-1', 'status': status} for status in
      ['blocked', 'paused', 'complete', 'budgetLimited', 'usageLimited', 'unknown', None])])
def test_worker_resume_rejects_before_launch(monkeypatch, tmp_path, goal):
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('resume', encoding='utf-8')
    spec = tmp_path / 'worker.json'
    spec.write_text(json.dumps(dict(command=['codex', 'exec', '--json', '-'],
        prompt_path=str(prompt), workspace_dir=str(tmp_path), resume_thread_id='incident-1')))
    monkeypatch.setattr(goal_worker, 'read_codex_thread_goal', lambda _: {'goal': goal})
    launches = []
    monkeypatch.setattr(goal_worker, 'popen_service', lambda *a, **kw: launches.append(a))
    with pytest.raises(RuntimeError, match='恢复拒绝'):
        goal_worker.run_goal_dispatch(spec)
    assert launches == []


def test_worker_rechecks_goal_between_turns(monkeypatch, tmp_path):
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('repair')
    spec = tmp_path / 'worker.json'
    spec.write_text(json.dumps(dict(command=['codex', 'exec', '--json', '-'],
        prompt_path=str(prompt), workspace_dir=str(tmp_path))))
    launches = []
    class Process:
        stdin = io.BytesIO()
        stdout = io.BytesIO(b'{"type":"thread.started","thread_id":"incident-1"}\n')
        def wait(self): return 0
        def poll(self): return 0
    monkeypatch.setattr(goal_worker, 'popen_service', lambda *a, **kw: launches.append(a) or Process())
    statuses = iter(['active', 'blocked'])
    monkeypatch.setattr(goal_worker, 'read_codex_thread_goal', lambda _: {
        'goal': {'threadId': 'incident-1', 'status': next(statuses)}})
    with pytest.raises(RuntimeError, match='恢复拒绝'):
        goal_worker.run_goal_dispatch(spec)
    assert len(launches) == 1


@pytest.mark.parametrize('mode', ['read_failure', 'active', 'missing_identity'])
def test_first_resumed_turn_admission(monkeypatch, tmp_path, mode):
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('resume')
    spec = tmp_path / 'worker.json'
    spec.write_text(json.dumps(dict(command=['codex', 'exec', '--json', '-'],
        prompt_path=str(prompt), workspace_dir=str(tmp_path),
        resume_thread_id=None if mode == 'missing_identity' else 'incident-1')))
    launches = []
    class Process:
        stdin = io.BytesIO()
        stdout = io.BytesIO(b'{"type":"thread.started","thread_id":"incident-1"}\n')
        def wait(self): return 0
        def poll(self): return 0
    monkeypatch.setattr(goal_worker, 'popen_service', lambda command, **kw:
        launches.append(command) or Process())
    statuses = iter(['active', 'complete'])
    def read(thread_id):
        if mode == 'read_failure': raise ConnectionError('native read failed')
        return {'goal': {'threadId': thread_id, 'status': next(statuses)}}
    monkeypatch.setattr(goal_worker, 'read_codex_thread_goal', read)
    if mode == 'active':
        assert goal_worker.run_goal_dispatch(spec) == 0
        assert launches == [['codex', 'exec', '--json', 'resume', 'incident-1', '-']]
    else:
        with pytest.raises((RuntimeError, ConnectionError)):
            goal_worker.run_goal_dispatch(spec)
        assert launches == []
