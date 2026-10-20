import json
from pathlib import Path

import pytest
import requests

from backend.core.skilldep_materials import public_docs as pd
from backend.core.skilldep_materials.catalog import CatalogStore

MID = 'sd-test00000001'
URL = 'https://docs.example.gov/a.html'
PDF_URL = 'https://docs.example.gov/a.pdf'
BODY = '第一条 本指南适用于测试。' * 30
HTML = f'<html><body><div class="nav"><a>首页</a></div><div class="main"><h1>测试指南</h1><p>{BODY}</p>' \
       '<script>var x=1;</script></div></body></html>'.encode('utf-8')
PDF = b'%PDF-1.7\n' + b'x' * 100


class Resp:
    def __init__(self, url, status=200, body=b'', ctype='text/html; charset=utf-8', headers=None, history=()):
        self.url, self.status_code, self._body, self.history = url, status, body, list(history)
        self.headers = {'Content-Type': ctype, **(headers or {})}

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kwargs):
        self.calls.append(url)
        got = self.routes[url]
        if isinstance(got, Exception):
            raise got
        return got() if callable(got) else got


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t, self.slept = t, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def spec(*parts, **kw):
    parts = parts or (pd.PartSpec(key='main', url=URL, selector='div.main', min_chars=100,
                                  required_text=('测试指南',), items=13),)
    return pd.DocumentSpec(material_id=MID, name='测试指南', source_url=URL, allowed_hosts=('docs.example.gov',),
                           parts=parts, **kw)


ATTACH = pd.PartSpec(key='att', url=PDF_URL, kind='attachment', format='pdf', min_bytes=50)


def go(tmp_path, s, routes, clock=None, **kw):
    session = routes if isinstance(routes, Session) else Session(routes)
    clock = clock or Clock()
    out = pd.run(s, tmp_path, session=session, clock=clock, sleep=clock.sleep, min_free=0, **kw)
    return out, session


def test_imports_this_checkout():
    assert Path(pd.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[2])


def test_spec_rejects_unlisted_host_missing_selector_and_bad_format():
    with pytest.raises(pd.PublicDocsError, match='allowed_hosts'):
        spec(pd.PartSpec(key='x', url='https://evil.example/x', selector='div'))
    with pytest.raises(pd.PublicDocsError, match='selector'):
        spec(pd.PartSpec(key='x', url=URL))
    with pytest.raises(pd.PublicDocsError, match='format'):
        spec(pd.PartSpec(key='x', url=PDF_URL, kind='attachment', format='exe'))
    s = spec(ATTACH)
    assert pd.DocumentSpec.from_json(json.loads(json.dumps(s.to_json()))) == s


def test_body_text_rejects_shells_directories_and_missing_text():
    part = spec().parts[0]
    text, problem = pd.body_text(part, HTML)
    assert problem is None and '测试指南' in text and 'var x' not in text and '首页' not in text
    captcha = HTML.replace('测试指南'.encode(), '测试指南 请输入验证码'.encode())
    assert '验证' in pd.body_text(part, captcha)[1]
    links = ('<div class="main">测试指南' + ''.join(f'<a>通知标题{i}' + '很长' * 10 + '</a>' for i in range(40))
             + '</div>').encode()
    assert '目录' in pd.body_text(part, links)[1]
    assert '必需文字' in pd.body_text(part, HTML.replace('测试指南'.encode(), b'other'))[1]
    assert '未命中' in pd.body_text(part, b'<html><p>x</p></html>')[1]


def test_attachment_magic_and_html_disguise():
    assert pd.attachment_problem(ATTACH, PDF, 'application/pdf') is None
    assert 'HTML' in pd.attachment_problem(ATTACH, b'<!DOCTYPE html>' + b'x' * 80, 'application/pdf')
    assert '魔数' in pd.attachment_problem(ATTACH, b'PK\x03\x04' + b'x' * 80, 'application/octet-stream')


def test_full_document_with_attachment_is_downloaded_and_rerun_fetches_nothing(tmp_path):
    s = spec(spec().parts[0], ATTACH)
    routes = {URL: lambda: Resp(URL, body=HTML), PDF_URL: lambda: Resp(PDF_URL, body=PDF, ctype='application/pdf')}
    clock = Clock()
    out, session = go(tmp_path, s, routes, clock)
    man = out['manifest']
    assert out['outcome'] == 'done' and man['status'] == 'downloaded' and man['complete']
    assert session.calls == [URL, PDF_URL] and clock.slept == [pd.GAP]  # serial with the persisted gap
    assert [f['key'] for f in man['files']] == ['main', 'att'] and man['items'] == 14
    path = Path(man['manifest_path'])
    assert path.name == pd.sha256_file(path) + '.json' == man['manifest_sha256'] + '.json'
    assert man['files'][0]['sha256'] == pd._sha(HTML)
    again, session2 = go(tmp_path, s, routes)
    assert session2.calls == [] and again['fetched'] == [] and again['manifest_sha256'] == man['manifest_sha256']


