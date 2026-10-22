import json

from backend.core.skilldep_materials.listing_crawl import ListingCrawler, check_blocked, json_path


class Resp:
    def __init__(self, status=200, body=b'', ctype='application/json'):
        self.status_code, self.content, self.encoding = status, body, 'utf-8'
        self.headers = {'Content-Type': ctype}

    def iter_content(self, n):
        for i in range(0, len(self.content), 4):
            yield self.content[i:i + 4]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def close(self):
        pass


class Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, **kw):
        self.calls.append(url)
        r = self.routes[url]
        return r() if callable(r) else r


def page(items, total):
    return Resp(body=json.dumps({'announcements': items, 'totalAnnouncement': total}).encode())


def cfg(tmp_path, **kw):
    return {'material_id': 'sd-x', 'list_url': 'http://l/?p={page}', 'items_path': 'announcements',
            'total_path': 'totalAnnouncement', 'key_field': 'id', 'attachment_url': 'http://s/{u}',
            'out_dir': str(tmp_path / 'out'), 'scope_note': '最新 {count} 份/共 {total} 份', **kw}


class Reg:
    device_id, device_name = 'dev', 'Dev'

    def __init__(self):
        self.recs = {}

    def register(self, mid, rec):
        self.recs[mid] = rec

    def acquisitions(self, mid):
        return [self.recs[mid]]


def test_pages_download_resume_and_register(tmp_path):
    routes = {'http://l/?p=1': page([{'id': 1, 'u': 'a.pdf', 't': '<em>增资</em>'}, {'id': 2, 'u': 'b.pdf'}], 3),
              'http://l/?p=2': page([{'id': 3, 'u': 'c.pdf'}], 3),
              'http://s/a.pdf': Resp(body=b'%PDF-aaaa'), 'http://s/b.pdf': Resp(body=b'%PDF-bb'),
              'http://s/c.pdf': Resp(body=b'%PDF-c')}
    sleeps = []
    s = Session(routes)
    c = ListingCrawler(cfg(tmp_path, max_items=2), session=s, sleep=sleeps.append, rand=lambda a, b: 15, log=lambda m: None)
    st = c.run()
    assert st['status'] == 'done' and st['total'] == 3
    man = json.loads((tmp_path / 'out/manifest.json').read_text('utf-8'))
    assert man['files']['1']['source_url'] == 'http://s/a.pdf' and man['files']['1']['size_bytes'] == 9
    assert len(man['files']) == 2 and 'http://l/?p=2' not in s.calls
    assert sleeps == [15, 15]
    assert '增资' in (tmp_path / 'out/listing.csv').read_text('utf-8-sig') and '<em>' not in (tmp_path / 'out/listing.csv').read_text('utf-8-sig')
    # resume: existing files skipped, only c.pdf fetched
    s2 = Session(routes)
    c2 = ListingCrawler(cfg(tmp_path, max_items=3), session=s2, sleep=lambda x: None, log=lambda m: None)
    c2.run()
    assert [u for u in s2.calls if u.startswith('http://s/')] == ['http://s/c.pdf']
    reg = Reg()
    res = c2.register(reg)
    assert res['registered']
    rec = reg.recs['sd-x']
    assert rec['status'] == 'downloaded' and rec['scope']['note'] == '最新 3 份/共 3 份' and rec['size_bytes'] == 9 + 7 + 6


def test_blocked_stops_without_retry(tmp_path):
    routes = {'http://l/?p=1': page([{'id': 1, 'u': 'a.pdf'}, {'id': 2, 'u': 'b.pdf'}], 2),
              'http://s/a.pdf': Resp(body='<html>访问过于频繁</html>'.encode(), ctype='text/html'),
              'http://s/b.pdf': Resp(body=b'%PDF')}
    s = Session(routes)
    st = ListingCrawler(cfg(tmp_path), session=s, sleep=lambda x: None, log=lambda m: None).run()
    assert st['status'] == 'blocked' and st['blocked_at'] and s.calls.count('http://s/a.pdf') == 1
    assert 'http://s/b.pdf' not in s.calls
    s = Session({'http://l/?p=1': Resp(status=412)})
    st = ListingCrawler(cfg(tmp_path / 'x'), session=s, sleep=lambda x: None, log=lambda m: None).run()
    assert st['status'] == 'blocked' and st['blocked_reason'] == 'HTTP 412' and len(s.calls) == 1


