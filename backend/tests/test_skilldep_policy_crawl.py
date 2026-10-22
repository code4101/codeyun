from pathlib import Path
from urllib.parse import quote

import pytest
from filelock import FileLock

from backend.core.skilldep_materials import policy_crawl as pc

T0 = 1_800_000_000.0
PDF = b'%PDF-1.6\n' + b'x' * 2000
DOCX = b'PK\x03\x04' + b'y' * 1500
FREE = lambda root: 50 * 1024 ** 3  # noqa: E731


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
    def __init__(self, status=200, body=b'', headers=None):
        self.status_code, self._body = status, body
        self.headers = {'Content-Type': 'text/html', 'Content-Length': str(len(body)), **(headers or {})}

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        pass


class Site:
    """Fake server: ``pages`` maps URL -> Resp or list of Resp (served in order, last repeats)."""

    def __init__(self, clock, pages):
        self.clock, self.pages, self.calls = clock, dict(pages), []

    def get(self, url, **kw):
        assert kw.get('allow_redirects') is False
        self.calls.append((url, self.clock()))
        r = self.pages.get(url, Resp(404, b'<html>404 Not Found</html>'))
        if isinstance(r, list):
            return r.pop(0) if len(r) > 1 else r[0]
        return r


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


# Markup below copies the real list/article structure (see policy-crawl-contracts.json).
PBC_BASE = 'https://www.pbc.gov.cn/tiaofasi/144941/144957/'


def pbc_list(page, pages, total, ids):
    rows = ''.join(
        f'<tr><td><font class="newslist_style"><a href="/tiaofasi/144941/144957/{i}/index.html" onclick="void(0)" '
        f'target="_blank" title="中国人民银行令第{i}号" istitle="true">中国人民银行令第{i}号</a></font>'
        f'<span class="hui12">2024-11-0{len(ids)}</span></td></tr>' for i in ids)
    return (f'<html><head><meta charset="utf-8"><meta name="ColumnName" content="部门规章"><title>部门规章</title>'
            f'</head><body><table>{rows}</table><div><a tagname="[HOMEPAGE]">首页</a>'
            f'<a tagname="/tiaofasi/144941/144957/21892-{pages}.html" class="pagingNormal">尾页</a>'
            f'<td class="Normal"> 总记录数:{total},每页显示20条记录,当前页: <font color="red">{page}</font> /{pages} </td>'
            f'<input type="hidden" name="article_paging_list_hidden" moduleid="21892" totalpage="{pages}"></div>'
            '</body></html>').encode('utf-8')


def pbc_article(i, links=''):
    return (f'<html><head><meta charset="utf-8"><meta name="ArticleTitle" content="中国人民银行令第{i}号">'
            '<meta name="PubDate" content="1993-01-14"><meta name="ColumnName" content="部门规章">'
            f'<meta name="ContentSource" content=""><title>令</title></head><body>'
            f'<div id="zoom"><p>第一条 为了规范人民币管理。</p>{links}</div></body></html>').encode('utf-8')


CSRC_LIST = 'https://www.csrc.gov.cn/csrc/c106256/fg.shtml'


def csrc_list(page, pages, total, ids):
    rows = ''.join(f'<li><td class="info zc_info"><a class="list clear" href="/csrc/c106256/c{i}/content.shtml" '
                   f'target="_blank"><span class="til">规定{i}</span></a></td></li>' for i in ids)
    return (f'<html><head><meta charset="UTF-8"><title>规章</title></head><body><ul>{rows}</ul>'
            f"<div id='page_div'></div><script>createPageHTML('page_div',{pages}, {page},'fg','shtml',{total});"
            '</script></body></html>').encode('utf-8')


def csrc_article(i, names=('《规定》.pdf', '《规定》.docx')):
    head = ''.join(f'<a href="{i}/files/{n}" alt="规定" download>下载</a>' for n in names)
    return (f'<html><head><meta charset="UTF-8"><meta name="ArticleTitle" content="规定{i}">'
            '<meta name="PubDate" content="2026-08-01 21:41:52"><meta name="ColumnName" content="规章">'
            '<meta name="ContentSource" content="中国证监会"><title>规定</title></head><body>'
            f'<div class="content-head">{head}<div class="clear"></div></div>'
            f'<div class="content-body"><h3>规定{i}</h3><p>第一条 摘要。</p></div></body></html>').encode('utf-8')


