"""Download selected SkillDep plan materials to this device and register them on CodeHub.

The plan, its stable material IDs and the acquisition records belong to the
CodeHub backend; this side only downloads, hashes and reports. A file counts as
done locally once its bytes match the local manifest; it counts as registered
only after the CodeHub record read back matches the same size, hash and path.
Failures are registered as ``failed`` and never as downloaded. While a file streams
in, ``progress.ProgressReporter`` reports ``downloading`` per the collection rule in
``progress`` (announced at the start, then every 10 min / 1GB). Before each fetch the
site's CodeHub breaker is checked (``site_guard``); a throttle answer trips it and the
remaining items of that site are not fetched. ``first_batch_limit_mb`` caps an item's
bytes for this round.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from .download import fetch_to_file, filename_for, sha256_file
from .batch import first_batch_limit_bytes
from .progress import ProgressReporter
from .site_guard import SiteBlocked, assert_open, site_name, throttle_reason, trip

MB = 1024 * 1024
# User-chosen durable home for SkillDep materials on this device. An explicit root
# overrides it; a missing F: drive fails loudly instead of falling back to C:.
DEFAULT_ROOT = Path('F:/CodeYun/materials/m2610skilldependency')
# One hard cap for everything kept under a root: direct files plus slow-source samples.
HARD_TOTAL_LIMIT = 100 * MB
# An estimate in the plan is only a hint: allow headroom, but never unbounded.
ESTIMATE_HEADROOM = 1.5
UNKNOWN_SIZE_LIMIT = 20 * MB
RECORD_KEYS = ('status', 'availability', 'path', 'size_bytes', 'sha256',
               'manifest_path', 'manifest_sha256', 'source_url')
# The scope fields CodeHub persists; anything else in ``scope`` is dropped on PUT and never reads back.
REMOTE_SCOPE_KEYS = ('items', 'note', 'pages', 'page_limit')


class Registry(Protocol):
    device_id: str
    device_name: str

    def materials(self) -> list[dict[str, Any]]: ...
    def register(self, material_id: str, record: dict[str, Any]) -> dict[str, Any]: ...
    def acquisitions(self, material_id: str) -> list[dict[str, Any]]: ...


def materials_root() -> Path:
    return DEFAULT_ROOT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / 'manifest.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'items': {}}


def _save_manifest(root: Path, manifest: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'manifest.json'
    tmp = path.with_name('manifest.json.tmp')
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def item_limit(material: dict[str, Any], remaining: int) -> int:
    estimate = material.get('size_mb_est')
    limit = int(estimate * MB * ESTIMATE_HEADROOM) + MB if estimate else UNKNOWN_SIZE_LIMIT
    return min(limit, remaining, first_batch_limit_bytes(material) or limit)


def _verified_local(entry: dict[str, Any] | None) -> bool:
    if not entry or entry.get('status') != 'downloaded':
        return False
    path = Path(entry['path'])
    return (path.is_file() and path.stat().st_size == entry['size_bytes']
            and sha256_file(path) == entry['sha256'])


def direct_bytes(root: Path) -> int:
    """Bytes of verified direct files recorded in the manifest of root."""
    return sum(e['size_bytes'] for e in _load_manifest(Path(root))['items'].values() if _verified_local(e))


def _samples_bytes(root: Path) -> int:
    from .samples import DEFAULT_SAMPLES_DIRNAME, load_manifest

    return load_manifest(root / DEFAULT_SAMPLES_DIRNAME).get('total_payload_bytes', 0)


def _adopt(entry: dict[str, Any] | None, url: str, target: Path) -> dict[str, Any] | None:
    """Copy a verified file of an older root into ``target``; the copy must hash the same."""
    if not _verified_local(entry) or entry.get('source_url') != url:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + '.part')
    try:
        shutil.copyfile(entry['path'], part)
        if sha256_file(part) != entry['sha256']:
            return None
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    return {**entry, 'path': str(target), 'adopted_from': entry['path']}


def register_and_verify(registry: Registry, material_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """Idempotent PUT of this device's record, then read it back and compare."""
    try:
        registry.register(material_id, record)
        remote = [r for r in registry.acquisitions(material_id) if r.get('device_id') == registry.device_id]
        if not remote:
            return {'registered': False, 'remote_record': None, 'register_error': '回读未找到本设备的登记记录'}
        got = remote[0]
        diffs = [(k, record.get(k), got.get(k)) for k in RECORD_KEYS if got.get(k) != record.get(k)]
        diffs += [(f'scope.{k}', v, (got.get('scope') or {}).get(k))
                  for k, v in (record.get('scope') or {}).items() if (got.get('scope') or {}).get(k) != v]
        res = {'registered': not diffs, 'remote_record': got}
        if diffs:
            res['register_error'] = ('回读不一致：' + '；'.join(
                f'{k} 本地 {v!r:.80} / 远端 {r!r:.80}' for k, v, r in diffs))[:500]
        return res
    except Exception as exc:
        return {'registered': False, 'register_error': f'{type(exc).__name__}: {exc}'[:500]}


