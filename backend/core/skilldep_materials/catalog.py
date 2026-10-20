"""Offline, durable SkillDep material catalog (queue) on this device.

The plan, acquisition records and cloud/delivery status belong to CodeHub; this
store keeps the latest snapshot of them on the materials drive so status and
task selection work without CodeHub. ``catalog.json`` is the only authority and
is written atomically under a file lock; ``catalog.csv`` is an Excel-readable
view regenerated from it. Importing the same snapshot content again changes
nothing (the fingerprint covers the data, not ``generated_at``/versions), and a
failed refresh leaves the stored catalog untouched.

``local`` is this device only: the latest ``windows_local`` acquisition recorded
under this device's LingNet ``device_id``. Other devices' records stay in
``remote_acquisitions`` and the plan's aggregate flags in ``acquisition``; neither
counts as a local file. Record times are compared as datetimes (mixed time zones
are fine). A newer snapshot record for this device replaces the stored one,
downgrades included. A stored ``partial``/``downloaded`` record is kept only when
the snapshot has no record for this device or an older one, and its file (size
and SHA-256) or directory manifest (SHA-256) is still on disk as recorded.

``record_local`` records local progress without CodeHub: it verifies the
evidence on disk, saves the record and the CSV under the lock, then queues it in
``outbox.json`` (latest record per material wins, keyed by catalog revision).
``publication.local_saved`` and ``publication.remote_registered`` are separate:
only a CodeHub read-back acknowledged through ``ack_publication`` sets the
latter (see ``local_sync``). While a local record still waits in the outbox, no
snapshot replaces it as long as its evidence holds.

Task order: rate-limited (slow) sources first so they can start early, then the
smallest estimated size. Purchases/logins and known-blocked routes are never
offered as tasks.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
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
OUTBOX_SCHEMA = 'codeyun.skilldep_outbox/v1'
DEFAULT_QUEUE = DEFAULT_ROOT / 'queue'
DEFAULT_SNAPSHOT = DEFAULT_ROOT / 'orchestration' / 'codehub-snapshot.json'
ROUTES_NAME = 'candidate-routes.json'
LOCAL_STATUSES = ('pending', 'failed', 'partial', 'downloaded')
LOCAL_AVAILABILITY = 'windows_local'
# Worker-owned task states; ``done`` is deliberately absent: completion only comes
# from a verified acquisition record in a snapshot.
TASK_STATES = ('pending', 'claimed', 'failed', 'blocked')
LOCAL_KEYS = ('status', 'device_id', 'availability', 'path', 'size_bytes', 'sha256', 'manifest_path',
              'manifest_sha256', 'scope', 'source_url', 'finished_at', 'error', 'device_name', 'recorded_at')
# Delivery rows can carry share links; only these non-secret fields are kept.
DELIVERY_KEYS = ('delivery_status', 'delivered_at', 'resource_version', 'target_cloud_dir')
REMOTE_KEYS = ('device_id', 'device_name', 'availability', 'status', 'recorded_at')
CSV_COLUMNS = ('id', 'domain', 'label', 'via', 'name', 'type', 'size', 'size_mb_est', 'count',
               'requires_login_or_purchase', 'local_status', 'local_size_bytes', 'local_path', 'local_sha256',
               'local_scope_note', 'rate_limited', 'blocked_reason', 'task_status', 'executable',
               'delivery_status', 'cloud_transferred', 'cloud_transferring', 'cloud_failed', 'cloud_stale',
               'mac_accessible', 'in_plan', 'url', 'what', 'price', 'meta', 'local_saved', 'remote_registered')
# Remote-assigned or local-only fields never sent in a CodeHub PUT.
UNPUBLISHED_KEYS = ('recorded_at', 'origin')
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
_HASH_CACHE: dict[tuple[str, int, int], str] = {}


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


def _parse_time(value: Any) -> datetime | None:
    """An ISO time as an aware datetime (``Z`` accepted; a naive value counts as UTC)."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _aware_iso(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.strip().replace('Z', '+00:00')).tzinfo is not None
    except ValueError:
        return False


