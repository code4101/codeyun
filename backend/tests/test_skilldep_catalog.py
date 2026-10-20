import csv
import hashlib
import json
from pathlib import Path

import pytest

from backend.core.skilldep_materials import catalog
from backend.core.skilldep_materials.catalog import CatalogError, CatalogStore

DEV = 'dev-windows-f'
MAC = 'dev-mac'
OTHER_WIN = 'dev-windows-other'


def _plan(mid, size_mb, *, via='crawl', buy=False, downloaded=False, partial=False):
    return {
        'id': mid, 'domain': 'EDU', 'label': 'EDU', 'via': via, 'name': f'名称-{mid}', 'url': f'https://x/{mid}',
        'size': f'{size_mb}MB', 'size_mb_est': size_mb, 'count': '1', 'type': '文档',
        'requires_login_or_purchase': buy, 'labels': ['EDU'], 'what': f'公开说明 {mid}', 'size_note': None,
        'site': None, 'meta': None, 'price': None,
        'acquisition': {'windows_local_downloaded': downloaded, 'windows_local_partial': partial,
                        'windows_local_downloading': False, 'mac_accessible': False},
        'cloud_transfer': {'cloud_transferred': False, 'cloud_transferring': False,
                           'cloud_failed': False, 'cloud_stale': False},
    }


def _sha(path):
    """Real SHA-256 of an existing fixture file, else a placeholder for a path that is not on disk."""
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'ab'


def _snapshot(generated_at='2026-10-20T22:00:00+08:00', version='v1', done_path='F:/m/sd-done/a.pdf'):
    items = [
        _plan('sd-big', 500),
        _plan('sd-small', 1),
        _plan('sd-slow', 300, partial=True),
        _plan('sd-done', 2, downloaded=True),
        _plan('sd-buy', 0.1, via='buy', buy=True),
        _plan('sd-blocked', 0.5),
    ]
    return {
        'generated_at': generated_at,
        'versions': {'plan': {'version': version}, 'acquisitions': {'version': version}},
        'materials': {'count': len(items), 'items': items},
        'acquisitions': [
            {'material_id': 'sd-done', 'device_id': DEV, 'availability': 'windows_local', 'status': 'downloaded',
             'path': str(done_path), 'size_bytes': 10, 'sha256': _sha(done_path), 'scope': None,
             'recorded_at': '2026-10-20T21:00:00+08:00'},
            {'material_id': 'sd-slow', 'device_id': DEV, 'availability': 'windows_local', 'status': 'partial',
             'path': 'F:/m/slow', 'size_bytes': 5,
             'scope': {'page_limit': 1, 'pages': [1], 'note': '仅第1页'}, 'recorded_at': '2026-10-20T21:00:00+08:00'},
        ],
        'cloud_transfers': [],
        'delivery_resources': {'items': [{'material_id': 'sd-buy', 'delivery_status': 'awaiting',
                                          'share_url': 'SECRET-LINK', 'extract_code': 'SECRET-CODE'}]},
    }


@pytest.fixture
def routes(tmp_path):
    path = tmp_path / 'candidate-routes.json'
    path.write_text(json.dumps({
        'candidates': [{'material_id': 'sd-slow', 'rate_limit': 'token bucket 200'},
                       {'material_id': 'sd-small', 'rate_limit': 'none observed; Range supported'}],
        'blocked_or_needs_link_parse': [{'material_id': 'sd-blocked', 'reason': '需解析链接'},
                                        {'material_id': 'api.github.com (all)', 'reason': 'x'}],
    }), encoding='utf-8')
    return path


@pytest.fixture(autouse=True)
def no_slow_samples(monkeypatch):
    monkeypatch.setattr(catalog, '_slow_sources', lambda: set())


def test_import_keeps_public_fields_states_and_versions(tmp_path, routes):
    store = CatalogStore(tmp_path / 'queue', device_id=DEV)
    result = store.import_snapshot(_snapshot(), source='fake', routes_path=routes)
    assert result['changed'] and result['count'] == 6

    data = store.load()
    slow = data['items']['sd-slow']
    assert slow['what'] == '公开说明 sd-slow' and slow['labels'] == ['EDU']
    assert slow['local']['status'] == 'partial' and slow['local']['scope']['note'] == '仅第1页'
    assert slow['cloud_transfer']['cloud_transferred'] is False and slow['rate_limited']
    assert data['items']['sd-done']['local']['sha256'] == 'ab'
    assert data['source_versions']['plan']['version'] == 'v1'
    assert 'SECRET' not in store.path.read_text(encoding='utf-8')
    assert 'SECRET' not in store.csv_path.read_text(encoding='utf-8-sig')

    status = store.status()
    assert status['total'] == 6
    assert status['local_status'] == {'downloaded': 1, 'partial': 1, 'pending': 4}