def csrc_file(i, name):
    return f'https://www.csrc.gov.cn/csrc/c106256/c{i}/{i}/files/{quote(name)}'


def step(source, root, site, clock, **kw):
    kw.setdefault('registry_factory', Registry)
    return pc.crawl_step(source, root, session=site, clock=clock, sleep=clock.sleep, disk_free=FREE, **kw)


def pbc_site(clock):
    """Two list pages; item 2 shifts onto page 2 too (duplicate across pages); 3 distinct articles."""
    return Site(clock, {
        PBC_BASE + 'index.html': Resp(body=pbc_list(1, 2, 3, ['1', '2'])),
        PBC_BASE + '21892-2.html': Resp(body=pbc_list(2, 2, 3, ['2', 'b71de796'])),
        PBC_BASE + '1/index.html': Resp(body=pbc_article(1, '<a href="/tiaofasi/144941/144957/1/2018.pdf">附件</a>')),
        PBC_BASE + '2/index.html': Resp(body=pbc_article(2)),
        PBC_BASE + 'b71de796/index.html': Resp(body=pbc_article(3)),
        PBC_BASE + '1/2018.pdf': Resp(body=PDF, headers={'Content-Type': 'application/pdf'}),
    })


def test_real_list_markup_is_parsed_with_declared_pagination():
    pbc = pc.parse_pbc_list(pbc_list(1, 6, 113, ['5916164', 'b71de79692a649a6863b7ba9c9f681b4']), 1)
    assert (pbc['declared_pages'], pbc['declared_items'], len(pbc['items'])) == (6, 113, 2)
    assert pbc['items'][0]['date'] == '2024-11-02'
    csrc = pc.parse_csrc_list(csrc_list(1, 5, 89, ['3217074', '1942800', '3217074']), 1)
    assert (csrc['declared_pages'], csrc['declared_items'], len(csrc['items'])) == (5, 89, 2)
    # real page 3: one rule is listed at another PBC column with an absolute URL; it counts in the total
    other = 'https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/2025092212545029671/index.html'
    body = pbc_list(3, 6, 113, ['5741168']).replace(b'/tiaofasi/144941/144957/5741168/index.html', other.encode())
    assert [i['url'] for i in pc.parse_pbc_list(body, 3)['items']] == [other]
    assert pc.PBC.article_id(other) == '2025092212545029671'
    with pytest.raises(pc.PageInvalid):  # an out-of-range page redirected to the home page
        pc.parse_csrc_list(csrc_list(1, 5, 89, ['1']), 6)


def test_attachment_urls_official_only_and_no_traversal():
    page = 'https://www.csrc.gov.cn/csrc/c106256/c3217074/content.shtml'
    url, err = pc.attachment_url(page, '3217074/files/《规定》.pdf', pc.CSRC.hosts, '/csrc/')
    assert err is None and url == csrc_file('3217074', '《规定》.pdf')
    assert pc.attachment_url(page, 'https://evil.example.com/a.pdf', pc.CSRC.hosts, '/csrc/')[1] == 'off_site'
    assert pc.attachment_url(page, '../../../etc/a.pdf', pc.CSRC.hosts, '/csrc/')[1] == 'path_traversal'
    assert pc.attachment_url(page, '%2e%2e/x.pdf', pc.CSRC.hosts, '/csrc/')[1] == 'path_traversal'
    assert pc.safe_filename('https://x/a/..%5C..%5Cboot.ini', set()) == '_.._boot.ini'
    assert pc.magic_problem(b'<!DOCTYPE html><html>', '.pdf') == 'html_instead_of_file'
    assert pc.magic_problem(PDF, '.pdf') is None and pc.magic_problem(DOCX, '.pdf').startswith('bad_magic')


