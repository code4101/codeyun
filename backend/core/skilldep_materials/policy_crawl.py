"""Resumable full crawler for the PBC 部门规章 and CSRC 规章库 SkillDep materials.

Sources (contracts: ``orchestration/policy-crawl-contracts.json``):

* ``sd-98bf2d2f5d43`` PBC: ``index.html`` + ``21892-{n}.html``, items ``a[istitle=true]``,
  body ``#zoom``, attachments are document links in the page under the article directory.
* ``sd-3b648dbbe220`` CSRC: ``fg.shtml`` + ``fg_{n}.shtml`` (``createPageHTML``), items
  ``/csrc/c106256/c{id}/content.shtml``, body ``div.content-body``, attachments are
  ``a[download]`` in ``div.content-head`` (Chinese names, percent-encoded on fetch).

Layout per source under ``<root>/<mid>/``: ``state.json`` (list pages, articles, attachment
queue, pause cursor), ``lists/page-NNN.html``, ``articles/<id>/{page.html,text.txt,meta.json}``,
``articles/<id>/files/<name>``, ``snapshots/manifest-<sha16>.json`` (immutable publish copy),
``pending-sync.json`` (outbox), ``worker.json``, ``STOP``, ``.crawl.lock``, ``crawl-log.jsonl``.

Every file is written to ``.part``, hashed and renamed; the state only records a file after
that. A restart verifies recorded files (size + SHA-256) and refetches only the broken one;
``text.txt``/``meta.json`` are rebuilt from a verified ``page.html`` without a request.
Requests are serial with a persisted gap of ``GAP`` seconds, never follow redirects,
and 403/429/503 or a WAF/challenge page persist ``retry_at`` (``Retry-After`` when given).
Attachments must be on the official host, have no ``..`` segment, match the extension's
magic bytes (HTML is always rejected) and the ``Content-Length``. Caps: ``FILE_CAP`` per
file, ``STEP_BYTES`` per step, ``MIN_FREE`` free disk.

Completion: all declared list pages, distinct articles == declared total, every article and
every attachment verified (a CSRC page without download links counts only when its body passes
``inline_fulltext``: consecutive 第一条..第N条 ending with the effective-date clause). CSRC is then registered ``downloaded``; PBC stays ``partial``
because the plan item also wants listed-bank annual reports/statistics, which are not here.

CLI: ``python -m backend.core.skilldep_materials.policy_crawl --source pbc|csrc --once|--run``,
``status``/``stop`` (``--source all`` allowed for those two).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from filelock import FileLock, Timeout

from .download import USER_AGENT, sha256_file
from .samples import DENIAL_MARKERS, decode_html

MIB = 1024 ** 2
DEFAULT_ROOT = Path('F:/CodeYun/materials/m2610skilldependency/staging/policy-20261020')
GAP = 3.2
FILE_CAP, STEP_BYTES, HTML_CAP, MIN_FREE = 64 * MIB, 300 * MIB, 4 * MIB, 5 * 1024 ** 3
ARTICLES_PER_STEP = 25
STEP_SECONDS = 15 * 60
BLOCK_STATUSES = (403, 429, 503)
BLOCK_PAUSE = 3600
ERROR_PAUSE = 15 * 60
ATTEMPTS = 2  # the contract: two unexpected answers in a row stop the source
ATTACH_ATTEMPTS = 3
IDLE_SLICE = 30
WAF_MARKERS = (*DENIAL_MARKERS, 'acw_sc__v2', '滑块', 'nc_1_wrapper', '_waf_', 'aliyun_waf')
ATTACH_EXT = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.wps', '.ofd', '.zip', '.rar', '.txt', '.et')
MAGIC = {'.pdf': (b'%PDF-',), '.docx': (b'PK\x03\x04',), '.xlsx': (b'PK\x03\x04',),
         '.zip': (b'PK\x03\x04',), '.ofd': (b'PK\x03\x04',), '.rar': (b'Rar!',),
         '.doc': (b'\xd0\xcf\x11\xe0', b'PK\x03\x04'), '.xls': (b'\xd0\xcf\x11\xe0', b'PK\x03\x04'),
         '.wps': (b'\xd0\xcf\x11\xe0', b'PK\x03\x04'), '.et': (b'\xd0\xcf\x11\xe0', b'PK\x03\x04')}


class SourceBusy(RuntimeError):
    pass


class PageInvalid(ValueError):
    pass


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='seconds')


def _ts(iso: str | None) -> float | None:
    return datetime.fromisoformat(iso).timestamp() if iso else None


def _wj(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def _rj(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _soup(body: bytes) -> BeautifulSoup:
    return BeautifulSoup(decode_html(body), 'html.parser')


def _meta(soup: BeautifulSoup, name: str) -> str | None:
    tag = soup.find('meta', attrs={'name': name})
    return tag.get('content', '').strip() if tag else None


def waf_marker(body: bytes) -> str | None:
    text = decode_html(body[:200000]).lower()
    return next((m for m in WAF_MARKERS if m.lower() in text), None)


# ---- list pages ---------------------------------------------------------------------

def parse_pbc_list(body: bytes, page: int) -> dict[str, Any]:
    """Items, declared page count and item total of a PBC 部门规章 list page."""
    soup = _soup(body)
    items, seen = [], set()
    for a in soup.select('a[istitle=true][href]'):
        # Most rules live under this column; a few are listed at another PBC column (absolute URL).
        parts = urlsplit(urljoin('https://www.pbc.gov.cn/tiaofasi/144941/144957/index.html', a['href'].strip()))
        if (parts.hostname or '').lower() not in ('www.pbc.gov.cn', 'pbc.gov.cn') or \
                not re.fullmatch(r'(/[0-9A-Za-z_-]+)+/index\.html', parts.path) or parts.path in seen:
            continue
        seen.add(parts.path)
        date = a.find_next('span', class_='hui12')
        items.append({'url': 'https://www.pbc.gov.cn' + parts.path, 'title': a.get('title') or a.get_text(strip=True),
                      'date': date.get_text(strip=True) if date else None})
    hidden = soup.find('input', attrs={'name': 'article_paging_list_hidden'})
    pages = int(hidden['totalpage']) if hidden and str(hidden.get('totalpage', '')).isdigit() else None
    if pages is None:
        nums = [int(m) for t in soup.find_all(attrs={'tagname': True})
                for m in re.findall(r'21892-(\d+)\.html', t['tagname'])]
        pages = max(nums) if nums else None
    text = soup.get_text(' ', strip=True)
    total = re.search(r'总记录数[:：]\s*(\d+)', text)
    current = re.search(r'当前页[:：]\s*(\d+)\s*/\s*(\d+)', text)
    if not items:
        raise PageInvalid('PBC 列表页没有 a[istitle=true] 条目')
    if pages is None or total is None:
        raise PageInvalid('PBC 列表页缺少分页（totalpage/总记录数）')
    if current and int(current.group(1)) != page:
        raise PageInvalid(f'PBC 列表页当前页 {current.group(1)} != 请求页 {page}')
    return {'items': items, 'declared_pages': pages, 'declared_items': int(total.group(1))}


CSRC_PAGER = re.compile(r"createPageHTML\(\s*'[^']*'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'[^']*'\s*,\s*'[^']*'\s*,\s*(\d+)\s*\)")


def parse_csrc_list(body: bytes, page: int) -> dict[str, Any]:
    """Items (deduplicated by href), declared page count and total of a CSRC 规章库 list page."""
    html = decode_html(body)
    soup = BeautifulSoup(html, 'html.parser')
    items, seen = [], set()
    for a in soup.find_all('a', href=True):
        path = urlsplit(urljoin('https://www.csrc.gov.cn/csrc/c106256/fg.shtml', a['href'].strip())).path
        if not re.fullmatch(r'/csrc/c106256/c[0-9]+/content\.shtml', path) or path in seen:
            continue
        seen.add(path)
        til = a.find('span', class_='til')
        items.append({'url': 'https://www.csrc.gov.cn' + path,
                      'title': (til or a).get_text(strip=True), 'date': None})
    pager = CSRC_PAGER.search(html)
    if not items:
        raise PageInvalid('CSRC 列表页没有 /csrc/c106256/c*/content.shtml 条目')
    if not pager:
        raise PageInvalid('CSRC 列表页缺少 createPageHTML 分页脚本')
    if int(pager.group(2)) != page:
        raise PageInvalid(f'CSRC 列表页当前页 {pager.group(2)} != 请求页 {page}')
    return {'items': items, 'declared_pages': int(pager.group(1)), 'declared_items': int(pager.group(3))}


# ---- articles and attachments -------------------------------------------------------

def attachment_url(page_url: str, href: str, hosts: tuple[str, ...], prefix: str) -> tuple[str | None, str | None]:
    """Resolved, percent-encoded official URL of an attachment link, or ``(None, reason)``."""
    raw = href.strip()
    decoded = unquote(raw)
    if '\\' in decoded or '..' in decoded.split('/'):
        return None, 'path_traversal'
    parts = urlsplit(urljoin(page_url, raw))
    if parts.scheme not in ('http', 'https') or (parts.hostname or '').lower() not in hosts:
        return None, 'off_site'
    path = posixpath.normpath(unquote(parts.path))
    if not path.startswith(prefix):
        return None, 'outside_site_prefix'
    return urlunsplit(('https', parts.netloc.lower(), quote(path, safe='/'), parts.query, '')), None


def safe_filename(url: str, taken: set[str]) -> str:
    name = unquote(posixpath.basename(urlsplit(url).path)) or 'file'
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', name).strip(' .') or 'file'
    stem, ext = os.path.splitext(name)
    stem, ext = stem[:120], ext[:10]
    out, n = stem + ext, 1
    while out.lower() in taken:
        n += 1
        out = f'{stem}-{n}{ext}'
    taken.add(out.lower())
    return out


def magic_problem(head: bytes, ext: str) -> str | None:
    """Why ``head`` is not a real ``ext`` file (HTML error pages are always rejected)."""
    lead = head.lstrip()[:64].lower()
    if lead.startswith(b'<') or b'<!doctype' in lead or b'<html' in lead:
        return 'html_instead_of_file'
    allowed = MAGIC.get(ext.lower())
    if allowed and not any(head.startswith(m) for m in allowed):
        return f'bad_magic:{head[:8].hex()}'
    return None


def parse_article(src: 'Source', body: bytes, url: str) -> dict[str, Any]:
    """Metadata, structured text and attachment links of one article page.

    ``src.attachment_scopes`` are ``(selector, article_dir_only)``: links found by a broad
    selector only count when they sit under the article's own directory.
    """
    soup = _soup(body)
    title = _meta(soup, 'ArticleTitle')
    container = soup.select_one(src.body_selector)
    if not title or container is None:
        raise PageInvalid(f'正文页缺少 ArticleTitle 或 {src.body_selector}')
    body_text = '\n'.join(ln.strip() for ln in container.get_text('\n').splitlines() if ln.strip())
    article_dir = posixpath.dirname(urlsplit(url).path) + '/'
    links, seen, taken = [], set(), set()
    for selector, dir_only in src.attachment_scopes:
        for a in soup.select(selector):
            href = a.get('href', '').strip()
            if not href or href.startswith(('javascript:', '#', 'mailto:')):
                continue
            is_doc = posixpath.splitext(unquote(urlsplit(href).path))[1].lower() in ATTACH_EXT
            if not (is_doc or a.has_attr('download')):
                continue
            resolved, reason = attachment_url(url, href, src.hosts, article_dir if dir_only else src.attach_prefix)
            if dir_only and not resolved:
                continue
            key = resolved or href
            if key in seen:
                continue
            seen.add(key)
            entry = {'href_raw': href, 'label': a.get_text(strip=True), 'url': resolved,
                     'status': 'pending' if resolved else 'rejected', 'error': reason, 'attempts': 0}
            if resolved:
                entry['filename'] = safe_filename(resolved, taken)
            links.append(entry)
    meta = {k: _meta(soup, n) for k, n in (('title', 'ArticleTitle'), ('pub_date', 'PubDate'),
                                           ('column', 'ColumnName'), ('content_source', 'ContentSource'),
                                           ('keywords', 'Keywords'))}
    head = [f'标题：{meta["title"]}', f'发布日期：{meta["pub_date"] or ""}', f'栏目：{meta["column"] or ""}',
            f'来源：{meta["content_source"] or ""}', f'网址：{url}']
    head += [f'附件：{x.get("filename") or x["href_raw"]}' for x in links]
    return {'meta': meta, 'text': '\n'.join(head) + '\n\n' + body_text + '\n',
            'body_text_sha256': _sha(body_text.encode('utf-8')), 'body_chars': len(body_text),
            'attachments': links, 'inline_fulltext': inline_fulltext(body_text)}


_CN_DIGITS = {c: i for i, c in enumerate('零一二三四五六七八九')} | {'〇': 0}
_HEADING = re.compile(r'^第([零〇一二三四五六七八九十百]+)条(?:[\s　]|$)')
_EFFECTIVE = re.compile(r'自.{0,60}?(?:起|之日)(?:施行|实施)')
INLINE_VERSION = 1
INLINE_MIN_ARTICLES = 3


def _cn_number(s: str) -> int | None:
    """``四十三`` -> 43, ``一百零五`` -> 105; ``None`` when not a plain numeral."""
    total, digit = 0, None
    for ch in s:
        if ch in _CN_DIGITS:
            digit = _CN_DIGITS[ch]
        elif ch in '十百':
            total += (1 if digit is None else digit) * (10 if ch == '十' else 100)
            digit = None
        else:
            return None
    return total + (digit or 0)


def inline_fulltext(body_text: str) -> dict[str, Any]:
    """Structural proof that the page body itself is the whole regulation text.

    Complete only when the line-leading ``第N条`` headings run 第一条..第N条 without a gap
    (N >= ``INLINE_MIN_ARTICLES``), no heading beyond N exists, and the last article holds
    the effective-date clause (``自……起施行``). Notices, abstracts and truncated bodies fail.
    """
    lines = body_text.splitlines()
    heads = [(i, _cn_number(m.group(1))) for i, ln in enumerate(lines) if (m := _HEADING.match(ln.strip()))]
    expect, last_line = 1, None
    for i, n in heads:  # in-text cross references split onto their own line are skipped here
        if n == expect:
            expect, last_line = expect + 1, i
    last = expect - 1
    beyond = sorted({n for _, n in heads if n and n > last})
    tail = ''.join(lines[last_line:]) if last_line is not None else ''
    clause = _EFFECTIVE.search(re.sub(r'\s+', '', tail))
    complete = last >= INLINE_MIN_ARTICLES and not beyond and bool(clause)
    return {'version': INLINE_VERSION, 'complete': complete, 'first_article': 1 if last else None,
            'last_article': last or None, 'beyond_headings': beyond[:10],
            'effective_clause': clause.group(0) if clause else None,
            'body_text_sha256': _sha(body_text.encode('utf-8'))}


@dataclass(frozen=True)
class Source:
    key: str
    mid: str
    name: str
    plan_url: str
    hosts: tuple[str, ...]
    attach_prefix: str
    body_selector: str
    attachment_scopes: tuple[tuple[str, bool], ...]
    parse_list: Callable[[bytes, int], dict[str, Any]]
    list_url: Callable[[int], str]
    completes_material: bool
    expects_attachments: bool
    scope_note: str

    def article_id(self, url: str) -> str:
        return urlsplit(url).path.strip('/').split('/')[-2]


PBC = Source(
    key='pbc', mid='sd-98bf2d2f5d43', name='中国人民银行部门规章',
    plan_url='http://www.pbc.gov.cn/tiaofasi/144941/144957/index.html',
    hosts=('www.pbc.gov.cn', 'pbc.gov.cn'), attach_prefix='/', body_selector='#zoom',
    attachment_scopes=(('#zoom a[href]', False), ('a[href]', True)), parse_list=parse_pbc_list,
    list_url=lambda n: ('https://www.pbc.gov.cn/tiaofasi/144941/144957/index.html' if n == 1
                        else f'https://www.pbc.gov.cn/tiaofasi/144941/144957/21892-{n}.html'),
    completes_material=False, expects_attachments=False,
    scope_note='仅人民银行条法司“部门规章”栏目全部列表、正文与正文附件；计划中“配上市银行年报与统计数据”'
               '不在此来源、未采集，素材保持 partial')
CSRC = Source(
    key='csrc', mid='sd-3b648dbbe220', name='证监会规章库',
    plan_url='https://www.csrc.gov.cn/csrc/c106256/fg.shtml',
    hosts=('www.csrc.gov.cn', 'csrc.gov.cn'), attach_prefix='/csrc/', body_selector='div.content-body',
    attachment_scopes=(('div.content-head a[href]', False), ('a[download][href]', False),
                       ('div.content-body a[href]', False)),
    parse_list=parse_csrc_list,
    list_url=lambda n: ('https://www.csrc.gov.cn/csrc/c106256/fg.shtml' if n == 1
                        else f'https://www.csrc.gov.cn/csrc/c106256/fg_{n}.shtml'),
    completes_material=True, expects_attachments=True,
    scope_note='证监会规章库全部列表页、每篇正文 HTML 与 content-head 下载图片版/文字版附件')
SOURCES = {s.key: s for s in (PBC, CSRC)} | {s.mid: s for s in (PBC, CSRC)}


def get_source(key: str) -> Source:
    try:
        return SOURCES[key]
    except KeyError:
        raise ValueError(f'未知来源 {key}；可选 pbc/csrc 或 {PBC.mid}/{CSRC.mid}') from None


# ---- files, HTTP, state -------------------------------------------------------------

def _paths(root: Path, src: Source) -> dict[str, Path]:
    d = Path(root) / src.mid
    return {'dir': d, 'state': d / 'state.json', 'outbox': d / 'pending-sync.json', 'worker': d / 'worker.json',
            'stop': d / 'STOP', 'log': d / 'crawl-log.jsonl', 'lock': d / '.crawl.lock', 'blocked': d / 'blocked'}


def source_lock(root: Path, src: Source) -> FileLock:
    path = _paths(root, src)['lock']
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path))


def _write_file(path: Path, data: bytes) -> dict[str, Any]:
    """Atomically write ``data`` and return its verified ``bytes``/``sha256``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + '.part')
    with part.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sha = _sha(data)
    if sha256_file(part) != sha:
        part.unlink(missing_ok=True)
        raise OSError(f'hash mismatch after writing {path.name}')
    os.replace(part, path)
    return {'bytes': len(data), 'sha256': sha}


