"""Resumable fetcher for explicitly verified public documents (HTML body + required attachments).

A ``DocumentSpec`` lists the exact URLs of one SkillDep material: HTML parts whose body
is taken from a CSS selector and must hold the asserted text, and attachments whose
format magic is checked. Nothing is discovered: no crawling, no search pages, no browser.

Layout under ``<root>/<material_id>/``: ``parts/<key>/<sha256>.raw`` (bytes as served),
``parts/<key>/<sha256>.txt`` (selected body text), ``state.json`` (per-part hashes,
retry/backoff) and ``manifests/<sha256>.json``. Payload, text and manifest files are all
content addressed and never rewritten, so every manifest keeps pointing at its own bytes
(older ``parts/<key>.raw|txt`` files stay readable and untouched). One process per
material (``FileLock`` next to the directory). A stored part whose hash still matches is
never fetched again; a changed spec revalidates the cached bytes offline, and a part counts
as verified only for the exact URL and checks (``check_key``) it was verified against.

The record is saved locally first (``CatalogStore.record_local``) and then synced with a
bounded ``local_sync.flush_outbox``; an offline CodeHub leaves it queued.

CLI: ``python -m backend.core.skilldep_materials.public_docs run|status|write-specs``.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import time
import zipfile
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import requests
from filelock import FileLock, Timeout

from .download import USER_AGENT, sha256_file

DEFAULT_ROOT = Path('F:/CodeYun/materials/m2610skilldependency/staging/public-documents-20261021')
SPEC_DIR = Path('F:/CodeYun/materials/m2610skilldependency/orchestration/public-docs-specs')
MANIFEST_SCHEMA = 'codeyun.skilldep_public_docs/v1'
MiB = 1024 ** 2
FILE_CAP, BATCH_CAP, MIN_FREE = 64 * MiB, 100 * MiB, 5 * 1024 ** 3
GAP, BACKOFF, MAX_BACKOFF = 3.0, 60.0, 6 * 3600.0
# Pages that are no document body: verification walls, error shells, rate-limit notices.
SHELL_TEXT = ('验证码', '安全验证', '人机验证', 'captcha', '访问过于频繁', '请开启JavaScript', '404 Not Found')
OLE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
MAGIC = {'pdf': (b'%PDF-',), 'zip': (b'PK\x03\x04',), 'docx': (b'PK\x03\x04',), 'xlsx': (b'PK\x03\x04',),
         'ofd': (b'PK\x03\x04',), 'doc': (OLE,), 'xls': (OLE,), 'wps': (OLE,)}
ZIP_FORMATS = ('zip', 'docx', 'xlsx', 'ofd')
# Package parts a ZIP-family attachment must contain (OPC content types + main part; OFD entry file).
PACKAGE_PARTS = {'docx': ('[Content_Types].xml', 'word/document.xml'),
                 'xlsx': ('[Content_Types].xml', 'xl/workbook.xml'), 'ofd': ('OFD.xml',)}
PDF_TAIL = 4096
# A directory/search page is mostly link text; a document body is mostly prose.
MAX_LINK_RATIO = 0.5


class PublicDocsError(RuntimeError):
    pass


@dataclass(frozen=True)
class PartSpec:
    """One explicitly verified URL. ``kind='html'`` takes the ``selector`` body text and
    asserts ``required_text``/``min_chars``; ``kind='attachment'`` checks ``format`` magic."""
    key: str
    url: str
    kind: str = 'html'
    selector: str | None = None
    encoding: str = 'utf-8'
    required_text: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()
    min_chars: int = 200
    format: str | None = None
    min_bytes: int = 1
    items: int = 1
    label: str = ''


@dataclass(frozen=True)
class DocumentSpec:
    """A material's parts; ``scope_complete`` says whether all parts together are the whole
    material (else the record stays ``partial`` with ``note``). ``hold_reason`` blocks runs."""
    material_id: str
    name: str
    source_url: str
    allowed_hosts: tuple[str, ...]
    parts: tuple[PartSpec, ...]
    scope_complete: bool = True
    note: str = ''
    hold_reason: str | None = None

    def __post_init__(self):
        keys = [p.key for p in self.parts]
        if not self.parts or len(set(keys)) != len(keys):
            raise PublicDocsError(f'{self.material_id}: parts 为空或 key 重复')
        for part in self.parts:
            if part.kind not in ('html', 'attachment'):
                raise PublicDocsError(f'{part.key}: kind 只能是 html/attachment')
            if part.kind == 'html' and not part.selector:
                raise PublicDocsError(f'{part.key}: html 正文必须给 selector')
            if part.kind == 'attachment' and part.format not in MAGIC:
                raise PublicDocsError(f'{part.key}: 附件 format 必须是 {sorted(MAGIC)}')
            if not part.key.replace('-', '').replace('_', '').isalnum():
                raise PublicDocsError(f'{part.key}: key 只能含字母数字 - _')
            if host_of(part.url) not in self.allowed_hosts:
                raise PublicDocsError(f'{part.key}: {part.url} 不在 allowed_hosts')

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> 'DocumentSpec':
        parts = tuple(PartSpec(**{k: tuple(v) if isinstance(v, list) else v for k, v in p.items()})
                      for p in data['parts'])
        return cls(**{**data, 'parts': parts, 'allowed_hosts': tuple(data['allowed_hosts'])})

    def sha256(self) -> str:
        return _sha(json.dumps(self.to_json(), ensure_ascii=False, sort_keys=True).encode('utf-8'))


def check_key(part: PartSpec) -> str:
    """Identity of a part's URL and verification constraints (``label``/``items`` are metadata)."""
    data = {k: v for k, v in asdict(part).items() if k not in ('label', 'items')}
    return _sha(json.dumps(data, ensure_ascii=False, sort_keys=True).encode('utf-8'))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or '').lower()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def body_text(part: PartSpec, raw: bytes) -> tuple[str, str | None]:
    """The selected body text of an HTML part and why it is no document body (None if it is)."""
    from bs4 import BeautifulSoup

    try:
        html = raw.decode(part.encoding)
    except UnicodeDecodeError as exc:
        return '', f'不是 {part.encoding} 文本：{exc.reason}'
    node = BeautifulSoup(html, 'html.parser').select_one(part.selector)
    if node is None:
        return '', f'选择器 {part.selector} 未命中'
    for junk in node.select('script, style'):
        junk.decompose()
    text = '\n'.join(line.strip() for line in node.get_text('\n').splitlines() if line.strip())
    chars = len(''.join(text.split()))
    if chars < part.min_chars:
        return text, f'正文仅 {chars} 字，少于 {part.min_chars}'
    links = len(''.join(''.join(a.get_text().split()) for a in node.find_all('a')))
    if links > MAX_LINK_RATIO * chars:
        return text, f'链接文字占 {links}/{chars}，像目录/搜索页而非正文'
    missing = [t for t in part.required_text if t not in text]
    if missing:
        return text, f'缺少必需文字：{missing}'
    shell = [t for t in part.forbidden_text + SHELL_TEXT if t in text]
    if shell:
        return text, f'含禁止文字（验证/错误壳）：{shell}'
    return text, None


