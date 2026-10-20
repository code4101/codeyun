"""Small, throttled samples of slow (crawl-only) SkillDep plan materials.

Some plan materials have no direct link: they are list pages or paged APIs that
must be walked slowly. This module only takes a fixed small sample of each so
the slow sources can start early; it never downloads a whole material, and its
manifest always says ``scope: sample``/``partial`` with the exact coverage.

Bounds: ``SAMPLE_TOTAL_LIMIT`` bytes on disk under the samples root (and the
root-wide ``acquire.HARD_TOTAL_LIMIT`` together with direct files), at most
``REQUESTS_PER_SOURCE`` requests per material (retries included), requests are
sequential with ``THROTTLE_SECONDS`` between them, and a 429/503 is retried once
only when its ``Retry-After`` is short. A file whose body is a challenge/denial
page or unparsable is kept as ``*.rejected`` for audit and never counts as ok.

Re-runs reuse every manifest file whose path, source URL, size and SHA-256 still
match, without any request; only missing or damaged files are fetched again.

Each material gets a stable ``<samples root>/<material id>/manifest.json`` whose
SHA-256 is what CodeHub stores for a ``partial`` record (a directory has no
hash of its own); ``sample_and_register`` writes and reads back those records.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin

import requests

from .acquire import DEFAULT_ROOT, HARD_TOTAL_LIMIT, Registry, direct_bytes, register_and_verify
from .download import fetch_to_file, sha256_file

MB = 1024 * 1024
DEFAULT_SAMPLES_DIRNAME = 'slow-samples'
DEFAULT_SAMPLES_ROOT = DEFAULT_ROOT / DEFAULT_SAMPLES_DIRNAME
MANIFEST = 'sample_manifest.json'
SOURCE_MANIFEST = 'manifest.json'
MANIFEST_SCHEMA = 'codeyun.slow_sample_manifest/v2'
SOURCE_MANIFEST_SCHEMA = 'codeyun.slow_sample_source/v1'
SAMPLE_TOTAL_LIMIT = 20 * MB
FILE_KEYS = ('kind', 'source_url', 'final_url', 'http_status', 'content_type', 'fetched_at',
             'path', 'bytes', 'sha256', 'status', 'error')
MANIFEST_RESERVE = 64 * 1024
REQUESTS_PER_SOURCE = 5
THROTTLE_SECONDS = 3.0
MAX_RETRY_AFTER = 60
PAGE_LIMIT = 1 * MB
API_PAGE_LIMIT = 4 * MB
DENIAL_MARKERS = ('验证码', 'captcha', '访问过于频繁', '安全验证', 'wzws', 'Access Denied',
                  '403 Forbidden', '请开启JavaScript', '滑动验证')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def decode_html(body: bytes) -> str:
    match = re.search(rb'charset=["\']?([\w-]+)', body[:4000], re.I)
    try:
        return body.decode(match.group(1).decode() if match else 'utf-8', 'ignore')
    except LookupError:
        return body.decode('utf-8', 'ignore')


def _links(html: str, base: str, pattern: str) -> list[str]:
    out: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html):
        url = urljoin(base, href.strip())
        if url != base and re.search(pattern, url) and url not in out:
            out.append(url)
    return out


def pbc_article_links(html: str, base: str) -> list[str]:
    """Rule pages linked from the PBC 部门规章 list page, in page order."""
    return _links(html, base, r'pbc\.gov\.cn/tiaofasi/144941/144957/\d+/index\.html$')


def csrc_article_links(html: str, base: str) -> list[str]:
    """Rule pages linked from the CSRC 规章库 list page, in page order."""
    return _links(html, base, r'csrc\.gov\.cn/csrc/c\d+/c\d+/content\.shtml$')


def html_problem(body: bytes) -> str | None:
    if len(body) < 512:
        return 'too_small'
    text = decode_html(body[:200000]).lower()
    for marker in DENIAL_MARKERS:
        if marker.lower() in text:
            return f'denial_marker:{marker}'
    return None if '<title' in text else 'no_title'


def gitee_page_problem(body: bytes) -> str | None:
    try:
        data = json.loads(body)
    except ValueError:
        return 'bad_json'
    if not isinstance(data, list):
        return 'error_json'
    if not data or not all(isinstance(i, dict) and 'number' in i for i in data):
        return 'unexpected_json'
    return None


# One entry per supported slow material. ``list_url`` must equal the plan URL;
# a changed plan URL is refused instead of guessed.
HTML_SOURCES: dict[str, dict[str, Any]] = {
    'sd-98bf2d2f5d43': {'list_url': 'http://www.pbc.gov.cn/tiaofasi/144941/144957/index.html',
                        'links': pbc_article_links, 'articles': 2},
    'sd-3b648dbbe220': {'list_url': 'https://www.csrc.gov.cn/csrc/c106256/fg.shtml',
                        'links': csrc_article_links, 'articles': 2},
}
API_SOURCES: dict[str, dict[str, Any]] = {
    'sd-b70266a40bc8': {'list_url': 'https://gitee.com/api/v5/repos/openharmony/docs/issues?state=all&per_page=100',
                        'page': 1},
}
SUPPORTED = (*HTML_SOURCES, *API_SOURCES)
COVERAGE = {
    'html': '仅列表第1页 + 前{n}篇正文HTML，不含附件、不含其余列表页',
    'api': '仅 REST 第{page}页（per_page=100，state=all），仅 openharmony/docs 仓库',
}


def load_manifest(root: Path) -> dict[str, Any]:
    path = Path(root) / MANIFEST
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def _save_manifest(root: Path, manifest: dict[str, Any]) -> None:
    path = Path(root) / MANIFEST
    tmp = path.with_name(MANIFEST + '.tmp')
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def _ok(record: dict[str, Any]) -> bool:
    # v1 manifests had no ``status``; their successful files carry content_check=ok.
    status = record.get('status') or ('ok' if record.get('content_check') == 'ok'
                                      and record.get('http_status') == 200
                                      and not record.get('truncated_at_cap') else 'failed')
    return status == 'ok'


def _same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _reusable(previous: Iterable[dict[str, Any]], path: Path, url: str) -> dict[str, Any] | None:
    for record in previous:
        if (_ok(record) and record.get('source_url') == url and _same_path(record.get('path', ''), path)
                and path.is_file() and path.stat().st_size == record.get('bytes')
                and sha256_file(path) == record.get('sha256')):
            return record
    return None


def _disk_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob('*')
               if p.is_file() and p.name.removesuffix('.tmp') not in (MANIFEST, SOURCE_MANIFEST))


class _Run:
    """Shared state of one sampling run: budget, throttle and counters."""

    def __init__(self, root: Path, session, sleep: Callable[[float], None], max_total_bytes: int):
        self.root, self.session, self.sleep = root, session, sleep
        # Direct files kept under the same materials root share its 100MiB cap.
        self.cap = min(max_total_bytes, SAMPLE_TOTAL_LIMIT, HARD_TOTAL_LIMIT - direct_bytes(root.parent))
        self.requests_total = 0
        self.counts = {'reused': 0, 'downloaded': 0, 'failed': 0}

    def remaining(self, replacing: Path) -> int:
        existing = replacing.stat().st_size if replacing.is_file() else 0
        return self.cap - MANIFEST_RESERVE - (_disk_bytes(self.root) - existing)


class _Source:
    def __init__(self, run: _Run, material_id: str, previous: list[dict[str, Any]]):
        self.run, self.material_id, self.previous = run, material_id, previous
        self.dir = run.root / material_id
        self.requests = 0
        self.stopped: str | None = None
        self.files: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def get(self, url: str, name: str, kind: str, limit: int,
            problem: Callable[[bytes], str | None]) -> bytes | None:
        """Reuse a verified local copy, or fetch once (plus one bounded Retry-After retry)."""
        target = self.dir / name
        old = _reusable(self.previous, target, url)
        if old:
            self.run.counts['reused'] += 1
            self.files.append({**_file_record({**old, 'status': 'ok', 'kind': kind,
                                               'path': target.as_posix()}), 'reused': True})
            return target.read_bytes()
        record = {'kind': kind, 'source_url': url, 'path': target.as_posix(), 'reused': False}
        body, error = self._fetch(url, target, limit)
        if body is not None:
            reason = problem(body)
            record.update(fetched_at=_now(), bytes=len(body), sha256=self._last.sha256,
                          content_type=self._last.content_type)
            if reason:
                rejected = target.with_name(target.name + '.rejected')
                os.replace(target, rejected)
                record.update(path=rejected.as_posix(), status='rejected', error=reason)
                body = None
            else:
                record['status'] = 'ok'
                self.run.counts['downloaded'] += 1
        else:
            record.update(status='failed', error=error, fetched_at=_now())
        if record['status'] != 'ok':
            self.run.counts['failed'] += 1
            self.errors.append(f'{url}: {record["error"]}')
        self.files.append(record)
        return body

    def _fetch(self, url: str, target: Path, limit: int) -> tuple[bytes | None, str]:
        waited = False
        for attempt in range(2):
            if self.stopped:
                return None, f'skipped: {self.stopped}'
            if self.requests >= REQUESTS_PER_SOURCE:
                return None, f'skipped: 每源请求上限 {REQUESTS_PER_SOURCE}'
            budget = min(limit, self.run.remaining(target))
            if budget <= 0:
                return None, f'skipped: 总硬上限 {self.run.cap} 字节已用尽'
            if self.run.requests_total and not waited:
                self.run.sleep(THROTTLE_SECONDS)
            self.requests += 1
            self.run.requests_total += 1
            try:
                self._last = fetch_to_file(url, target, max_bytes=budget, session=self.run.session)
                return target.read_bytes(), ''
            except requests.HTTPError as exc:
                response = exc.response
                status = getattr(response, 'status_code', None)
                if status in (429, 503):
                    retry_after = (response.headers.get('Retry-After') or '').strip()
                    wait = int(retry_after) if retry_after.isdigit() else None
                    if attempt == 0 and wait is not None and wait <= MAX_RETRY_AFTER:
                        self.run.sleep(max(wait, THROTTLE_SECONDS))
                        waited = True
                        continue
                    self.stopped = f'HTTP {status} Retry-After={retry_after!r}，停止本源'
                    return None, self.stopped
                return None, f'HTTPError {status}: {exc}'[:500]
            except Exception as exc:  # DownloadTooLarge, network errors: failure, never success
                return None, f'{type(exc).__name__}: {exc}'[:500]
        return None, 'retry exhausted'


def _sample_html(src: _Source, spec: dict[str, Any]) -> None:
    def problem(body: bytes) -> str | None:
        return html_problem(body) or (None if spec['links'](decode_html(body), spec['list_url'])
                                      else 'no_article_links')

    body = src.get(spec['list_url'], 'list_page1.html', 'list_page', PAGE_LIMIT, problem)
    if body is None:
        return
    links = spec['links'](decode_html(body), spec['list_url'])
    for index, url in enumerate(links[:spec['articles']], 1):
        src.get(url, f'article_{index:02d}.html', 'article_page', PAGE_LIMIT, html_problem)


def _sample_api(src: _Source, spec: dict[str, Any]) -> None:
    url = f"{spec['list_url']}&page={spec['page']}"
    src.get(url, f"issues_page{spec['page']}.json", 'api_page', API_PAGE_LIMIT, gitee_page_problem)


def sample(plan_items: Iterable[dict[str, Any]], material_ids: Iterable[str] | None = None, *,
           root: Path | None = None, session=None, sleep: Callable[[float], None] = time.sleep,
           max_total_bytes: int = SAMPLE_TOTAL_LIMIT, write_manifest: bool = True) -> dict[str, Any]:
    """Take (or reuse) the fixed small sample of each slow material; return the manifest."""
    root = Path(root or DEFAULT_SAMPLES_ROOT)
    plan = {m['id']: m for m in plan_items}
    ids = list(dict.fromkeys(material_ids or SUPPORTED))
    unsupported = [i for i in ids if i not in SUPPORTED]
    if unsupported:
        raise ValueError(f'没有慢源采样规则：{unsupported}')
    missing = [i for i in ids if i not in plan]
    if missing:
        raise ValueError(f'方案中不存在这些素材：{missing}')
    for material_id in ids:
        item = plan[material_id]
        spec = HTML_SOURCES.get(material_id) or API_SOURCES[material_id]
        if item.get('requires_login_or_purchase') or item.get('url') != spec['list_url']:
            raise ValueError(f'{material_id} 需要登录/购买，或方案 URL 已变化，拒绝猜测：{item.get("url")}')

    root.mkdir(parents=True, exist_ok=True)
    old = load_manifest(root)
    old_files = {r.get('material_id'): r.get('files', []) for r in old.get('resources', [])}
    run = _Run(root, session or requests.Session(), sleep, max_total_bytes)
    resources = {r['material_id']: r for r in old.get('resources', []) if r.get('material_id') not in ids}
    for material_id in ids:
        item = plan[material_id]
        src = _Source(run, material_id, old_files.get(material_id, []))
        if material_id in HTML_SOURCES:
            spec = HTML_SOURCES[material_id]
            _sample_html(src, spec)
            coverage = COVERAGE['html'].format(n=spec['articles'])
            expected = 1 + spec['articles']
        else:
            spec = API_SOURCES[material_id]
            _sample_api(src, spec)
            coverage = COVERAGE['api'].format(page=spec['page'])
            expected = 1
        ok = [f for f in src.files if f.get('status') == 'ok']
        if material_id in HTML_SOURCES:
            items = len([f for f in ok if f['kind'] == 'article_page'])
        else:
            items = len(json.loads((src.dir / ok[0]['path'].rsplit('/', 1)[-1]).read_bytes())) if ok else 0
        resources[material_id] = {
            'material_id': material_id, 'name': item.get('name'), 'plan_url': item['url'],
            'via': item.get('via'), 'plan_size_est': item.get('size'), 'plan_count': item.get('count'),
            'scope': 'sample' if len(ok) == expected else 'partial',
            'status': 'sampled' if len(ok) == expected else ('partial' if ok else 'failed'),
            'coverage': coverage, 'items': items, 'expected_files': expected, 'valid_files': len(ok),
            'bytes': sum(f['bytes'] for f in ok), 'requests_used': src.requests,
            'files': src.files, 'errors': src.errors,
            'not_downloaded_note': '仅样本，非整项资源下载；不得登记为 downloaded',
        }
    ordered = [resources[i] for i in dict.fromkeys((*SUPPORTED, *resources)) if i in resources]
    manifest = {
        'schema': MANIFEST_SCHEMA, 'generated_at': _now(), 'root': root.as_posix(), 'scope': 'sample',
        'hard_cap_bytes': run.cap, 'per_source_request_cap': REQUESTS_PER_SOURCE,
        'throttle_seconds': THROTTLE_SECONDS,
        'total_payload_bytes': sum(r['bytes'] for r in ordered),
        'run': {**run.counts, 'requests': run.requests_total},
        'resources': ordered,
    }
    if write_manifest:
        for material_id in ids:
            _write_json(root / material_id / SOURCE_MANIFEST, source_manifest(resources[material_id]))
        _save_manifest(root, manifest)
    return manifest


def _file_record(record: dict[str, Any]) -> dict[str, Any]:
    return {k: record[k] for k in FILE_KEYS if record.get(k) is not None}


def source_manifest(resource: dict[str, Any]) -> dict[str, Any]:
    """Run-independent per-material manifest, so its SHA-256 only changes with the sample."""
    keys = ('material_id', 'name', 'plan_url', 'via', 'scope', 'status', 'coverage', 'items',
            'expected_files', 'valid_files', 'bytes', 'not_downloaded_note')
    return {'schema': SOURCE_MANIFEST_SCHEMA, **{k: resource.get(k) for k in keys},
            'files': [_file_record(f) for f in resource['files']]}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def sample_record(resource: dict[str, Any], root: Path) -> dict[str, Any]:
    """CodeHub acquisition record of one sampled material: ``partial`` (never downloaded) or ``failed``."""
    record = {'availability': 'windows_local', 'source_url': resource['plan_url'],
              'finished_at': max((f.get('fetched_at') or '' for f in resource['files']), default='') or _now()}
    if not resource['valid_files']:
        return {**record, 'status': 'failed', 'error': '; '.join(resource['errors'])[:2000] or '没有有效样本'}
    manifest_path = Path(root) / resource['material_id'] / SOURCE_MANIFEST
    return {**record, 'status': 'partial', 'path': (Path(root) / resource['material_id']).as_posix(),
            'size_bytes': resource['bytes'], 'manifest_path': manifest_path.as_posix(),
            'manifest_sha256': sha256_file(manifest_path),
            'scope': {'page_limit': 1, 'pages': [1], 'items': resource['items'], 'note': resource['coverage']}}


def sample_and_register(registry: Registry, material_ids: Iterable[str] | None = None, *,
                        root: Path | None = None, session=None,
                        sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Sample (reusing verified copies) from CodeHub's plan, then register each as partial and read back."""
    root = Path(root or DEFAULT_SAMPLES_ROOT)
    wanted = list(dict.fromkeys(material_ids or SUPPORTED))
    manifest = sample(registry.materials(), wanted, root=root, session=session, sleep=sleep)
    results = []
    for resource in manifest['resources']:
        if resource['material_id'] not in wanted:
            continue
        record = {'device_id': registry.device_id, 'device_name': registry.device_name,
                  **sample_record(resource, root)}
        results.append({'material_id': resource['material_id'], 'sample_status': resource['status'],
                        **record, **register_and_verify(registry, resource['material_id'], record)})
    return {'run': manifest['run'], 'total_payload_bytes': manifest['total_payload_bytes'],
            'results': results}