def _file_ok(root: Path, rec: dict | None, full: bool) -> bool:
    if not rec or not rec.get('path'):
        return False
    f = Path(root) / rec['path']
    return f.is_file() and f.stat().st_size == rec['bytes'] and (not full or sha256_file(f) == rec['sha256'])


def retry_after_seconds(value: str | None, now: float) -> float | None:
    """``Retry-After`` as delta seconds or an HTTP date; ``None`` when absent/unparseable."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - now)
    except (TypeError, ValueError):
        return None


class Fetched:
    def __init__(self, status: int, headers: dict[str, str], body: bytes, too_large: bool = False):
        self.status, self.headers, self.body, self.too_large = status, headers, body, too_large


def _fetch(session, url: str, cap: int) -> Fetched:
    """GET without redirects; stops reading (``too_large``) once ``cap`` bytes are exceeded."""
    r = session.get(url, timeout=(20, 90), stream=True, allow_redirects=False,
                    headers={'User-Agent': USER_AGENT, 'Accept-Language': 'zh-CN,zh;q=0.9'})
    headers = {k: v for k, v in r.headers.items() if k.lower() != 'set-cookie'}
    length = headers.get('Content-Length') or headers.get('content-length')
    if length and length.isdigit() and int(length) > cap:
        r.close()
        return Fetched(r.status_code, headers, b'', True)
    chunks, size = [], 0
    for c in r.iter_content(256 * 1024):
        size += len(c)
        if size > cap:
            r.close()
            return Fetched(r.status_code, headers, b'', True)
        chunks.append(c)
    return Fetched(r.status_code, headers, b''.join(chunks))


def _new_state(src: Source) -> dict[str, Any]:
    return {'schema': 'codeyun.skilldep.policy-crawl/1', 'material_id': src.mid, 'name': src.name,
            'plan_url': src.plan_url, 'source_key': src.key, 'reused_from': None,
            'list': {'declared_pages': None, 'declared_items': None, 'pages': {}},
            'articles': {}, 'order': [],
            'cursor': {'retry_at': None, 'pause_reason': None, 'last_request_at': None}}


def _rebuild_order(state: dict) -> None:
    """Articles in list order (page, index), deduplicated across pages by article id."""
    order = []
    for n in sorted(state['list']['pages'], key=int):
        for aid in state['list']['pages'][n]['article_ids']:
            if aid not in order:
                order.append(aid)
    state['order'] = order


def verify_files(root: Path, src: Source, state: dict, *, full: bool = True) -> list[str]:
    """Drop or re-queue every recorded file that is missing or differs; return what was dropped.

    A bad list page is refetched; a bad ``page.html`` re-queues only that article's HTML;
    a bad ``text.txt``/``meta.json`` is rebuilt locally; a bad attachment re-queues only itself.
    """
    bad = []
    for n, page in list(state['list']['pages'].items()):
        if not _file_ok(root, page, full):
            state['list']['pages'].pop(n)
            bad.append(page['path'])
    for aid, art in state['articles'].items():
        if art.get('html') and not _file_ok(root, art['html'], full):
            bad.append(art['html']['path'])
            art['html'], art['status'] = None, 'pending'
        if art.get('html'):
            for kind in ('text', 'meta'):
                if not _file_ok(root, art.get(kind), full):
                    bad.append((art.get(kind) or {}).get('path') or f'{aid}/{kind}')
                    art[kind] = None
        for att in art.get('attachments', []):
            if att['status'] == 'ok' and not _file_ok(root, att, full):
                bad.append(att['path'])
                att.update(status='pending', error='verify_failed', attempts=0)
    return bad


class Paused(Exception):
    """The source must pause (blocked, WAF, disk, repeated errors); ``retry_at`` is persisted."""

    def __init__(self, reason: str, retry_at: float):
        super().__init__(reason)
        self.reason, self.retry_at = reason, retry_at


class _Ctx:
    def __init__(self, root, src, state, session, clock, sleep, disk_free):
        self.root, self.src, self.state, self.session = Path(root), src, state, session
        self.clock, self.sleep, self.disk_free = clock, sleep, disk_free
        self.p = _paths(root, src)
        self.bytes = 0

    @property
    def cur(self) -> dict:
        return self.state['cursor']

    def save(self) -> None:
        self.state['updated_at'] = _iso(self.clock())
        _wj(self.p['state'], self.state)

    def evidence(self, url: str, f: Fetched, why: str) -> str:
        """Keep the head of a blocked/challenge answer for audit (never retried around)."""
        rel = f'{self.src.mid}/blocked/{int(self.clock())}-{f.status}.html'
        _write_file(self.root / rel, f.body[:64 * 1024])
        self.cur['last_block'] = {'url': url, 'status': f.status, 'why': why, 'headers': f.headers,
                                  'at': _iso(self.clock()), 'evidence': rel}
        return rel

    def block(self, url: str, f: Fetched, why: str) -> Paused:
        h = {k.lower(): v for k, v in f.headers.items()}
        secs = retry_after_seconds(h.get('retry-after'), self.clock())
        self.evidence(url, f, why)
        how = f'Retry-After {int(secs)}s' if secs is not None else f'no Retry-After, default {BLOCK_PAUSE}s'
        return Paused(f'{why} ({how})', self.clock() + (BLOCK_PAUSE if secs is None else secs))

    def get(self, url: str, cap: int) -> Fetched:
        """One serial request after the persisted gap; network errors/5xx retried ``ATTEMPTS`` times.

        403/429/503 raise ``Paused``; any other status (200, 3xx, 404 ...) is returned to the caller.
        """
        if self.disk_free(self.root) < MIN_FREE:
            raise Paused('disk free < 5GiB', self.clock() + ERROR_PAUSE)
        err = None
        for attempt in range(ATTEMPTS):
            wait = GAP + 0.01 - (self.clock() - (_ts(self.cur.get('last_request_at')) or 0))
            if wait > 0:
                self.sleep(wait)
            self.cur['last_request_at'] = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
            self.save()
            try:
                f = _fetch(self.session, url, cap)
            except requests.RequestException as exc:
                err = f'{type(exc).__name__}: {exc}'[:300]
                continue
            if f.status in BLOCK_STATUSES:
                raise self.block(url, f, f'HTTP {f.status}')
            if f.status >= 500:
                err = f'HTTP {f.status}'
                continue
            return f
        raise Paused(f'{url} failed {ATTEMPTS}x: {err}', self.clock() + ERROR_PAUSE)

    def html_page(self, url: str, parse: Callable[[bytes], Any]) -> tuple[Fetched, Any]:
        """Fetch an HTML page that must be 200 and parse; a challenge page pauses, other junk fails."""
        f = self.get(url, HTML_CAP)
        if f.too_large:
            raise PageInvalid('HTML > 4MiB')
        if f.status != 200:
            raise PageInvalid(f'HTTP {f.status} {f.headers.get("Location", "")}'.strip())
        try:
            return f, parse(f.body)
        except PageInvalid as exc:
            marker = waf_marker(f.body)
            if marker:
                raise self.block(url, f, f'WAF/challenge page ({marker})') from None
            self.evidence(url, f, f'invalid page: {exc}')
            raise


def _keep_headers(h: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in h.items() if k.lower() in ('content-type', 'content-length', 'etag',
                                                        'last-modified', 'server', 'date')}


def _list_task(ctx: _Ctx, n: int) -> None:
    src, lst = ctx.src, ctx.state['list']
    url = src.list_url(n)
    f, parsed = ctx.html_page(url, lambda b: src.parse_list(b, n))
    rel = f'{src.mid}/lists/page-{n:03d}.html'
    rec = _write_file(ctx.root / rel, f.body)
    if lst['declared_pages'] not in (None, parsed['declared_pages']) or \
            lst['declared_items'] not in (None, parsed['declared_items']):
        lst.setdefault('changes', []).append({'page': n, 'at': _iso(ctx.clock()),
                                              'was': [lst['declared_pages'], lst['declared_items']],
                                              'now': [parsed['declared_pages'], parsed['declared_items']]})
    lst.update(declared_pages=parsed['declared_pages'], declared_items=parsed['declared_items'])
    lst['pages'][str(n)] = {'page': n, 'url': url, 'path': rel, **rec, 'http_status': f.status,
                            'headers': _keep_headers(f.headers), 'fetched_at': _iso(ctx.clock())}
    _apply_list(ctx, n, parsed)


def _apply_list(ctx: _Ctx, n: int, parsed: dict) -> None:
    ids = []
    for i, item in enumerate(parsed['items'], 1):
        aid = ctx.src.article_id(item['url'])
        ids.append(aid)
        ctx.state['articles'].setdefault(aid, {
            'id': aid, 'url': item['url'], 'list_page': n, 'list_index': i, 'list_title': item['title'],
            'list_date': item['date'], 'status': 'pending', 'html': None, 'text': None, 'meta': None,
            'attachments': [], 'attempts': 0, 'error': None})
    ctx.state['list']['pages'][str(n)].update(items=len(ids), article_ids=ids, declared_pages=parsed['declared_pages'],
                                              declared_items=parsed['declared_items'])
    _rebuild_order(ctx.state)


def _reparse_lists(ctx: _Ctx) -> None:
    """Re-derive items from the verified local list pages (no request), so parser fixes apply on resume."""
    for n, page in list(ctx.state['list']['pages'].items()):
        try:
            _apply_list(ctx, int(n), ctx.src.parse_list((ctx.root / page['path']).read_bytes(), int(n)))
        except PageInvalid:
            ctx.state['list']['pages'].pop(n)


def _article_dir(src: Source, aid: str) -> str:
    return f'{src.mid}/articles/{aid}'


def _write_meta(ctx: _Ctx, art: dict) -> None:
    keys = ('id', 'url', 'list_page', 'list_index', 'list_title', 'list_date', 'title', 'pub_date', 'column',
            'content_source', 'keywords', 'fetched_at', 'http_status', 'headers', 'body_chars', 'body_text_sha256')
    meta = {'source_id': ctx.src.mid, 'item_url': art['url'], **{k: art.get(k) for k in keys},
            'html_sha256': art['html']['sha256'], 'html_bytes': art['html']['bytes'],
            'attachments': [{k: a.get(k) for k in ('href_raw', 'label', 'url', 'filename', 'status', 'bytes',
                                                   'size_declared', 'mime', 'magic', 'sha256', 'error')}
                            for a in art['attachments']]}
    data = json.dumps(meta, ensure_ascii=False, indent=1).encode('utf-8')
    rel = _article_dir(ctx.src, art['id']) + '/meta.json'
    art['meta'] = {'path': rel, **_write_file(ctx.root / rel, data)}


def _apply_article(ctx: _Ctx, art: dict, body: bytes) -> None:
    """Rebuild text/meta and the attachment queue from verified HTML; keep verified attachments."""
    parsed = parse_article(ctx.src, body, art['url'])
    base = _article_dir(ctx.src, art['id'])
    art.update(parsed['meta'], body_chars=parsed['body_chars'], body_text_sha256=parsed['body_text_sha256'],
               inline_fulltext=parsed['inline_fulltext'])
    art['text'] = {'path': base + '/text.txt', **_write_file(ctx.root / base / 'text.txt',
                                                             parsed['text'].encode('utf-8'))}
    old = {a['url']: a for a in art.get('attachments', []) if a.get('url')}
    art['attachments'] = [old[a['url']] if a.get('url') in old and old[a['url']].get('filename') == a.get('filename')
                          else a for a in parsed['attachments']]
    _write_meta(ctx, art)


def _article_task(ctx: _Ctx, art: dict) -> None:
    url = art['url']
    art['attempts'] = art.get('attempts', 0) + 1
    try:
        f, _ = ctx.html_page(url, lambda b: parse_article(ctx.src, b, url))
    except PageInvalid as exc:
        gone = str(exc).startswith(('HTTP 404', 'HTTP 410'))
        art.update(error=str(exc)[:300], status='gone' if gone else
                   ('error' if art['attempts'] >= ATTACH_ATTEMPTS else 'pending'))
        return
    rel = _article_dir(ctx.src, art['id']) + '/page.html'
    art['html'] = {'path': rel, **_write_file(ctx.root / rel, f.body)}
    art.update(status='fetched', error=None, http_status=f.status, headers=_keep_headers(f.headers),
               fetched_at=_iso(ctx.clock()))
    _apply_article(ctx, art, f.body)
    ctx.bytes += len(f.body)


def _rebuild_local(ctx: _Ctx, art: dict) -> None:
    """Restore a damaged text.txt/meta.json from the verified page.html without any request."""
    _apply_article(ctx, art, (ctx.root / art['html']['path']).read_bytes())


def _classify_cached(ctx: _Ctx) -> int:
    """Backfill ``inline_fulltext`` evidence from hash-verified cached ``page.html`` (no request).

    Only the state changes: ``text.txt``/``meta.json``/attachments stay as published.
    """
    n = 0
    for art in ctx.state['articles'].values():
        rec = art.get('html')
        if not rec or (art.get('inline_fulltext') or {}).get('version') == INLINE_VERSION:
            continue
        body = (ctx.root / rec['path']).read_bytes()
        if _sha(body) != rec['sha256']:
            continue
        try:
            art['inline_fulltext'] = parse_article(ctx.src, body, art['url'])['inline_fulltext']
        except PageInvalid:
            continue
        n += 1
    return n


def _attachment_task(ctx: _Ctx, art: dict, att: dict) -> None:
    att['attempts'] = att.get('attempts', 0) + 1
    remaining = STEP_BYTES - ctx.bytes
    f = ctx.get(att['url'], min(FILE_CAP, max(remaining, 0)))
    ext = posixpath.splitext(att['filename'])[1].lower()

    def fail(status: str, error: str) -> None:
        if status == 'failed' and att['attempts'] >= ATTACH_ATTEMPTS:
            status = 'invalid'  # gave up: stays a reported gap, the material cannot complete
        att.update(status=status, error=error)

    if f.status in (404, 410):
        return fail('gone', f'HTTP {f.status}')
    if f.status != 200:
        return fail('failed', f'HTTP {f.status} {f.headers.get("Location", "")}'.strip())
    if f.too_large:
        if remaining < FILE_CAP:
            att['attempts'] -= 1
            raise _StepFull()
        return fail('too_large', 'file > 64MiB')
    problem = magic_problem(f.body[:1024], ext)
    if problem == 'html_instead_of_file':
        marker = waf_marker(f.body)
        if marker:
            raise ctx.block(att['url'], f, f'WAF/challenge page instead of attachment ({marker})')
        ctx.evidence(att['url'], f, 'html_instead_of_file')
    if problem:
        return fail('failed', problem)
    h = {k.lower(): v for k, v in f.headers.items()}
    length = h.get('content-length') if h.get('content-encoding', 'identity') == 'identity' else None
    if length and length.isdigit() and int(length) != len(f.body):
        return fail('failed', f'length {len(f.body)} != Content-Length {length}')
    rel = _article_dir(ctx.src, art['id']) + '/files/' + att['filename']
    target = (ctx.root / rel).resolve()
    if (ctx.root / _article_dir(ctx.src, art['id']) / 'files').resolve() not in target.parents:
        return fail('rejected', 'path_traversal')
    att.update(path=rel, **_write_file(target, f.body), status='ok', error=None,
               size_declared=int(length) if length and length.isdigit() else None,
               mime=f.headers.get('Content-Type'), magic=f.body[:8].hex(), fetched_at=_iso(ctx.clock()),
               headers=_keep_headers(f.headers))
    ctx.bytes += len(f.body)
    _write_meta(ctx, art)


class _StepFull(Exception):
    """The per-step byte budget cannot fit the next file; it is retried next step."""


# ---- progress, publication, sync ----------------------------------------------------

FINAL_GAPS = ('gone', 'too_large', 'invalid', 'rejected')


def summarize(src: Source, state: dict) -> dict[str, Any]:
    """Counts and explicit gaps; ``complete`` needs every declared page, article and attachment."""
    lst = state['list']
    declared = lst['declared_pages']
    pages = sorted(int(n) for n in lst['pages'])
    list_complete = bool(declared) and pages == list(range(1, declared + 1)) \
        and len(state['order']) == lst['declared_items']
    arts = [state['articles'][a] for a in state['order']]
    gaps, ok, fetched, att_ok, att_total, att_pending, payload = [], 0, 0, 0, 0, 0, 0
    for art in arts:
        if art['status'] in ('gone', 'error'):
            gaps.append({'article': art['id'], 'kind': f'article_{art["status"]}', 'detail': art.get('error')})
        if not (art.get('html') and art.get('text') and art.get('meta')):
            continue
        fetched += 1
        payload += art['html']['bytes'] + art['text']['bytes'] + art['meta']['bytes']
        atts = art['attachments']
        att_total += len(atts)
        good = [a for a in atts if a['status'] == 'ok']
        att_ok += len(good)
        payload += sum(a['bytes'] for a in good)
        att_pending += sum(a['status'] in ('pending', 'failed') for a in atts)
        for a in atts:
            if a['status'] in FINAL_GAPS:
                gaps.append({'article': art['id'], 'kind': f'attachment_{a["status"]}',
                             'detail': f'{a.get("url") or a["href_raw"]}: {a.get("error")}'})
        no_links = src.expects_attachments and not atts and not (art.get('inline_fulltext') or {}).get('complete')
        if no_links:
            gaps.append({'article': art['id'], 'kind': 'no_attachment_links',
                         'detail': '证监会正文页未发现 content-head 下载链接，需人工核对'})
        if len(good) == len(atts) and not no_links:
            ok += 1
    payload += sum(p['bytes'] for p in lst['pages'].values())
    complete = list_complete and ok == len(arts) and bool(arts)
    return {'list_pages_done': pages, 'declared_pages': declared, 'declared_items': lst['declared_items'],
            'list_complete': list_complete, 'articles_listed': len(arts), 'articles_fetched': fetched,
            'articles_complete': ok, 'attachments_ok': att_ok, 'attachments_known': att_total,
            'attachments_pending': att_pending, 'gaps': gaps, 'payload_bytes': payload, 'complete': complete}


def snapshot(root: Path, src: Source, state: dict, summary: dict) -> tuple[Path, str]:
    """Immutable, content-addressed manifest of every verified file, for registration."""
    files = [{'kind': 'list_page', 'path': p['path'], 'bytes': p['bytes'], 'sha256': p['sha256'], 'url': p['url'],
              'fetched_at': p['fetched_at']} for _, p in sorted(state['list']['pages'].items(), key=lambda kv: int(kv[0]))]
    articles = []
    for aid in state['order']:
        art = state['articles'][aid]
        if not (art.get('html') and art.get('text') and art.get('meta')):
            continue
        for kind in ('html', 'text', 'meta'):
            files.append({'kind': f'article_{kind}', 'article': aid, **{k: art[kind][k] for k in ('path', 'bytes', 'sha256')}})
        for a in art['attachments']:
            if a['status'] == 'ok':
                files.append({'kind': 'attachment', 'article': aid, 'url': a['url'], 'mime': a.get('mime'),
                              'magic': a.get('magic'), **{k: a[k] for k in ('path', 'bytes', 'sha256')}})
        articles.append({k: art.get(k) for k in ('id', 'url', 'list_page', 'list_index', 'title', 'pub_date',
                                                  'column', 'content_source', 'body_chars', 'body_text_sha256',
                                                  'inline_fulltext')})
    snap = {'schema': 'codeyun.skilldep.policy-crawl.manifest/1', 'material_id': src.mid, 'name': src.name,
            'plan_url': src.plan_url, 'scope_note': src.scope_note, 'reused_from': state.get('reused_from'),
            'summary': {k: v for k, v in summary.items()}, 'articles': articles, 'files': files}
    data = json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True).encode('utf-8')
    sha = _sha(data)
    path = Path(root) / src.mid / 'snapshots' / f'manifest-{sha[:16]}.json'
    if not (path.is_file() and sha256_file(path) == sha):
        _write_file(path, data)
    return path, sha


def build_record(root: Path, src: Source, state: dict, summary: dict, now: float) -> dict[str, Any]:
    path, sha = snapshot(root, src, state, summary)
    done = summary['complete'] and src.completes_material
    note = (f'{src.scope_note}。列表 {len(summary["list_pages_done"])}/{summary["declared_pages"]} 页，'
            f'正文 {summary["articles_fetched"]}/{summary["declared_items"]} 篇（完整 {summary["articles_complete"]}），'
            f'附件 {summary["attachments_ok"]}/{summary["attachments_known"]}，缺口 {len(summary["gaps"])}')
    rec = {'status': 'downloaded' if done else 'partial', 'availability': 'windows_local',
           'source_url': src.plan_url, 'path': (Path(root) / src.mid).as_posix(),
           'size_bytes': summary['payload_bytes'], 'manifest_path': path.as_posix(), 'manifest_sha256': sha,
           'finished_at': _iso(now)}
    if not done:  # CodeHub's scope schema keeps only page_limit/pages/items/note
        rec['scope'] = {'page_limit': len(summary['list_pages_done']), 'pages': summary['list_pages_done'],
                        'items': summary['articles_fetched'], 'note': note}
    return rec


def sync(root: Path, src: Source, record: dict, registry_factory: Callable[[], Any] | None,
         now: float) -> dict[str, Any]:
    """Register on CodeHub through its public API; on failure keep the latest record in the outbox."""
    from .acquire import register_and_verify

    p = _paths(root, src)
    try:
        if registry_factory is None:
            from .codehub import CodeHubSkillDep
            registry_factory = CodeHubSkillDep
        reg = registry_factory()
        rec = {'device_id': reg.device_id, 'device_name': reg.device_name, **record}
        res = register_and_verify(reg, src.mid, rec)
    except Exception as exc:
        rec, res = record, {'registered': False, 'register_error': f'{type(exc).__name__}: {exc}'[:300]}
    if res.get('registered'):
        p['outbox'].unlink(missing_ok=True)
    else:
        _wj(p['outbox'], {'record': rec, 'error': res.get('register_error') or 'read-back mismatch', 'at': _iso(now)})
    return {'registered': bool(res.get('registered')), 'error': res.get('register_error'),
            'status': record['status'], 'manifest_path': record['manifest_path']}


# ---- step ---------------------------------------------------------------------------

def _next_task(state: dict, tried: set) -> tuple[str, Any] | None:
    """List pages first, then each article's attachments right after it, in list order."""
    lst = state['list']
    if '1' not in lst['pages']:
        return ('list', 1) if ('list', 1) not in tried else None
    for n in range(2, (lst['declared_pages'] or 1) + 1):
        if str(n) not in lst['pages']:
            return ('list', n) if ('list', n) not in tried else None
    for aid in state['order']:
        art = state['articles'][aid]
        if art.get('html') and not (art.get('text') and art.get('meta')):
            return 'rebuild', art
        if art.get('html'):
            for i, att in enumerate(art['attachments']):
                if att['status'] in ('pending', 'failed') and (aid, i) not in tried:
                    return 'attachment', (art, att, i)
        elif art['status'] == 'pending' and aid not in tried:
            return 'article', art
    return None