def _zip_problem(fmt: str, raw: bytes) -> str | None:
    """A ZIP-family attachment must be a readable archive (CRC-checked) holding its package parts."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = set(z.namelist())
            bad = z.testzip()
    except (zipfile.BadZipFile, zlib.error, OSError, EOFError, RuntimeError, NotImplementedError) as exc:
        return f'附件不是有效 ZIP（{type(exc).__name__}）'
    if bad is not None:
        return f'附件 ZIP 成员 {bad} 校验失败'
    missing = [n for n in PACKAGE_PARTS.get(fmt, ()) if n not in names]
    return f'附件缺少 {fmt} 包结构：{missing}' if missing else None


def attachment_problem(part: PartSpec, raw: bytes, content_type: str) -> str | None:
    """Judge the bytes, not the label: a wrong ``text/html`` Content-Type is tolerated only when the
    bytes pass the strong check of their format (ZIP/OOXML structure, PDF with ``%%EOF``, OLE header)."""
    if len(raw) < part.min_bytes:
        return f'附件仅 {len(raw)} 字节，少于 {part.min_bytes}'
    html_label = 'html' in content_type.lower()
    if raw.lstrip(b'\xef\xbb\xbf \t\r\n')[:15].lower().startswith((b'<!doctype', b'<html')):
        return f'附件返回的是 HTML（{content_type}），不是 {part.format}'
    if not raw.startswith(MAGIC[part.format]):
        problem = f'附件魔数 {raw[:8]!r} 不是 {part.format}'
    elif part.format in ZIP_FORMATS:
        problem = _zip_problem(part.format, raw)
    elif part.format == 'pdf' and html_label and b'%%EOF' not in raw[-PDF_TAIL:]:
        problem = '附件 PDF 末尾缺少 %%EOF'
    else:
        problem = None
    if problem and html_label:
        return f'附件返回的是 HTML（{content_type}），不是 {part.format}：{problem}'
    return problem


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec='seconds')


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _dump(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True).encode('utf-8')


def paths(root: Path, material_id: str) -> dict[str, Path]:
    d = Path(root) / material_id
    return {'dir': d, 'parts': d / 'parts', 'manifests': d / 'manifests', 'state': d / 'state.json',
            'lock': Path(root) / f'{material_id}.lock'}


def load_state(p: dict[str, Path]) -> dict[str, Any]:
    if p['state'].is_file():
        return json.loads(p['state'].read_text(encoding='utf-8'))
    return {'parts': {}, 'failures': 0, 'retry_at': 0.0, 'next_request_at': 0.0, 'runs': []}


def retry_after_seconds(value: str | None, now: float) -> float | None:
    """``Retry-After`` as seconds from ``now`` (delta-seconds or HTTP date); None when absent/invalid."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - now)
    except (TypeError, ValueError):
        return None