def test_pbc_full_crawl_dedupes_across_pages_throttles_and_is_downloaded_when_whole(tmp_path):
    clock = Clock()
    site = pbc_site(clock)
    reg = Registry()
    res = step('pbc', tmp_path, site, clock, registry_factory=lambda: reg)
    s = res['summary']
    assert res['stop'] == 'done' and s['complete'] and s['articles_listed'] == 3 == s['declared_items']
    assert s['attachments_ok'] == 1 and not s['gaps']
    gaps = [b - a for (_, a), (_, b) in zip(site.calls, site.calls[1:])]
    assert min(gaps) >= pc.GAP_MIN and len(site.calls) == len({u for u, _ in site.calls}) == 6
    rec = reg.records[pc.PBC.mid]
    # the plan item is the column's rules; its statistics are the separate materials, not a gap here
    assert rec['status'] == 'downloaded' and 'scope' not in rec
    assert pc.PBC.completes_material and 'sd-2e5af11ea217' in pc.PBC.scope_note and 'sd-e52fcd896b79' in pc.PBC.scope_note
    snap = Path(rec['manifest_path'])
    assert snap.is_file() and pc.sha256_file(snap) == rec['manifest_sha256']
    art = tmp_path / pc.PBC.mid / 'articles' / '1'
    assert (art / 'files' / '2018.pdf').read_bytes() == PDF
    assert '标题：中国人民银行令第1号' in (art / 'text.txt').read_text(encoding='utf-8')
    # restart: everything verified, no request at all
    site.calls.clear()
    again = step('pbc', tmp_path, site, clock, registry_factory=lambda: reg)
    assert again['stop'] == 'done' and site.calls == [] and again['new_files'] == []


def test_csrc_head_attachments_chinese_urls_and_downloaded_when_whole(tmp_path):
    clock = Clock()
    names = ('《中国证监会派出机构监管职责规定》.pdf', '《中国证监会派出机构监管职责规定》.docx')
    site = Site(clock, {CSRC_LIST: Resp(body=csrc_list(1, 1, 1, ['3217074'])),
                        'https://www.csrc.gov.cn/csrc/c106256/c3217074/content.shtml': Resp(body=csrc_article('3217074', names)),
                        csrc_file('3217074', names[0]): Resp(body=PDF), csrc_file('3217074', names[1]): Resp(body=DOCX)})
    reg = Registry()
    res = step('csrc', tmp_path, site, clock, registry_factory=lambda: reg)
    assert res['summary']['complete'] and res['summary']['attachments_ok'] == 2
    assert csrc_file('3217074', names[0]) in [u for u, _ in site.calls]
    assert reg.records[pc.CSRC.mid]['status'] == 'downloaded'
    assert (tmp_path / pc.CSRC.mid / 'articles' / 'c3217074' / 'files' / names[1]).read_bytes() == DOCX


def test_fake_pdf_rejected_and_missing_attachments_block_completion(tmp_path):
    clock = Clock()
    a = 'https://www.csrc.gov.cn/csrc/c106256/c{}/content.shtml'
    html_pdf = Resp(body=b'<!DOCTYPE html><html><title>302 Found</title></html>')
    site = Site(clock, {CSRC_LIST: Resp(body=csrc_list(1, 1, 3, ['1', '2', '3'])),
                        a.format(1): Resp(body=csrc_article('1', ('a.pdf',))), csrc_file('1', 'a.pdf'): html_pdf,
                        a.format(2): Resp(body=csrc_article('2', ())),
                        a.format(3): Resp(body=csrc_article('3', ('gone.pdf',)))})
    reg = Registry()
    for _ in range(pc.ATTACH_ATTEMPTS):
        res = step('csrc', tmp_path, site, clock, registry_factory=lambda: reg)
    kinds = sorted(g['kind'] for g in res['summary']['gaps'])
    assert kinds == ['attachment_gone', 'attachment_invalid', 'no_attachment_links']
    assert not res['summary']['complete'] and reg.records[pc.CSRC.mid]['status'] == 'partial'
    assert not (tmp_path / pc.CSRC.mid / 'articles' / 'c1' / 'files' / 'a.pdf').exists()
    assert list((tmp_path / pc.CSRC.mid / 'blocked').iterdir())  # evidence kept


