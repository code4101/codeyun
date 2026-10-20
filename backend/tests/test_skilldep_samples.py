import hashlib
import json

import pytest
import requests

from backend.core.skilldep_materials import samples
from backend.core.skilldep_materials import __main__ as cli
from backend.core.skilldep_materials.samples import MB, SAMPLE_TOTAL_LIMIT, sample, sample_and_register

PBC, CSRC, GITEE = 'sd-98bf2d2f5d43', 'sd-3b648dbbe220', 'sd-b70266a40bc8'
PBC_LIST_URL = samples.HTML_SOURCES[PBC]['list_url']
CSRC_LIST_URL = samples.HTML_SOURCES[CSRC]['list_url']
GITEE_URL = samples.API_SOURCES[GITEE]['list_url'] + '&page=1'
CSRC_A1 = 'https://www.csrc.gov.cn/csrc/c106256/c1/content.shtml'
CSRC_A2 = 'https://www.csrc.gov.cn/csrc/c106256/c2/content.shtml'
PLAN = [
    {'id': PBC, 'name': '人民银行部门规章', 'via': 'crawl', 'requires_login_or_purchase': False, 'url': PBC_LIST_URL},
    {'id': CSRC, 'name': '证监会规章库', 'via': 'crawl', 'requires_login_or_purchase': False, 'url': CSRC_LIST_URL},
    {'id': GITEE, 'name': 'Gitee issues', 'via': 'crawl', 'requires_login_or_purchase': False,
     'url': samples.API_SOURCES[GITEE]['list_url']},
]


def page(title, links=()):
    hrefs = ''.join(f'<a href="{u}">x</a>' for u in links)
    pad = ' ' * 600
    return f'<html><head><meta charset="utf-8"><title>{title}</title></head><body>{hrefs}{pad}</body></html>'.encode()


def bodies():
    return {
        PBC_LIST_URL: page('部门规章', ['/tiaofasi/144941/144957/111/index.html',
                                    '/tiaofasi/144941/144957/222/index.html',
                                    '/tiaofasi/144941/144957/333/index.html', '/other/1.html']),
        'http://www.pbc.gov.cn/tiaofasi/144941/144957/111/index.html': page('令1'),
        'http://www.pbc.gov.cn/tiaofasi/144941/144957/222/index.html': page('令2'),
        CSRC_LIST_URL: page('规章库', ['/csrc/c106256/c1/content.shtml', '/csrc/c106256/c2/content.shtml']),
        CSRC_A1: page('规章1'),
        CSRC_A2: page('规章2'),
        GITEE_URL: json.dumps([{'number': f'I{i}', 'title': 't'} for i in range(100)]).encode(),
    }


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status_code, self.headers = body, status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def iter_content(self, size):
        for i in range(0, len(self.body), 1024):
            yield self.body[i:i + 1024]


class FakeSession:
    def __init__(self, routes=None, script=None):
        self.routes, self.script, self.calls = routes or bodies(), script or {}, []

    def get(self, url, **kwargs):
        self.calls.append(url)
        queued = self.script.get(url)
        if queued:
            return queued.pop(0)
        return FakeResponse(self.routes[url])


class NoNetwork:
    def get(self, url, **kwargs):
        raise AssertionError(f'network forbidden: {url}')


def run(tmp_path, session, ids=None, **kw):
    sleeps = []
    manifest = sample(PLAN, ids, root=tmp_path, session=session, sleep=sleeps.append, **kw)
    return manifest, sleeps


def by_id(manifest):
    return {r['material_id']: r for r in manifest['resources']}


def test_default_root_and_hard_cap():
    assert samples.DEFAULT_SAMPLES_ROOT.as_posix() == 'F:/CodeYun/materials/m2610skilldependency/slow-samples'
    assert SAMPLE_TOTAL_LIMIT == 20 * MB