def fetch(session: requests.Session, url: str, allowed_hosts: tuple[str, ...], cap: int,
          timeout: tuple[float, float] = (10, 60)) -> dict[str, Any]:
    """GET one URL (streamed, at most ``cap`` bytes); every redirect hop must stay on ``allowed_hosts``."""
    with session.get(url, stream=True, timeout=timeout, headers={'User-Agent': USER_AGENT},
                     allow_redirects=True) as response:
        hops = [r.url for r in response.history] + [response.url]
        foreign = [h for h in hops if host_of(h) not in allowed_hosts]
        if foreign:
            raise PublicDocsError(f'重定向离开允许域名：{foreign}')
        body = bytearray()
        if response.status_code == 200:
            for chunk in response.iter_content(256 * 1024):
                body += chunk
                if len(body) > cap:
                    raise PublicDocsError(f'{url} 超过单文件上限 {cap} 字节')
        headers = response.headers
        return {'status': response.status_code, 'final_url': response.url, 'body': bytes(body),
                'content_type': headers.get('Content-Type', ''), 'etag': headers.get('ETag'),
                'last_modified': headers.get('Last-Modified'), 'retry_after': headers.get('Retry-After'),
                'redirected_from': hops[0] if len(hops) > 1 else None}


def _backoff(state: dict[str, Any], now: float, retry_after: float | None, reason: str) -> None:
    """Persisted pause: ``Retry-After`` when given, else exponential from ``BACKOFF``."""
    state['failures'] = int(state.get('failures', 0)) + 1
    wait = retry_after if retry_after is not None else BACKOFF * 2 ** (state['failures'] - 1)
    state['retry_at'] = now + min(MAX_BACKOFF, wait)
    state['last_error'] = reason


def _read_if(path: Path, data_sha: str) -> bytes | None:
    if not path.is_file():
        return None
    data = path.read_bytes()
    return data if _sha(data) == data_sha else None