def test_429_pauses_with_persisted_retry_after_then_resumes(tmp_path):
    clock = Clock()
    site = pbc_site(clock)
    site.pages[PBC_BASE + '2/index.html'] = [Resp(429, b'busy', {'Retry-After': '120'}), Resp(body=pbc_article(2))]
    first = step('pbc', tmp_path, site, clock)
    assert 'HTTP 429' in first['stop'] and first['cursor']['retry_at'] == pc._iso(clock() + 120)
    n = len(site.calls)
    paused = step('pbc', tmp_path, site, clock)
    assert paused['stop'].startswith('paused until') and len(site.calls) == n
    clock.t += 121
    done = step('pbc', tmp_path, site, clock)
    assert done['stop'] == 'done' and done['summary']['complete']
    assert [u for u, _ in site.calls[n:]] == [PBC_BASE + '2/index.html', PBC_BASE + 'b71de796/index.html']


def test_waf_page_pauses_and_keeps_evidence(tmp_path):
    clock = Clock()
    site = pbc_site(clock)
    site.pages[PBC_BASE + '1/index.html'] = Resp(body=b'<html><title>x</title><script>var acw_sc__v2=1</script>'
                                                      + b' ' * 600 + b'</html>')
    res = step('pbc', tmp_path, site, clock)
    assert 'WAF' in res['stop'] and res['cursor']['retry_at']
    status = pc.source_status('pbc', tmp_path)
    assert (tmp_path / status['cursor']['last_block']['evidence']).is_file()


def test_corruption_repairs_only_the_broken_file(tmp_path):
    clock = Clock()
    site = pbc_site(clock)
    step('pbc', tmp_path, site, clock)
    art = tmp_path / pc.PBC.mid / 'articles' / '1'
    (art / 'files' / '2018.pdf').write_bytes(b'%PDF-broken')
    (art / 'text.txt').write_text('damaged', encoding='utf-8')
    site.calls.clear()
    res = step('pbc', tmp_path, site, clock)
    assert [u for u, _ in site.calls] == [PBC_BASE + '1/2018.pdf']
    assert len(res['repaired']) == 2 and res['summary']['complete']
    assert (art / 'files' / '2018.pdf').read_bytes() == PDF
    assert '标题：' in (art / 'text.txt').read_text(encoding='utf-8')


def test_outbox_kept_on_sync_failure_and_flushed_later(tmp_path):
    clock = Clock()
    site = pbc_site(clock)
    res = step('pbc', tmp_path, site, clock, registry_factory=lambda: Registry(fail=True))
    assert res['sync']['registered'] is False
    outbox = tmp_path / pc.PBC.mid / 'pending-sync.json'
    assert outbox.is_file()
    reg = Registry()
    again = step('pbc', tmp_path, site, clock, registry_factory=lambda: reg)
    assert again['sync']['registered'] and not outbox.exists() and pc.PBC.mid in reg.records


def test_lock_is_exclusive_and_stop_ends_worker(tmp_path):
    clock = Clock()
    site = pbc_site(clock)
    lock = FileLock(str(pc._paths(tmp_path, pc.PBC)['lock']))
    lock.acquire()
    try:
        with pytest.raises(pc.SourceBusy):
            step('pbc', tmp_path, site, clock)
        with pytest.raises(pc.SourceBusy):
            pc.run('pbc', tmp_path, session=site, clock=clock, sleep=clock.sleep, disk_free=FREE)
    finally:
        lock.release()
    pc.stop('pbc', tmp_path)  # a stale STOP does not block a new start
    site.pages[PBC_BASE + '2/index.html'] = Resp(429, b'busy', {'Retry-After': '600'})

    def sleep(s):
        clock.sleep(s)
        if clock() - T0 > 100:
            pc.stop('pbc', tmp_path)

    info = pc.run('pbc', tmp_path, session=site, clock=clock, sleep=sleep, disk_free=FREE,
                  registry_factory=Registry)
    assert info['state'] == 'stopped' and info['pid'] and info['create_time']
    assert info['steps'] == 1 and 'HTTP 429' in info['last_result']['stop']
    st = pc.source_status('pbc', tmp_path)
    assert st['worker']['state'] == 'stopped' and not st['worker_alive'] and not st['stop_requested']



def cn(n):
    d = '零一二三四五六七八九'
    h, t, u = n // 100, n // 10 % 10, n % 10
    s = (d[h] + '百' if h else '') + ((d[t] if t > 1 or h else '') + '十' if t else ('零' if h and u else ''))
    return s + (d[u] if u else '')