def test_helpers():
    assert json_path({'a': [{'b': 1}]}, 'a.0.b') == 1
    assert check_blocked(429, b'') == 'HTTP 429' and check_blocked(200, '限制访问'.encode()) and not check_blocked(200, b'ok')


def test_5xx_retries_then_skips_and_continues(tmp_path):
    import requests
    calls = {'a': 0}

    def flaky_a():
        calls['a'] += 1
        return Resp(status=502) if calls['a'] < 3 else Resp(body=b'%PDF-a')

    def timeout():
        raise requests.Timeout('read timed out')

    routes = {'http://l/?p=1': page([{'id': 1, 'u': 'a.pdf'}, {'id': 2, 'u': 'b.pdf'}, {'id': 3, 'u': 'c.pdf'},
                                     {'id': 4, 'u': 'd.pdf'}], 4),
              'http://l/?p=2': page([], 4),
              'http://s/a.pdf': flaky_a, 'http://s/b.pdf': Resp(status=503), 'http://s/c.pdf': timeout,
              'http://s/d.pdf': Resp(body=b'%PDF-d')}
    sleeps, s = [], Session(routes)
    c = ListingCrawler(cfg(tmp_path), session=s, sleep=sleeps.append, rand=lambda a, b: a, log=lambda m: None)
    st = c.run()
    assert st['status'] == 'done' and calls['a'] == 3
    assert s.calls.count('http://s/b.pdf') == 3 and s.calls.count('http://s/c.pdf') == 3
    assert sleeps.count(60) == 6  # two retries each for a, b and c
    assert [(x['url'], x['status_code']) for x in st['skipped']] == [('http://s/b.pdf', 503), ('http://s/c.pdf', None)]
    assert all(x['at'] for x in st['skipped'])
    assert set(c.manifest['files']) == {'1', '4'}
    rec = c.record()
    assert '2 份' in rec['scope']['note'] and len(rec['scope']['skipped']) == 2


def test_three_consecutive_5xx_stops_as_blocked(tmp_path):
    routes = {'http://l/?p=1': page([{'id': i, 'u': f'{i}.pdf'} for i in range(1, 5)], 4),
              **{f'http://s/{i}.pdf': Resp(status=500) for i in range(1, 4)}, 'http://s/4.pdf': Resp(body=b'%PDF')}
    s = Session(routes)
    st = ListingCrawler(cfg(tmp_path), session=s, sleep=lambda x: None, log=lambda m: None).run()
    assert st['status'] == 'blocked' and '连续 3 条' in st['blocked_reason'] and len(st['skipped']) == 3
    assert 'http://s/4.pdf' not in s.calls


def test_429_not_retried(tmp_path):
    routes = {'http://l/?p=1': page([{'id': 1, 'u': 'a.pdf'}], 1), 'http://s/a.pdf': Resp(status=429)}
    s = Session(routes)
    st = ListingCrawler(cfg(tmp_path), session=s, sleep=lambda x: None, log=lambda m: None).run()
    assert st['status'] == 'blocked' and s.calls.count('http://s/a.pdf') == 1 and not st['skipped']


ART = ('<html><head><title>T</title><meta property="article:published_time" content="2026-10-20T09:08:00Z">'
       '</head><body><h1>{t}</h1><div class="c"><p>第一段</p><p>{t}正文</p></div></body></html>')


def html(body, status=200):
    return Resp(status=status, body=body.encode('utf-8'), ctype='text/html')