def crawl_step(source: str, root: Path = DEFAULT_ROOT, *, session=None,
               registry_factory: Callable[[], Any] | None = None, max_articles: int = ARTICLES_PER_STEP,
               max_seconds: float = STEP_SECONDS, full_verify: bool = True, lock_held: bool = False,
               clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
               disk_free: Callable[[Path], int] | None = None,
               on_progress: Callable[[dict], None] | None = None) -> dict[str, Any]:
    """One bounded step for ``source`` (pbc/csrc or material id) from the persisted state.

    Fetches missing list pages, then up to ``max_articles`` article pages, each followed by
    its attachments, within ``STEP_BYTES``/``max_seconds``. Returns ``stop`` (why the step
    ended; ``done`` means nothing is left to try), new files, repairs, summary and sync.
    """
    src = get_source(source)
    root = Path(root)
    lock = None
    if not lock_held:
        lock = source_lock(root, src)
        try:
            lock.acquire(timeout=0)
        except Timeout:
            raise SourceBusy(f'{src.key} 已被另一个 crawler 持有：{lock.lock_file}') from None
    try:
        return _step(root, src, session or requests.Session(), registry_factory, max_articles, max_seconds,
                     full_verify, clock, sleep, disk_free or (lambda r: shutil.disk_usage(r).free), on_progress)
    finally:
        if lock:
            lock.release()