def _put(p: dict[str, Path], key: str, data: bytes, ext: str) -> str:
    """Store ``data`` content addressed (``parts/<key>/<sha>.<ext>``); never changes other bytes."""
    rel = f'parts/{key}/{_sha(data)}.{ext}'
    if _read_if(p['dir'] / rel, _sha(data)) is None:  # absent, or a torn write of these same bytes
        _write_atomic(p['dir'] / rel, data)
    return rel


def _raw_path(entry: dict[str, Any], key: str) -> str:
    return entry.get('path') or f'parts/{key}.raw'  # entries without ``path`` predate content addressing


def _cached(p: dict[str, Path], entry: dict[str, Any] | None, part: PartSpec) -> bytes | None:
    """The stored bytes of a part when they are still the ones fetched from ``part.url``."""
    if not entry or entry.get('url') != part.url or not entry.get('sha256'):
        return None
    raw_path = p['dir'] / _raw_path(entry, part.key)
    if not raw_path.is_file() or raw_path.stat().st_size != entry.get('bytes'):
        return None
    return _read_if(raw_path, entry['sha256'])


def _verify(p: dict[str, Path], part: PartSpec, raw: bytes, entry: dict[str, Any]) -> None:
    """Check stored bytes against the part's assertions; stores the body text of a valid HTML part.

    An earlier text file is reused only when it holds exactly this text; otherwise the text gets
    its own content-addressed file, so text referenced by older manifests never changes."""
    previous = [entry.get('text_path')] + ([] if entry.get('path') else [f'parts/{part.key}.txt'])
    entry.update(verified=False, check=check_key(part), text_sha256=None, text_chars=None, text_path=None,
                 format=part.format or 'html')
    if part.kind == 'attachment':
        entry['error'] = attachment_problem(part, raw, entry.get('content_type') or '')
    else:
        ctype = (entry.get('content_type') or '').lower()
        text, problem = ('', f'Content-Type {ctype} 不是 HTML') if ctype and 'html' not in ctype \
            else body_text(part, raw)
        if problem is None:
            data = text.encode('utf-8')
            rel = next((r for r in previous if r and _read_if(p['dir'] / r, _sha(data)) is not None), None)
            entry.update(text_path=rel or _put(p, part.key, data, 'txt'), text_sha256=_sha(data),
                         text_chars=len(''.join(text.split())), text_bytes=len(data))
        entry['error'] = problem
    entry['verified'] = entry['error'] is None


def _current(entry: dict[str, Any] | None, part: PartSpec) -> bool:
    """Verified for exactly this part's URL and checks (not for an earlier spec of the same key)."""
    return bool(entry and entry.get('verified') and entry.get('url') == part.url
                and entry.get('check') == check_key(part))


def _revalidate_offline(p: dict[str, Path], part: PartSpec, entry: dict[str, Any] | None) -> None:
    """A part the run did not fetch: re-check its cached bytes offline, else drop its verified flag."""
    raw = _cached(p, entry, part)
    if raw is not None:
        _verify(p, part, raw, entry)
    elif entry and entry.get('verified'):
        entry.update(verified=False, error='缓存与当前 spec 的 URL 不符或文件缺失，待重新下载')