def acfg(tmp_path, **kw):
    return {'material_id': 'sd-h', 'mode': 'article', 'list_format': 'html', 'list_url': 'http://g/list?p={page}',
            'link_selector': 'h3 a', 'link_pattern': r'^/\d+\.html$', 'content_selector': 'div.c',
            'out_dir': str(tmp_path / 'out'), 'scope_note': '{count} 篇', **kw}


def test_html_list_articles_saved_with_text_and_resume(tmp_path):
    lst = ('<h3><a href="/1.html">A</a></h3><h3><a href="/wl?m=x">ext</a></h3>'
           '<h3><a href="/2.html">B</a></h3><h3><a href="/1.html">A again</a></h3>')
    routes = {'http://g/list?p=1': html(lst), 'http://g/list?p=2': html('<h3><a href="/3.html">C</a></h3>'),
              'http://g/1.html': html(ART.format(t='甲')), 'http://g/2.html': html(ART.format(t='乙')),
              'http://g/3.html': html(ART.format(t='丙'))}
    s = Session(routes)
    sleeps = []
    st = ListingCrawler(acfg(tmp_path, max_items=3), session=s, sleep=sleeps.append, rand=lambda a, b: 12,
                        log=lambda m: None).run()
    assert st['status'] == 'done' and 'http://g/wl?m=x' not in s.calls
    out = tmp_path / 'out'
    man = json.loads((out / 'manifest.json').read_text('utf-8'))
    assert sorted(e['path'] for e in man['files'].values()) == ['files/1.html', 'files/2.html', 'files/3.html']
    txt = (out / 'files/1.txt').read_text('utf-8')
    assert txt.startswith('标题：甲\n发布时间：2026-10-20T09:08:00Z\n原文：http://g/1.html\n') and '甲正文' in txt
    assert (out / 'files/1.html').read_bytes().startswith(b'<html>')
    assert 'http://g/2.html' in (out / 'listing.csv').read_text('utf-8-sig')
    assert sleeps == [12] * 4
    s2 = Session(routes)
    ListingCrawler(acfg(tmp_path, max_items=3), session=s2, sleep=lambda x: None, log=lambda m: None).run()
    assert not [u for u in s2.calls if u.endswith('.html')]


def test_json_cursor_sections_filter_and_scope(tmp_path):
    def lp(items, cur):
        return Resp(body=json.dumps({'data': {'list': items, 'next': cur}}).encode())
    routes = {'http://a/?c=1': lp([{'url': '/1.html', 'w': 1}, {'url': '/2.html', 'w': 0}], 'k1'),
              'http://a/?c=1&cur=k1': lp([{'url': '/3.html', 'w': 0}], None),
              'http://a/?c=2': lp([{'url': '/4.html', 'w': 0}], None),
              **{f'http://g/{i}.html': html(ART.format(t=str(i))) for i in range(1, 5)}}
    cfg_ = acfg(tmp_path, list_format='json', items_path='data.list', cursor_path='data.next',
                article_url='http://g{url}', item_filter={'w': 0}, max_items=5,
                sections=[{'name': '策划', 'list_url_first': 'http://a/?c=1', 'list_url': 'http://a/?c=1&cur={cursor}'},
                          {'name': '程序', 'list_url_first': 'http://a/?c=2', 'list_url': 'http://a/?c=2&cur={cursor}'}])
    del cfg_['list_url']
    c = ListingCrawler(cfg_, session=Session(routes), sleep=lambda x: None, log=lambda m: None)
    st = c.run()
    assert st['status'] == 'done' and st['sections'] == {'策划': 2, '程序': 1}
    assert (tmp_path / 'out/files/策划/3.txt').is_file() and (tmp_path / 'out/files/程序/4.html').is_file()
    rec = c.record()
    assert rec['scope']['note'] == '3 篇：策划 2 篇，程序 1 篇' and rec['source_url'] == 'http://a/?c=1'


