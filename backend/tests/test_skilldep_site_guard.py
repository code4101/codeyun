import json

import pytest

from backend.core.skilldep_materials.batch import first_batch_limit_bytes, next_batch
from backend.core.skilldep_materials.listing_crawl import ListingCrawler
from backend.core.skilldep_materials.site_guard import (SiteBlocked, assert_open, site_name, throttle_reason,
                                                        trip)


class Hub:
    device_id, device_name = 'dev', 'Dev'

    def __init__(self, blocks=None, materials=()):
        self.blocks, self._materials, self.recs, self.puts = dict(blocks or {}), list(materials), {}, []

    def site_blocks(self):
        return self.blocks

    def put_site_block(self, site, reason):
        self.puts.append((site, reason))
        self.blocks[site] = {'active': True, 'reason': reason}
        return self.blocks[site]

    def materials(self):
        return self._materials

    def register(self, mid, rec):
        self.recs[mid] = rec

    def acquisitions(self, mid):
        return [self.recs[mid]] if mid in self.recs else []


def test_site_name_and_throttle():
    assert site_name('http://www.cninfo.com.cn/new/x') == 'cninfo'
    assert site_name('https://search.ccgp.gov.cn/a') == 'ccgp'
    assert site_name('https://item.taobao.com/item.htm') == 'taobao'
    assert site_name('https://huggingface.co/datasets/x') == 'huggingface'
    assert throttle_reason(429, b'') == 'HTTP 429'
    assert throttle_reason(200, '访问过于频繁'.encode())
    assert throttle_reason(200, b' ') == '无提示空白页' and throttle_reason(200, b'', expect_body=False) is None
    assert throttle_reason(200, b'ok') is None


def test_assert_open_matches_short_name_and_legacy_host():
    hub = Hub({'cninfo': {'active': True, 'reason': 'r'}, 'travel.state.gov': {'active': True, 'reason': 'cf'},
               'taobao': {'active': False}})
    with pytest.raises(SiteBlocked):
        assert_open(hub, 'http://static.cninfo.com.cn/a.pdf')
    with pytest.raises(SiteBlocked):
        assert_open(hub, 'https://travel.state.gov/content')
    assert_open(hub, 'https://item.taobao.com/x')
    assert trip(hub, 'https://www.court.gov.cn/x', 'HTTP 403')['site'] == 'court' and hub.puts[0][0] == 'court'


def test_next_batch_priority_skips_present_buy_and_blocked():
    ms = [{'id': 'a', 'priority': 1, 'tier': 1, 'via': 'crawl', 'url': 'https://a.example.com'},
          {'id': 'b', 'priority': 9, 'tier': 1, 'via': 'crawl', 'url': 'https://b.example.com',
           'acquisition': {'windows_local_partial': True}},
          {'id': 'c', 'priority': 8, 'tier': 1, 'via': 'buy', 'url': 'https://item.taobao.com/x'},
          {'id': 'd', 'priority': 7, 'tier': 2, 'via': 'crawl', 'url': 'https://d.example.com'},
          {'id': 'e', 'priority': 6, 'tier': 1, 'via': 'crawl', 'url': 'https://www.cninfo.com.cn/x',
           'first_batch_limit_mb': 2},
          {'id': 'f', 'priority': 5, 'tier': 1, 'via': 'crawl', 'url': 'https://f.example.org'}]
    hub = Hub({'cninfo': {'active': True, 'reason': '403', 'until': 'T'}}, ms)
    out = next_batch(hub, limit=2)
    assert [m['id'] for m in out['selected']] == ['f', 'a']
    assert [(m['id'], m['reason'][:4]) for m in out['skipped']] == [('c', '需要登录'), ('e', '站点 c')]
    assert first_batch_limit_bytes(ms[4]) == 2 * 1024 * 1024 and first_batch_limit_bytes(ms[0]) is None


class Resp:
    def __init__(self, status=200, body=b''):
        self.status_code, self.content, self.encoding, self.headers = status, body, 'utf-8', {}

    def iter_content(self, n):
        yield self.content

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
        return self.routes[url]


def _cfg(tmp_path):
    return {'material_id': 'sd-x', 'list_url': 'http://www.site.com.cn/l?p={page}', 'items_path': 'items',
            'attachment_url': 'http://www.site.com.cn/{u}', 'key_field': 'id', 'out_dir': str(tmp_path / 'o')}


def test_listing_throttle_trips_breaker_and_registers_failed(tmp_path):
    hub = Hub()
    s = Session({'http://www.site.com.cn/l?p=1': Resp(429)})
    c = ListingCrawler(_cfg(tmp_path), session=s, registry=hub, sleep=lambda x: None, log=lambda m: None)
    st = c.run()
    assert st['status'] == 'blocked' and st['throttle'] and hub.puts[0][0] == 'site'
    assert c.register()['registered'] and hub.recs['sd-x']['status'] == 'failed'
    # next run is refused before any request
    s2 = Session({})
    st = ListingCrawler(_cfg(tmp_path), session=s2, registry=hub, sleep=lambda x: None, log=lambda m: None).run()
    assert st['status'] == 'refused' and s2.calls == []


def test_listing_captcha_and_blank_first_page(tmp_path):
    hub = Hub()
    s = Session({'http://www.site.com.cn/l?p=1': Resp(200, b'')})
    st = ListingCrawler(_cfg(tmp_path), session=s, registry=hub, sleep=lambda x: None, log=lambda m: None).run()
    assert st['blocked_reason'] == '无提示空白页' and hub.puts
    body = json.dumps({'items': [{'id': 1, 'u': 'a.pdf'}]}).encode()
    hub2 = Hub()
    s = Session({'http://www.site.com.cn/l?p=1': Resp(200, body), 'http://www.site.com.cn/l?p=2': Resp(200, b'{}'),
                 'http://www.site.com.cn/a.pdf': Resp(200, b'%PDF-1')})
    st = ListingCrawler(_cfg(tmp_path / 'b'), session=s, registry=hub2, sleep=lambda x: None,
                        log=lambda m: None).run()
    assert st['status'] == 'done' and not hub2.puts
    assert hub2.recs['sd-x']['status'] == 'downloading' and hub2.recs['sd-x']['size_bytes'] == 6
