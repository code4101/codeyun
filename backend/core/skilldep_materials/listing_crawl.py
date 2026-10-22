"""Config-driven crawler: page through a public list API and download one attachment per item.

A JSON config (see ``configs/``) names the list URL (``{page}`` placeholder, GET or
POST), the JSON paths of the items and of the total count, and an attachment URL
template filled from each item's fields. Requests are serial with a random
10-20 s gap. Any 403/412/429 or a body saying the site is throttling stops the run
at once, records ``blocked`` with the time in ``state.json`` and never retries.
A 5xx (500/502/503/504) or network timeout is transient: wait a random 60-180 s and
retry, at most twice per request. A file still failing is recorded under ``skipped``
and the run moves on; three consecutive skipped files mean the site is down, so the
run stops as ``blocked``.

Files land in ``<out_dir>/files``; ``manifest.json`` lists every file with its
source URL, SHA-256 and size, ``listing.csv`` keeps the list metadata. Re-running
skips files already verified in the manifest. At the end the result is registered
on CodeHub as ``partial`` (same manifest + PUT acquisitions as ``acquire``).

``"mode": "article"`` crawls web articles instead of attachments. The list is either an
HTML page (``"list_format": "html"``; ``link_selector`` picks article links, optional
``link_pattern`` regex filters them) or the JSON list above with ``article_url`` filled
from each item. ``{cursor}`` in ``list_url`` pages by the ``cursor_path`` value of the
previous response (``list_url_first`` for the first page). Each article page is saved as
raw HTML plus a ``.txt`` (title, published time, source URL, then the text of
``content_selector``). A page whose body has no content but shows a captcha stops the run
as ``blocked``; it is never bypassed. ``sections`` (list of ``{name, list_url, ...}``)
runs the same crawl per column, each with its own ``max_items``. ``meta_labels`` adds labelled
fields (e.g. 发文字号) read from the cell after a ``<td>`` with that label to the ``.txt`` head.
``attachment_selector`` also downloads the links it matches on each article page (filtered by the
``attachment_pattern`` regex and ``attachment_domains``) next to the article. ``max_files`` caps the
total number of files saved (HTML, text and attachments) across all sections.

With a CodeHub registry (the CLI default) the run first checks the site's breaker
(``site_guard``) and refuses an active one. A throttle stop (403/412/429, throttling text,
a blank list/article page) trips the breaker for every device; a captcha or a site that
keeps failing stops the run without tripping it. ``first_batch_limit_mb`` of the material
lowers ``max_bytes`` for this round. ``downloading`` with the bytes so far is registered at
the start and after every list page; the end registers ``partial`` with its scope, or
``failed`` with the reason when nothing was fetched.

``"mode": "list"`` (or ``--material <id> --out <dir>``, which reads the material's ``list`` field
from CodeHub) is the checklist mode of ``ListModeCrawler``: each ``{name, url}`` is fetched one by
one, sites interleave and each site keeps its own 10-20 s gap.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from .download import USER_AGENT, filename_for, sha256_file
from .site_guard import SiteBlocked, assert_open, trip

CONFIG_DIR = Path(__file__).with_name('configs')
BLOCK_STATUSES = {403, 412, 429}
BLOCK_TEXTS = ('访问过于频繁', '限制访问')
CAPTCHA_TEXTS = ('验证码', 'captcha', '人机验证', '安全验证', '滑动验证')
TRANSIENT_STATUSES = {500, 502, 503, 504}
TRANSIENT_ERRORS = (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError)
RETRY_WAIT = (60, 180)
MAX_RETRIES = 2
MAX_CONSECUTIVE_SKIPS = 3
CHUNK = 1024 * 256


class Blocked(RuntimeError):
    """``throttle``: the site limited us (breaker trips); otherwise captcha / site down (stop, no breaker)."""

    def __init__(self, message: str, throttle: bool = False):
        super().__init__(message)
        self.throttle = throttle


class NoContent(RuntimeError):
    """Article page without extractable content (and no captcha): skipped."""


class Transient(RuntimeError):
    def __init__(self, reason: str, status: int | None = None):
        super().__init__(reason)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _wj(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def _rj(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def json_path(data: Any, path: str) -> Any:
    """Dotted path lookup (``a.b.0.c``); None when any step is missing."""
    for key in [k for k in path.split('.') if k]:
        if isinstance(data, list) and key.isdigit() and int(key) < len(data):
            data = data[int(key)]
        elif isinstance(data, dict):
            data = data.get(key)
        else:
            return None
    return data


def _fill(template: str, item: dict[str, Any]) -> str:
    return re.sub(r'\{([^{}]+)\}', lambda m: str(json_path(item, m.group(1)) or ''), template)


def check_blocked(status: int, body: bytes) -> str | None:
    if status in BLOCK_STATUSES:
        return f'HTTP {status}'
    head = body[:65536].decode('utf-8', errors='ignore')
    hit = next((t for t in BLOCK_TEXTS if t in head), None)
    return f'正文含“{hit}”' if hit else None


def load_config(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        p = CONFIG_DIR / (str(path) if str(path).endswith('.json') else f'{path}.json')
    cfg = json.loads(p.read_text(encoding='utf-8'))
    if cfg.get('mode') == 'list':
        required = ['material_id', 'out_dir', 'list']
    elif cfg.get('mode') == 'article':
        required = ['material_id', 'out_dir', 'content_selector']
        required += ['link_selector'] if cfg.get('list_format') == 'html' else ['items_path', 'article_url']
        if not cfg.get('sections'):
            required.append('list_url')
    else:
        required = ['material_id', 'list_url', 'items_path', 'attachment_url', 'out_dir']
    for key in required:
        if not cfg.get(key):
            raise ValueError(f'配置缺少 {key}：{p}')
    return cfg


class ListingCrawler:
    def __init__(self, cfg: dict[str, Any], *, session=None, registry=None,
                 sleep: Callable[[float], Any] = time.sleep,
                 rand: Callable[[float, float], float] = random.uniform, log: Callable[[str], Any] = print):
        self.cfg = cfg
        self.registry = registry
        self.out = Path(cfg['out_dir'])
        self.files = self.out / 'files'
        self.session = session or requests.Session()
        if session is None:
            # HTTP headers are latin-1: percent-encode non-ASCII (e.g. a Chinese Referer).
            self.session.headers.update({'User-Agent': USER_AGENT, **{
                k: requests.utils.requote_uri(v) for k, v in (cfg.get('headers') or {}).items()}})
        self.sleep, self.rand, self.log = sleep, rand, log
        self.gap = tuple(cfg.get('interval_seconds') or (10, 20))
        self.retry_wait = tuple(cfg.get('retry_wait_seconds') or RETRY_WAIT)
        self.max_retries = int(cfg.get('max_retries', MAX_RETRIES))
        self.requested = False
        self.manifest_path = self.out / 'manifest.json'
        self.state_path = self.out / 'state.json'
        self.manifest = _rj(self.manifest_path, None) or {
            'material_id': cfg['material_id'], 'name': cfg.get('name'), 'config': cfg.get('config_name'),
            'files': {}}
        self.state = _rj(self.state_path, {})

    # -- http --------------------------------------------------------------
    def _request(self, method: str, url: str, **kw) -> requests.Response:
        if self.requested:
            self.sleep(self.rand(*self.gap))
        self.requested = True
        try:
            return self.session.request(method, url, timeout=self.cfg.get('timeout', 60), **kw)
        except TRANSIENT_ERRORS as exc:
            raise Transient(f'{type(exc).__name__}: {exc}'[:300]) from exc

    def _retry(self, fn: Callable[[], Any], url: str) -> Any:
        """Run ``fn`` retrying transient failures after a random 60-180 s wait."""
        for attempt in range(self.max_retries + 1):
            try:
                return fn()
            except Transient as exc:
                if attempt >= self.max_retries:
                    raise
                wait = self.rand(*self.retry_wait)
                self.log(f'RETRY {attempt + 1}/{self.max_retries} in {wait:.0f}s after {exc}: {url}')
                self.sleep(wait)

    def _block(self, reason: str, url: str, throttle: bool = False) -> None:
        self.state.update(status='blocked', blocked_at=_now(), blocked_reason=reason, blocked_url=url,
                          throttle=throttle)
        if throttle and self.registry is not None:
            self.state['site_block'] = trip(self.registry, url, reason)
        _wj(self.state_path, self.state)
        raise Blocked(f'{reason}: {url}', throttle)

    def list_page(self, page: int, cfg: dict[str, Any] | None = None,
                  cursor: str | None = None) -> tuple[list[dict[str, Any]], int | None]:
        cfg = cfg or self.cfg
        method = (cfg.get('method') or 'GET').upper()
        template = cfg['list_url_first'] if cursor is None and cfg.get('list_url_first') else cfg['list_url']
        url = (template.replace('{page}', str(page)).replace('{page_size}', str(cfg.get('page_size', '')))
               .replace('{cursor}', requests.utils.quote(str(cursor or ''), safe='')))
        kw: dict[str, Any] = {}
        if method == 'POST' and cfg.get('post_data') is not None:
            kw['data'] = {k: str(v).replace('{page}', str(page)) for k, v in cfg['post_data'].items()}
        resp = self._request(method, url, **kw)
        body = resp.content
        # An empty later page may just end the list; an empty first page is a silent block.
        first_page = cursor is None and page == int(cfg.get('page_start', 1))
        if reason := check_blocked(resp.status_code, body) or (first_page and _blank(resp.status_code, body)):
            self._block(reason, url, throttle=True)
        if resp.status_code in TRANSIENT_STATUSES:
            raise Transient(f'HTTP {resp.status_code}', resp.status_code)
        resp.raise_for_status()
        if cfg.get('list_format') == 'html':
            return self._html_links(body.decode(_html_encoding(resp), errors='replace'), url, cfg), None
        data = json.loads(body.decode(resp.encoding or 'utf-8'))
        if cfg.get('cursor_path'):
            nxt = json_path(data, cfg['cursor_path'])
            self.next_cursor = str(nxt) if nxt not in (None, '') else None
        items = json_path(data, cfg['items_path']) or []
        total = json_path(data, cfg['total_path']) if cfg.get('total_path') else None
        return list(items), (int(total) if total is not None else None)

    @staticmethod
    def _html_links(html: str, base: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        from urllib.parse import urljoin
        from bs4 import BeautifulSoup
        pattern = re.compile(cfg['link_pattern']) if cfg.get('link_pattern') else None
        items, seen = [], set()
        for a in BeautifulSoup(html, 'html.parser').select(cfg['link_selector']):
            href = (a.get('href') or '').strip()
            if not href or (pattern and not pattern.search(href)):
                continue
            url = urljoin(base, href)
            if url not in seen:
                seen.add(url)
                items.append({'url': url, 'href': href, 'link_text': a.get_text(' ', strip=True)})
        return items

    def _article(self, url: str, html_path: Path, max_bytes: int) -> dict[str, Any]:
        """Fetch one article page; write raw HTML and extracted text."""
        from bs4 import BeautifulSoup
        cfg = self.cfg
        resp = self._request('GET', url)
        body = resp.content
        if reason := check_blocked(resp.status_code, body) or _blank(resp.status_code, body):
            self._block(reason, url, throttle=True)
        if resp.status_code in TRANSIENT_STATUSES:
            raise Transient(f'HTTP {resp.status_code}', resp.status_code)
        resp.raise_for_status()
        html = body.decode(_html_encoding(resp), errors='replace')
        soup = BeautifulSoup(html, 'html.parser')
        content = soup.select_one(cfg['content_selector'])
        text = content.get_text('\n', strip=True) if content else ''
        if not text:
            low = html.lower()
            if hit := next((t for t in cfg.get('captcha_texts') or CAPTCHA_TEXTS if t.lower() in low), None):
                self._block(f'页面出现验证码（“{hit}”），停止且不绕过', url)
            raise NoContent('正文选择器未匹配到内容')
        title = (_select_text(soup, cfg.get('title_selector') or 'h1')
                 or (soup.title.get_text(strip=True) if soup.title else ''))
        published = _select_text(soup, cfg.get('time_selector') or 'meta[property="article:published_time"]')
        labels = {k: _label_text(soup, k) for k in cfg.get('meta_labels') or []}
        head = ''.join(f'{k}：{v}\n' for k, v in labels.items())
        txt = f'标题：{title}\n{head}发布时间：{published}\n原文：{url}\n\n{text}\n'.encode('utf-8')
        if len(body) + len(txt) > max_bytes:
            raise RuntimeError(f'超过剩余字节额度 {max_bytes}')
        import hashlib
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_bytes(body)
        html_path.with_suffix('.txt').write_bytes(txt)
        return {'size_bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'text_bytes': len(txt),
                'title': title, 'published': published, **({'labels': labels} if labels else {}),
                **({'attachments': _attachment_links(soup, url, cfg)} if cfg.get('attachment_selector') else {})}

    def _download(self, url: str, target: Path, max_bytes: int) -> tuple[int, str]:
        import hashlib
        part = target.with_name(target.name + '.part')
        resp = self._request('GET', url, stream=True)
        try:
            first = b''
            chunks = resp.iter_content(CHUNK)
            for chunk in chunks:
                first += chunk
                if len(first) >= 65536 or first.startswith(b'%PDF'):
                    break
            if reason := check_blocked(resp.status_code, b'' if first.startswith(b'%PDF') else first):
                self._block(reason, url, throttle=True)
            if resp.status_code in TRANSIENT_STATUSES:
                raise Transient(f'HTTP {resp.status_code}', resp.status_code)
            resp.raise_for_status()
            digest, size = hashlib.sha256(), 0
            target.parent.mkdir(parents=True, exist_ok=True)
            with part.open('wb') as fh:
                for chunk in _chain(first, chunks):
                    size += len(chunk)
                    if size > max_bytes:
                        raise RuntimeError(f'超过剩余字节额度 {max_bytes}')
                    digest.update(chunk)
                    fh.write(chunk)
            os.replace(part, target)
            return size, digest.hexdigest()
        except TRANSIENT_ERRORS as exc:
            raise Transient(f'{type(exc).__name__}: {exc}'[:300]) from exc
        finally:
            resp.close()
            part.unlink(missing_ok=True)


    # -- main loop ---------------------------------------------------------
    def _verified(self, key: str) -> bool:
        e = self.manifest['files'].get(key)
        ok = bool(e) and (self.out / e['path']).is_file() and (self.out / e['path']).stat().st_size == e['size_bytes']
        return ok and (not e.get('text_path') or (self.out / e['text_path']).is_file())

    def _save(self) -> None:
        files = self.manifest['files']
        self.manifest.update(file_count=len(files),
                             payload_bytes=sum(e['size_bytes'] + e.get('text_bytes', 0) for e in files.values()),
                             updated_at=_now())
        _wj(self.manifest_path, self.manifest)
        _wj(self.state_path, self.state)

    def _write_csv(self, rows: list[dict[str, Any]]) -> None:
        fields = self.cfg.get('csv_fields') or sorted({k for r in rows for k in r
                                                       if not k.startswith('_') and not isinstance(r[k], (dict, list))})
        path = self.out / 'listing.csv'
        tmp = path.with_name('listing.csv.tmp')
        with tmp.open('w', encoding='utf-8-sig', newline='') as fh:
            lead = ['_section'] if any('_section' in r for r in rows) else []
            w = csv.DictWriter(fh, fieldnames=[*lead, '_key', '_attachment_url', '_file', *fields],
                               extrasaction='ignore')
            w.writeheader()
            for r in rows:
                w.writerow({k: re.sub(r'<[^>]+>', '', str(v)) if isinstance(v, str) else v for k, v in r.items()})
        os.replace(tmp, path)

    def run(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        self.skipped_keys: set[str] = set()
        self.consecutive_skips = 0
        # Earlier skips are retried on resume; only this run's skips are kept.
        self.state.update(status='running', started_at=_now(), pid=os.getpid(), skipped=[])
        for stale in ('blocked_at', 'blocked_reason', 'blocked_url', 'error', 'finished_at', 'stopped_reason',
                      'sections'):
            self.state.pop(stale, None)
        _wj(self.state_path, self.state)
        try:
            if self.registry is not None:
                assert_open(self.registry, self._source_url())
                self.report_progress()
            for sec in self.cfg.get('sections') or [{}]:
                got = self._crawl({**self.cfg, **sec}, rows, sec.get('name'))
                if sec.get('name'):
                    self.state.setdefault('sections', {})[sec['name']] = got
                if self.state.get('stopped_reason') in ('max_bytes', 'max_files'):
                    break
            self.state['status'] = 'done'
        except SiteBlocked as exc:
            self.state.update(status='refused', error=str(exc))
            self.log(f'REFUSED {exc}')
        except Blocked as exc:
            self.log(f'BLOCKED {exc}')
        except Exception as exc:
            self.state.update(status='failed', error=f'{type(exc).__name__}: {exc}'[:500])
            self.log(f'FAILED {exc}')
        finally:
            self.state['finished_at'] = _now()
            if rows:
                self._write_csv(rows)
            self._save()
        return self.state

    def _file_count(self) -> int:
        return sum(1 + bool(e.get('text_path')) for e in self.manifest['files'].values())

    def _attachments(self, links: list[str], article_rel: str, parent: str, section: str | None,
                     max_bytes: int, max_files: int) -> None:
        """Download an article's attachments into ``<article>_附件/``; failures are skipped."""
        for link in links:
            if self._verified(link):
                continue
            if self._file_count() >= max_files:
                self.state['stopped_reason'] = 'max_files'
                return
            used = sum(e['size_bytes'] + e.get('text_bytes', 0) for e in self.manifest['files'].values())
            rel = f'{article_rel[:-5]}_附件/{filename_for(link, "attachment")}'
            try:
                size, sha = self._retry(lambda: self._download(link, self.out / rel, max_bytes - used), link)
            except Blocked:
                raise
            except (Transient, RuntimeError, requests.HTTPError) as exc:
                self.state['skipped'].append({'key': link, 'url': link, 'status_code': getattr(exc, 'status', None),
                                              'reason': str(exc)[:300], 'at': _now()})
                self.log(f'SKIPPED attachment {exc}: {link}')
                continue
            self.manifest['files'][link] = {'path': rel, 'source_url': link, 'size_bytes': size, 'sha256': sha,
                                            'fetched_at': _now(), 'attachment_of': parent,
                                            **({'section': section} if section else {})}
            self._save()
            self.log(f'  attachment {size} B {link}')

    def _crawl(self, cfg: dict[str, Any], rows: list[dict[str, Any]], section: str | None) -> int:
        """Crawl one list (or section); returns how many items it contributed."""
        article = cfg.get('mode') == 'article'
        max_items = int(cfg.get('max_items') or 10 ** 9)
        max_bytes = int(cfg.get('max_bytes') or 10 ** 15)
        max_files = int(cfg.get('max_files') or 10 ** 9)
        key_field = cfg.get('key_field')
        page, page_end = int(cfg.get('page_start', 1)), cfg.get('max_pages')
        cursor: str | None = None
        count = 0
        while count < max_items:
            if page_end and page > int(cfg.get('page_start', 1)) + int(page_end) - 1:
                break
            self.next_cursor = None
            try:
                items, total = self._retry(lambda: self.list_page(page, cfg, cursor), f'list page {page}')
            except Transient as exc:
                self._block(f'列表页重试后仍失败（站点异常）：{exc}', f'list page {page}')
            if total is not None and not section:
                self.state['total'] = total
            if not items:
                break
            for item in items:
                if count >= max_items:
                    break
                if any(str(json_path(item, k)) != str(v) for k, v in (cfg.get('item_filter') or {}).items()):
                    continue
                if article:
                    url = item['url'] if cfg.get('list_format') == 'html' else _fill(cfg['article_url'], item)
                else:
                    url = _fill(cfg['attachment_url'], item)
                key = str(json_path(item, key_field)) if key_field else url
                if key in self.skipped_keys or any(r['_key'] == key for r in rows):
                    continue
                row = {**item, '_key': key, '_attachment_url': url, **({'_section': section} if section else {})}
                rows.append(row)
                count += 1
                if self._verified(key):
                    row['_file'] = self.manifest['files'][key]['path']
                    continue
                if self._file_count() + (2 if article else 1) > max_files:
                    rows.pop()
                    count -= 1
                    self.state['stopped_reason'] = 'max_files'
                    break
                used = sum(e['size_bytes'] + e.get('text_bytes', 0) for e in self.manifest['files'].values())
                if used >= max_bytes:
                    self.state['stopped_reason'] = 'max_bytes'
                    break
                sub = f'{_safe(section)}/' if section else ''
                try:
                    if article:
                        name = re.sub(r'\.s?html?$', '', filename_for(url, '')) or f'item-{len(rows)}'
                        rel = f'files/{sub}{name}.html'
                        meta = self._retry(lambda: self._article(url, self.out / rel, max_bytes - used), url)
                    else:
                        rel = f'files/{sub}{filename_for(url, f"item-{len(rows)}")}'
                        size, sha = self._retry(lambda: self._download(url, self.out / rel, max_bytes - used), url)
                        meta = {'size_bytes': size, 'sha256': sha}
                except (Transient, NoContent) as exc:
                    rows.pop()
                    count -= 1
                    self.skipped_keys.add(key)
                    status = exc.status if isinstance(exc, Transient) else None
                    self.state['skipped'].append({'key': key, 'url': url, 'status_code': status,
                                                  'reason': str(exc), 'at': _now()})
                    _wj(self.state_path, self.state)
                    self.log(f'SKIPPED {exc}: {url}')
                    if isinstance(exc, Transient):
                        self.consecutive_skips += 1
                        if self.consecutive_skips >= MAX_CONSECUTIVE_SKIPS:
                            self._block(f'连续 {self.consecutive_skips} 条重试后仍失败，站点异常：{exc}', url)
                    continue
                self.consecutive_skips = 0
                links = meta.pop('attachments', [])
                self.manifest['files'][key] = {'path': rel, 'source_url': url, **meta, 'fetched_at': _now(),
                                               **({'text_path': rel[:-5] + '.txt'} if article else {}),
                                               **({'section': section} if section else {})}
                row['_file'] = rel
                self._save()
                self.log(f'[{len(rows)}] {meta["size_bytes"]} B {url}')
                if links:
                    self.manifest['files'][key]['attachments'] = links
                    self._attachments(links, rel, url, section, max_bytes, max_files)
                if self.state.get('stopped_reason') == 'max_files':
                    break
            if self.state.get('stopped_reason') in ('max_bytes', 'max_files'):
                break
            self.report_progress()
            if cfg.get('cursor_path'):
                if not self.next_cursor:
                    break
                cursor = self.next_cursor
            page += 1
        return count

    # -- registration ------------------------------------------------------
    def _source_url(self) -> str:
        return self.cfg.get('source_url') or self.cfg.get('list_url') or _section_url(self.cfg['sections'][0])

    def report_progress(self) -> None:
        """``downloading`` with the bytes fetched so far (no-op without a registry; never raises)."""
        if self.registry is None:
            return
        from .progress import report_downloading
        files = self.manifest['files']
        report_downloading(self.registry, self.cfg['material_id'], self.out,
                           sum(e['size_bytes'] + e.get('text_bytes', 0) for e in files.values()),
                           source_url=self._source_url())

    def failed_record(self) -> dict[str, Any]:
        reason = self.state.get('blocked_reason') or self.state.get('error') or '没有取到任何文件'
        return {'status': 'failed', 'availability': 'windows_local', 'source_url': self._source_url(),
                'finished_at': _now(), 'error': str(reason)[:500]}

    def record(self) -> dict[str, Any]:
        files = self.manifest['files']
        total = self.state.get('total')
        note = (self.cfg.get('scope_note') or '{count} 份/共 {total} 份').format(count=len(files), total=total)
        if sections := self.state.get('sections'):
            note += '：' + '，'.join(f'{k} {v} 篇' for k, v in sections.items())
        if self.state.get('status') == 'blocked':
            note += f'（{self.state.get("blocked_at")} 被限制访问后停止：{self.state.get("blocked_reason")}）'
        if skipped := self.state.get('skipped'):
            note += f'；{len(skipped)} 份因服务端错误/超时重试后跳过：' + '，'.join(
                f'{s["url"]}（{s.get("status_code") or s.get("reason")}，{s["at"]}）' for s in skipped)
        complete = (self.state.get('status') == 'done' and not self.state.get('stopped_reason')
                    and not self.state.get('skipped') and total is not None and len(files) >= int(total))
        return {'status': 'downloaded' if complete else 'partial', 'availability': 'windows_local',
                'source_url': self._source_url(),
                'path': self.out.as_posix(),
                'size_bytes': sum(e['size_bytes'] + e.get('text_bytes', 0) for e in files.values()),
                'manifest_path': self.manifest_path.as_posix(), 'manifest_sha256': sha256_file(self.manifest_path),
                'finished_at': _now(), 'scope': {'items': len(files), 'total': total, 'note': note,
                                                 **({'sections': self.state['sections']} if self.state.get('sections') else {}),
                                                 'skipped': self.state.get('skipped') or []}}

    def register(self, registry=None) -> dict[str, Any]:
        """PUT ``record()`` (or ``failed_record()``) and verify the read-back; the PUT scope keeps only the
        fields CodeHub persists (total, sections and skipped are already spelled out in ``note``)."""
        from .acquire import REMOTE_SCOPE_KEYS, register_and_verify
        registry = registry or self.registry
        if registry is None:
            from .codehub import CodeHubSkillDep
            registry = CodeHubSkillDep()
        body = self.record() if self.manifest['files'] else self.failed_record()
        if 'scope' in body:
            body['scope'] = {k: v for k, v in body['scope'].items() if k in REMOTE_SCOPE_KEYS}
        rec = {'device_id': registry.device_id, 'device_name': registry.device_name, **body}
        res = register_and_verify(registry, self.cfg['material_id'], rec)
        self.state['registration'] = {'registered': bool(res.get('registered')), 'error': res.get('register_error'),
                                      'at': _now()}
        _wj(self.state_path, self.state)
        return res


