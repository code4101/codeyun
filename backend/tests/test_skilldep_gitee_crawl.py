import hashlib
import json
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

import pytest
from filelock import FileLock

from backend.core.skilldep_materials import gitee_crawl as g

T0 = 1_800_000_000.0


class Clock:
    def __init__(self):
        self.t = T0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Resp:
    def __init__(self, status, body, headers):
        self.status_code, self._body, self.headers = status, body, headers

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]


class Gitee:
    """Fake API: page n holds ids n*1000..; pages beyond ``last`` are empty; ``script`` overrides."""

    def __init__(self, clock, last=30, remaining=50, script=None):
        self.clock, self.last, self.remaining, self.script = clock, last, remaining, script or {}
        self.calls = []

    def get(self, url, **kw):
        n = int(url.rsplit('page=', 1)[1])
        self.calls.append((n, self.clock()))
        if n in self.script:
            return self.script.pop(n)
        return Resp(200, page_body(n, self.last), {'X-RateLimit-Limit': '60',
                                                   'X-RateLimit-Remaining': str(self.remaining),
                                                   'total_count': str(self.last * 2)})


def page_body(n, last=30):
    items = [] if n > last else [{'id': n * 1000 + i} for i in range(2)]
    return json.dumps(items).encode()


class Registry:
    device_id, device_name = 'dev-1', 'win'

    def __init__(self, fail=False):
        self.fail, self.records = fail, {}

    def register(self, mid, record):
        if self.fail:
            raise ConnectionError('codehub down')
        self.records[mid] = record

    def acquisitions(self, mid):
        return [self.records[mid]] if mid in self.records else []


def seed_v1(root, pages=21):
    """Lay out sources like the manual driver did: pages 1..21 and next_page 22."""
    d = root / g.MID
    man = g._new_manifest()
    for n in range(1, pages + 1):
        body = page_body(n)
        rel = f'{g.MID}/pages/page-{n:05d}.json'
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(body)
        man['pages'][str(n)] = {'page': n, 'path': rel, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                                'items': 2, 'item_ids': [n * 1000, n * 1000 + 1], 'fetch_url': 'x',
                                'http_status': 200, 'headers': None, 'fetched_at': 'old', 'reused_from': None}
    (d / 'source-manifest.json').write_text(json.dumps(man), encoding='utf-8')
    (d / 'cursor.json').write_text(json.dumps({'next_page': pages + 1, 'exhausted': False, 'retry_at': None}),
                                   encoding='utf-8')
    return d


def step(root, clock, api, reg=None, **kw):
    reg = reg or Registry()
    return g.crawl_step(root, session=api, registry_factory=lambda: reg, clock=clock, sleep=clock.sleep,
                        disk_free=lambda r: 10 * 1024 ** 3, **kw)


def cursor(root):
    return json.loads((root / g.MID / 'cursor.json').read_text(encoding='utf-8'))


def test_imports_this_checkout():
    assert Path(g.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[2])


def test_resumes_v1_at_22_serially_and_registers_partial_snapshot(tmp_path):
    seed_v1(tmp_path)
    clock, reg = Clock(), Registry()
    api = Gitee(clock)
    res = step(tmp_path, clock, api, reg, max_pages=3)
    assert [n for n, _ in api.calls] == [22, 23, 24]
    assert all(b - a >= g.GAP for (_, a), (_, b) in zip(api.calls, api.calls[1:]))
    assert res['stop'] == 'page budget 3' and res['complete'] is False
    assert cursor(tmp_path)['next_page'] == 25 and not cursor(tmp_path)['exhausted']
    rec = reg.records[g.MID]
    assert rec['status'] == 'partial' and rec['scope']['pages'] == list(range(1, 25))
    snap1 = Path(rec['manifest_path'])
    first = snap1.read_bytes()
    assert hashlib.sha256(first).hexdigest() == rec['manifest_sha256']
    # A restart neither refetches 1..24 nor rewrites the registered snapshot.
    api2 = Gitee(clock)
    step(tmp_path, clock, api2, reg, max_pages=2)
    assert [n for n, _ in api2.calls] == [25, 26]
    assert snap1.read_bytes() == first and reg.records[g.MID]['manifest_path'] != str(snap1.as_posix())


def test_only_corrupt_page_is_refetched(tmp_path):
    d = seed_v1(tmp_path)
    (d / 'pages' / 'page-00005.json').write_bytes(b'[{"id": 1}]  corrupted')
    clock = Clock()
    api = Gitee(clock)
    res = step(tmp_path, clock, api, max_pages=2)
    assert res['repaired'] == [5] and [n for n, _ in api.calls] == [5, 22]
    assert g.sha256_file(d / 'pages' / 'page-00005.json') == hashlib.sha256(page_body(5)).hexdigest()


