"""Write one finalized (cleaned, structured) SkillDep material package.

A package is ``storage_layout.local_dir(...)`` holding::

    data/...            cleaned structured records (JSONL/CSV), the substance
    originals/...       necessary verbatim source files (small ones only)
    schema.json         field definitions of every data file
    provenance.json     source URL, staging manifest path/SHA, parsing notes, gaps
    manifest.json       per-file SHA-256/bytes/record counts + scope + status

``status`` is ``finalized`` only when every record of the verified staging scope
was parsed; otherwise ``partial`` with ``pending`` reasons.  Raw downloads or
hash inventories alone are never a package.

Writes are atomic and never overwrite: files go to a sibling ``.tmp-<id>``
directory which is renamed into place.  An existing package with the same
content fingerprint is reported ``unchanged``; a different one is ``conflict``
(left untouched for the owner to inspect).  Staging inputs are only read.
Correcting an existing package's file set goes through ``rewrite_package``
(verified backup first, rollback on error); ``revise_manifest`` only edits metadata.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

MANIFEST_SCHEMA = 'codeyun.skilldep_final_package/v1'
MANIFEST_KEYS = ('schema', 'material_id', 'status', 'content_fingerprint', 'records_total', 'files')
_RESERVED_TOP = ('manifest.json', 'manifest-history')
_GENERATED_KEYS = ('schema', 'material_id', 'status', 'finalized_at', 'content_fingerprint', 'scope', 'source',
                   'records_total', 'files', 'pending', 'manifest_revision', 'previous_manifest_sha256',
                   'revision_reason', 'revised_at')


def package_path(rel: str) -> str:
    """Validate a package-relative POSIX path; reject absolute, traversal and reserved names."""
    if not isinstance(rel, str) or not rel or '\\' in rel or ':' in rel or rel.startswith('/'):
        raise ValueError(f'invalid package path {rel!r}')
    parts = PurePosixPath(rel).parts
    if any(p in ('', '.', '..') or p.startswith('.') for p in parts) or '/'.join(parts) != rel:
        raise ValueError(f'invalid package path {rel!r}')
    if parts[0] in _RESERVED_TOP:
        raise ValueError(f'reserved package path {rel!r}')
    return rel


def _inside(base: Path, rel: str) -> Path:
    path = (base / package_path(rel)).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f'package path escapes {base}: {rel!r}')
    return path


def _fingerprint(entries: list[dict[str, Any]]) -> str:
    return sha256_bytes(json.dumps(entries, sort_keys=True).encode())


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def jsonl_bytes(records: Iterable[dict[str, Any]]) -> bytes:
    """Deterministic UTF-8 JSONL: keys in given order, one record per line."""
    return ''.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n' for r in records).encode()


def csv_bytes(header: list[str], rows: Iterable[list[Any]]) -> bytes:
    """UTF-8 with BOM (Excel-readable), ``\\n`` line ends, ``None`` as empty cell."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(header)
    for row in rows:
        w.writerow(['' if v is None else v for v in row])
    return b'\xef\xbb\xbf' + buf.getvalue().encode()


def layout_source(where: dict[str, Any]) -> dict[str, Any]:
    """Manifest ``source`` fields naming where the package belongs (from ``storage_layout.resolve``)."""
    return {'final_local_dir': Path(where['local_dir']).as_posix(), 'final_cloud_dir': where['cloud_dir'],
            'domain': where['domain'], 'subdomain': where['subdomain'], 'catalog_label': where['label'],
            'naming_schema': 'skilldep.storage-naming/v1', 'naming_snapshot': where['naming_path'],
            'naming_snapshot_sha256': where['naming_sha256']}


