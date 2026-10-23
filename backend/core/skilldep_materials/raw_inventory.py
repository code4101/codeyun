"""One verified file list for any SkillDep staging acquisition, whatever shape its manifest has.

Staging manifests grew many shapes (``files`` as a URL-keyed dict or a list; ``path``/``rel``/``local_rel``;
``size``/``size_bytes``/``bytes``; ``source_url``/``url``).  :func:`inventory` turns the catalog ``local``
record into ``[{rel, path, sha256, bytes, url, title, published, text_path}]`` after re-hashing every file,
and raises when a listed file is missing or changed.  A record without a manifest file but with a single
``path`` + ``sha256`` (one downloaded file) is accepted as a one-file inventory.  Staging is only read.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .finalize import sha256_file


class InventoryError(ValueError):
    pass


def _entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(manifest.get('pages'), dict) and not manifest.get('files'):
        return [dict(v) for v in manifest['pages'].values() if isinstance(v, dict)]
    if isinstance(manifest.get('archive'), dict) and manifest['archive'].get('sha256'):
        return [dict(manifest['archive'], url=manifest.get('pinned_url') or manifest.get('source_url'))]
    files = manifest.get('files')
    if isinstance(files, dict):
        return [dict(v, _key=k) for k, v in files.items() if isinstance(v, dict)]
    if isinstance(files, list):
        return [f for f in files if isinstance(f, dict)]
    raise InventoryError('manifest has no files list')


def _resolve(rel: str, roots: list[Path]) -> Path | None:
    p = Path(rel)
    if p.is_absolute():
        return p if p.is_file() else None
    for root in roots:
        if (root / rel).is_file():
            return root / rel
    return None


def inventory(local: dict[str, Any]) -> dict[str, Any]:
    """``{files, root, manifest_path, manifest_sha256, derived_text}`` of a verified staging acquisition."""
    mp = local.get('manifest_path')
    base = Path(local['path']) if local.get('path') else None
    if not mp or not Path(mp).is_file():
        if base and base.is_file() and local.get('sha256'):
            sha = sha256_file(base)
            if sha != local['sha256']:
                raise InventoryError(f'single file changed: {base}')
            return {'files': [{'rel': base.name, 'path': base, 'sha256': sha, 'bytes': base.stat().st_size,
                               'url': local.get('source_url'), 'title': None, 'published': None, 'text_path': None}],
                    'root': base.parent, 'manifest_path': None, 'manifest_sha256': None, 'derived_text': set(), 'overwritten': []}
        raise InventoryError('no manifest and no single verified file')
    mp = Path(mp)
    if local.get('manifest_sha256') and sha256_file(mp) != local['manifest_sha256']:
        raise InventoryError('manifest SHA-256 mismatch')
    manifest = json.loads(mp.read_text(encoding='utf-8'))
    declared = Path(manifest['root']) if isinstance(manifest.get('root'), str) else None
    roots = [r for r in (declared, base if base and base.is_dir() else None, mp.parent, mp.parent.parent,
                         mp.parent.parent.parent) if r is not None]
    out, derived, overwritten = [], set(), []
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in _entries(manifest):
        rel = e.get('local_rel') or e.get('rel') or e.get('path')
        if rel and e.get('sha256'):
            groups.setdefault(rel, []).append(e)
    for rel, group in groups.items():
        path = _resolve(rel, roots)
        if path is None:
            raise InventoryError(f'listed file missing: {rel}')
        actual = sha256_file(path)
        hit = [e for e in group if e['sha256'].lower() == actual]
        if not hit:
            raise InventoryError(f'listed file changed: {rel}')
        if len({e['sha256'].lower() for e in group}) > 1:  # crawler rewrote one file name for several sources
            overwritten.append({'rel': rel, 'listed_versions': len({e['sha256'].lower() for e in group}),
                                'kept_sha256': actual, 'lost_urls': [e.get('source_url') or e.get('url')
                                                                     for e in group if e not in hit]})
        e = hit[-1]
        sha = actual
        size = e.get('size_bytes', e.get('size', e.get('bytes')))
        if size is not None and path.stat().st_size != size:
            raise InventoryError(f'listed file changed: {rel}')
        text_path = e.get('text_path')
        if text_path:
            derived.add(Path(text_path).as_posix())
        if any(f['path'].resolve() == path.resolve() for f in out):
            continue  # same physical file listed under two spellings
        out.append({'rel': Path(rel).as_posix() if not Path(rel).is_absolute() else path.name, 'path': path,
                    'sha256': sha, 'bytes': path.stat().st_size,
                    'url': e.get('source_url') or e.get('url') or e.get('final_url') or (
                        e['_key'] if str(e.get('_key', '')).startswith('http') else None),
                    'title': e.get('title') or e.get('name'), 'published': e.get('published'), 'text_path': text_path})
    if not out:
        raise InventoryError('manifest lists no verifiable files')
    seen: dict[str, int] = {}
    for f in out:  # distinct package names when two absolute paths share a file name
        n = seen[f['rel']] = seen.get(f['rel'], 0) + 1
        if n > 1:
            f['rel'] = f'{Path(f["rel"]).stem}~{n}{Path(f["rel"]).suffix}'
    return {'files': out, 'root': mp.parent, 'manifest_path': mp, 'manifest_sha256': sha256_file(mp),
            'derived_text': derived, 'manifest': manifest, 'overwritten': overwritten}
