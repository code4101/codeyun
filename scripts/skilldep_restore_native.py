"""Restore already-usable native files as primary SkillDep data.

The caller first checks business usability: Excel/PDF/documents and genuine
datasets can be copied. Executable JS, API transport envelopes and website shells
are carriers, not automatically deliverables; remove only their necessary wrapper
before using the public package APIs. Source-byte equality alone is not acceptance.

One mapping per material: material_id, target, expected_manifest_sha256, files
[{source, path: "data/original.xls", sha256, kind: "excel"}], optional pending.
Supporting context files may omit kind; at least one substantive file is required.
All materials use the same path: verify source bytes -> backed-up package rewrite
-> public registration/read-back. Native JSON/CSV datasets remain native too.
Network failure leaves a local-success receipt; --publish-only retries metadata,
never the file rewrite. Earlier permission denials must be resolved by the caller.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parent / 'xlproject' / 'src')]
import xlproject.loadenv  # noqa: E402,F401
from backend.core.skilldep_materials.codehub import CodeHubSkillDep  # noqa: E402
from backend.core.skilldep_materials.finalize import (  # noqa: E402
    rewrite_package, sha256_bytes, sha256_file, verify_package,
)


def restore(mapping: dict, receipt_path: Path, *, publish_only=False) -> dict:
    target = Path(mapping['target']).resolve(strict=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    def save(value):
        receipt_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    if publish_only:
        result = json.loads(receipt_path.read_text(encoding='utf-8'))
    else:
        old = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
        if old['material_id'] != mapping['material_id']:
            raise ValueError('target material mismatch')
        if sha256_file(target / 'manifest.json') != mapping['expected_manifest_sha256']:
            raise ValueError('manifest changed; recheck owner before restoring')
        files, native = {}, {}
        for row in mapping['files']:
            rel = row['path']
            if not rel.startswith('data/') or rel in files:
                raise ValueError('native paths must be unique and under data/')
            content = Path(row['source']).read_bytes()
            if sha256_bytes(content) != row['sha256']:
                raise ValueError(f'source changed: {row["source"]}')
            files[rel] = content
            if row.get('kind'):
                native[rel] = row['kind']
        if not native:
            raise ValueError('no substantive source files')
        # Keep existing source evidence; obsolete primary data is backed up by
        # rewrite_package before removal. No workbook/document is opened/saved.
        files.update({e['path']: (target / e['path']).read_bytes() for e in old['files']
                      if not e['path'].startswith('data/') and e['path'] != 'schema.json'})
        pending = mapping.get('pending', old.get('pending', []))
        written = rewrite_package(target, files, records={}, native=native,
            scope={'kind': 'native_source_files', 'files': len(native)},
            status='partial' if pending or old['status'] == 'partial' else 'finalized',
            source=old['source'], pending=pending,
            reason='Restore substantive native files; no format conversion.',
            backup_dir=receipt_path.parent / 'backups' / mapping['material_id'])
        result = {'material_id': mapping['material_id'], 'path': str(target),
                  'local_success': True, 'native_files': len(native),
                  'source_bytes_identical': True, 'rewrite': written, 'remote_ack': False}
        save(result)
    check = verify_package(target)
    if check['status'] != 'verified':
        raise ValueError(f'package verification failed: {check}')
    result['verify'] = check
    save(result)
    result.pop('publication_error', None)
    try:
        hub = CodeHubSkillDep(timeout=8, retries=1)
        hub.verify_peer()
        def current():
            items = hub.finalizations()['items']
            items = items.values() if isinstance(items, dict) else items
            return next(x for x in items if x.get('material_id') == mapping['material_id']
                        and x.get('root') == 'local')
        previous = current()
        if Path(previous['path']).resolve() != target:
            raise ValueError('remote target differs')
        manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
        record = {k: previous.get(k) for k in ('source_acquisition_device_id', 'expected_resource_version')}
        record.update(status='cleaning' if manifest['status'] == 'partial' else 'finalized',
            path=target.as_posix(), size_bytes=sum(f.stat().st_size for f in target.rglob('*') if f.is_file()),
            file_count=sum(1 for f in target.rglob('*') if f.is_file()),
            manifest_path=(target / 'manifest.json').as_posix(),
            manifest_sha256=sha256_file(target / 'manifest.json'),
            finished_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            note=f'{result["native_files"]} native source files restored byte-for-byte; previous derivatives backed up.',
            error=None)
        result['publication_record'] = record
        save(result)
        hub.put_finalization(mapping['material_id'], 'local', record)
        back = current()
        result['remote_ack'] = all(back.get(k) == v for k, v in record.items())
        result['remote_readback'] = back
    except Exception as exc:
        result['publication_error'] = str(exc)
    save(result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--publish-only', action='store_true')
    args = parser.parse_args()
    output = restore(json.loads(args.mapping.read_text(encoding='utf-8')), args.receipt,
                     publish_only=args.publish_only)
    print(json.dumps({k: output.get(k) for k in ('material_id', 'local_success', 'native_files', 'remote_ack')}))
