"""Resumable crawler for the Gitee issues list of SkillDep material sd-b70266a40bc8.

Keeps the v1 layout written by the first manual driver: ``<root>/<mid>/source-manifest.json``,
``cursor.json`` and ``pages/page-NNNNN.json``. A page only advances the cursor after its
bytes were written atomically and hashed; verified pages are never fetched again and a
corrupt page is the only one fetched again. Requests are serial with a persisted gap.
403/429/503 and a low anonymous quota persist ``retry_at`` in the cursor, so a restart
honours the pause. One process per source root (``FileLock``). The CodeHub record is
always ``partial`` (this repo's issue list only, no comments, no other repos/sources)
and points at an immutable manifest snapshot.

With ``queue_root`` (the CLI default: the materials catalog) every real progress is first
committed through ``CatalogStore.record_local`` (catalog + CSV + outbox, visible offline)
and then sent by a bounded, non-blocking ``local_sync.flush_outbox``; the catalog outbox
is the pending-sync authority and an older ``pending-sync.json`` entry is migrated into it.
Without ``queue_root`` the legacy direct sync is kept: a failed sync leaves the latest
record in ``pending-sync.json``. Neither path blocks local progress.

CLI: ``python -m backend.core.skilldep_materials.gitee_crawl --once|--run|status|stop|publish-current``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

import requests
from filelock import FileLock, Timeout

from .catalog import DEFAULT_QUEUE
from .download import USER_AGENT, sha256_file

MID = 'sd-b70266a40bc8'
DEFAULT_ROOT = Path('F:/CodeYun/materials/m2610skilldependency/staging/gitee-issues-20261020')
SOURCE_URL = 'https://gitee.com/api/v5/repos/openharmony/docs/issues?state=all&per_page=100'
API = SOURCE_URL + '&page={}'
GAP = 3.2
PAGES_PER_STEP = 20
STEP_SECONDS = 11 * 60
PAGE_CAP, BATCH_CAP, MIN_FREE = 6 * 1024 ** 2, 150 * 1024 ** 2, 5 * 1024 ** 3
BLOCK_STATUSES = (403, 429, 503)
BLOCK_PAUSE = 3600
# Gitee sends X-RateLimit-Remaining without a reset field; its window is not documented,
# so a nearly used quota pauses a conservative hour (a guess, not a proven unit).
QUOTA_FLOOR, QUOTA_PAUSE = 3, 3600
ERROR_PAUSE = 15 * 60
ATTEMPTS = 3
IDLE_SLICE = 30


class SourceBusy(RuntimeError):
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


def source_lock(root: Path) -> FileLock:
    return FileLock(str(Path(root) / '.crawl.lock'))


def _paths(root: Path) -> dict[str, Path]:
    d = Path(root) / MID
    return {'dir': d, 'manifest': d / 'source-manifest.json', 'cursor': d / 'cursor.json',
            'outbox': Path(root) / 'pending-sync.json', 'worker': Path(root) / 'worker.json',
            'stop': Path(root) / 'STOP', 'log': Path(root) / 'crawl-log.jsonl'}


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


def pause_for(status: int, headers: dict[str, str], now: float) -> tuple[float, str] | None:
    """Pause deadline and reason for a blocked response or a nearly used quota."""
    h = {k.lower(): v for k, v in headers.items()}
    if status in BLOCK_STATUSES:
        secs = retry_after_seconds(h.get('retry-after'), now)
        how = 'Retry-After' if secs is not None else f'no Retry-After, default {BLOCK_PAUSE}s'
        return now + (BLOCK_PAUSE if secs is None else secs), f'HTTP {status} ({how})'
    remaining = h.get('x-ratelimit-remaining', '')
    if status == 200 and remaining.lstrip('-').isdigit() and int(remaining) <= QUOTA_FLOOR:
        reset = h.get('x-ratelimit-reset', '')
        if reset.isdigit():
            at = float(reset) if int(reset) > 1e9 else now + float(reset)
            return at, f'quota remaining {remaining}, X-RateLimit-Reset'
        return now + QUOTA_PAUSE, f'quota remaining {remaining}, no reset header: conservative {QUOTA_PAUSE}s'
    return None


def _ids(body: bytes) -> list:
    data = json.loads(body)
    if not isinstance(data, list) or any(not isinstance(x, dict) or 'id' not in x for x in data):
        raise ValueError('not an issues JSON list')
    return [x['id'] for x in data]


def _new_manifest() -> dict[str, Any]:
    return {'schema': 'codeyun.skilldep.gitee-issues/1', 'material_id': MID,
            'name': 'Gitee 开源项目工单（OpenHarmony、MindSpore）', 'plan_url': SOURCE_URL,
            'repo': 'openharmony/docs', 'per_page': 100, 'content': 'issues list JSON only (no comments)',
            'pages': {}}


def _save_state(p: dict[str, Path], man: dict, cur: dict, now: float) -> None:
    ids: set = set()
    for page in man['pages'].values():
        ids.update(page['item_ids'])
    pages = sorted(int(k) for k in man['pages'])
    man.update(pages_done=pages, distinct_issues=len(ids),
               contiguous_through=next(i - 1 for i in range(1, len(pages) + 2) if i not in pages),
               payload_bytes=sum(x['bytes'] for x in man['pages'].values()), updated_at=_iso(now))
    _wj(p['manifest'], man)
    _wj(p['cursor'], cur)


def verify_pages(root: Path, man: dict, *, full: bool = True) -> list[int]:
    """Drop manifest entries whose file is missing or differs; return the dropped pages."""
    bad = []
    for key, page in list(man['pages'].items()):
        f = Path(root) / page['path']
        ok = f.is_file() and f.stat().st_size == page['bytes'] and (not full or sha256_file(f) == page['sha256'])
        if not ok:
            man['pages'].pop(key)
            bad.append(int(key))
    return sorted(bad)


def _target(man: dict, cur: dict) -> int | None:
    """Lowest missing page below the cursor (repair), else the cursor page."""
    for n in range(1, cur['next_page']):
        if str(n) not in man['pages']:
            return n
    return None if cur.get('exhausted') else cur['next_page']


def _write_page(path: Path, body: bytes, sha: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + '.part')
    with part.open('wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    if sha256_file(part) != sha:
        part.unlink(missing_ok=True)
        raise OSError(f'hash mismatch after writing {path.name}')
    os.replace(part, path)


def _fetch(session, url: str) -> tuple[int, dict[str, str], bytes]:
    r = session.get(url, timeout=60, stream=True,
                    headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    chunks, size = [], 0
    for c in r.iter_content(256 * 1024):
        size += len(c)
        if size > PAGE_CAP:
            raise ValueError('page > 6MiB')
        chunks.append(c)
    headers = {k: v for k, v in r.headers.items() if k.lower() != 'set-cookie'}
    return r.status_code, headers, b''.join(chunks)


def snapshot(p: dict[str, Path], man: dict) -> tuple[Path, str]:
    """Write an immutable, content-addressed copy of the page table for registration."""
    keys = ('schema', 'material_id', 'name', 'plan_url', 'repo', 'per_page', 'content',
            'pages_done', 'distinct_issues', 'contiguous_through', 'payload_bytes')
    snap = {k: man.get(k) for k in keys}
    snap['pages'] = {k: {f: v[f] for f in ('page', 'path', 'bytes', 'sha256', 'items', 'fetch_url', 'fetched_at')}
                     for k, v in sorted(man['pages'].items(), key=lambda kv: int(kv[0]))}
    data = json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True).encode('utf-8')
    sha = hashlib.sha256(data).hexdigest()
    path = p['dir'] / 'snapshots' / f'manifest-{sha[:16]}.json'
    if not (path.is_file() and sha256_file(path) == sha):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return path, sha


def partial_record(p: dict[str, Path], man: dict, cur: dict, now: float) -> dict[str, Any]:
    path, sha = snapshot(p, man)
    pages = man['pages_done']
    total = (cur.get('last_headers') or {}).get('total_count')
    span = f'第 {pages[0]}-{pages[-1]} 页中 {len(pages)} 页' if pages else '0 页'
    return {'status': 'partial', 'availability': 'windows_local', 'source_url': SOURCE_URL,
            'path': p['dir'].as_posix(), 'size_bytes': man['payload_bytes'],
            'manifest_path': path.as_posix(), 'manifest_sha256': sha, 'finished_at': _iso(now),
            'scope': {'page_limit': len(pages), 'pages': pages, 'items': man['distinct_issues'],
                      'note': (f'仅 openharmony/docs issues 列表 JSON（per_page=100,state=all）{span}，'
                               f'{man["distinct_issues"]} 个去重工单；接口 total_count={total}；不含评论，'
                               '不含 mindspore 等其他仓库与其他来源；非完整素材')}}


def sync(root: Path, record: dict, registry_factory: Callable[[], Any] | None, now: float) -> dict[str, Any]:
    """Register the partial record on CodeHub; on failure keep only the latest in the outbox."""
    from .acquire import register_and_verify

    p = _paths(root)
    try:
        if registry_factory is None:
            from .codehub import CodeHubSkillDep
            registry_factory = CodeHubSkillDep
        reg = registry_factory()
        rec = {'device_id': reg.device_id, 'device_name': reg.device_name, **record}
        res = register_and_verify(reg, MID, rec)
    except Exception as exc:
        rec, res = record, {'registered': False, 'register_error': f'{type(exc).__name__}: {exc}'[:300]}
    box = _rj(p['outbox'], {'items': {}})
    if res.get('registered'):
        box['items'].pop(MID, None)
    else:
        box['items'][MID] = {'record': rec, 'error': res.get('register_error') or 'read-back mismatch',
                             'at': _iso(now)}
    _wj(p['outbox'], box)
    return {'registered': bool(res.get('registered')), 'error': res.get('register_error'),
            'manifest_path': record['manifest_path']}


def _legacy_pending(p: dict[str, Path]) -> bool:
    return MID in (_rj(p['outbox'], {'items': {}}) or {'items': {}})['items']


def publish(root: Path, record: dict, queue_root: Path, *, device_id: str | None = None,
            registry_factory: Callable[[], Any] | None = None, flush: bool = True) -> dict[str, Any]:
    """Commit ``record`` to the local catalog first (CSV + outbox), then one bounded CodeHub flush.

    A manifest the catalog already holds for this device is not recorded again. Once the
    catalog holds the record, a legacy ``pending-sync.json`` entry is dropped (migrated); when
    the local commit fails, the record stays there so the source never loses its pending state.
    ``registry_factory()`` takes no arguments (kept from the legacy sync).
    """
    from .catalog import CatalogStore
    from .local_sync import flush_outbox

    p = _paths(root)
    store = CatalogStore(queue_root, device_id=device_id)
    out = {'queue_root': str(queue_root), 'local_saved': False, 'remote_registered': False, 'pending': True,
           'manifest_path': record['manifest_path'], 'manifest_sha256': record['manifest_sha256']}
    try:
        local = (store.load()['items'].get(MID) or {}).get('local') or {}
        if local.get('manifest_sha256') == record['manifest_sha256'] and local.get('device_id') == store.device_id:
            out.update(local_saved=True, already_recorded=True)
        else:
            out.update(local_saved=True, outbox_seq=store.record_local(MID, record)['outbox_seq'])
    except Exception as exc:  # noqa: BLE001 - a missing catalog/identity/evidence is a recorded failure
        out['error'] = f'{type(exc).__name__}: {exc}'[:300]
        box = _rj(p['outbox'], {'items': {}}) or {'items': {}}
        box['items'][MID] = {'record': record, 'error': out['error'], 'at': _iso(time.time())}
        _wj(p['outbox'], box)
        return out
    if _legacy_pending(p):
        box = _rj(p['outbox'])
        box['items'].pop(MID, None)
        _wj(p['outbox'], box)
        out['migrated_legacy_outbox'] = True
    if flush:
        factory = None if registry_factory is None else (lambda **budget: registry_factory())
        try:
            res = flush_outbox(store, [MID], registry_factory=factory)
            out['sync'] = next((r for r in res['results'] if r['material_id'] == MID), None)
            out['busy'] = bool(res.get('busy'))
        except Exception as exc:  # noqa: BLE001 - the record stays queued in the catalog outbox
            out['sync'] = {'remote_registered': False, 'error': f'{type(exc).__name__}: {exc}'[:300]}
        out['error'] = (out['sync'] or {}).get('error')
    pending = MID in store.outbox()['items']
    pub = store.load()['items'][MID].get('publication')
    # No publication block: the catalog record came from a CodeHub snapshot, i.e. it is registered there.
    out.update(pending=pending, remote_registered=not pending and (pub is None or bool(pub.get('remote_registered'))))
    return out


def crawl_step(root: Path = DEFAULT_ROOT, *, session=None, registry_factory: Callable[[], Any] | None = None,
               max_pages: int = PAGES_PER_STEP, max_seconds: float = STEP_SECONDS, full_verify: bool = True,
               lock_held: bool = False, clock: Callable[[], float] = time.time,
               sleep: Callable[[float], None] = time.sleep, disk_free: Callable[[Path], int] | None = None,
               on_page: Callable[[dict], None] | None = None, queue_root: Path | None = None,
               device_id: str | None = None) -> dict[str, Any]:
    """Fetch up to ``max_pages`` pages from the persisted cursor and register a partial snapshot.

    Returns ``stop`` (why this step ended), ``new_pages``, ``repaired``, the cursor and the sync
    result. A step ending on its page/byte budget is not completion of the repo or material.
    With ``queue_root`` the snapshot goes through ``publish`` (local catalog first) every step.
    """
    root = Path(root)
    lock = None
    if not lock_held:
        lock = source_lock(root)
        try:
            lock.acquire(timeout=0)
        except Timeout:
            raise SourceBusy(f'{root} 已被另一个 crawler 持有') from None
    try:
        return _step(root, session or _session(), registry_factory, max_pages, max_seconds, full_verify,
                     clock, sleep, disk_free or (lambda r: shutil.disk_usage(r).free), on_page,
                     queue_root, device_id)
    finally:
        if lock:
            lock.release()


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # direct connection; the system proxy breaks TLS to gitee here
    return s


def _step(root, session, registry_factory, max_pages, max_seconds, full_verify, clock, sleep, disk_free, on_page,
          queue_root=None, device_id=None):
    p = _paths(root)
    t0 = clock()
    man = _rj(p['manifest']) or _new_manifest()
    cur = _rj(p['cursor']) or {'next_page': 1, 'exhausted': False, 'retry_at': None}
    repaired = verify_pages(root, man, full=full_verify)
    _save_state(p, man, cur, t0)
    new_pages, new_bytes, stop = [], 0, None
    retry_at = _ts(cur.get('retry_at'))
    if retry_at and retry_at > t0:
        stop = f"paused until {cur['retry_at']}: {cur.get('pause_reason')}"
    while not stop:
        n = _target(man, cur)
        if n is None:
            stop = 'exhausted'
            break
        if len(new_pages) >= max_pages:
            stop = f'page budget {max_pages}'
            break
        if clock() - t0 > max_seconds:
            stop = 'step time budget'
            break
        if p['stop'].exists():
            stop = 'stop requested'
            break
        if disk_free(root) < MIN_FREE:
            cur.update(retry_at=_iso(clock() + ERROR_PAUSE), pause_reason='disk free < 5GiB')
            stop = 'disk free < 5GiB'
            break
        if new_bytes + PAGE_CAP > BATCH_CAP:
            stop = 'batch cap 150MiB'
            break
        url, body, err, pause = API.format(n), None, None, None
        for attempt in range(ATTEMPTS):
            wait = GAP - (clock() - (_ts(cur.get('last_request_at')) or 0))
            if wait > 0:
                sleep(wait)
            cur['last_request_at'] = datetime.fromtimestamp(clock(), timezone.utc).isoformat()
            _wj(p['cursor'], cur)
            try:
                status, headers, raw = _fetch(session, url)
            except Exception as exc:
                err = f'{type(exc).__name__}: {exc}'[:300]
                sleep(5 * (attempt + 1))
                continue
            if status in BLOCK_STATUSES:
                pause = pause_for(status, headers, clock())
                cur['last_block'] = {'page': n, 'status': status, 'headers': headers, 'at': _iso(clock()),
                                     'body_head': raw[:300].decode('utf-8', 'replace')}
                break
            if status != 200:
                err = f'HTTP {status}'
                sleep(5 * (attempt + 1))
                continue
            try:
                ids = _ids(raw)
            except ValueError as exc:
                err = f'invalid JSON: {exc}'[:200]
                cur['last_invalid'] = {'page': n, 'head': raw[:200].decode('utf-8', 'replace')}
                sleep(5 * (attempt + 1))
                continue
            body = raw
            break
        if pause:
            cur.update(retry_at=_iso(pause[0]), pause_reason=pause[1])
            stop = f'{pause[1]}; retry_at {cur["retry_at"]}'
            break
        if body is None:
            cur.update(retry_at=_iso(clock() + ERROR_PAUSE), pause_reason=f'page {n} failed: {err}')
            stop = cur['pause_reason']
            break
        if not ids:
            if n != cur['next_page']:
                cur.update(retry_at=_iso(clock() + ERROR_PAUSE), pause_reason=f'repair page {n} came back empty')
                stop = cur['pause_reason']
                break
            cur.update(exhausted=True, exhausted_at_page=n)
            continue
        sha = hashlib.sha256(body).hexdigest()
        rel = f'{MID}/pages/page-{n:05d}.json'
        _write_page(root / rel, body, sha)
        man['pages'][str(n)] = {'page': n, 'path': rel, 'bytes': len(body), 'sha256': sha, 'items': len(ids),
                                'item_ids': ids, 'fetch_url': url, 'http_status': 200, 'headers': headers,
                                'fetched_at': _iso(clock()), 'reused_from': None}
        if n == cur['next_page']:
            cur['next_page'] = n + 1
        cur.update(retry_at=None, pause_reason=None, last_headers=headers)
        new_pages.append(n)
        new_bytes += len(body)
        quota = pause_for(200, headers, clock())
        if quota:
            cur.update(retry_at=_iso(quota[0]), pause_reason=quota[1])
            stop = f'{quota[1]}; retry_at {cur["retry_at"]}'
        _save_state(p, man, cur, clock())
        if on_page:
            on_page({'page': n, 'bytes': len(body)})
    _save_state(p, man, cur, clock())
    synced = None
    if queue_root is not None:
        if man['pages']:  # the catalog decides whether this snapshot is new; its outbox retries the rest
            synced = publish(root, partial_record(p, man, cur, clock()), Path(queue_root), device_id=device_id,
                             registry_factory=registry_factory)
    elif new_pages or repaired or _legacy_pending(p):
        synced = sync(root, partial_record(p, man, cur, clock()), registry_factory, clock())
    result = {'at': _iso(clock()), 'stop': stop, 'new_pages': new_pages, 'new_bytes': new_bytes,
              'repaired': repaired, 'pages_done': len(man['pages']), 'contiguous_through': man['contiguous_through'],
              'cursor': {k: cur.get(k) for k in ('next_page', 'exhausted', 'retry_at', 'pause_reason')},
              'sync': synced, 'complete': False}
    with p['log'].open('a', encoding='utf-8') as log:
        log.write(json.dumps(result, ensure_ascii=False) + '\n')
    return result


def _alive(info: dict | None) -> bool:
    import psutil

    if not info or not info.get('pid'):
        return False
    try:
        return abs(psutil.Process(info['pid']).create_time() - info.get('create_time', 0)) < 1
    except psutil.Error:
        return False


def publish_current(root: Path = DEFAULT_ROOT, queue_root: Path = DEFAULT_QUEUE, *, device_id: str | None = None,
                    registry_factory: Callable[[], Any] | None = None, flush: bool = True) -> dict[str, Any]:
    """Adopt the source's verified pages as they are now: immutable snapshot -> local catalog -> optional flush.

    Takes the source lock (``SourceBusy`` while a crawler runs), re-hashes every page and
    drops bad ones from the manifest first; fetches nothing from Gitee.
    """
    root = Path(root)
    lock = source_lock(root)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise SourceBusy(f'{root} 已被另一个 crawler 持有') from None
    try:
        p = _paths(root)
        man = _rj(p['manifest'])
        if not man or not man.get('pages'):
            raise ValueError(f"{p['manifest']} 没有已验证页面")
        cur = _rj(p['cursor']) or {'next_page': 1, 'exhausted': False, 'retry_at': None}
        repaired = verify_pages(root, man, full=True)
        _save_state(p, man, cur, time.time())
        if not man['pages']:
            raise ValueError('全部页面校验失败')
        out = publish(root, partial_record(p, man, cur, time.time()), Path(queue_root), device_id=device_id,
                      registry_factory=registry_factory, flush=flush)
        return {**out, 'pages_done': len(man['pages']), 'distinct_issues': man['distinct_issues'],
                'repaired': repaired, 'complete': False}
    finally:
        lock.release()


def _catalog_status(queue_root: Path) -> dict[str, Any]:
    """This material's catalog record and outbox entry (read only)."""
    from .catalog import CatalogStore

    try:
        store = CatalogStore(queue_root)
        item = store.load()['items'].get(MID) or {}
        entry = store.outbox()['items'].get(MID)
    except Exception as exc:  # noqa: BLE001 - status never fails on a missing catalog
        return {'queue_root': str(queue_root), 'error': f'{type(exc).__name__}: {exc}'[:300]}
    local, pub = item.get('local') or {}, item.get('publication')
    return {'queue_root': str(queue_root), 'local_status': local.get('status'),
            'local_manifest_sha256': local.get('manifest_sha256'),
            'local_pages': len((local.get('scope') or {}).get('pages') or []), 'local_saved': bool(local),
            'outbox_pending': entry is not None, 'outbox_seq': (entry or {}).get('seq'),
            'last_error': (entry or {}).get('last_error'),
            'remote_registered': entry is None and bool(local) and (pub is None or bool(pub.get('remote_registered')))}


