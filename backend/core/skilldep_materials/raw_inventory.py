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
                    'root': base.parent, 'manifest_path': None, 'manifest_sha256': None, 'derived_text': set()}
        raise InventoryError('no manifest and no single verified file')
    mp = Path(mp)
    if local.get('manifest_sha256') and sha256_file(mp) != local['manifest_sha256']:
        raise InventoryError('manifest SHA-256 mismatch')
    manifest = json.loads(mp.read_text(encoding='utf-8'))
    roots = [r for r in (base if base and base.is_dir() else None, mp.parent, mp.parent.parent, mp.parent.parent.parent)
             if r is not None]
    out, derived = [], set()
    for e in _entries(manifest):
        rel = e.get('path') or e.get('rel') or e.get('local_rel')
        sha = e.get('sha256')
        if not rel or not sha:
            continue
        path = _resolve(rel, roots)
        if path is None:
            raise InventoryError(f'listed file missing: {rel}')
        size = e.get('size_bytes', e.get('size', e.get('bytes')))
        if (size is not None and path.stat().st_size != size) or sha256_file(path) != sha:
            raise InventoryError(f'listed file changed: {rel}')
        text_path = e.get('text_path')
        if text_path:
            derived.add(Path(text_path).as_posix())
        out.append({'rel': Path(rel).as_posix() if not Path(rel).is_absolute() else path.name, 'path': path,
                    'sha256': sha, 'bytes': path.stat().st_size,
                    'url': e.get('source_url') or e.get('url') or e.get('final_url') or (
                        e['_key'] if str(e.get('_key', '')).startswith('http') else None),
                    'title': e.get('title') or e.get('name'), 'published': e.get('published'), 'text_path': text_path})
    if not out:
        raise InventoryError('manifest lists no verifiable files')
    rels = [f['rel'] for f in out]
    if len(rels) != len(set(rels)):
        raise InventoryError('duplicate file paths in manifest')
    return {'files': out, 'root': mp.parent, 'manifest_path': mp, 'manifest_sha256': sha256_file(mp),
            'derived_text': derived, 'manifest': manifest}