def test_only_corrupt_part_is_refetched_and_spec_change_revalidates_offline(tmp_path):
    s = spec(spec().parts[0], ATTACH)
    routes = {URL: lambda: Resp(URL, body=HTML), PDF_URL: lambda: Resp(PDF_URL, body=PDF, ctype='application/pdf')}
    first, _ = go(tmp_path, s, routes)
    (tmp_path / MID / first['manifest']['files'][1]['path']).write_bytes(b'%PDF-broken')
    out, session = go(tmp_path, s, routes)
    assert session.calls == [PDF_URL] and out['manifest']['status'] == 'downloaded'
    stricter = spec(pd.PartSpec(key='main', url=URL, selector='div.main', min_chars=100,
                                required_text=('测试指南', '第十三条')), ATTACH)
    out, session = go(tmp_path, stricter, routes)
    assert session.calls == [] and out['manifest']['status'] == 'partial'
    assert '第十三条' in out['manifest']['missing'][0]['error']


def test_retry_after_is_persisted_and_honoured_after_restart(tmp_path):
    s = spec(spec().parts[0], ATTACH)
    clock = Clock()
    routes = {URL: lambda: Resp(URL, body=HTML), PDF_URL: lambda: Resp(PDF_URL, 429, headers={'Retry-After': '600'})}
    out, _ = go(tmp_path, s, routes, clock)
    assert out['outcome'] == 'paused' and out['retry_at'] == clock.t + 600
    assert out['manifest']['status'] == 'partial'  # the verified HTML part is kept
    clock.t += 300
    out, session = go(tmp_path, s, routes, clock)
    assert session.calls == [] and out['outcome'] == 'paused'
    clock.t += 301
    routes[PDF_URL] = lambda: Resp(PDF_URL, body=PDF, ctype='application/pdf')
    out, session = go(tmp_path, s, routes, clock)
    assert session.calls == [PDF_URL] and out['manifest']['status'] == 'downloaded' and out['retry_at'] is None


def test_network_error_backs_off_exponentially(tmp_path):
    clock = Clock()
    out, _ = go(tmp_path, spec(), {URL: requests.ConnectionError('reset')}, clock)
    assert out['outcome'] == 'paused' and out['retry_at'] == clock.t + pd.BACKOFF and out['manifest'] is None
    clock.t = out['retry_at'] + 1
    out, _ = go(tmp_path, spec(), {URL: requests.ConnectionError('reset')}, clock)
    assert out['retry_at'] == clock.t + 2 * pd.BACKOFF


def test_foreign_redirect_404_and_missing_attachment_stay_unverified(tmp_path):
    hop = Resp('https://docs.example.gov/old')
    routes = {URL: lambda: Resp('https://login.other.example/x', body=HTML, history=[hop]),
              PDF_URL: lambda: Resp(PDF_URL, 404)}
    out, _ = go(tmp_path, spec(spec().parts[0], ATTACH), routes)
    assert out['manifest'] is None
    assert '重定向' in out['parts']['main']['error'] and out['parts']['att']['http_status'] == 404
    routes[URL] = lambda: Resp(URL, body=HTML)
    out, _ = go(tmp_path, spec(spec().parts[0], ATTACH), routes)
    assert out['manifest']['status'] == 'partial' and out['manifest']['missing'][0]['key'] == 'att'


def test_scope_incomplete_spec_is_partial_even_when_all_parts_verify(tmp_path):
    out, _ = go(tmp_path, spec(scope_complete=False, note='仅部分年份'), {URL: lambda: Resp(URL, body=HTML)})
    assert out['manifest']['status'] == 'partial' and not out['manifest']['complete']


def test_file_cap_disk_floor_lock_and_hold(tmp_path):
    out, _ = go(tmp_path, spec(), {URL: lambda: Resp(URL, body=HTML)}, file_cap=100)
    assert '上限' in out['parts']['main']['error']
    clock = Clock()
    out = pd.run(spec(), tmp_path / 'b', session=Session({}), clock=clock, sleep=clock.sleep, min_free=1 << 62)
    assert out['outcome'] == 'disk_low'
    lock = pd.FileLock(str(pd.paths(tmp_path, MID)['lock']))
    with lock:
        assert pd.run(spec(), tmp_path, session=Session({}), min_free=0)['outcome'] == 'busy'
    with pytest.raises(pd.PublicDocsError, match='暂停'):
        pd.run(spec(hold_reason='owner running'), tmp_path, session=Session({}))