def _step(root, src, session, registry_factory, max_articles, max_seconds, full_verify, clock, sleep,
          disk_free, on_progress):
    p = _paths(root, src)
    t0 = clock()
    state = _rj(p['state']) or _new_state(src)
    ctx = _Ctx(root, src, state, session, clock, sleep, disk_free)
    repaired = verify_files(root, src, state, full=full_verify)
    _reparse_lists(ctx)
    reclassified = _classify_cached(ctx)
    ctx.save()
    new, tried, articles, stop = [], set(), 0, None
    retry_at = _ts(ctx.cur.get('retry_at'))
    if retry_at and retry_at > t0:
        stop = f"paused until {ctx.cur['retry_at']}: {ctx.cur.get('pause_reason')}"
    while not stop:
        task = _next_task(state, tried)
        if task is None:
            stop = 'done'
            break
        kind, arg = task
        if p['stop'].exists():
            stop = 'stop requested'
            break
        if clock() - t0 > max_seconds:
            stop = 'step time budget'
            break
        if kind == 'article' and articles >= max_articles:
            stop = f'article budget {max_articles}'
            break
        if ctx.bytes >= STEP_BYTES:
            stop = 'step byte cap 300MiB'
            break
        try:
            if kind == 'list':
                tried.add(('list', arg))
                _list_task(ctx, arg)
                new.append(state['list']['pages'][str(arg)]['path'])
            elif kind == 'rebuild':
                _rebuild_local(ctx, arg)
                new.append(arg['text']['path'])
            elif kind == 'article':
                tried.add(arg['id'])
                articles += 1
                _article_task(ctx, arg)
                if arg.get('html'):
                    new.append(arg['html']['path'])
            else:
                art, att, i = arg
                tried.add((art['id'], i))
                _attachment_task(ctx, art, att)
                if att['status'] == 'ok':
                    new.append(att['path'])
        except Paused as exc:
            ctx.cur.update(retry_at=_iso(exc.retry_at), pause_reason=exc.reason)
            stop = f'{exc.reason}; retry_at {ctx.cur["retry_at"]}'
        except _StepFull:
            stop = 'step byte cap 300MiB'
        except PageInvalid as exc:  # only list pages get here: the listing must be whole
            ctx.cur.update(retry_at=_iso(clock() + ERROR_PAUSE), pause_reason=f'list page {arg}: {exc}')
            stop = ctx.cur['pause_reason']
        else:
            if kind != 'rebuild':
                ctx.cur.update(retry_at=None, pause_reason=None)
        ctx.save()
        if on_progress:
            on_progress({'task': kind, 'new_files': len(new)})
    summary = summarize(src, state)
    state['summary'] = {k: v for k, v in summary.items() if k != 'gaps'} | {'gaps': len(summary['gaps'])}
    ctx.save()
    synced = None
    if new or repaired or reclassified or p['outbox'].exists():
        synced = sync(root, src, build_record(root, src, state, summary, clock()), registry_factory, clock())
    result = {'at': _iso(clock()), 'source': src.key, 'material_id': src.mid, 'stop': stop,
              'new_files': new, 'new_bytes': ctx.bytes, 'repaired': repaired, 'reclassified': reclassified,
              'cursor': {k: ctx.cur.get(k) for k in ('retry_at', 'pause_reason', 'last_request_at')},
              'summary': summary, 'sync': synced}
    with p['log'].open('a', encoding='utf-8') as log:
        log.write(json.dumps({**result, 'summary': state['summary']}, ensure_ascii=False) + '\n')
    return result