def test_retry_after_http_date_persists_and_survives_restart(tmp_path):
    seed_v1(tmp_path)
    clock = Clock()
    when = datetime.fromtimestamp(T0 + 7200, timezone.utc)
    api = Gitee(clock, script={22: Resp(429, b'{}', {'Retry-After': format_datetime(when, usegmt=True)})})
    res = step(tmp_path, clock, api)
    cur = cursor(tmp_path)
    assert cur['retry_at'] == when.isoformat(timespec='seconds') and cur['next_page'] == 22
    assert 'HTTP 429' in cur['pause_reason'] and res['new_pages'] == []
    api2 = Gitee(clock)
    res2 = step(tmp_path, clock, api2)
    assert api2.calls == [] and res2['stop'].startswith('paused until')
    clock.t = T0 + 7201
    assert step(tmp_path, clock, Gitee(clock), max_pages=1)['new_pages'] == [22]
    assert cursor(tmp_path)['retry_at'] is None


def test_403_without_retry_after_defaults_to_an_hour():
    assert g.pause_for(403, {}, T0) == (T0 + 3600, 'HTTP 403 (no Retry-After, default 3600s)')
    assert g.pause_for(503, {'retry-after': '120'}, T0)[0] == T0 + 120


def test_low_quota_keeps_page_then_pauses_an_hour(tmp_path):
    seed_v1(tmp_path)
    clock = Clock()
    res = step(tmp_path, clock, Gitee(clock, remaining=3))
    cur = cursor(tmp_path)
    assert res['new_pages'] == [22] and cur['next_page'] == 23
    assert g._ts(cur['retry_at']) >= T0 + 3600 and 'no reset header' in cur['pause_reason']


def test_batch_byte_cap_and_exhaustion(tmp_path, monkeypatch):
    seed_v1(tmp_path)
    monkeypatch.setattr(g, 'BATCH_CAP', g.PAGE_CAP + 10)
    clock = Clock()
    res = step(tmp_path, clock, Gitee(clock))
    assert res['stop'] == 'batch cap 150MiB' and res['new_pages'] == [22] and not cursor(tmp_path)['exhausted']
    monkeypatch.setattr(g, 'BATCH_CAP', 150 * 1024 ** 2)
    res = step(tmp_path, clock, Gitee(clock, last=24))
    assert res['stop'] == 'exhausted' and cursor(tmp_path)['exhausted_at_page'] == 25
    assert res['complete'] is False


def test_codehub_failure_keeps_latest_outbox_and_local_progress(tmp_path):
    seed_v1(tmp_path)
    clock, down = Clock(), Registry(fail=True)
    step(tmp_path, clock, Gitee(clock), down, max_pages=1)
    step(tmp_path, clock, Gitee(clock), down, max_pages=1)
    box = json.loads((tmp_path / 'pending-sync.json').read_text(encoding='utf-8'))['items'][g.MID]
    assert cursor(tmp_path)['next_page'] == 24 and box['record']['scope']['pages'][-1] == 23
    up = Registry()
    res = step(tmp_path, clock, Gitee(clock), up, max_pages=0)  # no new pages, but pending outbox is retried
    assert res['sync']['registered'] and up.records[g.MID]['scope']['pages'][-1] == 23
    assert json.loads((tmp_path / 'pending-sync.json').read_text(encoding='utf-8'))['items'] == {}


def test_single_instance_per_source_root(tmp_path):
    seed_v1(tmp_path)
    clock = Clock()
    with FileLock(str(tmp_path / '.crawl.lock')):
        with pytest.raises(g.SourceBusy):
            step(tmp_path, clock, Gitee(clock))
        with pytest.raises(g.SourceBusy):
            g.run_worker(tmp_path, clock=clock, sleep=clock.sleep)


def test_worker_pauses_with_heartbeat_and_exits_on_stop_file(tmp_path):
    seed_v1(tmp_path)
    clock = Clock()
    api = Gitee(clock, remaining=2)
    real_sleep = clock.sleep

    def sleep(s):
        real_sleep(s)
        info = json.loads((tmp_path / 'worker.json').read_text(encoding='utf-8'))
        if info['state'] == 'paused':
            g.request_stop(tmp_path)

    info = g.run_worker(tmp_path, clock=clock, sleep=sleep, session=api, registry_factory=Registry,
                        disk_free=lambda r: 10 * 1024 ** 3)
    saved = json.loads((tmp_path / 'worker.json').read_text(encoding='utf-8'))
    assert info['state'] == saved['state'] == 'stopped' and saved['create_time'] > 0 and saved['pid']
    assert saved['heartbeat_at'] and 'quota remaining 2' in saved['pause_reason'] and saved['retry_at']
    assert not (tmp_path / 'STOP').exists() and cursor(tmp_path)['next_page'] == 23
    st = g.status(tmp_path)
    assert st['worker_alive'] is True and st['complete'] is False  # this test process owns the record


# -- local catalog first (queue_root) ------------------------------------------

def catalog_store(tmp_path, monkeypatch):
    from backend.core.skilldep_materials import catalog

    monkeypatch.setattr(catalog, '_slow_sources', lambda: set())
    store = catalog.CatalogStore(tmp_path / 'queue', device_id=Registry.device_id)
    plan = {'id': g.MID, 'via': 'crawl', 'name': 'gitee', 'url': g.SOURCE_URL, 'size_mb_est': 1,
            'requires_login_or_purchase': False, 'acquisition': {}, 'cloud_transfer': {}}
    store.import_snapshot({'materials': {'count': 1, 'items': [plan]}, 'acquisitions': []}, source='fake')
    return store