def status(root: Path = DEFAULT_ROOT, queue_root: Path | None = None) -> dict[str, Any]:
    p = _paths(root)
    man = _rj(p['manifest'], {}) or {}
    worker = _rj(p['worker'])
    legacy = _legacy_pending(p)
    catalog = _catalog_status(Path(queue_root)) if queue_root is not None else None
    return {'root': str(root), 'material_id': MID, 'complete': False,
            'pages_done': len(man.get('pages', {})), 'contiguous_through': man.get('contiguous_through'),
            'distinct_issues': man.get('distinct_issues'), 'payload_bytes': man.get('payload_bytes'),
            'cursor': {k: (_rj(p['cursor'], {}) or {}).get(k)
                       for k in ('next_page', 'exhausted', 'retry_at', 'pause_reason', 'last_request_at')},
            'total_page': ((_rj(p['cursor'], {}) or {}).get('last_headers') or {}).get('total_page'),
            'worker': worker, 'worker_alive': _alive(worker), 'stop_requested': p['stop'].exists(),
            'outbox_pending': legacy or bool(catalog and catalog.get('outbox_pending')),
            'legacy_outbox_pending': legacy, 'catalog': catalog}


def request_stop(root: Path = DEFAULT_ROOT) -> Path:
    path = _paths(root)['stop']
    path.write_text(_iso(time.time()), encoding='utf-8')
    return path