# ---- worker lifecycle ---------------------------------------------------------------

def _alive(info: dict | None) -> bool:
    import psutil

    if not info or not info.get('pid') or info.get('ended_at'):
        return False
    try:
        return abs(psutil.Process(info['pid']).create_time() - info.get('create_time', 0)) < 1
    except psutil.Error:
        return False


def source_status(source: str, root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    """Local progress from the F: state (no network, no CodeHub needed)."""
    src = get_source(source)
    p = _paths(root, src)
    state = _rj(p['state'])
    summary = summarize(src, state) if state else None
    worker = _rj(p['worker'])
    return {'root': str(root), 'source': src.key, 'material_id': src.mid, 'name': src.name,
            'completes_material': src.completes_material, 'scope_note': src.scope_note,
            'summary': summary,
            'cursor': {k: (state or {}).get('cursor', {}).get(k) for k in ('retry_at', 'pause_reason',
                                                                          'last_request_at', 'last_block')},
            'worker': worker, 'worker_alive': _alive(worker), 'stop_requested': p['stop'].exists(),
            'outbox': _rj(p['outbox'])}


def stop(source: str, root: Path = DEFAULT_ROOT) -> Path:
    """Ask the worker of ``source`` to stop at its next task boundary."""
    path = _paths(root, get_source(source))['stop']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_iso(time.time()), encoding='utf-8')
    return path


