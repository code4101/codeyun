import csv
import hashlib
import json

import pytest

from backend.core.skilldep_materials import catalog, codehub, local_sync
from backend.core.skilldep_materials.catalog import CatalogError, CatalogStore
from backend.core.skilldep_materials.local_sync import flush_outbox, publish_local

DEV = 'dev-windows-f'


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan(mid):
    return {'id': mid, 'via': 'crawl', 'name': f'名称-{mid}', 'url': f'https://x/{mid}', 'size_mb_est': 1,
            'requires_login_or_purchase': False, 'acquisition': {}, 'cloud_transfer': {}}


def _snapshot(*records):
    items = [_plan('sd-a'), _plan('sd-b'), _plan('sd-dir')]
    return {'materials': {'count': len(items), 'items': items}, 'acquisitions': list(records)}


def _remote(mid, status, recorded_at, **extra):
    return {'material_id': mid, 'device_id': DEV, 'availability': 'windows_local', 'status': status,
            'recorded_at': recorded_at, **extra}


@pytest.fixture(autouse=True)
def no_slow_samples(monkeypatch):
    monkeypatch.setattr(catalog, '_slow_sources', lambda: set())


@pytest.fixture
def store(tmp_path):
    s = CatalogStore(tmp_path / 'queue', device_id=DEV)
    s.import_snapshot(_snapshot(), source='fake')
    return s


@pytest.fixture
def payload(tmp_path):
    path = tmp_path / 'files' / 'a.pdf'
    path.parent.mkdir()
    path.write_bytes(b'0123456789')
    return path


def _file_record(path, **extra):
    return {'status': 'downloaded', 'path': str(path), 'size_bytes': path.stat().st_size, 'sha256': _sha(path),
            'source_url': 'https://x/sd-a', 'finished_at': '2026-10-20T12:00:00Z', **extra}


def _dir_record(tmp_path, pages=(1, 2), name='manifest-1.json'):
    d = tmp_path / 'crawl' / 'sd-dir'
    (d / 'snapshots').mkdir(parents=True, exist_ok=True)
    manifest = d / 'snapshots' / name
    manifest.write_text(json.dumps({'material_id': 'sd-dir', 'pages': {str(p): {'bytes': 5} for p in pages}}),
                        encoding='utf-8')
    return {'status': 'partial', 'path': d.as_posix(), 'size_bytes': 5 * len(pages),
            'manifest_path': manifest.as_posix(), 'manifest_sha256': _sha(manifest),
            'scope': {'pages': list(pages), 'note': f'{len(pages)} 页'}}


class Hub:
    """Fake public CodeHub registry: register + acquisitions read-back, with optional failure hooks."""

    def __init__(self, device_id=DEV, fail=None, tamper=None, on_register=None):
        self.device_id, self.device_name = device_id, 'F 机'
        self.records, self.calls = {}, []
        self.fail, self.tamper, self.on_register = fail, tamper, on_register

    def register(self, material_id, record):
        self.calls.append((material_id, dict(record)))
        if self.on_register:
            self.on_register(material_id)
        if self.fail:
            raise codehub.CodeHubError(self.fail)
        self.records[material_id] = {**record, 'recorded_at': '2026-10-21T09:00:00+08:00'}
        if self.tamper:
            self.records[material_id].update(self.tamper)
        return self.records[material_id]

    def acquisitions(self, material_id):
        return [self.records[material_id]] if material_id in self.records else []

    def factory(self, **budget):
        self.budget = budget
        return self


def _rows(store):
    return {r['id']: r for r in csv.DictReader(store.csv_path.read_text(encoding='utf-8-sig').splitlines())}


def test_record_local_offline_shows_in_csv_and_after_restart(store, payload):
    result = store.record_local('sd-a', _file_record(payload))
    assert result['local_saved'] and result['remote_registered'] is False

    row = _rows(store)['sd-a']
    assert row['local_status'] == 'downloaded' and row['local_sha256'] == _sha(payload)
    assert row['local_saved'] == 'True' and row['remote_registered'] == 'False'

    reopened = CatalogStore(store.root, device_id=DEV)
    item = reopened.load()['items']['sd-a']
    assert item['local']['status'] == 'downloaded' and not item['executable']
    assert item['publication']['local_saved'] and not item['publication']['remote_registered']
    assert reopened.status()['pending_sync'] == 1
    assert reopened.outbox()['items']['sd-a']['seq'] == result['outbox_seq']
    assert 'recorded_at' not in reopened.outbox()['items']['sd-a']['record']

    # Hub down: refresh fails, local progress stays; an old snapshot cannot roll it back either.
    assert reopened.refresh(lambda: (_ for _ in ()).throw(ConnectionError('offline')))['ok'] is False
    reopened.import_snapshot(_snapshot(_remote('sd-a', 'failed', '2026-10-19T00:00:00Z')), source='old')
    assert reopened.load()['items']['sd-a']['local']['status'] == 'downloaded'
    assert _rows(reopened)['sd-a']['local_status'] == 'downloaded'