def csrc_inline(i, n=43, skip=None, tail='本办法自<span>2017</span>年<span>7</span>月<span>1</span>日起施行。'):
    """Real c1653849 shape: empty ``download`` placeholders, headings in their own span, baike links."""
    arts = []
    for k in range(1, n + 1):
        if k == skip:
            continue
        body = tail if k == n else (f'为了规范管理，根据《</span><a href="https://baike.baidu.com/item/x"><span>证券法'
                                    f'</span></a><span>》，违反本办法</span><a><span>第{cn(n)}条</span></a><span>的，处理。')
        arts.append(f'<p><span>第{cn(k)}条&ensp;&ensp;</span><span>{body}</span></p>')
    return (f'<html><head><meta charset="UTF-8"><meta name="ArticleTitle" content="办法{i}">'
            '<meta name="PubDate" content="2026-08-01 21:42:00"><meta name="ColumnName" content="规章">'
            '<meta name="ContentSource" content="中国证监会"><title>办法</title></head><body>'
            '<div class="content-head"><a href="" alt="办法" download>下载图片版</a>'
            '<a href="" alt="办法" download>下载文字版</a><div class="clear"></div></div>'
            f'<div class="content-body"><h3>办法{i}</h3><p class="sub-title">（证监会令第130号公布）</p>'
            f'{"".join(arts)}</div></body></html>').encode('utf-8')


def inline(body):
    return pc.parse_article(pc.CSRC, body, 'https://www.csrc.gov.cn/csrc/c106256/c9/content.shtml')['inline_fulltext']


def test_inline_fulltext_needs_consecutive_articles_and_effective_clause():
    assert [cn(n) for n in (1, 10, 12, 43, 63, 105)] == ['一', '十', '十二', '四十三', '六十三', '一百零五']
    whole = inline(csrc_inline('9'))
    assert whole['complete'] and (whole['first_article'], whole['last_article']) == (1, 43)
    assert whole['effective_clause'] == '自2017年7月1日起施行'
    assert inline(csrc_inline('9', 63, tail='本办法自2020年10月1日起施行。《旧办法》同时废止。'))['complete']
    assert not inline(csrc_inline('9', skip=20))['complete']  # a missing article
    assert not inline(csrc_inline('9', tail='（未完）'))['complete']  # truncated: no effective clause
    assert not inline(csrc_inline('9', 2, tail='本办法自公布之日起施行。'))['complete']  # too short to prove
    notice = csrc_inline('9', 0).replace(b'</div></body>', '<p>《办法》已经审议通过，现予公布，自2020年1月1日起施行。</p>'
                                         '</div></body>'.encode())
    assert not inline(notice)['complete']  # 公布通知/摘要 without the articles
    assert not inline(csrc_article('9', ()))['complete']


def test_csrc_inline_fulltext_counts_without_links_and_cached_html_is_reclassified_offline(tmp_path):
    clock = Clock()
    a = 'https://www.csrc.gov.cn/csrc/c106256/c{}/content.shtml'
    site = Site(clock, {CSRC_LIST: Resp(body=csrc_list(1, 1, 3, ['1', '2', '3'])),
                        a.format(1): Resp(body=csrc_article('1', ('a.pdf',))), csrc_file('1', 'a.pdf'): Resp(body=PDF),
                        a.format(2): Resp(body=csrc_inline('2')), a.format(3): Resp(body=csrc_inline('3', skip=7))})
    reg = Registry()
    res = step('csrc', tmp_path, site, clock, registry_factory=lambda: reg)
    assert [(g['article'], g['kind']) for g in res['summary']['gaps']] == [('c3', 'no_attachment_links')]
    # state + manifest as published before the classifier existed: c2 was a no_attachment_links gap
    state_path = tmp_path / pc.CSRC.mid / 'state.json'
    state = pc._rj(state_path)
    for art in state['articles'].values():
        art.pop('inline_fulltext')
    pc._wj(state_path, state)
    old_manifest, _ = pc.snapshot(tmp_path, pc.CSRC, state, pc.summarize(pc.CSRC, state))
    assert pc.summarize(pc.CSRC, state)['articles_complete'] == 1
    old_sha = pc.sha256_file(old_manifest)
    files = {p: pc.sha256_file(p) for p in (tmp_path / pc.CSRC.mid / 'articles').rglob('*') if p.is_file()}
    site.calls.clear()
    again = step('csrc', tmp_path, site, clock, registry_factory=lambda: reg)
    assert site.calls == [] and again['new_files'] == [] and again['reclassified'] == 3
    assert [(g['article'], g['kind']) for g in again['summary']['gaps']] == [('c3', 'no_attachment_links')]
    after = pc._rj(state_path)
    assert after['articles']['c2']['inline_fulltext']['last_article'] == 43
    assert after['articles']['c1']['attachments'][0]['status'] == 'ok'
    assert {p: pc.sha256_file(p) for p in files} == files  # html/text/meta/attachments untouched
    assert pc.sha256_file(old_manifest) == old_sha
    assert again['sync']['registered'] and reg.records[pc.CSRC.mid]['manifest_path'] != old_manifest.as_posix()
    assert step('csrc', tmp_path, site, clock, registry_factory=lambda: reg)['reclassified'] == 0


