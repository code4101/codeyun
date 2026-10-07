from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api import browser_diagnostics as api
from backend.core.access.auth import get_current_active_superuser, get_current_active_user
from backend.core.runtime.browser_diagnostics import BrowserDiagnostics, BrowserDiagnosticsMiddleware, HEADER
from backend.models import User


def make_client(monkeypatch):
    now = [1000.0]
    store = BrowserDiagnostics(clock=lambda: now[0])
    monkeypatch.setattr(api, 'diagnostics', store)
    app = FastAPI()
    app.include_router(api.router, prefix='/api/browser-diagnostics')
    app.add_middleware(BrowserDiagnosticsMiddleware, store=store)
    app.dependency_overrides[get_current_active_superuser] = lambda: User(id=2, username='admin', hashed_password='x', is_superuser=True)
    app.dependency_overrides[get_current_active_user] = lambda: User(id=2, username='admin', hashed_password='x')

    @app.get('/api/book')
    def book():
        return {'content': '正文'}

    return TestClient(app), store, now


def test_disabled_and_other_browsers_do_not_collect(monkeypatch):
    client, store, _ = make_client(monkeypatch)
    headers = {'user-agent': 'QAXBrowser test'}
    assert HEADER not in client.get('/api/book', headers=headers).headers
    assert not store.snapshot()['events']
    client.put('/api/browser-diagnostics/control', json={'enabled': True})
    assert HEADER not in client.get('/api/book', headers={'user-agent': 'Chrome'}).headers
    assert not store.snapshot()['events']


def test_target_response_report_close_and_auto_expiry(monkeypatch):
    client, store, now = make_client(monkeypatch)
    run = client.put('/api/browser-diagnostics/control', json={'enabled': True, 'ttl_seconds': 60}).json()
    response = client.get('/api/book?token=never-record-me', headers={'user-agent': 'QAXBrowser test'})
    assert response.headers[HEADER].startswith(run['run_id'])
    event = store.snapshot()['events'][0]
    assert event['path'] == '/api/book' and event['status'] == 200
    assert event['duration_ms'] >= 0 and event['bytes'] > 0
    report = {'run_id': run['run_id'], 'events': [{'kind': 'snapshot', 'values': {'height': 900, 'supportsDvh': False}}]}
    assert client.post('/api/browser-diagnostics/events', json=report).status_code == 200
    assert store.snapshot()['events'][-1]['user_id'] == 2
    now[0] += 61
    assert not client.get('/api/browser-diagnostics').json()['enabled']
    assert HEADER not in client.get('/api/book', headers={'user-agent': 'QAXBrowser test'}).headers
    assert client.post('/api/browser-diagnostics/events', json=report).status_code == 410
    client.put('/api/browser-diagnostics/control', json={'enabled': False})
    assert not store.active()


def test_explicit_link_and_limits(monkeypatch):
    client, store, _ = make_client(monkeypatch)
    client.put('/api/browser-diagnostics/control', json={'enabled': True})
    assert HEADER in client.get('/api/book', headers={'user-agent': 'Chrome', 'X-CodeYun-Debug-Browser': 'qianxin'}).headers
    run_id = store.run_id
    for index in range(600):
        store.record(run_id, {'index': index})
    assert len(store.snapshot()['events']) == 500
    for _ in range(100):
        assert store.report(run_id, 2, [{'kind': 'snapshot'}])
    assert not store.report(run_id, 2, [{'kind': 'snapshot'}])
    assert client.put('/api/browser-diagnostics/control', json={'enabled': True, 'ttl_seconds': 3600}).status_code == 422
    assert client.put('/api/browser-diagnostics/control', json={'enabled': True, 'user_agent_contains': ['']}).status_code == 422


def test_administration_requires_superuser():
    app = FastAPI()
    app.include_router(api.router, prefix='/api/browser-diagnostics')
    client = TestClient(app)
    assert client.get('/api/browser-diagnostics').status_code == 401
    assert client.put('/api/browser-diagnostics/control', json={'enabled': True}).status_code == 401