def test_record_local_directory_manifest(store, tmp_path):
    rec = _dir_record(tmp_path)
    store.record_local('sd-dir', rec)
    local = store.load()['items']['sd-dir']['local']
    assert local['status'] == 'partial' and local['manifest_sha256'] == rec['manifest_sha256']
    assert local['scope']['pages'] == [1, 2]


def test_record_local_rejects_bad_identity_evidence_and_unknown(store, payload, tmp_path):
    before = store.path.read_bytes()
    good = _file_record(payload)
    bad_cases = {
        'unknown material': ('sd-nope', good),
        'other device': ('sd-a', {**good, 'device_id': 'dev-mac'}),
        'not windows_local': ('sd-a', {**good, 'availability': 'mac_local'}),
        'wrong hash': ('sd-a', {**good, 'sha256': '0' * 64}),
        'no hash': ('sd-a', {k: v for k, v in good.items() if k != 'sha256'}),
        'wrong size': ('sd-a', {**good, 'size_bytes': 11}),
        'missing file': ('sd-a', {**good, 'path': str(tmp_path / 'missing.pdf')}),
        'bad status': ('sd-a', {**good, 'status': 'done'}),
        'naive time': ('sd-a', {**good, 'recorded_at': '2026-10-20T12:00:00'}),
        'other material id': ('sd-a', {**good, 'material_id': 'sd-b'}),
    }
    rec = _dir_record(tmp_path)
    random_dir = tmp_path / 'random'
    random_dir.mkdir()
    empty = random_dir / 'manifest.json'
    empty.write_text('{}', encoding='utf-8')
    outside = tmp_path / 'outside.json'
    outside.write_text(json.dumps({'pages': {'1': {}}}), encoding='utf-8')
    bad_cases.update({
        'dir without manifest': ('sd-dir', {**rec, 'manifest_path': None}),
        'manifest hash': ('sd-dir', {**rec, 'manifest_sha256': '0' * 64}),
        'empty manifest': ('sd-dir', {**rec, 'path': random_dir.as_posix(), 'manifest_path': empty.as_posix(),
                                      'manifest_sha256': _sha(empty)}),
        'manifest outside dir': ('sd-dir', {**rec, 'manifest_path': outside.as_posix(),
                                            'manifest_sha256': _sha(outside)}),
        'manifest of other material': ('sd-a', {**rec}),
    })
    for name, (mid, record) in bad_cases.items():
        with pytest.raises(CatalogError):
            store.record_local(mid, record)
            pytest.fail(name)
    # A store opened under another device id refuses even a record that names no device.
    with pytest.raises(CatalogError, match='总表属于设备'):
        CatalogStore(store.root, device_id='dev-other').record_local('sd-b', {'status': 'pending'})
    assert store.path.read_bytes() == before and not store.outbox_path.exists()


def test_published_manifest_is_immutable(store, tmp_path):
    rec = _dir_record(tmp_path)
    store.record_local('sd-dir', rec)
    rewritten = _dir_record(tmp_path, pages=(1, 2, 3))  # same manifest path, rewritten content
    with pytest.raises(CatalogError, match='manifest 不可改'):
        store.record_local('sd-dir', rewritten)
    # A new content-addressed manifest is fine.
    store.record_local('sd-dir', _dir_record(tmp_path, pages=(1, 2, 3), name='manifest-2.json'))
    assert store.load()['items']['sd-dir']['local']['scope']['pages'] == [1, 2, 3]