def word97(paras):
    """Word 97 shape: OLE magic, UTF-16LE text with ``\r`` paragraph marks, binary tail."""
    return (b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 504 + '\r'.join(paras).encode('utf-16le')
            + b'\x00\x00\x01\x02' * 16 + 'DocumentSummaryInformation'.encode('utf-16le'))


RULE = '金融机构客户身份识别和客户身份资料及交易记录保存管理办法'
V2007 = [RULE, '中国人民银行令［2007］第2 号', '第一章 总则', '第一条 为了预防洗钱和恐怖融资活动。',
         '第二条 本办法适用于金融机构。', '第三条 本办法自2007 年8 月1 日起施行。']
V2022 = [RULE.replace('身份识别', '尽职调查'), '中国人民银行令〔2022〕第1号', '第一条 为了预防洗钱。',
         '第二条 适用。', '第三条 本办法自2022年3月1日起施行。']
REQUIRE = (RULE, '〔2007〕第2号', '自2007年8月1日起施行')
FORBID = ('尽职调查',)
GONE_PDF = PBC_BASE + '1/2018.pdf'
SUB_DOC = 'https://www.pbc.gov.cn/eportal/fileDir/image_public/UserFiles/upload/File/' + quote('5-办法.doc')
NEW_DOC = 'https://www.pbc.gov.cn/eportal/fileDir/image_public/UserFiles/upload/File/new.doc'


def test_version_proof_needs_whole_text_and_the_right_version():
    whole = pc.version_proof(pc.document_text(word97(V2007), '.doc'), REQUIRE, FORBID)
    assert whole['ok'] and whole['inline_fulltext']['last_article'] == 3 and not whole['missing']
    newer = pc.version_proof(pc.document_text(word97(V2022), '.doc'), REQUIRE, FORBID)
    assert not newer['ok'] and newer['forbidden_found'] == ['尽职调查'] and '〔2007〕第2号' in newer['missing']
    notice = pc.version_proof('中国人民银行今日发布《令〔2007〕第2号》（见附件）。', REQUIRE)
    assert not notice['ok'] and not notice['inline_fulltext']['complete']
    assert not pc.version_proof('\n'.join(V2007), ())['ok']  # no version marker given: nothing proven