class Registry:
    device_id, device_name = 'dev-1', 'win'

    def __init__(self, fail=False):
        self.fail, self.records, self.puts = fail, {}, 0

    def register(self, mid, record):
        if self.fail:
            raise ConnectionError('codehub down')
        # CodeHub's schema (HTTP 422 otherwise): scope.pages are integers.
        assert all(isinstance(p, int) for p in record['scope']['pages'])
        self.puts += 1
        self.records[mid] = record

    def acquisitions(self, mid):
        return [{**self.records[mid], 'device_id': self.device_id}] if mid in self.records else []


def test_publish_saves_locally_first_keeps_outbox_offline_then_syncs_once(tmp_path, monkeypatch):
    from backend.core.skilldep_materials import catalog

    # This worktree only carries catalog/codehub/local_sync; the plan's samples module is not copied.
    monkeypatch.setattr(catalog, '_slow_sources', lambda: set())
    store = CatalogStore(tmp_path / 'queue', device_id='dev-1')
    store.import_snapshot({'materials': [{'id': MID, 'url': URL}]}, source='test')
    go(tmp_path / 'docs', spec(), {URL: lambda: Resp(URL, body=HTML)})
    down = Registry(fail=True)
    out = pd.publish(spec(), tmp_path / 'docs', store=store, registry_factory=lambda **kw: down)
    assert out['local_saved'] and out['newly_saved'] and not out['remote_registered']
    assert MID in store.outbox()['items'] and out['local']['status'] == 'downloaded'
    scope = out['local']['scope']
    assert set(scope) == {'items', 'pages', 'page_limit', 'note'} and scope['items'] == 13 and scope['pages'] == [1]
    up = Registry()
    out = pd.publish(spec(), tmp_path / 'docs', store=store, registry_factory=lambda **kw: up)
    assert out['remote_registered'] and not out['newly_saved'] and up.puts == 1
    assert up.records[MID]['manifest_sha256'] == out['local']['manifest_sha256'] and not store.outbox()['items']
    again = pd.publish(spec(), tmp_path / 'docs', store=store, registry_factory=lambda **kw: up)
    assert again['remote_registered'] and up.puts == 1  # same manifest: nothing re-recorded or re-sent


def test_builtin_specs_are_valid_and_gov_is_held_partial(tmp_path):
    gov = pd.SPECS['sd-2df4c1da864b']
    assert gov.hold_reason and not gov.scope_complete and len(gov.parts) == 7
    assert pd.SPECS['sd-18af9e8c5e5c'].scope_complete
    written = pd.write_specs(tmp_path)
    assert all(pd.DocumentSpec.from_json(json.loads(Path(p).read_text(encoding='utf-8'))) in pd.SPECS.values()
               for p in written)


def snapshot(man):
    """Each file a manifest references, with its hash read back from disk now."""
    d = Path(man['manifest_path']).parents[1]
    refs = [(f[k], f[h]) for f in man['files'] for k, h in (('path', 'sha256'), ('text_path', 'text_sha256'))
            if f.get(k)]
    return {rel: (want, pd.sha256_file(d / rel)) for rel, want in refs}


def intact(man):
    return pd.sha256_file(Path(man['manifest_path'])) == man['manifest_sha256'] and \
        all(want == got for want, got in snapshot(man).values())


def test_refetch_of_changed_body_keeps_both_snapshots_immutable(tmp_path):
    old, _ = go(tmp_path, spec(), {URL: lambda: Resp(URL, body=HTML)})
    before = snapshot(old['manifest'])
    html2 = HTML.replace('适用于测试'.encode(), '适用于新版测试'.encode())
    new, session = go(tmp_path, spec(), {URL: lambda: Resp(URL, body=html2)}, refetch=True)
    assert session.calls == [URL] and new['manifest']['status'] == 'downloaded'
    assert new['manifest_sha256'] != old['manifest_sha256']
    assert snapshot(old['manifest']) == before and intact(old['manifest']) and intact(new['manifest'])
    assert new['manifest']['files'][0]['sha256'] == pd._sha(html2) != old['manifest']['files'][0]['sha256']
    assert set(snapshot(old['manifest'])).isdisjoint(snapshot(new['manifest']))


def test_selector_change_keeps_old_text_and_adds_a_new_text_version(tmp_path):
    old, _ = go(tmp_path, spec(), {URL: lambda: Resp(URL, body=HTML)})
    wider = spec(pd.PartSpec(key='main', url=URL, selector='body', min_chars=100, required_text=('测试指南',),
                             items=13))
    new, session = go(tmp_path, wider, {})
    assert session.calls == [] and new['manifest']['status'] == 'downloaded'
    o, n = old['manifest']['files'][0], new['manifest']['files'][0]
    assert n['path'] == o['path'] and n['text_path'] != o['text_path'] and n['text_sha256'] != o['text_sha256']
    assert intact(old['manifest']) and intact(new['manifest'])