def test_outbox_latest_wins_and_recovers_after_crash(store, payload, tmp_path):
    first = store.record_local('sd-a', _file_record(payload))
    second = store.record_local('sd-dir', _dir_record(tmp_path))
    third = store.record_local('sd-a', {'status': 'failed', 'error': '文件损坏，重新下载'})
    box = store.outbox()['items']
    assert box['sd-a']['seq'] == third['outbox_seq'] > first['outbox_seq']
    assert box['sd-a']['record']['status'] == 'failed' and len(box) == 2

    store.outbox_path.unlink()  # crash after the catalog commit, before the outbox write
    reopened = CatalogStore(store.root, device_id=DEV)
    entries = reopened.pending_publications()
    assert [(e['material_id'], e['seq']) for e in entries] == [('sd-dir', second['outbox_seq']),
                                                               ('sd-a', third['outbox_seq'])]
    assert all(e['recovered'] for e in entries) and reopened.outbox_path.exists()


def test_flush_success_failure_and_readback_mismatch(store, payload):
    store.record_local('sd-a', _file_record(payload))

    down = Hub(fail='PUT /x HTTP 409 conflict')
    result = flush_outbox(store, registry_factory=down.factory, timeout=3, retries=1)
    assert down.budget == {'timeout': 3, 'retries': 1}
    assert result['registered'] == 0 and result['pending'] == 1 and '409' in result['results'][0]['error']
    item = store.load()['items']['sd-a']
    assert item['local']['status'] == 'downloaded' and item['publication']['local_saved']
    assert not item['publication']['remote_registered'] and '409' in item['publication']['last_error']
    assert store.outbox()['items']['sd-a']['attempts'] == 1

    liar = Hub(tamper={'sha256': 'f' * 64})
    error = flush_outbox(store, registry_factory=liar.factory)['results'][0]['error']
    assert error.startswith('回读不一致') and 'sha256' in error
    assert not store.load()['items']['sd-a']['publication']['remote_registered']

    wrong = Hub(device_id='dev-other')
    assert '身份' in flush_outbox(store, registry_factory=wrong.factory)['results'][0]['error']
    assert wrong.calls == []

    def offline(**_):
        raise codehub.CodeHubError('CodeHub 灵网对端未核验')

    assert flush_outbox(store, registry_factory=offline)['pending'] == 1

    hub = Hub()
    result = flush_outbox(store, registry_factory=hub.factory)
    assert result['registered'] == 1 and result['pending'] == 0 and store.outbox()['items'] == {}
    sent = hub.calls[0][1]
    assert sent['sha256'] == _sha(payload) and sent['device_name'] == 'F 机'
    assert 'recorded_at' not in sent and 'origin' not in sent
    pub = store.load()['items']['sd-a']['publication']
    assert pub['local_saved'] and pub['remote_registered'] and pub['pending_seq'] is None
    assert _rows(store)['sd-a']['remote_registered'] == 'True'


def test_ack_of_old_entry_keeps_newer_record_queued(store, payload, tmp_path):
    store.record_local('sd-a', _file_record(payload))

    def newer_record_meanwhile(material_id):
        if len(hub.calls) == 1:  # while the first PUT is in flight, a newer local record lands
            store.record_local('sd-a', {'status': 'failed', 'error': '重新校验失败'})

    hub = Hub(on_register=newer_record_meanwhile)
    result = flush_outbox(store, registry_factory=hub.factory)
    assert result['results'][0]['remote_registered'] and not result['results'][0]['ack_current']
    entry = store.outbox()['items']['sd-a']
    assert entry['record']['status'] == 'failed'
    item = store.load()['items']['sd-a']
    assert item['local']['status'] == 'failed' and not item['publication']['remote_registered']

    assert flush_outbox(store, registry_factory=hub.factory)['pending'] == 0
    assert hub.records['sd-a']['status'] == 'failed'
    assert store.load()['items']['sd-a']['publication']['remote_registered']


def test_publish_local_commits_before_network(store, payload):
    seen = {}

    def check_local_first(material_id):
        seen['status'] = CatalogStore(store.root, device_id=DEV).load()['items'][material_id]['local']['status']

    hub = Hub(fail='连接失败', on_register=check_local_first)
    result = publish_local(store, 'sd-a', _file_record(payload), registry_factory=hub.factory)
    assert seen == {'status': 'downloaded'}
    assert result['local_saved'] and result['remote_registered'] is False and result['sync']['error'] == 'CodeHubError: 连接失败'
    assert publish_local(store, 'sd-b', {'status': 'pending'}, registry_factory=Hub().factory)['remote_registered']
    with pytest.raises(CatalogError):
        publish_local(store, 'sd-a', {**_file_record(payload), 'sha256': '0' * 64}, registry_factory=Hub().factory)


