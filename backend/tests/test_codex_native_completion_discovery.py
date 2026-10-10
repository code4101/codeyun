"""Exact native metadata RPC and real TEMP append-only completion events."""
import json

import pytest

from backend.core.codex import app_server, sessions


def test_native_completion_discovery_ignores_unrelated_cache_and_truncated_tail(tmp_path, monkeypatch):
    rollout = tmp_path / 'native.jsonl'
    events = [
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'final', 'text': 'not completion'}},
        {'type': 'event_msg', 'timestamp': 'now', 'payload': {'type': 'task_complete', 'turn_id': 'turn-1', 'last_agent_message': 'completed'}},
        {'type': 'event_msg', 'payload': {'type': 'task_complete', 'last_agent_message': 'missing identity'}},
    ]
    rollout.write_text('\n'.join(map(json.dumps, events))+'\n{"type":', encoding='utf-8')
    def metadata(thread_id):
        assert thread_id == 'thread'
        return {'id': thread_id, 'path': str(rollout)}
    monkeypatch.setattr(app_server, 'read_codex_thread_metadata', metadata)
    def cache_forbidden(*a, **k):
        raise sessions.CodexCacheSourceChangedError('unrelated source continuously changing')
    monkeypatch.setattr(sessions, '_ensure_codex_text_cache', cache_forbidden)
    first = sessions.read_codex_completed_turns('thread')
    assert [r['turn_id'] for r in first] == ['turn-1']
    with rollout.open('a', encoding='utf-8') as out:
        out.write('\n'+json.dumps({'type': 'event_msg', 'payload': {
            'type': 'task_complete', 'turn_id': 'turn-2', 'last_agent_message': 'new completion'}})+'\n')
    assert [r['turn_id'] for r in sessions.read_codex_completed_turns('thread')] == ['turn-1', 'turn-2']


def test_metadata_uses_native_read_without_loading_turns(monkeypatch):
    calls = []
    def rpc(**kwargs):
        calls.append(kwargs)
        return {'thread': {'id': 'thread', 'path': 'native.jsonl'}}
    monkeypatch.setattr(app_server, '_read_codex_app_server', rpc)
    assert app_server.read_codex_thread_metadata('thread')['path'] == 'native.jsonl'
    assert calls[0]['method'] == 'thread/read'
    assert calls[0]['params'] == {'threadId': 'thread', 'includeTurns': False}


@pytest.mark.parametrize('thread', [None, {'id': 'other', 'path': 'native.jsonl'},
                                    {'id': 'thread'}, {'id': 'thread', 'path': ''}])
def test_foreign_or_incomplete_native_metadata_never_guesses_rollout(monkeypatch, thread):
    monkeypatch.setattr(app_server, '_read_codex_app_server', lambda **k: {'thread': thread})
    with pytest.raises(app_server.CodexAppServerError):
        app_server.read_codex_thread_metadata('thread')
