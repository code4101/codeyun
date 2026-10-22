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