FILE_EXT = re.compile(r'\.(pdf|docx?|xlsx?|pptx?|wps|et|zip|rar|7z|ofd|csv)$', re.I)
DATE_RE = re.compile(r'((?:19|20)\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})')
CONTENT_SELECTORS = ('.TRS_Editor', '#zoom', '.article-content', '.content', 'article', '#content', '.main')


class ListModeCrawler(ListingCrawler):
    """Checklist mode: fetch each ``{name, url}`` of the material's ``list`` field one by one.

    A file URL (pdf/doc/xls/...) is saved as is; a web page is saved as HTML plus a ``.txt`` (title,
    date, source URL, text) and the same-site attachments it links. Items are grouped by host: one
    host waits a random 10-20 s between requests, different hosts interleave. A throttle or captcha
    stops only that host (``blocked_sites``; a throttle trips the CodeHub breaker), and hosts with an
    active breaker are skipped up front. Items without a URL are recorded in ``skipped``. The result
    is ``downloaded`` only when every list item is fetched.
    """

    def __init__(self, cfg: dict[str, Any], *, clock: Callable[[], float] = time.monotonic, **kw):
        super().__init__(cfg, **kw)
        self.clock = clock
        self.ready: dict[str, float] = {}

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        from .site_guard import host_of
        host = host_of(url)
        if host in self.ready and (wait := self.ready[host] - self.clock()) > 0:
            self.sleep(wait)
        try:
            return self.session.request(method, url, timeout=self.cfg.get('timeout', 60), **kw)
        except TRANSIENT_ERRORS as exc:
            raise Transient(f'{type(exc).__name__}: {exc}'[:300]) from exc
        finally:
            self.ready[host] = self.clock() + self.rand(*self.gap)

    def _block(self, reason: str, url: str, throttle: bool = False) -> None:
        from .site_guard import host_of
        rec = {'host': host_of(url), 'blocked_at': _now(), 'reason': reason, 'url': url, 'throttle': throttle}
        if throttle and self.registry is not None:
            rec['site_block'] = trip(self.registry, url, reason)
        self.state.setdefault('blocked_sites', []).append(rec)
        _wj(self.state_path, self.state)
        raise Blocked(f'{reason}: {url}', throttle)

    def _skip(self, url: str, name: str | None, reason: str, status: int | None = None) -> None:
        self.state['skipped'].append({'key': url, 'url': url, 'name': name, 'status_code': status,
                                      'reason': reason[:300], 'at': _now()})
        _wj(self.state_path, self.state)

    def _page(self, url: str, rel: str, max_bytes: int) -> tuple[str, dict[str, Any]]:
        """Fetch a non-file URL: HTML -> page + text + attachment links; any other body is kept as a file."""
        import hashlib
        from urllib.parse import urlsplit
        from bs4 import BeautifulSoup
        from .site_guard import site_name
        resp = self._request('GET', url)
        body = resp.content
        if reason := check_blocked(resp.status_code, body) or _blank(resp.status_code, body):
            self._block(reason, url, throttle=True)
        if resp.status_code in TRANSIENT_STATUSES:
            raise Transient(f'HTTP {resp.status_code}', resp.status_code)
        resp.raise_for_status()
        if len(body) > max_bytes:
            raise RuntimeError(f'超过剩余字节额度 {max_bytes}')
        meta: dict[str, Any] = {'size_bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        is_html = 'html' in (resp.headers.get('Content-Type') or '').lower() or body.lstrip()[:15].lower().startswith(
            (b'<!doctype', b'<html'))
        if not is_html:
            rel = rel[:-5] + (Path(urlsplit(url).path).suffix.lower() or '.bin')
            (self.out / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.out / rel).write_bytes(body)
            return rel, meta
        html = body.decode(_html_encoding(resp), errors='replace')
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        content = next((el for sel in (self.cfg.get('content_selector'), *CONTENT_SELECTORS)
                        if sel and (el := soup.select_one(sel)) and el.get_text(strip=True)), soup.body or soup)
        text = content.get_text('\n', strip=True)
        if not text:
            low = html.lower()
            if hit := next((t for t in CAPTCHA_TEXTS if t.lower() in low), None):
                self._block(f'页面出现验证码（“{hit}”），停止且不绕过', url)
            raise NoContent('页面没有可提取的正文')
        title = (_select_text(soup, self.cfg.get('title_selector') or 'h1')
                 or (soup.title.get_text(strip=True) if soup.title else ''))
        published = (_select_text(soup, 'meta[name="PubDate"]')
                     or _select_text(soup, 'meta[property="article:published_time"]'))
        if not published and (m := DATE_RE.search(content.get_text(' ')) or DATE_RE.search(soup.get_text(' '))):
            published = f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
        txt = f'标题：{title}\n发布日期：{published}\n原文：{url}\n\n{text}\n'.encode('utf-8')
        target = self.out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        target.with_suffix('.txt').write_bytes(txt)
        links = [u for u in _attachment_links(soup, url, {'attachment_selector': 'a[href]'})
                 if site_name(u) == site_name(url)]
        return rel, {**meta, 'text_bytes': len(txt), 'text_path': rel[:-5] + '.txt', 'title': title,
                     'published': published, 'attachments': links}

    def run(self) -> dict[str, Any]:
        from .site_guard import active_block, host_of
        self.state.update(status='running', started_at=_now(), pid=os.getpid(), skipped=[], blocked_sites=[],
                          total=len(self.cfg['list']))
        for stale in ('blocked_at', 'blocked_reason', 'blocked_url', 'error', 'finished_at', 'stopped_reason'):
            self.state.pop(stale, None)
        _wj(self.state_path, self.state)
        try:
            queues: dict[str, list[tuple[int, dict[str, Any]]]] = {}
            for i, item in enumerate(self.cfg['list'], 1):
                url = (item.get('url') or '').strip()
                if url:
                    queues.setdefault(host_of(url), []).append((i, {**item, 'url': url}))
                else:
                    self._skip('', item.get('name'), f'清单第 {i} 条没有 url')
            if self.registry is not None:
                for host in list(queues):
                    if hit := active_block(self.registry, f'https://{host}/'):
                        for _, item in queues.pop(host):
                            self._skip(item['url'], item.get('name'), f'站点 {hit["site"]} 熔断中，跳过')
                self.report_progress()
            while queues:
                # The host that may be requested soonest goes next, so different sites interleave.
                host = min(queues, key=lambda h: self.ready.get(h, float('-inf')))
                i, item = queues[host].pop(0)
                try:
                    self._item(i, item)
                except Blocked as exc:
                    self.log(f'BLOCKED {host}: {exc}')
                    for _, rest in [(i, item), *queues.pop(host)]:
                        if not self._verified(rest['url']):
                            self._skip(rest['url'], rest.get('name'), f'站点停止：{exc}')
                    continue
                if not queues[host]:
                    queues.pop(host)
            self.state['status'] = 'done'
        except SiteBlocked as exc:
            self.state.update(status='refused', error=str(exc))
            self.log(f'REFUSED {exc}')
        except Exception as exc:
            self.state.update(status='failed', error=f'{type(exc).__name__}: {exc}'[:500])
            self.log(f'FAILED {exc}')
        finally:
            self.state['finished_at'] = _now()
            self._save()
        return self.state

    def _item(self, i: int, item: dict[str, Any]) -> None:
        from urllib.parse import urlsplit
        url = item['url']
        if self._verified(url):
            return
        max_bytes = int(self.cfg.get('max_bytes') or 10 ** 15)
        used = sum(e['size_bytes'] + e.get('text_bytes', 0) for e in self.manifest['files'].values())
        stem = f'files/{i:02d}-{_safe(item.get("name") or "item")[:60]}'
        suffix = Path(urlsplit(url).path).suffix.lower()
        try:
            if FILE_EXT.search(suffix):
                rel = stem + suffix
                size, sha = self._retry(lambda: self._download(url, self.out / rel, max_bytes - used), url)
                meta: dict[str, Any] = {'size_bytes': size, 'sha256': sha}
            else:
                rel, meta = self._retry(lambda: self._page(url, stem + '.html', max_bytes - used), url)
        except Blocked:
            raise
        except (Transient, NoContent, RuntimeError, requests.HTTPError) as exc:
            self._skip(url, item.get('name'), str(exc), getattr(exc, 'status', None))
            self.log(f'SKIPPED {exc}: {url}')
            return
        links = meta.pop('attachments', [])
        self.manifest['files'][url] = {'path': rel, 'source_url': url, 'name': item.get('name'), 'list_index': i,
                                       **meta, 'fetched_at': _now()}
        self._save()
        self.log(f'[{i}] {meta["size_bytes"]} B {url}')
        if links:
            self.manifest['files'][url]['attachments'] = links
            self._attachments(links, rel, url, None, max_bytes, 10 ** 9)
        self.report_progress()

    def _source_url(self) -> str:
        return self.cfg.get('source_url') or next((x['url'] for x in self.cfg['list'] if x.get('url')), '')

    def record(self) -> dict[str, Any]:
        rec = super().record()
        items = self.cfg['list']
        missing = [f'#{i} {x.get("name")}' for i, x in enumerate(items, 1)
                   if not (x.get('url') or '').strip() or not self._verified(x['url'].strip())]
        note = f'清单 {len(items) - len(missing)}/{len(items)} 份'
        if missing:
            note += '；缺：' + '，'.join(missing)
        if blocked := self.state.get('blocked_sites'):
            note += '；被限制站点：' + '，'.join(f'{b["host"]}（{b["reason"]}，{b["blocked_at"]}）' for b in blocked)
        if skipped := self.state.get('skipped'):
            note += '；跳过：' + '，'.join(f'{s.get("url") or s.get("name")}（{s["reason"]}）' for s in skipped)
        complete = self.state.get('status') == 'done' and not missing and not self.state.get('skipped')
        rec['status'] = 'downloaded' if complete else 'partial'
        rec['scope'].update(items=len(items) - len(missing), total=len(items), note=note)
        return rec