def _new_manifest(material_id: str, files: dict[str, bytes], *, records: dict[str, int], scope: dict[str, Any],
                  status: str, source: dict[str, Any], pending: list[str] | None,
                  extra: dict[str, Any] | None) -> dict[str, Any]:
    if status not in ('finalized', 'partial'):
        raise ValueError(status)
    for required in ('schema.json', 'provenance.json'):
        if required not in files:
            raise ValueError(f'missing {required}')
    for rel in files:
        package_path(rel)
    if set(records) - set(files):
        raise ValueError(f'records for unknown files {sorted(set(records) - set(files))}')
    if any(not isinstance(n, int) or n < 0 for n in records.values()):
        raise ValueError('record counts must be non-negative integers')
    if status == 'finalized' and not any(r.startswith('data/') and n > 0 and files[r] for r, n in records.items()):
        raise ValueError('finalized package needs a non-empty data/ file with records')
    entries = [{'path': p, 'bytes': len(b), 'sha256': sha256_bytes(b), 'records': records.get(p)}
               for p, b in sorted(files.items())]
    fingerprint = _fingerprint(entries)
    manifest = {
        'schema': MANIFEST_SCHEMA, 'material_id': material_id, 'status': status,
        'finalized_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'content_fingerprint': fingerprint, 'scope': scope, 'source': source,
        'records_total': sum(records.values()), 'files': entries, 'pending': pending or [],
    }
    if extra:
        if set(extra) & set(_GENERATED_KEYS):
            raise ValueError(f'extra manifest keys clash {sorted(set(extra) & set(_GENERATED_KEYS))}')
        manifest.update(extra)
    return manifest


