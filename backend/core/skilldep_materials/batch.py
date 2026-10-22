"""Pick the next SkillDep download batch from CodeHub's ``priority`` order.

``priority`` is recomputed by CodeHub after every download (it already folds in "every
second-level point gets something" and "big packs first get a slice"), so each batch
re-reads ``GET /plan/materials`` and takes the highest first. ``tier`` 1 = needed by the
first release, 2 = other demand, 0 = no third-level use (last, or never).

A material counts as present when this device already has it on disk per CodeHub
(``windows_local_downloaded``/``partial``/``downloading``); a netdisk copy does not count.
``via=buy`` / login-or-purchase items and items on a site with an active breaker are not
picked but listed under ``skipped`` with the reason.

CLI: ``uv run python -m backend.core.skilldep_materials.batch [--limit 10] [--tier 1]``.
"""
from __future__ import annotations

import argparse
import json
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='python -m backend.core.skilldep_materials.batch',
                                 description='按 CodeHub priority 取下一批本机还没有的素材')
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--tier', type=int, default=1, help='-1 表示不限 tier')
    args = ap.parse_args(argv)
    from .codehub import CodeHubSkillDep

    out = next_batch(CodeHubSkillDep(), limit=args.limit, tier=None if args.tier < 0 else args.tier)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
