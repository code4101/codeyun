"""Write one finalized SkillDep material package.

A package is ``storage_layout.local_dir(...)`` holding::

    data/...            the deliverable: native source files and/or derived tables
    originals/...       optional extra verbatim source files
    schema.json         field definitions, required only when structured records are declared
    provenance.json     source URL, staging manifest path/SHA, notes, gaps (always required)
    manifest.json       per-file SHA-256/bytes, ``kind`` or record count, scope + status

Native files are first-class deliverables.  A substantive, readable PDF, image,
Word, Excel, HTML, text or data file is packaged byte-for-byte and declared in
``native={path: kind}`` (``NATIVE_KINDS``); it may be the whole package.  No
conversion to CSV/JSONL, OCR, parsing, schema or row count is required; native
files carry ``records: null``, never a made-up row count.  Excel workbooks keep
their sheets, formulas and styles because their bytes are not touched.
``records`` stays optional for derived tables that really exist.

``status`` is ``finalized`` when the package holds at least one substantive
deliverable (a native file passing its format check, or a ``data/`` file with
records > 0); otherwise ``partial`` with ``pending`` reasons.  The native check
only sniffs bytes: the declared kind must match the file signature (an HTML error
page saved as ``.xls`` is rejected) and an HTML page needs visible text outside
links/navigation, so catalog, login, error and link-listing shells are not
upgraded to content.  Choosing which files are substantive stays with the caller;
a hash inventory alone is not a deliverable.

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
import re
import shutil
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

MANIFEST_SCHEMA = 'codeyun.skilldep_final_package/v1'
MANIFEST_KEYS = ('schema', 'material_id', 'status', 'content_fingerprint', 'records_total', 'files')
_RESERVED_TOP = ('manifest.json', 'manifest-history')
_GENERATED_KEYS = ('schema', 'material_id', 'status', 'finalized_at', 'content_fingerprint', 'scope', 'source',
                   'records_total', 'files', 'pending', 'manifest_revision', 'previous_manifest_sha256',
                   'revision_reason', 'revised_at')
NATIVE_KINDS = ('pdf', 'excel', 'word', 'presentation', 'image', 'html', 'text', 'data')
HTML_MIN_TEXT = 200  # visible characters outside links/navigation for an HTML page to count as content
_OLE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
_OOXML = {'excel': b'xl/', 'word': b'word/', 'presentation': b'ppt/'}
_IMAGE_MAGIC = (b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a', b'BM', b'II*\x00', b'MM\x00*')
_HTML_HEAD = re.compile(rb'^\s*(<!--.*?-->\s*)*<(!doctype\s+html|html|head|body)\b', re.I | re.S)


class _PageText(HTMLParser):
    """Counts visible characters outside links, navigation chrome, scripts and the title."""
    SKIP = {'a', 'nav', 'header', 'footer', 'script', 'style', 'title', 'noscript', 'select', 'button'}

    def __init__(self):
        super().__init__()
        self.depth, self.chars = 0, 0

    def handle_starttag(self, tag, attrs):
        self.depth += tag in self.SKIP

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.chars += len(''.join(data.split()))


def native_problem(kind: str, data: bytes) -> str | None:
    """Why ``data`` is not a usable native ``kind`` deliverable, or ``None`` when it is (bytes only)."""
    if kind not in NATIVE_KINDS:
        return f'unknown native kind {kind!r}'
    if not data:
        return 'empty file'
    head = data[:4096]
    if kind == 'pdf':
        ok = b'%PDF-' in data[:1024]
    elif kind in _OOXML:
        ok = data.startswith(_OLE) or (data.startswith(b'PK\x03\x04') and _OOXML[kind] in data)
    elif kind == 'image':
        ok = data.startswith(_IMAGE_MAGIC) or (data[:4] == b'RIFF' and data[8:12] == b'WEBP') or b'<svg' in head
    elif kind == 'html':
        page = _PageText()
        for enc in ('utf-8', 'gb18030', 'latin-1'):
            try:
                page.feed(data.decode(enc))
                break
            except UnicodeDecodeError:
                continue
        page.close()
        ok = page.chars >= HTML_MIN_TEXT
        if not ok:
            return f'html has {page.chars} visible characters outside links/navigation (< {HTML_MIN_TEXT})'
    else:  # text / data: anything but an HTML page in disguise
        ok = not _HTML_HEAD.match(head)
    return None if ok else f'bytes do not look like {kind}'


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
                  extra: dict[str, Any] | None, native: dict[str, str] | None = None) -> dict[str, Any]:
    if status not in ('finalized', 'partial'):
        raise ValueError(status)
    native = native or {}
    for required in ('provenance.json',) + (('schema.json',) if records else ()):
        if required not in files:
            raise ValueError(f'missing {required}')
    for rel in files:
        package_path(rel)
    if set(records) - set(files):
        raise ValueError(f'records for unknown files {sorted(set(records) - set(files))}')
    if any(not isinstance(n, int) or n < 0 for n in records.values()):
        raise ValueError('record counts must be non-negative integers')
    if set(native) - set(files) or set(native) & set(records):
        raise ValueError(f'native files must be package files without record counts: {sorted(native)}')
    unusable = {r: why for r, k in native.items() if (why := native_problem(k, files[r]))}
    if unusable:
        raise ValueError(f'not usable native deliverables: {unusable}')
    if status == 'finalized' and not native and not any(
            r.startswith('data/') and n > 0 and files[r] for r, n in records.items()):
        raise ValueError('finalized package needs a native deliverable or a non-empty data/ file with records')
    entries = [{'path': p, 'bytes': len(b), 'sha256': sha256_bytes(b), 'records': records.get(p)}
               | ({'kind': native[p]} if p in native else {}) for p, b in sorted(files.items())]
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
                  extra: dict[str, Any] | None = None, native: dict[str, str] | None = None) -> dict[str, Any]:
    """Atomically create ``target`` with ``files`` (+ generated manifest.json).

    ``files`` maps package-relative POSIX paths to bytes and must include
    ``provenance.json`` (plus ``schema.json`` when ``records`` is non-empty).
    ``native`` maps unchanged source files to a ``NATIVE_KINDS`` kind; ``records``
    maps derived table paths to real record counts and may be ``{}``.  ``extra``
    adds non-generated top-level manifest keys (e.g. ``data_quality``).
    Returns ``{status, path, manifest_sha256, fingerprint}``."""
    manifest = _new_manifest(material_id, files, records=records, scope=scope, status=status, source=source,
                             pending=pending, extra=extra, native=native)
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
                    preserve: Iterable[str] = (), native: dict[str, str] | None = None) -> dict[str, Any]:
    """Replace the file set of an existing, verified package in place (stable path and ID).

    Every old file that changes or leaves the package, plus the old manifest, is first copied to
    ``backup_dir/r<N>-<sha12>/`` (outside the package) and re-hashed; then new files are written
    atomically, the old manifest is kept as ``manifest-history/r<N>-<sha>.json``, manifest revision
    N+1 is written and dropped files are unlinked.  Any error restores the old package from the
    backup.  ``preserve`` lists paths whose bytes must stay identical; ``native`` as in
    ``write_package`` (re-declaring kept originals as native does not touch their bytes).  Same fingerprint ->
    ``unchanged`` (re-run is a no-op).  Returns ``{status, kept, written, removed, backup, ...}``."""
    target = Path(target)
    check = verify_package(target)
    if check['status'] != 'verified':
        raise ValueError(f'package not verified: {check["bad"] or check["reason"]}')
    old_bytes = (target / 'manifest.json').read_bytes()
    old = json.loads(old_bytes)
    new = _new_manifest(old['material_id'], files, records=records, scope=scope, status=status, source=source,
                        pending=pending, extra=extra, native=native)
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


__all__ = ['NATIVE_KINDS', 'copy_original', 'csv_bytes', 'jsonl_bytes', 'native_problem', 'sha256_bytes',
           'sha256_file', 'layout_source', 'package_path', 'revise_manifest', 'rewrite_package', 'verify_package',
           'write_package']