def record_time(record: dict[str, Any] | None) -> datetime:
    """When a record was made: ``recorded_at``, else ``finished_at``; unknown sorts first."""
    record = record or {}
    return _parse_time(record.get('recorded_at')) or _parse_time(record.get('finished_at')) or _EPOCH


def _is_local(record: dict[str, Any], device_id: str) -> bool:
    return record.get('device_id') == device_id and record.get('availability') == LOCAL_AVAILABILITY


def _latest_local(records: list[dict[str, Any]], device_id: str) -> dict[str, Any] | None:
    """This device's most recent windows_local record (by time, never by status rank)."""
    mine = [r for r in records if _is_local(r, device_id) and r.get('status') in LOCAL_STATUSES]
    best = max(mine, key=record_time, default=None)
    return best and {k: best.get(k) for k in LOCAL_KEYS if best.get(k) is not None}


def _sha256(path: Path) -> str:
    """SHA-256 of a file, cached per (path, size, mtime) so repeated imports stay cheap."""
    from .download import sha256_file

    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if key not in _HASH_CACHE:
        _HASH_CACHE[key] = sha256_file(path)
    return _HASH_CACHE[key]


def _manifest_problem(material_id: str | None, directory: Path, manifest: Any, sha: Any) -> str | None:
    """Why a directory's manifest is no evidence; None when it is a real, non-empty one inside it."""
    if not manifest or not sha:
        return '目录记录需要 manifest_path 与 manifest_sha256'
    manifest = Path(manifest)
    if not manifest.is_file():
        return f'manifest 不存在：{manifest}'
    if not manifest.resolve().is_relative_to(directory.resolve()):
        return f'manifest 不在记录目录内：{manifest}'
    if _sha256(manifest) != sha:
        return f'manifest SHA-256 与记录不符：{manifest}'
    try:
        content = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return f'manifest 不是 JSON：{type(exc).__name__}'
    if not isinstance(content, dict) or not (content.get('files') or content.get('pages')):
        return 'manifest 没有 files/pages 条目，不能作为进展证据'
    if material_id and content.get('material_id') not in (None, material_id):
        return f"manifest 属于 {content.get('material_id')}，不是 {material_id}"
    return None


def evidence_problem(local: dict[str, Any], material_id: str | None = None) -> str | None:
    """Why a partial/downloaded record has no real evidence on disk; None when it does.

    A file needs its recorded size and SHA-256; a directory needs a non-empty
    manifest inside it with the recorded SHA-256 (payload coverage is the
    writing provider's contract).
    """
    path = local.get('path')
    if not path:
        return '缺少 path'
    path = Path(path)
    size = local.get('size_bytes')
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return 'size_bytes 必须是非负整数'
    if path.is_file():
        if path.stat().st_size != size:
            return f'文件大小 {path.stat().st_size} 与记录 {size} 不符'
        if not local.get('sha256'):
            return '文件记录缺少 sha256'
        if _sha256(path) != local['sha256']:
            return '文件 SHA-256 与记录不符'
        if local.get('manifest_path'):
            return _manifest_problem(material_id, path.parent, local['manifest_path'], local.get('manifest_sha256'))
        return None
    if path.is_dir():
        return _manifest_problem(material_id, path, local.get('manifest_path'), local.get('manifest_sha256'))
    return f'路径不存在：{path}'


def _file_evidence(local: dict[str, Any]) -> bool:
    """The recorded local file (size, SHA-256) or directory manifest is still on disk as recorded."""
    if not (local.get('recorded_at') or local.get('finished_at')):
        return False
    try:
        return evidence_problem(local) is None
    except OSError:
        return False


def _publishable(record: dict[str, Any]) -> dict[str, Any]:
    """The CodeHub PUT body of a local record: CodeHub assigns ``recorded_at`` itself."""
    return {k: v for k, v in record.items() if k in LOCAL_KEYS and k not in UNPUBLISHED_KEYS}


def _pending_local(item: dict[str, Any]) -> bool:
    """The item's local record was made here and still waits for its CodeHub acknowledgement."""
    pub = item.get('publication') or {}
    return (item.get('local') or {}).get('origin') == 'local' and pub.get('pending_seq') is not None


