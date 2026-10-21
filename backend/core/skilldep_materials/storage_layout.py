"""Single source of truth for where finished SkillDep materials live.

Staging (raw crawls, checkpoints, queues, immutable evidence) stays under
``acquire.DEFAULT_ROOT`` = ``F:/CodeYun/materials/m2610skilldependency``.
Finished, cleaned data lives in the final tree, mirrored in the own netdisk::

    F:/data/m2610skilldep/<中文领域>/<中文子领域>/<素材名>_<sd-id>/
    /data/m2610skilldep/<中文领域>/<中文子领域>/<素材名>_<sd-id>/

Legacy cloud paths ``/data/m2610skilldependency/sources/<领域>/...`` were moved
as whole subtrees (ids preserved) on 2026-10-21; :func:`cloud_from_legacy` maps
them.  Old evidence/manifests keep their recorded paths and are never rewritten.

The 中文子领域 of each material is owned by CodeHub (label -> subdomain).  Its
typed naming snapshot ``skilldep.storage-naming/v1`` (cached by the main agent
at ``orchestration/codehub-storage-layout-current.json``) is consumed through
:func:`load_naming` / :func:`resolve`; no network.  A material missing from
that snapshot, or carrying an ``error``, fails instead of being filed under a
placeholder.  :func:`relative_dir` builds the same shape for callers that
already hold a subdomain.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

FINAL_LOCAL_ROOT = Path('F:/data/m2610skilldep')
FINAL_CLOUD_ROOT = '/data/m2610skilldep'
LEGACY_CLOUD_ROOT = '/data/m2610skilldependency/sources'
LEGACY_CLOUD_AUX = '历史来源'  # emptied former ``sources`` folder, not a domain
DOMAIN_DIRS = {
    'DA': '数据分析', 'DE': '设计', 'EDU': '教育', 'FIN': '金融', 'GA': '游戏',
    'GO': '通用办公', 'LAW': '法律', 'MA': '市场营销', 'MED': '医疗',
}
NAMING_SCHEMA = 'skilldep.storage-naming/v1'
DEFAULT_NAMING = Path('F:/CodeYun/materials/m2610skilldependency/orchestration/codehub-storage-layout-current.json')
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(name: str) -> str:
    """Folder-safe material name: path separators/illegal characters become ``_``."""
    value = _BAD.sub('_', str(name or '').strip()).strip(' .')
    if not value:
        raise ValueError('empty material name')
    return value


def relative_dir(domain: str, subdomain: str, name: str, material_id: str) -> str:
    """``<中文领域>/<中文子领域>/<素材名>_<sd-id>`` shared by local and cloud trees."""
    if domain not in DOMAIN_DIRS:
        raise ValueError(f'unknown domain {domain!r}')
    if not re.fullmatch(r'sd-[0-9a-f]{12}', material_id):
        raise ValueError(f'invalid material id {material_id!r}')
    return f'{DOMAIN_DIRS[domain]}/{safe_name(subdomain)}/{safe_name(name)}_{material_id}'


def local_dir(domain: str, subdomain: str, name: str, material_id: str,
              root: Path = FINAL_LOCAL_ROOT) -> Path:
    return Path(root) / relative_dir(domain, subdomain, name, material_id)


def cloud_dir(domain: str, subdomain: str, name: str, material_id: str) -> str:
    return f'{FINAL_CLOUD_ROOT}/{relative_dir(domain, subdomain, name, material_id)}'


def cloud_from_legacy(path: str) -> str | None:
    """Current cloud path of a legacy ``/data/m2610skilldependency/sources/...`` path."""
    prefix = LEGACY_CLOUD_ROOT + '/'
    if not path.startswith(prefix):
        return None
    return f'{FINAL_CLOUD_ROOT}/{path[len(prefix):]}'


def load_naming(path: Path | str = DEFAULT_NAMING) -> dict[str, Any]:
    """Read a cached CodeHub naming snapshot: ``{schema, sha256, path, items{id: record}}``."""
    raw = Path(path).read_bytes()
    doc = json.loads(raw)
    if doc.get('schema_id') != NAMING_SCHEMA:
        raise ValueError(f'unexpected naming schema {doc.get("schema_id")!r}')
    items = {r['material_id']: r for r in doc['items']}
    if len(items) != len(doc['items']):
        raise ValueError('duplicate material ids in naming snapshot')
    return {'schema': NAMING_SCHEMA, 'sha256': hashlib.sha256(raw).hexdigest(),
            'path': Path(path).as_posix(), 'items': items}


def resolve(material_id: str, naming: dict[str, Any], root: Path = FINAL_LOCAL_ROOT) -> dict[str, Any]:
    """Final dirs of one material from the naming snapshot, validated against this layout.

    Returns ``{local_dir: Path, cloud_dir, domain, subdomain, label, naming_sha256}``;
    raises ``LookupError`` when the material is absent or flagged with an error."""
    rec = naming['items'].get(material_id)
    if rec is None:
        raise LookupError(f'{material_id} not in naming snapshot')
    if rec.get('error'):
        raise LookupError(f'{material_id}: naming error {rec["error"]}')
    local, cloud = rec['desired_local_dir'], rec['desired_cloud_dir']
    rel = local[len(str(FINAL_LOCAL_ROOT.as_posix())) + 1:] if local.startswith(FINAL_LOCAL_ROOT.as_posix() + '/') else None
    parts = rel.split('/') if rel else []
    if (len(parts) != 3 or rec.get('domain') not in DOMAIN_DIRS or parts[0] != DOMAIN_DIRS[rec['domain']]
            or not parts[2].endswith('_' + material_id) or cloud != f'{FINAL_CLOUD_ROOT}/{rel}'
            or any(p != safe_name(p) for p in parts)):
        raise ValueError(f'{material_id}: naming record does not fit layout: {local!r}')
    return {'local_dir': Path(root) / rel, 'cloud_dir': cloud, 'domain': rec['domain'], 'subdomain': parts[1],
            'label': rec.get('label'), 'naming_sha256': naming['sha256'], 'naming_path': naming['path']}