def list_config(registry, material_id: str, out_dir: str) -> dict[str, Any]:
    """Checklist-mode config built from the material's ``list`` field on CodeHub."""
    material = next((m for m in registry.materials() if m['id'] == material_id), None)
    if not material or not material.get('list'):
        raise ValueError(f'CodeHub 素材 {material_id} 没有 list 字段')
    return {'mode': 'list', 'material_id': material_id, 'name': material.get('name'), 'out_dir': out_dir,
            'list': material['list'], 'source_url': material.get('url')}


def _select_text(soup, selector: str) -> str:
    el = soup.select_one(selector)
    if el is None:
        return ''
    return (el.get('content') or el.get('datetime') or el.get_text(' ', strip=True)).strip()


def _html_encoding(resp) -> str:
    """Header charset, else the page's ``<meta charset>``, else UTF-8 (requests' ISO-8859-1 guess garbles CJK)."""
    if 'charset' in (getattr(resp, 'headers', {}).get('Content-Type') or '').lower() and resp.encoding:
        return resp.encoding
    m = re.search(rb'<meta[^>]+charset=["\x27]?([\w-]+)', resp.content[:4096], re.I)
    return m.group(1).decode('ascii') if m else 'utf-8'


def _label_text(soup, label: str) -> str:
    """Text of the cell right after the ``<td>`` whose text is ``label`` (optionally with a colon)."""
    for td in soup.find_all('td'):
        if td.get_text(strip=True).rstrip('：:') == label:
            nxt = td.find_next_sibling('td')
            return nxt.get_text(' ', strip=True) if nxt else ''
    return ''


