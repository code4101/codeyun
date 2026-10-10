"""Official event parsing and durable failure evidence, no model/game execution."""
import threading
import pytest

from backend.core.codex.wechat_agent import CodexWechatClient
from backend.core.codex.turn_errors import CodexTurnFailed


def run_events(monkeypatch, events):
    client = CodexWechatClient()
    monkeypatch.setattr(client, 'current_defaults', lambda: {'model': 'original-model'})
    calls = []
    def rpc(method, params):
        calls.append(method)
        return {'turn': {'id': 'owned'}}
    monkeypatch.setattr(client, 'rpc', rpc)
    for event in events:
        client.events.put(event)
    try:
        return client.run('original', 'continue unfinished work', lambda: False,
            threading.Event(), lambda _: None, raw_text=True, timeout=1)
    finally:
        assert 'turn/interrupt' not in calls


def notification(method, error=None, *, turn_id='owned', status=None):
    params = dict(threadId='original', turnId=turn_id)
    if status:
        params['turn'] = dict(id=turn_id, status=status, error=error)
    else:
        params['error'] = error
    return dict(method=method, params=params)


def test_matching_failed_terminal_preserves_structured_first_error(monkeypatch):
    first = {'message': 'remote compact stream disconnected',
        'codexErrorInfo': {'ResponseStreamDisconnected': {'httpStatusCode': 502}},
        'additionalDetails': 'error decoding response body'}
    with pytest.raises(CodexTurnFailed) as caught:
        run_events(monkeypatch, [notification('error', {'message': 'foreign'}, turn_id='old'),
            notification('error', first),
            notification('turn/completed', {'message': 'compact failed'}, status='failed')])
    exc = caught.value
    assert (exc.thread_id, exc.turn_id, exc.native_terminal_status) == ('original', 'owned', 'failed')
    assert exc.native_error == {'message': 'compact failed'}
    assert exc.error_events == [first]


def test_intermediate_error_does_not_override_completed_terminal(monkeypatch):
    result = run_events(monkeypatch, [notification('error', {'message': 'retryable'}),
        notification('turn/completed', status='completed')])
    assert result['action'] == 'ignore'


def test_failure_receipt_is_bounded_and_missing_details_not_invented():
    exc = CodexTurnFailed('thread', 'turn', None, [{'message': 'x'*5000}]*20)
    assert exc.native_error == {}
    assert len(exc.error_events) == 8
    assert all(len(row['message']) == 1200 for row in exc.error_events)