def qstep(src, store, clock, api, reg, **kw):
    return step(src, clock, api, reg, queue_root=store.root, device_id=Registry.device_id, **kw)


def csv_row(store):
    import csv
    rows = csv.DictReader(store.csv_path.read_text(encoding='utf-8-sig').splitlines())
    return next(r for r in rows if r['id'] == g.MID)


def test_step_commits_to_catalog_offline_then_flushes_on_reconnect(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    seed_v1(src)
    store = catalog_store(tmp_path, monkeypatch)
    clock, down = Clock(), Registry(fail=True)
    res = qstep(src, store, clock, Gitee(clock), down, max_pages=1)
    sync = res['sync']
    assert sync['local_saved'] and sync['pending'] and not sync['remote_registered'] and 'codehub down' in sync['error']
    row = csv_row(store)
    assert row['local_status'] == 'partial' and '22 页' in row['local_scope_note']
    assert row['local_saved'] == 'True' and row['remote_registered'] == 'False'
    assert store.status()['pending_sync'] == 1
    assert not g._legacy_pending(g._paths(src))  # the catalog outbox is the authority now
    st = g.status(src, store.root)
    assert st['outbox_pending'] and st['catalog']['outbox_pending'] and not st['catalog']['remote_registered']
    assert st['catalog']['local_pages'] == 22 and st['complete'] is False

    up = Registry()
    res = qstep(src, store, clock, Gitee(clock), up, max_pages=0)  # no new pages: the queued record is retried
    assert res['sync']['remote_registered'] and not res['sync']['pending']
    assert up.records[g.MID]['scope']['pages'][-1] == 22 and up.records[g.MID]['device_id'] == 'dev-1'
    st = g.status(src, store.root)
    assert not st['outbox_pending'] and st['catalog']['remote_registered']
    assert csv_row(store)['remote_registered'] == 'True'
    # Same manifest again: nothing new recorded, nothing sent.
    before = len(up.records)
    res = qstep(src, store, clock, Gitee(clock), up, max_pages=0)
    assert res['sync']['already_recorded'] and res['sync']['remote_registered'] and len(up.records) == before


def test_busy_publisher_keeps_queue_and_does_not_block_pages(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    seed_v1(src)
    store = catalog_store(tmp_path, monkeypatch)
    clock, up = Clock(), Registry()
    with FileLock(str(store.root / 'publish.lock')):
        res = qstep(src, store, clock, Gitee(clock), up, max_pages=2)
    assert res['new_pages'] == [22, 23] and res['sync']['busy'] and res['sync']['pending']
    assert up.records == {} and store.outbox()['items'][g.MID]['record']['scope']['pages'][-1] == 23


def test_publish_current_adopts_snapshot_and_migrates_legacy_outbox(tmp_path, monkeypatch, capsys):
    src = tmp_path / 'src'
    seed_v1(src)
    store = catalog_store(tmp_path, monkeypatch)
    (src / 'pending-sync.json').write_text(json.dumps({'items': {g.MID: {'record': {'old': 1}, 'error': 'x'}}}),
                                           encoding='utf-8')
    assert g.status(src, store.root)['legacy_outbox_pending']
    with FileLock(str(src / '.crawl.lock')):
        with pytest.raises(g.SourceBusy):
            g.publish_current(src, store.root, device_id='dev-1', flush=False)

    code = g.main(['publish-current', '--root', str(src), '--queue-root', str(store.root),
                   '--device-id', 'dev-1', '--no-flush'])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out['local_saved'] and out['pending'] and out['migrated_legacy_outbox']
    assert out['pages_done'] == 21 and out['outbox_seq'] and out['complete'] is False
    local = store.load()['items'][g.MID]['local']
    snap = json.loads(Path(local['manifest_path']).read_text(encoding='utf-8'))
    assert local['manifest_sha256'] == out['manifest_sha256'] == g.sha256_file(Path(local['manifest_path']))
    assert sorted(map(int, snap['pages'])) == list(range(1, 22)) and snap['material_id'] == g.MID
    st = g.status(src, store.root)
    assert not st['legacy_outbox_pending'] and st['outbox_pending'] and st['catalog']['outbox_seq'] == out['outbox_seq']

    up = Registry()
    again = g.publish_current(src, store.root, device_id='dev-1', registry_factory=lambda: up)
    assert again['already_recorded'] and again['remote_registered'] and not again['pending']
    assert up.records[g.MID]['manifest_sha256'] == out['manifest_sha256']


def test_bad_catalog_keeps_legacy_pending(tmp_path):
    src = tmp_path / 'src'
    seed_v1(src)
    clock = Clock()
    res = step(src, clock, Gitee(clock), Registry(), queue_root=tmp_path / 'missing', device_id='dev-1', max_pages=1)
    assert not res['sync']['local_saved'] and 'CatalogError' in res['sync']['error']
    assert g._legacy_pending(g._paths(src)) and g.status(src)['outbox_pending']
    assert cursor(src)['next_page'] == 23
