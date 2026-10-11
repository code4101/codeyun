import pytest

from backend.core.codex import app_server


def test_turn_diagnostics_preserve_bounded_read_only_protocol(monkeypatch):
    calls = []
    response = {'data': [{'id': 'turn', 'status': 'failed',
                         'error': {'message': 'compact transport failed'}}],
                'nextCursor': 'older'}
    def request(**kwargs):
        calls.append(kwargs)
        return response
    monkeypatch.setattr(app_server, '_read_codex_app_server', request)
    assert app_server.read_codex_thread_turns('thread', limit=5, cursor='opaque') is response
    assert calls == [{'method': 'thread/turns/list',
                      'params': {'threadId': 'thread', 'limit': 5,
                                 'sortDirection': 'desc', 'itemsView': 'notLoaded',
                                 'cursor': 'opaque'},
                      'timeout_seconds': 25.0, 'experimental_api': True}]


@pytest.mark.parametrize('arguments', [
    {'thread_id': ''}, {'thread_id': 't', 'limit': True},
    {'thread_id': 't', 'limit': 101}, {'thread_id': 't', 'limit': 0},
    {'thread_id': 't', 'cursor': ''},
])
def test_invalid_turn_diagnostics_never_start_app_server(monkeypatch, arguments):
    monkeypatch.setattr(app_server, '_read_codex_app_server',
                        lambda **kwargs: pytest.fail('invalid request must stay local'))
    with pytest.raises(ValueError):
        app_server.read_codex_thread_turns(**arguments)


def test_turn_diagnostics_reject_unbounded_or_incompatible_response(monkeypatch):
    monkeypatch.setattr(app_server, '_read_codex_app_server', lambda **kwargs: {})
    with pytest.raises(app_server.CodexAppServerError, match='data'):
        app_server.read_codex_thread_turns('thread')


def test_exact_turn_items_use_bounded_native_read_and_validate_identity(monkeypatch):
    calls = []
    response = {'data': [{'turnId': 'turn', 'item': {'type': 'commandExecution'}}], 'nextCursor': None}
    def request(**kwargs):
        calls.append(kwargs)
        return response
    monkeypatch.setattr(app_server, '_read_codex_app_server', request)
    assert app_server.read_codex_thread_items('thread', turn_id='turn', limit=2) is response
    assert calls[0]['method'] == 'thread/items/list'
    assert calls[0]['params'] == {'threadId': 'thread', 'turnId': 'turn', 'limit': 2, 'sortDirection': 'asc'}
    response['data'][0]['turnId'] = 'different'
    with pytest.raises(app_server.CodexAppServerError, match='identity'):
        app_server.read_codex_thread_items('thread', turn_id='turn')


def test_forensic_window_pages_metadata_and_keeps_unknown_end_explicit(monkeypatch):
    pages = iter([
        {'data': [{'id': 'new', 'startedAt': 300, 'completedAt': 400}], 'nextCursor': 'older'},
        {'data': [{'id': 'match', 'startedAt': 120, 'completedAt': 180},
                  {'id': 'uncertain', 'startedAt': 90, 'completedAt': None}], 'nextCursor': None},
    ])
    monkeypatch.setattr(app_server, 'read_codex_thread_turns', lambda *a, **k: next(pages))
    result = app_server.read_codex_thread_turn_window('thread', start_epoch=100, end_epoch=200)
    assert [row['id'] for row in result['turns']] == ['match', 'uncertain']
    assert result['pagination_complete'] and not result['complete']
    assert result['uncertain_overlap_turn_ids'] == ['uncertain']
    assert result['writer_resumed'] is False


def test_forensic_window_late_native_response_cannot_prove_complete(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(app_server.time, 'monotonic', lambda: clock[0])
    def request(*a, **k):
        clock[0] = 91.0
        return {'data': [{'id': 'match', 'startedAt': 120, 'completedAt': 180}], 'nextCursor': None}
    monkeypatch.setattr(app_server, 'read_codex_thread_turns', request)
    result = app_server.read_codex_thread_turn_window('thread', start_epoch=100, end_epoch=200)
    assert result['deadline_exceeded'] and not result['complete']
    assert result['elapsed_seconds'] == 91.0


def test_forensic_window_observation_failure_preserves_previous_page(monkeypatch):
    calls = [0]
    def request(*a, **k):
        calls[0] += 1
        if calls[0] == 2:
            raise app_server.CodexAppServerError('observation timeout')
        return {'data': [{'id': 'match', 'startedAt': 120, 'completedAt': 180}], 'nextCursor': 'older'}
    monkeypatch.setattr(app_server, 'read_codex_thread_turns', request)
    result = app_server.read_codex_thread_turn_window('thread', start_epoch=100, end_epoch=200)
    assert result['turns'][0]['id'] == 'match'
    assert result['next_cursor'] == 'older' and result['pages_read'] == 1
    assert result['read_error'] == 'observation timeout' and not result['complete']