def test_samples_fixed_scope_then_rerun_reuses_without_network(tmp_path):
    session = FakeSession()
    first, sleeps = run(tmp_path, session)
    assert first['run'] == {'reused': 0, 'downloaded': 7, 'failed': 0, 'requests': 7}
    assert sleeps == [samples.THROTTLE_SECONDS] * 6  # serial, throttled between requests
    res = by_id(first)
    assert [f['source_url'].split('/')[-2] for f in res[PBC]['files'][1:]] == ['111', '222']
    for r in res.values():
        assert r['scope'] == 'sample' and r['status'] == 'sampled' and r['coverage'].startswith('仅')
        for f in r['files']:
            data = (tmp_path / r['material_id'] / f['path'].rsplit('/', 1)[-1]).read_bytes()
            assert f['bytes'] == len(data) and f['sha256'] == hashlib.sha256(data).hexdigest()
    assert first['total_payload_bytes'] == sum(len(b) for b in bodies().values())

    again, sleeps = run(tmp_path, NoNetwork())
    assert again['run'] == {'reused': 7, 'downloaded': 0, 'failed': 0, 'requests': 0} and sleeps == []
    assert again['total_payload_bytes'] == first['total_payload_bytes']


def test_damaged_file_is_refetched_alone(tmp_path):
    run(tmp_path, FakeSession())
    (tmp_path / CSRC / 'article_02.html').write_bytes(b'tampered')
    session = FakeSession()
    again, _ = run(tmp_path, session)
    assert session.calls == [CSRC_A2]
    assert again['run'] == {'reused': 6, 'downloaded': 1, 'failed': 0, 'requests': 1}
    assert (tmp_path / CSRC / 'article_02.html').read_bytes() == page('规章2')


def test_denial_page_is_kept_as_rejected_not_ok(tmp_path):
    routes = bodies()
    routes[CSRC_A1] = page('请输入验证码')
    manifest, _ = run(tmp_path, FakeSession(routes), [CSRC])
    res = by_id(manifest)[CSRC]
    bad = res['files'][1]
    assert bad['status'] == 'rejected' and bad['path'].endswith('.rejected') and 'denial_marker' in bad['error']
    assert res['scope'] == 'partial' and res['status'] == 'partial' and res['valid_files'] == 2
    assert not (tmp_path / CSRC / 'article_01.html').exists()
    session = FakeSession()  # a rejected copy is never reused
    again, _ = run(tmp_path, session, [CSRC])
    assert session.calls == [CSRC_A1] and by_id(again)[CSRC]['status'] == 'sampled'


def test_list_without_links_and_error_json_are_failures(tmp_path):
    routes = bodies()
    routes[PBC_LIST_URL] = page('部门规章')
    routes[GITEE_URL] = b'{"message": "rate limit exceeded"}'
    manifest, _ = run(tmp_path, FakeSession(routes), [PBC, GITEE])
    res = by_id(manifest)
    assert res[PBC]['status'] == 'failed' and res[PBC]['files'][0]['error'] == 'no_article_links'
    assert res[GITEE]['status'] == 'failed' and res[GITEE]['files'][0]['error'] == 'error_json'
    assert manifest['total_payload_bytes'] == 0 and manifest['run']['downloaded'] == 0


def test_short_retry_after_is_honoured_once(tmp_path):
    session = FakeSession(script={GITEE_URL: [FakeResponse(b'', 429, {'Retry-After': '7'})]})
    manifest, sleeps = run(tmp_path, session, [GITEE])
    assert sleeps == [7] and len(session.calls) == 2 and by_id(manifest)[GITEE]['status'] == 'sampled'


def test_long_or_repeated_429_stops_the_source(tmp_path):
    session = FakeSession(script={PBC_LIST_URL: [FakeResponse(b'', 429, {'Retry-After': '3600'})]})
    manifest, sleeps = run(tmp_path, session, [PBC])
    assert len(session.calls) == 1 and sleeps == []
    assert by_id(manifest)[PBC]['status'] == 'failed' and 'Retry-After' in by_id(manifest)[PBC]['errors'][0]

    session = FakeSession(script={PBC_LIST_URL: [FakeResponse(b'', 429, {'Retry-After': '5'}) for _ in range(2)]})
    _, sleeps = run(tmp_path, session, [PBC])
    assert len(session.calls) == 2 and sleeps == [5]


def test_request_cap_per_source(tmp_path, monkeypatch):
    monkeypatch.setattr(samples, 'REQUESTS_PER_SOURCE', 2)
    manifest, _ = run(tmp_path, FakeSession(), [PBC])
    res = by_id(manifest)[PBC]
    assert res['requests_used'] == 2 and res['status'] == 'partial'
    assert '请求上限' in res['files'][2]['error']


