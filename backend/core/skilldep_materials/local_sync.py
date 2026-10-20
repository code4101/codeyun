"""Publish locally recorded SkillDep progress to CodeHub, local commit first.

``publish_local`` saves the record through ``CatalogStore.record_local`` (verified
evidence, catalog + CSV, outbox) and only then tries CodeHub; ``flush_outbox``
retries every queued record later. Each record goes through the public CodeHub
``register`` + ``acquisitions`` read-back (``acquire.register_and_verify``);
only a matching read-back acknowledges it, so ``remote_registered`` never comes
from a local guess. Network work runs outside the catalog lock with a short,
bounded budget (per-request ``timeout``/``retries`` and a total ``budget_seconds``).
Failures (offline, HTTP 409, read-back mismatch, identity mismatch) keep the
outbox entry with its error; an acknowledgement only clears the exact entry that
was sent, never a newer record queued meanwhile.

One publisher per queue: ``flush_outbox`` holds ``publish.lock`` (cross-process,
next to the catalog) for its whole network phase and re-reads each material's
current entry inside it, so PUTs of one material never overlap and a late old PUT
cannot land after a newer record was acknowledged. A busy lock skips the flush
(``busy``) and leaves every entry queued; ``record_local`` never waits for it.

CLI: ``python -m backend.core.skilldep_materials.local_sync publish|flush|outbox``.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock, Timeout

from .acquire import register_and_verify
from .catalog import DEFAULT_QUEUE, CatalogError, CatalogStore, add_record_arguments, record_from_args, write_json

DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 2
DEFAULT_BUDGET_SECONDS = 60.0


def codehub_registry(*, timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES):
    """The CodeHub client with a short request budget (no network until used)."""
    from .codehub import CodeHubSkillDep

    return CodeHubSkillDep(timeout=timeout, retries=retries, backoff=1)


def publisher_lock(store: CatalogStore) -> FileLock:
    """The cross-process lock every CodeHub publication of this queue runs under."""
    return FileLock(str(Path(store.root) / 'publish.lock'))


def flush_outbox(store: CatalogStore, material_ids: list[str] | None = None, *,
                 registry_factory: Callable[..., Any] | None = None, timeout: float = DEFAULT_TIMEOUT,
                 retries: int = DEFAULT_RETRIES, budget_seconds: float = DEFAULT_BUDGET_SECONDS,
                 limit: int | None = None, clock: Callable[[], float] = time.monotonic,
                 lock_timeout: float = 0) -> dict[str, Any]:
    """Register queued local records on CodeHub and verify each read-back; never raises on network errors.

    ``registry_factory(timeout=, retries=)`` returns an object with ``device_id``,
    ``device_name``, ``register`` and ``acquisitions`` (default: CodeHub client).
    Entries left when ``budget_seconds`` is spent are skipped untouched. When another
    publisher holds the lock past ``lock_timeout`` the result has ``busy`` and every
    entry stays queued (``skipped: busy``).
    """
    entries = store.pending_publications(material_ids, limit)
    results: list[dict[str, Any]] = []
    if not entries:
        return {'attempted': 0, 'registered': 0, 'results': results, 'pending': 0}
    lock = publisher_lock(store)
    try:
        lock.acquire(timeout=lock_timeout)
    except Timeout:
        return {'attempted': 0, 'registered': 0, 'busy': True, 'pending': len(store.pending_publications()),
                'results': [{'material_id': e['material_id'], 'seq': e['seq'], 'remote_registered': False,
                             'skipped': 'busy'} for e in entries]}
    try:
        _publish(store, [e['material_id'] for e in entries], registry_factory, timeout, retries,
                 budget_seconds, clock, results)
    finally:
        lock.release()
    return {'attempted': sum(1 for r in results if not r.get('skipped')),
            'registered': sum(1 for r in results if r['remote_registered']),
            'results': results, 'pending': len(store.pending_publications())}


def _publish(store, material_ids, registry_factory, timeout, retries, budget_seconds, clock, results) -> None:
    """Send each material's entry as it is now (under the publisher lock), oldest first."""
    try:
        registry = (registry_factory or codehub_registry)(timeout=timeout, retries=retries)
    except Exception as exc:  # noqa: BLE001 - a missing peer/config is a recorded sync failure
        registry, setup_error = None, f'{type(exc).__name__}: {exc}'[:500]
    start = clock()
    for entry in store.pending_publications(material_ids):  # re-read: a newer save replaces an older seq
        mid, seq, record = entry['material_id'], entry['seq'], dict(entry['record'])
        if registry is None:
            outcome = {'registered': False, 'register_error': setup_error}
        elif clock() - start > budget_seconds:
            results.append({'material_id': mid, 'seq': seq, 'remote_registered': False, 'skipped': 'budget'})
            continue
        elif registry.device_id != record.get('device_id'):
            outcome = {'registered': False,
                       'register_error': f"CodeHub 客户端身份 {registry.device_id} 不是记录的 {record.get('device_id')}"}
        else:
            record.setdefault('device_name', registry.device_name)
            outcome = register_and_verify(registry, mid, record)
        error = outcome.get('register_error') or (None if outcome['registered'] else 'read-back mismatch')
        ack = store.ack_publication(mid, seq, registered=outcome['registered'], error=error,
                                    remote_record=outcome.get('remote_record'))
        results.append({'material_id': mid, 'seq': seq, 'remote_registered': outcome['registered'],
                        'error': error, 'ack_current': ack['current']})