def test_article_captcha_and_block_stop(tmp_path):
    lst = '<h3><a href="/1.html">A</a></h3><h3><a href="/2.html">B</a></h3>'
    routes = {'http://g/list?p=1': html(lst), 'http://g/1.html': html('<html>请输入验证码</html>')}
    s = Session(routes)
    st = ListingCrawler(acfg(tmp_path), session=s, sleep=lambda x: None, log=lambda m: None).run()
    assert st['status'] == 'blocked' and '验证码' in st['blocked_reason'] and 'http://g/2.html' not in s.calls
    routes = {'http://g/list?p=1': html(lst), 'http://g/1.html': html('', status=412)}
    st = ListingCrawler(acfg(tmp_path), session=Session(routes), sleep=lambda x: None, log=lambda m: None).run()
    assert st['status'] == 'blocked' and st['blocked_reason'] == 'HTTP 412'


def test_article_5xx_retried_then_skipped(tmp_path):
    lst = '<h3><a href="/1.html">A</a></h3><h3><a href="/2.html">B</a></h3>'
    routes = {'http://g/list?p=1': html(lst), 'http://g/list?p=2': html(''),
              'http://g/1.html': html('', status=502), 'http://g/2.html': html(ART.format(t='乙'))}
    s = Session(routes)
    waits = []
    st = ListingCrawler(acfg(tmp_path, retry_wait_seconds=[60, 180]), session=s, sleep=waits.append,
                        rand=lambda a, b: a, log=lambda m: None).run()
    assert st['status'] == 'done' and s.calls.count('http://g/1.html') == 3
    assert [x['url'] for x in st['skipped']] == ['http://g/1.html'] and waits.count(60) == 2
    assert (tmp_path / 'out/files/2.txt').is_file()


class ScopeDroppingReg(Reg):
    """CodeHub keeps only its own scope fields; extra keys never read back."""

    def register(self, mid, rec):
        self.recs[mid] = {**rec, 'scope': {k: rec['scope'].get(k) for k in ('page_limit', 'pages', 'items', 'note')}}


def test_register_sends_only_persisted_scope_and_reports_mismatch(tmp_path):
    from backend.core.skilldep_materials.acquire import register_and_verify
    routes = {'http://l/?p=1': page([{'id': 1, 'u': 'a.pdf'}], 5), 'http://s/a.pdf': Resp(body=b'%PDF-a')}
    c = ListingCrawler(cfg(tmp_path, max_items=1), session=Session(routes), sleep=lambda x: None, log=lambda m: None)
    c.run()
    reg = ScopeDroppingReg()
    res = c.register(reg)
    assert res['registered'] and res.get('register_error') is None
    assert reg.recs['sd-x']['status'] == 'partial' and reg.recs['sd-x']['scope']['note'] == '最新 1 份/共 5 份'
    assert c.state['registration']['registered'] is True
    # a read-back that differs is never silent: the differing fields land in register_error
    rec = {'device_id': 'dev', **c.record()}
    res = register_and_verify(ScopeDroppingReg(), 'sd-x', rec)
    assert not res['registered'] and 'scope.total' in res['register_error'] and 'scope.skipped' in res['register_error']

    class Stale(Reg):
        def register(self, mid, rec):
            self.recs[mid] = {**rec, 'size_bytes': 1}

    res = register_and_verify(Stale(), 'sd-x', {**rec, 'scope': {}})
    assert not res['registered'] and 'size_bytes' in res['register_error']

    class Missing(Reg):
        def acquisitions(self, mid):
            return []

    res = register_and_verify(Missing(), 'sd-x', rec)
    assert not res['registered'] and '未找到' in res['register_error']


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleep_log.append(round(s))
        self.t += s


def list_crawler(tmp_path, routes, items, registry=None):
    from backend.core.skilldep_materials.listing_crawl import ListModeCrawler
    clock = Clock()
    clock.sleep_log = []
    s = Session(routes)
    c = ListModeCrawler({'mode': 'list', 'material_id': 'sd-l', 'out_dir': str(tmp_path / 'out'), 'list': items},
                        session=s, registry=registry, sleep=clock.sleep, rand=lambda a, b: 15, clock=clock,
                        log=lambda m: None)
    return c, s, clock