def test_import_is_idempotent(tmp_path, routes):
    store = CatalogStore(tmp_path / 'queue', device_id=DEV)
    first = store.import_snapshot(_snapshot(), source='fake', routes_path=routes)
    before = store.path.read_bytes()
    again = store.import_snapshot(_snapshot(), source='fake', routes_path=routes)
    assert again == {'changed': False, 'revision': first['revision'], 'count': 6}
    assert store.path.read_bytes() == before


def test_next_task_rate_limited_first_then_small_and_skips_unexecutable(tmp_path, routes):
    store = CatalogStore(tmp_path / 'queue', device_id=DEV)
    store.import_snapshot(_snapshot(), source='fake', routes_path=routes)
    ids = [t['id'] for t in store.next_tasks(10)]
    # done, purchase and blocked are never offered; slow 300MB beats small 1MB.
    assert ids == ['sd-slow', 'sd-small', 'sd-big']


def test_state_survives_restart_and_reimport(tmp_path, routes):
    root = tmp_path / 'queue'
    done = tmp_path / 'sd-done.pdf'
    done.write_bytes(b'0123456789')
    store = CatalogStore(root, device_id=DEV)
    store.import_snapshot(_snapshot(done_path=done), source='fake', routes_path=routes)
    store.set_task('sd-slow', 'claimed', note='worker-1')

    reopened = CatalogStore(root, device_id=DEV)
    assert reopened.load()['items']['sd-slow']['task']['status'] == 'claimed'
    assert [t['id'] for t in reopened.next_tasks(1)] == ['sd-small']

    # A newer snapshot that lost the download record keeps the verified local file and the task.
    newer = _snapshot(generated_at='2026-10-21T00:00:00+08:00', version='v2', done_path=done)
    newer['acquisitions'] = []
    for item in newer['materials']['items']:
        item['acquisition']['windows_local_downloaded'] = False
    result = reopened.import_snapshot(newer, source='fake2', routes_path=routes)
    assert result['changed'] and result['kept_local_downloads'] == ['sd-done']
    data = reopened.load()
    assert data['items']['sd-done']['local']['status'] == 'downloaded'
    assert data['items']['sd-slow']['task']['status'] == 'claimed'
    with pytest.raises(CatalogError):
        reopened.set_task('sd-slow', 'done')


def test_refresh_failure_keeps_snapshot(tmp_path, routes):
    store = CatalogStore(tmp_path / 'queue', device_id=DEV)
    store.import_snapshot(_snapshot(), source='fake', routes_path=routes)
    items_before = store.load()['items']

    def offline():
        raise ConnectionResetError('ECONNRESET')

    result = store.refresh(offline, routes_path=routes)
    assert result['ok'] is False and 'ECONNRESET' in result['error']
    data = store.load()
    assert data['items'] == items_before and data['source'] == 'fake'
    assert data['last_refresh']['ok'] is False

    empty = store.refresh(lambda: {'materials': {'count': 0, 'items': []}}, routes_path=routes)
    assert empty['ok'] is False and store.load()['items'] == items_before

    # Same content under new versions: items untouched, the check itself is recorded.
    ok = store.refresh(lambda: _snapshot(version='v3'), routes_path=routes)
    data = store.load()
    assert ok['ok'] and not ok['changed'] and data['items'] == items_before
    assert data['last_refresh']['source_versions']['plan']['version'] == 'v3'

    ok = store.refresh(lambda: _changed_snapshot(version='v4'), routes_path=routes)
    assert ok['ok'] and ok['changed'] and store.load()['source_versions']['plan']['version'] == 'v4'


def test_csv_is_excel_readable(tmp_path, routes):
    store = CatalogStore(tmp_path / 'queue', device_id=DEV)
    store.import_snapshot(_snapshot(), source='fake', routes_path=routes)
    raw = store.csv_path.read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf')
    rows = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
    assert len(rows) == 6
    slow = next(r for r in rows if r['id'] == 'sd-slow')
    assert slow['local_status'] == 'partial' and slow['what'] == '公开说明 sd-slow' and slow['rate_limited'] == 'True'


