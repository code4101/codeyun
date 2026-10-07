"""Lease boundaries and real cross-process crash/competition contracts."""
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.runtime.resource_leases import (
    ResourceLeaseStore, ResourceLease, LeaseError, hold_resource,
)


@pytest.fixture
def clocked(tmp_path):
    now = [1000.0]
    return ResourceLeaseStore(tmp_path, clock=lambda: now[0]), now


def test_default_expiry_and_resource_independence(clocked):
    store, now = clocked
    grant = store.acquire('gui:desktop:local', 'task-a')
    assert grant['expires_at'] == 1300
    assert 'token' not in store.status('gui:desktop:local')
    with pytest.raises(LeaseError, match='resource_busy'):
        store.acquire('gui:desktop:local', 'task-a')
    assert store.acquire('gui:desktop:other', 'task-b')['active']
    now[0] = 1300
    assert not store.status('gui:desktop:local')['active']
    replacement = store.acquire('gui:desktop:local', 'task-b')
    assert replacement['generation'] == 2
    assert replacement['token'] != grant['token']


def test_renewal_only_last_minute_and_adds_five_minutes(clocked):
    store, now = clocked
    grant = store.acquire('desktop', 'task')
    now[0] = 1239.999
    with pytest.raises(LeaseError, match='renewal_too_early'):
        store.renew('desktop', grant['token'])
    now[0] = 1240
    assert store.renew('desktop', grant['token'])['expires_at'] == 1600
    with pytest.raises(LeaseError, match='renewal_too_early'):
        store.renew('desktop', grant['token'])
    now[0] = 1599.999
    assert store.renew('desktop', grant['token'])['expires_at'] == 1900


def test_expired_and_foreign_holders_cannot_renew_or_clear_successor(clocked):
    store, now = clocked
    grant = store.acquire('desktop', 'task')
    with pytest.raises(LeaseError, match='not_owner'):
        store.renew('desktop', 'foreign')
    now[0] = 1300
    with pytest.raises(LeaseError, match='lease_expired'):
        store.renew('desktop', grant['token'])
    assert ResourceLease(store, 'desktop', grant['token']).lost()
    successor = store.acquire('desktop', 'task')
    assert not store.release('desktop', grant['token'])
    with pytest.raises(LeaseError, match='not_owner'):
        store.check('desktop', grant['token'])
    assert store.check('desktop', successor['token'])['active']


def test_context_releases_on_failure_and_stale_cleanup_is_safe(clocked):
    store, now = clocked
    with pytest.raises(RuntimeError, match='task failed'):
        with hold_resource('desktop', 'task', store=store):
            raise RuntimeError('task failed')
    assert not store.status('desktop')['active']
    with hold_resource('desktop', 'task', store=store) as lease:
        now[0] += 300
        replacement = store.acquire('desktop', 'next-task')
        assert lease.lost()
    assert store.check('desktop', replacement['token'])['active']


def test_wait_can_be_cancelled_and_read_does_not_persist(tmp_path):
    store = ResourceLeaseStore(tmp_path)
    assert not store.status('desktop')['active']
    assert list(tmp_path.glob('*.json')) == []
    stop = threading.Event()
    stop.set()
    with pytest.raises(InterruptedError):
        with hold_resource('desktop', 'task', store=store, stop_event=stop):
            pytest.fail('Cancelled task acquired resource')


CHILD = '''
import json,os,sys
from pathlib import Path
from backend.core.runtime.resource_leases import ResourceLeaseStore,LeaseError
store=ResourceLeaseStore(Path(sys.argv[1]),clock=lambda:1000)
try:
    grant=store.acquire('desktop',sys.argv[2])
    print(json.dumps(grant),flush=True)
except LeaseError as exc:
    print(json.dumps({'error':exc.code}),flush=True)
os._exit(9)
'''


def test_concurrent_processes_have_one_winner_and_crash_expires(tmp_path):
    children = [subprocess.Popen([sys.executable, '-c', CHILD, str(tmp_path), f'task-{i}'],
                cwd=str(Path(__file__).resolve().parents[2]), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True) for i in range(4)]
    results = []
    try:
        for child in children:
            out, err = child.communicate(timeout=20)
            assert child.returncode == 9, err
            results.append(json.loads(out))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    assert sum('token' in result for result in results) == 1
    assert sum(result.get('error') == 'resource_busy' for result in results) == 3
    store = ResourceLeaseStore(tmp_path, clock=lambda: 1300)
    # No finally/release ran in any child; expiry alone admits the next task.
    grant = store.acquire('desktop', 'after-crash')
    assert grant['generation'] == 2


def test_api_uses_same_authority_and_rejects_invalid_renewal(clocked, monkeypatch):
    from backend.api import resource_leases as api
    store, now = clocked
    monkeypatch.setattr(api, 'ResourceLeaseStore', lambda: store)
    app = FastAPI()
    app.include_router(api.router, prefix='/api/resource-leases')
    app.dependency_overrides[api.get_current_active_superuser] = lambda: True
    client = TestClient(app)
    path = '/api/resource-leases'
    grant = client.post(path + '/acquire', json={'resource': 'desktop', 'owner': 'task'}).json()
    credentials = {'resource': 'desktop', 'token': grant['token']}
    assert 'token' not in client.get(path + '/status', params={'resource': 'desktop'}).json()
    assert client.post(path + '/renew', json=credentials).status_code == 409
    now[0] = 1240
    assert client.post(path + '/renew', json=credentials).json()['expires_at'] == 1600
    assert client.post(path + '/check', json=credentials).status_code == 200
    assert client.post(path + '/release', json=credentials).json() == {'released': True}
    assert client.post(path + '/check', json=credentials).status_code == 409