ARTICLE = ('<html><head><title>T</title></head><body><h1>规定</h1><div class="TRS_Editor">第一条 正文'
           '<a href="/files/fj.pdf">附件</a><a href="http://other.cn/x.pdf">外站</a></div>'
           '<span>2021-12-10</span></body></html>').encode()


def test_list_mode_files_pages_attachments_and_interleave(tmp_path):
    routes = {'http://www.a.gov.cn/p.shtml': Resp(body=ARTICLE, ctype='text/html; charset=utf-8'),
              'http://www.a.gov.cn/files/fj.pdf': Resp(body=b'%PDF-fj'),
              'http://www.a.gov.cn/d.pdf': Resp(body=b'%PDF-d'),
              'http://b.gov.cn/x.xlsx': Resp(body=b'PKxlsx', ctype='application/octet-stream')}
    items = [{'name': '规定', 'url': 'http://www.a.gov.cn/p.shtml'}, {'name': '样表', 'url': 'http://b.gov.cn/x.xlsx'},
             {'name': '整理规则', 'url': 'http://www.a.gov.cn/d.pdf'}]
    c, s, clock = list_crawler(tmp_path, routes, items)
    st = c.run()
    assert st['status'] == 'done' and st['skipped'] == []
    # site b goes in while site a waits out its own gap; only a's gaps are slept
    assert s.calls == ['http://www.a.gov.cn/p.shtml', 'http://www.a.gov.cn/files/fj.pdf', 'http://b.gov.cn/x.xlsx',
                       'http://www.a.gov.cn/d.pdf']
    assert clock.sleep_log == [15, 15]
    out = tmp_path / 'out'
    txt = (out / 'files/01-规定.txt').read_text('utf-8')
    assert '标题：规定' in txt and '发布日期：2021-12-10' in txt and '原文：http://www.a.gov.cn/p.shtml' in txt
    assert (out / 'files/01-规定_附件/fj.pdf').read_bytes() == b'%PDF-fj'
    assert (out / 'files/02-样表.xlsx').read_bytes() == b'PKxlsx'
    assert 'http://other.cn/x.pdf' not in s.calls
    reg = Reg()
    c.register(reg)
    assert reg.recs['sd-l']['status'] == 'downloaded' and '清单 3/3 份' in reg.recs['sd-l']['scope']['note']
    # resume: nothing refetched
    c2, s2, _ = list_crawler(tmp_path, routes, items)
    c2.run()
    assert s2.calls == []


class BlockReg(Reg):
    def __init__(self, blocks):
        super().__init__()
        self.blocks, self.puts = blocks, []

    def site_blocks(self):
        return self.blocks

    def put_site_block(self, site, reason):
        self.puts.append(site)
        return {'site': site, 'active': True}

    def register(self, mid, rec):
        if rec.get('status') != 'downloading':
            self.recs[mid] = rec


def test_list_mode_block_skip_and_empty_url(tmp_path):
    routes = {'http://www.a.gov.cn/1.pdf': Resp(status=429), 'http://b.gov.cn/2.pdf': Resp(body=b'%PDF-2')}
    items = [{'name': 'one', 'url': 'http://www.a.gov.cn/1.pdf'}, {'name': 'one-b', 'url': 'http://www.a.gov.cn/3.pdf'},
             {'name': 'two', 'url': 'http://b.gov.cn/2.pdf'}, {'name': 'none', 'url': ''},
             {'name': 'cut', 'url': 'http://c.gov.cn/4.pdf'}]
    reg = BlockReg({'c': {'active': True, 'reason': 'x'}})
    c, s, _ = list_crawler(tmp_path, routes, items, registry=reg)
    st = c.run()
    assert st['status'] == 'done'
    assert 'http://www.a.gov.cn/3.pdf' not in s.calls and 'http://c.gov.cn/4.pdf' not in s.calls
    assert reg.puts == ['a'] and st['blocked_sites'][0]['host'] == 'www.a.gov.cn'
    skipped = {x['name'] for x in st['skipped']}
    assert skipped == {'one', 'one-b', 'none', 'cut'}
    c.register(reg)
    rec = reg.recs['sd-l']
    assert rec['status'] == 'partial' and '清单 1/5 份' in rec['scope']['note'] and '#4 none' in rec['scope']['note']