def test_total_byte_cap_is_hard_and_not_raisable(tmp_path):
    big = bodies()
    big[GITEE_URL] = json.dumps([{'number': i, 'body': 'x' * 1000} for i in range(2000)]).encode()  # ~2 MB
    manifest, _ = run(tmp_path, FakeSession(big), [GITEE], max_total_bytes=1 * MB)
    res = by_id(manifest)[GITEE]
    assert res['status'] == 'failed' and 'DownloadTooLarge' in res['errors'][0]
    assert not [p for p in tmp_path.rglob('*')
                if p.is_file() and p.name not in (samples.MANIFEST, samples.SOURCE_MANIFEST)]
    manifest, _ = run(tmp_path, FakeSession(), [GITEE], max_total_bytes=10 ** 12)
    assert manifest['hard_cap_bytes'] == SAMPLE_TOTAL_LIMIT


def test_refuses_changed_plan_url_or_unknown_ids(tmp_path):
    changed = [dict(PLAN[0], url='http://www.pbc.gov.cn/other.html'), *PLAN[1:]]
    with pytest.raises(ValueError):
        sample(changed, [PBC], root=tmp_path, session=NoNetwork())
    with pytest.raises(ValueError):
        sample(PLAN, ['sd-unknown'], root=tmp_path, session=NoNetwork())


class FakeRegistry:
    device_id, device_name = 'node-1', 'codepc_mf'

    def __init__(self):
        self.records, self.puts = {}, 0

    def materials(self):
        return PLAN

    def register(self, material_id, record):
        self.puts += 1
        self.records[material_id] = {**record, 'scope': {'page_limit': None, 'pages': None, 'items': None,
                                                         'note': '', **(record.get('scope') or {})}}
        return self.records[material_id]

    def acquisitions(self, material_id):
        return [self.records[material_id]] if material_id in self.records else []


def test_registers_partial_with_source_manifest_hash_and_scope(tmp_path):
    registry = FakeRegistry()
    out = sample_and_register(registry, root=tmp_path, session=FakeSession(), sleep=lambda s: None)
    rows = {r['material_id']: r for r in out['results']}
    assert all(r['status'] == 'partial' and r['registered'] for r in rows.values())
    assert not any(r['status'] == 'downloaded' or r.get('sha256') for r in registry.records.values())
    pbc, gitee = rows[PBC], rows[GITEE]
    assert pbc['path'] == (tmp_path / PBC).as_posix() and pbc['manifest_path'].endswith(f'{PBC}/manifest.json')
    assert pbc['manifest_sha256'] == hashlib.sha256((tmp_path / PBC / 'manifest.json').read_bytes()).hexdigest()
    assert pbc['scope'] == {'page_limit': 1, 'pages': [1], 'items': 2, 'note': samples.COVERAGE['html'].format(n=2)}
    assert gitee['scope']['items'] == 100
    assert pbc['size_bytes'] == sum(len(b) for u, b in bodies().items() if 'pbc' in u)

    again = sample_and_register(registry, root=tmp_path, session=NoNetwork(), sleep=lambda s: None)
    assert again['run']['requests'] == 0 and len(registry.records) == 3
    assert [r['manifest_sha256'] for r in again['results']] == [r['manifest_sha256'] for r in out['results']]


class FakeHub(FakeRegistry):
    def verify_peer(self):
        return {}


def test_cli_samples_exit_code_follows_completeness(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'CodeHubSkillDep', FakeHub)
    monkeypatch.setattr(samples, 'THROTTLE_SECONDS', 0)
    routes = bodies()
    routes[CSRC_A1] = page('请输入验证码')
    monkeypatch.setattr(samples.requests, 'Session', lambda: FakeSession(routes))
    assert cli.main(['samples', '--root', str(tmp_path)]) == 1
    monkeypatch.setattr(samples.requests, 'Session', lambda: FakeSession())
    assert cli.main(['samples', '--root', str(tmp_path)]) == 0
    monkeypatch.setattr(samples.requests, 'Session', NoNetwork)
    capsys.readouterr()
    assert cli.main(['samples', '--root', str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)['run']['requests'] == 0