def test_flush_budget_leaves_remaining_entries_untouched(store, payload, tmp_path):
    store.record_local('sd-a', _file_record(payload))
    store.record_local('sd-dir', _dir_record(tmp_path))
    ticks = iter([0, 0, 100, 100])
    hub = Hub()
    result = flush_outbox(store, registry_factory=hub.factory, budget_seconds=5, clock=lambda: next(ticks))
    assert [r.get('skipped') for r in result['results']] == [None, 'budget']
    assert result['attempted'] == 1 and list(store.outbox()['items']) == ['sd-dir']
    assert store.outbox()['items']['sd-dir']['attempts'] == 0


def test_snapshot_merge_by_datetime_across_time_zones(store, payload):
    store.record_local('sd-a', _file_record(payload, recorded_at='2026-10-20T17:00:00Z'))
    flush_outbox(store, registry_factory=Hub().factory)  # acknowledged: the normal time rule applies again

    # 00:30+08:00 is 16:30Z, older than the local 17:00Z although it sorts later as a string.
    older = _remote('sd-a', 'failed', '2026-10-21T00:30:00+08:00')
    assert store.import_snapshot(_snapshot(older), source='s1')['kept_local_downloads'] == ['sd-a']
    assert store.load()['items']['sd-a']['local']['status'] == 'downloaded'

    # Same instant written in another zone: the snapshot wins (as before for equal times).
    same = _remote('sd-a', 'partial', '2026-10-21T01:00:00+08:00', path=str(payload), size_bytes=10)
    store.import_snapshot(_snapshot(same), source='s2')
    assert store.load()['items']['sd-a']['local']['status'] == 'partial'

    newer = _remote('sd-a', 'failed', '2026-10-20T17:30:00Z')
    store.import_snapshot(_snapshot(newer), source='s3')
    assert store.load()['items']['sd-a']['local']['status'] == 'failed'

    # Within one snapshot the latest record is picked by instant, not by string.
    store.import_snapshot(_snapshot(_remote('sd-b', 'failed', '2026-10-21T00:30:00+08:00'),
                                    _remote('sd-b', 'pending', '2026-10-20T17:00:00Z')), source='s4')
    assert store.load()['items']['sd-b']['local']['status'] == 'pending'


def test_newer_local_invalidation_downgrades_and_old_snapshot_cannot_revive(store, payload):
    store.record_local('sd-a', _file_record(payload, recorded_at='2026-10-20T12:00:00Z'))
    with pytest.raises(CatalogError, match='更新的本机记录'):
        store.record_local('sd-a', _file_record(payload, recorded_at='2026-10-20T11:00:00Z'))
    store.record_local('sd-a', {'status': 'failed', 'error': '校验失败', 'recorded_at': '2026-10-20T13:00:00Z'})
    assert store.load()['items']['sd-a']['executable']
    flush_outbox(store, registry_factory=Hub().factory)

    old = _remote('sd-a', 'downloaded', '2026-10-20T12:30:00Z', path=str(payload), size_bytes=10,
                  sha256=_sha(payload))
    store.import_snapshot(_snapshot(old), source='old')
    assert store.load()['items']['sd-a']['local']['status'] == 'failed'


def test_pending_local_without_evidence_yields_to_snapshot(store, payload):
    store.record_local('sd-a', _file_record(payload))
    payload.unlink()
    store.import_snapshot(_snapshot(_remote('sd-a', 'failed', '2026-10-19T00:00:00Z')), source='s')
    item = store.load()['items']['sd-a']
    assert item['local']['status'] == 'failed' and item['publication']['pending_seq'] is None
    assert store.pending_publications() == [] and store.outbox()['items'] == {}