def write_package(target: Path, material_id: str, files: dict[str, bytes], *,
                  records: dict[str, int], scope: dict[str, Any], status: str,
                  source: dict[str, Any], pending: list[str] | None = None,
                  extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Atomically create ``target`` with ``files`` (+ generated manifest.json).

    ``files`` maps package-relative POSIX paths to bytes and must include
    ``schema.json`` and ``provenance.json``.  ``records`` maps data file paths to
    record counts; ``extra`` adds non-generated top-level manifest keys (e.g.
    ``data_quality``).  Returns ``{status, path, manifest_sha256, fingerprint}``."""
    manifest = _new_manifest(material_id, files, records=records, scope=scope, status=status, source=source,
                             pending=pending, extra=extra)
    fingerprint = manifest['content_fingerprint']
    target = Path(target)
    if target.exists():  # never touched: unchanged only when it re-verifies with the same content
        check = verify_package(target)
        if check['status'] != 'verified':
            return {**check, 'fingerprint': fingerprint, 'existing_fingerprint': check.get('fingerprint')}
        same = check['fingerprint'] == fingerprint
        return {'status': 'unchanged' if same else 'conflict', 'path': str(target),
                'fingerprint': fingerprint, 'existing_fingerprint': check['fingerprint']}
    tmp = target.parent / f'.tmp-{material_id}'
    if tmp.exists():
        return {'status': 'conflict', 'path': str(target), 'reason': f'leftover {tmp}'}
    tmp.mkdir(parents=True)
    for rel, data in files.items():
        p = _inside(tmp, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    mbytes = (json.dumps(manifest, ensure_ascii=False, indent=1) + '\n').encode()
    (tmp / 'manifest.json').write_bytes(mbytes)
    os.replace(tmp, target)
    return verify_package(target) | {'manifest_sha256': sha256_bytes(mbytes)}


def revise_manifest(target: Path, updates: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Write manifest revision N+1 of an existing, verified package (data files untouched).

    The previous manifest is kept verbatim as ``manifest-history/r<N>-<sha>.json``
    (never overwritten); ``files``/``content_fingerprint`` cannot be changed here.
    Returns ``{status, revision, manifest_sha256, previous_manifest_sha256}``."""
    target = Path(target)
    check = verify_package(target)
    if check['status'] != 'verified':
        raise ValueError(f'package not verified: {check["bad"]}')
    if {'files', 'content_fingerprint', 'material_id'} & set(updates):
        raise ValueError('data identity fields cannot be revised')
    old_bytes = (target / 'manifest.json').read_bytes()
    old = json.loads(old_bytes)
    rev = int(old.get('manifest_revision', 1))
    old_sha = sha256_bytes(old_bytes)
    hist = target / 'manifest-history' / f'r{rev}-{old_sha}.json'
    if hist.exists():
        if hist.read_bytes() != old_bytes:
            raise ValueError(f'history conflict {hist}')
    else:
        hist.parent.mkdir(exist_ok=True)
        hist.write_bytes(old_bytes)
    new = {**old, **updates, 'manifest_revision': rev + 1, 'previous_manifest_sha256': old_sha,
           'revision_reason': reason, 'revised_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    data = (json.dumps(new, ensure_ascii=False, indent=1) + '\n').encode()
    tmp = target / '.manifest.json.tmp'
    tmp.write_bytes(data)
    os.replace(tmp, target / 'manifest.json')
    return {'status': 'revised', 'revision': rev + 1, 'manifest_sha256': sha256_bytes(data),
            'previous_manifest_sha256': old_sha, 'history': hist.as_posix()}


def _unlisted(target: Path, entries: list[dict[str, Any]]) -> list[str]:
    listed = {e['path'] for e in entries}
    out = []
    for path in sorted(target.rglob('*')):
        rel = path.relative_to(target).as_posix()
        if path.is_file() and rel != 'manifest.json' and not rel.startswith('manifest-history/') and rel not in listed:
            out.append(rel)
    return out


def rewrite_package(target: Path, files: dict[str, bytes], *, records: dict[str, int], scope: dict[str, Any],
                    status: str, source: dict[str, Any], reason: str, backup_dir: Path,
                    pending: list[str] | None = None, extra: dict[str, Any] | None = None,
                    preserve: Iterable[str] = ()) -> dict[str, Any]:
    """Replace the file set of an existing, verified package in place (stable path and ID).

    Every old file that changes or leaves the package, plus the old manifest, is first copied to
    ``backup_dir/r<N>-<sha12>/`` (outside the package) and re-hashed; then new files are written
    atomically, the old manifest is kept as ``manifest-history/r<N>-<sha>.json``, manifest revision
    N+1 is written and dropped files are unlinked.  Any error restores the old package from the
    backup.  ``preserve`` lists paths whose bytes must stay identical.  Same fingerprint ->
    ``unchanged`` (re-run is a no-op).  Returns ``{status, kept, written, removed, backup, ...}``."""
    target = Path(target)
    check = verify_package(target)
    if check['status'] != 'verified':
        raise ValueError(f'package not verified: {check["bad"] or check["reason"]}')
    old_bytes = (target / 'manifest.json').read_bytes()
    old = json.loads(old_bytes)
    new = _new_manifest(old['material_id'], files, records=records, scope=scope, status=status, source=source,
                        pending=pending, extra=extra)
    old_entries = {e['path']: e for e in old['files']}
    new_entries = {e['path']: e for e in new['files']}
    for rel in preserve:
        if rel not in old_entries or old_entries[rel]['sha256'] != new_entries.get(rel, {}).get('sha256'):
            raise ValueError(f'preserved file would change: {rel}')
    unlisted = _unlisted(target, old['files'])
    if new['content_fingerprint'] == old['content_fingerprint']:
        return {'status': 'unchanged', 'path': str(target), 'fingerprint': old['content_fingerprint'],
                'manifest_sha256': sha256_bytes(old_bytes), 'unlisted': unlisted}
    if unlisted:
        raise ValueError(f'unlisted files in package (another writer?): {unlisted}')
    backup_root = Path(backup_dir).resolve()
    if backup_root.is_relative_to(target.resolve()):
        raise ValueError('backup_dir must be outside the package')
    kept = sorted(p for p, e in new_entries.items() if old_entries.get(p, {}).get('sha256') == e['sha256'])
    written = sorted(set(new_entries) - set(kept))
    removed = sorted(set(old_entries) - set(new_entries))
    rev = int(old.get('manifest_revision', 1))
    old_sha = sha256_bytes(old_bytes)
    backup = backup_root / f'r{rev}-{old_sha[:12]}'
    saved = {'manifest.json': (old_sha, len(old_bytes))} | {
        p: (old_entries[p]['sha256'], old_entries[p]['bytes']) for p in sorted(set(written + removed) & set(old_entries))}
    for rel, (sha, _) in saved.items():
        dst = backup / rel
        if not (dst.is_file() and sha256_file(dst) == sha):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target / rel, dst)
        if sha256_file(dst) != sha:
            raise ValueError(f'backup copy mismatch: {dst}')
    new.update(manifest_revision=rev + 1, previous_manifest_sha256=old_sha, revision_reason=reason,
               revised_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    if 'finalized_at' in old:
        new['finalized_at'] = old['finalized_at']
    mbytes = (json.dumps(new, ensure_ascii=False, indent=1) + '\n').encode()
    hist = target / 'manifest-history' / f'r{rev}-{old_sha}.json'
    hist_created = not hist.exists()
    if not hist_created and hist.read_bytes() != old_bytes:
        raise ValueError(f'history conflict {hist}')
    try:
        if hist_created:
            hist.parent.mkdir(exist_ok=True)
            hist.write_bytes(old_bytes)
        for rel in written:
            dst = _inside(target, rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f'.{dst.name}.rewrite-tmp')
            tmp.write_bytes(files[rel])
            os.replace(tmp, dst)
        tmp = target / '.manifest.json.tmp'
        tmp.write_bytes(mbytes)
        os.replace(tmp, target / 'manifest.json')
        for rel in removed:
            _inside(target, rel).unlink()
        after = verify_package(target)
        if after['status'] != 'verified' or _unlisted(target, new['files']):
            raise ValueError(f'rewritten package does not verify: {after}')
    except BaseException:
        for rel in written:
            if rel not in old_entries:
                _inside(target, rel).unlink(missing_ok=True)
            tmp = _inside(target, rel).with_name(f'.{Path(rel).name}.rewrite-tmp')
            tmp.unlink(missing_ok=True)
        (target / '.manifest.json.tmp').unlink(missing_ok=True)
        for rel, (sha, size) in saved.items():
            dst = target / rel
            if not (dst.is_file() and dst.stat().st_size == size and sha256_file(dst) == sha):
                shutil.copyfile(backup / rel, dst)
        if hist_created:
            hist.unlink(missing_ok=True)
        raise
    return {'status': 'rewritten', 'path': str(target), 'revision': rev + 1, 'fingerprint': new['content_fingerprint'],
            'previous_fingerprint': old['content_fingerprint'], 'manifest_sha256': sha256_bytes(mbytes),
            'previous_manifest_sha256': old_sha, 'kept': kept, 'written': written, 'removed': removed,
            'backup': backup.as_posix(), 'history': hist.as_posix()}


def verify_package(target: Path) -> dict[str, Any]:
    """Re-read a package from disk (read-only).

    ``verified`` needs a complete manifest whose fingerprint matches its file list
    and every listed file inside the package with the recorded bytes/SHA-256;
    anything else is ``corrupt`` with the offending paths/reason."""
    target = Path(target)
    try:
        manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'status': 'corrupt', 'path': str(target), 'bad': ['manifest.json'], 'reason': 'manifest_unreadable'}
    missing = [k for k in MANIFEST_KEYS if k not in manifest]
    if missing or manifest['schema'] != MANIFEST_SCHEMA or not isinstance(manifest['files'], list):
        return {'status': 'corrupt', 'path': str(target), 'bad': ['manifest.json'], 'reason': 'manifest_incomplete',
                'missing': missing}
    entries = manifest['files']
    reason = None
    if _fingerprint(entries) != manifest['content_fingerprint']:
        reason = 'fingerprint_mismatch'
    elif manifest['records_total'] != sum(e.get('records') or 0 for e in entries):
        reason = 'records_total_mismatch'
    bad = []
    for e in entries:
        try:
            path = _inside(target, e['path'])
        except (ValueError, KeyError, TypeError):
            bad.append(str(e.get('path')) if isinstance(e, dict) else repr(e))
            continue
        if not path.is_file() or path.stat().st_size != e['bytes'] or sha256_file(path) != e['sha256']:
            bad.append(e['path'])
    ok = not bad and reason is None
    return {'status': 'verified' if ok else 'corrupt', 'path': str(target), 'bad': bad, 'reason': reason,
            'package_status': manifest['status'], 'records_total': manifest['records_total'],
            'fingerprint': manifest['content_fingerprint']}


def copy_original(src: Path | str, expected_sha256: str) -> bytes:
    """Bytes of a staging file after checking its recorded SHA-256 (staging untouched)."""
    data = Path(src).read_bytes()
    if sha256_bytes(data) != expected_sha256:
        raise ValueError(f'staging file changed: {src}')
    return data


__all__ = ['copy_original', 'csv_bytes', 'jsonl_bytes', 'sha256_bytes', 'sha256_file',
           'layout_source', 'package_path', 'revise_manifest', 'rewrite_package', 'verify_package', 'write_package']