def test_gone_attachment_supplemented_with_official_same_version_file(tmp_path):
    clock = Clock()
    site = pbc_site(clock)
    del site.pages[GONE_PDF]  # the original PDF now answers 404
    site.pages[SUB_DOC] = Resp(body=word97(V2007), headers={'Content-Type': 'application/msword'})
    site.pages[NEW_DOC] = Resp(body=word97(V2022), headers={'Content-Type': 'application/msword'})
    reg = Registry()
    res = step('pbc', tmp_path, site, clock, registry_factory=lambda: reg)
    assert [g['kind'] for g in res['summary']['gaps']] == ['attachment_gone']
    assert not res['summary']['complete'] and reg.records[pc.PBC.mid]['status'] == 'partial'
    mid = tmp_path / pc.PBC.mid
    published = {p: pc.sha256_file(p) for p in mid.rglob('*') if p.is_file() and p.suffix != '.lock'
                 and p.name not in ('state.json', 'crawl-log.jsonl')}
    old_manifest = Path(reg.records[pc.PBC.mid]['manifest_path'])

    def sup(url, **kw):
        kw.setdefault('require', REQUIRE)
        kw.setdefault('forbid', FORBID)
        kw.setdefault('note', '原 PDF 404；人民银行官网同版 Word 文本')
        return pc.supplement_attachment('pbc', '1', GONE_PDF, url, root=tmp_path, session=site, clock=clock,
                                        sleep=clock.sleep, disk_free=FREE, registry_factory=lambda: reg, **kw)

    n = len(site.calls)
    with pytest.raises(ValueError):
        sup(SUB_DOC, require=())
    with pytest.raises(ValueError):
        pc.supplement_attachment('pbc', '1', PBC_BASE + '1/other.pdf', SUB_DOC, require=REQUIRE, note='x',
                                 root=tmp_path, session=site)
    off = sup('https://evil.example.com/5.doc')
    assert not off['supplemented'] and 'off_site' in off['error'] and len(site.calls) == n
    wrong = sup(NEW_DOC)  # a later revision with the same subject is not the 2007 rule
    assert not wrong['supplemented'] and wrong['error'] == 'version proof failed'
    assert wrong['proof']['forbidden_found'] == ['尽职调查'] and not (mid / 'articles' / '1' / 'supplements').exists()
    assert reg.records[pc.PBC.mid]['status'] == 'partial'

    ok = sup(SUB_DOC)
    assert ok['supplemented'] and not ok['already'] and ok['substitute']['proof']['ok']
    assert site.calls[-1][1] - site.calls[-2][1] >= pc.GAP_MIN
    s = ok['summary']
    assert s['complete'] and not s['gaps'] and (s['attachments_ok'], s['attachments_substituted']) == (0, 1)
    assert s['substitutes'][0]['original'] == GONE_PDF and s['substitutes'][0]['original_error'] == 'HTTP 404'
    rec = reg.records[pc.PBC.mid]
    assert rec['status'] == 'downloaded'
    state = pc._rj(mid / 'state.json')
    att = state['articles']['1']['attachments'][0]
    assert (att['url'], att['status'], att['error']) == (GONE_PDF, 'gone', 'HTTP 404')  # 404 audit kept
    assert [a['error'] for a in att['substitute_attempts']][1:] == ['version proof failed']
    assert (tmp_path / att['substitute']['path']).read_bytes() == word97(V2007)
    assert RULE in (tmp_path / att['substitute']['text']['path']).read_text(encoding='utf-8')
    assert {p: pc.sha256_file(p) for p in published} == published  # nothing published was rewritten
    manifest = pc._rj(Path(rec['manifest_path']))
    assert Path(rec['manifest_path']) != old_manifest and old_manifest.is_file()
    subs = [f for f in manifest['files'] if f['kind'] == 'attachment_substitute']
    assert len(subs) == 1 and subs[0]['replaces'] == GONE_PDF and subs[0]['carrier'] == 'doc'
    assert subs[0]['url'] == SUB_DOC and subs[0]['note'].startswith('原 PDF 404')

    n = len(site.calls)
    assert sup(SUB_DOC)['already'] and len(site.calls) == n  # idempotent, no request
    with pytest.raises(ValueError):
        sup(NEW_DOC)  # one substitute per gap
    again = step('pbc', tmp_path, site, clock, registry_factory=lambda: reg)
    assert again['stop'] == 'done' and again['summary']['complete'] and len(site.calls) == n

    (tmp_path / att['substitute']['path']).write_bytes(b'\xd0\xcf\x11\xe0broken')
    broken = step('pbc', tmp_path, site, clock, registry_factory=lambda: reg)
    assert broken['repaired'] == [att['substitute']['path']] and len(site.calls) == n
    assert [g['kind'] for g in broken['summary']['gaps']] == ['attachment_gone']
    assert reg.records[pc.PBC.mid]['status'] == 'partial'
    assert pc._rj(mid / 'state.json')['articles']['1']['attachments'][0]['substitute_dropped']


def test_supplement_respects_the_source_lock(tmp_path):
    lock = FileLock(str(pc._paths(tmp_path, pc.PBC)['lock']))
    lock.acquire()
    try:
        with pytest.raises(pc.SourceBusy):
            pc.supplement_attachment('pbc', '1', GONE_PDF, SUB_DOC, require=REQUIRE, note='x', root=tmp_path)
    finally:
        lock.release()
