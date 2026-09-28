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

    statuses = iter(['active', final_status])
    observed = []

    def read_goal(thread_id):
        observed.append(thread_id)
        return {'goal': {'status': next(statuses)}}

    monkeypatch.setattr(goal_worker, 'popen_service', launch)
    monkeypatch.setattr(goal_worker, 'read_codex_thread_goal', read_goal)
    if final_status == 'complete':
        assert goal_worker.run_goal_dispatch(spec) == 0
    else:
        with pytest.raises(RuntimeError, match='没有工具行动|不能续轮'):
            goal_worker.run_goal_dispatch(spec)
    assert commands == [['codex', 'exec', '--json', '-'],
                        ['codex', 'exec', '--json', 'resume', 'incident-1', '-']]
    assert observed == ['incident-1', 'incident-1']