def acquire(registry: Registry, material_ids: Iterable[str], *, root: Path | None = None,
            max_total_bytes: int = HARD_TOTAL_LIMIT, session=None,
            adopt_from: Path | None = None) -> list[dict[str, Any]]:
    """Download (or reuse) each material, register it and verify the read-back record.

    ``adopt_from`` names an older root whose verified files are copied over
    instead of downloaded again; the old copies are left for the caller.
    """
    root = Path(root or materials_root()).resolve()
    max_total_bytes = min(max_total_bytes, HARD_TOTAL_LIMIT)
    plan = {m['id']: m for m in registry.materials()}
    ids = list(dict.fromkeys(material_ids))
    unknown = [i for i in ids if i not in plan]
    if unknown:
        raise ValueError(f'方案中不存在这些素材：{unknown}')
    blocked = [i for i in ids if plan[i].get('requires_login_or_purchase') or not plan[i].get('url')]
    if blocked:
        raise ValueError(f'这些素材需要登录/购买或没有直链，不在本工具范围：{blocked}')

    manifest = _load_manifest(root)
    old = _load_manifest(Path(adopt_from))['items'] if adopt_from else {}
    used = _samples_bytes(root) + direct_bytes(root)
    results = []
    stopped_sites: dict[str, str] = {}
    for material_id in ids:
        material = plan[material_id]
        entry = manifest['items'].get(material_id)
        reused = _verified_local(entry) and entry.get('source_url') == material['url']
        if not reused:
            target = root / material_id / filename_for(material['url'], material_id)
            candidate = old.get(material_id)
            over = bool(candidate) and used + (candidate.get('size_bytes') or 0) > max_total_bytes
            adopted = None if over else _adopt(candidate, material['url'], target)
            if adopted:
                entry = adopted
                used += entry['size_bytes']
            elif over:  # adopting is bounded like downloading; never fetch it again instead
                entry = {'status': 'failed', 'source_url': material['url'], 'finished_at': _now(),
                         'name': material.get('name'),
                         'error': f"采用旧文件 {candidate['size_bytes']} 字节超出剩余预算 {max_total_bytes - used}"}
            else:
                reporter = ProgressReporter(registry, material_id, target.parent, source_url=material['url'],
                                            announce=True)
                try:
                    site = site_name(material['url'])
                    if site in stopped_sites:
                        raise SiteBlocked(site, stopped_sites[site])
                    if hasattr(registry, 'site_blocks'):
                        assert_open(registry, material['url'])
                    got = fetch_to_file(material['url'], target, session=session, on_progress=reporter.update,
                                        max_bytes=item_limit(material, max_total_bytes - used))
                    entry = {'status': 'downloaded', 'path': str(got.path), 'size_bytes': got.size_bytes,
                             'sha256': got.sha256, 'content_type': got.content_type}
                    used += got.size_bytes
                except Exception as exc:  # incl. DownloadTooLarge and requests errors: a failure, never success
                    entry = {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'[:500]}
                    response = getattr(exc, 'response', None)
                    reason = response is not None and throttle_reason(response.status_code, b'', expect_body=False)
                    if reason and hasattr(registry, 'put_site_block'):
                        stopped_sites[site_name(material['url'])] = reason
                        entry['site_block'] = trip(registry, material['url'], reason)
                finally:
                    reporter.close()
                entry.update(source_url=material['url'], finished_at=_now(), name=material.get('name'))
            manifest['items'][material_id] = entry
            _save_manifest(root, manifest)

        record = {'device_id': registry.device_id, 'device_name': registry.device_name,
                  'status': entry['status'], 'availability': 'windows_local',
                  'source_url': entry['source_url'], 'finished_at': entry['finished_at']}
        if entry['status'] == 'downloaded':
            record.update(path=entry['path'], size_bytes=entry['size_bytes'], sha256=entry['sha256'])
        else:
            record['error'] = entry['error']
        results.append({'material_id': material_id, 'name': material.get('name'), 'reused_local': reused,
                        'adopted_from': entry.get('adopted_from') if not reused else None,
                        **record, **register_and_verify(registry, material_id, record)})
    return results