def build_manifest(spec: DocumentSpec, state: dict[str, Any], p: dict[str, Path]) -> dict[str, Any] | None:
    """Write the immutable, content-addressed manifest of the verified parts (None if none verified)."""
    files, missing = [], []
    for part in spec.parts:
        e = state['parts'].get(part.key) or {}
        if not _current(e, part):
            stale = e.get('verified') and '已核验数据属于旧 spec，待按当前 URL/校验重新核验'
            missing.append({'key': part.key, 'url': part.url, 'kind': part.kind, 'label': part.label,
                            'error': stale or e.get('error') or '未下载'})
            continue
        f = {'key': part.key, 'label': part.label, 'kind': part.kind, 'url': part.url,
             'final_url': e['final_url'], 'path': _raw_path(e, part.key), 'bytes': e['bytes'],
             'sha256': e['sha256'], 'format': e['format'], 'content_type': e.get('content_type'),
             'http_status': e.get('http_status'), 'fetched_at': e.get('fetched_at'), 'items': part.items,
             'selector': part.selector, 'required_text': list(part.required_text)}
        if part.kind == 'html':
            f.update(text_path=e['text_path'], text_sha256=e['text_sha256'], text_chars=e['text_chars'],
                     text_bytes=e['text_bytes'])
        files.append(f)
    if not files:
        return None
    complete = spec.scope_complete and not missing
    man = {'schema': MANIFEST_SCHEMA, 'material_id': spec.material_id, 'name': spec.name,
           'source_url': spec.source_url, 'spec_sha256': spec.sha256(), 'scope_complete': spec.scope_complete,
           'complete': complete, 'status': 'downloaded' if complete else 'partial', 'note': spec.note,
           'files': files, 'missing': missing, 'items': sum(f['items'] for f in files),
           'payload_bytes': sum(f['bytes'] + f.get('text_bytes', 0) for f in files)}
    body = _dump(man)
    sha = _sha(body)
    path = p['manifests'] / f'{sha}.json'
    if path.is_file():
        if sha256_file(path) != sha:
            raise PublicDocsError(f'不可变 manifest 已被改动：{path}')
    else:
        _write_atomic(path, body)
    return {**man, 'manifest_path': path.as_posix(), 'manifest_sha256': sha}


def run(spec: DocumentSpec, root: Path = DEFAULT_ROOT, *, session: requests.Session | None = None,
        clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
        refetch: bool = False, allow_hold: bool = False, gap: float = GAP, file_cap: int = FILE_CAP,
        batch_cap: int = BATCH_CAP, min_free: int = MIN_FREE) -> dict[str, Any]:
    """Fetch (or reuse) every part serially, verify it and write the manifest. Never publishes.

    Returns ``outcome`` ``busy`` (another process holds the material), ``paused`` (persisted
    ``retry_at`` not reached, or a new backoff), ``disk_low``, ``batch_cap`` or ``done``, plus
    the manifest (when any part is verified) and per-part results.
    """
    if spec.hold_reason and not allow_hold:
        raise PublicDocsError(f'{spec.material_id} 暂停执行：{spec.hold_reason}')
    p = paths(root, spec.material_id)
    p['dir'].mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(p['lock']))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return {'material_id': spec.material_id, 'outcome': 'busy'}
    try:
        return _run_locked(spec, p, session or requests.Session(), clock, sleep, refetch, gap, file_cap,
                           batch_cap, min_free)
    finally:
        lock.release()