def test_legacy_layout_is_reused_unchanged_and_never_overwritten(tmp_path):
    part = spec().parts[0]
    text = pd.body_text(part, HTML)[0].encode('utf-8')
    d = tmp_path / MID
    (d / 'parts').mkdir(parents=True)
    (d / 'parts' / 'main.raw').write_bytes(HTML)
    (d / 'parts' / 'main.txt').write_bytes(text)
    (d / 'state.json').write_text(json.dumps({'parts': {'main': {
        'url': URL, 'final_url': URL, 'http_status': 200, 'content_type': 'text/html', 'bytes': len(HTML),
        'sha256': pd._sha(HTML), 'verified': True, 'format': 'html', 'text_sha256': pd._sha(text),
        'text_chars': 1, 'text_bytes': len(text)}}, 'failures': 0, 'retry_at': 0.0, 'runs': []}), encoding='utf-8')
    out, session = go(tmp_path, spec(), {})
    f = out['manifest']['files'][0]
    assert session.calls == [] and (f['path'], f['text_path']) == ('parts/main.raw', 'parts/main.txt')
    html2 = HTML.replace('适用于测试'.encode(), '适用于新版测试'.encode())
    go(tmp_path, spec(), {URL: lambda: Resp(URL, body=html2)}, refetch=True)
    assert (d / 'parts' / 'main.raw').read_bytes() == HTML and (d / 'parts' / 'main.txt').read_bytes() == text
    assert intact(out['manifest'])


def moved(**kw):
    return pd.PartSpec(key='main', url='https://docs.example.gov/b.html', selector='div.main', min_chars=100,
                       required_text=('NEW_VERSION_REQUIRED',), items=13, **kw)


@pytest.mark.parametrize('blocked', ['503', 'retry_at'])
def test_same_key_new_url_paused_never_borrows_old_verified_bytes(tmp_path, blocked):
    clock = Clock()
    s = spec(spec().parts[0], ATTACH)
    routes = {URL: lambda: Resp(URL, body=HTML), PDF_URL: lambda: Resp(PDF_URL, body=PDF, ctype='application/pdf')}
    old, _ = go(tmp_path, s, routes, clock)
    if blocked == 'retry_at':  # an earlier backoff is still pending: no request at all
        state = json.loads((tmp_path / MID / 'state.json').read_text(encoding='utf-8'))
        state['retry_at'] = clock.t + 600
        (tmp_path / MID / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    new_url = moved().url
    # the moved part comes first: the loop stops before the attachment is reached
    out, session = go(tmp_path, spec(moved(), ATTACH), {new_url: lambda: Resp(new_url, 503)}, clock)
    assert out['outcome'] == 'paused' and session.calls == ([new_url] if blocked == '503' else [])
    man = out['manifest']
    assert not man['complete'] and man['status'] == 'partial' and [f['key'] for f in man['files']] == ['att']
    assert man['missing'][0]['key'] == 'main' and man['missing'][0]['url'] == new_url
    assert not out['parts']['main']['verified'] and intact(old['manifest'])
    # an unreached part whose checks tightened is revalidated offline, not carried over
    strict = pd.PartSpec(key='att', url=PDF_URL, kind='attachment', format='pdf', min_bytes=10_000)
    out, _ = go(tmp_path, spec(moved(), strict), {new_url: lambda: Resp(new_url, 503)}, clock)
    assert out['manifest'] is None and '少于 10000' in out['parts']['att']['error']


def test_publish_refuses_a_manifest_made_from_another_spec(tmp_path, monkeypatch):
    from backend.core.skilldep_materials import catalog

    monkeypatch.setattr(catalog, '_slow_sources', lambda: set())
    store = CatalogStore(tmp_path / 'queue', device_id='dev-1')
    store.import_snapshot({'materials': [{'id': MID, 'url': URL}]}, source='test')
    go(tmp_path / 'docs', spec(), {URL: lambda: Resp(URL, body=HTML)})
    up = Registry()
    out = pd.publish(spec(moved()), tmp_path / 'docs', store=store, registry_factory=lambda **kw: up)
    assert not out['local_saved'] and '另一 spec' in out['reason'] and up.puts == 0
    assert 'manifest_sha256' not in store.load()['items'][MID]['local'] and MID not in store.outbox()['items']
    # a tampered payload is not published either, even for the matching spec
    man = go(tmp_path / 'docs', spec(), {})[0]['manifest']
    (tmp_path / 'docs' / MID / man['files'][0]['text_path']).write_bytes(b'x')
    out = pd.publish(spec(), tmp_path / 'docs', store=store, registry_factory=lambda **kw: up)
    assert not out['local_saved'] and '哈希不符' in out['reason']
