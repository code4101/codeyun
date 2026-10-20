"""Offline, durable SkillDep material catalog (queue) on this device.

The plan, acquisition records and cloud/delivery status belong to CodeHub; this
store keeps the latest snapshot of them on the materials drive so status and
task selection work without CodeHub. ``catalog.json`` is the only authority and
is written atomically under a file lock; ``catalog.csv`` is an Excel-readable
view regenerated from it. Importing the same snapshot again changes nothing; a
snapshot never downgrades a local ``downloaded`` record, and a failed refresh
leaves the stored catalog untouched.

Task order: rate-limited (slow) sources first so they can start early, then the
smallest estimated size. Purchases/logins and known-blocked routes are never
offered as tasks.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock

from .acquire import DEFAULT_ROOT

SCHEMA = 'codeyun.skilldep_catalog/v1'
DEFAULT_QUEUE = DEFAULT_ROOT / 'queue'
DEFAULT_SNAPSHOT = DEFAULT_ROOT / 'orchestration' / 'codehub-snapshot.json'
ROUTES_NAME = 'candidate-routes.json'
LOCAL_RANK = {'pending': 0, 'failed': 1, 'partial': 2, 'downloaded': 3}
# Worker-owned task states; ``done`` is deliberately absent: completion only comes
# from a verified acquisition record in a snapshot.
TASK_STATES = ('pending', 'claimed', 'failed', 'blocked')
LOCAL_KEYS = ('status', 'availability', 'path', 'size_bytes', 'sha256', 'manifest_path',
              'manifest_sha256', 'scope', 'source_url', 'finished_at', 'error', 'device_name', 'recorded_at')
# Delivery rows can carry share links; only these non-secret fields are kept.
DELIVERY_KEYS = ('delivery_status', 'delivered_at', 'resource_version', 'target_cloud_dir')
CSV_COLUMNS = ('id', 'domain', 'label', 'via', 'name', 'type', 'size', 'size_mb_est', 'count',
               'requires_login_or_purchase', 'local_status', 'local_size_bytes', 'local_path', 'local_sha256',
               'local_scope_note', 'rate_limited', 'blocked_reason', 'task_status', 'executable',
               'delivery_status', 'cloud_transferred', 'cloud_transferring', 'cloud_failed', 'cloud_stale',
               'mac_accessible', 'in_plan', 'url', 'what', 'price', 'meta')


class CatalogError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _write_atomic(path: Path, text: str, encoding: str = 'utf-8') -> None:
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding=encoding, newline='')
    os.replace(tmp, path)


def _snapshot_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    materials = snapshot.get('materials')
    items = materials.get('items') if isinstance(materials, dict) else materials
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) and i.get('id') for i in items):
        raise CatalogError('快照缺少 materials.items 或条目无 id')
    if isinstance(materials, dict) and materials.get('count') not in (None, len(items)):
        raise CatalogError(f"快照 materials.count={materials.get('count')} 与条目数 {len(items)} 不符")
    return items


def _best_local(records: list[dict[str, Any]]) -> dict[str, Any]:
    best = max(records, key=lambda r: (LOCAL_RANK.get(r.get('status'), -1), r.get('recorded_at') or ''),
               default=None)
    if not best or best.get('status') not in LOCAL_RANK:
        return {'status': 'pending'}
    return {k: best.get(k) for k in LOCAL_KEYS if best.get(k) is not None}


def _local_from_flags(flags: dict[str, Any]) -> dict[str, Any]:
    if flags.get('windows_local_downloaded'):
        return {'status': 'downloaded'}
    if flags.get('windows_local_partial'):
        return {'status': 'partial'}
    return {'status': 'pending'}


def _load_routes(path: Path | None) -> tuple[dict[str, str], dict[str, str]]:
    """Return (rate-limit notes, blocked reasons) per material from the route survey."""
    if not path or not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding='utf-8'))
    limited = {}
    for c in data.get('candidates', []):
        note = str(c.get('rate_limit') or '').strip()
        if note and not note.lower().startswith(('none', 'no rate-limit')):
            limited[c['material_id']] = note
    blocked = {b['material_id']: b.get('reason', '') for b in data.get('blocked_or_needs_link_parse', [])
               if str(b.get('material_id', '')).startswith('sd-')}
    return limited, blocked


def _slow_sources() -> set[str]:
    from .samples import SUPPORTED
    return set(SUPPORTED)


class CatalogStore:
    def __init__(self, root: Path | str = DEFAULT_QUEUE):
        self.root = Path(root)
        self.path = self.root / 'catalog.json'
        self.csv_path = self.root / 'catalog.csv'
        self._lock = FileLock(str(self.root / 'catalog.lock'), timeout=60)

    # -- persistence -------------------------------------------------------
    def _read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding='utf-8'))

    def load(self) -> dict[str, Any]:
        data = self._read()
        if data is None:
            raise CatalogError(f'总表不存在：{self.path}；先执行 import')
        return data

    def _save(self, data: dict[str, Any]) -> None:
        data['revision'] = int(data.get('revision', 0)) + 1
        data['updated_at'] = _now()
        _write_atomic(self.path, json.dumps(data, ensure_ascii=False, indent=2))
        try:
            _write_atomic(self.csv_path, self._csv_text(data), encoding='utf-8-sig')
            data.pop('csv_error', None)
        except OSError as exc:  # e.g. the CSV is open in Excel; JSON stays authoritative
            data['csv_error'] = f'{type(exc).__name__}: {exc}'
            _write_atomic(self.path, json.dumps(data, ensure_ascii=False, indent=2))

    @staticmethod
    def _csv_text(data: dict[str, Any]) -> str:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction='ignore')
        writer.writeheader()
        for item in data['items'].values():
            local, cloud = item['local'], item.get('cloud_transfer') or {}
            scope = local.get('scope') if isinstance(local.get('scope'), dict) else {}
            writer.writerow({
                **{k: item.get(k) for k in CSV_COLUMNS if k in item},
                'local_status': local.get('status'), 'local_size_bytes': local.get('size_bytes'),
                'local_path': local.get('path'), 'local_sha256': local.get('sha256'),
                'local_scope_note': scope.get('note'), 'task_status': item['task']['status'],
                'delivery_status': (item.get('delivery') or {}).get('delivery_status'),
                'mac_accessible': (item.get('acquisition') or {}).get('mac_accessible'),
                **{k: cloud.get(k) for k in ('cloud_transferred', 'cloud_transferring', 'cloud_failed', 'cloud_stale')},
            })
        return buf.getvalue()

    # -- import / refresh --------------------------------------------------
    def import_snapshot(self, snapshot: dict[str, Any], *, source: str,
                        routes_path: Path | None = None) -> dict[str, Any]:
        """Merge a CodeHub plan snapshot; idempotent for an unchanged snapshot."""
        items = _snapshot_items(snapshot)
        limited, blocked = _load_routes(routes_path)
        slow = _slow_sources()
        by_material: dict[str, list[dict[str, Any]]] = {}
        for record in snapshot.get('acquisitions') or []:
            by_material.setdefault(record.get('material_id'), []).append(record)
        deliveries = snapshot.get('delivery_resources') or {}
        deliveries = deliveries.get('items', []) if isinstance(deliveries, dict) else deliveries
        delivery_by_id = {d.get('material_id'): {k: d.get(k) for k in DELIVERY_KEYS} for d in deliveries}
        fingerprint = json.dumps({'generated_at': snapshot.get('generated_at'), 'versions': snapshot.get('versions'),
                                  'routes': [limited, blocked]}, sort_keys=True, ensure_ascii=False)

        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            old = self._read() or {'schema': SCHEMA, 'revision': 0, 'items': {}}
            if old.get('fingerprint') == fingerprint and len(old['items']) >= len(items):
                return {'changed': False, 'revision': old['revision'], 'count': len(old['items'])}
            kept_downloads = []
            new_items: dict[str, dict[str, Any]] = {}
            for plan in items:
                mid = plan['id']
                prev = old['items'].get(mid) or {}
                local = (_best_local(by_material[mid]) if mid in by_material
                         else _local_from_flags(plan.get('acquisition') or {}))
                prev_local = prev.get('local') or {}
                if LOCAL_RANK.get(prev_local.get('status'), 0) > LOCAL_RANK.get(local['status'], 0) \
                        and prev_local.get('status') == 'downloaded':
                    local = prev_local
                    kept_downloads.append(mid)
                item = {k: v for k, v in plan.items()}
                item.update({
                    'local': local,
                    'delivery': delivery_by_id.get(mid),
                    'rate_limited': mid in slow or mid in limited,
                    'rate_limit_note': limited.get(mid) or ('slow-sample source' if mid in slow else None),
                    'blocked_reason': blocked.get(mid),
                    'task': prev.get('task') or {'status': 'pending'},
                    'in_plan': True,
                })
                new_items[mid] = item
            for mid, prev in old['items'].items():  # dropped from the plan: kept, never deleted
                if mid not in new_items:
                    new_items[mid] = {**prev, 'in_plan': False}
            for item in new_items.values():
                item['executable'] = self._executable(item)
            data = {
                'schema': SCHEMA, 'revision': old.get('revision', 0), 'fingerprint': fingerprint,
                'source': source, 'snapshot_generated_at': snapshot.get('generated_at'),
                'source_versions': snapshot.get('versions'), 'imported_at': _now(),
                'cloud_transfers_count': len(snapshot.get('cloud_transfers') or []),
                'last_refresh': old.get('last_refresh'), 'items': new_items,
            }
            self._save(data)
            return {'changed': True, 'revision': data['revision'], 'count': len(new_items),
                    'kept_local_downloads': kept_downloads}

    def refresh(self, fetch: Callable[[], dict[str, Any]], *, source: str = 'codehub',
                routes_path: Path | None = None) -> dict[str, Any]:
        """Fetch and import a new snapshot; any failure keeps the stored catalog as is."""
        try:
            snapshot = fetch()
            _snapshot_items(snapshot)
        except Exception as exc:  # noqa: BLE001 - every refresh failure is recorded, none propagates
            error = f'{type(exc).__name__}: {exc}'[:500]
            self.root.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = self._read()
                if data is not None:
                    data['last_refresh'] = {'at': _now(), 'ok': False, 'error': error}
                    self._save(data)
            return {'ok': False, 'error': error, 'kept_revision': data and data['revision']}
        result = self.import_snapshot(snapshot, source=source, routes_path=routes_path)
        with self._lock:
            data = self.load()
            data['last_refresh'] = {'at': _now(), 'ok': True, 'changed': result['changed']}
            self._save(data)
        return {'ok': True, **result}

    # -- queries -----------------------------------------------------------
    @staticmethod
    def _executable(item: dict[str, Any]) -> bool:
        return (item.get('in_plan', True) and not item.get('requires_login_or_purchase')
                and not item.get('blocked_reason') and item['local']['status'] != 'downloaded'
                and item['task']['status'] not in ('claimed', 'blocked'))

    @staticmethod
    def _priority(item: dict[str, Any]) -> tuple:
        size = item.get('size_mb_est')
        size = float(size) if isinstance(size, (int, float)) else math.inf
        return (not item.get('rate_limited'), size, item['id'])

    def status(self) -> dict[str, Any]:
        data = self.load()
        items = list(data['items'].values())

        def count(key: Callable[[dict[str, Any]], Any]) -> dict[str, int]:
            out: dict[str, int] = {}
            for i in items:
                k = str(key(i))
                out[k] = out.get(k, 0) + 1
            return dict(sorted(out.items()))

        return {
            'path': str(self.path), 'revision': data['revision'], 'updated_at': data.get('updated_at'),
            'source': data.get('source'), 'snapshot_generated_at': data.get('snapshot_generated_at'),
            'source_versions': data.get('source_versions'), 'last_refresh': data.get('last_refresh'),
            'total': len(items), 'in_plan': sum(1 for i in items if i.get('in_plan', True)),
            'local_status': count(lambda i: i['local']['status']),
            'task_status': count(lambda i: i['task']['status']),
            'via': count(lambda i: i.get('via')),
            'delivery_status': count(lambda i: (i.get('delivery') or {}).get('delivery_status')),
            'cloud_transferred': sum(1 for i in items if (i.get('cloud_transfer') or {}).get('cloud_transferred')),
            'rate_limited': sum(1 for i in items if i.get('rate_limited')),
            'executable': sum(1 for i in items if i.get('executable')),
            'csv': str(self.csv_path), 'csv_error': data.get('csv_error'),
        }

    def next_tasks(self, limit: int = 1) -> list[dict[str, Any]]:
        items = sorted((i for i in self.load()['items'].values() if i.get('executable')), key=self._priority)
        return [{'id': i['id'], 'name': i.get('name'), 'via': i.get('via'), 'url': i.get('url'),
                 'size': i.get('size'), 'size_mb_est': i.get('size_mb_est'), 'local_status': i['local']['status'],
                 'rate_limited': i.get('rate_limited'), 'rate_limit_note': i.get('rate_limit_note'),
                 'scope_note': (i['local'].get('scope') or {}).get('note')
                 if isinstance(i['local'].get('scope'), dict) else None}
                for i in items[:limit]]

    def set_task(self, material_id: str, status: str, note: str | None = None) -> dict[str, Any]:
        """Record worker-side task state (not completion) durably."""
        if status not in TASK_STATES:
            raise CatalogError(f'任务状态只能是 {TASK_STATES}')
        with self._lock:
            data = self.load()
            item = data['items'].get(material_id)
            if item is None:
                raise CatalogError(f'总表中没有 {material_id}')
            item['task'] = {'status': status, 'note': note, 'at': _now()}
            item['executable'] = self._executable(item)
            self._save(data)
            return item['task']


def codehub_snapshot() -> dict[str, Any]:
    """Fetch the plan snapshot through the public CodeHub client interface."""
    from .codehub import CodeHubSkillDep

    client = CodeHubSkillDep()
    fetch = getattr(client, 'snapshot', None)
    if fetch is None:
        raise CatalogError('CodeHubSkillDep 尚无公开 snapshot() 接口；请在提供方补齐后再刷新')
    return fetch()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m backend.core.skilldep_materials.catalog',
                                     description='SkillDep 素材离线总表（F 盘持久 JSON + Excel 可读 CSV）')
    parser.add_argument('--root', type=Path, default=DEFAULT_QUEUE, help='总表目录（默认 F 盘 queue）')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p_import = sub.add_parser('import', help='导入已有 CodeHub 快照文件（幂等）')
    p_import.add_argument('--snapshot', type=Path, default=DEFAULT_SNAPSHOT)
    p_import.add_argument('--routes', type=Path, default=None, help='来源调查 candidate-routes.json（默认快照同目录）')
    sub.add_parser('status', help='离线汇总')
    p_next = sub.add_parser('next-task', help='离线选出下一批可执行任务（限流优先，其次小资源）')
    p_next.add_argument('--limit', type=int, default=5)
    p_refresh = sub.add_parser('refresh', help='显式从 CodeHub 刷新；失败保留原快照')
    p_refresh.add_argument('--routes', type=Path, default=None)
    args = parser.parse_args(argv)

    store = CatalogStore(args.root)
    if args.cmd == 'import':
        snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
        routes = args.routes or args.snapshot.with_name(ROUTES_NAME)
        result = store.import_snapshot(snapshot, source=str(args.snapshot), routes_path=routes)
    elif args.cmd == 'status':
        result = store.status()
    elif args.cmd == 'next-task':
        result = store.next_tasks(args.limit)
    else:
        result = store.refresh(codehub_snapshot, routes_path=args.routes or DEFAULT_SNAPSHOT.with_name(ROUTES_NAME))
    if hasattr(sys.stdout, 'reconfigure'):  # a GBK console must not crash on material names
        sys.stdout.reconfigure(encoding='utf-8')
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return 0 if not (args.cmd == 'refresh' and not result.get('ok')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
