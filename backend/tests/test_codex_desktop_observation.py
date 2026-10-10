"""Public MCP observation contracts; transport failure is not thread termination."""
import pytest
from backend.core.codex import desktop


def test_failed_event_wait_preserves_first_error_and_uses_one_read_per_target(monkeypatch):
    calls = []
    def call(name, arguments, **kwargs):
        calls.append((name, arguments))
        if name == 'wait_threads':
            raise RuntimeError('MCP -32000 Codex app tool request failed')
        return {'thread': {'id': arguments['threadId'], 'status': {'type': 'active'}},
                'turns': [{'id': 'fresh-turn', 'status': 'inProgress'}]}
    monkeypatch.setattr(desktop, 'call_desktop_tool', call)
    result = desktop.observe_desktop_threads([
        {'threadId': 'one', 'hostId': 'local', 'afterCursor': 'opaque-wait-cursor'},
        {'threadId': 'two'}], timeout_ms=30000)
    assert [name for name, _ in calls] == ['wait_threads', 'read_thread', 'read_thread']
    assert 'afterCursor' not in calls[1][1] and 'cursor' not in calls[1][1]
    assert result['mode'] == 'snapshot_fallback'
    assert '-32000' in result['first_wait_error']
    assert result['observations'][0]['latest_turn']['status'] == 'inProgress'
    assert result['wait_cursor_advanced'] is False
    assert result['event_wakeup_supported'] is False
    assert result['ownership_release_authorized'] is False


def test_explicit_snapshot_never_retries_broken_wait(monkeypatch):
    calls = []
    def call(name, arguments, **kwargs):
        calls.append(name)
        assert name == 'read_thread'
        return {'thread': {'id': 'one', 'status': {'type': 'idle'}},
                'turns': [{'status': 'completed'}]}
    monkeypatch.setattr(desktop, 'call_desktop_tool', call)
    result = desktop.observe_desktop_threads([{'threadId': 'one'}])
    assert calls == ['read_thread']
    assert result['mode'] == 'snapshot'
    assert result['first_wait_error'] is None
    assert result['ownership_release_authorized'] is False


def test_read_failure_or_wrong_identity_is_unknown_and_does_not_stop_other_targets(monkeypatch):
    def call(name, arguments, **kwargs):
        if arguments['threadId'] == 'failed':
            raise TimeoutError('read unavailable')
        return {'thread': {'id': 'different'}}
    monkeypatch.setattr(desktop, 'call_desktop_tool', call)
    result = desktop.observe_desktop_threads([{'threadId': 'failed'}, {'threadId': 'wrong'}])
    assert [r['status'] for r in result['observations']] == ['unknown', 'unknown']
    assert 'TimeoutError' in result['observations'][0]['error']
    assert 'identity' in result['observations'][1]['error']
    assert result['ownership_release_authorized'] is False


@pytest.mark.parametrize('timeout', [0, 30000])
def test_successful_wait_retains_provider_payload_without_fallback(monkeypatch, timeout):
    def call(name, arguments, **kwargs):
        assert name == 'wait_threads'
        assert arguments == {'targets': [{'threadId': 'one'}], 'timeoutMs': timeout}
        return {'errors': [{'threadId': 'one', 'message': 'not available'}]}
    monkeypatch.setattr(desktop, 'call_desktop_tool', call)
    result = desktop.observe_desktop_threads([{'threadId': 'one'}], timeout_ms=timeout)
    assert result['payload']['errors'][0]['threadId'] == 'one'
    assert result['event_wakeup_supported'] is (timeout > 0)


@pytest.mark.parametrize('targets,timeout', [([], None), ([{'threadId': ''}], None),
    ([{'threadId': 'one', 'cursor': 'wrong-api'}], None), ([{'threadId': 'one'}] * 9, None),
    ([{'threadId': 'one'}], True), ([{'threadId': 'one'}], -1)])
def test_invalid_requests_never_touch_transport(monkeypatch, targets, timeout):
    monkeypatch.setattr(desktop, 'call_desktop_tool', lambda *a, **k: pytest.fail('invalid request sent'))
    with pytest.raises(ValueError):
        desktop.observe_desktop_threads(targets, timeout_ms=timeout)


def test_interrupt_is_not_converted_to_transport_failure(monkeypatch):
    def call(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(desktop, 'call_desktop_tool', call)
    with pytest.raises(KeyboardInterrupt):
        desktop.observe_desktop_threads([{'threadId': 'one'}], timeout_ms=0)
