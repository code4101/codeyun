"""Generic resource leases: 5-minute grant, explicit +5-minute renewal in last minute."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'acquire', 'check', 'renew', 'release'])
    parser.add_argument('--resource', default='gui:desktop:local')
    parser.add_argument('--owner', help='Required for acquire: task identity')
    parser.add_argument('--token', help='Grant token required for check/renew/release')
    args = parser.parse_args()
    if args.action == 'acquire' and not args.owner:
        parser.error('--owner is required for acquire')
    if args.action in {'check', 'renew', 'release'} and not args.token:
        parser.error('--token is required')
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
    ensure_attendance_engine_importable()
    import xlproject.loadenv
    from backend.core.runtime.resource_leases import ResourceLeaseStore, LeaseError
    store = ResourceLeaseStore()
    try:
        if args.action == 'status':
            result = store.status(args.resource)
        elif args.action == 'acquire':
            result = store.acquire(args.resource, args.owner)
        elif args.action == 'release':
            result = {'released': store.release(args.resource, args.token)}
        else:
            result = getattr(store, args.action)(args.resource, args.token)
        print(json.dumps(result, ensure_ascii=False))
        if result.get('released') is False:
            return 1
    except LeaseError as exc:
        print(json.dumps({'error': exc.code}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