def _run_locked(spec, p, session, clock, sleep, refetch, gap, file_cap, batch_cap, min_free) -> dict[str, Any]:
    state = load_state(p)
    started, outcome, fetched, fetched_bytes = clock(), 'done', [], 0

    def save() -> None:
        _write_atomic(p['state'], _dump(state))

    for part in spec.parts:
        entry = state['parts'].get(part.key)
        if outcome != 'done':  # stopped early: no more requests, but nothing stale may stay verified
            _revalidate_offline(p, part, entry)
            continue
        raw = None if refetch else _cached(p, entry, part)
        if raw is None:
            if clock() < float(state.get('retry_at') or 0):
                outcome = 'paused'
            elif shutil.disk_usage(p['dir']).free < min_free:
                outcome = 'disk_low'
            elif fetched_bytes >= batch_cap:
                outcome = 'batch_cap'
            if outcome != 'done':
                _revalidate_offline(p, part, entry)
                continue
            wait = float(state.get('next_request_at') or 0) - clock()
            if wait > 0:
                sleep(wait)
            try:
                got = fetch(session, part.url, spec.allowed_hosts, min(file_cap, batch_cap - fetched_bytes))
            except requests.RequestException as exc:
                _backoff(state, clock(), None, f'{part.key}: {type(exc).__name__}')
                outcome = 'paused'
                _revalidate_offline(p, part, entry)
                continue
            except PublicDocsError as exc:
                got = {'status': None, 'error': str(exc)}
            finally:
                state['next_request_at'] = clock() + gap
            if got['status'] in (403, 429) or (got['status'] or 0) >= 500:
                _backoff(state, clock(), retry_after_seconds(got.get('retry_after'), clock()),
                         f"{part.key}: HTTP {got['status']}")
                outcome = 'paused'
                _revalidate_offline(p, part, entry)
                save()
                continue
            if got['status'] != 200:
                state['parts'][part.key] = {'url': part.url, 'verified': False, 'http_status': got['status'],
                                            'error': got.get('error') or f"HTTP {got['status']}",
                                            'fetched_at': _iso(clock())}
                save()
                continue
            raw = got['body']
            entry = {'url': part.url, 'path': _put(p, part.key, raw, 'raw'), 'final_url': got['final_url'], 'redirected_from': got['redirected_from'],
                     'http_status': 200, 'content_type': got['content_type'], 'etag': got['etag'],
                     'last_modified': got['last_modified'], 'bytes': len(raw), 'sha256': _sha(raw),
                     'fetched_at': _iso(clock())}
            state['parts'][part.key] = entry
            state['failures'], state['retry_at'] = 0, 0.0
            fetched.append(part.key)
            fetched_bytes += len(raw)
        _verify(p, part, raw, entry)
        save()
    man = build_manifest(spec, state, p)
    summary = {'started_at': _iso(started), 'finished_at': _iso(clock()), 'outcome': outcome,
               'spec_sha256': spec.sha256(), 'fetched': fetched,
               'fetched_bytes': fetched_bytes, 'manifest_sha256': man and man['manifest_sha256'],
               'status': man and man['status']}
    state['runs'] = (state.get('runs') or [])[-19:] + [summary]
    save()
    parts = {k: {x: e.get(x) for x in ('verified', 'error', 'bytes', 'sha256', 'text_chars', 'http_status')}
             for k, e in state['parts'].items()}
    return {'material_id': spec.material_id, **summary, 'retry_at': state.get('retry_at') or None,
            'manifest': man, 'parts': parts}


def record_for(spec: DocumentSpec, man: dict[str, Any], directory: Path, finished_at: str) -> dict[str, Any]:
    """The acquisition record of a manifest; ``scope`` holds only items/pages/page_limit/note.

    CodeHub takes ``pages`` as integers: the 1-based positions of the verified parts in the spec."""
    done = [f['key'] for f in man['files']]
    order = [part.key for part in spec.parts]
    gaps = '；'.join(f"{m['key']}: {m['error']}" for m in man['missing'])
    note = (f"{spec.name}：{len(done)}/{len(spec.parts)} 部分已核验（{', '.join(done)}）"
            + (f"；缺：{gaps}" if gaps else '') + (f'；{spec.note}' if spec.note else '')
            + ('' if man['complete'] else '；非完整素材'))
    return {'status': man['status'], 'availability': 'windows_local', 'path': directory.as_posix(),
            'size_bytes': man['payload_bytes'], 'manifest_path': man['manifest_path'],
            'manifest_sha256': man['manifest_sha256'], 'source_url': spec.source_url, 'finished_at': finished_at,
            'scope': {'items': man['items'], 'pages': [order.index(k) + 1 for k in done], 'page_limit': len(spec.parts), 'note': note[:1000]}}


def manifest_problem(spec: DocumentSpec, p: dict[str, Path], sha: str) -> str | None:
    """Why manifest ``sha`` cannot be published for ``spec`` (None when it can): it must be intact,
    made from exactly this spec, and every file it references must still hold its hash."""
    path = p['manifests'] / f'{sha}.json'
    if not path.is_file() or sha256_file(path) != sha:
        return f'manifest {sha} 缺失或已被改动，不登记'
    man = json.loads(path.read_text(encoding='utf-8'))
    if man.get('material_id') != spec.material_id or man.get('spec_sha256') != spec.sha256():
        return f"最新 manifest 属于另一 spec（{man.get('spec_sha256')}），当前 spec 尚无核验结果，不登记"
    for f in man['files']:
        for rel, want in ((f['path'], f['sha256']), (f.get('text_path'), f.get('text_sha256'))):
            if rel and (not (p['dir'] / rel).is_file() or sha256_file(p['dir'] / rel) != want):
                return f'manifest 引用的 {rel} 缺失或哈希不符，不登记'
    return None