def _attachment_links(soup, base: str, cfg: dict[str, Any]) -> list[str]:
    from urllib.parse import urljoin, urlparse
    pattern = re.compile(cfg.get('attachment_pattern') or r'\.(pdf|docx?|xlsx?|wps|et|zip|rar|ofd)$', re.I)
    domains = cfg.get('attachment_domains') or []
    links: list[str] = []
    for a in soup.select(cfg['attachment_selector']):
        url = urljoin(base, (a.get('href') or '').strip())
        parts = urlparse(url)
        host = parts.hostname or ''
        if not pattern.search(parts.path):
            continue
        if domains and not any(host == d or host.endswith('.' + d) for d in domains):
            continue
        if url not in links:
            links.append(url)
    return links


def _blank(status: int, body: bytes) -> str | None:
    return '无提示空白页' if status == 200 and not body.strip() else None


def _section_url(sec: dict[str, Any]) -> str:
    return sec.get('source_url') or sec.get('list_url_first') or sec['list_url']


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or 'section'


def _chain(first: bytes, rest):
    if first:
        yield first
    for chunk in rest:
        if chunk:
            yield chunk


def _pid_alive(pid: int) -> bool:
    import psutil  # os.kill(pid, 0) terminates the process on Windows
    return psutil.pid_exists(pid)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='python -m backend.core.skilldep_materials.listing_crawl',
                                 description='Config-driven list API crawler for SkillDep materials (partial).')
    ap.add_argument('config', nargs='?', help='Config JSON path or name under configs/')
    ap.add_argument('--material', help='Checklist mode: crawl the list field of a CodeHub material (needs --out)')
    ap.add_argument('--out', help='With --material: output directory')
    ap.add_argument('--system-ca', action='store_true', help='用 Windows 证书库校验（站点缺中间证书时用）')
    ap.add_argument('--no-register', action='store_true', help='Skip CodeHub registration')
    ap.add_argument('--register-only', action='store_true',
                    help='Do not crawl: register the current result (partial/downloaded/failed) from state.json')
    ap.add_argument('--after-pid', type=int, help='With --register-only: wait for this crawler PID to exit first')
    args = ap.parse_args(argv)
    if args.system_ca:
        import truststore
        truststore.inject_into_ssl()
    if args.material:
        if not args.out:
            ap.error('--material 需要 --out')
        from .codehub import CodeHubSkillDep
        cfg = list_config(CodeHubSkillDep(), args.material, args.out)
    elif args.config:
        cfg = load_config(args.config)
    else:
        ap.error('需要 config 或 --material')
    cls = ListModeCrawler if cfg.get('mode') == 'list' else ListingCrawler
    if args.register_only:
        while args.after_pid and _pid_alive(args.after_pid):
            time.sleep(30)
        crawler = cls(cfg, log=lambda m: print(f'{_now()} {m}', flush=True))
        res = crawler.register()
        print(json.dumps({'registered': res.get('registered'), 'error': res.get('register_error')},
                         ensure_ascii=False), flush=True)
        return 0 if res.get('registered') else 1
    registry = None
    if not args.no_register:
        from .batch import first_batch_limit_bytes
        from .codehub import CodeHubSkillDep
        registry = CodeHubSkillDep()
        material = next((m for m in registry.materials() if m['id'] == cfg['material_id']), None)
        if cap := material and first_batch_limit_bytes(material):
            cfg['max_bytes'] = min(int(cfg.get('max_bytes') or cap), cap)
    crawler = cls(cfg, registry=registry, log=lambda m: print(f'{_now()} {m}', flush=True))
    state = crawler.run()
    print(json.dumps({k: state.get(k) for k in ('status', 'total', 'blocked_at', 'blocked_reason', 'error',
                                                 'site_block')}, ensure_ascii=False), flush=True)
    if registry is not None and state.get('status') != 'refused':
        res = crawler.register()
        print(json.dumps({'registered': res.get('registered'), 'error': res.get('register_error')},
                         ensure_ascii=False), flush=True)
    return 0 if state.get('status') in ('done', 'blocked', 'refused') else 1


if __name__ == '__main__':
    raise SystemExit(main())
