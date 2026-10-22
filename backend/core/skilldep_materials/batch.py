"""Pick the next SkillDep download batch from CodeHub's ``priority`` order.

``priority`` is recomputed by CodeHub after every download (it already folds in "every
second-level point gets something" and "big packs first get a slice"), so each batch
re-reads ``GET /plan/materials`` and takes the highest first. ``tier`` 1 = needed by the
first release, 2 = other demand, 0 = no third-level use (last, or never).

A material counts as present when this device already has it on disk per CodeHub
(``windows_local_downloaded``/``partial``/``downloading``); a netdisk copy does not count.
``via=buy`` / login-or-purchase items and items on a site with an active breaker are not
picked but listed under ``skipped`` with the reason.

``--launch`` starts one detached background process per site (materials of one site run
serially through ``page_set --expand``, different sites in parallel) and every launched batch
leaves ``<root>/_batches/<timestamp>.json`` with the list, PIDs, log paths and skip reasons.

CLI: ``uv run python -m backend.core.skilldep_materials.batch [--limit 10] [--tier 1] [--launch]
[--skip ID ...] [--expand 40]``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .site_guard import matching_block

MB = 1024 * 1024
PRESENT_FLAGS = ('windows_local_downloaded', 'windows_local_partial', 'windows_local_downloading')


def present_locally(material: dict[str, Any]) -> bool:
    acq = material.get('acquisition') or {}
    return any(acq.get(f) for f in PRESENT_FLAGS)


def first_batch_limit_bytes(material: dict[str, Any]) -> int | None:
    """Bytes this round may take for ``material`` (``first_batch_limit_mb``), None = no cut."""
    mb = material.get('first_batch_limit_mb')
    return int(float(mb) * MB) if mb else None


def skip_reason(material: dict[str, Any], blocks: dict[str, dict[str, Any]]) -> str | None:
    if material.get('via') == 'buy' or material.get('requires_login_or_purchase'):
        return '需要登录或付款（via=buy）'
    if not material.get('url'):
        return '没有来源链接'
    if hit := matching_block(blocks, material['url']):
        return f"站点 {hit[0]} 熔断中至 {hit[1].get('until')}：{hit[1].get('reason')}"
    return None


def next_batch(registry, *, limit: int = 10, tier: int | None = 1) -> dict[str, Any]:
    materials = sorted(registry.materials(), key=lambda m: -(m.get('priority') or 0))
    blocks = registry.site_blocks()
    selected, skipped = [], []
    for m in materials:
        if len(selected) >= limit:
            break
        if (tier is not None and m.get('tier') != tier) or present_locally(m):
            continue
        brief = {'id': m['id'], 'name': m.get('name'), 'priority': m.get('priority'), 'tier': m.get('tier'),
                 'url': m.get('url'), 'first_batch_limit_mb': m.get('first_batch_limit_mb'),
                 'first_batch_points': m.get('first_batch_points')}
        if reason := skip_reason(m, blocks):
            skipped.append({**brief, 'reason': reason})
        else:
            selected.append(brief)
    return {'selected': selected, 'skipped': skipped}


DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
BREAKAWAY = 0x01000000


def spawn_detached(args: list[str], log: Path, *, cwd: str | None = None) -> int:
    """Start ``python args...`` detached from this process, stdout/stderr to ``log``; returns the PID."""
    log.parent.mkdir(parents=True, exist_ok=True)
    kw: dict[str, Any] = {'cwd': cwd or str(Path(__file__).resolve().parents[3]), 'stdin': subprocess.DEVNULL,
                          'stderr': subprocess.STDOUT}
    with open(log, 'ab') as f:
        if sys.platform == 'win32':
            try:
                return subprocess.Popen([sys.executable, *args], stdout=f, creationflags=DETACHED | BREAKAWAY, **kw).pid
            except OSError:  # job object without breakaway permission
                return subprocess.Popen([sys.executable, *args], stdout=f, creationflags=DETACHED, **kw).pid
        return subprocess.Popen([sys.executable, *args], stdout=f, start_new_session=True, **kw).pid


def write_batch_record(record: dict[str, Any], root: Path | None = None) -> Path:
    """Write ``<root>/_batches/<record['batch']>.json`` (every launched batch keeps one)."""
    from .acquire import materials_root
    path = Path(root or materials_root()) / '_batches' / f"{record['batch']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    return path


def launch_batch(batch: dict[str, Any], *, skip: dict[str, str] | None = None, expand: int = 40,
                 root: Path | None = None) -> dict[str, Any]:
    """One detached ``page_set --expand`` process per site; returns the written batch record."""
    from .acquire import materials_root
    from .site_guard import site_name
    root = Path(root or materials_root())
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    logs = root / '_batches' / ts
    skip = skip or {}
    record: dict[str, Any] = {'batch': ts, 'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                              'tool': 'page_set --expand', 'processes': [], 'skipped': list(batch['skipped'])}
    groups: dict[str, list[dict[str, Any]]] = {}
    for m in batch['selected']:
        if m['id'] in skip:
            record['skipped'].append({**m, 'reason': skip[m['id']]})
        else:
            groups.setdefault(site_name(m['url']), []).append(m)
    for site, ms in groups.items():
        cmds = [['-m', 'backend.core.skilldep_materials.page_set', m['id'], m['url'], '--expand', str(expand)]
                for m in ms]
        args = cmds[0] if len(cmds) == 1 else [
            '-c', 'import subprocess, sys\nfor c in %r:\n    subprocess.run([sys.executable, *c])' % (cmds,)]
        log = logs / f'{site}.log'
        record['processes'].append({'site': site, 'pid': spawn_detached(args, log), 'log': str(log),
                                    'materials': [{k: m.get(k) for k in ('id', 'name', 'url', 'priority',
                                                                         'first_batch_limit_mb')} for m in ms]})
    record['path'] = str(write_batch_record(record, root))
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='python -m backend.core.skilldep_materials.batch',
                                 description='按 CodeHub priority 取下一批本机还没有的素材')
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--tier', type=int, default=1, help='-1 表示不限 tier')
    ap.add_argument('--launch', action='store_true', help='按站点起后台进程下载，并写 _batches/<时间戳>.json')
    ap.add_argument('--skip', nargs='*', default=[], metavar='ID=原因', help='本批不启动的素材（如需登录）')
    ap.add_argument('--expand', type=int, default=40, help='每条从入口页最多展开的正文/附件链接数')
    args = ap.parse_args(argv)
    from .codehub import CodeHubSkillDep

    out = next_batch(CodeHubSkillDep(), limit=args.limit, tier=None if args.tier < 0 else args.tier)
    if args.launch:
        skip = dict((s.split('=', 1) + ['跳过'])[:2] for s in args.skip)
        out = launch_batch(out, skip=skip, expand=args.expand)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
