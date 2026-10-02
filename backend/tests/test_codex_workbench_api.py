"""Desktop UI controls protect owner access and never retry ambiguous submissions."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import codex_sessions as api


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(api.router, prefix='/api/codex')
    app.dependency_overrides[api.get_current_user_from_token] = lambda: SimpleNamespace(is_superuser=True)
    with TestClient(app) as client:
        yield client


def test_non_admin_cannot_read_or_send_desktop_chats(client, monkeypatch):
    client.app.dependency_overrides[api.get_current_user_from_token] = lambda: SimpleNamespace(is_superuser=False)
    monkeypatch.setattr(api, 'call_desktop_tool', lambda *a, **k: pytest.fail('must not dispatch'))
    assert client.get('/api/codex/desktop/threads').status_code == 403
    assert client.post('/api/codex/desktop/threads/chat/messages', json={'prompt': 'hello'}).status_code == 403


def test_live_read_preserves_history_cursor_without_resuming(client, monkeypatch):
    calls = []
    monkeypatch.setattr(api, 'call_desktop_tool', lambda name, arguments: calls.append((name, arguments)) or {'turns': []})
    assert client.get('/api/codex/desktop/threads/chat', params={'cursor': 'older'}).json() == {'turns': []}
    assert calls[0][0] == 'read_thread'
    assert calls[0][1]['cursor'] == 'older'
    assert calls[0][1]['threadId'] == 'chat'
    assert calls[0][1]['turnLimit'] <= 10


def test_sidebar_respects_desktop_provider_limit(client, monkeypatch):
    calls = []
    monkeypatch.setattr(api, 'call_desktop_tool', lambda name, arguments: calls.append((name, arguments)) or {'threads': []})
    assert client.get('/api/codex/desktop/threads').status_code == 200
    assert calls == [('list_threads', {'limit': 50})]
    assert client.get('/api/codex/desktop/threads?limit=51').status_code == 422


def test_ambiguous_send_is_one_dispatch_and_explicit_conflict(client, monkeypatch):
    calls = []
    def fail(name, arguments):
        calls.append((name, arguments))
        raise TimeoutError('receipt lost')
    monkeypatch.setattr(api, 'call_desktop_tool', fail)
    response = client.post('/api/codex/desktop/threads/chat/messages', json={'prompt': ' hello '})
    assert response.status_code == 409
    assert '避免重复发送' in response.json()['detail']
    assert calls == [('send_message_to_thread', {'threadId': 'chat', 'hostId': 'local', 'prompt': 'hello'})]


def test_empty_message_never_dispatches(client, monkeypatch):
    monkeypatch.setattr(api, 'call_desktop_tool', lambda *a, **k: pytest.fail('must not dispatch'))
    assert client.post('/api/codex/desktop/threads/chat/messages', json={'prompt': '   '}).status_code == 422


def test_new_chat_uses_bound_project_and_reports_uncertain_creation(client, monkeypatch):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise api.DesktopDispatchUncertain('creation receipt lost')
    monkeypatch.setattr(api, 'create_desktop_repair', create)
    response = client.post('/api/codex/desktop/threads', json={'prompt': ' inspect code '})
    assert response.status_code == 409
    assert calls == [dict(prompt='inspect code', title='inspect code', workspace=api.ROOT_DIR)]
