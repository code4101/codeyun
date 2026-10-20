from __future__ import annotations

import argparse
import json
import sys

import requests

from . import DEFAULT_SAMPLES_ROOT, HARD_TOTAL_LIMIT, CodeHubSkillDep, acquire, materials_root, sample_and_register


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Download selected public CodeHub SkillDep plan materials to this device '
                    '(bounded size, sha256) and register them as windows_local on CodeHub.',
    )
    sub = parser.add_subparsers(dest='command', required=True)
    show = sub.add_parser('list', help='List plan materials that have a direct link and need no login')
    show.add_argument('--max-mb', type=float, default=20, help='Only show items estimated at or below this size')
    run = sub.add_parser('acquire', help='Download (or reuse verified local copies), register and read back')
    run.add_argument('material_ids', nargs='+', help='Stable CodeHub material IDs, e.g. sd-2578c263a774')
    run.add_argument('--max-total-mb', type=float, default=HARD_TOTAL_LIMIT / 1024 / 1024,
                     help='Total byte budget for this batch (capped at 100MB)')
    run.add_argument('--root', help=f'Storage root (default {materials_root()})')
    run.add_argument('--no-proxy', action='store_true',
                     help='Download material files directly, ignoring system proxy settings '
                          '(for sources whose TLS fails through the local proxy)')
    run.add_argument('--adopt-from', help='Older root whose verified files (by its manifest.json sha256) '
                                           'are copied in instead of downloaded again; old copies are kept')
    samples = sub.add_parser('samples', help='Small throttled samples of slow crawl-only materials, '
                                             'registered as partial (never downloaded) and read back')
    samples.add_argument('material_ids', nargs='*', help='Default: every supported slow source')
    samples.add_argument('--root', help=f'Samples root (default {DEFAULT_SAMPLES_ROOT})')
    status = sub.add_parser('status', help='Read back CodeHub acquisition records')
    status.add_argument('material_ids', nargs='+')
    args = parser.parse_args(argv)

    hub = CodeHubSkillDep()
    hub.verify_peer()
    if args.command == 'list':
        out = [m for m in hub.materials()
               if m.get('url') and not m.get('requires_login_or_purchase')
               and m.get('size_mb_est') is not None and m['size_mb_est'] <= args.max_mb]
    elif args.command == 'acquire':
        session = requests.Session()
        session.trust_env = not args.no_proxy
        out = acquire(hub, args.material_ids, root=args.root, session=session, adopt_from=args.adopt_from,
                      max_total_bytes=int(args.max_total_mb * 1024 * 1024))
    elif args.command == 'samples':
        out = sample_and_register(hub, args.material_ids or None, root=args.root)
    else:
        out = {i: hub.acquisitions(i) for i in args.material_ids}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.command == 'acquire':
        return 0 if all(r['status'] == 'downloaded' and r['registered'] for r in out) else 1
    if args.command == 'samples':
        return 0 if all(r['sample_status'] == 'sampled' and r['status'] == 'partial' and r['registered']
                        for r in out['results']) else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