def publish_local(store: CatalogStore, material_id: str, record: dict[str, Any], **flush_options) -> dict[str, Any]:
    """``record_local`` (raises ``CatalogError`` on bad evidence) then a bounded sync of that material."""
    saved = store.record_local(material_id, record)
    flushed = flush_outbox(store, [material_id], **flush_options)
    mine = next((r for r in flushed['results'] if r['seq'] == saved['outbox_seq']), None)
    return {'material_id': material_id, 'local_saved': saved['local_saved'],
            'remote_registered': bool(mine and mine['remote_registered']), 'outbox_seq': saved['outbox_seq'],
            'revision': saved['revision'], 'sync': mine}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m backend.core.skilldep_materials.local_sync',
                                     description='本机 SkillDep 进展：先离线落总表，再显式同步 CodeHub（outbox 最新覆盖）')
    parser.add_argument('--root', type=Path, default=DEFAULT_QUEUE, help='总表目录（默认 F 盘 queue）')
    parser.add_argument('--device-id', default=None, help='本机灵网 device_id（默认由本机 ConfigStore 节点解析）')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT, help='每次 CodeHub 请求超时秒数')
    parser.add_argument('--retries', type=int, default=DEFAULT_RETRIES, help='连接失败时每个请求的尝试次数')
    parser.add_argument('--budget-seconds', type=float, default=DEFAULT_BUDGET_SECONDS,
                        help='本次同步总预算；超出后其余条目保留在 outbox')
    sub = parser.add_subparsers(dest='cmd', required=True)
    add_record_arguments(sub.add_parser('publish', help='校验证据并离线保存，然后尝试同步该素材'))
    p_flush = sub.add_parser('flush', help='同步 outbox 中待同步的本机记录')
    p_flush.add_argument('material_ids', nargs='*', help='默认全部')
    p_flush.add_argument('--limit', type=int, default=None)
    sub.add_parser('outbox', help='查看待同步 outbox（只读）')
    args = parser.parse_args(argv)

    store = CatalogStore(args.root, device_id=args.device_id)
    options = {'timeout': args.timeout, 'retries': args.retries, 'budget_seconds': args.budget_seconds}
    if args.cmd == 'publish':
        try:
            result = publish_local(store, args.material_id, record_from_args(args), **options)
        except CatalogError as exc:
            write_json({'local_saved': False, 'remote_registered': False, 'error': str(exc)})
            return 2
        write_json(result)
        return 0 if result['remote_registered'] else 1
    if args.cmd == 'flush':
        result = flush_outbox(store, args.material_ids or None, limit=args.limit, **options)
        write_json(result)
        return 0 if result['pending'] == 0 else 1
    write_json(store.outbox())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