def publish(spec: DocumentSpec, root: Path = DEFAULT_ROOT, *, store=None, **flush_options) -> dict[str, Any]:
    """Save the latest manifest's record locally (``record_local``) unless already recorded, then
    run a bounded ``flush_outbox`` for this material. Network failures leave it in the outbox."""
    from .catalog import CatalogStore
    from .local_sync import flush_outbox

    p = paths(root, spec.material_id)
    run_ = next((r for r in reversed(load_state(p).get('runs') or []) if r.get('manifest_sha256')), None)
    if run_ is None:
        return {'material_id': spec.material_id, 'local_saved': False, 'remote_registered': False,
                'reason': '没有已核验部分，不登记'}
    problem = manifest_problem(spec, p, run_['manifest_sha256'])
    if problem:
        return {'material_id': spec.material_id, 'local_saved': False, 'remote_registered': False,
                'reason': problem}
    path = p['manifests'] / f"{run_['manifest_sha256']}.json"
    man = {**json.loads(path.read_text(encoding='utf-8')), 'manifest_path': path.as_posix(),
           'manifest_sha256': run_['manifest_sha256']}
    store = store or CatalogStore()
    local = (store.load()['items'].get(spec.material_id) or {}).get('local') or {}
    record, saved = record_for(spec, man, p['dir'], run_['finished_at']), None
    if any(local.get(k) != record[k] for k in ('status', 'manifest_sha256', 'scope')):
        saved = store.record_local(spec.material_id, record)
    flushed = flush_outbox(store, [spec.material_id], **flush_options)
    item = store.load()['items'][spec.material_id]
    pub = item.get('publication') or {}
    return {'material_id': spec.material_id, 'local_saved': True, 'newly_saved': saved is not None,
            'remote_registered': bool(pub.get('remote_registered')), 'publication': pub,
            'local': item.get('local'), 'sync': flushed}



SAMR_AD_GUIDE = DocumentSpec(
    material_id='sd-18af9e8c5e5c', name='广告绝对化用语执法指南',
    source_url='https://www.samr.gov.cn/ggjgs/tzgg/art/2023/art_183b5cb48d9e4f0dba67f9f912a913ba.html',
    allowed_hosts=('www.samr.gov.cn',),
    parts=(PartSpec(key='guide', label='市场监管总局公告2023年第6号 + 指南正文',
                    url='https://www.samr.gov.cn/ggjgs/tzgg/art/2023/art_183b5cb48d9e4f0dba67f9f912a913ba.html',
                    selector='div.zt_xilan_07', min_chars=1500, items=13,
                    required_text=('2023年第6号', '广告绝对化用语执法指南', '制定本指南', '一、本指南旨在',
                                   '六、有下列情形之一的', '十二、市场监管部门可以依照有关规定')),),
    note='13 条=序言+第一至第十二条，另含公告头；原计划 URL（wxlx.gov.cn）404，改用市场监管总局广告监管司原文页；页面无附件')

_GOV_YEARS = (  # (year, url, approx body chars) from source-gap-repairs.json verified_full_text_versions
    (2015, 'https://www.gov.cn/zhengce/zhengceku/2015-04/10/content_9588.htm', 17009),
    (2016, 'https://www.gov.cn/zhengce/content/2016-03/29/content_5059540.htm', 16286),
    (2017, 'https://www.gov.cn/gongbao/content/2017/content_5186974.htm', 17524),
    (2018, 'https://www.gov.cn/zhengce/zhengceku/2018-04/12/content_5281920.htm', 17697),
    (2020, 'https://www.gov.cn/zhengce/content/2020-06/11/content_5518699.htm', 13778),
    (2021, 'https://www.gov.cn/gongbao/content/2021/content_5598115.htm', 19650),
    (2022, 'https://www.gov.cn/zhengce/content/2022-03/25/content_5681343.htm', 21944),
)
GOV_WORK_REPORT_DIVISION = DocumentSpec(
    material_id='sd-2df4c1da864b', name='国务院落实《政府工作报告》重点工作分工的意见（历年）',
    source_url='https://www.gov.cn/zhengce/zhengceku/2022-03/25/content_5681343.htm',
    allowed_hosts=('www.gov.cn',),
    parts=tuple(PartSpec(key=f'y{year}', label=f'{year} 年意见全文', url=url, selector='#UCAP-CONTENT',
                         min_chars=int(chars * 0.7), required_text=('政府工作报告', '分工'))
                for year, url, chars in _GOV_YEARS),
    scope_complete=False,
    note='仅 2015-2018、2020-2022 共 7 年意见全文；2019/2014 未找到，2013 旧链接 404，'
         '2023-2026 仅会议报道无公开全文；不得标为历年完成',
    hold_reason='旧 2022 owner b5a3 仍在运行；本 spec 仅准备，未授权执行或发布')