def test_mediawiki_names_by_site_and_pageid_and_tolerates_bad_responses(tmp_path):
    def lp(ids, cur=None):
        body = {'query': {'allpages': [{'pageid': i, 'title': f't{i}'} for i in ids]}}
        if cur:
            body['continue'] = {'apcontinue': cur}
        return Resp(body=json.dumps(body).encode())

    def moved():
        r = html('<html>首页</html>')
        r.history = [Resp(status=301)]
        return r
    w = 'http://w/{s}/api.php?list=allpages'
    routes = {w.format(s='a'): lp([1, 2, 3], 'k'), w.format(s='a') + '&c=k': Resp(body=b'<html>not json</html>'),
              w.format(s='b'): Resp(body=b''),
              'http://w/a/index.php?curid=1&action=render': html('<div class="c">一</div>'),
              'http://w/a/index.php?curid=2&action=render': moved,
              'http://w/a/index.php?curid=3&action=render': html(''),
              'http://w/c/index.php?curid=1&action=render': html('<div class="c">丙</div>'),
              'http://w/c/index.php?curid=2&action=render': html('', status=567),
              w.format(s='c'): lp([1, 2])}
    secs = [{'name': s, 'list_url_first': w.format(s=s), 'list_url': w.format(s=s) + '&c={cursor}',
             'article_url': f'http://w/{s}/index.php?curid={{pageid}}&action=render'} for s in 'abc']
    cfg_ = acfg(tmp_path, list_format='json', mediawiki=True, items_path='query.allpages',
                cursor_path='continue.apcontinue', article_url='x', sections=secs)
    s = Session(routes)
    st = ListingCrawler(cfg_, session=s, sleep=lambda x: None, rand=lambda a, b: a, log=lambda m: None).run()
    assert st['status'] == 'done' and st['sections'] == {'a': 1, 'b': 0, 'c': 1}
    assert (tmp_path / 'out/files/a/a-1.txt').is_file() and (tmp_path / 'out/files/c/c-1.html').is_file()
    reasons = {x['key'].replace('/index.php?curid=', '#').split('&')[0]: x['reason'] for x in st['skipped']}
    assert reasons['http://w/a#2'] == 'HTTP 301 重定向' and reasons['http://w/a#3'] == '空响应'
    assert '非 JSON' in reasons['a list page 2'] and '空响应' in reasons['b list page 1']
    assert s.calls.count(w.format(s='b')) == 3 and 'site_block' not in st


def test_register_truncates_long_note_and_counts_skipped(tmp_path):
    routes = {'http://l/?p=1': page([{'id': 1, 'u': 'a.pdf'}], 5), 'http://s/a.pdf': Resp(body=b'%PDF-a')}
    c = ListingCrawler(cfg(tmp_path, max_items=1), session=Session(routes), sleep=lambda x: None, log=lambda m: None)
    c.run()
    c.state['skipped'] = [{'url': f'http://s/{"x" * 80}{i}.pdf', 'status_code': 503, 'at': 't'} for i in range(60)]
    assert len(c.record()['scope']['note']) > 2000
    reg = ScopeDroppingReg()
    res = c.register(reg)
    note = reg.recs['sd-x']['scope']['note']
    assert res['registered'] and len(note) <= 2000
    assert note.startswith('最新 1 份/共 5 份') and '60 份' in note and 'http://s/' not in note
    assert len(c.state['skipped']) == 60
