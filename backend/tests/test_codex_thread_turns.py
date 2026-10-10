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