def test_cli_offline_status_and_next_task(tmp_path, routes, capsys):
    snap = tmp_path / 'codehub-snapshot.json'
    snap.write_text(json.dumps(_snapshot(), ensure_ascii=False), encoding='utf-8')
    root = tmp_path / 'queue'
    assert catalog.main(['--root', str(root), '--device-id', DEV, 'import', '--snapshot', str(snap),
                         '--routes', str(routes)]) == 0
    capsys.readouterr()
    assert catalog.main(['--root', str(root), '--device-id', DEV, 'status']) == 0
    assert json.loads(capsys.readouterr().out)['total'] == 6
    assert catalog.main(['--root', str(root), '--device-id', DEV, 'next-task', '--limit', '1']) == 0
    assert json.loads(capsys.readouterr().out)[0]['id'] == 'sd-slow'


def _record(mid, device_id, status, recorded_at, *, path='F:/m/x', size=10, availability='windows_local'):
    return {'material_id': mid, 'device_id': device_id, 'device_name': device_id, 'availability': availability,
            'status': status, 'path': path, 'size_bytes': size, 'recorded_at': recorded_at}


def test_local_status_is_this_device_only(tmp_path, routes):
    snap = _snapshot()
    snap['acquisitions'] = [
        # Mac finished; this device only has a partial sample.
        _record('sd-slow', MAC, 'downloaded', '2026-10-20T23:00:00+08:00', availability='mac_local'),
        _record('sd-slow', DEV, 'partial', '2026-10-20T21:00:00+08:00'),
        # Another Windows PC finished; nothing is local, although the aggregate plan flag says downloaded.
        _record('sd-small', OTHER_WIN, 'downloaded', '2026-10-20T23:00:00+08:00'),
        # This device id, but not a local-disk availability.
        _record('sd-big', DEV, 'downloaded', '2026-10-20T23:00:00+08:00', availability='cloud'),
    ]
    for item in snap['materials']['items']:
        if item['id'] in ('sd-small', 'sd-done'):
            item['acquisition']['windows_local_downloaded'] = True
    store = CatalogStore(tmp_path / 'queue', device_id=DEV)
    store.import_snapshot(snap, source='fake', routes_path=routes)
    data = store.load()
    items = data['items']
    assert data['device_id'] == DEV
    assert items['sd-slow']['local']['status'] == 'partial' and items['sd-slow']['local']['device_id'] == DEV
    assert items['sd-slow']['remote_acquisitions'][0]['device_id'] == MAC
    assert items['sd-small']['local'] == {'status': 'pending'} and items['sd-small']['executable']
    assert items['sd-small']['remote_acquisitions'][0]['status'] == 'downloaded'
    assert items['sd-small']['acquisition']['windows_local_downloaded'] is True  # remote flags kept separately
    assert items['sd-big']['local'] == {'status': 'pending'}
    assert items['sd-done']['local'] == {'status': 'pending'}  # flags only: no local file is invented
    assert store.status()['local_status'] == {'partial': 1, 'pending': 5}
    assert [t['id'] for t in store.next_tasks(10)] == ['sd-slow', 'sd-small', 'sd-done', 'sd-big']


def test_newer_local_invalidation_replaces_downloaded(tmp_path, routes):
    done = tmp_path / 'sd-done.pdf'
    done.write_bytes(b'0123456789')
    store = CatalogStore(tmp_path / 'queue', device_id=DEV)
    store.import_snapshot(_snapshot(done_path=done), source='fake', routes_path=routes)
    newer = _snapshot(done_path=done)
    newer['acquisitions'][0] = _record('sd-done', DEV, 'failed', '2026-10-21T08:00:00+08:00', path=str(done))
    result = store.import_snapshot(newer, source='fake', routes_path=routes)
    assert result['changed'] and result['kept_local_downloads'] == []
    item = store.load()['items']['sd-done']
    assert item['local']['status'] == 'failed' and item['executable']


def test_lost_record_with_missing_file_is_not_kept(tmp_path, routes):
    done = tmp_path / 'sd-done.pdf'
    done.write_bytes(b'0123456789')
    store = CatalogStore(tmp_path / 'queue', device_id=DEV)
    store.import_snapshot(_snapshot(done_path=done), source='fake', routes_path=routes)
    done.unlink()
    newer = _snapshot(done_path=done)
    newer['acquisitions'] = newer['acquisitions'][1:]
    result = store.import_snapshot(newer, source='fake', routes_path=routes)
    assert result['kept_local_downloads'] == []
    assert store.load()['items']['sd-done']['local'] == {'status': 'pending'}