def run_worker(root: Path = DEFAULT_ROOT, *, clock: Callable[[], float] = time.time,
               sleep: Callable[[float], None] = time.sleep, max_steps: int | None = None,
               **step_kwargs) -> dict[str, Any]:
    """Plain engineering loop: step, honour persisted pauses, heartbeat, exit on STOP or exhaustion."""
    import psutil

    root = Path(root)
    p = _paths(root)
    lock = source_lock(root)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise SourceBusy(f'{root} 已被另一个 crawler 持有') from None
    me = psutil.Process()
    info = {'pid': me.pid, 'create_time': me.create_time(), 'started_at': _iso(clock()), 'state': 'running',
            'heartbeat_at': None, 'pause_reason': None, 'retry_at': None, 'steps': 0, 'last_result': None,
            'last_error': None}

    def beat(**kv):
        info.update(kv, heartbeat_at=_iso(clock()))
        _wj(p['worker'], info)

    try:
        p['stop'].unlink(missing_ok=True)  # a STOP left from an earlier stop does not block a new start
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
                res = crawl_step(root, lock_held=True, clock=clock, sleep=sleep, full_verify=full,
                                 on_page=lambda pg: beat(state='fetching', last_page=pg['page']), **step_kwargs)
                full = False
                cur = res['cursor']
                beat(state='running', steps=info['steps'] + 1, last_result=res, last_error=None,
                     pause_reason=cur.get('pause_reason'), retry_at=cur.get('retry_at'))
            except Exception as exc:
                beat(steps=info['steps'] + 1, last_error=f'{type(exc).__name__}: {exc}'[:300],
                     pause_reason='step error', retry_at=_iso(clock() + ERROR_PAUSE))
                cur = {'retry_at': info['retry_at']}
            else:
                if res['stop'] == 'exhausted':
                    info['state'] = 'exhausted'
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
    parser = argparse.ArgumentParser(prog='python -m backend.core.skilldep_materials.gitee_crawl',
                                     description=f'Resumable Gitee issues crawler for {MID} (always partial).')
    parser.add_argument('command', nargs='?', choices=('status', 'stop', 'publish-current'),
                        help='publish-current: adopt the verified pages into the local catalog, then flush')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--once', action='store_true', help='one bounded step (<=20 pages, <=150MiB)')
    mode.add_argument('--run', action='store_true', help='continuous worker until STOP or exhaustion')
    parser.add_argument('--root', default=str(DEFAULT_ROOT))
    parser.add_argument('--max-pages', type=int, default=PAGES_PER_STEP)
    parser.add_argument('--queue-root', default=str(DEFAULT_QUEUE), help='local materials catalog (outbox authority)')
    parser.add_argument('--device-id', default=None, help='this LingNet device_id (default: resolved locally)')
    parser.add_argument('--no-flush', action='store_true', help='publish-current: local catalog only, no CodeHub')
    args = parser.parse_args(argv)
    root, queue = Path(args.root), Path(args.queue_root)
    code = 0
    if args.command == 'status':
        out = status(root, queue)
    elif args.command == 'stop':
        out = {'stop_file': str(request_stop(root))}
    elif args.command == 'publish-current':
        try:
            out = publish_current(root, queue, device_id=args.device_id, flush=not args.no_flush)
            code = 0 if out['local_saved'] else 1
        except (SourceBusy, ValueError) as exc:
            out, code = {'local_saved': False, 'error': f'{type(exc).__name__}: {exc}'}, 2
    elif args.once:
        out = crawl_step(root, max_pages=args.max_pages, queue_root=queue, device_id=args.device_id)
    elif args.run:
        out = run_worker(root, max_pages=args.max_pages, queue_root=queue, device_id=args.device_id)
    else:
        parser.error('choose --once, --run, status, stop or publish-current')
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