SPECS = {s.material_id: s for s in (SAMR_AD_GUIDE, GOV_WORK_REPORT_DIVISION)}


def resolve_spec(material_id: str | None, spec_path: Path | None) -> DocumentSpec:
    if spec_path:
        return DocumentSpec.from_json(json.loads(Path(spec_path).read_text(encoding='utf-8')))
    if material_id not in SPECS:
        raise PublicDocsError(f'没有内置 spec：{material_id}（可用 {sorted(SPECS)} 或 --spec）')
    return SPECS[material_id]


def write_specs(directory: Path = SPEC_DIR) -> list[str]:
    """Write the built-in specs as JSON (spec files only; nothing is fetched or published)."""
    out = []
    for spec in SPECS.values():
        path = Path(directory) / f'{spec.material_id}.json'
        _write_atomic(path, _dump(spec.to_json()))
        out.append(path.as_posix())
    return out


def _print(obj: Any) -> None:
    import sys

    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False, indent=1).encode('utf-8') + b'\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m backend.core.skilldep_materials.public_docs',
                                     description='显式已核验公开文档：正文/附件校验、F 盘断点、不可变 manifest、本地优先同步')
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest='cmd', required=True)
    for name in ('run', 'status'):
        sp = sub.add_parser(name)
        sp.add_argument('material_id', nargs='?')
        sp.add_argument('--spec', type=Path, default=None, help='DocumentSpec JSON 文件')
    p_run = sub.choices['run']
    p_run.add_argument('--refetch', action='store_true', help='忽略已校验缓存重新下载')
    p_run.add_argument('--no-publish', action='store_true', help='只下载校验，不登记总表/CodeHub')
    p_run.add_argument('--allow-hold', action='store_true', help='执行带 hold_reason 的 spec')
    p_run.add_argument('--timeout', type=float, default=10.0)
    p_run.add_argument('--retries', type=int, default=2)
    p_run.add_argument('--budget-seconds', type=float, default=60.0)
    p_specs = sub.add_parser('write-specs', help='写出内置 spec JSON（不执行）')
    p_specs.add_argument('--dir', type=Path, default=SPEC_DIR)
    args = parser.parse_args(argv)

    if args.cmd == 'write-specs':
        _print({'written': write_specs(args.dir)})
        return 0
    spec = resolve_spec(args.material_id, args.spec)
    if args.cmd == 'status':
        state = load_state(paths(args.root, spec.material_id))
        _print({'material_id': spec.material_id, 'retry_at': state.get('retry_at') or None,
                'last_run': (state.get('runs') or [None])[-1],
                'parts': {k: {x: e.get(x) for x in ('verified', 'error', 'sha256')} for k, e in state['parts'].items()}})
        return 0
    result = run(spec, args.root, refetch=args.refetch, allow_hold=args.allow_hold)
    if result.get('outcome') != 'busy' and not args.no_publish:
        result['publication'] = publish(spec, args.root, timeout=args.timeout, retries=args.retries,
                                        budget_seconds=args.budget_seconds)
    _print(result)
    return 0 if result.get('outcome') == 'done' else 1


if __name__ == '__main__':
    raise SystemExit(main())