def test_cli_record_local_and_flush(store, payload, monkeypatch, capsys):
    base = ['--root', str(store.root), '--device-id', DEV]
    assert catalog.main(base + ['record-local', 'sd-a', '--record', json.dumps(_file_record(payload))]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['local_saved'] and not out['remote_registered']
    assert catalog.main(base + ['record-local', 'sd-nope', '--record', '{"status": "pending"}']) == 2
    assert 'sd-nope' in json.loads(capsys.readouterr().out)['error']

    def offline(**_):
        raise codehub.CodeHubError('offline')

    monkeypatch.setattr(local_sync, 'codehub_registry', offline)
    assert local_sync.main(base + ['--timeout', '2', '--retries', '1', 'flush']) == 1
    assert json.loads(capsys.readouterr().out)['pending'] == 1
    hub = Hub()
    monkeypatch.setattr(local_sync, 'codehub_registry', hub.factory)
    assert local_sync.main(base + ['--timeout', '2', '--retries', '1', 'flush']) == 0
    assert hub.budget == {'timeout': 2.0, 'retries': 1}
    capsys.readouterr()
    assert local_sync.main(base + ['outbox']) == 0
    assert json.loads(capsys.readouterr().out)['items'] == {}


def test_codehub_client_timeout_and_retries_are_bounded(monkeypatch):
    attempts, sleeps = [], []

    def refused(method, url, **kwargs):
        attempts.append(kwargs['timeout'])
        raise codehub.requests.ConnectionError('refused')

    monkeypatch.setattr(codehub.requests, 'request', refused)
    monkeypatch.setattr(codehub.time, 'sleep', sleeps.append)
    client = object.__new__(codehub.CodeHubSkillDep)
    client._peer, client.api_base = {'token': 'fake-token'}, 'https://hub.example/api'
    assert (client.timeout, client.retries) == (60, 3)  # unchanged defaults
    client.timeout, client.retries, client.backoff = 2.5, 2, 1
    with pytest.raises(codehub.CodeHubError, match='连接失败'):
        client.acquisitions('sd-a')
    assert attempts == [2.5, 2.5] and sleeps == [1]  # no sleep after the last attempt
    with pytest.raises(ValueError):
        codehub.CodeHubSkillDep(timeout=0)


def test_concurrent_flush_never_leaves_old_remote_with_empty_outbox(store, payload, tmp_path):
    """An old flush whose PUT lands late cannot hide a newer record a second flush published meanwhile."""
    import threading

    first = store.record_local('sd-a', _file_record(payload))
    in_flight, land = threading.Event(), threading.Event()

    def old_put_lands_late(material_id):
        if len(hub.calls) == 1:
            in_flight.set()
            assert land.wait(10)

    hub = Hub(on_register=old_put_lands_late)
    old = {}
    worker = threading.Thread(target=lambda: old.update(flush_outbox(store, registry_factory=hub.factory)))
    worker.start()
    try:
        assert in_flight.wait(10)
        other = CatalogStore(store.root, device_id=DEV)  # another process: own store and locks
        newer = other.record_local('sd-a', {'status': 'failed', 'error': '重新校验失败'})  # not blocked by the flush
        busy = flush_outbox(other, registry_factory=hub.factory)
    finally:
        land.set()
        worker.join(10)
    assert busy.get('busy') and busy['attempted'] == 0 and busy['results'][0]['skipped'] == 'busy'
    assert hub.records['sd-a']['status'] == 'downloaded'  # the old PUT landed last
    assert old['results'][0]['seq'] == first['outbox_seq'] and not old['results'][0]['ack_current']
    pub = store.load()['items']['sd-a']['publication']
    assert store.status()['pending_sync'] == 1 and store.outbox()['items']['sd-a']['seq'] == newer['outbox_seq']
    assert not pub['remote_registered'] and pub['pending_seq'] == newer['outbox_seq']

    assert flush_outbox(store, registry_factory=hub.factory)['pending'] == 0
    assert hub.records['sd-a']['status'] == 'failed'
    assert store.load()['items']['sd-a']['publication']['remote_registered']


def test_flush_rereads_current_seq_under_publisher_lock(store, payload, tmp_path):
    """An entry list read before the lock never sends a record a newer local save replaced."""
    store.record_local('sd-a', _file_record(payload))
    stale = store.pending_publications()
    store.record_local('sd-a', {'status': 'failed', 'error': 'x'})
    hub = Hub()
    original = store.pending_publications
    calls = []

    def first_call_stale(material_ids=None, limit=None):
        calls.append(material_ids)
        return stale if len(calls) == 1 else original(material_ids, limit)

    store.pending_publications = first_call_stale
    result = flush_outbox(store, registry_factory=hub.factory)
    assert [c[1]['status'] for c in hub.calls] == ['failed'] and result['pending'] == 0