def test_older_snapshot_and_network_failure_keep_valid_local(tmp_path, routes):
    done = tmp_path / 'sd-done.pdf'
    done.write_bytes(b'0123456789')
    root = tmp_path / 'queue'
    store = CatalogStore(root, device_id=DEV)
    store.import_snapshot(_snapshot(done_path=done), source='fake', routes_path=routes)
    older = _snapshot(generated_at='2026-10-19T00:00:00+08:00', done_path=done)
    older['acquisitions'][0] = _record('sd-done', DEV, 'partial', '2026-10-19T00:00:00+08:00', path=str(done))
    result = store.import_snapshot(older, source='old', routes_path=routes)
    assert result['kept_local_downloads'] == ['sd-done']
    assert store.load()['items']['sd-done']['local']['status'] == 'downloaded'

    def offline():
        raise ConnectionError('network unreachable')

    before = store.load()['items']
    assert store.refresh(offline, routes_path=routes)['ok'] is False
    reopened = CatalogStore(root, device_id=DEV)  # restart while still offline
    assert reopened.load()['items'] == before
    assert reopened.status()['local_status'] == {'downloaded': 1, 'partial': 1, 'pending': 4}
    assert done.read_bytes() == b'0123456789'


def _changed_snapshot(**kwargs):
    snap = _snapshot(**kwargs)
    snap['materials']['items'][1]['what'] = '新说明'
    snap['acquisitions'].append(_record('sd-small', DEV, 'partial', '2026-10-20T21:30:00+08:00'))
    return snap


def test_fingerprint_follows_content_not_metadata(tmp_path, routes):
    store = CatalogStore(tmp_path / 'queue', device_id=DEV)
    first = store.import_snapshot(_snapshot(), source='fake', routes_path=routes)

    meta_only = _snapshot()  # same generated_at and versions, changed plan metadata
    meta_only['materials']['items'][1]['what'] = '新说明'
    result = store.import_snapshot(meta_only, source='fake', routes_path=routes)
    assert result['changed'] and result['revision'] > first['revision']
    assert store.load()['items']['sd-small']['what'] == '新说明'

    result = store.import_snapshot(_changed_snapshot(), source='fake', routes_path=routes)
    assert result['changed'] and store.load()['items']['sd-small']['local']['status'] == 'partial'

    no_meta = {k: v for k, v in _changed_snapshot().items() if k not in ('generated_at', 'versions')}
    for snap in (_changed_snapshot(), _changed_snapshot(generated_at='2026-10-22T00:00:00+08:00'), no_meta):
        before = store.path.read_bytes()
        assert store.import_snapshot(snap, source='fake', routes_path=routes)['changed'] is False
        assert store.path.read_bytes() == before

    no_meta['acquisitions'][-1]['status'] = 'downloaded'  # content change without any metadata
    assert store.import_snapshot(no_meta, source='fake', routes_path=routes)['changed']
    assert store.load()['items']['sd-small']['local']['status'] == 'downloaded'


def test_codehub_snapshot_uses_public_client(monkeypatch):
    from backend.core.skilldep_materials import codehub

    calls = []

    class Fake:
        def snapshot(self, include_secrets=False):
            calls.append(include_secrets)
            return {'ok': True}

    monkeypatch.setattr(codehub, 'CodeHubSkillDep', Fake)
    assert catalog.codehub_snapshot() == {'ok': True} and calls == [False]


def test_codehub_client_snapshot_requests_without_secrets(monkeypatch):
    from backend.core.skilldep_materials import codehub

    seen = {}

    class Response:
        ok, status_code, text = True, 200, ''

        @staticmethod
        def json():
            return {'materials': {'items': []}}

    def fake_request(method, url, **kwargs):
        seen.update(method=method, url=url, params=kwargs.get('params'), auth=kwargs['headers']['Authorization'])
        return Response()

    client = object.__new__(codehub.CodeHubSkillDep)
    client._peer = {'token': 'fake-token'}
    client.api_base = 'https://hub.example/api'
    monkeypatch.setattr(codehub.requests, 'request', fake_request)
    assert client.snapshot() == {'materials': {'items': []}}
    assert seen == {'method': 'GET', 'url': 'https://hub.example/api/data-platform/skill-dependency/plan/snapshot',
                    'params': {'include_secrets': 'false'}, 'auth': 'Bearer fake-token'}