def _keep_previous(prev_item: dict[str, Any], new: dict[str, Any] | None, device_id: str) -> bool:
    """Keep a stored local record the snapshot lacks, only has an older record of, or not yet received.

    ``partial``/``downloaded`` need their evidence on disk; a newer locally recorded
    ``failed``/``pending`` (an explicit invalidation) needs none.
    """
    prev = prev_item.get('local') or {}
    if not _is_local(prev, device_id):
        return False
    if new is not None and record_time(new) >= record_time(prev) and not _pending_local(prev_item):
        return False
    if prev.get('status') in ('partial', 'downloaded'):
        return _file_evidence(prev)
    return prev.get('origin') == 'local'


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


def _fingerprint(*parts: Any) -> str:
    text = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class CatalogStore:
    def __init__(self, root: Path | str = DEFAULT_QUEUE, device_id: str | None = None):
        self.root = Path(root)
        self._device_id = device_id
        self.path = self.root / 'catalog.json'
        self.csv_path = self.root / 'catalog.csv'
        self.outbox_path = self.root / 'outbox.json'
        self._lock = FileLock(str(self.root / 'catalog.lock'), timeout=60)

    @property
    def device_id(self) -> str:
        """This device's LingNet node id, resolved locally when first needed."""
        if not self._device_id:
            from .codehub import local_device
            self._device_id = local_device()['device_id']
        return self._device_id

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
                **{k: (item.get('publication') or {}).get(k) for k in ('local_saved', 'remote_registered')},
            })
        return buf.getvalue()

    # -- import / refresh --------------------------------------------------
    def import_snapshot(self, snapshot: dict[str, Any], *, source: str,
                        routes_path: Path | None = None) -> dict[str, Any]:
        """Merge a CodeHub plan snapshot; idempotent for unchanged snapshot content."""
        items = _snapshot_items(snapshot)
        device_id = self.device_id
        limited, blocked = _load_routes(routes_path)
        slow = _slow_sources()
        acquisitions = snapshot.get('acquisitions') or []
        by_material: dict[str, list[dict[str, Any]]] = {}
        for record in acquisitions:
            by_material.setdefault(record.get('material_id'), []).append(record)
        deliveries = snapshot.get('delivery_resources') or {}
        deliveries = deliveries.get('items', []) if isinstance(deliveries, dict) else deliveries
        delivery_by_id = {d.get('material_id'): {k: d.get(k) for k in DELIVERY_KEYS} for d in deliveries}
        fingerprint = _fingerprint(device_id, items, acquisitions, delivery_by_id,
                                   len(snapshot.get('cloud_transfers') or []), [limited, blocked], sorted(slow))

        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            old = self._read() or {'schema': SCHEMA, 'revision': 0, 'items': {}}
            kept_downloads = []
            new_items: dict[str, dict[str, Any]] = {}
            for plan in items:
                mid = plan['id']
                prev = old['items'].get(mid) or {}
                records = by_material.get(mid, [])
                # Plan flags aggregate every Windows device, so they never make a local record.
                local = _latest_local(records, device_id)
                publication = prev.get('publication')
                if _keep_previous(prev, local, device_id):
                    local = prev['local']
                    kept_downloads.append(mid)
                elif publication and publication.get('pending_seq') is not None:
                    # The queued local record lost its evidence; its outbox entry is dropped on next flush.
                    publication = {**publication, 'pending_seq': None, 'local_saved': False,
                                   'superseded_by': 'snapshot'}
                item = {k: v for k, v in plan.items()}
                if publication:
                    item['publication'] = publication
                item.update({
                    'local': local or {'status': 'pending'},
                    'remote_acquisitions': [{k: r.get(k) for k in REMOTE_KEYS} for r in records
                                            if not _is_local(r, device_id)],
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
            # Kept files are re-verified each import, so the merged items are compared too.
            if old.get('fingerprint') == fingerprint and old.get('device_id') == device_id \
                    and old['items'] == new_items:
                return {'changed': False, 'revision': old['revision'], 'count': len(old['items'])}
            data = {
                'schema': SCHEMA, 'revision': old.get('revision', 0), 'fingerprint': fingerprint,
                'device_id': device_id,
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
            data['last_refresh'] = {'at': _now(), 'ok': True, 'changed': result['changed'],
                                    'snapshot_generated_at': snapshot.get('generated_at'),
                                    'source_versions': snapshot.get('versions')}
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
            'device_id': data.get('device_id'), 'total': len(items), 'in_plan': sum(1 for i in items if i.get('in_plan', True)),
            'local_status': count(lambda i: i['local']['status']),
            'task_status': count(lambda i: i['task']['status']),
            'via': count(lambda i: i.get('via')),
            'delivery_status': count(lambda i: (i.get('delivery') or {}).get('delivery_status')),
            'cloud_transferred': sum(1 for i in items if (i.get('cloud_transfer') or {}).get('cloud_transferred')),
            'rate_limited': sum(1 for i in items if i.get('rate_limited')),
            'executable': sum(1 for i in items if i.get('executable')),
            'csv': str(self.csv_path), 'csv_error': data.get('csv_error'),
            'local_saved': sum(1 for i in items if (i.get('publication') or {}).get('local_saved')),
            'remote_registered': sum(1 for i in items if (i.get('publication') or {}).get('remote_registered')),
            'pending_sync': sum(1 for i in items if _pending_local(i)),
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


    # -- local records and publication outbox --------------------------------
    def _read_outbox(self) -> dict[str, Any]:
        if not self.outbox_path.exists():
            return {'schema': OUTBOX_SCHEMA, 'items': {}}
        return json.loads(self.outbox_path.read_text(encoding='utf-8'))

    def _write_outbox(self, box: dict[str, Any]) -> None:
        box['updated_at'] = _now()
        _write_atomic(self.outbox_path, json.dumps(box, ensure_ascii=False, indent=2))

    def record_local(self, material_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """Save this device's verified local progress for one material, then queue it for CodeHub.

        ``record`` uses the acquisition record fields (``status``, ``path``,
        ``size_bytes``, ``sha256`` or ``manifest_path``/``manifest_sha256``,
        ``scope``, ``source_url``, ``finished_at``, ``error``); ``device_id`` and
        ``availability`` default to this device / ``windows_local`` and must match
        when given, ``recorded_at`` (time-zone aware) defaults to now. Evidence is
        checked on disk before the lock is taken. A record older than the stored
        local one is refused; a newer ``failed``/``pending`` one downgrades it.
        Nothing is sent over the network: ``remote_registered`` stays False until
        ``ack_publication``.
        """
        self._check_target(self.load(), material_id)
        record = self._verified_record(material_id, record)
        with self._lock:
            data = self.load()
            item = self._check_target(data, material_id)
            prev = item.get('local') or {}
            if _is_local(prev, self.device_id) and record_time(record) < record_time(prev):
                raise CatalogError(f"{material_id} 已有更新的本机记录（{prev.get('recorded_at')}），拒绝旧记录")
            box = self._read_outbox()
            queued = (box['items'].get(material_id) or {}).get('record') or {}
            for published in (prev, queued):
                if record.get('manifest_path') and published.get('manifest_path') == record['manifest_path'] \
                        and published.get('manifest_sha256') != record.get('manifest_sha256'):
                    raise CatalogError(f"已记录的 manifest 不可改：{record['manifest_path']}")
            seq = int(data.get('revision', 0)) + 1  # the revision this save gets: unique and monotonic
            pub = item.get('publication') or {}
            item['local'] = {**record, 'origin': 'local'}
            item['publication'] = {
                'local_saved': True, 'local_saved_at': _now(), 'local_recorded_at': record['recorded_at'],
                'remote_registered': False, 'pending_seq': seq, 'attempts': 0, 'last_error': None,
                'last_remote_registered_at': pub.get('remote_registered_at') or pub.get('last_remote_registered_at'),
            }
            item['executable'] = self._executable(item)
            self._save(data)
            # Queued after the local commit; a crash in between is repaired by pending_publications.
            box['items'][material_id] = {'seq': seq, 'record': _publishable(record), 'queued_at': _now(),
                                         'attempts': 0, 'last_error': None}
            self._write_outbox(box)
            return {'material_id': material_id, 'local_saved': True, 'remote_registered': False,
                    'outbox_seq': seq, 'revision': data['revision'], 'local': item['local']}

    def _check_target(self, data: dict[str, Any], material_id: str) -> dict[str, Any]:
        if data.get('device_id') != self.device_id:
            raise CatalogError(f"总表属于设备 {data.get('device_id')}，不是本机 {self.device_id}")
        item = data['items'].get(material_id)
        if item is None:
            raise CatalogError(f'总表中没有 {material_id}')
        return item

    def _verified_record(self, material_id: str, record: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(record, dict):
            raise CatalogError('record 必须是 JSON 对象')
        if record.get('material_id') not in (None, material_id):
            raise CatalogError(f"record.material_id={record.get('material_id')} 与 {material_id} 不符")
        rec = {k: record[k] for k in LOCAL_KEYS if record.get(k) is not None}
        rec.setdefault('device_id', self.device_id)
        rec.setdefault('availability', LOCAL_AVAILABILITY)
        if rec['device_id'] != self.device_id:
            raise CatalogError(f"device_id={rec['device_id']} 不是本机 {self.device_id}")
        if rec['availability'] != LOCAL_AVAILABILITY:
            raise CatalogError(f'availability 只能是 {LOCAL_AVAILABILITY}')
        if rec.get('status') not in LOCAL_STATUSES:
            raise CatalogError(f'本机状态只能是 {LOCAL_STATUSES}')
        rec.setdefault('recorded_at', _now())
        for key in ('recorded_at', 'finished_at'):
            if key in rec and not _aware_iso(rec[key]):
                raise CatalogError(f'{key} 必须是带时区的 ISO 时间：{rec[key]!r}')
        if rec['status'] in ('partial', 'downloaded'):
            problem = evidence_problem(rec, material_id)
            if problem:
                raise CatalogError(f'{material_id} 本地证据无效：{problem}')
        return rec

    def outbox(self) -> dict[str, Any]:
        """The queued local records not yet acknowledged by CodeHub (read only)."""
        return self._read_outbox()

    def pending_publications(self, material_ids: list[str] | None = None,
                             limit: int | None = None) -> list[dict[str, Any]]:
        """Queued records to publish, oldest first; repairs the outbox from the catalog.

        A record saved locally whose outbox write was lost is queued again; an
        entry whose record is no longer the item's pending local record is dropped.
        """
        if not self.path.exists():
            return []
        with self._lock:
            data = self.load()
            box = self._read_outbox()
            changed = False
            for mid, item in data['items'].items():
                entry = box['items'].get(mid)
                if _pending_local(item) and (entry is None or entry.get('seq') != item['publication']['pending_seq']):
                    box['items'][mid] = {'seq': item['publication']['pending_seq'],
                                         'record': _publishable(item['local']), 'queued_at': _now(),
                                         'attempts': 0, 'last_error': None, 'recovered': True}
                    changed = True
            for mid, entry in list(box['items'].items()):
                item = data['items'].get(mid) or {}
                if not _pending_local(item) or item['publication']['pending_seq'] != entry.get('seq'):
                    del box['items'][mid]
                    changed = True
            if changed:
                self._write_outbox(box)
        wanted = None if material_ids is None else set(material_ids)
        entries = sorted(({'material_id': mid, **entry} for mid, entry in box['items'].items()
                          if wanted is None or mid in wanted), key=lambda e: e['seq'])
        return entries[:limit] if limit is not None else entries

    def ack_publication(self, material_id: str, seq: int, *, registered: bool, error: str | None = None,
                        remote_record: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record the outcome of publishing outbox entry ``seq`` (verified read-back or failure).

        Only the entry and pending record with that exact ``seq`` are touched, so a
        late acknowledgement never clears a newer queued record.
        """
        with self._lock:
            data = self.load()
            box = self._read_outbox()
            entry = box['items'].get(material_id)
            current = bool(entry) and entry.get('seq') == seq
            if current:
                if registered:
                    del box['items'][material_id]
                else:
                    entry.update(attempts=int(entry.get('attempts') or 0) + 1, last_error=(error or '')[:500],
                                 last_attempt_at=_now())
                self._write_outbox(box)
            item = data['items'].get(material_id) or {}
            pub = item.get('publication') or {}
            if pub.get('pending_seq') == seq:
                if registered:
                    pub.update(remote_registered=True, remote_registered_at=_now(), pending_seq=None,
                               last_error=None, remote_record={k: (remote_record or {}).get(k) for k in REMOTE_KEYS})
                else:
                    pub.update(attempts=int(pub.get('attempts') or 0) + 1, last_error=(error or '')[:500],
                               last_attempt_at=_now())
                self._save(data)
            return {'material_id': material_id, 'seq': seq, 'current': current,
                    'remote_registered': bool(pub.get('remote_registered')) and pub.get('pending_seq') is None}

def codehub_snapshot() -> dict[str, Any]:
    """Fetch the plan snapshot through the public CodeHub client interface."""
    from .codehub import CodeHubSkillDep

    return CodeHubSkillDep().snapshot(include_secrets=False)


def add_record_arguments(parser: argparse.ArgumentParser) -> None:
    """``material_id`` plus exactly one of ``--record`` (JSON text) / ``--record-file`` (JSON file)."""
    parser.add_argument('material_id')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--record', help='本机记录 JSON（status/path/size_bytes/sha256 或 manifest_path/manifest_sha256…）')
    group.add_argument('--record-file', type=Path, help='本机记录 JSON 文件')


def record_from_args(args: argparse.Namespace) -> dict[str, Any]:
    text = args.record if args.record is not None else args.record_file.read_text(encoding='utf-8')
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CatalogError(f'record 不是合法 JSON：{exc}') from exc


def write_json(result: Any) -> None:
    if hasattr(sys.stdout, 'reconfigure'):  # a GBK console must not crash on material names
        sys.stdout.reconfigure(encoding='utf-8')
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m backend.core.skilldep_materials.catalog',
                                     description='SkillDep 素材离线总表（F 盘持久 JSON + Excel 可读 CSV）')
    parser.add_argument('--root', type=Path, default=DEFAULT_QUEUE, help='总表目录（默认 F 盘 queue）')
    parser.add_argument('--device-id', default=None, help='本机灵网 device_id（默认由本机 ConfigStore 节点解析）')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p_import = sub.add_parser('import', help='导入已有 CodeHub 快照文件（幂等）')
    p_import.add_argument('--snapshot', type=Path, default=DEFAULT_SNAPSHOT)
    p_import.add_argument('--routes', type=Path, default=None, help='来源调查 candidate-routes.json（默认快照同目录）')
    sub.add_parser('status', help='离线汇总')
    p_next = sub.add_parser('next-task', help='离线选出下一批可执行任务（限流优先，其次小资源）')
    p_next.add_argument('--limit', type=int, default=5)
    p_refresh = sub.add_parser('refresh', help='显式从 CodeHub 刷新；失败保留原快照')
    p_refresh.add_argument('--routes', type=Path, default=None)
    add_record_arguments(sub.add_parser('record-local', help='校验本机文件/manifest 证据后离线保存本机进展并排入待同步 outbox（不联网）'))
    sub.add_parser('outbox', help='查看待同步 outbox（只读）')
    args = parser.parse_args(argv)

    store = CatalogStore(args.root, device_id=args.device_id)
    if args.cmd == 'import':
        snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
        routes = args.routes or args.snapshot.with_name(ROUTES_NAME)
        result = store.import_snapshot(snapshot, source=str(args.snapshot), routes_path=routes)
    elif args.cmd == 'status':
        result = store.status()
    elif args.cmd == 'next-task':
        result = store.next_tasks(args.limit)
    elif args.cmd == 'record-local':
        try:
            result = store.record_local(args.material_id, record_from_args(args))
        except CatalogError as exc:
            write_json({'local_saved': False, 'remote_registered': False, 'error': str(exc)})
            return 2
    elif args.cmd == 'outbox':
        result = store.outbox()
    else:
        result = store.refresh(codehub_snapshot, routes_path=args.routes or DEFAULT_SNAPSHOT.with_name(ROUTES_NAME))
    write_json(result)
    return 0 if not (args.cmd == 'refresh' and not result.get('ok')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