def run(source: str, root: Path = DEFAULT_ROOT, *, clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep, max_steps: int | None = None, **step_kwargs) -> dict[str, Any]:
    """Plain loop for one source: step, honour persisted pauses, heartbeat, exit on STOP or ``done``."""
    import psutil

    src = get_source(source)
    root = Path(root)
    p = _paths(root, src)
    lock = source_lock(root, src)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise SourceBusy(f'{src.key} 已被另一个 crawler 持有：{lock.lock_file}') from None
    me = psutil.Process()
    info = {'pid': me.pid, 'create_time': me.create_time(), 'source': src.key, 'started_at': _iso(clock()),
            'state': 'running', 'heartbeat_at': None, 'pause_reason': None, 'retry_at': None, 'steps': 0,
            'last_result': None, 'last_error': None}

    def beat(**kv):
        info.update(kv, heartbeat_at=_iso(clock()))
        _wj(p['worker'], info)

    try:
        p['stop'].unlink(missing_ok=True)  # a STOP left by an earlier stop does not block a new start
        beat()
        full = True
        while True:
            if p['stop'].exists():
                info['state'] = 'stopped'
                break
            if max_steps is not None and info['steps'] >= max_steps:
                info['state'] = 'step limit'
                break
            try:
                res = crawl_step(src.key, root, lock_held=True, clock=clock, sleep=sleep, full_verify=full,
                                 on_progress=lambda _: beat(state='fetching'), **step_kwargs)
                full = False
                cur = res['cursor']
                brief = {k: res[k] for k in ('at', 'stop', 'new_bytes', 'repaired', 'sync')}
                brief['new_files'] = len(res['new_files'])
                brief['summary'] = {k: v for k, v in res['summary'].items() if k != 'gaps'}
                beat(state='running', steps=info['steps'] + 1, last_result=brief, last_error=None,
                     pause_reason=cur.get('pause_reason'), retry_at=cur.get('retry_at'))
            except Exception as exc:
                beat(steps=info['steps'] + 1, last_error=f'{type(exc).__name__}: {exc}'[:300],
                     pause_reason='step error', retry_at=_iso(clock() + ERROR_PAUSE))
                cur = {'retry_at': info['retry_at']}
            else:
                if res['stop'] == 'done':
                    info['state'] = 'complete' if res['summary']['complete'] else 'done_with_gaps'
                    break
            until = _ts(cur.get('retry_at')) or 0
            while clock() < until and not p['stop'].exists():
                beat(state='paused')
                sleep(min(IDLE_SLICE, until - clock()))
        p['stop'].unlink(missing_ok=True)
        return info
    finally:
        info['ended_at'] = _iso(clock())
        if info['state'] in ('running', 'paused', 'fetching'):
            info['state'] = 'crashed'
        _wj(p['worker'], info)
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m backend.core.skilldep_materials.policy_crawl',
                                     description='Resumable PBC 部门规章 / CSRC 规章库 crawler (lists, article HTML, '
                                                 'structured text, all official attachments).')
    parser.add_argument('command', nargs='?', choices=('status', 'stop'))
    parser.add_argument('--source', required=True, help='pbc, csrc (or material id); all for status/stop')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--once', action='store_true', help='one bounded step')
    mode.add_argument('--run', action='store_true', help='continuous worker until STOP or nothing left')
    parser.add_argument('--root', default=str(DEFAULT_ROOT))
    parser.add_argument('--max-articles', type=int, default=ARTICLES_PER_STEP, help='article pages per step')
    args = parser.parse_args(argv)
    root = Path(args.root)
    keys = ['pbc', 'csrc'] if args.source == 'all' else [args.source]
    if args.source == 'all' and not args.command:
        parser.error('--source all only for status/stop: run one worker process per source')
    if args.command == 'status':
        out = {k: source_status(k, root) for k in keys}
    elif args.command == 'stop':
        out = {k: str(stop(k, root)) for k in keys}
    elif args.once:
        out = crawl_step(args.source, root, max_articles=args.max_articles)
    elif args.run:
        out = run(args.source, root, max_articles=args.max_articles)
    else:
        parser.error('choose --once, --run, status or stop')
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
