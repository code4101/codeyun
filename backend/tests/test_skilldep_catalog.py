import csv
import json

import pytest

from backend.core.skilldep_materials import catalog
from backend.core.skilldep_materials.catalog import CatalogError, CatalogStore


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


def _snapshot(generated_at='2026-10-20T22:00:00+08:00', version='v1'):
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
            {'material_id': 'sd-done', 'status': 'downloaded', 'path': 'F:/m/sd-done/a.pdf', 'size_bytes': 10,
             'sha256': 'ab', 'scope': None, 'recorded_at': '2026-10-20T21:00:00+08:00'},
            {'material_id': 'sd-slow', 'status': 'partial', 'path': 'F:/m/slow', 'size_bytes': 5,
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
    store = CatalogStore(tmp_path / 'queue')
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
    store = CatalogStore(tmp_path / 'queue')
    first = store.import_snapshot(_snapshot(), source='fake', routes_path=routes)
    before = store.path.read_bytes()
    again = store.import_snapshot(_snapshot(), source='fake', routes_path=routes)
    assert again == {'changed': False, 'revision': first['revision'], 'count': 6}
    assert store.path.read_bytes() == before


def test_next_task_rate_limited_first_then_small_and_skips_unexecutable(tmp_path, routes):
    store = CatalogStore(tmp_path / 'queue')
    store.import_snapshot(_snapshot(), source='fake', routes_path=routes)
    ids = [t['id'] for t in store.next_tasks(10)]
    # done, purchase and blocked are never offered; slow 300MB beats small 1MB.
    assert ids == ['sd-slow', 'sd-small', 'sd-big']


def test_state_survives_restart_and_reimport(tmp_path, routes):
    root = tmp_path / 'queue'
    store = CatalogStore(root)
    store.import_snapshot(_snapshot(), source='fake', routes_path=routes)
    store.set_task('sd-slow', 'claimed', note='worker-1')

    reopened = CatalogStore(root)
    assert reopened.load()['items']['sd-slow']['task']['status'] == 'claimed'
    assert [t['id'] for t in reopened.next_tasks(1)] == ['sd-small']

    # A newer snapshot that lost the download record must not downgrade it nor reset the task.
    newer = _snapshot(generated_at='2026-10-21T00:00:00+08:00', version='v2')
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
    store = CatalogStore(tmp_path / 'queue')
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

    ok = store.refresh(lambda: _snapshot(version='v3'), routes_path=routes)
    assert ok['ok'] and store.load()['source_versions']['plan']['version'] == 'v3'


def test_csv_is_excel_readable(tmp_path, routes):
    store = CatalogStore(tmp_path / 'queue')
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
    assert catalog.main(['--root', str(root), 'import', '--snapshot', str(snap), '--routes', str(routes)]) == 0
    capsys.readouterr()
    assert catalog.main(['--root', str(root), 'status']) == 0
    assert json.loads(capsys.readouterr().out)['total'] == 6
    assert catalog.main(['--root', str(root), 'next-task', '--limit', '1']) == 0
    assert json.loads(capsys.readouterr().out)[0]['id'] == 'sd-slow'
